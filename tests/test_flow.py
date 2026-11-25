"""ハンドラの結合テスト（DynamoDBはmotoで擬似。外部サイトも擬似）。実際のAWS・外部サイトには接続しない。"""
import json
import os

import boto3
import pytest
from moto import mock_aws

import mock_sites as ms


@pytest.fixture()
def env(monkeypatch):
    with mock_aws():
        ddb = boto3.client("dynamodb")
        ddb.create_table(TableName="t", BillingMode="PAY_PER_REQUEST",
                         AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
                         KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}])
        monkeypatch.setenv("TABLE_NAME", "t")
        monkeypatch.setenv("WORKER_NAME", "worker")
        import keiba_core as core, store, handlers
        store.reset()
        monkeypatch.setattr(core, "fetch_text", ms.fake_fetch_text)
        monkeypatch.setattr(core, "fetch_json", lambda url, ttl=0: ms.meteo())
        monkeypatch.setattr(core, "discover_races", lambda weeks=2, force=False: ms.races())
        calls = []
        monkeypatch.setattr(handlers, "_invoke_worker", lambda rid, d, force=True: (calls.append(rid), handlers.worker_handler({"race_id": rid, "date": d})))
        monkeypatch.setattr(handlers, "ACCESS_CODE_UNUSED", None, raising=False)
        yield core, store, handlers, calls


def call(h, path, **q):
    r = h.api_handler({"rawPath": path, "queryStringParameters": q or None, "headers": {}})
    return r["statusCode"], json.loads(r["body"])


def test_full_flow(env):
    core, store, h, calls = env
    out = h.refresh_handler()
    assert out["days"] == 1 and out["queued"] == 2 or out["queued"] >= 1          # メイン/重賞だけ事前計算
    code, body = call(h, "/api/races")
    assert code == 200 and body["days"][0]["races"][0]["race_id"]
    d = ms.RACE_DAY.isoformat()
    code, body = call(h, "/api/predict", date=d, race_id=ms.RACE_ID)
    assert code == 200 and body["status"] == "ready"
    html = body["html"]
    assert "予想順位" in html and "展開図" not in html and "hztabs" not in html and "買い目メモ" not in html and "共有用" not in html
    assert "ペース別の予想" not in html and "各馬の評価理由" not in html
    assert json.dumps(body)                                                      # JSON化できる
    # 券種の切り替え（別の画面が返る・数字は共通）
    _, b2 = call(h, "/api/predict", date=d, race_id=ms.RACE_ID, mode="3連単")
    assert b2["status"] == "ready" and b2["html"] != html and "3連単" in b2["html"]
    import re
    pct = lambda x: re.findall(r"<b>(\d+\.\d)%</b>", x)[:18]
    assert pct(html) == pct(b2["html"])
    _, b3 = call(h, "/api/predict", date=d, race_id=ms.RACE_ID, mode="でたらめ")
    assert b3["html"] == html                                                    # 不正な券種は標準へ
    # 未計算のレース → 取得依頼 → (擬似的に同期実行) → ready
    code, body = call(h, "/api/predict", date=d, race_id="202608040902")
    assert code in (200, 202)
    code, body = call(h, "/api/predict", date=d, race_id="202608040902")
    assert body["status"] == "ready"
    # 不正な入力
    assert call(h, "/api/predict", date="x", race_id="1")[0] == 400
    assert call(h, "/api/predict", date=d, race_id="ovs:../x")[0] == 400
    assert call(h, "/api/predict", date=d, race_id="999999999999")[0] in (200, 404)
    # 結果の照合: 保存された予想の日付を過去にして実行
    for r in store.query("HIST"):
        old_sk = f"{r['date']}#{r['race_id']}"
        store.delete("HIST", old_sk)
        r["date"] = "2026-01-01"
        store.put("HIST", f"2026-01-01#{r['race_id']}", r)
    s = h.settle_handler()
    assert s["settled"] == 2
    code, body = call(h, "/api/stats")
    assert body["stats"]["n"] == 2 and body["stats"]["markets"]


def test_old_page_version_is_recomputed(env):
    """コード更新前に保存されたページ（verが違う）は表示せず、再計算してから返す"""
    core, store, h, calls = env
    h.refresh_handler()
    d = ms.RACE_DAY.isoformat()
    pred, _ = store.get(f"RACE#{ms.RACE_ID}", "PRED")
    assert pred["ver"] == core.PAGE_VER
    pred["ver"] = "old"
    store.put(f"RACE#{ms.RACE_ID}", "PRED", pred)
    n = len(calls)
    code, body = call(h, "/api/predict", date=d, race_id=ms.RACE_ID)
    assert len(calls) == n + 1                                        # 再計算が依頼された
    code, body = call(h, "/api/predict", date=d, race_id=ms.RACE_ID)
    assert body["status"] == "ready" and "展開図" not in body["html"]


def test_access_code(env, monkeypatch):
    core, store, h, calls = env
    monkeypatch.setenv("ACCESS_CODE", "secret")
    r = h.api_handler({"rawPath": "/api/races", "headers": {}})
    assert r["statusCode"] == 401
    r = h.api_handler({"rawPath": "/api/races", "headers": {"X-Access-Code": "secret"}})
    assert r["statusCode"] == 200


def test_failure_marker(env, monkeypatch):
    core, store, h, calls = env
    h.refresh_handler()
    monkeypatch.setattr(core, "fetch_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("403")))
    h.worker_handler({"race_id": "202608040902", "date": ms.RACE_DAY.isoformat()})
    code, body = call(h, "/api/predict", date=ms.RACE_DAY.isoformat(), race_id="202608040902")
    assert body["status"] == "failed"


def test_stored_size(env):
    """DynamoDBの1項目400KB上限に余裕があること（画面は圧縮して1券種ごとに別項目で保存）"""
    core, store, h, calls = env
    h.refresh_handler()
    biggest = 0
    for m in core.PAGE_MODES:
        it = store.table().get_item(Key={"pk": f"RACE#{ms.RACE_ID}", "sk": f"PAGE#{m}"}).get("Item")
        assert it, m
        biggest = max(biggest, len(it["data"].encode()))
    meta = store.table().get_item(Key={"pk": f"RACE#{ms.RACE_ID}", "sk": "PRED"})["Item"]
    print("page bytes max", biggest, "meta", len(meta["data"].encode()))
    assert biggest < 120_000 and len(meta["data"].encode()) < 50_000


def test_history_record(env):
    core, store, h, calls = env
    h.refresh_handler()
    recs = store.query("HIST")
    assert recs and all(r["stars"] is not None or "stars" in r for r in recs)


def test_overseas(env, monkeypatch):
    """海外レース（race_id=ovs:…）: 一覧に出て、予想でき、成績には記録されない"""
    core, store, h, calls = env
    import mock_sites as ms2
    key = "2026arc"
    monkeypatch.setattr(core, "discover_races", lambda weeks=2, force=False: ms2.races_with_overseas())
    monkeypatch.setattr(core, "overseas_manual", lambda ref, force=False: ms2.ovs_manual(core))
    h.refresh_handler()
    _, lst = call(h, "/api/races")
    assert any(r["race_id"] == "ovs:2026arc" and r["region"] == "海外" for r in lst["days"][0]["races"])
    d = ms.RACE_DAY.isoformat()
    code, _b = call(h, "/api/predict", date=d, race_id=f"ovs:{key}")             # 事前計算の対象（海外）。同期実行されて保存済み
    _, body = call(h, "/api/predict", date=d, race_id=f"ovs:{key}")
    assert body["status"] == "ready" and "予想順位" in body["html"] and "海外" in body["html"]
    assert [r for r in store.query("HIST") if str(r["race_id"]).startswith("ovs:")] == []


def test_manual_race(env, monkeypatch):
    core, store, h, calls = env
    text = "1,アルファ,騎手A,3.2,逃\n2,ブラボー,騎手B,4.5,先\n3,チャーリー,騎手C,6.0,差\n4,デルタ,騎手D,9.8\n5,エコー,騎手E,15.0,追\n6,フォックス,騎手F,30.0"

    class FakeBody:
        def __init__(self, d): self.d = d
        def read(self): return self.d

    def fake_invoke(**kw):
        out = h.worker_handler(json.loads(kw["Payload"]))
        return {"Payload": FakeBody(json.dumps(out).encode())}
    monkeypatch.setattr(h, "_client", lambda: type("C", (), {"invoke": staticmethod(fake_invoke)})())
    code, body = call(h, "/api/manual", name="テストS", venue="海外", surface="芝", distance="2400", condition="良", text=text)
    assert code == 200 and "予想順位" in body["html"] and "手入力" in body["html"]
    _, b2 = call(h, "/api/manual", name="テストS", venue="海外", surface="芝", distance="2400", condition="良", text=text, mode="馬連")
    assert "馬連" in b2["html"]
    assert store.query("HIST") == []                              # 手入力は成績に記録しない
    code, body = call(h, "/api/manual", text="1,あ,い,0.5")
    assert code == 400 and "オッズ" in body["error"] or "4頭" in body["error"]
    assert call(h, "/api/manual", text=text, distance="abc")[0] == 400
    assert call(h, "/api/manual", text="x" * 4000)[0] == 400
