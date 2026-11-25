"""Lambdaハンドラ一式。

  api_handler        … HTTP API（GET /api/races, /api/predict, /api/stats）
  worker_handler     … 1レースを分析してDynamoDBに保存（事前計算・オンデマンド共通）
  refresh_handler    … 開催レース一覧の更新（毎朝）→ そのまま事前計算を起動
  precompute_handler … 直近の開催レースを順に worker へ（開催日は毎時）
  settle_handler     … 結果の照合と成績集計（開催日の夕方・月曜朝）
"""
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta

import boto3

import keiba_core as core
import store

STALE_SEC = int(os.environ.get("STALE_SEC", "1800"))          # これより古い予想は再計算を依頼
PRECOMPUTE_MODE = os.environ.get("PRECOMPUTE_MODE", "main")   # main=重賞+メインR / all=全レース
PRECOMPUTE_DAYS = int(os.environ.get("PRECOMPUTE_DAYS", "3"))
ALLOW_ONDEMAND = os.environ.get("ALLOW_ONDEMAND", "true").lower() == "true"
AUTOTUNE = os.environ.get("AUTOTUNE", "false").lower() == "true"
_lambda = None


def _client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda")
    return _lambda


def _invoke_worker(race_id, date_str, force=True):
    _client().invoke(FunctionName=os.environ["WORKER_NAME"], InvocationType="Event",
                     Payload=json.dumps({"race_id": race_id, "date": date_str, "force": force}).encode())


def _load_params():
    p, _ = store.get("CFG", "PARAMS")
    core.set_params((p or {}).get("blend"), (p or {}).get("scale"))


def _races_of(date_str):
    d, _ = store.get(f"DAY#{date_str}", "RACES")
    return (d or {}).get("races", [])


def _resp(code, body, cache=0):
    return {"statusCode": code, "headers": {"content-type": "application/json; charset=utf-8",
                                            "cache-control": f"public, max-age={cache}" if cache else "no-store"},
            "body": json.dumps(body, ensure_ascii=False)}


# ---------------------------------------------------------------- API
def api_handler(event, context=None):
    code = os.environ.get("ACCESS_CODE", "")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if code and not hmac.compare_digest(headers.get("x-access-code", ""), code):
        return _resp(401, {"error": "unauthorized"})
    path = event.get("rawPath", "")
    q = event.get("queryStringParameters") or {}
    try:
        if path.endswith("/api/races"):
            return _api_races()
        if path.endswith("/api/predict"):
            return _api_predict(q.get("date", ""), q.get("race_id", ""), q.get("mode", ""))
        if path.endswith("/api/manual"):
            return _api_manual(q)
        if path.endswith("/api/stats"):
            st, _ = store.get("CFG", "STATS")
            params, _ = store.get("CFG", "PARAMS")
            return _resp(200, {"stats": st or {"n": 0, "pending": 0}, "params": params or {}}, cache=60)
        return _resp(404, {"error": "not found"})
    except Exception as e:                                       # 詳細は出さずログへ
        print("ERROR", repr(e))
        return _resp(500, {"error": "internal error"})


def _api_races():
    today = datetime.now(core.JST).date()
    days = []
    for i in range(15):
        d = (today + timedelta(days=i)).isoformat()
        races = _races_of(d)
        if races:
            days.append({"date": d, "races": races})
    return _resp(200, {"days": days}, cache=120)


RACE_ID_PAT = r"\d{12}|ovs:[0-9A-Za-z_\-]{3,40}"          # 国内=12桁 / 海外=ovs:キー


def _mode_of(m):
    return m if m in core.PAGE_MODES else core.PAGE_MODES[0]


def _page_of(race_id, mode):
    d, _ = store.get(f"RACE#{race_id}", f"PAGE#{mode}")
    return core.unpack_html(d["z"]) if d else None


def _api_predict(date_str, race_id, mode=""):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str) or not re.fullmatch(RACE_ID_PAT, race_id):
        return _resp(400, {"error": "bad request"})
    mode = _mode_of(mode)
    pred, ts = store.get(f"RACE#{race_id}", "PRED")
    html = _page_of(race_id, mode) if pred and pred.get("ver") == core.PAGE_VER else None   # 古い作りのページは出さず再計算
    if pred and html:
        age = int(time.time()) - ts
        refreshing = False
        if age > STALE_SEC and not pred.get("started") and ALLOW_ONDEMAND and _request(date_str, race_id):
            refreshing = True
        return _resp(200, {"status": "ready", "age_sec": age, "refreshing": refreshing, "html": html,
                           "title": pred.get("name") or "", "race_id": race_id})
    err, _ = store.get(f"RACE#{race_id}", "ERR")
    if err:
        return _resp(200, {"status": "failed", "message": err.get("message", "")})
    if not ALLOW_ONDEMAND:
        return _resp(200, {"status": "none"})
    if _request(date_str, race_id) is None:
        return _resp(404, {"error": "unknown race"})
    return _resp(202, {"status": "pending"})


def _api_manual(q):
    """海外など自動取得できないレース: 入力された出走表をその場で計算して返す（保存しない）"""
    m = {"name": q.get("name", "")[:60], "venue": q.get("venue", "")[:30],
         "surface": q.get("surface", "芝") if q.get("surface") in ("芝", "ダ") else "芝",
         "distance": q.get("distance", "2000"), "condition": q.get("condition", "良")
         if q.get("condition") in ("良", "稍重", "重", "不良") else "良", "text": q.get("text", "")}
    if len(m["text"]) > 3000 or not re.fullmatch(r"\d{3,4}", str(m["distance"])):
        return _resp(400, {"error": "入力が長すぎるか、距離が正しくありません"})
    try:
        core.parse_manual(m["text"])
    except core.ManualInputError as e:
        return _resp(400, {"error": str(e)})
    r = _client().invoke(FunctionName=os.environ["WORKER_NAME"], InvocationType="RequestResponse",
                         Payload=json.dumps({"manual": m, "mode": _mode_of(q.get("mode", ""))}).encode())
    out = json.loads(r["Payload"].read() or b"{}")
    if r.get("FunctionError") or "html" not in out:
        print("MANUAL ERROR", str(out)[:300])
        return _resp(500, {"error": "計算できませんでした"})
    return _resp(200, {"status": "ready", "age_sec": 0, "refreshing": False, "html": out["html"], "title": out.get("title", "")})


def _request(date_str, race_id):
    """未計算/古い予想の再計算を依頼。短時間の重複依頼は1回にまとめる。未知のレースはNone"""
    if not any(r["race_id"] == race_id for r in _races_of(date_str)):
        return None
    req, _ = store.get(f"RACE#{race_id}", "REQ")
    if req:
        return False
    store.put(f"RACE#{race_id}", "REQ", {"at": int(time.time())}, ttl_sec=300)
    _invoke_worker(race_id, date_str)
    return True


# ---------------------------------------------------------------- Worker
def worker_handler(event, context=None):
    if event.get("manual"):                                      # 手入力レース（保存しない・指定の券種だけ計算）
        _load_params()
        mode = _mode_of(event.get("mode", ""))
        R, pages, _ = core.analyze_pages("manual", manual=event["manual"], modes=[mode])
        return {"html": pages[mode], "title": R["info"]["name"] or ""}
    race_id = event["race_id"]
    _load_params()
    old, _ = store.get(f"RACE#{race_id}", "PRED")
    if old and old.get("snap"):
        core._snap[race_id] = old["snap"]                        # 前回との比較用
    try:
        R, pages, snap = core.analyze_pages(race_id, force=bool(event.get("force", True)))
        for mode, h in pages.items():                            # 画面を先に保存し、最後に目次(PRED)を保存する
            store.put(f"RACE#{race_id}", f"PAGE#{mode}", {"z": core.pack_html(h)}, ttl_sec=30 * 86400)
        meta = core.page_meta(R)
        meta["snap"] = core._jsonable(snap)
        store.put(f"RACE#{race_id}", "PRED", meta, ttl_sec=30 * 86400)
        recordable = not R["started"] and R["race_dt"] and not R.get("exp") and not R["manual"] and not race_id.startswith("ovs:")
        if recordable:                                           # 発走前の標準の予想だけを成績の対象にする
            rec = core.build_record(R)
            sk = f"{rec['date']}#{race_id}"
            cur, _ = store.get("HIST", sk)
            if not (cur and cur.get("result")):
                store.put("HIST", sk, rec)
        store.delete(f"RACE#{race_id}", "ERR")
    except Exception as e:
        print("WORKER ERROR", race_id, repr(e))
        store.put(f"RACE#{race_id}", "ERR", {"message": "データを取得できませんでした。時間をおいて再度お試しください。"}, ttl_sec=300)
    finally:
        store.delete(f"RACE#{race_id}", "REQ")
    return {"race_id": race_id}


# ---------------------------------------------------------------- Scheduled
def refresh_handler(event=None, context=None):
    races = core.discover_races(weeks=2, force=True)
    by_day = {}
    for r in races:
        r = dict(r, date=r["date"].isoformat())
        by_day.setdefault(r["date"], []).append(r)
    for d, rs in by_day.items():
        store.put(f"DAY#{d}", "RACES", {"date": d, "races": rs}, ttl_sec=21 * 86400)
    out = precompute_handler()
    return {"days": len(by_day), **out}


def precompute_handler(event=None, context=None):
    today = datetime.now(core.JST).date()
    n = 0
    for i in range(PRECOMPUTE_DAYS + 1):
        d = (today + timedelta(days=i)).isoformat()
        for r in _races_of(d):
            if PRECOMPUTE_MODE == "main" and not (r["grade"] or r["rno"] == 11 or r.get("region") == "海外"):
                continue
            pred, ts = store.get(f"RACE#{r['race_id']}", "PRED")
            if pred and pred.get("ver") == core.PAGE_VER and (pred.get("started") or int(time.time()) - ts < 1500):
                continue
            _invoke_worker(r["race_id"], d)
            n += 1
    return {"queued": n}


def settle_handler(event=None, context=None):
    today = datetime.now(core.JST).date().isoformat()
    recs = store.query("HIST")
    done = waiting = 0
    for r in recs:
        if r.get("result"):
            continue
        if r["date"] > today:
            waiting += 1
            continue
        try:
            res = core.fetch_result(r["race_id"])
        except Exception as e:
            print("SETTLE ERROR", r["race_id"], repr(e))
            continue
        if not res:
            waiting += 1
            continue
        r["result"] = res
        store.put("HIST", f"{r['date']}#{r['race_id']}", r)
        done += 1
    msg = ""
    if AUTOTUNE:
        params, _ = store.get("CFG", "PARAMS")
        cb, cs = (params or {}).get("blend", 0.2), (params or {}).get("scale", 1.0)
        changed, msg, newp = core.tune_params(recs, cb, cs)
        if changed:
            store.put("CFG", "PARAMS", newp)
    stats = core.compute_stats({"records": recs})
    stats["pending"] = len([r for r in recs if not r.get("result")])
    store.put("CFG", "STATS", core._jsonable(stats))
    return {"settled": done, "waiting": waiting, "tune": msg}
