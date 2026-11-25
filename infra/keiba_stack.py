"""競馬予想シミュレーターのAWS構成（サーバーレス）。

  ブラウザ ─ CloudFront ─┬─ S3（静的な画面 index.html）
                         └─ /api/* ─ API Gateway(HTTP API) ─ Lambda(api) ─┐
                                                                         ├─ DynamoDB（予想・履歴・成績）
  EventBridge(定期) ─ Lambda(refresh / precompute / settle) ─ Lambda(worker)┘
                                          └─ 外部データ取得（差し替え可能なアダプタ）
  CloudWatch Alarms ─ SNS ─ メール通知（Lambdaエラー / API 5xx / スロットリング）
"""
from aws_cdk import (
    CfnOutput, Duration, RemovalPolicy, Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as apigw_int,
    aws_budgets as budgets,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_dynamodb as ddb,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
)
from constructs import Construct

TAGS = {"app": "keiba-simulator"}


class KeibaStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)
        ctx = lambda k, d=None: self.node.try_get_context(k) if self.node.try_get_context(k) not in (None, "") else d
        access_code = str(ctx("access_code", ""))
        mode = str(ctx("precompute_mode", "main"))
        worker_conc = int(ctx("worker_concurrency", 0) or 0)
        autotune = str(ctx("autotune", False)).lower() == "true"

        # ---- データ保存: DynamoDB（オンデマンド課金。TTLで古いデータを自動削除）----
        table = ddb.Table(
            self, "Table", partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST, time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY)      # 学習用: スタック削除でデータも消える。本番はRETAIN推奨

        # ---- Lambda共通設定（Graviton/arm64、コードは build/lambda を配布）----
        code = _lambda.Code.from_asset("build/lambda")
        base_env = {"TABLE_NAME": table.table_name, "PRECOMPUTE_MODE": mode, "AUTOTUNE": str(autotune).lower()}

        def fn(name, handler, mem, timeout, env=None, conc=None):
            return _lambda.Function(
                self, name, runtime=_lambda.Runtime.PYTHON_3_12, architecture=_lambda.Architecture.ARM_64,
                handler=handler, code=code, memory_size=mem, timeout=Duration.seconds(timeout),
                environment={**base_env, **(env or {})}, reserved_concurrent_executions=conc,
                log_group=logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.TWO_WEEKS,
                                        removal_policy=RemovalPolicy.DESTROY))

        worker = fn("Worker", "handlers.worker_handler", 1024, 180, conc=worker_conc or None)
        env_w = {"WORKER_NAME": worker.function_name}
        api_fn = fn("ApiFn", "handlers.api_handler", 256, 29, {**env_w, "ACCESS_CODE": access_code})
        refresh = fn("Refresh", "handlers.refresh_handler", 512, 300, env_w)
        precompute = fn("Precompute", "handlers.precompute_handler", 256, 120, env_w)
        settle = fn("Settle", "handlers.settle_handler", 1024, 600)

        for f in (worker, api_fn, refresh, precompute, settle):
            table.grant_read_write_data(f)
        for f in (api_fn, refresh, precompute):
            worker.grant_invoke(f)

        # ---- API: API Gateway(HTTP API) ----
        api = apigw.HttpApi(self, "HttpApi", create_default_stage=True)
        integ = apigw_int.HttpLambdaIntegration("ApiInteg", api_fn)
        for path in ("/api/races", "/api/predict", "/api/stats", "/api/manual"):
            api.add_routes(path=path, methods=[apigw.HttpMethod.GET], integration=integ)
        api.default_stage.node.default_child.add_property_override(      # 乱用対策のレート制限
            "DefaultRouteSettings", {"ThrottlingRateLimit": 20, "ThrottlingBurstLimit": 40})

        # ---- 画面: S3 + CloudFront（/api/* は API Gateway へ転送。同一ドメインなのでCORS不要）----
        bucket = s3.Bucket(self, "Site", block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                           encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True,
                           removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True)
        api_cache = cf.CachePolicy(
            self, "ApiCache", default_ttl=Duration.seconds(0), min_ttl=Duration.seconds(0), max_ttl=Duration.seconds(300),
            query_string_behavior=cf.CacheQueryStringBehavior.all(),
            header_behavior=cf.CacheHeaderBehavior.allow_list("x-access-code"))
        dist = cf.Distribution(
            self, "Cdn", default_root_object="index.html", price_class=cf.PriceClass.PRICE_CLASS_200,
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED),
            additional_behaviors={"/api/*": cf.BehaviorOptions(
                origin=origins.HttpOrigin(f"{api.api_id}.execute-api.{self.region}.amazonaws.com"),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.HTTPS_ONLY,
                allowed_methods=cf.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=api_cache,
                origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER)})
        s3deploy.BucketDeployment(self, "Deploy", sources=[s3deploy.Source.asset("frontend")],
                                  destination_bucket=bucket, distribution=dist, distribution_paths=["/index.html"])

        # ---- 定期実行（EventBridge。cronはUTC。JST = UTC+9）----
        def rule(name, schedule, target):
            events.Rule(self, name, schedule=schedule, targets=[targets.LambdaFunction(target, retry_attempts=1)])

        rule("RefreshDaily", events.Schedule.cron(minute="30", hour="20"), refresh)             # 毎日 05:30 JST
        rule("PrecomputeSat", events.Schedule.cron(minute="0", hour="22-23", week_day="FRI,SAT"), precompute)  # 土日の朝(JST 07-08時)
        rule("PrecomputeRaceDay", events.Schedule.cron(minute="0", hour="0-8", week_day="SAT,SUN"), precompute)  # 土日 09-17時 JST
        rule("SettleEvening", events.Schedule.cron(minute="30", hour="9", week_day="SAT,SUN"), settle)  # 土日 18:30 JST
        rule("SettleMonday", events.Schedule.cron(minute="0", hour="1", week_day="MON"), settle)         # 月曜 10:00 JST

        # ---- 監視: Lambdaのエラー / API 5xx を SNS 経由でメール通知（-c alert_email=... か budget_email）----
        alert_to = ctx("alert_email", ctx("budget_email"))
        topic = sns.Topic(self, "Alerts", display_name="keiba-simulator alerts")
        if alert_to:
            topic.add_subscription(subs.EmailSubscription(str(alert_to)))
        action = cw_actions.SnsAction(topic)

        def alarm(name, metric, threshold, desc):
            a = cw.Alarm(self, name, metric=metric, threshold=threshold, evaluation_periods=1,
                         comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                         treat_missing_data=cw.TreatMissingData.NOT_BREACHING, alarm_description=desc)
            a.add_alarm_action(action)

        for label, f in (("Worker", worker), ("ApiFn", api_fn), ("Refresh", refresh),
                         ("Precompute", precompute), ("Settle", settle)):
            alarm(f"{label}Errors", f.metric_errors(period=Duration.minutes(15), statistic="Sum"), 1,
                  f"{label} Lambda がエラー終了しました。CloudWatch Logs を確認してください")
        alarm("Api5xx", cw.Metric(namespace="AWS/ApiGateway", metric_name="5xx", statistic="Sum",
                                  period=Duration.minutes(5), dimensions_map={"ApiId": api.api_id}), 3,
              "API が5分間に3回以上 5xx を返しました")
        alarm("ApiThrottled", api_fn.metric_throttles(period=Duration.minutes(5), statistic="Sum"), 1,
              "API Lambda がスロットリングされました（同時実行の上限）")

        # ---- 料金の見張り（任意: -c budget_email=you@example.com）----
        email = ctx("budget_email")
        if email:
            budgets.CfnBudget(self, "Budget", budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST", time_unit="MONTHLY", budget_limit=budgets.CfnBudget.SpendProperty(amount=5, unit="USD")),
                notifications_with_subscribers=[budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type="ACTUAL", comparison_operator="GREATER_THAN", threshold=80),
                    subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=str(email))])])

        CfnOutput(self, "SiteUrl", value=f"https://{dist.distribution_domain_name}")
        CfnOutput(self, "AlertTopicArn", value=topic.topic_arn)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "WorkerName", value=worker.function_name)
        CfnOutput(self, "RefreshName", value=refresh.function_name)
        CfnOutput(self, "SettleName", value=settle.function_name)
