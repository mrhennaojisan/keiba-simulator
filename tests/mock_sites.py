"""外部サイト（netkeiba / Yahoo!スポーツナビ / Open-Meteo）の擬似レスポンス。実サイトには接続しない。"""
from datetime import datetime, timedelta, timezone

import numpy as np

JST = timezone(timedelta(hours=9))
H = 18
NAMES = [f"テスト馬{i + 1}" for i in range(H)]
JK = ["岩田望", "西村淳", "津村", "田山", "原", "川田", "坂井", "松山", "武豊", "ルメール", "岩田康", "田辺", "丹内", "戸崎圭", "酒井", "池添", "石川", "北村友"]
FULL = ["岩田望来", "西村淳也", "津村明秀", "田山旺佑", "原優介", "川田将雅", "坂井瑠星", "松山弘平", "武豊", "Ｃ．ルメール", "岩田康誠", "田辺裕信", "丹内祐次", "戸崎圭太", "酒井学", "池添謙一", "石川裕紀人", "北村友一"]
RACE_DAY = (datetime.now(JST) + timedelta(days=3)).date()
RACE_ID = "202608040911"


def waku(u, n=H):
    from keiba_core import waku_from_umaban
    return waku_from_umaban(u, n)


def shutuba():
    rows = ""
    for i in range(H):
        k = waku(i + 1)
        rows += (f'<tr class="HorseList"><td class="Waku{k}"><span>{k}</span></td><td class="Umaban{k}">{i + 1}</td><td>--</td>'
                 f'<td class="HorseInfo"><a href="https://db.netkeiba.com/horse/2023{i:06d}" title="{NAMES[i]}">{NAMES[i]}</a></td>'
                 f'<td class="Barei">牡3</td><td>57.0</td><td class="Jockey"><a href="/jockey/result/recent/{1000 + i:05d}/">{JK[i]}</a></td>'
                 f'<td class="Weight">480({(i % 5 - 2) * 4:+d})</td></tr>')
    return (f'<html><head><title>菊花賞(G1) 出馬表 | {RACE_DAY.year}年{RACE_DAY.month}月{RACE_DAY.day}日 京都11R</title></head><body>'
            f'<div class="RaceName">菊花賞</div><div class="RaceData01">15:40発走 / 芝3000m (右 外 A)</div>'
            f'<table class="Shutuba_Table">{rows}</table></body></html>')


def past(seed=5):
    rng = np.random.default_rng(seed)

    def cell(k, i):
        r = int(rng.integers(1, 12)); g = ["", "1", "2", "3"][int(rng.integers(0, 4))]
        gi = f'<span class="Icon_GradeType Icon_GradeType{g}"></span>' if g else ""
        pas = "-".join(str(int(rng.integers(1, 16))) for _ in range(3)); d = f"2026.{10 - k:02d}.{4 + k:02d}"
        return (f'<td class="Past"><div class="Data01"><span>{d} {["京都", "東京", "阪神", "中山"][k % 4]}</span><span class="Num">{r}</span></div>'
                f'<div class="Data02"><a>レース{k}</a>{gi}</div><div class="Data05">芝{[2000, 2200, 2400, 3000, 1800][k % 5]} <span>2:14.5</span> {["良", "稍", "重"][k % 3]}</div>'
                f'<div class="Data03"><span>16頭 {k + 1}番 {k + 2}人 {JK[i][:2]} 56.0</span></div><div class="Data06">{pas} ({33 + rng.random() * 3:.1f}) 480(+2)</div></td>')

    rows = "".join(f'<tr class="HorseList"><td class="Waku1">1</td><td class="Horse_Info"><a href="https://db.netkeiba.com/horse/2023{i:06d}">{NAMES[i]}</a></td>'
                   + "".join(cell(k, i) for k in range(5)) + '</tr>' for i in range(H))
    return f'<table class="Shutuba_Past5_Table">{rows}</table>'


def leading():
    rows = "".join(f'<tr><td>{i}</td><td><a href="/jockey/{1000 + i:05d}/">{FULL[i]}</a></td><td>{200 + i * 10}</td><td>{0.25 - i * 0.01:.3f}</td><td>{0.55 - i * 0.015:.3f}</td></tr>' for i in range(H))
    return f'<table><tr><th>順位</th><th>騎手</th><th>出走回数</th><th>勝率</th><th>複勝率</th></tr>{rows}</table>'


def ydenma():
    rows = "".join(f'<tr><td>{waku(i + 1)}</td><td>{i + 1}</td><td><a href="/keiba/directory/horse/2023{i:06d}/">{NAMES[i]}</a></td><td>48{i % 10}kg(+2)</td>'
                   f'<td>父{i}</td><td>母{i}</td><td>母父{i}</td><td>(栗東)調教師{i}</td></tr>' for i in range(H))
    return f'<html><head><title>2026年菊花賞 出馬表 - スポーツナビ</title></head><body>{RACE_DAY.year}年{RACE_DAY.month}月{RACE_DAY.day}日 15:40発走 芝 3000m<table>{rows}</table></body></html>'


def yodds():
    rows = "".join(f'<tr><td>{waku(i + 1)}</td><td>{i + 1}</td><td>{NAMES[i]}</td><td>{2 + i * 2.7:.1f}</td><td>{1.2 + i * 0.6:.1f}-{1.6 + i * 0.8:.1f}</td></tr>' for i in range(H))
    return f'<html><body>2026/10/25 12:00 現在<table>{rows}</table></body></html>'


def yresult(seed=1):
    rng = np.random.default_rng(seed)
    order = list(rng.permutation(16) + 1)
    rows = "".join(f'<tr><td>{rk}</td><td>{min(8, (u + 1) // 2)}</td><td>{u}</td><td>馬{u}</td><td>{max(1, u - 2)}-{max(1, u - 2)}-{u} / 34.{u % 10}</td></tr>' for rk, u in enumerate(order, 1))
    pay = ("払戻 単勝 3 520円 1人気 複勝 3 150円 5 230円 8 310円 枠連 2-3 880円 馬連 3-5 2,100円 ワイド 3-5 640円 3-8 900円 5-8 1,230円 "
           "馬単 3→5 4,200円 3連複 3-5-8 5,900円 3連単 3→5→8 33,000円")
    return f'<html><body>芝 2000m 良 / 晴<table>{rows}</table>{pay}</body></html>'


def oikiri():
    rows = "".join(f'<tr><td>{i + 1}</td><td><a href="https://db.netkeiba.com/horse/2023{i:06d}" title="{NAMES[i]}">{NAMES[i]}</a></td><td>{["A", "B", "C"][i % 3]}</td>'
                   f'<td>{"動き軽快で順調に仕上がっている" if i % 4 == 0 else "もう少し気配がほしい まだ余裕残し"}</td></tr>' for i in range(H))
    return f'<table>{rows}</table>'


def meteo():
    t0 = datetime.now(JST).replace(tzinfo=None, minute=0, second=0, microsecond=0) - timedelta(days=2)
    times = [(t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(24 * 9)]
    return {"hourly": {"time": times, "precipitation": [0.2] * len(times), "precipitation_probability": [30] * len(times)}}


def fake_fetch_text(url, force=False, ttl=0):
    if "shutuba_past" in url: return past()
    if "shutuba.html" in url: return shutuba()
    if "jockey_leading" in url: return leading()
    if "oikiri" in url: return oikiri()
    if "yahoo" in url:
        if "/race/denma/" in url: return ydenma()
        if "/race/odds/" in url: return yodds()
        if "/race/result/" in url: return yresult()
    raise RuntimeError("403 (blocked)")


def races():
    return [{"date": RACE_DAY, "time": "15:40", "venue": "京都", "rno": 11, "race_id": RACE_ID, "name": "菊花賞", "grade": "G1", "course": "芝3000m"},
            {"date": RACE_DAY, "time": "10:00", "venue": "京都", "rno": 2, "race_id": "202608040902", "name": "3歳未勝利", "grade": "", "course": "ダ1800m"}]


def ovs_manual(core):
    """JRA海外競馬ページから作られる『手入力と同じ形』の擬似データ"""
    lines = [f"{i + 1},ホース{chr(0x30a2 + i)}ーノ,,{3.0 + i * 2.5}" for i in range(8)]
    return {"name": "凱旋門賞", "venue": "パリロンシャン", "surface": "芝", "distance": 2400, "condition": "良",
            "text": "\n".join(lines), "date_text": f"{RACE_DAY.year}年{RACE_DAY.month}月{RACE_DAY.day}日 23:05発走（日本時間）",
            "date_obj": RACE_DAY, "post_time": "23:05", "ovs": True, "url": "https://www.jra.go.jp/keiba/overseas/race/2026arc/morning.html"}


def races_with_overseas():
    return races() + [{"race_id": "ovs:2026arc", "date": RACE_DAY, "grade": "", "time": "23:05", "rno": 0, "venue": "パリロンシャン",
                       "name": "凱旋門賞", "course": "芝2400m", "region": "海外"}]
