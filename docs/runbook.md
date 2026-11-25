# 運用手順書（Runbook）

## 日次（約5分）
| # | 確認 | 見る場所 | 正常 | 異常時 |
|---|---|---|---|---|
| 1 | アラームのメールが来ていないか | メール / CloudWatch → アラーム | 0件 | 該当Lambdaのログを開く（下記） |
| 2 | 朝の開催一覧更新が成功したか | `/aws/lambda/…Refresh…` のログ | `days` が1以上（開催週） | 取得元の構造変更を疑う（下記） |
| 3 | 開催日は予想が更新されているか | 画面の「更新からの経過」 | 30分以内 | `Precompute` / `Worker` のログ |

## 週次
- Cost Explorer / Budgets で当月の費用を確認（目安: 月5ドル以内）
- DynamoDBのアイテム数と容量が増え続けていないか（TTLが効いているか）

## 月次
- 依存ライブラリの更新（`pip list --outdated`）→ テスト → デプロイ
- アクセスコードの変更（GitHub Secretsを更新して再デプロイ）
- 運用ログ（`docs/incident-log.md`）の見直し

## 障害対応の基本手順
1. **症状の切り分け**: 画面が出ない（CloudFront/S3）か、APIが失敗（API Gateway/Lambda）か、データが古い（EventBridge/Worker）か
2. **ログ**: CloudWatch Logs → 該当Lambdaのロググループ → `ERROR` で検索
3. **再現**: `aws lambda invoke --function-name <名前> out.json` で単体実行
4. **復旧**: 直近の正常なコミットから `deploy` ワークフローを手動実行
5. **記録**: `docs/incident-log.md` に追記（症状・原因・対処・再発防止）

## 取得元（外部データ）が変わった・使えなくなった時
- `Worker` のログに取得失敗が並び、画面は「取得できませんでした」になる
- 手入力モード（`/api/manual`）は外部に依存せず動くので、サービス全体は止まらない
- 取得アダプタだけを差し替える（`docs/data-sources.md`）

## 主なコマンド
```bash
./scripts/build_lambda.sh                      # Lambdaの素材を作る
cdk deploy -c access_code=... -c alert_email=... # デプロイ
aws lambda invoke --function-name <RefreshName> out.json   # 一覧更新を手動実行
aws logs tail /aws/lambda/<名前> --follow       # ログを追う
```
