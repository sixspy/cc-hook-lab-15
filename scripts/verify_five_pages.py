"""hook-15 收工五页核对：逐页 HTTP 打开 + 页/接口/库同源比对。

五页：投递互证列表、端点名册详情、投递流水卡片、签名规目录、事件队列策略。
另造主链数据（入队→投递→互证→闭窗→旁路），保证不是空页核对。
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, tempfile, time, urllib.request, urllib.parse, urllib.error, http.cookiejar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PORT = int(os.environ.get("HOOK15_CHECK_PORT", "8532"))
BASE = f"http://127.0.0.1:{PORT}"
data_dir = tempfile.mkdtemp(prefix="hook15_pages_")

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def http(method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + urllib.parse.quote(path, safe="/?=&"),
                                data=data, method=method,
                                headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with opener.open(req, timeout=15) as r:
            txt = r.read().decode()
            return r.status, (txt if raw else json.loads(txt))
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        return e.code, (txt if raw else _j(txt))

def _j(t):
    try: return json.loads(t)
    except Exception: return {"_text": t}

def db():
    c = sqlite3.connect(Path(data_dir) / "投递台.db")
    c.row_factory = sqlite3.Row
    return c

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" — " + extra) if extra else ""))
    if not cond: fails.append(name)

env = dict(os.environ, 数据目录=data_dir, 夹具目录=str(ROOT / "fixtures"),
           端口=str(PORT), PYTHONPATH=str(BACKEND))
srv = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=str(BACKEND), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/login", timeout=2).read(); break
        except Exception: time.sleep(0.5)
    else:
        print("server boot failed"); sys.exit(2)

    http("POST", "/api/登录", {"账号": "admin", "密码": "123456"})

    # —— 造主链数据 ——
    _, ev = http("POST", "/api/事件/入队", {"事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": "page-check-1"})
    eid = ev["事件号"]
    st, rep = http("POST", "/api/投递", {"事件号": eid}); assert st == 200, rep
    http("POST", "/api/互证/跑", {})
    http("POST", "/api/日切闭窗", {"闭窗": True})
    _, ev2 = http("POST", "/api/事件/入队", {"事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": "page-check-2"})
    st, _ = http("POST", "/api/投递", {"事件号": ev2["事件号"]})
    assert st == 400
    http("POST", "/api/互证/跑", {})  # 闭窗后再互证，造同票批次

    def page(file, markers):
        st, html = http("GET", "/" + file, raw=True)
        check(f"打开页 {file} HTTP200", st == 200, f"status={st}")
        for m in markers:
            check(f"  {file} 含「{m}」", m in html)
        return html

    # 1) 投递互证列表
    h = page("投递互证.html", ["闭窗灯", "批次水位", "中文旁路", "/api/日切闭窗", "/api/互证", "闭窗票号"])
    _, api_rows = http("GET", "/api/互证")
    c = db()
    db_rows = [dict(r) for r in c.execute("SELECT * FROM 互证批次 ORDER BY 批次号 DESC LIMIT 50")]
    check("互证列表 API 行数=库行数", len(api_rows) == len(db_rows), f"api={len(api_rows)} db={len(db_rows)}")
    if api_rows and db_rows:
        a, d = api_rows[0], db_rows[0]
        check("最新批次 已投递数/票号/水位 同源",
              a["已投递数"] == d["已投递数"] and a["闭窗票号"] == d["闭窗票号"]
              and a["流水水位"] == d["流水水位"] and a["票外回刷数"] == d["票外回刷数"],
              f"票={d['闭窗票号']} 水位={d['流水水位']}")
    _, gate = http("GET", "/api/日切闭窗")
    grow = c.execute("SELECT * FROM 日切闭窗闸 WHERE 闸号=1").fetchone()
    bcount = c.execute("SELECT COUNT(*) n FROM 闭窗旁路").fetchone()["n"]
    blatest = c.execute("SELECT 中文命运 FROM 闭窗旁路 ORDER BY 时刻 DESC, rowid DESC LIMIT 1").fetchone()
    check("闭窗灯状态口=库闸行", gate["闭窗"] == bool(grow["闭窗"]) and gate["票号"] == grow["票号"]
          and gate["水位"] == grow["水位"], f"灯={gate['闭窗灯']} 票={grow['票号']}")
    if api_rows:
        b0 = api_rows[0]
        check("最新互证批次=闭窗同票同水位",
              b0["闭窗票号"] == gate["票号"] and b0["流水水位"] == gate["水位"]
              and b0["闭窗票号"].startswith("CUT-"),
              f"批次票={b0['闭窗票号']} 闸票={gate['票号']} 水位={b0['流水水位']}/{gate['水位']}")
    check("旁路 API 与库同数且中文命运同源",
          len(gate["旁路"]) == bcount and bcount >= 1
          and gate["旁路"][0]["中文命运"] == blatest["中文命运"]
          and "旁路" in blatest["中文命运"], f"旁路 {bcount} 条")
    c.close()

    # 2) 端点名册详情
    page("端点名册.html", ["端点名册详情", "/api/端点/", "中文命运", "详情"])
    _, eps = http("GET", "/api/端点")
    c = db()
    db_eps = [dict(r) for r in c.execute("SELECT * FROM 投递端点 ORDER BY 端点键")]
    check("端点名册 API=库", [(e["端点键"], e["启用"]) for e in eps]
          == [(e["端点键"], e["启用"]) for e in db_eps], f"{len(eps)} 端点")
    key = "ep-billing"
    _, detail = http("GET", f"/api/端点/{key}")
    drow = c.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (key,)).fetchone()
    n_ok = c.execute("SELECT COUNT(*) n FROM 投递尝试 WHERE 端点键=? AND 状态='成功'", (key,)).fetchone()["n"]
    check("端点详情字段=库行", detail["名称"] == drow["名称"] and detail["回调URL"] == drow["回调URL"]
          and detail["密钥版本"] == drow["密钥版本"] and detail["成功数"] == n_ok
          and "命运" in detail["中文命运"])
    check("端点详情近期流水=库查", len(detail["近期投递"]) == c.execute(
        "SELECT COUNT(*) n FROM (SELECT 1 FROM 投递尝试 WHERE 端点键=? ORDER BY 投递时 DESC, rowid DESC LIMIT 10)",
        (key,)).fetchone()["n"])
    c.close()

    # 3) 投递流水卡片
    page("投递流水.html", ["闭窗旁路卡片", "中文命运", "/api/投递", "/api/日切闭窗"])
    _, flows = http("GET", "/api/投递")
    c = db()
    db_n = c.execute("SELECT COUNT(*) n FROM (SELECT 1 FROM 投递尝试 ORDER BY 投递时 DESC, rowid DESC LIMIT 200)").fetchone()["n"]
    first = c.execute("SELECT * FROM 投递尝试 ORDER BY 投递时 DESC, rowid DESC LIMIT 1").fetchone()
    check("投递流水 API 条数=库(LIMIT200)", len(flows) == db_n, f"{len(flows)}")
    check("流水首行=库最新行", flows[0]["尝试号"] == first["尝试号"] and flows[0]["状态"] == first["状态"])
    check("闭窗后被拒事件无正式流水", all(f["事件号"] != ev2["事件号"] for f in flows))
    c.close()

    # 4) 签名规目录
    page("签名规.html", ["签名规目录", "规则目录", "日切闭窗", "/api/签名规"])
    _, rules = http("GET", "/api/签名规")
    c = db()
    db_rules = {r["键"]: r["值"] for r in c.execute("SELECT 键,值 FROM 签名规")}
    check("签名规 API 逐键=库", all(rules.get(k) == v for k, v in db_rules.items()) and len(rules) == len(db_rules),
          f"{len(db_rules)} 键")
    check("库内日切闭窗印记=开（目录拨闸同源）", db_rules.get("日切闭窗") == "开")
    c.close()

    # 5) 事件队列策略
    page("事件队列.html", ["事件队列策略", "中文命运", "/api/事件", "批量重试"])
    _, events = http("GET", "/api/事件")
    c = db()
    db_ev = [dict(r) for r in c.execute("SELECT * FROM 事件 ORDER BY 入队时 DESC")]
    check("事件列表 API=库（顺序一致）", [e["事件号"] for e in events] == [e["事件号"] for e in db_ev],
          f"{len(events)} 事件")
    check("闭窗后灌的事件仍停在待投", next(e for e in events if e["事件号"] == ev2["事件号"])["状态"] == "待投")
    c.close()

    # 首页投递台命运 + 死信页顺带打开
    page("index.html", ["闭窗灯", "中文命运", "/api/概览"])
    page("死信匣.html", ["中文命运", "闭窗"])

    print("\n结果：", "全部同源核对通过 ✅" if not fails else f"{len(fails)} 项失败：{fails}")
    sys.exit(1 if fails else 0)
finally:
    srv.terminate()
    try: srv.communicate(timeout=10)
    except subprocess.TimeoutExpired: srv.kill()
