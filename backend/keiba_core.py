# ==========================================================
# 競馬レース予想シミュレーター v12（地方・海外・払戻・ペース別の予想・資金推移・荒れ方・市場との乖離・自分の印つき／娯楽・参考用）
# Colab / Jupyter / Voila 用。1セルにそのまま貼り付けて実行できます。
#
# 【データ源】
#   netkeiba            … 出馬表（枠・馬番・騎手）/ 馬柱（各馬の過去成績）/ 騎手リーディング
#   Yahoo!スポーツナビ  … 出馬表・血統・調教師 / 単勝・複勝オッズ / 各馬の過去成績(予備)
#                         / 同じ競馬場の直近レース結果（当日の馬場傾向: 内外・前残り）
#   Open-Meteo          … 天気予報（雨量から当日の馬場状態を推定。発表値があればそちらを優先）
#   nar.netkeiba.com    … 地方競馬の開催一覧・出馬表（v10で追加。中央と同じ作りのページとして解析。実ページでの動作は未検証）
#   JRA海外競馬ページ   … 海外レース（馬券が発売されるレース）の出走馬・参考オッズ（v12で追加。実ページでの動作は未検証。取得できない時はURL貼り付け／手入力）
#   ※ 競馬ラボはサイト側が自動取得(robots.txt)を禁止しているため使用しません。
#   ※ いずれかが取得できなくても、残りのデータで動作します（画面に取得状況を表示）。
#
# 【公開時の注意】
#   ・各サイトの利用規約（自動取得の可否）を必ず確認してください。大規模に公開する場合は、
#     JRA-VAN Data Lab. 等の正式なデータ提供サービスの利用を検討してください。
#   ・Open-Meteo の無料枠は非商用利用向けです（商用は有料プランが必要）。
# ==========================================================

import base64
import html as htmllib
import json
import math
import os
import re
import time
import threading
import unicodedata
from itertools import combinations, permutations
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------
# 設定
# ----------------------------------------------------------
DEV_MODE = False                  # True: 開発者向け項目（乱数シード・キャッシュ無効）を画面に表示
WEEKS_AHEAD = 2                   # 何週先の開催まで一覧に出すか
NUM_SIMULATIONS = 100000          # シミュレーション回数（10万回: 計算約0.3秒・メモリ約100MB。1万回との差は勝率のブレ±0.4pt→±0.13pt）
NOISE_SD = 4.5
UNIT = 100
MAX_TICKETS = 5
MAX_RUNS = 8                      # 能力推定に使う直近レース数
RECENCY = 0.8                     # 古いレースほど重みを下げる係数
PRIOR = 75.0                      # 出走数が少ない馬を引き寄せる事前値
PRIOR_STRENGTH = 1.5              # 事前値の強さ（出走数換算）
DEFAULT_JOCKEY = (78.0, 78.0)     # 騎手成績が取れない場合の仮値（技術, 適性）
MIN_RIDES = 100                   # リーディングで評価対象にする最低騎乗数（未満は平均へ寄せる）
ABILITY_SPREAD = 0.6              # 馬の能力差の圧縮率（1.0=そのまま。過信を防ぐ）
PACE_EFFECT = 3.0                 # 展開(脚質×ペース)が着順に与える影響の大きさ
WET_STAMINA = {"良": 0.0, "稍重": 0.10, "重": 0.25, "不良": 0.25}
ODDS_SCALE = 0.70                 # オッズ→能力差の換算係数（勝率がオッズ通りになるよう較正）
SHOW_POPULARITY = False           # 予想順位表に人気・オッズ列を出すか（取得元が確定するまで非表示）
MARKET_BLEND = 0.2                # 人気(オッズ)を能力推定に混ぜる強さ（0=人気を一切使わない）
WAKU_ADJ = {1: 0.3, 2: 0.3, 7: -0.4, 8: -0.4}   # 当日傾向が取れない時の枠の補正（既定）
BIAS_MAX_RACES = 14               # 当日の馬場傾向の集計に使う直近レース数
BIAS_MIN_SAMPLES = 9              # 傾向を使うのに必要な「3着以内の馬」の最少数
BIAS_COEF = 12.0                  # 当日傾向（内外・前残り）の補正の強さ
BIAS_CAP = 2.0                    # 当日傾向の補正の上限（能力値の加減点）
CACHE_TTL = 300
HORSE_CACHE_TTL = 3600
RESULT_CACHE_TTL = 600
REQUEST_INTERVAL = 0.4            # 同一サイトへのアクセス間隔(秒)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}
RACE_BASE = "https://race.netkeiba.com"
DB_BASE = "https://db.netkeiba.com"
YAHOO_BASE = "https://sports.yahoo.co.jp/keiba"
METEO_URL = "https://api.open-meteo.com/v1/forecast"
HISTORY_PATH = None   # 予想・結果の保存先（Colabでは再起動で消えます。Drive上のパスにすると残ります）
TUNE_MIN_RACES = 30               # 自動調整に必要な「結果が確定した予想」の最少レース数
BATCH_MAX_RACES = 12              # 一括予想で一度に処理する最大レース数
ABILITY_SCALE = 1.0               # 能力差の拡大率（自動調整で更新。1.0=標準）
SIRE_WEIGHT = 1.0                 # 血統適性の効き具合（0で無効）
TRAINING_WEIGHT = 1.0             # 調教評価の効き具合（0で無効）
NAR_BASE = "https://nar.netkeiba.com"      # 地方競馬
INCLUDE_NAR = True                # 地方競馬を一覧に含める
NAR_DAYS_AHEAD = 4                # 地方競馬は何日先まで一覧に出すか（ほぼ毎日開催のため日数で指定）
PACE_MODES = {"自動(想定)": None, "ハイペース": 1.0, "ミドルペース": 0.0, "スローペース": -1.0}   # ペース指定（+:ハイ）
PACE_FIXED_SD = 0.35              # ペースを指定した時の、レースごとのペースのばらつき（自動は0.7）
MANUAL_BLEND = 0.9                # 手入力レース（過去成績なし）で単勝オッズを能力に混ぜる強さ
RETURN_RATE_JRA = {"単勝": 0.80, "複勝": 0.80, "枠連": 0.775, "馬連": 0.775, "ワイド": 0.775,
                   "馬単": 0.75, "3連複": 0.75, "3連単": 0.725}      # 払戻率（売上のうち払い戻す割合。目安）
RETURN_RATE_NAR = {"単勝": 0.80, "複勝": 0.80, "枠連": 0.75, "馬連": 0.75, "ワイド": 0.75,
                   "馬単": 0.75, "3連複": 0.75, "3連単": 0.70}        # 地方は主催者ごとに異なる（目安）




# ----------------------------------------------------------
# 1. 共通ユーティリティ（サイトごとにアクセス間隔を制御・スレッド安全）
# ----------------------------------------------------------
_cache = {}
_cache_lock = threading.Lock()
_host_last = {}
_host_lock = threading.Lock()


def normalize_name(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", "", s)


def norm_text(s):
    """NFKC正規化し、空白を1つにまとめる（名前の中の空白は残す）"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


JOCKEY_ALIAS = {"Cルメール": "C.ルメール", "Cデムーロ": "C.デムーロ", "Mデムーロ": "M.デムーロ"}


def normalize_jockey(s):
    s = normalize_name(s)
    return JOCKEY_ALIAS.get(s, s)


def _throttle(url):
    host = re.match(r"https?://([^/]+)", url).group(1)
    with _host_lock:
        now = time.time()
        wait = REQUEST_INTERVAL - (now - _host_last.get(host, 0.0))
        _host_last[host] = now + max(wait, 0.0)     # 先に枠を確保（並列でも間隔を守る）
    if wait > 0:
        time.sleep(wait)


def _cache_get(url, ttl):
    with _cache_lock:
        hit = _cache.get(url)
    return hit[1] if hit and time.time() - hit[0] < ttl else None


def fetch_text(url, force=False, ttl=CACHE_TTL):
    if not force:
        cached = _cache_get(url, ttl)
        if cached is not None:
            return cached
    _throttle(url)
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    raw = r.content
    text = None
    for enc in ("utf-8", "euc-jp", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("euc-jp", errors="replace")
    with _cache_lock:
        _cache[url] = (time.time(), text)
    return text


def fetch_json(url, ttl=CACHE_TTL):
    cached = _cache_get(url, ttl)
    if cached is not None:
        return cached
    _throttle(url)
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    js = r.json()
    with _cache_lock:
        _cache[url] = (time.time(), js)
    return js


def _int(text):
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def _float(text):
    m = re.search(r"\d+(?:\.\d+)?", (text or "").replace(",", ""))
    return float(m.group()) if m else None


def find_col(headers, key):
    """見出しから列位置を取得（完全一致→部分一致の順）"""
    for i, h in enumerate(headers):
        if h == key:
            return i
    for i, h in enumerate(headers):
        if key in h:
            return i
    return None





# ----------------------------------------------------------
# 2a. 開催レースの自動検出（今日から先の開催日 → レース一覧）
# ----------------------------------------------------------
JST = timezone(timedelta(hours=9))
WEEKDAY = "月火水木金土日"
VENUE = {"01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
         "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"}
# 地方競馬の競馬場コード（netkeibaのrace_id 5〜6桁目。ばんえい(帯広)は競走の性質が違うため対象外）
NAR_VENUE = {"30": "門別", "35": "盛岡", "36": "水沢", "42": "浦和", "43": "船橋", "44": "大井", "45": "川崎",
             "46": "金沢", "47": "笠松", "48": "名古屋", "50": "園田", "51": "姫路", "54": "高知", "55": "佐賀"}
ALL_VENUE = {**VENUE, **NAR_VENUE}


def is_nar(race_id):
    return str(race_id)[4:6] in NAR_VENUE


def race_base(race_id):
    return NAR_BASE if is_nar(race_id) else RACE_BASE


def today_jst():
    return datetime.now(JST).date()


def candidate_dates(weeks):
    """今日〜weeks週先までの開催日候補。トップページの開催日リンク ＋ 土日"""
    today = today_jst()
    end = today + timedelta(days=7 * weeks)
    dates = set()
    d = today
    while d <= end:
        if d.weekday() in (5, 6):
            dates.add(d)
        d += timedelta(days=1)
    try:   # 祝日開催(月曜など)も拾うため、トップページ上の開催日も加える
        html = fetch_text(f"{RACE_BASE}/top/", ttl=CACHE_TTL)
        for ymd in set(re.findall(r"kaisai_date=(\d{8})", html)):
            try:
                dd = datetime.strptime(ymd, "%Y%m%d").date()
            except ValueError:
                continue
            if today <= dd <= end:
                dates.add(dd)
    except Exception:
        pass
    return sorted(dates)


def detect_grade(classes, text):
    m = re.search(r"Icon_GradeType([123])(?!\d)", classes)
    if m:
        return f"G{m.group(1)}"
    t = unicodedata.normalize("NFKC", text).upper()
    mj = re.search(r"JPN\s*(III|II|I|[123])(?![I\d])", t)
    if mj:
        return "Jpn" + {"I": "1", "II": "2", "III": "3"}.get(mj.group(1), mj.group(1))
    for pat, g in ((r"G(?:1|I)(?![I\d])", "G1"), (r"G(?:2|II)(?!I)", "G2"), (r"G(?:3|III)", "G3")):
        if re.search(pat, t):
            return g
    return ""


def parse_race_list(html, day):
    soup = BeautifulSoup(html, "html.parser")
    races, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"race_id=(\d{12})")):
        rid = re.search(r"race_id=(\d{12})", a["href"]).group(1)
        if rid in seen or rid[4:6] not in ALL_VENUE:
            continue
        seen.add(rid)
        box = a.find_parent("li") or a
        text = box.get_text(" ", strip=True)
        classes = " ".join(c for e in box.find_all(True) for c in e.get("class", []))
        title_el = box.select_one(".ItemTitle, .RaceName")
        name = title_el.get_text(strip=True) if title_el else re.sub(r"^\d{1,2}R\s*", "", text)[:20]
        m = re.search(r"(芝|ダ|障)\s*(\d{3,4})\s*m", text)
        tm = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", text)
        races.append({"date": day, "time": f"{int(tm.group(1)):02d}:{tm.group(2)}" if tm else "", "venue": ALL_VENUE[rid[4:6]], "region": "地方" if rid[4:6] in NAR_VENUE else "中央",
                      "rno": int(rid[10:12]), "race_id": rid,
                      "name": name, "grade": detect_grade(classes, text),
                      "course": f"{m.group(1)}{m.group(2)}m" if m else ""})
    return races


def nar_dates():
    today = today_jst()
    return [today + timedelta(days=i) for i in range(NAR_DAYS_AHEAD + 1)]


def discover_races(weeks=2, force=False):
    allr = []
    for day in candidate_dates(weeks):
        ymd = day.strftime("%Y%m%d")
        got = []
        for ep in ("top/race_list_sub.html", "top/race_list.html"):
            try:
                got = parse_race_list(fetch_text(f"{RACE_BASE}/{ep}?kaisai_date={ymd}", force=force), day)
            except Exception:
                got = []
            if got:
                break
        allr.extend([r for r in got if r["region"] == "中央"])
    if INCLUDE_NAR:
        for day in nar_dates():
            ymd = day.strftime("%Y%m%d")
            got = []
            for ep in ("top/race_list_sub.html", "top/race_list.html"):
                try:
                    got = parse_race_list(fetch_text(f"{NAR_BASE}/{ep}?kaisai_date={ymd}", force=force), day)
                except Exception:
                    got = []
                if got:
                    break
            allr.extend([r for r in got if r["region"] == "地方"])
    if INCLUDE_OVERSEAS:
        try:
            allr.extend(discover_overseas(force=force))
        except Exception:
            pass
    by_day = {}
    for r in allr:
        by_day.setdefault(r["date"], []).append(r)
    out_list = []
    for day in sorted(by_day):
        rs = by_day[day]
        if all(r["time"] for r in rs):                       # 発走時刻が全て取れた日は時刻順
            rs.sort(key=lambda r: (r["time"], r["rno"], r["race_id"][4:6]))
        else:                                                # 取れない日はR番号順（各場を交互に並べて概ね時間順）
            rs.sort(key=lambda r: (r["rno"], r["race_id"][4:6]))
        out_list.extend(rs)
    return out_list


def race_label(r):
    d = r["date"]
    g = f" [{r['grade']}]" if r["grade"] else ""
    t = f"{int(r['time'][:2])}:{r['time'][3:]} " if r.get("time") else ""
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]}) {t}{r['venue']}{r['rno']}R {r['name']}{g} {r['course']}".strip()


def filter_races(races, mode):
    if mode == "全レース":
        return races
    if mode == "重賞のみ":
        return [r for r in races if r["grade"] or r.get("region") == "海外"]
    return [r for r in races if r["grade"] or r["rno"] == 11 or r.get("region") == "海外"]




# ----------------------------------------------------------
# 2. 出馬表の取得（枠・馬番・騎手など）
# ----------------------------------------------------------
def waku_from_umaban(umaban, n):
    """JRAの枠番割付ルール（検証用）"""
    if n <= 8:
        sizes = [1] * n
    elif n <= 16:
        k = n - 8
        sizes = [1] * (8 - k) + [2] * k
    elif n == 17:
        sizes = [2] * 7 + [3]
    else:
        sizes = [2] * 6 + [3, 3]
    acc = 0
    for w, sz in enumerate(sizes, 1):
        acc += sz
        if umaban <= acc:
            return w
    return None


def parse_shutuba(html):
    soup = BeautifulSoup(html, "html.parser")
    info = {"name": None, "date": None, "venue_r": None, "distance": None,
            "surface": None, "condition": None, "year": None,
            "date_obj": None, "post_time": None}
    el = soup.select_one(".RaceName")
    if el:
        info["name"] = el.get_text(strip=True)
    title = soup.title.get_text(strip=True) if soup.title else ""
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\S+?\d{1,2}R)", title)
    if m:
        info["year"] = int(m.group(1))
        info["date"] = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日"
        info["venue_r"] = m.group(4)
        try:
            info["date_obj"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    if not info["name"] and title:
        info["name"] = title.split("|")[0].strip()
    d1 = soup.select_one(".RaceData01")
    d1_text = d1.get_text(" ", strip=True) if d1 else ""
    m = re.search(r"(芝|ダ|障)[^\d/]{0,4}(\d{3,4})\s*m", d1_text)
    if m:
        info["surface"] = m.group(1)
        info["distance"] = int(m.group(2))
    mt = re.search(r"(\d{1,2}):(\d{2})\s*発走", d1_text)
    if mt:
        info["post_time"] = f"{int(mt.group(1)):02d}:{mt.group(2)}"
    m = re.search(r"馬場\s*[:：]\s*(不良|稍重|良|重)", d1_text)
    if m:
        info["condition"] = m.group(1)

    # 出馬表本体のテーブルを優先（馬の行が最も多いテーブル）。無ければ全体から探す
    best_tbl, best_n = None, 0
    for t in soup.find_all("table"):
        n_rows = len([tr for tr in t.find_all("tr") if tr.find("a", href=re.compile(r"/horse/\d+"))])
        if n_rows > best_n:
            best_tbl, best_n = t, n_rows
    scope = best_tbl if best_tbl is not None else soup
    trs = scope.select("tr.HorseList") or [
        tr for tr in scope.find_all("tr") if tr.find("a", href=re.compile(r"/horse/\d+"))]
    trs = [tr for tr in trs if tr.find("tr") is None]          # 入れ子の外側の行は除外

    records, cancelled, raw_count = {}, [], 0
    for tr in trs:
        a = tr.find("a", href=re.compile(r"/horse/(\d+)"))
        if not a:
            continue
        name = normalize_name(a.get("title") or a.get_text(strip=True))
        horse_id = re.search(r"/horse/(\d+)", a["href"]).group(1)
        if "Cancel" in (tr.get("class") or []):
            if name not in cancelled:
                cancelled.append(name)
            continue
        raw_count += 1
        tds = tr.find_all("td")

        def td_by_class(pattern):
            for td in tds:
                for c in td.get("class", []):
                    if re.fullmatch(pattern, c):
                        return td
            return None

        wtd, utd = td_by_class(r"Waku\d*"), td_by_class(r"Umaban\d*")
        waku = _int(wtd.get_text()) if wtd else (_int(tds[0].get_text()) if tds else None)
        umaban = _int(utd.get_text()) if utd else (_int(tds[1].get_text()) if len(tds) > 1 else None)

        ja = tr.find("a", href=re.compile(r"/jockey/"))
        jockey = normalize_jockey((ja.get("title") or ja.get_text(strip=True)) if ja else "")
        mj = re.search(r"/jockey/(?:result/recent/|profile/)?(\d{4,6})", ja.get("href", "")) if ja else None
        jockey_id = mj.group(1) if mj else ""
        btd = td_by_class(r"Barei")
        sexage = btd.get_text(strip=True) if btd else ""
        kinryo = None
        if btd is not None:
            nxt = btd.find_next_sibling("td")
            kinryo = _float(nxt.get_text(strip=True)) if nxt else None
        wt = td_by_class(r"Weight")
        weight = None
        if wt:
            mw = re.search(r"(\d{3})\s*\(\s*([+\-]?\d+)\s*\)", wt.get_text())
            weight = f"{mw.group(1)}({mw.group(2)})" if mw else None

        rec = {"名前": name, "horse_id": horse_id, "枠": waku, "馬番": umaban,
               "騎手": jockey, "jockey_id": jockey_id, "性齢": sexage, "斤量": kinryo, "馬体重": weight}
        # 同じ馬の行が複数あれば、情報が最も揃った行を採用（足りない項目は他の行で補完）
        score = sum(x not in (None, "") for x in (umaban, waku, jockey, sexage, kinryo))
        if horse_id not in records:
            records[horse_id] = (score, len(records), rec)
        else:
            old_score, order, old = records[horse_id]
            keep, other = (rec, old) if score > old_score else (old, rec)
            for k, v in other.items():
                if keep.get(k) in (None, "") and v not in (None, ""):
                    keep[k] = v
            records[horse_id] = (max(score, old_score), order, keep)

    horses = [r for _, _, r in sorted(records.values(), key=lambda x: x[1])]
    info["duplicates"] = raw_count - len(horses)
    return info, horses, cancelled


def load_race(race_id, force=False):
    url = f"{race_base(race_id)}/race/shutuba.html?race_id={race_id}"
    info, horses, cancelled = parse_shutuba(fetch_text(url, force=force))
    if not horses:
        raise RuntimeError("出馬表から出走馬を取得できませんでした（race_idの誤り、または出馬表が未公開）")
    info["url"] = url
    info["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return info, horses, cancelled




# ----------------------------------------------------------
# 3. 過去成績の取得 → 能力・適性プロファイルの推定
# ----------------------------------------------------------
CLASS_RULES = [  # (正規表現, クラス水準) ※NFKC正規化後の文字列に適用
    (r"G(?:1|I)(?![I\d])", 100), (r"G(?:2|II)(?!I)", 94), (r"G(?:3|III)", 90),
    (r"\(L\)|リステッド", 86), (r"OP|オープン", 86), (r"3勝|1600万", 82),
    (r"2勝|1000万", 78), (r"1勝|500万", 74), (r"新馬|未勝利", 70),
]
DEFAULT_CLASS = 80
BABA_MAP = {"良": "良", "稍": "稍重", "重": "重", "不": "不良"}


def class_level(race_name):
    s = unicodedata.normalize("NFKC", race_name or "").upper().replace(" ", "")
    for pat, lv in CLASS_RULES:
        if re.search(pat, s):
            return lv
    return DEFAULT_CLASS


def parse_horse_results(html):
    """db.netkeiba.com/horse/{id} の競走成績表を見出し名から解析"""
    soup = BeautifulSoup(html, "html.parser")
    tbl = soup.select_one("table.db_h_race_results")
    if tbl is None:
        for t in soup.find_all("table"):
            head = t.get_text()[:400]
            if "着順" in head and "距離" in head:
                tbl = t
                break
    if tbl is None:
        return []
    rows = tbl.find_all("tr")
    if not rows:
        return []
    headers = [re.sub(r"\s+", "", c.get_text()) for c in rows[0].find_all(["th", "td"])]
    keys = ("日付", "開催", "レース名", "頭数", "着順", "騎手", "斤量", "距離", "馬場", "通過", "上り")
    col = {k: find_col(headers, k) for k in keys}
    if col["着順"] is None or col["距離"] is None:
        return []

    def cell(tds, key):
        i = col.get(key)
        return tds[i].get_text(strip=True) if i is not None and i < len(tds) else ""

    runs = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        rank_txt = cell(tds, "着順")
        if rank_txt in ("取", "除", "") or rank_txt.startswith(("取", "除")):
            continue
        m = re.match(r"(芝|ダ|障)\D*?(\d{3,4})", unicodedata.normalize("NFKC", cell(tds, "距離")))
        if not m or m.group(1) == "障":
            continue
        name_el = tds[col["レース名"]] if col["レース名"] is not None else None
        baba_txt = cell(tds, "馬場")[:1]
        pm = re.match(r"\d+", cell(tds, "通過"))
        runs.append({
            "date": cell(tds, "日付"),
            "venue": cell(tds, "開催"),
            "name": name_el.get_text(strip=True) if name_el else "",
            "n": _int(cell(tds, "頭数")),
            "rank": _int(rank_txt) if rank_txt[:1].isdigit() else None,   # 中止・失格など→None
            "surface": m.group(1), "dist": int(m.group(2)),
            "baba": BABA_MAP.get(baba_txt, ""),
            "pos1": int(pm.group()) if pm else None,                      # 最初のコーナー通過順
            "jockey": normalize_jockey(cell(tds, "騎手")),
            "kinryo": _float(cell(tds, "斤量")),
            "agari": _float(cell(tds, "上り")),
        })
    return runs


def run_pct(run):
    n, r = run["n"], run["rank"]
    if r is None:
        return 0.1
    if n and n > 1:
        return 1 - (min(r, n) - 1) / (n - 1)
    return 0.5


def run_score(run):
    return class_level(run["name"]) * (0.55 + 0.45 * run_pct(run))


def _wavg(vals, ws):
    ws = np.asarray(ws, float)
    return float(np.dot(vals, ws) / ws.sum())


def _fit(delta, k, coef, cap):
    """適性補正: 差分×係数を上限で丸め、サンプルが少ないほど弱める"""
    return float(np.clip(delta * coef, -cap, cap) * k / (k + 1.0))


def build_profile(runs, ctx):
    """直近成績から能力と各種適性を推定。成績なしは None
    ctx: dist, surface, venue(開催場名), condition(良/稍重/重/不良), today(date)"""
    runs = runs[:MAX_RUNS]
    if not runs:
        return None
    td, ts = ctx["dist"], ctx["surface"]
    w = np.array([RECENCY ** i * (1.0 if r["surface"] == ts else 0.6) for i, r in enumerate(runs)])
    sc = np.array([run_score(r) for r in runs])
    pct = np.array([run_pct(r) for r in runs])
    dist = np.array([r["dist"] for r in runs])
    same_s = np.array([r["surface"] == ts for r in runs])
    overall = _wavg(sc, w)

    # --- 基本能力 ---
    long_m = dist >= td - 300
    sta = _wavg(sc[long_m], w[long_m]) if long_m.any() else overall - min(10.0, (td - dist.max()) / 100 * 1.5)
    short_m = dist <= td + 200
    spd = _wavg(sc[short_m], w[short_m]) if short_m.any() else overall - min(6.0, (dist.min() - td) / 100)
    base = 34.5 + (td - 1600) / 1000 + (1.5 if ts == "ダ" else 0.0)
    kv, kw = [], []
    for s, r, wi in zip(sc, runs, w):
        if r["agari"]:
            kv.append(s + float(np.clip((base - r["agari"]) * 3, -8, 8)))
            kw.append(wi)
    kic = _wavg(kv, kw) if kv else overall
    k = len(runs) / (len(runs) + PRIOR_STRENGTH)
    shrink = lambda v: k * v + (1 - k) * PRIOR

    # --- 脚質（最初のコーナーの位置取り: 1=先頭, 0=最後方） ---
    fv, fw = [], []
    for r, wi in zip(runs, w):
        if r["pos1"] and r["n"] and r["n"] > 1:
            fv.append(1 - (min(r["pos1"], r["n"]) - 1) / (r["n"] - 1))
            fw.append(wi)
    front = _wavg(fv, fw) if fv else 0.5

    # --- 成績の安定感（ばらつきが大きい馬は番狂わせの幅も大きい） ---
    instab = float(np.clip(np.std(pct) / 0.35, 0, 1)) if len(runs) >= 3 else 0.5

    # --- 距離適性: 今回の距離±200mでの成績 − 全体 ---
    near = same_s & (np.abs(dist - td) <= 200)
    dist_fit = _fit(_wavg(sc[near], w[near]) - overall, int(near.sum()), 0.15, 3.0) if near.any() else 0.0

    # --- 馬場適性（重・不良時のみ意味を持つ）: 渋った馬場での成績 − 良馬場での成績 ---
    wet_fit = 0.0
    if ctx["condition"] != "良":
        wet = np.array([r["baba"] in ("稍重", "重", "不良") for r in runs])
        good = np.array([r["baba"] == "良" for r in runs])
        if wet.any() and good.any():
            wet_fit = _fit(_wavg(sc[wet], w[wet]) - _wavg(sc[good], w[good]), int(wet.sum()), 0.15, 2.5)

    # --- コース(競馬場)適性 ---
    ven = np.array([bool(ctx["venue"]) and ctx["venue"] in r["venue"] for r in runs])
    venue_fit = _fit(_wavg(sc[ven], w[ven]) - overall, int(ven.sum()), 0.10, 2.0) if ven.any() else 0.0

    # --- 調子（直近2走 − それ以前） ---
    form = 0.0
    if len(runs) >= 3:
        form = float(np.clip((sc[:2].mean() - sc[2:5].mean()) * 0.12, -3.0, 3.0))

    # --- 休養（間隔） ---
    rest = 0.0
    try:
        last = datetime.strptime(runs[0]["date"], "%Y/%m/%d").date()
        days = (ctx["today"] - last).days
        rest = -1.0 if days > 150 else (-0.4 if days > 100 else (-0.4 if days < 14 else 0.0))
    except (ValueError, TypeError):
        days = None

    return {"spd": shrink(spd), "sta": shrink(sta), "kic": shrink(kic), "n": len(runs),
            "front": front, "instab": instab, "dist_fit": dist_fit, "wet_fit": wet_fit,
            "venue_fit": venue_fit, "form": form, "rest": rest, "days": days,
            "last_jockey": runs[0]["jockey"], "last_kinryo": runs[0]["kinryo"]}


JOCKEY_BY_ID = {}


def canon_jockey(x):
    x = normalize_name(x)
    x = re.sub(r"^[☆▲△◇★]+", "", x)
    return re.sub(r"^[A-Za-z]\.", "", x)


def same_jockey(a, b):
    """『岩田望』と『岩田望来』、『ルメール』と『C.ルメール』を同一視"""
    a, b = canon_jockey(a), canon_jockey(b)
    return bool(a and b and (a.startswith(b) or b.startswith(a)))


def skill_of(skills, name, default):
    if name in skills:
        return skills[name]
    hits = [v for k, v in skills.items() if same_jockey(k, name)]
    return hits[0] if len(hits) == 1 else default


PAST_RE = re.compile(r"(\d{4})[./](\d{1,2})[./](\d{1,2})\s*(\S+?)\s+(\d{1,2}|中|取|除|失|降|止)(?=\s)\s*(.*)")


def parse_past_cell(td):
    """馬柱の1走分のセルを文字列から解析（クラス名に依存しない）"""
    text = unicodedata.normalize("NFKC", td.get_text(" ", strip=True))
    m = PAST_RE.search(text)
    if not m:
        return None
    y, mo, d, venue, rank_txt, rest = m.groups()
    if rank_txt in ("取", "除"):
        return None
    c = re.search(r"(芝|ダ|障)\s*(\d{3,4})", rest)
    if not c or c.group(1) == "障":
        return None
    gm = re.search(r"Icon_GradeType([123])(?!\d)", " ".join(x for e in td.find_all(True) for x in e.get("class", [])))
    name = rest[:c.start()].strip() + (f" G{gm.group(1)}" if gm else "")
    after = rest[c.end():]
    nm = re.search(r"(\d+)頭", after)
    seg = re.sub(r"\d+:\d{2}\.\d", "", after[:nm.start()] if nm else after[:30])
    bm = re.search(r"(不良|稍重|不|稍|重|良)", seg)
    jm = re.search(r"(\d+)番\s*(\d+)人\s*(\S+?)\s+(\d{2}(?:\.\d)?)", after)
    pm = re.search(r"(?<![\d.:])(\d{1,2}(?:-\d{1,2})+)(?![\d.:])", after)
    am = re.search(r"\((\d{2}\.\d)\)", after)
    return {
        "date": f"{y}/{int(mo):02d}/{int(d):02d}", "venue": venue, "name": name,
        "n": int(nm.group(1)) if nm else None,
        "rank": int(rank_txt) if rank_txt.isdigit() else None,
        "surface": c.group(1), "dist": int(c.group(2)),
        "baba": BABA_MAP.get(bm.group(1)[:1], "") if bm else "",
        "pos1": int(pm.group(1).split("-")[0]) if pm else None,
        "jockey": normalize_jockey(re.sub(r"^[☆▲△◇★]+", "", jm.group(3))) if jm else "",
        "kinryo": float(jm.group(4)) if jm else None,
        "agari": float(am.group(1)) if am else None,
    }


def parse_past_page(html):
    """shutuba_past.html（馬柱）→ {horse_id: [直近の成績…（新しい順）]}"""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tr in soup.find_all("tr"):
        if tr.find("tr") is not None:
            continue
        a = tr.find("a", href=re.compile(r"/horse/(\d+)"))
        if not a:
            continue
        hid = re.search(r"/horse/(\d+)", a["href"]).group(1)
        cells = [td for td in tr.find_all("td") if any(str(c).startswith("Past") for c in td.get("class", []))]
        runs = [r for r in (parse_past_cell(td) for td in cells) if r]
        if runs and hid not in out:
            out[hid] = runs
    return out



def load_runs(horses, race_id, force=False):
    """各馬の過去成績（新しい順）を集める。優先順位:
       ① netkeibaの馬柱（1回の取得で全頭） → ② Yahoo!スポーツナビの各馬ページ(予備) → ③ netkeibaの各馬ページ
       戻り値: ({馬名: [成績…]}, {馬名: 取得元}, 取得に関する注意のリスト)"""
    runs_by, src_by, problems, past = {}, {}, [], {}
    if race_id:
        try:
            past = parse_past_page(fetch_text(f"{race_base(race_id)}/race/shutuba_past.html?race_id={race_id}", force=force))
            if not past:
                problems.append("netkeibaの馬柱から過去成績を解析できませんでした（未公開、または表構造の変更）")
        except Exception as e:
            problems.append(f"netkeibaの馬柱の取得に失敗（{e}）")
    y_err = d_err = None
    for h in horses:
        runs, src = past.get(h["horse_id"], []), "netkeiba"
        if len(runs) < 3 and y_err is None:                  # 少ない馬はYahooで補完
            try:
                yr = parse_yahoo_horse(fetch_text(f"{YAHOO_BASE}/directory/horse/{h['horse_id']}/",
                                                  force=force, ttl=HORSE_CACHE_TTL))
                if len(yr) > len(runs):
                    runs, src = yr, "Yahoo"
            except Exception as e:
                y_err = str(e)
                problems.append(f"Yahoo!スポーツナビの馬ページの取得に失敗（{e}）")
        if len(runs) < 3 and d_err is None:                  # それでも少なければnetkeibaの個別ページ
            try:
                dr = parse_horse_results(fetch_text(f"{DB_BASE}/horse/{h['horse_id']}",
                                                    force=force, ttl=HORSE_CACHE_TTL))
                if len(dr) > len(runs):
                    runs, src = dr, "netkeiba(個別)"
            except Exception as e:
                d_err = str(e)
                problems.append(f"netkeibaの馬ページの取得に失敗（{e}）")
        if runs:
            runs_by[h["名前"]], src_by[h["名前"]] = runs, src
    return runs_by, src_by, problems


def make_profiles(horses, runs_by, ctx):
    profiles, problems = {}, []
    for h in horses:
        prof = build_profile(runs_by.get(h["名前"], []), ctx)
        if prof is None:
            problems.append(f"{h['名前']}: 過去成績を取得できず（初出走、または取得失敗）")
            continue
        profiles[h["名前"]] = prof
    return profiles, problems





# ----------------------------------------------------------
# 4. 騎手成績（年間リーディング）
# ----------------------------------------------------------
def parse_jockey_leading(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if len(rows) < 5:
            continue
        headers = [re.sub(r"\s+", "", c.get_text()) for c in rows[0].find_all(["th", "td"])]
        c_name, c_win, c_top3 = find_col(headers, "騎手"), find_col(headers, "勝率"), find_col(headers, "複勝率")
        c_runs = find_col(headers, "出走回数") or find_col(headers, "騎乗回数")
        if c_win is None or c_top3 is None:
            continue
        stats = {}
        for tr in rows[1:]:
            tds = tr.find_all("td")
            if len(tds) <= max(c_win, c_top3):
                continue
            a = tr.find("a", href=re.compile(r"/jockey/"))
            name = normalize_jockey((a.get("title") or a.get_text(strip=True)) if a
                                    else (tds[c_name].get_text() if c_name is not None else ""))
            win, top3 = _float(tds[c_win].get_text()), _float(tds[c_top3].get_text())
            if not name or win is None or top3 is None:
                continue
            if win > 1.0:      # %表記なら割合へ
                win, top3 = win / 100, top3 / 100
            if a:
                mid = re.search(r"/jockey/(?:result/recent/|profile/)?(\d{4,6})", a.get("href", ""))
                if mid:
                    JOCKEY_BY_ID[mid.group(1)] = name
            runs = _int(tds[c_runs].get_text()) if (c_runs is not None and c_runs < len(tds)) else None
            stats[name] = (win, top3, runs)
        if stats:
            return stats
    return {}


def load_jockey_skills(year):
    """年間リーディングの勝率・複勝率を順位(パーセンタイル)化 → (技術, 適性)"""
    url = f"{DB_BASE}/?pid=jockey_leading&year={year}"
    stats = parse_jockey_leading(fetch_text(url, ttl=HORSE_CACHE_TTL))
    pool = {k: v for k, v in stats.items() if v[2] is None or v[2] >= MIN_RIDES}
    if len(pool) < 5:
        pool = stats
    if not pool:
        return {}
    wins = np.array([v[0] for v in pool.values()])
    tops = np.array([v[1] for v in pool.values()])

    def pct(arr, x):
        return float((arr < x).mean() + 0.5 * (arr == x).mean())

    out = {}
    for k, v in stats.items():
        tech, apt = 70 + 30 * pct(wins, v[0]), 70 + 30 * pct(tops, v[1])
        kk = min(1.0, v[2] / MIN_RIDES) if v[2] is not None else 1.0   # 騎乗数が少ないほど平均(80)へ
        out[k] = (80 + kk * (tech - 80), 80 + kk * (apt - 80))
    return out




# ----------------------------------------------------------
# 5. netkeiba以外のデータ源
#    Yahoo!スポーツナビ（race_id の先頭2桁を除いた10桁がそのままYahooのレースID。
#    馬のIDはnetkeibaと共通）と、Open-Meteo（天気予報）
# ----------------------------------------------------------
GRADE_PAT = r"(?:G(?:III|II|I|[123])|Jpn(?:III|II|I|[123])|JG(?:III|II|I|[123])|L|OP)"


def yahoo_id(race_id):
    return race_id[2:]


# ---- 5a. Yahoo 単勝・複勝オッズ -------------------------------------------
def parse_yahoo_odds(html):
    """単勝・複勝オッズの表 → ({馬番: {win, place, name, horse_id}}, {asof, interim})
    枠番セルが結合(rowspan)されて列がずれても読めるよう、行の右端から解釈する"""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    range_pat = re.compile(r"^\d+(?:\.\d+)?\s*[-~〜]\s*\d+(?:\.\d+)?$")
    for tr in soup.find_all("tr"):
        if tr.find("tr") is not None:
            continue
        cells = [norm_text(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c != ""]
        if len(cells) < 4:
            continue
        k = next((i for i in range(len(cells) - 1, -1, -1) if range_pat.match(cells[i])), None)
        if k is not None and k >= 3:
            win_txt, name, uma_txt = cells[k - 1], cells[k - 2], cells[k - 3]
            lo, hi = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", cells[k])[:2]]
            place = (lo, hi)
        else:
            continue
        if not re.fullmatch(r"\d{1,2}", uma_txt) or not re.fullmatch(r"\d+(?:\.\d+)?", win_txt):
            continue
        a = tr.find("a", href=re.compile(r"/horse/(\d+)"))
        hid = re.search(r"/horse/(\d+)", a["href"]).group(1) if a else None
        out[int(uma_txt)] = {"win": float(win_txt), "place": place, "name": normalize_name(name), "horse_id": hid}
    text = norm_text(soup.get_text(" ", strip=True))
    m = re.search(r"(\d{4}/\d{1,2}/\d{1,2}\s*\d{1,2}:\d{2})", text)
    meta = {"asof": m.group(1) if m else "", "interim": "中間" in text}
    return out, meta


def fetch_yahoo_odds(race_id, force=False):
    html = fetch_text(f"{YAHOO_BASE}/race/odds/tfw/{yahoo_id(race_id)}", force=force, ttl=120)
    return parse_yahoo_odds(html)


# ---- 5b. Yahoo 出馬表（血統・調教師・馬体重・枠/馬番） --------------------------
BW_TOKEN = re.compile(r"^(?:-|\d{3}(?:kg)?)\s*(?:\(\s*[+\-]?\d*\s*\))?$")


def parse_yahoo_denma(html):
    """→ ({horse_id: {名前, 枠, 馬番, 馬体重, 父, 母, 母父, 調教師, 騎手?}}, race_info)"""
    soup = BeautifulSoup(html, "html.parser")
    horses, order = {}, []
    for tr in soup.find_all("tr"):
        if tr.find("tr") is not None:
            continue
        a = tr.find("a", href=re.compile(r"/keiba/directory/horse/(\d+)"))
        if not a:
            continue
        hid = re.search(r"/horse/(\d+)", a["href"]).group(1)
        if hid in horses:
            continue
        toks = [norm_text(t) for t in tr.stripped_strings]
        toks = [t for t in toks if t]
        name = norm_text(a.get_text(" ", strip=True))
        if name not in toks:
            continue
        i = toks.index(name)
        nums = [t for t in toks[:i] if re.fullmatch(r"\d{1,2}", t)]
        rest = toks[i + 1:]
        bw = None
        if rest and BW_TOKEN.match(rest[0]):
            bw = rest[0]
            rest = rest[1:]
        names = [t for t in rest if not re.fullmatch(r"[\d.\-()+/: ]+", t)]
        rec = {"名前": normalize_name(name), "horse_id": hid,
               "枠": int(nums[0]) if len(nums) >= 1 else None,
               "馬番": int(nums[1]) if len(nums) >= 2 else None,
               "馬体重": bw if bw and re.search(r"\d{3}", bw) else None,
               "父": names[0] if len(names) > 0 else "", "母": names[1] if len(names) > 1 else "",
               "母父": names[2] if len(names) > 2 else "",
               "調教師": re.sub(r"[()（）]|栗東|美浦|地方|海外", "", names[3]).strip() if len(names) > 3 else ""}
        horses[hid] = rec
        order.append(hid)
    # 騎手・性齢・斤量は別表（馬名列なし）のことがあるため、行数が一致する場合のみ順番で対応づける
    jrows = []
    for tr in soup.find_all("tr"):
        if tr.find("tr") is not None or tr.find("a", href=re.compile(r"/keiba/directory/horse/")):
            continue
        ja = tr.find("a", href=re.compile(r"/keiba/directory/jockey/"))
        if ja:
            toks = [norm_text(t) for t in tr.stripped_strings]
            sexage = next((t for t in toks if re.fullmatch(r"[牡牝セ騸]\d{1,2}", t)), "")
            kin = next((float(t) for t in toks if re.fullmatch(r"\d{2}(?:\.\d)?", t) and 45 <= float(t) <= 62), None)
            jrows.append({"騎手": normalize_jockey(ja.get_text(strip=True)), "性齢": sexage, "斤量": kin})
    if order and len(jrows) == len(order):
        for hid, jr in zip(order, jrows):
            horses[hid].update(jr)
    text = norm_text(soup.get_text(" ", strip=True))
    title = norm_text(soup.title.get_text(strip=True)) if soup.title else ""
    info = {"name": None, "date_obj": None, "post_time": None, "dist": None, "surface": None}
    m = re.search(r"\d{4}年(.+?)\s*出馬表", title)
    if m:
        info["name"] = m.group(1).strip()
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        info["date_obj"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", text)
    if m:
        info["post_time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"(芝|ダート|ダ|障害)[^\d]{0,8}?(\d{3,4})\s*m", text)
    if m:
        info["surface"] = {"芝": "芝", "ダート": "ダ", "ダ": "ダ"}.get(m.group(1), "障")
        info["dist"] = int(m.group(2))
    return horses, info


def fetch_yahoo_denma(race_id, force=False):
    return parse_yahoo_denma(fetch_text(f"{YAHOO_BASE}/race/denma/{yahoo_id(race_id)}?detail=1", force=force))


# ---- 5c. Yahoo 各馬の過去成績（netkeibaが取れない時の予備） ---------------------
YRUN_RE = re.compile(
    r"(?P<y>\d{4})/(?P<m>\d{1,2})/(?P<d>\d{1,2})\s+(?P<name>.+?)\s+(?:(?P<grade>" + GRADE_PAT + r")\s+)?"
    r"(?P<venue>[^\s\d]+?)\s*(?P<surf>芝|ダート|ダ|障害)\s*(?P<dist>\d{3,4})\s*m\s*(?P<n>\d{1,2})\s*頭\s+"
    r"(?P<rank>\d{1,2}|中止|競走中止|失格|降着|取消|除外)\s*着?\s+(?P<uma>\d{1,2})\s+"
    r"(?P<jk>[^\d()]+?)\s*\(\s*(?P<kin>\d{2}(?:\.\d)?)\s*\)\s+"
    r"(?:(?P<pop>\d{1,2})\s*人?気?\s*\(\s*(?P<odds>[\d.]+)\s*倍?\s*\)\s+)?"
    r"(?:(?P<bw>\d{3})\s*(?:kg)?\s*(?:\(\s*(?P<bwd>[+\-]?\d*)\s*\))?\s*)?"
    r"(?:(?P<time>\d:\d{2}\.\d)\s*)?(?:\(\s*(?P<diff>[^)]*)\)\s*)?"
    r"(?:(?P<pas>\d{1,2}(?:-\d{1,2})+)\s*)?(?:(?P<agari>\d{2}\.\d))?")


def parse_yahoo_horse(html):
    """各馬ページの『出走レース』→ build_profile用の成績リスト（新しい順）。馬場は載っていない"""
    soup = BeautifulSoup(html, "html.parser")
    text = norm_text(" ".join(soup.stripped_strings))
    runs = []
    for m in YRUN_RE.finditer(text):
        if m.group("rank") in ("取消", "除外") or m.group("surf") == "障害":
            continue
        rk = m.group("rank")
        pas = m.group("pas")
        runs.append({
            "date": f"{int(m.group('y'))}/{int(m.group('m')):02d}/{int(m.group('d')):02d}",
            "venue": m.group("venue"),
            "name": m.group("name").strip() + (f" {m.group('grade')}" if m.group("grade") else ""),
            "n": int(m.group("n")), "rank": int(rk) if rk.isdigit() else None,
            "surface": "芝" if m.group("surf") == "芝" else "ダ", "dist": int(m.group("dist")),
            "baba": "", "pos1": int(pas.split("-")[0]) if pas else None,
            "jockey": normalize_jockey(m.group("jk")), "kinryo": float(m.group("kin")),
            "agari": float(m.group("agari")) if m.group("agari") else None,
        })
    runs.sort(key=lambda r: r["date"], reverse=True)
    return runs


# ---- 5d. Yahoo レース結果 → 当日の馬場傾向（内外・前残り） ----------------------
YRES_ROW = re.compile(
    r"^(?P<rank>\d{1,2}|中止|競走中止|失格|降着|取消|除外)\s*着?\s+(?P<waku>[1-8])\s+(?P<uma>\d{1,2})\s+.*?"
    r"(?:(?P<pas>\d{1,2}(?:-\d{1,2})+)\s*/?\s*(?P<agari>\d{2}\.\d))?")


def parse_yahoo_result(html):
    """結果ページ → {surface, dist, going, n, rows:[{rank, uma, pas1}]}。結果が未確定なら None"""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        if tr.find("tr") is not None:
            continue
        txt = norm_text(" ".join(tr.stripped_strings))
        m = YRES_ROW.match(txt)
        if not m or m.group("rank") in ("取消", "除外"):
            continue
        pm = re.search(r"(?<![\d.:])(\d{1,2}(?:-\d{1,2})+)(?![\d.:])", txt)
        rk = m.group("rank")
        rows.append({"rank": int(rk) if rk.isdigit() else None, "uma": int(m.group("uma")), "waku": int(m.group("waku")),
                     "pos1": int(pm.group(1).split("-")[0]) if pm else None})
    if len(rows) < 6 or not any(r["rank"] == 1 for r in rows):
        return None
    text = norm_text(soup.get_text(" ", strip=True))
    c = re.search(r"(芝|ダート)[^\d]{0,8}?(\d{3,4})\s*m", text)
    g = re.search(r"(不良|稍重|良|重)\s*/\s*(?:晴|曇|雨|小雨|雪|小雪)", text)
    return {"surface": ("芝" if c.group(1) == "芝" else "ダ") if c else None,
            "dist": int(c.group(2)) if c else None, "going": g.group(1) if g else "",
            "n": len(rows), "rows": rows}


def previous_race_ids(race_id, limit=BIAS_MAX_RACES):
    """同じ競馬場の『直近に行われたレース』のID（同日の前のR → 前日の最終Rから遡る）"""
    if is_nar(race_id):                              # 地方はIDが 年+場+月日+R のため、同日の前のRのみ
        ids = [f"{race_id[:10]}{r:02d}" for r in range(int(race_id[10:12]) - 1, 0, -1)]
        return [i[2:] for i in ids[:limit]]
    ym, venue, kai, day, rno = race_id[:4], race_id[4:6], race_id[6:8], int(race_id[8:10]), int(race_id[10:12])
    ids = [f"{ym}{venue}{kai}{day:02d}{r:02d}" for r in range(rno - 1, 0, -1)]
    if day > 1:
        ids += [f"{ym}{venue}{kai}{day - 1:02d}{r:02d}" for r in range(12, 0, -1)]
    return [i[2:] for i in ids[:limit]]            # Yahoo形式(10桁)


def fetch_recent_results(race_id, force=False):
    out = []
    for yid in previous_race_ids(race_id):
        try:
            res = parse_yahoo_result(fetch_text(f"{YAHOO_BASE}/race/result/{yid}", force=force, ttl=RESULT_CACHE_TTL))
        except Exception:
            res = None
        if res:
            res["same_day"] = yid[:8] == race_id[2:10]
            out.append(res)
    return out


def compute_bias(results, surface):
    """3着以内に入った馬の『馬番の位置』『最初のコーナーの位置』から、内外・前残りの傾向を数値化
    戻り値: {waku, front, m, k, desc} ※ waku>0=内有利, front>0=前有利（能力値への加減点の上限つき）"""
    pos_u, pos_f, k = [], [], 0
    for res in results:
        if res["surface"] != surface or res["n"] < 8:
            continue
        k += 1
        w = 1.0 if res.get("same_day") else 0.6
        n = res["n"]
        for r in res["rows"]:
            if r["rank"] and r["rank"] <= 3:
                pos_u.append(((min(r["uma"], n) - 1) / max(1, n - 1), w))
                if r["pos1"]:
                    pos_f.append(((min(r["pos1"], n) - 1) / max(1, n - 1), w))
    if len(pos_u) < BIAS_MIN_SAMPLES:
        return None
    mean = lambda xs: sum(v * w for v, w in xs) / sum(w for _, w in xs)
    inner = 0.5 - mean(pos_u)
    front = (0.5 - mean(pos_f)) if len(pos_f) >= BIAS_MIN_SAMPLES else 0.0
    m = len(pos_u)
    s = m / (m + 12.0)
    clip = lambda x: float(np.clip(x * BIAS_COEF * s, -BIAS_CAP, BIAS_CAP))
    waku_eff, front_eff = clip(inner), clip(front)
    parts = []
    parts.append("内枠有利" if waku_eff >= 0.4 else "外枠有利" if waku_eff <= -0.4 else "枠の有利不利は小さい")
    parts.append("前残り傾向" if front_eff >= 0.4 else "差し・追込が届く傾向" if front_eff <= -0.4 else "脚質の偏りは小さい")
    return {"waku": waku_eff, "front": front_eff, "m": m, "k": k, "desc": "・".join(parts)}


# ---- 5e. 天気予報 → 馬場状態の推定 -------------------------------------------
COURSE_LATLON = {  # 競馬場のおおよその位置（降水量の取得用。市街地レベルの精度で十分）
    "01": (43.07, 141.33), "02": (41.78, 140.77), "03": (37.75, 140.47), "04": (37.96, 139.23),
    "05": (35.66, 139.48), "06": (35.73, 139.96), "07": (35.06, 136.99), "08": (34.91, 135.73),
    "09": (34.78, 135.36), "10": (33.85, 130.88),
    "30": (42.47, 141.87), "35": (39.70, 141.15), "36": (39.14, 141.14), "42": (35.86, 139.65), "43": (35.69, 139.99),
    "44": (35.59, 139.74), "45": (35.52, 139.72), "46": (36.57, 136.65), "47": (35.37, 136.76), "48": (35.05, 136.87),
    "50": (34.77, 135.42), "51": (34.83, 134.70), "54": (33.54, 133.52), "55": (33.28, 130.31),
}
RAIN_WINDOWS = ((0, 24, 1.0), (24, 48, 0.5), (48, 72, 0.2))     # (何時間前から, 何時間前まで, 重み)


def going_from_rain(weighted_mm):
    return "良" if weighted_mm < 3 else "稍重" if weighted_mm < 10 else "重" if weighted_mm < 22 else "不良"


def estimate_going(js, race_dt):
    """Open-Meteoの時間別降水量から、発走時刻までの重み付き雨量→馬場状態を推定"""
    hourly = js["hourly"]
    times = [datetime.fromisoformat(t) for t in hourly["time"]]
    pr = [v or 0.0 for v in hourly["precipitation"]]
    if not times or race_dt > times[-1] + timedelta(hours=1):
        return None
    total, covered = 0.0, 0
    for a, b, w in RAIN_WINDOWS:
        lo, hi = race_dt - timedelta(hours=b), race_dt - timedelta(hours=a)
        mm = sum(p for t, p in zip(times, pr) if lo <= t < hi)
        n = sum(1 for t in times if lo <= t < hi)
        covered += n
        total += mm * w
    if covered < 12:                       # 予報の期間外
        return None
    pp = hourly.get("precipitation_probability") or []
    day_p = [v for t, v in zip(times, pp) if v is not None and race_dt - timedelta(hours=4) <= t <= race_dt + timedelta(hours=1)]
    return {"going": going_from_rain(total), "rain_mm": round(total, 1),
            "pop_max": int(max(day_p)) if day_p else None}


def fetch_weather(race_id, race_dt):
    lat, lon = COURSE_LATLON.get(race_id[4:6], (None, None))
    if lat is None or race_dt is None:
        return None
    url = (f"{METEO_URL}?latitude={lat}&longitude={lon}&hourly=precipitation,precipitation_probability"
           f"&past_days=2&forecast_days=7&timezone={quote('Asia/Tokyo')}")
    return estimate_going(fetch_json(url, ttl=1800), race_dt)


# ---- 5e2. 調教評価・厩舎コメント（netkeiba『調教』ページ。表構造は未検証） --------------
TRAIN_GRADE = {"S": 1.0, "A": 0.7, "B": 0.3, "C": -0.2, "D": -0.6, "E": -0.8}
POS_WORDS = ("好調", "絶好調", "上々", "順調", "良化", "動き軽快", "仕上がり良", "文句なし", "万全", "好気配", "活気", "力強")
NEG_WORDS = ("不安", "物足り", "重め", "余裕残し", "まだ", "太め", "気難し", "カイ食い", "歩様", "いまひとつ", "一息")


def parse_oikiri(html):
    """→ {馬名: 調教スコア(-1〜+1)}。評価(A/B/C…)をレース内で相対化し、コメントの語感で微調整"""
    soup = BeautifulSoup(html, "html.parser")
    raw = {}
    for tr in soup.find_all("tr"):
        if tr.find("tr") is not None:
            continue
        a = tr.find("a", href=re.compile(r"/horse/\d+"))
        if not a:
            continue
        name = normalize_name(a.get("title") or a.get_text(strip=True))
        cells = [norm_text(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        grade = next((c for c in cells if c in TRAIN_GRADE), None)
        comment = max((c for c in cells if len(c) >= 8 and re.search(r"[ぁ-んァ-ン一-龥]", c) and c != name),
                      key=len, default="")
        sentiment = (0.3 if any(w in comment for w in POS_WORDS) else 0.0) - \
                    (0.3 if any(w in comment for w in NEG_WORDS) else 0.0)
        if grade is None and not sentiment:
            continue
        raw[name] = (TRAIN_GRADE.get(grade) if grade else None, sentiment)
    graded = [g for g, _ in raw.values() if g is not None]
    base = float(np.mean(graded)) if graded else 0.0
    return {n: float(np.clip(((g - base) if g is not None else 0.0) + sn, -1.0, 1.0)) for n, (g, sn) in raw.items()}


def fetch_oikiri(race_id, force=False):
    return parse_oikiri(fetch_text(f"{race_base(race_id)}/race/oikiri.html?race_id={race_id}", force=force, ttl=900))


# ---- 5e3. 払戻金（Yahoo結果ページ。表構造は未検証のため、読めた分だけ使う） ------------
PAY_MARKETS = ("単勝", "複勝", "枠連", "馬連", "ワイド", "馬単", "3連複", "3連単")


def pay_key(market, nums):
    nums = tuple(int(x) for x in nums)
    return nums if market in ("馬単", "3連単") else tuple(sorted(nums))


def parse_yahoo_payouts(html):
    """→ {券種: {組み合わせ(タプル): 払戻金(100円あたり)}}。読めなければ空"""
    text = norm_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    start = text.find("払戻")
    if start >= 0:
        text = text[start:]
    pos = []
    for mk in PAY_MARKETS:
        m = re.search(re.escape(mk), text)
        if m:
            pos.append((m.start(), mk))
    pos.sort()
    out = {}
    for i, (st, mk) in enumerate(pos):
        seg = text[st + len(mk): pos[i + 1][0] if i + 1 < len(pos) else len(text)]
        for m in re.finditer(r"(\d{1,2}(?:\s*[-－→>]\s*\d{1,2}){0,2})\s*([\d,]{3,})\s*円", seg):
            nums = re.findall(r"\d+", m.group(1))
            need = {"単勝": 1, "複勝": 1, "枠連": 2, "馬連": 2, "ワイド": 2, "馬単": 2, "3連複": 3, "3連単": 3}[mk]
            if len(nums) != need:
                continue
            out.setdefault(mk, {})[pay_key(mk, nums)] = int(m.group(2).replace(",", ""))
    return out


# ---- 5f. 予備: netkeibaの単勝オッズAPI（非公式・未検証。Yahooが取れない時のみ） -----
def fetch_win_odds(race_id):
    """{馬番: 単勝オッズ}。失敗時は空"""
    api = "api_get_nar_odds" if is_nar(race_id) else "api_get_jra_odds"
    table = None
    for q in ("type=1&action=init", "type=1&action=update", "type=1"):   # 発売前は『予想オッズ』が別の形で返る場合に備えて順に試す
        try:
            js = requests.get(f"{race_base(race_id)}/api/{api}.html?race_id={race_id}&{q}", headers=HEADERS, timeout=15).json()
            t = js["data"]["odds"]["1"]
            if t:
                table = t
                break
        except Exception:
            continue
    if not table:
        return {}
    out = {}
    for k, v in table.items():
        try:
            val = float(v[0] if isinstance(v, (list, tuple)) else v)
            if val > 0:
                out[int(k)] = val
        except (ValueError, TypeError, IndexError):
            continue
    return out





# ----------------------------------------------------------
# 6. データ検証
# ----------------------------------------------------------
def validate(horses, profiles, jockey_skills):
    msgs = []
    n = len(horses)
    drawn = [h for h in horses if h["枠"] is not None and h["馬番"] is not None]
    if not drawn:
        msgs.append("枠順が未確定です（通常はレース週の木曜に発表）。枠の補正なしで計算します。")
    else:
        for h in drawn:
            exp = waku_from_umaban(h["馬番"], n)
            if exp is not None and exp != h["枠"]:
                msgs.append(f"枠番の不一致: {h['名前']} 取得={h['枠']}枠 / 馬番{h['馬番']}・{n}頭の規定={exp}枠")
        nums = sorted(h["馬番"] for h in drawn)
        if len(nums) != len(set(nums)):
            msgs.append("馬番が重複しています")
    names_only = [h["名前"] for h in horses]
    dup_names = sorted({x for x in names_only if names_only.count(x) > 1})
    if dup_names:
        msgs.append("馬名が重複しています: " + "、".join(dup_names))
    seen = {}
    for h in horses:
        j = h["騎手"]
        if not j:
            msgs.append(f"{h['名前']}: 騎手が取得できていません")
        elif j in seen:
            msgs.append(f"騎手『{j}』が複数頭に登録されています（{seen[j]} / {h['名前']}）")
        seen[j] = h["名前"]
    missing = [h["名前"] for h in horses if h["名前"] not in profiles]
    if missing:
        msgs.append(f"過去成績なしのため仮値{PRIOR:.0f}・適性補正なしで計算: " + "、".join(missing))
    if not jockey_skills:
        msgs.append(f"騎手リーディングを取得できなかったため、全騎手を仮値{DEFAULT_JOCKEY}で計算します。")
    else:
        miss_j = sorted({h["騎手"] for h in horses if h["騎手"] and skill_of(jockey_skills, h["騎手"], None) is None})
        if miss_j:
            msgs.append("騎手成績が見つからず仮値で計算: " + "、".join(miss_j))
    return msgs




# ----------------------------------------------------------
# 7. シミュレーション（人気以外の要素を多数反映）
#    基本能力 + 騎手 + 距離/馬場/コース適性 + 調子 + 間隔 + 乗り替わり + 斤量 + 馬体重 + 枠
#    + レース展開（脚質×ペース）+ 馬ごとの不安定さ + （任意で）市場オッズ
# ----------------------------------------------------------
DEFAULT_PROFILE = {"spd": PRIOR, "sta": PRIOR, "kic": PRIOR, "n": 0, "front": 0.5, "instab": 0.5,
                   "dist_fit": 0.0, "wet_fit": 0.0, "venue_fit": 0.0, "form": 0.0, "rest": 0.0,
                   "days": None, "last_jockey": "", "last_kinryo": None}


# 主な種牡馬の傾向（目安。作者の主観ベースの簡易表なので、気になる馬は自由に追記・修正してください）
#   (得意な距離m, ダート適性[-1=芝向き … +1=ダート向き], 渋った馬場への適性[-1 … +1])
SIRE_TRAITS = {
    "ディープインパクト": (2000, -0.9, -0.3), "キングカメハメハ": (1900, 0.2, 0.3), "ロードカナロア": (1400, 0.0, 0.1),
    "ドゥラメンテ": (2100, -0.2, 0.4), "エピファネイア": (2200, -0.4, 0.0), "ハーツクライ": (2200, -0.3, 0.2),
    "モーリス": (1600, -0.4, 0.2), "キズナ": (1900, -0.1, 0.2), "ルーラーシップ": (2200, -0.2, 0.2),
    "ゴールドシップ": (2800, -0.4, 0.8), "オルフェーヴル": (2400, -0.3, 0.7), "キタサンブラック": (2400, -0.5, 0.3),
    "ダイワメジャー": (1600, -0.2, 0.3), "スクリーンヒーロー": (2000, -0.3, 0.4), "ステイゴールド": (2400, -0.3, 0.6),
    "ヘニーヒューズ": (1300, 1.0, 0.1), "シニスターミニスター": (1400, 1.0, 0.3), "ドレフォン": (1400, 0.8, 0.0),
    "サートゥルナーリア": (1900, -0.6, 0.0), "ジャスタウェイ": (1800, -0.2, 0.0), "リオンディーズ": (2000, -0.2, 0.2),
    "ミッキーアイル": (1400, -0.3, 0.0), "マクフィ": (1400, -0.7, 0.0), "ハービンジャー": (2200, -0.9, 0.5),
    "ネオユニヴァース": (2000, -0.3, 0.3), "スワーヴリチャード": (2000, -0.3, 0.2), "サトノダイヤモンド": (2400, -0.8, 0.0),
    "シルバーステート": (1800, -0.4, 0.1), "ブリックスアンドモルタル": (1800, -0.8, 0.0), "コントレイル": (2000, -0.7, 0.0),
    "イクイノックス": (2000, -0.8, 0.0), "ゴールドアリュール": (1700, 1.0, 0.2), "クロフネ": (1600, 0.8, 0.2),
    "パイロ": (1400, 1.0, 0.1), "ヴィクトワールピサ": (2000, 0.0, 0.3), "ベンバトル": (2000, -0.5, 0.3),
}


def sire_trait(name):
    key = re.sub(r"[(（].*?[)）]", "", norm_text(name or "")).strip()
    return SIRE_TRAITS.get(key)


def sire_fit(h, prof, ctx):
    """父・母父の傾向から距離/芝ダ/馬場の向き不向きを加減点に。自身の成績が多い馬ほど弱める"""
    total, wsum = 0.0, 0.0
    for key, w in (("父", 1.0), ("母父", 0.5)):
        t = sire_trait(h.get(key))
        if not t:
            continue
        best, dirt, wet = t
        d = 0.8 - min(abs(ctx["dist"] - best) / 400.0, 1.0) * 1.6
        sf = 0.5 * (dirt if ctx["surface"] == "ダ" else -dirt)
        wt = wet * 0.4 if ctx["condition"] != "良" else 0.0
        total += w * (d + sf + wt)
        wsum += w
    if not wsum:
        return 0.0
    n = prof.get("n", 0)
    reliab = 1.0 if n < 3 else (0.6 if n < 6 else 0.35)
    return float(np.clip(total / wsum * 0.8 * reliab, -1.2, 1.2))


def parse_weight_delta(s):
    m = re.search(r"\(([+\-]?\d+)\)", s or "")
    return int(m.group(1)) if m else None


def style_label(front):
    return "逃げ・先行" if front >= 0.65 else ("差し・追込" if front <= 0.35 else "中団")


def base_abilities(horses, ctx, profiles, jockey_skills):
    """戻り値: (人気を混ぜる前の能力値, 要素別の補正DataFrame)"""
    P = [profiles.get(h["名前"], DEFAULT_PROFILE) for h in horses]
    col = lambda k: np.array([p[k] for p in P], float)
    spd, sta, kic = col("spd"), col("sta"), col("kic")
    dist = ctx["dist"]
    if dist <= 2400:
        horse_ab = spd * 0.45 + kic * 0.35 + sta * 0.20
    else:
        horse_ab = sta * 0.50 + spd * 0.25 + kic * 0.25
    horse_ab = horse_ab.mean() + ABILITY_SPREAD * (horse_ab - horse_ab.mean())

    jk = np.array([skill_of(jockey_skills, h["騎手"], DEFAULT_JOCKEY) for h in horses], float)
    ability = horse_ab * 0.75 + (jk[:, 0] * 0.6 + jk[:, 1] * 0.4) * 0.25

    f = {
        "距離適性": col("dist_fit"),
        "馬場適性": col("wet_fit") + (sta - sta.mean()) * WET_STAMINA[ctx["condition"]],
        "コース適性": col("venue_fit"),
        "調子": col("form"),
        "間隔": col("rest"),
    }
    chg, kin, bw, wk = [], [], [], []
    scale = 1.0 if dist <= 2000 else 0.6
    for h, p in zip(horses, P):
        lj = p["last_jockey"]
        if lj and h["騎手"] and not same_jockey(lj, h["騎手"]):
            old = skill_of(jockey_skills, lj, DEFAULT_JOCKEY)
            new = skill_of(jockey_skills, h["騎手"], DEFAULT_JOCKEY)
            chg.append(float(np.clip((new[0] - old[0]) * 0.03, -1.0, 1.0)) - 0.2)
        else:
            chg.append(0.2 if lj and same_jockey(lj, h["騎手"]) else 0.0)
        lk, k = p.get("last_kinryo"), h.get("斤量")
        kin.append(float(np.clip(-(k - lk) * 0.3, -1.5, 1.5)) if lk and k else 0.0)
        d = parse_weight_delta(h.get("馬体重"))
        bw.append(0.0 if d is None else (-0.6 if abs(d) >= 14 else (-0.3 if abs(d) >= 10 else 0.0)))
        wk.append(WAKU_ADJ.get(h["枠"], 0.0) * scale if h["枠"] else 0.0)
    f["乗り替わり"], f["斤量差"], f["体重増減"], f["枠補正"] = (np.array(x) for x in (chg, kin, bw, wk))
    f["脚質傾向"] = np.zeros(len(horses))
    bias = ctx.get("bias")
    if bias:                                   # 当日の馬場傾向（内外・前残り）を実績から反映
        front_v = col("front")
        f["脚質傾向"] = np.clip(bias["front"] * (front_v - 0.5) * 2, -BIAS_CAP, BIAS_CAP)
        nums = [h["馬番"] for h in horses]
        if all(nums) and len(nums) > 1:
            mx = max(nums)
            pos = np.array([(u - 1) / (mx - 1) for u in nums], float)
            f["枠補正"] = np.clip(bias["waku"] * (0.5 - pos) * 2, -BIAS_CAP, BIAS_CAP)
    # 血統（父・母父の傾向）と調教評価
    f["血統適性"] = np.array([sire_fit(h, p, ctx) for h, p in zip(horses, P)]) * SIRE_WEIGHT
    tr = ctx.get("training") or {}
    f["調教"] = np.array([tr.get(h["名前"], 0.0) for h in horses], float) * TRAINING_WEIGHT
    for v in f.values():
        ability = ability + v

    return ability, pd.DataFrame(f)


def market_vector(horses, odds_by_name):
    """単勝オッズ → 能力値と同じ尺度の『市場の評価』。全頭分そろわなければ None"""
    if not odds_by_name:
        return None
    o = np.array([odds_by_name.get(h["名前"], np.nan) for h in horses], float)
    if not np.isfinite(o).all() or (o <= 0).any():
        return None
    p = (1 / o) / (1 / o).sum()
    return NOISE_SD * ODDS_SCALE * np.log(p)


def finalize_ability(pre, market, blend, scale):
    """人気は『一要素』として任意の強さで混ぜる。scaleは能力差の拡大率"""
    base = (pre - pre.mean()) * scale
    if market is not None and blend > 0:
        return (1 - blend) * base + blend * (market - market.mean())
    return base


def auto_pace(front):
    """先行馬の多さから想定ペースを求める → (+:ハイ〜-:スローの値, 逃げ・先行の頭数)"""
    H = len(front)
    n_front = float((front >= 0.6).sum())
    expect = max(1.0, H / 6)
    return float(np.clip((n_front - expect) / expect, -1, 1)), int(n_front)


def simulate(ability, front, instab, n, seed=None, pace_fixed=None):
    """レース展開（先行馬の多さ→ペース）と、馬ごとのブレ幅を含めて着順を生成。
    pace_fixed を渡すと、そのペース（+:ハイ / 0:ミドル / -:スロー）に固定して計算する"""
    rng = np.random.default_rng(seed)
    H = len(ability)
    sd = NOISE_SD * (0.85 + 0.3 * instab)                       # 成績が不安定な馬ほどブレる
    noise = rng.normal(0, 1, size=(n, H)) * sd
    mean_pace, n_front = auto_pace(front)
    pace_sd = 0.7
    if pace_fixed is not None:
        mean_pace, pace_sd = float(pace_fixed), PACE_FIXED_SD
    pace = rng.normal(mean_pace, pace_sd, size=(n, 1))
    scores = ability + noise - pace * (front - 0.5) * PACE_EFFECT
    order = np.argsort(-scores, axis=1)
    ranks = np.empty_like(order)
    ranks[np.arange(n)[:, None], order] = np.arange(1, H + 1)
    return order, ranks, mean_pace, n_front


def pace_compare(ability, front, instab, n=3000, seed=12345):
    """ハイ／ミドル／スローそれぞれの上位3頭。同じ乱数で比べるので、差はペースの影響だけ"""
    out = []
    for name, v in PACE_MODES.items():
        if v is None:
            continue
        _, ranks, _, _ = simulate(ability, front, instab, n, seed, pace_fixed=v)
        avg, win = ranks.mean(0), (ranks == 1).mean(0)
        top = [int(i) for i in np.argsort(avg)[:3]]
        out.append({"mode": name, "idx": top, "win": [float(win[i]) for i in top]})
    return out


def pace_text(mean_pace):
    return ("ハイペース想定（差し・追込に有利）" if mean_pace > 0.3
            else "スローペース想定（逃げ・先行に有利）" if mean_pace < -0.3 else "平均ペース想定")




# ----------------------------------------------------------
# 7b. 推奨馬券（買い方はシミュレーション結果から自動判断）
# ----------------------------------------------------------
def recommend(ranks, horses, labels, odds_by_name, instab):
    n, H = ranks.shape
    top3 = ranks <= 3
    p_win, p3 = (ranks == 1).mean(0), top3.mean(0)
    order_idx = list(np.argsort(-(0.5 * p_win + 0.5 * p3)))
    axis = order_idx[0]
    conf = float(p_win[axis])
    kind = "堅め" if conf >= 0.25 else ("標準" if conf >= 0.15 else "混戦")
    partners = order_idx[1:1 + (4 if kind == "堅め" else 5)]
    lab = lambda idx: "・".join(labels[i] for i in idx)

    # 穴馬: オッズがあれば単勝期待値、なければ複勝率×不安定さ
    rest = [i for i in order_idx if i != axis and i not in partners]
    hole = None
    have_odds = bool(odds_by_name) and all(h["名前"] in odds_by_name for h in horses)
    cand = [i for i in rest if p3[i] >= 0.05]
    if cand:
        if have_odds:
            hole = max(cand, key=lambda i: p_win[i] * odds_by_name[horses[i]["名前"]])
        else:
            hole = max(cand, key=lambda i: p3[i] * (1 + instab[i]))

    tickets = []
    pa = np.array(partners)
    hit = (top3[:, axis] & top3[:, pa].any(1)).mean()
    tickets.append(("本線", "ワイド 軸流し", f"{labels[axis]} － {lab(partners)}", len(partners), float(hit),
                    ("ワイド", axis, list(partners))))
    hit = (top3[:, axis] & (top3[:, pa].sum(1) == 2)).mean()
    tickets.append(("本線", "3連複 軸1頭流し", f"軸 {labels[axis]} － 相手 {lab(partners)}",
                    len(partners) * (len(partners) - 1) // 2, float(hit), ("3連複流し", axis, list(partners))))
    if kind != "堅め":
        box = [axis] + partners[:4]
        hit = (top3[:, box].sum(1) == 3).mean()
        tickets.append(("押さえ", "3連複 BOX", lab(box), math.comb(len(box), 3), float(hit), ("3連複BOX", list(box))))
    else:
        hit = ((ranks[:, axis] == 1) & (top3[:, pa].sum(1) == 2)).mean()
        tickets.append(("狙い", "3連単 1着固定流し", f"1着 {labels[axis]} → 2,3着 {lab(partners)}",
                        len(partners) * (len(partners) - 1), float(hit), ("3連単流し", axis, list(partners))))
        tickets.append(("押さえ", "単勝", labels[axis], 1, float(p_win[axis]), ("単勝", [axis])))
    if hole is not None:
        pair = [axis, partners[0]]
        hit = (top3[:, hole] & top3[:, pair].any(1)).mean()
        tickets.append(("穴狙い", "ワイド", f"{labels[hole]} － {lab(pair)}", 2, float(hit),
                        ("ワイド", hole, list(pair))))

    value = []
    if have_odds:
        for i in np.argsort(-(p_win * np.array([odds_by_name[h["名前"]] for h in horses]))):
            ev = p_win[i] * odds_by_name[horses[i]["名前"]]
            if ev >= 1.1 and p_win[i] >= 0.03:
                value.append((labels[i], float(ev)))

    # ---- 全券種（JRAの8種）を、同じ軸・相手から算出 ----
    idx_of = {l: i for i, l in enumerate(labels)}
    by = {}
    singles = [axis] + [idx_of[v[0]] for v in value[:1] if idx_of[v[0]] != axis]
    by["単勝"] = [("本線", "単勝", lab(singles), len(singles), float((ranks[:, singles] == 1).any(1).mean()), ("単勝", singles))]
    s2 = [int(i) for i in np.argsort(-p3)[:2]]
    by["複勝"] = [("本線", "複勝", lab(s2), 2, float(top3[:, s2].any(1).mean()), ("複勝", s2))]
    wk = [h["枠"] for h in horses]
    if all(wk):
        wk = np.array(wk)
        a1, a2 = wk[(ranks == 1).argmax(1)], wk[(ranks == 2).argmax(1)]
        code = np.minimum(a1, a2) * 10 + np.maximum(a1, a2)
        u, c = np.unique(code, return_counts=True)
        pick = np.argsort(-c)[:4]
        chosen = set(int(u[i]) for i in pick)
        hit = float(np.isin(code, list(chosen)).mean())
        buy = "、".join(f"{int(u[i]) // 10}-{int(u[i]) % 10}" for i in pick)
        by["枠連"] = [("本線", "枠連", buy, len(pick), hit,
                    ("枠連", [(int(u[i]) // 10, int(u[i]) % 10) for i in pick]))]
    else:
        by["枠連"] = []
    by["馬連"] = [("本線", "馬連 軸流し", f"{labels[axis]} － {lab(partners)}", len(partners),
                  float(((ranks[:, axis] <= 2) & (ranks[:, pa] <= 2).any(1)).mean()),
                  ("馬連", axis, list(partners)))]
    by["ワイド"] = [t for t in tickets if t[1].startswith("ワイド")]
    by["馬単"] = [("本線", "馬単 1着固定流し", f"1着 {labels[axis]} → 2着 {lab(partners)}", len(partners),
                  float(((ranks[:, axis] == 1) & (ranks[:, pa] == 2).any(1)).mean()),
                  ("馬単", axis, list(partners)))]
    by["3連複"] = [t for t in tickets if t[1].startswith("3連複")]
    t3 = [t for t in tickets if t[1].startswith("3連単")]
    if not t3:
        p4 = np.array(partners[:4])
        t3 = [("狙い", "3連単 1着固定流し", f"1着 {labels[axis]} → 2,3着 {lab(partners[:4])}",
               len(p4) * (len(p4) - 1), float(((ranks[:, axis] == 1) & (top3[:, p4].sum(1) == 2)).mean()),
               ("3連単流し", axis, list(partners[:4])))]
    by["3連単"] = t3
    return kind, conf, labels[axis], (labels[hole] if hole is not None else None), tickets, value, by


TICKET_ORDER = ["単勝", "複勝", "枠連", "馬連", "ワイド", "馬単", "3連複", "3連単"]


def select_tickets(mode, auto_tickets, by_type):
    if mode == "おまかせ(自動)":
        return auto_tickets
    if mode == "全種類":
        return [t for k in TICKET_ORDER for t in by_type.get(k, [])]
    return by_type.get(mode, [])




# ----------------------------------------------------------
# 7c. 払戻金の推定
#   単勝オッズ → 各馬が勝つ確率（控除分を除いて合計1に正規化）→ ハーヴィル式で組み合わせごとの確率
#   → 払戻の目安 = 払戻率 ÷ 確率 × 100円。発売中の売れ方で実際の払戻は変わります（目安）。
#   単勝は実際のオッズ、複勝はYahooの複勝オッズ幅（取れた時）を使います。
# ----------------------------------------------------------
def return_rate(race_id, market):
    return (RETURN_RATE_NAR if is_nar(race_id) else RETURN_RATE_JRA)[market]


def win_probs(horses, odds_by_name):
    inv = {h["馬番"]: 1.0 / odds_by_name[h["名前"]] for h in horses}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def _p_order(p, seq):
    """馬番の並び seq が、この順に1着・2着・3着…となる確率（ハーヴィル式）"""
    prob, left = 1.0, 1.0
    for k in seq:
        if left <= 1e-9:
            return 0.0
        prob *= p[k] / left
        left -= p[k]
    return prob


def _top3_prob(p, a):
    pa, tot = p[a], p[a]
    for j in p:
        if j == a:
            continue
        tot += p[j] * pa / max(1 - p[j], 1e-9)
        for k in p:
            if k in (a, j):
                continue
            tot += p[j] * p[k] / max(1 - p[j], 1e-9) * pa / max(1 - p[j] - p[k], 1e-9)
    return tot


def combo_prob(market, key, p, waku_of):
    if market == "単勝":
        return p[key[0]]
    if market == "複勝":
        return min(1.0, _top3_prob(p, key[0]))
    if market == "馬単":
        return _p_order(p, key)
    if market == "馬連":
        return _p_order(p, key) + _p_order(p, key[::-1])
    if market == "3連単":
        return _p_order(p, key)
    if market == "3連複":
        return sum(_p_order(p, s) for s in permutations(key))
    if market == "ワイド":
        a, b = key
        return sum(sum(_p_order(p, s) for s in permutations((a, b, k))) for k in p if k not in (a, b))
    if market == "枠連":
        wa, wb = key
        A = [u for u, w in waku_of.items() if w == wa]
        B = [u for u, w in waku_of.items() if w == wb]
        pairs = combinations(A, 2) if wa == wb else ((i, j) for i in A for j in B)
        return sum(_p_order(p, (i, j)) + _p_order(p, (j, i)) for i, j in pairs)
    return 0.0


def _floor10(x):
    return int(math.floor(x / 10.0) * 10)


def pay_range(market, key, p, waku_of, odds_u, place_by_uma, race_id):
    """100円あたりの払戻の目安 (最小, 最大)。推定できなければ (None, None)"""
    if market == "単勝":
        v = _floor10(odds_u[key[0]] * UNIT)
        return v, v
    if market == "複勝" and place_by_uma.get(key[0]):
        lo, hi = place_by_uma[key[0]]
        return _floor10(lo * UNIT), _floor10(hi * UNIT)
    pr = combo_prob(market, key, p, waku_of)
    if pr <= 1e-9:
        return None, None
    v = max(UNIT, _floor10(return_rate(race_id, market) / pr * UNIT))
    return v, v


def hit_mask(market, key, ranks, uma_idx, wk):
    """シミュレーションの各回で、この買い目が的中したか（bool配列）"""
    r = lambda u: ranks[:, uma_idx[u]]
    if market == "単勝":
        return r(key[0]) == 1
    if market == "複勝":
        return r(key[0]) <= 3
    if market == "馬連":
        return (r(key[0]) <= 2) & (r(key[1]) <= 2)
    if market == "馬単":
        return (r(key[0]) == 1) & (r(key[1]) == 2)
    if market == "ワイド":
        return (r(key[0]) <= 3) & (r(key[1]) <= 3)
    if market == "3連複":
        return (r(key[0]) <= 3) & (r(key[1]) <= 3) & (r(key[2]) <= 3)
    if market == "3連単":
        return (r(key[0]) == 1) & (r(key[1]) == 2) & (r(key[2]) == 3)
    if market == "枠連" and wk is not None:
        w1, w2 = wk[(ranks == 1).argmax(1)], wk[(ranks == 2).argmax(1)]
        a, b = key
        return ((w1 == a) & (w2 == b)) | ((w1 == b) & (w2 == a))
    return None


def payout_table(tickets, horses, ranks, odds_by_name, place_by_uma, race_id):
    """各買い目の払戻の目安 → [{lo, hi, mean, ret} または None]。オッズ・馬番がそろわなければ None
       ret = 期待回収率（シミュレーションの的中率 × 払戻の目安 ÷ 購入額。モデルの確率で買い続けた時の目安）"""
    if not tickets or not odds_by_name or any(h.get("馬番") is None for h in horses):
        return None
    if any(h["名前"] not in odds_by_name for h in horses):
        return None
    p = win_probs(horses, odds_by_name)
    uma_idx = {h["馬番"]: i for i, h in enumerate(horses)}
    waku_of = {h["馬番"]: h["枠"] for h in horses if h.get("枠")}
    wk = np.array([h["枠"] for h in horses]) if len(waku_of) == len(horses) else None
    odds_u = {h["馬番"]: odds_by_name[h["名前"]] for h in horses}
    out = []
    for tk in tickets:
        combos = expand_spec(tk[5], horses)
        if not combos:
            out.append(None)
            continue
        lows, highs, mids, exp = [], [], [], 0.0
        ok = True
        for m, key in combos:
            lo, hi = pay_range(m, key, p, waku_of, odds_u, place_by_uma, race_id)
            mask = hit_mask(m, key, ranks, uma_idx, wk)
            if lo is None or mask is None:
                ok = False
                break
            mid = (lo + hi) / 2.0
            lows.append(lo), highs.append(hi), mids.append(mid)
            exp += float(mask.mean()) * mid
        out.append({"lo": min(lows), "hi": max(highs), "mean": float(np.mean(mids)),
                    "ret": exp / (UNIT * len(combos))} if ok else None)
    return out






# ----------------------------------------------------------
# 8. 成績集計（保存先に依存しない部分）
# ----------------------------------------------------------
_DEF_BLEND = MARKET_BLEND
_DEF_SCALE = ABILITY_SCALE


# ---- 買い目の展開と的中判定 --------------------------------------------------
def expand_spec(spec, horses):
    """買い目 → [(券種, 組み合わせ)]。組み合わせは馬番（枠連は枠番）のタプル。馬番が未定なら空"""
    if any(h.get("馬番") is None for h in horses):
        return []
    um = lambda i: horses[i]["馬番"]
    t = spec[0]
    if t in ("単勝", "複勝"):
        return [(t, (um(i),)) for i in spec[1]]
    if t == "枠連":
        return [("枠連", tuple(sorted(p))) for p in spec[1]]
    if t in ("馬連", "ワイド"):
        return [(t, tuple(sorted((um(spec[1]), um(p))))) for p in spec[2]]
    if t == "馬単":
        return [(t, (um(spec[1]), um(p))) for p in spec[2]]
    if t == "3連複流し":
        return [("3連複", tuple(sorted((um(spec[1]), um(p), um(q))))) for p, q in combinations(spec[2], 2)]
    if t == "3連複BOX":
        return [("3連複", tuple(sorted(um(i) for i in c))) for c in combinations(spec[1], 3)]
    if t == "3連単流し":
        return [("3連単", (um(spec[1]), um(p), um(q))) for p, q in permutations(spec[2], 2)]
    return []


def winning_keys(res):
    o = {int(k): v for k, v in res["order"].items()}
    wk = {int(k): v for k, v in (res.get("waku") or {}).items()}
    r1, r2, r3 = o.get(1), o.get(2), o.get(3)
    w = {m: set() for m in PAY_MARKETS}
    if r1:
        w["単勝"].add((r1,))
    for r in (r1, r2, r3):
        if r:
            w["複勝"].add((r,))
    if r1 and r2:
        w["馬連"].add(tuple(sorted((r1, r2))))
        w["馬単"].add((r1, r2))
        if r1 in wk and r2 in wk:
            w["枠連"].add(tuple(sorted((wk[r1], wk[r2]))))
    if r1 and r2 and r3:
        for a, b in combinations((r1, r2, r3), 2):
            w["ワイド"].add(tuple(sorted((a, b))))
        w["3連複"].add(tuple(sorted((r1, r2, r3))))
        w["3連単"].add((r1, r2, r3))
    return w


def ticket_outcome(tk, res):
    """→ (的中した点数, 購入額[100円/点], 払戻額 or None[払戻が読めなかった])"""
    combos = [(m, tuple(k)) for m, k in tk["combos"]]
    if not combos:
        return None
    win = winning_keys(res)
    hits = [c for c in combos if c[1] in win[c[0]]]
    cost = UNIT * len(combos)
    if not hits:
        return 0, cost, 0
    pay = res.get("pay") or {}
    amt = 0
    for m, k in hits:
        v = (pay.get(m) or {}).get("-".join(map(str, k)))
        if v is None:
            return len(hits), cost, None
        amt += v
    return len(hits), cost, amt


def ser_ticket(tk, horses, units=1):
    role, kind, buy, pts, hit, spec = tk
    return {"role": role, "kind": kind, "buy": buy, "pts": pts, "hit": round(float(hit), 4), "units": units,
            "combos": [[m, list(k)] for m, k in expand_spec(spec, horses)]}


WIN_BINS = (0, .02, .05, .10, .20, .35, 1.01)
TOP3_BINS = (0, .10, .25, .40, .60, .80, 1.01)


def calibration(pairs, bins):
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        sel = [(p, y) for p, y in pairs if lo <= p < hi]
        if sel:
            rows.append((lo, min(hi, 1.0), len(sel), float(np.mean([p for p, _ in sel])),
                         float(np.mean([y for _, y in sel]))))
    return rows


def compute_stats(d):
    recs = [r for r in d["records"] if r.get("result") and r["result"].get("order")]
    out = {"n": len(recs), "pending": len([r for r in d["records"] if not r.get("result")])}
    if not recs:
        return out
    top1_win = top1_3 = win_in_top3 = logloss_n = 0
    ll, win_pairs, top3_pairs = 0.0, [], []
    for r in recs:
        o = {int(k): v for k, v in r["result"]["order"].items()}
        rank_of = {u: k for k, u in o.items()}
        hs = [h for h in r["horses"] if h.get("uma") is not None]
        if not hs:
            continue
        hs.sort(key=lambda h: h["avg"])
        top1_win += rank_of.get(hs[0]["uma"]) == 1
        top1_3 += rank_of.get(hs[0]["uma"], 99) <= 3
        win_in_top3 += any(rank_of.get(h["uma"]) == 1 for h in hs[:3])
        for h in hs:
            rk = rank_of.get(h["uma"])
            win_pairs.append((h["win"] / 100, rk == 1))
            top3_pairs.append((h["top3"] / 100, rk is not None and rk <= 3))
            if rk == 1:
                ll += -math.log(max(h["win"] / 100, 0.005))
                logloss_n += 1
    n = len(recs)
    out.update({"top1_win": top1_win / n, "top1_3": top1_3 / n, "win_in_top3": win_in_top3 / n,
                "logloss": ll / logloss_n if logloss_n else None,
                "cal_win": calibration(win_pairs, WIN_BINS), "cal_top3": calibration(top3_pairs, TOP3_BINS)})
    # 券種別
    markets = {}
    def acc(label, tk, res):
        o = ticket_outcome(tk, res)
        if o is None:
            return
        hits, cost, amt = o
        m = markets.setdefault(label, {"races": 0, "hit": 0, "cost_all": 0, "cost": 0, "ret": 0, "n_ret": 0})
        m["races"] += 1
        m["hit"] += hits > 0
        m["cost_all"] += cost
        if amt is not None:
            m["cost"] += cost
            m["ret"] += amt
            m["n_ret"] += 1
    for r in recs:
        for tk in r.get("all_tickets", []):
            acc(tk["kind"].split(" ")[0], tk, r["result"])
        auto = r.get("auto_tickets", [])
        if auto:
            agg = {"combos": [c for tk in auto for c in tk["combos"]]}
            acc("おまかせ(全体)", agg, r["result"])
    out["markets"] = markets
    # 購入おすすめ度・荒れ方の区分ごとの成績（区分に意味があるかの検証用）
    def grp(key):
        g = {}
        for r in recs:
            k = r.get(key)
            hs = [h for h in r["horses"] if h.get("uma") is not None]
            if k is None or not hs:
                continue
            o = {int(a): b for a, b in r["result"]["order"].items()}
            rank_of = {u: a for a, u in o.items()}
            top = min(hs, key=lambda h: h["avg"])["uma"]
            e = g.setdefault(k, {"n": 0, "win": 0, "top3": 0})
            e["n"] += 1
            e["win"] += rank_of.get(top) == 1
            e["top3"] += rank_of.get(top, 99) <= 3
        return g
    out["by_stars"], out["by_upset"], out["by_trust"] = grp("stars"), grp("upset"), grp("trust")
    # 自分の印とAIの比較（自分の印を入れて予想した分だけ）
    mine = {"n": 0, "my_win": 0, "ai_win": 0, "my_3": 0, "ai_3": 0, "my_any": 0, "ai_any": 0}
    for r in recs:
        pk, ai = r.get("mine") or [], r.get("ai_top") or []
        if not pk or not ai:
            continue
        o = {int(a): b for a, b in r["result"]["order"].items()}
        rank_of = {u: a for a, u in o.items()}
        mine["n"] += 1
        mine["my_win"] += rank_of.get(pk[0]) == 1
        mine["ai_win"] += rank_of.get(ai[0]) == 1
        mine["my_3"] += rank_of.get(pk[0], 99) <= 3
        mine["ai_3"] += rank_of.get(ai[0], 99) <= 3
        mine["my_any"] += any(rank_of.get(u) == 1 for u in pk)
        mine["ai_any"] += any(rank_of.get(u) == 1 for u in ai[:len(pk)])
    out["mine"] = mine
    return out


def tune_params(records, cur_blend, cur_scale, n_sims=2000):
    """確定済みの予想から、人気の混ぜ具合と能力差の拡大率を調整。戻り値: (変更したか, メッセージ, params or None)"""
    recs = []
    for r in records:
        res = r.get("result")
        if not res or not res.get("order") or not r["horses"] or any(h.get("market") is None for h in r["horses"]):
            continue
        uma = [h["uma"] for h in r["horses"]]
        w = res["order"].get("1")
        if w in uma:
            recs.append((r, uma.index(w)))
    recs = recs[-200:]
    if len(recs) < TUNE_MIN_RACES:
        return False, f"自動調整には、オッズ付きで結果が確定した予想が{TUNE_MIN_RACES}レース以上必要です（現在 {len(recs)}）。", None
    data = [(np.array([h["pre"] for h in r["horses"]]), np.array([h["market"] for h in r["horses"]]),
             np.array([h["front"] for h in r["horses"]]), np.array([h["instab"] for h in r["horses"]]), wi)
            for r, wi in recs]

    def loss(blend, scale):
        tot = 0.0
        for pre, mk, fr, ins, wi in data:
            _, ranks, _, _ = simulate(finalize_ability(pre, mk, blend, scale), fr, ins, n_sims, 1)
            tot += -math.log(max(float((ranks[:, wi] == 1).mean()), 0.005))
        return tot / len(data)

    base = loss(cur_blend, cur_scale)
    best = (base, cur_blend, cur_scale)
    for b in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
        for sc in (0.7, 0.85, 1.0, 1.15, 1.3):
            v = loss(b, sc)
            if v < best[0] - 1e-9:
                best = (v, b, sc)
    if base - best[0] < 0.003:
        return False, f"現在の設定が最良でした（{len(data)}レースで評価。対数損失 {base:.3f}）。変更しません。", None
    nb, ns_ = round((best[1] + cur_blend) / 2, 2), round((best[2] + cur_scale) / 2, 2)
    after = loss(nb, ns_)
    params = {"blend": nb, "scale": ns_, "n_races": len(data), "ll_before": round(base, 3), "ll_after": round(after, 3),
              "tuned_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M")}
    return True, f"調整しました: 人気の混ぜ具合 {nb}、能力差の拡大率 {ns_}", params




# ----------------------------------------------------------
# 7d. 追加の分析（資金推移・荒れ方・市場との乖離・馬場のもしも・データの信頼度）
# ----------------------------------------------------------
BANK_RACES = 100                  # 資金推移で『同じ買い方を何レース続けるか』
BANK_TRIALS = 3000                # 資金推移の試行回数


def ticket_net_samples(tickets, units, horses, ranks, odds_by_name, place_by_uma, race_id):
    """選んだ買い目をすべて買った時の、シミュレーション1回ごとの損益（円）。払戻を推定できなければ None
       → (損益の配列, 1レースの購入額)"""
    if not tickets or not odds_by_name or any(h.get("馬番") is None for h in horses):
        return None
    if any(h["名前"] not in odds_by_name for h in horses):
        return None
    p = win_probs(horses, odds_by_name)
    uma_idx = {h["馬番"]: i for i, h in enumerate(horses)}
    waku_of = {h["馬番"]: h["枠"] for h in horses if h.get("枠")}
    wk = np.array([h["枠"] for h in horses]) if len(waku_of) == len(horses) else None
    odds_u = {h["馬番"]: odds_by_name[h["名前"]] for h in horses}
    gain = np.zeros(ranks.shape[0])
    cost = 0.0
    for tk, u in zip(tickets, units):
        if not u:
            continue
        combos = expand_spec(tk[5], horses)
        if not combos:
            return None
        for m, key in combos:
            lo, hi = pay_range(m, key, p, waku_of, odds_u, place_by_uma, race_id)
            mask = hit_mask(m, key, ranks, uma_idx, wk)
            if lo is None or mask is None:
                return None
            gain += mask * ((lo + hi) / 2.0) * u
            cost += UNIT * u
    if cost <= 0:
        return None
    return gain - cost, cost


def bankroll_fan(net, cost, n_races=BANK_RACES, trials=BANK_TRIALS, seed=20260101):
    """同じ買い方でn_races回買い続けた時の累計損益の幅（10%・中央値・90%）。シミュレーションの損益を無作為に引いて作る"""
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(net), size=(trials, n_races))
    path = np.concatenate([np.zeros((trials, 1)), np.cumsum(net[idx], axis=1)], axis=1)
    q = lambda a: [float(v) for v in np.percentile(path, a, axis=0)]
    final = path[:, -1]
    return {"n": n_races, "cost": float(cost), "p10": q(10), "p50": q(50), "p90": q(90),
            "roi": float(1 + net.mean() / cost), "p_profit": float((final > 0).mean()),
            "final10": float(np.percentile(final, 10)), "final50": float(np.percentile(final, 50)),
            "final90": float(np.percentile(final, 90)), "dd10": float(np.percentile(path.min(axis=1), 10)),
            "hit_any": float((net > -cost + 1e-9).mean())}


def upset_stats(ranks, odds_by_name, horses):
    """荒れ方の分布: 本命が崩れる確率、人気薄が勝つ確率など"""
    avg = ranks.mean(axis=0)
    order = np.argsort(avg)
    hon, top3 = int(order[0]), order[:3]
    win_idx = np.argmin(ranks, axis=1)
    if odds_by_name and all(h["名前"] in odds_by_name for h in horses):
        o = np.array([odds_by_name[h["名前"]] for h in horses], float)
        pop = o.argsort().argsort() + 1
        basis = "人気"
    else:
        pop = avg.argsort().argsort() + 1
        basis = "予想順位"
    wp = pop[win_idx]
    p_hon_out = float((ranks[:, hon] > 3).mean())
    p_long = float((wp >= 6).mean())
    idx = 0.5 * p_hon_out + 0.5 * p_long
    label = "堅め" if idx < 0.30 else ("標準" if idx < 0.42 else "荒れ模様")
    return {"hon": hon, "p_hon_out": p_hon_out, "p_long": p_long, "p_big": float((wp >= 9).mean()),
            "p_all3": float((ranks[:, top3] <= 3).all(axis=1).mean()),
            "p_win_in_top3": float(np.isin(win_idx, top3).mean()), "basis": basis, "index": float(idx), "label": label}


def market_gap(horses, win_pct, odds_by_name):
    """モデルの勝率と、オッズから逆算した勝率（控除を除いて正規化）の比較"""
    inv = np.array([1.0 / odds_by_name[h["名前"]] for h in horses])
    mk = inv / inv.sum()
    out = []
    for i, h in enumerate(horses):
        m = float(win_pct[i]) / 100.0
        out.append({"i": i, "model": m, "market": float(mk[i]), "ratio": m / max(float(mk[i]), 1e-6)})
    return out


def data_trust(n, n_ok, have_odds, nar, manual, bias, training_n):
    """データの信頼度（高/中/低）と理由。予想の当たりやすさではなく『材料がどれだけ揃ったか』"""
    pts, why = 0, []
    r = n_ok / max(n, 1)
    pts += int(round(4 * min(r, 1.0)))
    if r < 0.8:
        why.append(f"過去成績が取れた馬が{n_ok}/{n}頭")
    if have_odds:
        pts += 2
    else:
        why.append("オッズ未取得")
    if bias:
        pts += 1
    else:
        why.append("当日の馬場傾向が不足")
    if training_n >= 0.5 * n:
        pts += 1
    else:
        why.append("調教評価なし")
    if not nar:
        pts += 1
    else:
        why.append("地方競馬（騎手・血統・調教が不足しやすい）")
    if manual:
        pts = min(pts, 3)
        why = ["手入力・海外（過去成績なし。オッズ中心）"]
    label = "高" if pts >= 7 else ("中" if pts >= 4 else "低")
    return label, why or ["主要なデータは揃っています"]


def cond_compare(horses, runs_by, ctx, skills, market, blend, n=3000, seed=12345):
    """馬場状態を変えた時の上位3頭（その馬場だった場合のもしも）"""
    out = []
    for c in ("良", "稍重", "重", "不良"):
        ctx2 = dict(ctx, condition=c)
        prof, _ = make_profiles(horses, runs_by, ctx2)
        pre, _ = base_abilities(horses, ctx2, prof, skills)
        ab = finalize_ability(pre, market, blend, ABILITY_SCALE)
        P = [prof.get(h["名前"], DEFAULT_PROFILE) for h in horses]
        front = np.array([h["front_hint"] if h.get("front_hint") is not None else p["front"] for h, p in zip(horses, P)])
        instab = np.array([p["instab"] for p in P])
        _, ranks, _, _ = simulate(ab, front, instab, n, seed)
        avg, win = ranks.mean(0), (ranks == 1).mean(0)
        top = [int(i) for i in np.argsort(avg)[:3]]
        out.append({"cond": c, "idx": top, "win": [float(win[i]) for i in top]})
    return out


def parse_umas(text, horses):
    """『5,7 3』のような馬番の入力 → 出走馬にある馬番のリスト（重複除去・先頭から最大3つ）"""
    have = {h["馬番"] for h in horses if h.get("馬番") is not None}
    out = []
    for t in re.split(r"[,\s、]+", unicodedata.normalize("NFKC", text or "").strip()):
        if t.isdigit() and int(t) in have and int(t) not in out:
            out.append(int(t))
    return out[:3]


# ----------------------------------------------------------
# 9b. 手入力レース（海外レースなど、自動取得できないレース用）
#     1行1頭『馬番,馬名,騎手,単勝オッズ,脚質(任意: 逃/先/差/追)』。過去成績が無いので、能力は単勝オッズ中心に推定します。
# ----------------------------------------------------------
STYLE_FRONT = {"逃": 0.9, "逃げ": 0.9, "先": 0.7, "先行": 0.7, "中": 0.5, "中団": 0.5, "差": 0.3, "差し": 0.3,
               "追": 0.1, "追込": 0.1, "追い込み": 0.1}


class ManualInputError(ValueError):
    pass


def parse_manual(text):
    horses, nums, names = [], set(), set()
    for ln, raw in enumerate((text or "").splitlines(), 1):
        line = unicodedata.normalize("NFKC", raw).strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[,\t、]", line) if re.search(r"[,\t、]", line) else line.split()
        toks = [t.strip() for t in parts if t.strip()]
        hint = STYLE_FRONT[toks.pop()] if toks and toks[-1] in STYLE_FRONT else None
        bad = ManualInputError(f"{ln}行目を読めません。『馬番,馬名,騎手,単勝オッズ,脚質(任意)』の順に入力してください")
        if len(toks) < 3 or not re.fullmatch(r"\d{1,2}", toks[0]):
            raise bad
        try:
            odds = float(toks[-1])
        except ValueError:
            raise bad
        if odds < 1.0:
            raise ManualInputError(f"{ln}行目: 単勝オッズは1.0以上で入力してください")
        mid = toks[1:-1]
        uma, name = int(toks[0]), norm_text(mid[0])
        if uma in nums or name in names:
            raise ManualInputError(f"{ln}行目: 馬番または馬名が重複しています")
        nums.add(uma), names.add(name)
        horses.append({"名前": name, "horse_id": f"manual{uma}", "枠": None, "馬番": uma,
                       "騎手": normalize_jockey(mid[1]) if len(mid) > 1 else "", "jockey_id": "", "性齢": "",
                       "斤量": None, "馬体重": None, "odds": odds, "front_hint": hint})
    if len(horses) < 4:
        raise ManualInputError("出走馬を4頭以上入力してください")
    return horses


def manual_race(m):
    horses = parse_manual(m["text"])
    now = datetime.now(JST)
    ovs = bool(m.get("ovs"))
    info = {"name": m.get("name") or "手入力レース", "date": m.get("date_text") or f"{now.year}年{now.month}月{now.day}日（入力日）",
            "venue_r": m.get("venue") or "海外", "distance": int(m.get("distance") or 2000),
            "surface": m.get("surface") or "芝", "condition": m.get("condition") or "良", "year": now.year,
            "date_obj": m.get("date_obj"), "post_time": m.get("post_time"), "duplicates": 0, "url": m.get("url", ""),
            "ovs": ovs, "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S")}
    note = ("海外レース: JRAの海外競馬ページの出走馬と参考オッズから計算しています。騎手・過去成績・馬場傾向は使わず、馬場は『良』と仮定しています"
            if ovs else "手入力レースです。過去成績・騎手成績・馬場傾向は使わず、単勝オッズと入力した脚質を中心に計算しています")
    return info, horses, [], [note], ("JRA海外競馬" if ovs else "手入力")




# ----------------------------------------------------------
# 9c. 海外レースの自動取得（JRAの海外競馬ページ。馬券が発売される海外レースの出走馬と参考オッズ）
# ----------------------------------------------------------
JRA_OVS = "https://www.jra.go.jp/keiba/overseas"
INCLUDE_OVERSEAS = True           # 海外レースを一覧に含める（取得できない時は一覧に出ないだけ）
OVS_MAX_PAGES = 12                # 一覧づくりで確認する海外レースの最大数


class OverseasError(ManualInputError):
    pass


def ovs_key(ref):
    """JRA海外競馬ページのURL・『ovs:キー』・キー（例 2026arc）→ キー。判別できなければ None"""
    ref = (ref or "").strip()
    m = re.search(r"/keiba/overseas/race/([0-9A-Za-z_\-]+)/", ref)
    if m:
        return m.group(1)
    ref = ref.replace("ovs:", "")
    return ref if re.fullmatch(r"\d{4}[0-9A-Za-z_\-]+", ref) else None


def _clean_name(t):
    t = unicodedata.normalize("NFKC", t or "")
    t = re.sub(r"[（(][A-Za-z]{2,4}[）)]", "", t)             # 生産国（FR）などを除く
    t = re.sub(r"\s+", " ", t).strip()
    m = re.match(r"[^A-Za-z]+", t)                            # 日本語名があればその部分だけ（英字表記は捨てる）
    return (m.group(0).strip() if m and m.group(0).strip() else t) or ""


def parse_ovs_entries(html):
    """出走馬の表 → [(馬番, 馬名, 単勝オッズ or None)]。『馬番／馬名／オッズ』が並ぶ行を探す"""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for tr in soup.find_all("tr"):
        cells = [unicodedata.normalize("NFKC", c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cells) < 3 or not re.fullmatch(r"\d{1,2}", cells[0]):
            continue
        uma = int(cells[0])
        name = _clean_name(cells[1])
        if not name or uma in seen:
            continue
        odds = None
        for c in reversed(cells[2:]):
            if re.fullmatch(r"\d+(?:\.\d+)?", c) and float(c) >= 1.0:
                odds = float(c)
                break
        seen.add(uma)
        out.append((uma, name, odds))
    return out


def parse_ovs_info(html, key):
    """ページの本文から、レース名・日付・発走時刻（日本時間）・競馬場・距離・馬場を読む。読めた項目だけ返す"""
    soup = BeautifulSoup(html, "html.parser")
    text = unicodedata.normalize("NFKC", soup.get_text(" ", strip=True))
    info = {}
    h = soup.find("h1")
    title = (h.get_text(" ", strip=True) if h else "") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    title = re.split(r"[｜|]", unicodedata.normalize("NFKC", title))[0]
    title = re.sub(r"^\d{4}年\s*", "", title)
    title = re.sub(r"\s*(出馬表|出走馬|オッズ|基本情報|レース情報|海外競馬).*$", "", title).strip()
    if title:
        info["name"] = title
    pos = text.find("発走")
    near = text[max(0, pos - 80):pos + 40] if pos >= 0 else ""          # 『発走』の近くにある日付・時刻を優先（発売開始の時刻と区別するため）
    m = re.search(r"(\d{1,2})月(\d{1,2})日", near) or re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        try:
            info["date"] = date(int(key[:4]), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    ts = re.findall(r"(\d{1,2})時(\d{2})分", near) or re.findall(r"(\d{1,2}):(\d{2})", near) or re.findall(r"(\d{1,2})時(\d{2})分", text)
    ts = [t for t in ts if int(t[0]) < 24]
    if ts:
        info["time"] = f"{int(ts[-1][0]):02d}:{ts[-1][1]}" if pos >= 0 and near else f"{int(ts[0][0]):02d}:{ts[0][1]}"
    m = re.search(r"([^\s　（()、。]{2,12}競馬場)", text)
    if m:
        info["venue"] = m.group(1)
    m = re.search(r"(芝|ダート)[^\d]{0,8}([\d,]{3,5})\s*(?:メートル|m)", text)
    if m:
        info["surface"], info["distance"] = ("芝" if m.group(1) == "芝" else "ダ"), int(m.group(2).replace(",", ""))
    else:
        m = re.search(r"([\d,]{3,5})\s*(?:メートル|m)", text)
        if m:
            info["distance"] = int(m.group(1).replace(",", ""))
            info["surface"] = "ダ" if re.search(r"ダート", text) else "芝"
    return info


def overseas_manual(ref, force=False):
    """海外レースのページを取得して、手入力レースと同じ形（manual）にする"""
    key = ovs_key(ref)
    if not key:
        raise OverseasError("JRAの海外競馬ページのURLを入れてください（例: https://www.jra.go.jp/keiba/overseas/race/2026arc/morning.html）")
    base = f"{JRA_OVS}/race/{key}"
    try:
        html = fetch_text(f"{base}/morning.html", force=force)
    except Exception:
        raise OverseasError("海外レースのページを取得できませんでした。URLを確認するか、下の『手入力』で出走馬とオッズを入れてください。")
    ent = parse_ovs_entries(html)
    if len(ent) < 4:
        raise OverseasError("出走馬の表を読み取れませんでした（まだ公開されていない可能性があります）。公開後にもう一度試すか、『手入力』をお使いください。")
    if any(o is None for _, _, o in ent):
        raise OverseasError("オッズがまだ公表されていない馬がいます。公表後にもう一度試すか、『手入力』をお使いください。")
    info = parse_ovs_info(html, key)
    if not all(k in info for k in ("date", "venue", "distance")):          # 足りない項目は基本情報のページで補う
        try:
            for k, v in parse_ovs_info(fetch_text(f"{base}/basic.html", force=force), key).items():
                info.setdefault(k, v)
        except Exception:
            pass
    names, lines = set(), []
    for uma, name, odds in sorted(ent):
        if name in names:
            name = f"{name}{uma}"
        names.add(name)
        lines.append(f"{uma},{name.replace(',', ' ')},,{odds}")
    d, tm = info.get("date"), info.get("time")
    wd = f"（{WEEKDAY[d.weekday()]}）" if d else ""
    date_text = (f"{d.year}年{d.month}月{d.day}日{wd}" + (f" {tm}発走（日本時間）" if tm else "")) if d else ""
    return {"name": info.get("name") or "海外レース", "venue": info.get("venue", "海外"),
            "surface": info.get("surface", "芝"), "distance": info.get("distance", 2000), "condition": "良",
            "text": "\n".join(lines), "date_text": date_text, "date_obj": d, "post_time": tm, "ovs": True, "url": f"{base}/morning.html"}


def discover_overseas(force=False):
    """JRAの海外競馬の一覧ページから、これから（または直近に）行われる海外レースを探す。取得できなければ空"""
    try:
        idx = fetch_text(f"{JRA_OVS}/", force=force)
    except Exception:
        return []
    keys = []
    for m in re.finditer(r"/keiba/overseas/race/([0-9A-Za-z_\-]+)/", idx):
        if m.group(1) not in keys:
            keys.append(m.group(1))
    today, out = today_jst(), []
    for key in keys[:OVS_MAX_PAGES]:
        try:
            info = parse_ovs_info(fetch_text(f"{JRA_OVS}/race/{key}/basic.html", force=force), key)
        except Exception:
            continue
        d = info.get("date")
        if not d or d < today - timedelta(days=1):
            continue
        out.append({"race_id": "ovs:" + key, "date": d, "grade": "", "time": info.get("time", ""), "rno": 0,
                    "venue": info.get("venue", "海外").replace("競馬場", ""), "name": info.get("name", key),
                    "course": f"{info.get('surface', '')}{info.get('distance', '')}m" if info.get("distance") else "", "region": "海外"})
    return out


# ----------------------------------------------------------
# 9. 出馬表の取得（netkeiba優先・Yahooで代替/照合）とデータ統合
# ----------------------------------------------------------
def load_race_any(race_id, force=False):
    """netkeibaの出馬表を優先。取れなければYahoo!スポーツナビの出馬表で代替"""
    notes = []
    try:
        info, horses, cancelled = load_race(race_id, force=force)
        return info, horses, cancelled, notes, "netkeiba"
    except Exception:
        notes.append("netkeibaの出馬表を取得できなかったため、Yahoo!スポーツナビの出馬表で代替しました")
    yh, yinfo = fetch_yahoo_denma(race_id, force=force)
    if not yh:
        raise RuntimeError("出馬表を取得できませんでした")
    horses = [{"名前": r["名前"], "horse_id": r["horse_id"], "枠": r["枠"], "馬番": r["馬番"],
               "騎手": r.get("騎手", ""), "jockey_id": "", "性齢": r.get("性齢", ""),
               "斤量": r.get("斤量"), "馬体重": r.get("馬体重")} for r in yh.values()]
    d = yinfo["date_obj"]
    info = {"name": yinfo["name"] or "", "date": f"{d.year}年{d.month}月{d.day}日" if d else None,
            "venue_r": f"{ALL_VENUE.get(race_id[4:6], '')}{int(race_id[10:12])}R", "distance": yinfo["dist"],
            "surface": yinfo["surface"], "condition": None, "year": d.year if d else None,
            "date_obj": d, "post_time": yinfo["post_time"], "duplicates": 0,
            "url": f"{YAHOO_BASE}/race/denma/{yahoo_id(race_id)}",
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    return info, horses, [], notes, "Yahoo"


def merge_yahoo(horses, yden):
    """Yahooの出馬表と照合し、足りない項目を補う。戻り値: 食い違いのメッセージ"""
    n, mism = len(horses), []
    for h in horses:
        y = yden.get(h["horse_id"])
        if not y:
            continue
        for key in ("枠", "馬番"):
            a, b = h.get(key), y.get(key)
            if a is None and b is not None:
                h[key] = b
            elif a is not None and b is not None and a != b:
                mism.append(f"{h['名前']}の{key}（netkeiba={a} / Yahoo={b}）")
                if key == "枠" and h.get("馬番") and waku_from_umaban(h["馬番"], n) == b:
                    h["枠"] = b                                  # JRAの枠番割付ルールに合う方を採用
        if not h.get("馬体重") and y.get("馬体重"):
            h["馬体重"] = y["馬体重"]
        if not h.get("騎手") and y.get("騎手"):
            h["騎手"] = y["騎手"]
        h["父"], h["母父"], h["調教師"] = y.get("父", ""), y.get("母父", ""), y.get("調教師", "")
    ids = {h["horse_id"] for h in horses}
    only_y = [y["名前"] for hid, y in yden.items() if hid not in ids]
    if only_y and len(yden) >= 0.5 * len(horses):
        mism.append("Yahooにのみ登録の馬: " + "、".join(only_y))
    return mism


def task_netkeiba(horses, race_id, year, force):
    runs_by, src_by, problems = load_runs(horses, race_id, force=force)
    try:
        skills = load_jockey_skills(year)
    except Exception as e:
        skills = {}
        problems.append(f"騎手リーディングの取得に失敗（{e}）")
    try:
        training = fetch_oikiri(race_id, force=force)
    except Exception:
        training = {}
    return {"runs": runs_by, "src": src_by, "skills": skills, "problems": problems, "training": training}


def task_yahoo(race_id, surface, force):
    out = {"denma": {}, "odds": {}, "odds_meta": {}, "bias": None, "bias_races": 0, "problems": []}
    try:
        out["denma"], _ = fetch_yahoo_denma(race_id, force=force)
    except Exception as e:
        out["problems"].append(f"Yahoo!スポーツナビの出馬表を取得できませんでした（{e}）")
    try:
        out["odds"], out["odds_meta"] = fetch_yahoo_odds(race_id, force=force)
    except Exception as e:
        out["problems"].append(f"Yahoo!スポーツナビのオッズを取得できませんでした（{e}）")
    try:
        results = fetch_recent_results(race_id, force=force)
        out["bias_races"] = sum(1 for r in results if r["surface"] == surface)
        out["bias"] = compute_bias(results, surface)
    except Exception as e:
        out["problems"].append(f"当日の馬場傾向を取得できませんでした（{e}）")
    return out


def task_weather(race_id, race_dt):
    try:
        return fetch_weather(race_id, race_dt)
    except Exception:
        return None


def parse_odds_text(text, horses):
    """人気・オッズの貼り付け → ({馬名: 単勝オッズ}, エラー文 or None)。
    1行に『馬番 オッズ』（区切りは空白・カンマ・タブ）。『馬番 馬名 オッズ』『人気 馬番 馬名 オッズ』でも、最後の数値をオッズ、馬名に合う数字を馬番とみなす"""
    by_uma = {h.get("馬番"): h for h in horses if h.get("馬番") is not None}
    by_nm = {h["名前"]: h for h in horses}
    out = {}
    for ln in (text or "").splitlines():
        ln = norm_text(ln).strip()
        if not ln:
            continue
        toks = [t for t in re.split(r"[\s,、\t]+", ln) if t]
        odds_t = [t for t in toks if re.fullmatch(r"\d+(?:\.\d+)?", t) and "." in t]
        if not odds_t and len(toks) == 2 and re.fullmatch(r"\d+", toks[1]):
            odds_t = [toks[1]]
        h = next((by_nm[normalize_name(t)] for t in toks if normalize_name(t) in by_nm), None)
        if h is None:
            ints = [t for t in toks if re.fullmatch(r"\d{1,2}", t)]
            if ints and ints[0] != (odds_t[0] if odds_t else None):
                h = by_uma.get(int(ints[0]))
        if h is None or not odds_t:
            return {}, f"『{ln}』を読めません。1行に『馬番 オッズ』の形で入力してください（例: 5 12.3）"
        v = float(odds_t[-1])
        if v < 1.0:
            return {}, f"『{ln}』: オッズは1.0以上で入力してください"
        out[h["名前"]] = v
    miss = [h["名前"] for h in horses if h["名前"] not in out]
    if miss:
        return {}, f"オッズの入力が足りません（未入力: {'、'.join(miss[:5])}{'…' if len(miss) > 5 else ''}）。全頭ぶん入力してください"
    return out, None


def collect_odds(horses, race_id, yodds):
    """Yahooのオッズ（馬名で照合→馬番で補完）→ 無ければnetkeibaのAPI。戻り値: ({馬名: 単勝}, 取得元)"""
    odds, src = {}, ""
    if yodds:
        by_name = {v["name"]: v["win"] for v in yodds.values()}
        for h in horses:
            if h["名前"] in by_name:
                odds[h["名前"]] = by_name[h["名前"]]
            elif h.get("馬番") in yodds and not yodds[h["馬番"]]["name"]:
                odds[h["名前"]] = yodds[h["馬番"]]["win"]
        src = "Yahoo!スポーツナビ"
    if len(odds) < len(horses):
        nk = fetch_win_odds(race_id)
        for h in horses:
            if h["名前"] not in odds and h.get("馬番") in nk:
                odds[h["名前"]] = nk[h["馬番"]]
        if nk and not src:
            src = "netkeiba"
    return odds, src




# ----------------------------------------------------------
# 画面表示用のHTML部品（ライト/ダーク両対応・スマホ幅対応）
# ----------------------------------------------------------
WAKU_COL = {1: ("#ffffff", "#222222"), 2: ("#222222", "#ffffff"), 3: ("#e53935", "#ffffff"), 4: ("#1e63d6", "#ffffff"),
            5: ("#f4c20d", "#222222"), 6: ("#2e9e4f", "#ffffff"), 7: ("#f28a1a", "#222222"), 8: ("#ec6fa8", "#222222")}
MARKS = ["◎", "○", "▲", "△", "△"]
RESULT_CSS = """
<style>
.ks{--paper:#eaf0ec;--card:#ffffff;--ink:#15231c;--mute:#5b6b62;--line:#d3ddd6;--tint:#f2f8f4;--turf:#0f3d2e;
--green:#1b7f52;--teal:#23728f;--red:#c62828;--amber-bg:#fff2d6;--amber-line:#e0a100;--blue-bg:#e3eefb;
--gold:#c8921a;--silver:#8895a0;--bronze:#a8662a;--violet:#6b5bd1;--orange:#d9480f;--brown:#8a5a2b;
font-family:-apple-system,'Hiragino Sans','Noto Sans JP',Meiryo,sans-serif;line-height:1.55;max-width:900px;
background:var(--paper);color:var(--ink);padding:12px;border-radius:14px}
@media (prefers-color-scheme:dark){.ks{--paper:#0d1612;--card:#16221c;--ink:#e8f0ea;--mute:#9fb1a6;--line:#2b3b32;--tint:#1b2b23;
--green:#46c48b;--teal:#62b8d8;--red:#ff7b73;--amber-bg:#3a2e17;--amber-line:#b8860b;--blue-bg:#17293c;
--gold:#e0b04a;--silver:#aab6bd;--bronze:#d08a50;--violet:#a79bff;--orange:#ff8a5c;--brown:#c99a6a}}
.ks *{box-sizing:border-box}
.ks .hero{background:var(--turf);color:#fff;border-radius:12px 12px 0 0;padding:16px 18px 14px}
.ks .hero,.ks .hero *{color:#fff!important}
.ks .hero .top{display:flex;gap:12px 18px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap}
.ks .hero h2{margin:0;padding:0;border:0;font-size:24px;font-weight:800;line-height:1.25;letter-spacing:.02em;color:#fff!important}
.ks .hero .when{margin-top:4px;font-size:13px;opacity:.9}
.ks .hero .rec{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.3);border-radius:10px;padding:7px 14px;text-align:center;min-width:150px}
.ks .hero .rec .k{display:block;font-size:11.5px;opacity:.9}
.ks .hero .rec .stars{display:block;font-size:22px;line-height:1.2;letter-spacing:3px;color:#ffd166!important}
.ks .hero .rec .lv{display:block;font-size:13px;font-weight:800}
.ks .hero .whyrec{margin-top:8px;font-size:12.5px;opacity:.92}
.ks .hero .pills{margin-top:10px;display:flex;flex-wrap:wrap;gap:6px}
.ks .hero .pills span{padding:2px 11px;border-radius:999px;font-size:12.5px;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.25)}
.ks .hero .pills span.key{background:#fff;color:#0f3d2e!important;border-color:#fff;font-weight:700}
.ks .gate{display:flex;height:7px;border-radius:0 0 12px 12px;overflow:hidden;margin-bottom:12px}
.ks .gate i{flex:1;display:block;box-shadow:inset 0 0 0 1px rgba(0,0,0,.12)}
.ks .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 14px;margin:10px 0;font-size:14px}
.ks .card.sub{font-size:12.5px;color:var(--mute)}
.ks .warn{background:var(--amber-bg);border-color:var(--amber-line);border-left:6px solid var(--amber-line);font-size:13.5px}
.ks .chg{background:var(--blue-bg);border-color:var(--teal);border-left:6px solid var(--teal);font-size:13.5px}
.ks h3{color:var(--ink)!important;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:22px 0 8px;padding-bottom:6px;border-bottom:2px solid var(--ink);font-size:17px;font-weight:800}
.ks .sub{color:var(--mute);font-size:12.5px;font-weight:400}
.ks h3 .sub{font-weight:400}
.ks .tblwrap{overflow-x:auto}
.ks table{width:100%;border-collapse:separate;border-spacing:0;background:var(--card);border:1px solid var(--line);border-radius:10px;font-size:13.5px}
.ks th{background:var(--tint);color:var(--mute);font-weight:700;font-size:12px;text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.ks td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
.ks tr:last-child td{border-bottom:none}
.ks tr.top td{background:var(--tint)}
.ks tr.p1 td:first-child{box-shadow:inset 5px 0 0 var(--gold)}
.ks tr.p2 td:first-child{box-shadow:inset 5px 0 0 var(--silver)}
.ks tr.p3 td:first-child{box-shadow:inset 5px 0 0 var(--bronze)}
.ks tr.cur td{background:var(--blue-bg)}
.ks td,.ks th{color:var(--ink)}
.ks th{color:var(--mute)}
.ks .rk{display:inline-grid;place-items:center;min-width:28px;height:28px;border-radius:50%;font-weight:800;font-size:15px;background:var(--line);color:var(--ink)}
.ks .rk.g1{background:var(--gold);color:#fff}.ks .rk.g2{background:var(--silver);color:#fff}.ks .rk.g3{background:var(--bronze);color:#fff}
.ks .mark{font-size:21px;font-weight:700;display:inline-block;width:26px;text-align:center}
.ks .mk0{color:var(--red)}.ks .mk1{color:var(--teal)}.ks .mk2{color:var(--green)}.ks .mk3,.ks .mk4{color:var(--mute)}
.ks .no{display:inline-block;min-width:26px;height:26px;line-height:26px;text-align:center;border-radius:6px;font-weight:800;font-size:13.5px;margin-right:7px;border:1px solid rgba(128,128,128,.5)}
.ks .nm{font-weight:800;font-size:15px}.ks .jk{color:var(--mute);font-size:12px;margin-left:6px}
.ks .why{display:block;font-size:12px;color:var(--mute);margin:2px 0 0 33px}
.ks .sty{font-size:11px;padding:1px 8px;border-radius:9px;background:var(--line);margin-left:6px;white-space:nowrap}
.ks .bar{display:inline-block;vertical-align:middle;width:78px;height:10px;border-radius:5px;background:var(--line);overflow:hidden;margin-right:8px}
.ks .bar i{display:block;height:100%;background:var(--green);border-radius:5px}
.ks .bar.g i{background:var(--teal)}
.ks .num{font-variant-numeric:tabular-nums;font-size:13.5px;white-space:nowrap}
.ks .num b{font-size:15px}
.ks .banner{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center}
.ks .kind,.ks .role{color:#fff}
.ks .kind{font-weight:800;font-size:15px;color:#fff;padding:2px 14px;border-radius:999px;background:var(--mute)}
.ks .kind.kd1{background:var(--green)}.ks .kind.kd2{background:#b7791f}.ks .kind.kd3{background:var(--red)}
.ks .tk{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--mute);border-radius:10px;margin:10px 0;overflow:hidden}
.ks .tk.r1{border-left-color:var(--green)}.ks .tk.r2{border-left-color:var(--violet)}.ks .tk.r3{border-left-color:var(--brown)}.ks .tk.r4{border-left-color:var(--orange)}
.ks .tk .hd{padding:9px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.ks .tk .hd b{font-size:16px}
.ks .tk .bd{padding:2px 14px 12px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.ks .role{font-size:11.5px;font-weight:700;padding:1px 10px;border-radius:9px;color:#fff;background:var(--mute)}
.ks .role.r1{background:var(--green)}.ks .role.r2{background:var(--violet)}.ks .role.r3{background:var(--brown)}.ks .role.r4{background:var(--orange)}
.ks .pts{margin-left:auto;font-size:12.5px;color:var(--mute)}
.ks .st{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);border-top:1px solid var(--line)}
.ks .st>div{background:var(--card);padding:8px 14px}
.ks .st .k{display:block;font-size:11.5px;color:var(--mute);margin-bottom:1px}
.ks .st b{font-size:16px}
.ks .st .big{font-size:20px;font-weight:800}
.ks .st .pay{background:var(--tint)}
.ks .st .up{color:var(--green)}.ks .st .dn{color:var(--mute)}
.ks .sep{color:var(--mute);font-weight:700;padding:0 2px}.ks .lab{font-size:12px;color:var(--mute)}
.ks .chip{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:7px;padding:2px 9px 2px 2px;font-size:13.5px;font-weight:600;background:var(--card)}
.ks .chip .no{margin-right:6px;border:none;border-radius:5px;min-width:24px;height:24px;line-height:24px}
.ks .pill{display:inline-block;padding:2px 11px;border-radius:12px;border:1px solid var(--line);font-weight:700;font-size:13.5px;background:var(--card)}
.ks .foot{font-size:11.5px;color:var(--mute);margin-top:16px}
.ks ul{margin:4px 0;padding-left:20px}
.ks .recs{display:flex;gap:8px;flex-wrap:wrap}
.ks .pcols{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px}.ks .pc{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:3px 0}
.ks .pt{margin:4px 0;font-size:13px}.ks .pcard.cur{border-left:6px solid var(--green)}
.ks .bktbl td:first-child{min-width:170px;white-space:normal}.ks .bktbl td.num{white-space:normal;min-width:110px}
.ks .hero .rec.tr .lv{font-size:17px}
.ks .skip{background:var(--amber-bg);border-color:var(--amber-line);border-left:6px solid var(--red);font-size:13.5px}
.ks .mt{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:4px 0}
.ks .mt .k{flex:1 1 240px;font-size:13.5px}.ks .mt .sub{flex:0 0 auto}
.ks .formation{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.ks .fc{background:var(--tint);border:1px solid var(--line);border-radius:10px;padding:8px}
.ks .fh{font-weight:800;margin-bottom:6px}
.ks .fm{display:flex;align-items:center;gap:6px;margin:4px 0;flex-wrap:wrap}
.ks .gapwrap{display:grid;grid-template-columns:minmax(260px,340px) 1fr;gap:12px;align-items:start}
.ks .gp{margin-bottom:8px;font-size:13px}.ks .gp .chip{margin:2px 6px 2px 0}
.ks .bk{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:8px}
.ks .bk>div{background:var(--tint);border-radius:8px;padding:6px 10px}
.ks .bk .k{display:block;font-size:11.5px;color:var(--mute)}.ks .bk b{font-size:16px}
.ks .bk .up{color:var(--green)}.ks .bk .dn{color:var(--mute)}
@media (max-width:560px){
.ks .formation{grid-template-columns:1fr}.ks .pcols{grid-template-columns:1fr}.ks .gapwrap{grid-template-columns:1fr}
.ks{padding:8px}.ks .hero h2{font-size:20px}.ks .bar{display:none}.ks td,.ks th{padding:7px 5px}
.ks .nm{white-space:nowrap}.ks .jk{display:block;margin:0 0 0 33px}.ks .sty{margin-left:33px;display:inline-block}
.ks .why{margin-left:0}.ks .st{grid-template-columns:1fr 1fr}
}
</style>
"""


def _wk(h):
    return WAKU_COL.get(h.get("枠"), ("#bbbbbb", "#222222"))


def no_html(h):
    bg, fg = _wk(h)
    txt = h["馬番"] if h.get("馬番") is not None else "-"
    return f"<span class='no' style='background:{bg};color:{fg}'>{txt}</span>"


def chip_html(h):
    return f"<span class='chip'>{no_html(h)}{htmllib.escape(h['名前'])}</span>"


def chips_html(idxs, horses):
    return "".join(chip_html(horses[i]) for i in idxs)


def spec_html(spec, horses, fallback=""):
    """買い目を『馬番の色付きチップ』で表示"""
    t, e = spec[0], htmllib.escape
    sep = lambda s: f"<span class='sep'>{s}</span>"
    lab = lambda s: f"<span class='lab'>{s}</span>"
    if t in ("単勝", "複勝"):
        return chips_html(spec[1], horses)
    if t == "枠連":
        return "".join(f"<span class='pill'>{a}-{b}</span>" for a, b in spec[1])
    if t in ("馬連", "ワイド"):
        return lab("軸") + chips_html([spec[1]], horses) + sep("－") + lab("相手") + chips_html(spec[2], horses)
    if t == "馬単":
        return lab("1着") + chips_html([spec[1]], horses) + sep("→") + lab("2着") + chips_html(spec[2], horses)
    if t == "3連複流し":
        return (lab("軸") + chips_html([spec[1]], horses) + sep("－") + lab("相手(2頭)") +
                chips_html(spec[2], horses))
    if t == "3連複BOX":
        return lab("BOX") + chips_html(spec[1], horses)
    if t == "3連単流し":
        return lab("1着") + chips_html([spec[1]], horses) + sep("→") + lab("2・3着") + chips_html(spec[2], horses)
    return e(fallback)


ROLE_CLS = {"本線": "r1", "押さえ": "r2", "狙い": "r4", "穴狙い": "r3"}


def gate_html():
    """枠番の8色の帯（画面の飾りではなく、競馬の枠色そのもの）"""
    return "<div class='gate'>" + "".join(f"<i style='background:{WAKU_COL[k][0]}'></i>" for k in range(1, 9)) + "</div>"


def buy_score(R):
    """購入おすすめ度（1〜5）。『本命の抜け具合』『データの揃い具合』『単勝の妙味』から作った目安で、的中や収益の保証ではない。
       → (星の数 or None[発走済み], ラベル, 理由のリスト)"""
    if R.get("started"):
        return None, "発走済み", ["発走時刻を過ぎています"]
    n = max(1, len(R["horses"]))
    conf = R["conf"]
    s_conf = min(max((conf - 0.12) / 0.28, 0.0), 1.0)
    s_data = min(R["n_ok"] / n, 1.0) * 0.7 + (0.3 if R["have_odds"] else 0.0)
    best_ev = max((ev for _, ev in R["value"]), default=0.0)
    s_val = min(max((best_ev - 1.0) / 0.5, 0.0), 1.0)
    stars = 1 + int(round(4 * (0.45 * s_conf + 0.30 * s_data + 0.25 * s_val)))
    why = []
    if conf >= 0.25:
        why.append(f"本命の勝率が高い（{conf * 100:.0f}%）")
    elif conf < 0.15:
        why.append("混戦で絞りにくい")
    if best_ev >= 1.1:
        why.append(f"単勝に妙味あり（勝率×オッズ {best_ev:.2f}）")
    if R["n_ok"] < 0.8 * n and not R["manual"]:
        why.append("過去成績の取得が不足")
    if not R["have_odds"]:
        why.append("オッズ未取得")
    if R["n_ok"] < 0.5 * n and not R["manual"]:
        stars = min(stars, 2)
    if R["manual"]:
        stars = min(stars, 3)
        why.append("手入力のため過去成績なし")
    label = {5: "買い向き", 4: "やや買い向き", 3: "普通", 2: "見送り寄り", 1: "見送り推奨"}[stars]
    return stars, label, why or ["目立つ強み・弱みはなし"]


def stars_text(n):
    return "★" * n + "☆" * (5 - n) if n else "—"


KIND_CLS = {"堅め": "kd1", "標準": "kd2", "混戦": "kd3"}


# ---- v11: 追加セクションのHTML部品 ------------------------------------------
UPSET_CLS = {"堅め": "kd1", "標準": "kd2", "荒れ模様": "kd3"}


def _meter(label, p, note=""):
    e = htmllib.escape
    return (f"<div class='mt'><span class='k'>{e(label)}</span><span class='bar'><i style='width:{min(100, p * 100):.0f}%'></i></span>"
            f"<b>{p * 100:.0f}%</b>{('<span class=sub>' + e(note) + '</span>') if note else ''}</div>")


def upset_html(R):
    u, e = R["upset"], htmllib.escape
    basis = u["basis"]
    hon = R["horses"][u["hon"]]
    body = (_meter(f"◎{hon['名前']}が3着に入れない", u["p_hon_out"]) +
            _meter(f"勝ち馬が{basis}6番手以下", u["p_long"], "人気薄の激走") +
            _meter(f"勝ち馬が{basis}9番手以下", u["p_big"], "大穴") +
            _meter("◎○▲の3頭がそろって3着以内", u["p_all3"]) +
            _meter("1着馬が◎○▲の中にいる", u["p_win_in_top3"]))
    return (f"<h3>荒れ方の分布 <span class='sub'>（シミュレーション{NUM_SIMULATIONS:,}回の中での割合）</span></h3>"
            f"<div class='card'><div class='banner'><span class='kind {UPSET_CLS.get(u['label'], '')}'>{e(u['label'])}</span>"
            f"<span class='sub'>荒れ度 {u['index'] * 100:.0f}（目安: 30未満=堅め／42以上=荒れ模様）</span></div>{body}</div>")


def gap_html(R):
    gap, horses = R["gap"], R["horses"]
    if not gap:
        return ""
    W, Hh, pad = 340, 300, 36
    mx = max(max(g["market"] for g in gap), max(g["model"] for g in gap)) * 1.12
    X = lambda v: pad + v / mx * (W - pad - 10)
    Y = lambda v: Hh - pad - v / mx * (Hh - pad - 10)
    pts = []
    for g in gap:
        cls = "var(--green)" if g["ratio"] >= 1.2 else ("var(--red)" if g["ratio"] <= 0.8 else "var(--mute)")
        no = horses[g["i"]]["馬番"]
        pts.append(f"<g><circle cx='{X(g['market']):.1f}' cy='{Y(g['model']):.1f}' r='10' style='fill:{cls};opacity:.9'/>"
                   f"<text x='{X(g['market']):.1f}' y='{Y(g['model']) + 4:.1f}' text-anchor='middle' font-size='11' font-weight='700' fill='#fff'>{no if no is not None else ''}</text></g>")
    ticks = "".join(f"<text x='{X(v):.0f}' y='{Hh - pad + 14}' text-anchor='middle' font-size='10' style='fill:var(--mute)'>{v * 100:.0f}%</text>"
                    f"<text x='{pad - 5}' y='{Y(v) + 3:.0f}' text-anchor='end' font-size='10' style='fill:var(--mute)'>{v * 100:.0f}%</text>"
                    for v in [mx * k / 4 for k in range(1, 4)])
    svg = (f"<svg viewBox='0 0 {W} {Hh}' width='100%' style='max-width:{W}px'>"
           f"<rect x='{pad}' y='10' width='{W - pad - 10}' height='{Hh - pad - 10}' style='fill:var(--tint);stroke:var(--line)'/>"
           f"<line x1='{X(0):.0f}' y1='{Y(0):.0f}' x2='{X(mx):.0f}' y2='{Y(mx):.0f}' style='stroke:var(--mute)' stroke-dasharray='4 4'/>"
           f"{ticks}{''.join(pts)}"
           f"<text x='{(W + pad) / 2:.0f}' y='{Hh - 4}' text-anchor='middle' font-size='11' style='fill:var(--mute)'>オッズから逆算した勝率</text>"
           f"<text x='11' y='{Hh / 2:.0f}' text-anchor='middle' font-size='11' style='fill:var(--mute)' transform='rotate(-90 11 {Hh / 2:.0f})'>モデルの勝率</text></svg>")
    hi = sorted([g for g in gap if g["ratio"] >= 1.2], key=lambda g: -g["ratio"])[:3]
    lo = sorted([g for g in gap if g["ratio"] <= 0.8], key=lambda g: g["ratio"])[:3]
    fmt = lambda g: (f"{chip_html(horses[g['i']])}<span class='sub'>モデル{g['model'] * 100:.1f}% / 市場{g['market'] * 100:.1f}%"
                     f"（{g['ratio']:.1f}倍）</span>")
    txt = ("<div class='gp'><b style='color:var(--green)'>モデルの方が高く見ている馬</b><br>" + ("<br>".join(fmt(g) for g in hi) or "<span class='sub'>目立つ馬なし</span>") + "</div>"
           "<div class='gp'><b style='color:var(--red)'>モデルの方が低く見ている馬</b><br>" + ("<br>".join(fmt(g) for g in lo) or "<span class='sub'>目立つ馬なし</span>") + "</div>")
    return ("<h3>市場との乖離 <span class='sub'>（点が対角線より上＝モデルが市場より高評価）</span></h3>"
            f"<div class='card gapwrap'>{svg}<div>{txt}<div class='sub' style='margin-top:6px'>"
            "市場の勝率は単勝オッズの逆数を合計1に直したものです。乖離は『お買い得』の保証ではなく、モデルと人気の見方の違いを示すだけです。</div></div></div>")


def cond_html(R):
    cc = R["cond_cmp"]
    if not cc:
        return ""
    horses = R["horses"]
    rows = []
    for c in cc:
        cur = " class='cur'" if c["cond"] == R["condition"] else ""
        rows.append(f"<tr{cur}><td><b>{c['cond']}</b></td><td>" +
                    "".join(f"<span class='mark mk{j}'>{MARKS[j]}</span>{chip_html(horses[i])} " for j, i in enumerate(c["idx"])) +
                    f"</td><td class='num'>{c['win'][0] * 100:.0f}%</td></tr>")
    return ("<h3>馬場状態別の上位3頭 <span class='sub'>（馬場だけを変えて比べたもの。今回の想定の行に色が付きます）</span></h3>"
            "<div class='tblwrap'><table><tr><th>馬場</th><th>◎ ○ ▲</th><th>◎の勝率</th></tr>" + "".join(rows) + "</table></div>")


def _yen_s(v):
    return f"{'+' if v > 0 else ('−' if v < 0 else '')}{abs(round(v)):,}円"


def bank_html(R):
    b, m = R["bank"], R.get("bank_m")
    if not b:
        return ""
    W, Hh, pl, pb = 640, 250, 64, 26
    n = b["n"]
    allv = b["p10"] + b["p90"] + ((m["p10"] + m["p90"]) if m else []) + [0]
    lo_v, hi_v = min(allv), max(allv)
    clipped = False
    if m:                                                    # モデル側が極端に大きい時は、オッズ通りの線が見える範囲に絞る
        mv = m["p10"] + m["p90"] + [0, -b["cost"] * 10]
        if max(abs(min(allv)), abs(max(allv))) > 4 * max(abs(min(mv)), abs(max(mv))):
            lo_v, hi_v, clipped = min(mv) * 1.3, max(mv) * 1.3, True
    span = (hi_v - lo_v) or 1.0
    X = lambda i: pl + i / n * (W - pl - 10)
    Y = lambda v: 10 + (hi_v - v) / span * (Hh - pb - 10)
    xs = range(n + 1)

    def fan(d, color, op):
        band = " ".join(f"{X(i):.1f},{Y(d['p90'][i]):.1f}" for i in xs) + " " + " ".join(f"{X(i):.1f},{Y(d['p10'][i]):.1f}" for i in reversed(xs))
        line = " ".join(f"{X(i):.1f},{Y(d['p50'][i]):.1f}" for i in xs)
        return (f"<polygon points='{band}' style='fill:{color};opacity:{op}'/>"
                f"<polyline points='{line}' fill='none' style='stroke:{color}' stroke-width='2.5'/>")
    yt = "".join(f"<text x='{pl - 6}' y='{Y(v) + 3:.0f}' text-anchor='end' font-size='10' style='fill:var(--mute)'>{_yen_s(v) if abs(v) > 1 else '0円'}</text>"
                 f"<line x1='{pl}' x2='{W - 10}' y1='{Y(v):.0f}' y2='{Y(v):.0f}' style='stroke:var(--line)'/>"
                 for v in (lo_v, lo_v + span / 2, hi_v))
    xt = "".join(f"<text x='{X(i):.0f}' y='{Hh - 12}' text-anchor='middle' font-size='10' style='fill:var(--mute)'>{i}</text>" for i in range(0, n + 1, max(1, n // 5)))
    svg = (f"<svg viewBox='0 0 {W} {Hh}' width='100%' style='max-width:{W}px'>{yt}"
           f"<defs><clipPath id='bkc'><rect x='{pl}' y='10' width='{W - pl - 10}' height='{Hh - pb - 10}'/></clipPath></defs>"
           f"<line x1='{pl}' x2='{W - 10}' y1='{Y(0):.0f}' y2='{Y(0):.0f}' style='stroke:var(--ink)' stroke-dasharray='5 4'/>"
           f"<g clip-path='url(#bkc)'>" + (fan(m, "var(--orange)", 0.20) if m else "") + fan(b, "var(--teal)", 0.20) + "</g>" + xt +
           f"<text x='{(W + pl) / 2:.0f}' y='{Hh - 1}' text-anchor='middle' font-size='10' style='fill:var(--mute)'>買ったレース数</text></svg>")
    legend = ("<div class='sub'><b style='color:var(--teal)'>■</b> このモデルの確率どおりに結果が出た場合　" +
              ("<b style='color:var(--orange)'>■</b> オッズ（市場）の見立てどおりに結果が出た場合" if m else "") + "（帯=上位10%〜下位10%、線=中央値）" +
              ("<br>※モデル側の推定が極端に大きいため、グラフはオッズ通りの範囲に絞っています（モデル側は枠外に伸びます）。" if clipped else "") + "</div>")
    rows = []
    for lab, f in (("1レースの購入額", lambda d: f"{d['cost']:,.0f}円"), ("期待回収率", lambda d: f"{d['roi'] * 100:.0f}%" + ("（200%以上は過大評価の疑い）" if d["roi"] >= 2 else "")),
                   ("最終的にプラスの確率", lambda d: f"{d['p_profit'] * 100:.0f}%"),
                   (f"{n}レース後（中央値）", lambda d: _yen_s(d["final50"])), ("うまくいった時（上位10%）", lambda d: _yen_s(d["final90"])),
                   ("悪かった時（下位10%）", lambda d: _yen_s(d["final10"])), ("途中の最大の落ち込み（下位10%の場合）", lambda d: _yen_s(d["dd10"])),
                   ("1レースで1点でも当たる確率", lambda d: f"{d['hit_any'] * 100:.0f}%")):
        rows.append(f"<tr><td>{lab}</td><td class='num'>{f(b)}</td>" + (f"<td class='num'>{f(m)}</td>" if m else "") + "</tr>")
    tbl = ("<div class='tblwrap'><table class='bktbl'><tr><th></th><th>モデル通り</th>" + ("<th>オッズ通り</th>" if m else "") + "</tr>" + "".join(rows) + "</table></div>")
    return (f"<h3>資金推移のシミュレーション <span class='sub'>（この買い目を{n}レース買い続けた場合の累計損益）</span></h3>"
            f"<div class='card'>{svg}{legend}{tbl}"
            f"<div class='sub' style='margin-top:6px'>モデルの的中確率は、オッズより自信過剰になりがちで、特にモデルが人気薄を高く評価した買い目ほどその影響を受けます。"
            f"実際の成績は『オッズ通り』の線に近くなることが多い点にご注意ください（払戻は推定値で、控除率の分、オッズ通りなら回収率は100%を下回るのが普通です）。"
            f"回収率が100%を下回る買い方は、続けるほどマイナスに近づきます。娯楽の範囲で、無理のない金額にしてください。</div></div>")


def mine_html(R):
    m = R["mine"]
    if not m:
        return ""
    horses = R["horses"]
    by_uma = {h["馬番"]: h for h in horses}
    mk = lambda picks: "".join(f"<span class='mark mk{j}'>{MARKS[j]}</span>{chip_html(by_uma[u])} " for j, u in enumerate(picks))
    ai = [int(u) for u in R["df"]["馬番"].head(len(m["picks"]))]
    det = "　".join(f"{u}番 <span class='sub'>（AIの勝率{w:.1f}%・予想{r}位）</span>" for u, w, r in zip(m["picks"], m["win"], m["rank"]))
    return (f"<div class='card'><b>あなたの印とAIの比較</b><br>あなた: {mk(m['picks'])}<br>AI: {mk(ai)}<br><span class='sub'>{det}</span>"
            "<br><span class='sub'>印を入れて予想した分は、結果が出たあと「成績・答え合わせ」でAIとの的中率を比べられます。</span></div>")


def result_html(R, standalone=False):
    e = htmllib.escape
    info, df, horses = R["info"], R["df"], R["horses"]
    by_name = {h["名前"]: h for h in horses}
    wx_txt = f"降水確率 最大{R['wx']['pop_max']}%" if R["wx"] and R["wx"].get("pop_max") is not None else ""
    cs = R["cond_src"].replace("（", "・").replace("）", "")
    badges = [(f"馬場 {R['condition']}（{cs}）", True), (R["pace_txt"], True), (f"逃げ・先行 {R['n_front']}頭", False)]
    if wx_txt:
        badges.append((wx_txt, False))
    if R["bias"]:
        badges.append(("当日の傾向: " + R["bias"]["desc"], False))
    stars, rec_label, rec_why = buy_score(R)
    rec_html = (f"<div class='rec'><span class='k'>購入おすすめ度</span><span class='stars'>{stars_text(stars)}</span>"
                f"<span class='lv'>{e(rec_label)}</span></div>")
    pills = "".join(("<span class='key'>" if k else "<span>") + e(b) + "</span>" for b, k in badges)
    t_lab, t_why = R["trust"]
    trust_html = (f"<div class='rec tr'><span class='k'>データ信頼度</span><span class='stars'>{'●●●' if t_lab == '高' else ('●●○' if t_lab == '中' else '●○○')}</span>"
                  f"<span class='lv'>{t_lab}</span></div>")
    t_why_txt = e("／".join(t_why))
    out = [f"<div class='ks'>{RESULT_CSS}" if not standalone else "<div class='ks'>",
           f"<div class='hero'><div class='top'><div><h2>{e(info['name'] or '')}</h2>"
           f"<div class='when'>{e(info['date'] or '')} {e(info['venue_r'] or '')} ／ {e(R['surface'])}{R['distance']}m ／ {len(horses)}頭</div></div>"
           f"<div class='recs'>{rec_html}{trust_html}</div></div>"
           f"<div class='pills'>{pills}</div><div class='whyrec'>おすすめ度の理由: {e('／'.join(rec_why))}<br>データ信頼度の理由: {t_why_txt}</div></div>"
           + gate_html()]
    notes = []
    if R["cancelled"]:
        notes.append("出走取消・除外: " + "、".join(R["cancelled"]))
    if R["n_ok"] < 0.5 * len(horses):
        notes.append("過去成績を十分に取得できなかったため、予想の精度は低くなります。時間をおいて再実行してください。")
    if R.get("started"):
        notes.append("このレースは発走時刻を過ぎています。表示は参考で、成績の記録には含めません。")
    if R["nar"]:
        notes.append("地方競馬: 騎手成績は中央のリーディングを使うため、地方の騎手は標準値で計算しています。馬場傾向・血統・調教も取れない場合があります。")
    if R["manual"]:
        notes.append("海外レースです。JRAの海外競馬ページの出走馬・参考オッズから計算しており、過去成績・騎手成績・馬場傾向は使っていません（参考程度に）。"
                     "参考オッズは日本のオッズと異なる場合があります。" if R["info"].get("ovs") else
                     "手入力レースです。過去成績・騎手成績・馬場傾向は使わず、単勝オッズと入力した脚質を中心にした予想です（参考程度に）。")
    if R["pace_mode"] != "自動(想定)":
        notes.append(f"ペースを『{R['pace_mode']}』に指定した仮定の予想です（自動の想定は『{pace_text(R['auto_pace'])}』）。成績の記録には含めません。")
    if R["cond_mode"] in ("良", "稍重", "重", "不良"):
        notes.append(f"馬場を『{R['cond_mode']}』に指定した仮定の予想です。成績の記録には含めません。")
    if R["scratch_names"]:
        notes.append(f"{'、'.join(R['scratch_names'])}を出走取消にした仮定の予想です。成績の記録には含めません。")
    if notes:
        out.append("<div class='card warn'>⚠️ " + "<br>⚠️ ".join(e(n) for n in notes) + "</div>")
    if stars is not None and stars <= 2:
        out.append("<div class='card skip'><b>今回は見送り寄りです。</b> 本命が抜けていない、またはデータが足りないレースです。"
                   "無理に買わず、見送るのも一つの選択です。</div>")
    out.append(mine_html(R))
    if R["changes"] is not None:
        out.append("<div class='card chg'><b>前回の予想からの変化</b><ul>" +
                   "".join(f"<li>{e(c)}</li>" for c in (R["changes"] or ["変化はありません"])) + "</ul></div>")

    # ---- 予想順位 ----
    est_odds, _eo, _pop, pop_lab, pop_note = not R["have_odds"], {}, {}, "", ""
    if SHOW_POPULARITY:
        if est_odds:
            # オッズ未公開: モデルの勝率から逆算（単勝の払戻率80%）して、予想オッズ・予想人気とする
            _eo = {r["馬名"]: min(999.9, max(1.0, round(0.8 / max(r["勝率(%)"] / 100.0, 0.0008), 1))) for _, r in df.iterrows()}
            _srt = sorted(df.itertuples(index=False), key=lambda t: (-t[list(df.columns).index("勝率(%)")], t[list(df.columns).index("予想順位")]))
            _pop = {t[list(df.columns).index("馬名")]: i + 1 for i, t in enumerate(_srt)}
            pop_lab, pop_note = "AI推定", "実際の人気とは異なる場合あり"
        else:
            _eo = {r["馬名"]: float(r["単勝オッズ"]) for _, r in df.iterrows()}
            _pop = {r["馬名"]: int(r["人気"]) for _, r in df.iterrows()}
            if R.get("started"):
                pop_lab, pop_note = "確定", "発走後のオッズ"
            elif R["manual"] or R.get("odds_typed"):
                pop_lab, pop_note = "入力", "入力したオッズ"
            else:
                pop_lab, pop_note = "現在", f"{R['odds_time']}時点" if R.get("odds_time") else "現在のオッズ"
    maxwin = max(float(df["勝率(%)"].max()), 1.0)
    rows = []
    for k, (_, r) in enumerate(df.iterrows()):
        h = by_name[r["馬名"]]
        top = k < 5
        mark = MARKS[k] if top else ""
        why = "／".join(R["reasons"][r["馬名"]][:2]) if top else ""
        odds_td = "" if not SHOW_POPULARITY else (f"<td class='num'>{_pop[r['馬名']]}番人気<br><span class='sub'>{'約' if est_odds else ''}{_eo[r['馬名']]:.1f}倍</span></td>")
        rank_i = int(r['予想順位'])
        medal = {1: "g1", 2: "g2", 3: "g3"}.get(rank_i, "")
        rows.append(
            f"<tr class='{('top p' + str(rank_i)) if rank_i <= 3 else ('top' if top else '')}'>"
            f"<td><span class='mark mk{k}'>{mark}</span><span class='rk {medal}'>{rank_i}</span></td>"
            f"<td>{no_html(h)}<span class='nm'>{e(r['馬名'])}</span><span class='jk'>{e(str(r['騎手'] or ''))}</span>"
            f"<span class='sty'>{e(r['脚質'])}</span>" + (f"<span class='why'>{e(why)}</span>" if why else "") + "</td>"
            f"<td class='num'><span class='bar'><i style='width:{min(100, r['勝率(%)'] / maxwin * 100):.0f}%'></i></span><b>{r['勝率(%)']:.1f}%</b></td>"
            f"<td class='num'><span class='bar g'><i style='width:{min(100, r['複勝率(%)']):.0f}%'></i></span>{r['複勝率(%)']:.1f}%</td>"
            f"{odds_td}</tr>")
    out.append(f"<h3>予想順位 <span class='sub'>（{NUM_SIMULATIONS:,}回のシミュレーション）</span></h3><div class='tblwrap'><table>"
               "<tr><th>順位</th><th>馬</th><th>勝率</th><th>3着内率</th>"
               + (f"<th>{pop_lab}人気・オッズ<br><span class='sub'>{e(pop_note)}</span></th>" if SHOW_POPULARITY else "") + "</tr>"
               + "".join(rows) + "</table></div>"
               + ("" if not SHOW_POPULARITY else "<div class='sub'>オッズを取得できなかったため、右の人気・オッズは<b>AIの勝率から逆算した推定</b>です（払戻率80%）。実際の人気とは大きく違うことがあります。"
                  "オッズが取れるようになると現在の値に、発走後に更新すると確定の値に変わります。</div>" if est_odds else
                  "<div class='sub'>人気・オッズは、予想を更新するたびに最新の値に置き換わります。発走後に更新すると確定の値になります。</div>"))

    out.append(upset_html(R))
    out.append(gap_html(R))
    out.append(cond_html(R))

    # ---- 推奨馬券 ----
    out.append("<h3>推奨馬券</h3>")
    out.append(f"<div class='card banner'><span class='kind {KIND_CLS.get(R['kind'], '')}'>{e(R['kind'])}</span>"
               f"<span>本命の勝率 <b>{R['conf'] * 100:.1f}%</b></span>"
               f"<span>軸馬 <b>{e(R['axis_lab'])}</b></span>" + (f"<span>穴馬 <b>{e(R['hole_lab'])}</b></span>" if R["hole_lab"] else "")
               + "</div>")
    if not R["tickets"]:
        out.append("<div class='card warn'>この券種の買い目を作れませんでした（枠順が未確定の場合、枠連は作れません）。</div>")
    total_pts = total_yen = 0
    pay_list = R.get("pay_info") or [None] * len(R["tickets"])
    for (role, kind_t, buy, pts, hit, spec), u, px in zip(R["tickets"], R["units"], pay_list):
        total_pts += pts
        if R["budget"]:
            if u:
                money = f"1点{u * UNIT:,}円 × {pts}点 = <b>{u * pts * UNIT:,}円</b>"
                total_yen += u * pts * UNIT
            else:
                money = "<span class='sub'>予算内に収まらないため見送り</span>"
        else:
            money = f"各100円なら {pts * UNIT:,}円"
        pay_html = ""
        if px:
            span = f"{px['lo']:,}円" if px["lo"] == px["hi"] else f"{px['lo']:,}〜{px['hi']:,}円"
            mult = ""
            if R["budget"] and u:
                a_, b_ = px["lo"] * u, px["hi"] * u
                mult = f"<br><span class='sub'>{u * UNIT:,}円/点なら {(f'{a_:,}円' if a_ == b_ else f'{a_:,}〜{b_:,}円')}</span>"
            roi = px["ret"]
            roi_txt = "200%以上" if roi >= 2 else format(roi * 100, ".0f") + "%"
            pay_html = (f"<div class='pay'><span class='k'>的中時の払戻（100円あたり）</span><b>{span}</b>{mult}</div>"
                        f"<div class='pay'><span class='k'>期待回収率（モデル）</span><b class='{'up' if roi >= 1 else 'dn'}'>{roi_txt}</b></div>")
        out.append(
            f"<div class='tk {ROLE_CLS.get(role, '')}'><div class='hd'><span class='role {ROLE_CLS.get(role, '')}'>{e(role)}</span><b>{e(kind_t)}</b>"
            f"<span class='pts'>{pts}点</span></div><div class='bd'>{spec_html(spec, horses, buy)}</div>"
            f"<div class='st'><div><span class='k'>的中率の目安</span><span class='bar g'><i style='width:{min(100, hit * 100):.0f}%'></i></span>"
            f"<b class='big'>{hit * 100:.1f}%</b></div>"
            f"<div><span class='k'>購入額</span><b>{money}</b></div>{pay_html}</div></div>")
    if R["tickets"] and R.get("pay_info"):
        out.append(f"<div class='card sub'>払戻の目安は、{e(str(R['odds_time']))} 時点の単勝オッズから、各馬が勝つ確率を逆算し、"
                   "組み合わせごとの確率と払戻率で推定したものです（単勝は実際のオッズ、複勝は取れた時だけ公表の幅）。"
                   "発売が進むとオッズも払戻も変わります。期待回収率は『このモデルの確率で買い続けた時の回収率の目安』で、"
                   "100%を超えるのは、モデルがオッズより有利と見ている買い目です（当たる保証ではありません）。</div>")
    elif R["tickets"]:
        out.append("<div class='card sub'>オッズまたは馬番が取得できていないため、払戻の目安は計算できませんでした。</div>")
    if R["tickets"]:
        if R["budget"] and total_yen == 0:
            need = min(t[3] for t in R["tickets"]) * UNIT
            out.append(f"<div class='card warn'>予算 {R['budget']:,}円では買い目を組めません。最も点数の少ない券でも{need:,}円必要です。</div>")
        elif R["budget"]:
            out.append(f"<div class='card'>予算 <b>{R['budget']:,}円</b> のうち <b>{total_yen:,}円</b> を配分しました"
                       f"<span class='sub'>（100円単位。残りは端数）</span></div>")
        else:
            out.append(f"<div class='card'>すべて100円ずつ買う場合: 合計 <b>{total_pts}点・{total_pts * UNIT:,}円</b>"
                       f"<span class='sub'>（予算欄に金額を入れると、自動で配分します）</span></div>")
    out.append(bank_html(R))
    if R["value"]:
        out.append("<div class='card'><b>単勝の妙味</b><span class='sub'>（勝率×オッズが1.1以上）</span><br>" +
                   "　".join(f"{e(n)} <span class='sub'>({ev:.2f})</span>" for n, ev in R["value"]) + "</div>")
    out.append("<div class='foot'>過去成績・適性・血統・調教・展開・当日の馬場傾向からの推定です。当日の気配や展開の乱れは反映できません。<br>"
               "購入おすすめ度は、本命の抜け具合・データの揃い具合・単勝の妙味から作った目安です。"
               "娯楽・参考用です。的中や収益を保証するものではありません。馬券は20歳になってから。のめり込みにご注意ください。</div></div>")
    return "".join(out)


def batch_html(rows):
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    gate = gate_html().replace("class='gate'", "class='gate' style='border-radius:6px'")
    return (f"<div class='ks'>{RESULT_CSS}{gate}<div class='tblwrap'><table><tr><th>時刻</th><th>レース</th><th>本命（勝率）</th>"
            f"<th>対抗</th><th>単穴</th><th>妙味</th><th>傾向</th><th>おすすめ度</th><th>荒れ方</th><th>信頼度</th></tr>{body}</table></div></div>")


# ----------------------------------------------------------
# 10. 評価理由・予算配分・前回との比較・共有
# ----------------------------------------------------------
FACTOR_TEXT = {
    "距離適性": ("今回の距離で好成績", "今回の距離は実績が薄い／苦戦"),
    "馬場適性": ("今の馬場に向く", "今の馬場は向かない可能性"),
    "コース適性": ("この競馬場で好走歴あり", "この競馬場は実績が乏しい"),
    "調子": ("近走の調子が上向き", "近走は下降気味"),
    "間隔": ("ローテーションは良好", "休み明け／間隔が詰まっている"),
    "乗り替わり": ("騎手が強化／継続騎乗", "騎手が弱化／乗り替わり"),
    "斤量差": ("前走より斤量が軽い", "前走より斤量が重い"),
    "体重増減": ("馬体重は安定", "馬体重の大きな増減"),
    "枠補正": ("枠順が有利", "枠順が不利"),
    "脚質傾向": ("当日の傾向に脚質が合う", "当日の傾向に脚質が合わない"),
    "血統適性": ("血統的に条件が合う", "血統的には条件が合いにくい"),
    "調教": ("調教の評価が高い", "調教の評価が低め／不安コメント"),
}


def make_reasons(horses, factors, pre, front, mean_pace, ranks_avg, odds_by_name, odds_rank):
    """各馬の評価理由（日本語の短文リスト）。補正値の大きい順に最大3つ＋基礎能力・展開・人気との差"""
    H = len(horses)
    base = pre - factors.sum(axis=1).to_numpy()
    base_rank = (-base).argsort().argsort() + 1
    pred_rank = ranks_avg.argsort().argsort() + 1
    out = {}
    for i, h in enumerate(horses):
        items = []
        if base_rank[i] <= max(3, H // 5):
            items.append(f"基礎能力は上位（{base_rank[i]}位）")
        elif base_rank[i] > H - max(3, H // 5):
            items.append(f"基礎能力は下位（{base_rank[i]}位）")
        fv = sorted(((c, float(factors.iloc[i][c])) for c in factors.columns), key=lambda x: -abs(x[1]))
        for c, v in fv[:3]:
            if abs(v) >= 0.5 and c in FACTOR_TEXT:
                items.append(FACTOR_TEXT[c][0 if v > 0 else 1] + f"（{v:+.1f}）")
        if front[i] >= 0.65 and mean_pace < -0.3:
            items.append("スロー想定で逃げ・先行に有利")
        elif front[i] <= 0.35 and mean_pace > 0.3:
            items.append("ハイペース想定で差し・追込が届きやすい")
        elif front[i] >= 0.65 and mean_pace > 0.3:
            items.append("ハイペース想定で先行は苦しい展開")
        elif front[i] <= 0.35 and mean_pace < -0.3:
            items.append("スロー想定で後方は届きにくい")
        if odds_rank is not None and abs(odds_rank[i] - pred_rank[i]) >= 3:
            items.append(f"{odds_rank[i]}番人気だが予想{pred_rank[i]}位（" +
                         ("人気より高評価" if pred_rank[i] < odds_rank[i] else "人気より低評価") + "）")
        out[h["名前"]] = items or ["目立つ強み・弱みはなし（総合力で評価）"]
    return out


ROLE_W = {"本線": 3.0, "押さえ": 1.5, "狙い": 1.0, "穴狙い": 1.0}


def allocate_budget(tickets, budget):
    """予算(円)を100円単位で配分。戻り値: 各チケットの『1点あたりの口数(100円単位)』。0=買わない
    重要度の高い券から1口ずつ確保し、余りは重み×的中率の高い券へ追加"""
    n = len(tickets)
    units = [0] * n
    total = int(budget) // UNIT
    if total <= 0 or not n:
        return units
    w = [ROLE_W.get(t[0], 1.0) for t in tickets]
    used = 0
    for i in sorted(range(n), key=lambda i: (-w[i], -tickets[i][4])):
        if used + tickets[i][3] <= total:
            units[i], used = 1, used + tickets[i][3]
    left = total - used
    while True:
        cand = [i for i in range(n) if units[i] and tickets[i][3] <= left]
        if not cand:
            break
        i = max(cand, key=lambda i: w[i] * (tickets[i][4] ** 0.5) / units[i])
        units[i] += 1
        left -= tickets[i][3]
    return units


_snap = {}          # race_id → 前回の予想時点の状態（直前情報の比較用）


def diff_snapshot(race_id, now):
    prev = _snap.get(race_id)
    _snap[race_id] = now
    if not prev:
        return None
    ch = []
    for n in sorted(set(now["cancelled"]) - set(prev["cancelled"])):
        ch.append(f"出走取消・除外: {n}")
    for n, cur in now["horses"].items():
        old = prev["horses"].get(n)
        if not old:
            continue
        if old["騎手"] and cur["騎手"] and old["騎手"] != cur["騎手"]:
            ch.append(f"騎手変更: {n}（{old['騎手']} → {cur['騎手']}）")
        if cur["馬体重"] and cur["馬体重"] != old["馬体重"]:
            ch.append(f"馬体重: {n} {old['馬体重'] or '未発表'} → {cur['馬体重']}")
        if old["odds"] and cur["odds"] and abs(cur["odds"] / old["odds"] - 1) >= 0.2:
            pct = (cur["odds"] / old["odds"] - 1) * 100
            ch.append(f"オッズ変動: {n} {old['odds']:.1f} → {cur['odds']:.1f}（{pct:+.0f}%・" +
                      ("売れている" if pct < 0 else "人気落ち") + "）")
    if prev["top3"] != now["top3"]:
        ch.append("予想上位3頭: " + "・".join(prev["top3"]) + " → " + "・".join(now["top3"]))
    return ch


def share_html(R, budget_units=None):
    return ("<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{htmllib.escape(R['info']['name'] or '予想')}</title>{RESULT_CSS}"
            "<style>body{margin:0;padding:12px;background:#dfe7e2}@media (prefers-color-scheme:dark){body{background:#080e0b}}</style></head><body>"
            + result_html(R, standalone=True) +
            f"<div class='ks'><div class='foot'>作成: {htmllib.escape(R['info']['fetched_at'])}</div></div></body></html>")


def make_record(R):
    horses, df = R["horses"], R["df"]
    avg = dict(zip(df["馬名"], df["平均着順"]))
    win = dict(zip(df["馬名"], df["勝率(%)"]))
    top3 = dict(zip(df["馬名"], df["複勝率(%)"]))
    fl = lambda x: None if x is None else float(x)
    hs = []
    for i, h in enumerate(horses):
        hs.append({"uma": h["馬番"], "waku": h["枠"], "name": h["名前"], "win": float(win[h["名前"]]),
                   "top3": float(top3[h["名前"]]), "avg": float(avg[h["名前"]]),
                   "odds": R["odds_by_name"].get(h["名前"]) if R["have_odds"] else None,
                   "pre": float(R["pre"][i]), "market": fl(R["market"][i]) if R["market"] is not None else None,
                   "front": float(R["front"][i]), "instab": float(R["instab"][i])})
    d = R["info"].get("date_obj")
    return {"race_id": R["race_id"], "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "date": d.isoformat() if d else None, "name": R["info"]["name"], "venue_r": R["info"]["venue_r"],
            "surface": R["surface"], "distance": R["distance"], "condition": R["condition"],
            "params": {"blend": R["blend"], "scale": ABILITY_SCALE}, "horses": hs,
            "all_tickets": [ser_ticket(t, horses) for k in TICKET_ORDER for t in R["by_type"].get(k, [])],
            "auto_tickets": [ser_ticket(t, horses) for t in R["auto_tickets"]], "result": None,
            "stars": buy_score(R)[0], "upset": R["upset"]["label"], "trust": R["trust"][0], "kind": R["kind"],
            "ai_top": [int(u) for u in df["馬番"].head(3) if pd.notna(u)],
            "mine": R["mine"]["picks"] if R["mine"] else []}


# ----------------------------------------------------------
# 11. 分析本体（画面に依存しない）
# ----------------------------------------------------------
def analyze(race_id, force=False, ticket_mode="おまかせ(自動)", budget=0, seed=None, save=True,
            pace_mode="自動(想定)", manual=None, cond_mode="自動", scratch=None, my_picks="", odds_text=""):
    pace_fixed = PACE_MODES.get(pace_mode)
    if str(race_id).startswith("ovs:") and not manual:
        manual = overseas_manual(race_id, force=force)          # 海外レース: JRAの海外競馬ページから取得（手入力と同じ計算経路）
    if manual:
        if not str(race_id).startswith("ovs:"):
            race_id = "manual"
        info, horses, cancelled, notes, race_src = manual_race(manual)
    else:
        info, horses, cancelled, notes, race_src = load_race_any(race_id, force=force)
    scratch_names = []
    if scratch:                                              # もしも: 指定した馬が出走取消だった場合
        sc = set(scratch)
        keep = [h for h in horses if h["馬番"] not in sc]
        if len(keep) < 4:
            raise ManualInputError("取消にすると出走馬が4頭未満になります")
        scratch_names = [f"{h['馬番']}{h['名前']}" for h in horses if h["馬番"] in sc]
        horses = keep
    surface = info["surface"] if info["surface"] in ("芝", "ダ") else "芝"
    distance = info["distance"] or 2000
    d = info.get("date_obj")
    pt = info.get("post_time") or "15:00"
    race_dt = datetime.combine(d, datetime.strptime(pt, "%H:%M").time()) if d else None
    year = info["year"] or datetime.now().year

    if manual:
        na = {"runs": {}, "src": {}, "skills": {}, "problems": [], "training": {}}
        yb = {"denma": {}, "odds": {}, "odds_meta": {}, "bias": None, "bias_races": 0, "problems": []}
        wx = None
    else:
        with ThreadPoolExecutor(max_workers=3) as ex:
            fa = ex.submit(task_netkeiba, horses, race_id, year, force)
            fb = ex.submit(task_yahoo, race_id, surface, force)
            fc = ex.submit(task_weather, race_id, race_dt)
            na, yb, wx = fa.result(), fb.result(), fc.result()
    if scratch_names:
        notes = notes + [f"もしも: {'、'.join(scratch_names)}を出走取消として計算しています"]
    problems = notes + na["problems"] + yb["problems"]
    mism = merge_yahoo(horses, yb["denma"])
    skills = na["skills"]
    for h in horses:
        full = JOCKEY_BY_ID.get(h.get("jockey_id") or "")
        if not full:
            hits = [k for k in skills if same_jockey(k, h["騎手"])]
            full = hits[0] if len(hits) == 1 else None
        if full:
            h["騎手"] = full

    if info["condition"]:
        condition, cond_src = info["condition"], ("入力値" if manual else "発表値")
    elif wx:
        condition, cond_src = wx["going"], "天気予報から推定"
    else:
        condition, cond_src = "良", "データなし（良で計算）"
    if cond_mode in ("良", "稍重", "重", "不良"):                # もしも: 馬場状態を指定
        condition, cond_src = cond_mode, "もしも指定"
    tr_all = na.get("training") or {}
    tr_hit = {h["名前"]: tr_all[h["名前"]] for h in horses if h["名前"] in tr_all}
    training = tr_hit if len(tr_hit) >= 0.5 * len(horses) else {}
    ctx = {"dist": distance, "surface": surface, "venue": ALL_VENUE.get(race_id[4:6], ""),
           "condition": condition, "today": today_jst(), "bias": yb["bias"], "training": training}

    profiles, pprob = make_profiles(horses, na["runs"], ctx)
    n_ok = len(profiles)
    if manual:
        odds_all, odds_src = {h["名前"]: h["odds"] for h in horses}, "手入力"
    else:
        odds_all, odds_src = collect_odds(horses, race_id, yb["odds"])
    odds_typed = False
    if (odds_text or "").strip() and not manual:
        typed, oerr = parse_odds_text(odds_text, horses)
        if typed:
            odds_all, odds_src, odds_typed = typed, "手入力", True
        else:
            problems.append("人気・オッズの入力は使いませんでした: " + oerr)
    pre, factors = base_abilities(horses, ctx, profiles, skills)
    market = market_vector(horses, odds_all)
    have_odds = market is not None
    odds_by_name = odds_all if have_odds else {}
    blend = (MANUAL_BLEND if manual else MARKET_BLEND) if have_odds else 0.0
    ability = finalize_ability(pre, market, blend, ABILITY_SCALE)

    P = [profiles.get(h["名前"], DEFAULT_PROFILE) for h in horses]
    front = np.array([h["front_hint"] if h.get("front_hint") is not None else p["front"] for h, p in zip(horses, P)])
    instab = np.array([p["instab"] for p in P])
    order, ranks, mean_pace, n_front = simulate(ability, front, instab, NUM_SIMULATIONS, seed, pace_fixed=pace_fixed)
    auto_p, _ = auto_pace(front)
    names = [h["名前"] for h in horses]
    labels = [f"{h['馬番']}{h['名前']}" if h["馬番"] else h["名前"] for h in horses]
    win, top3 = (ranks == 1).mean(axis=0) * 100, (ranks <= 3).mean(axis=0) * 100
    avg_rank = ranks.mean(axis=0)
    df = pd.DataFrame({
        "枠": [h["枠"] for h in horses], "馬番": [h["馬番"] for h in horses], "馬名": names,
        "騎手": [h["騎手"] for h in horses], "脚質": [style_label(f) for f in front],
        "平均着順": avg_rank, "勝率(%)": win, "複勝率(%)": top3})
    df["予想順位"] = df["平均着順"].rank(method="first").astype(int)
    odds_rank = None
    if have_odds:
        df["単勝オッズ"] = df["馬名"].map(odds_by_name)
        df["人気"] = df["単勝オッズ"].rank(method="min").astype(int)
        odds_rank = (np.array([odds_by_name[n] for n in names])).argsort().argsort() + 1
    df = df.sort_values("平均着順").reset_index(drop=True)
    show = ["予想順位", "枠", "馬番", "馬名", "騎手", "脚質", "勝率(%)", "複勝率(%)"] + (["人気", "単勝オッズ"] if have_odds else [])
    fmt = {"予想順位": "{:.0f}", "枠": "{:.0f}", "馬番": "{:.0f}", "勝率(%)": "{:.1f}", "複勝率(%)": "{:.1f}",
           "人気": "{:.0f}", "単勝オッズ": "{:.1f}"}
    kind, conf, axis_lab, hole_lab, auto_t, value, by_type = recommend(
        ranks, horses, labels, odds_by_name if have_odds else None, instab)
    tickets = sorted(select_tickets(ticket_mode, auto_t, by_type), key=lambda t: -t[4])   # 的中率の高い順（同率は元の順）
    units = allocate_budget(tickets, budget) if budget else [1] * len(tickets)
    reasons = make_reasons(horses, factors, pre, front, mean_pace, avg_rank, odds_by_name if have_odds else None, odds_rank)
    place_by_uma = {u: v["place"] for u, v in (yb["odds"] or {}).items() if v.get("place")}
    try:
        pay_info = payout_table(tickets, horses, ranks, odds_by_name if have_odds else None, place_by_uma, race_id)
    except Exception:
        pay_info = None
    nb = None
    try:
        nb = ticket_net_samples(tickets, units, horses, ranks, odds_by_name if have_odds else None, place_by_uma, race_id)
    except Exception:
        nb = None
    bank = bankroll_fan(*nb) if nb else None
    bank_m = None                                            # 比較用: オッズ（市場）の見立て通りに結果が出た場合
    if nb and market is not None:
        try:
            _, rk_m, _, _ = simulate(finalize_ability(pre, market, 1.0, ABILITY_SCALE), front, instab, 30000,
                                     None if seed is None else seed + 1, pace_fixed=pace_fixed)
            nb2 = ticket_net_samples(tickets, units, horses, rk_m, odds_by_name, place_by_uma, race_id)
            bank_m = bankroll_fan(*nb2, seed=20260102) if nb2 else None
        except Exception:
            bank_m = None
    upset = upset_stats(ranks, odds_by_name if have_odds else None, horses)
    gap = market_gap(horses, win, odds_by_name) if have_odds else []
    try:
        cond_cmp = [] if manual else cond_compare(horses, na["runs"], ctx, skills, market, blend)
    except Exception:
        cond_cmp = []
    trust = data_trust(len(horses), n_ok, have_odds, is_nar(race_id), bool(manual), yb["bias"], len(training))
    mine = None
    pk = parse_umas(my_picks, horses)
    if pk:
        by_uma = {h["馬番"]: i for i, h in enumerate(horses)}
        rk = dict(zip(df["馬番"], df["予想順位"]))
        mine = {"picks": pk, "win": [float(win[by_uma[u]]) for u in pk], "rank": [int(rk[u]) for u in pk]}
    exp = (pace_fixed is not None) or bool(manual) or cond_mode in ("良", "稍重", "重", "不良") or bool(scratch_names)
    odds_time = ((yb["odds_meta"] or {}).get("asof") if odds_src.startswith("Yahoo") else "") or info.get("fetched_at", "")

    # 取得状況
    src_cnt = {}
    for nm, s in na["src"].items():
        src_cnt[s] = src_cnt.get(s, 0) + 1
    status = [
        _line(race_src == "netkeiba", f"出馬表: {race_src}" + ("（Yahoo!スポーツナビで照合）" if yb["denma"] else "")),
        _line(n_ok >= 0.8 * len(horses), f"過去成績: {n_ok}/{len(horses)}頭（" + "、".join(f"{k} {v}頭" for k, v in src_cnt.items()) + "）"),
        _line(bool(skills), f"騎手成績: {len(skills)}人分"),
        _line(have_odds, "オッズ: " + (f"{odds_src}" + (f"（{yb['odds_meta'].get('asof', '')}時点"
              f"{'・中間オッズ' if yb['odds_meta'].get('interim') else ''}）" if odds_src.startswith('Yahoo') else "")
              if have_odds else "取得できず（人気は使わずに計算）")),
        _line(cond_src != "データなし（良で計算）", f"馬場状態: {condition}（{cond_src}" +
              (f"・直近の重み付き雨量 {wx['rain_mm']}mm" if wx and cond_src.startswith('天気') else "") + "）"),
        _line(bool(yb["bias"]), "当日の馬場傾向: " + (f"{surface}の直近{yb['bias']['k']}レースから「{yb['bias']['desc']}」"
              if yb["bias"] else f"データ不足（{surface}の結果{yb['bias_races']}レース）のため既定値を使用")),
        _line(bool(training), "調教評価: " + (f"{len(training)}頭分を反映" if training else "取得できず（使用せず）")),
        _line(any(sire_trait(h.get("父")) for h in horses), "血統: " +
              (f"父の傾向を{sum(1 for h in horses if sire_trait(h.get('父')))}頭分に反映" if any(sire_trait(h.get('父')) for h in horses)
               else "登録済みの種牡馬がいない／血統データなし")),
    ]
    if mism:
        problems.append("出馬表の情報源で食い違い: " + "；".join(mism[:6]))

    pace_txt = pace_text(mean_pace) if pace_fixed is None else f"{pace_mode}で計算（指定）"
    snap = {"cancelled": list(cancelled),
            "horses": {h["名前"]: {"騎手": h["騎手"], "馬体重": h.get("馬体重"), "odds": odds_by_name.get(h["名前"])} for h in horses},
            "top3": list(df["馬名"].head(3))}
    changes = diff_snapshot(race_id, snap)

    card = pd.DataFrame(horses)
    card["脚質"] = [style_label(p["front"]) for p in P]
    for k, key in (("スピード", "spd"), ("スタミナ", "sta"), ("瞬発力", "kic")):
        card[k] = [p[key] for p in P]
    card["参照戦数"] = [p["n"] for p in P]
    card = pd.concat([card, factors.round(2)], axis=1)
    card["補正合計"] = factors.sum(axis=1).round(2)
    card = card.sort_values(["馬番", "名前"], na_position="last").reset_index(drop=True)
    cols = ["枠", "馬番", "名前", "騎手", "脚質", "スピード", "スタミナ", "瞬発力", "参照戦数"] + \
           [c for c in ("父", "母父", "調教師") if c in card.columns] + list(factors.columns) + ["補正合計"]

    R = dict(race_id=race_id, info=info, surface=surface, distance=distance, condition=condition, cond_src=cond_src,
             wx=wx, bias=yb["bias"], cancelled=cancelled, problems=problems, pprob=pprob, mism=mism, status=status,
             horses=horses, df=df, show=show, fmt=fmt, kind=kind, conf=conf, axis_lab=axis_lab, hole_lab=hole_lab,
             tickets=tickets, units=units, auto_tickets=auto_t, by_type=by_type, value=value, mean_pace=mean_pace,
             n_front=n_front, pace_txt=pace_txt, have_odds=have_odds, odds_by_name=odds_by_name, n_ok=n_ok,
             card=card, cols=cols, factors=factors, reasons=reasons, changes=changes, pre=pre, market=market,
             front=front, instab=instab, blend=blend, budget=budget,
             validate=(["手入力レース: 枠番・過去成績・騎手成績は使っていません"] if manual else validate(horses, profiles, skills)),
             ticket_mode=ticket_mode, pace_mode=pace_mode, auto_pace=auto_p,
             pay_info=pay_info, odds_time=odds_time, odds_typed=odds_typed, manual=bool(manual), nar=is_nar(race_id),
             bank=bank, bank_m=bank_m, upset=upset, gap=gap, cond_cmp=cond_cmp, trust=trust, mine=mine, exp=exp,
             cond_mode=cond_mode, scratch_names=scratch_names, ranks=ranks)
    R["saved"] = False
    started = bool(race_dt) and datetime.now(JST).replace(tzinfo=None) >= race_dt
    if save and not started and not exp:   # 発走後・ペース指定・手入力の予想は記録しない
        try:
            R["saved"] = bool(save_record(make_record(R)))
        except Exception:
            pass
    R["started"] = started
    R["race_dt"] = race_dt
    return R



# ----------------------------------------------------------
# 外部（Lambda等）から使う入口
# ----------------------------------------------------------
import zlib


def _line(ok, text):
    return f"{'✓' if ok else '△'} {text}"


def save_record(rec):
    """AWS版では保存はハンドラ側（DynamoDB）で行うため、分析本体からは保存しない"""
    return False


def set_params(blend=None, scale=None):
    """自動調整で得た値を反映（None=既定のまま）"""
    global MARKET_BLEND, ABILITY_SCALE
    MARKET_BLEND = 0.2 if blend is None else float(blend)
    ABILITY_SCALE = 1.0 if scale is None else float(scale)


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(float(o)) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return o


def pack_html(h):
    """HTML → 圧縮した文字列（DynamoDBの1項目400KB上限に収めるため）"""
    return base64.b64encode(zlib.compress(h.encode("utf-8"), 9)).decode("ascii")


def unpack_html(s):
    return zlib.decompress(base64.b64decode(s)).decode("utf-8")


PAGE_VER = "2026-11-25-nohz"       # 画面の作りを変えたら更新する。保存済みの古いページは、この値が違えば表示せず再計算する
PAGE_MODES = ["おまかせ(自動)", "全種類", "単勝", "複勝", "枠連", "馬連", "ワイド", "馬単", "3連複", "3連単"]


def _seed_of(race_id):
    d = re.sub(r"\D", "", str(race_id))
    return (int(d[-9:]) if d else 12345) % 2147483647


def analyze_pages(race_id, force=False, manual=None, modes=None):
    """券種ごとの画面（HTML）を作る → (標準のR, {券種: HTML}, 次回比較用のスナップ)
       乱数を固定するので、券種を切り替えても勝率などの数字は変わらない。2回目以降の取得は同じプロセス内のキャッシュを使う"""
    seed = _seed_of(race_id)
    prev = _snap.get(race_id)
    pages, R0 = {}, None
    for k, mode in enumerate(modes or PAGE_MODES):
        if prev is None:
            _snap.pop(race_id, None)
        else:
            _snap[race_id] = prev                      # どの券種の画面でも『前回からの変化』は同じ内容にする
        R = analyze(race_id, force=(force and k == 0), ticket_mode=mode, budget=0, seed=seed, save=False, manual=manual)
        if R0 is None:
            R0 = R
        pages[mode] = result_html(R)
    return R0, pages, _snap.get(race_id)


def page_meta(R):
    d = R["info"].get("date_obj")
    return {"race_id": R["race_id"], "race_date": d.isoformat() if d else None, "name": R["info"]["name"],
            "venue_r": R["info"]["venue_r"], "started": bool(R.get("started")), "modes": PAGE_MODES, "ver": PAGE_VER,
            "manual": bool(R["manual"]), "exp": bool(R.get("exp"))}


def build_record(R):
    """答え合わせ用の保存データ（make_record）をJSON化"""
    return _jsonable(make_record(R))


def result_css():
    """画面全体で使うCSS（サイトの<head>に入れる）"""
    return RESULT_CSS


# ---- 結果の照合（保存先に依存しない部分） ---------------------------------------
def settle_from_html(html):
    """結果ページのHTML → 保存用のresult。結果が未確定ならNone"""
    res = parse_yahoo_result(html)
    if not res:
        return None
    order = {}
    for row in res["rows"]:
        if row["rank"] and row["rank"] not in order:
            order[row["rank"]] = row["uma"]
    try:
        pay = parse_yahoo_payouts(html)
    except Exception:
        pay = {}
    return {"order": {str(k): v for k, v in order.items()},
            "waku": {str(row["uma"]): row["waku"] for row in res["rows"]},
            "pay": {m: {"-".join(map(str, k)): v for k, v in dd.items()} for m, dd in pay.items()},
            "settled_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M")}


def fetch_result(race_id):
    return settle_from_html(fetch_text(f"{YAHOO_BASE}/race/result/{yahoo_id(race_id)}", force=True))
