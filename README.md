# 競馬レース予想シミュレーター（AWS サーバーレス版）

[![ci](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

AWSのサーバーレス構成で、**事前計算 → 保存 → 配信 → 監視**までを運用する個人開発です。
予想の中身は娯楽・参考用で、このリポジトリで見てほしいのはクラウドの設計と運用です。

| 見てほしい点 | 場所 |
|---|---|
| 構成図と設計判断（なぜその構成か、他案との比較） | [docs/architecture.md](docs/architecture.md) |
| IaC（AWS CDK / Python） | [infra/keiba_stack.py](infra/keiba_stack.py) |
| テスト（pytest + moto、CDKのスナップショットでなく性質を検査） | [tests/](tests) |
| CI/CD（テスト → ビルド → OIDCでデプロイ） | [.github/workflows](.github/workflows) |
| 監視（Lambdaエラー・API 5xx → SNS → メール） | `infra/keiba_stack.py` の Alarm |
| 運用手順書（日次/週次/月次、障害対応） | [docs/runbook.md](docs/runbook.md) |
| 障害の記録と再発防止 | [docs/incident-log.md](docs/incident-log.md) |
| データ取得元と利用上の注意 | [docs/data-sources.md](docs/data-sources.md) |

**娯楽・参考用**です。的中や収益を保証しません。

## 構成

```
ブラウザ ─ CloudFront ─┬─ S3（画面: index.html）
                       └─ /api/* ─ API Gateway(HTTP API) ─ Lambda(api) ─┐
                                                                        ├─ DynamoDB
EventBridge(定期) ─ Lambda(refresh / precompute / settle) ─ Lambda(worker)┘
                                         └─ 外部データ取得（差し替え可能。docs/data-sources.md）
CloudWatch Alarms ─ SNS ─ メール通知
```

| サービス | 役割 | 学べること |
|---|---|---|
| CloudFront + S3 | 画面の配信。`/api/*` だけ API へ転送（同一ドメインなので CORS 不要） | CDN、OAC、キャッシュポリシー |
| API Gateway (HTTP API) | `GET /api/races` `/api/predict` `/api/manual` `/api/stats` | ルーティング、スロットリング |
| Lambda ×5 | api / worker / refresh / precompute / settle | 非同期呼び出し、権限(IAM)、arm64 |
| DynamoDB | 予想・履歴・成績（単一テーブル設計、TTL で自動削除） | キー設計、TTL、オンデマンド課金 |
| EventBridge | 毎朝の開催一覧更新、開催日は毎時の事前計算、夕方の結果照合 | cron、サーバーレスな定期実行 |
| CloudWatch Logs | ログ（14日で自動削除） | ログ保持期間 |
| AWS Budgets（任意） | 月5ドル超過の警告メール | 料金管理 |

### 処理の流れ（なぜ「事前計算」なのか）
10,000 回のシミュレーション自体は 0.1 秒未満です。時間がかかるのは外部サイトからのデータ取得（10〜30 秒）なので、
**開催前に Lambda が予想を作って DynamoDB に保存し、利用者は保存済みの結果を見るだけ**にしています。
利用者が増えても外部サイトへのアクセス数は増えません（ここが公開運用で最も重要な設計です）。
画面（HTML）は worker が作って保存します。券種を切り替えると、保存済みのその券種の画面を取得します（再計算はしません）。予算の配分・ペース指定・馬場や取消の仮定は Colab 版のみです。

- 開催レース一覧: 毎日 05:30(JST) に更新（refresh）
- 予想の事前計算: 開催日の朝〜夕に毎時（precompute → worker）。既定は重賞とメイン(11R)のみ
- 未計算のレースを開いた場合: worker を非同期に起動し、画面が数秒ごとに確認（オンデマンド）
- 結果の照合・成績集計: 土日 18:30 と月曜 10:00(JST)（settle）。発走前の予想だけを対象にします

## 前提
- AWS アカウント、AWS CLI の認証設定（`aws configure`、または CloudShell）
- Python 3.12 以上、Node.js（CDK CLI 用）
- 本リポジトリのコマンドは Linux/macOS/CloudShell 向けです

## デプロイ手順

```bash
# 1. CDK CLI と Python ライブラリ
npm install -g aws-cdk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Lambda に載せるコードとライブラリを作る（Docker 不要。arm64 / Python3.12 向け）
./scripts/build_lambda.sh

# 3. 初回のみ（アカウント/リージョンごとに1回）
cdk bootstrap

# 4. デプロイ（アクセスコードと料金警告メールは任意）
cdk deploy -c access_code=好きな合言葉 -c budget_email=you@example.com
```

デプロイが終わると `SiteUrl`（公開 URL）などが表示されます。

```bash
# 5. 初回だけ手動で開催一覧を作る（以降は毎朝自動）。関数名は出力の RefreshName
aws lambda invoke --function-name <RefreshName> --cli-read-timeout 300 /dev/stdout
```

数分後に `SiteUrl` を開くと、直近の開催レースが選べます。

### 設定（`-c キー=値`）
| キー | 既定 | 意味 |
|---|---|---|
| `access_code` | なし | 設定すると、画面で合言葉の入力が必要になります（下記の注意参照） |
| `budget_email` | なし | 設定すると、月額が 5 ドルの 80% を超えたときにメールが届きます |
| `alert_email` | `budget_email` と同じ | Lambdaのエラー・API 5xx・スロットリングのアラームを通知するメール（初回は届く確認メールで承認が必要） |
| `precompute_mode` | `main` | `all` にすると全レースを事前計算（外部サイトへのアクセスが増えます） |
| `worker_concurrency` | 0 | worker の同時実行数の上限。新規アカウントは上限が小さく設定できないことがあるので、通常は 0 のまま |
| `autotune` | false | `true` で、結果の照合時に係数の自動調整も行う（30 レース以上たまってから） |

## ローカルでのテスト（AWS・外部サイトに接続しません）

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests          # ハンドラの結合テスト（DynamoDB は moto で擬似）
python tests/ui_check.py           # 画面をヘッドレスブラウザで操作（要 playwright）
CDK_OUTDIR=cdk.out python app.py   # テンプレートの生成だけ確認（デプロイしない）
```

## 費用の目安
個人利用〜少人数なら、多くの項目が無料枠内に収まる想定です（Lambda・DynamoDB・API Gateway・CloudFront）。
実際の請求は、アクセス数や事前計算の件数で変わります。**デプロイ後は Billing の画面で最初の1〜2週間を確認してください。**
（設定の `budget_email` を使うと、超過の手前で気づけます）

## 後片付け
```bash
cdk destroy
```
S3 バケット・DynamoDB のデータごと削除されます（学習用に `RemovalPolicy.DESTROY` にしています。
本番運用するなら `infra/keiba_stack.py` の DynamoDB を `RETAIN` に変更してください）。

## 公開する前に必ず読んでください

1. **データ元サイトの利用規約**: netkeiba・Yahoo!スポーツナビの自動取得が許されるかは、各サイトの規約・robots.txt を
   ご自身で確認してください。この構成は「利用者数に関係なくアクセス数を一定にする」作りですが、
   **不特定多数に公開するサービスで使ってよい保証はありません**。まず自分と知人だけで使い、
   本格的に公開する場合は JRA-VAN Data Lab. など正式なデータ提供サービスへの切り替えを検討してください。
   （競馬ラボは自動取得を禁止しているため使っていません。）
2. **アクセスコードは簡易的な制限です**: 合言葉を知っていれば誰でも入れます（入力後はブラウザに保存され、通信ごとに送られます）。
   本格的な認証が必要なら Amazon Cognito + CloudFront(Lambda@Edge など)、不正アクセス対策には AWS WAF の導入を検討してください。
3. **Open-Meteo の無料枠は非商用向け**です（商用は有料プランが必要）。
4. **表示の注意書き**（娯楽・参考用、20歳未満の馬券購入禁止、のめり込み注意）は残してください。
5. **未検証の解析部分**: Yahoo の出馬表・オッズ・結果・払戻、netkeiba の調教ページなどの読み取りは、実際のページ構造に合わせて
   未検証です。最初に画面の「データ取得状況」で △ が出ていないか確認し、出ていたら `backend/keiba_core.py` の
   該当の `parse_*` 関数を調整してください（読めない項目は使わずに動作します）。

## ファイル構成
```
app.py, cdk.json          CDK のエントリ
infra/keiba_stack.py      AWS リソースの定義（ここを読むとAWS構成が分かります）
backend/keiba_core.py     予想ロジック本体（取得・能力推定・シミュレーション・推奨馬券・成績集計）
backend/handlers.py       Lambda ハンドラ（api / worker / refresh / precompute / settle）
backend/store.py          DynamoDB の読み書き（単一テーブル設計の説明つき）
frontend/index.html       画面（HTML+JS 1ファイル。ビルド不要）
scripts/build_lambda.sh   Lambda 用の配布物を作る
tests/                    結合テスト・画面チェック・外部サイトの擬似データ
```

## 次の学習ステップ（発展）
- AWS WAF（レート制限）と Cognito 認証の追加
- 独自ドメイン（Route 53 + ACM）
- CloudWatch アラーム（Lambda エラー、worker の失敗率）と X-Ray によるトレース
- DynamoDB の履歴を S3 + Athena で分析（回収率・較正の長期推移）
- GitHub Actions + OIDC による自動デプロイ
