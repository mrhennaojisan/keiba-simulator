"""CDKスタックのテスト: 監視と主要リソースが定義されていること（AWSへは接続しない）"""
import os
import shutil
import sys

import aws_cdk as cdk
import pytest
from aws_cdk import assertions

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)


@pytest.fixture(scope="module")
def template():
    made = not os.path.isdir(os.path.join(ROOT, "build", "lambda"))
    if made:
        os.makedirs(os.path.join(ROOT, "build", "lambda"))
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        from infra.keiba_stack import KeibaStack
        app = cdk.App(context={"alert_email": "a@example.com", "access_code": "x"})
        st = KeibaStack(app, "T", env=cdk.Environment(account="123456789012", region="ap-northeast-1"))
        yield assertions.Template.from_stack(st)
    finally:
        os.chdir(cwd)
        if made:
            shutil.rmtree(os.path.join(ROOT, "build"), ignore_errors=True)


def test_alarms_notify_sns(template):
    template.resource_count_is("AWS::CloudWatch::Alarm", 7)
    template.resource_count_is("AWS::SNS::Subscription", 1)


def test_lambda_is_arm64_py312(template):
    fns = template.find_resources("AWS::Lambda::Function", {"Properties": {"Runtime": "python3.12"}})
    assert len(fns) >= 5
    assert all(f["Properties"]["Architectures"] == ["arm64"] for f in fns.values())


def test_bucket_is_private(template):
    template.has_resource_properties("AWS::S3::Bucket", {"PublicAccessBlockConfiguration": {
        "BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True}})


def test_dynamodb_ttl_and_on_demand(template):
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "BillingMode": "PAY_PER_REQUEST", "TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True}})
