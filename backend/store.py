"""DynamoDB（単一テーブル設計）の読み書き。

  pk                 sk              内容
  DAY#2026-11-01     RACES           その日のレース一覧
  RACE#202608040911  PRED            予想結果（画面用JSON）
  RACE#202608040911  REQ / ERR       取得リクエスト中・失敗のマーカー（短いTTL）
  HIST               2026-11-01#id   答え合わせ用の予想記録（結果確定後はresultが入る）
  CFG                PARAMS / STATS  自動調整値 / 成績集計

値はJSON文字列として `data` に入れる（DynamoDBのDecimal問題を避けるため）。
"""
import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def reset():
    global _table
    _table = None


def put(pk, sk, obj, ttl_sec=None):
    item = {"pk": pk, "sk": sk, "data": json.dumps(obj, ensure_ascii=False), "updated_at": int(time.time())}
    if ttl_sec:
        item["ttl"] = int(time.time()) + int(ttl_sec)
    table().put_item(Item=item)


def get(pk, sk):
    """→ (obj, updated_at) / (None, None)"""
    it = table().get_item(Key={"pk": pk, "sk": sk}).get("Item")
    if not it or int(it.get("ttl", 1 << 60)) < time.time():
        return None, None
    return json.loads(it["data"]), int(it.get("updated_at", 0))


def delete(pk, sk):
    table().delete_item(Key={"pk": pk, "sk": sk})


def query(pk):
    items, kw = [], {"KeyConditionExpression": Key("pk").eq(pk)}
    while True:
        r = table().query(**kw)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return [json.loads(i["data"]) for i in items]
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
