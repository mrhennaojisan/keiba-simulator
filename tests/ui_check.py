"""画面(index.html)をヘッドレスブラウザで開き、擬似APIで描画・操作できるか確認する。"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend")); sys.path.insert(0, os.path.dirname(__file__))
os.environ.update(AWS_DEFAULT_REGION="ap-northeast-1", AWS_ACCESS_KEY_ID="t", AWS_SECRET_ACCESS_KEY="t", TABLE_NAME="t", WORKER_NAME="w")
import boto3
from moto import mock_aws
import mock_sites as ms

with mock_aws():
    boto3.client("dynamodb").create_table(TableName="t", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}])
    import keiba_core as core, store, handlers
    core.fetch_text = ms.fake_fetch_text; core.fetch_json = lambda u, ttl=0: ms.meteo(); core.discover_races = lambda weeks=2, force=False: ms.races()
    handlers._invoke_worker = lambda rid, d, force=True: handlers.worker_handler({"race_id": rid, "date": d})
    handlers.refresh_handler()
    class _B:
        def __init__(s, d): s.d = d
        def read(s): return s.d
    handlers._client = lambda: type("C", (), {"invoke": staticmethod(lambda **kw: {"Payload": _B(json.dumps(handlers.worker_handler(json.loads(kw["Payload"]))).encode())})})()
    for r in store.query("HIST"):                                    # 成績画面用に結果も作る
        store.delete("HIST", f"{r['date']}#{r['race_id']}"); r["date"] = "2026-01-01"; store.put("HIST", f"2026-01-01#{r['race_id']}", r)
    handlers.settle_handler()
    def api(path, q):
        r = handlers.api_handler({"rawPath": path, "queryStringParameters": q or None, "headers": {}})
        return r["statusCode"], r["body"]

    from playwright.sync_api import sync_playwright
    html = open(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"), encoding="utf-8").read()
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, w, scheme in (("pc", 900, "light"), ("sp", 390, "dark")):
            pg = b.new_page(viewport={"width": w, "height": 900}, color_scheme=scheme)
            pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            def route(r):
                u = r.request.url
                if u.endswith("/"): return r.fulfill(status=200, content_type="text/html", body=html)
                path = u.split("//")[1].split("/", 1)[1].split("?")[0]; path = "/" + path
                from urllib.parse import parse_qsl
                q = dict(parse_qsl(u.split("?")[1])) if "?" in u else {}
                code, body = api(path, q); r.fulfill(status=code, content_type="application/json", body=body)
            pg.route("**/*", route)
            pg.goto("http://site.test/")
            pg.wait_for_selector("#race option", state="attached")
            assert pg.locator("#race option").count() >= 1
            pg.select_option("#ticket", "全種類")
            pg.click("#go"); pg.wait_for_selector("#out .ks .tk", timeout=15000)
            n_tk = pg.locator("#out .tk").count(); rows = pg.locator("#out tr.top").count()
            hero = pg.inner_text("#out .hero")
            assert "購入おすすめ度" in hero and "★" in hero, hero
            pg.select_option("#ticket", "馬連"); pg.wait_for_function("document.querySelectorAll('#out .tk').length == 1", timeout=15000)
            n2 = pg.locator("#out .tk").count(); assert n2 == 1, n2
            pg.select_option("#ticket", "おまかせ(自動)"); pg.wait_for_function("document.querySelectorAll('#out .tk').length > 1", timeout=15000)
            assert pg.locator("#out .pay").count() >= 2
            hv = [float(x.rstrip("%")) for x in pg.locator("#out .tk .st .big").all_inner_texts()]
            assert hv == sorted(hv, reverse=True), hv
            out_txt = pg.inner_text("#out")
            assert "買い目メモ" not in out_txt and "共有用" not in out_txt and "ペース別の予想" not in out_txt
            assert pg.locator("#pace").count() == 0 and pg.locator("#budget").count() == 0
            assert "展開図" not in out_txt
            pg.screenshot(path=f"/tmp/ui_{name}.png", full_page=True)
            pg.click("#region button[data-v='地方']"); assert pg.locator("#race option").first.inner_text().startswith("（開催"), pg.locator("#race option").first.inner_text()
            pg.click("#region button[data-v='手入力']")
            pg.fill("#m_text", "1,アルファ,騎手A,3.2,逃\n2,ブラボー,騎手B,4.5,先\n3,チャーリー,騎手C,6.0,差\n4,デルタ,騎手D,9.8\n5,エコー,騎手E,15.0,追")
            pg.click("#go"); pg.wait_for_selector("#out .hero", timeout=10000)
            assert "手入力レース" in pg.inner_text("#out .warn"), pg.inner_text("#out .warn")
            pg.screenshot(path=f"/tmp/ui_{name}_manual.png", full_page=True)
            pg.fill("#m_text", "1,あ,い,0.3"); pg.click("#go"); pg.wait_for_function("document.querySelector('#msg').textContent.includes('❌')", timeout=10000)
            pg.click("#region button[data-v='すべて']")
            pg.click("#t2"); pg.wait_for_selector("#stats table", timeout=5000)
            pg.screenshot(path=f"/tmp/ui_{name}_stats.png", full_page=True)
            print(name, "tickets(全種類)=", n_tk, "top rows=", rows, "馬連=", n2, "ok")
        b.close()
    print("JS errors:", errors or "none")
