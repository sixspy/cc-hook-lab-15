"""hook-15 收工争用核验：另起双会话并发打同一把日切闭窗锁。

做两件事：
1) 跨进程同锁探针：起两个 OS 子进程同时抢 app.services.日切闭窗.闸锁，
   打印持锁区间，证明 flock 跨进程不重叠；
2) 起真实 Flask 服务，开两个独立登录会话（双 cookie 罐），
   - 同事件并发投递：至多一笔正式成功；
   - 闭窗当刻双路投递 + 闭窗三方争用多轮：票水位后无正式成功；
   - 闭窗后两会话再投：双双被硬闸联拒，闭窗灯红、旁路增长、计数不涨。

只用标准库发 HTTP。用法：
  .venv/bin/python scripts/dual_session_race.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PORT = int(os.environ.get("HOOK15_PORT", "8531"))
BASE = f"http://127.0.0.1:{PORT}"


# ------------------------------------------------------------ 跨进程锁探针

def lock_worker(data_dir: str) -> None:
    sys.path.insert(0, str(BACKEND))
    os.environ["数据目录"] = data_dir
    from app.services import 日切闭窗
    with 日切闭窗.闸锁():
        print(f"ACQ pid={os.getpid()} wall={time.time():.6f}", flush=True)
        time.sleep(1.0)
        print(f"REL pid={os.getpid()} wall={time.time():.6f}", flush=True)


def cross_process_probe(data_dir: str) -> bool:
    print("== 跨进程同锁探针：两个子进程同时抢 闸锁 ==")
    env = dict(os.environ, 数据目录=data_dir, PYTHONPATH=str(BACKEND))
    procs = [
        subprocess.Popen([sys.executable, __file__, "--lock-worker", data_dir],
                         env=env, stdout=subprocess.PIPE, text=True),
        subprocess.Popen([sys.executable, __file__, "--lock-worker", data_dir],
                         env=env, stdout=subprocess.PIPE, text=True),
    ]
    lines = []
    for p in procs:
        out, _ = p.communicate(timeout=30)
        lines.extend(out.strip().splitlines())
        print(out.strip())
    intervals = {}
    for l in lines:
        kind = "ACQ" if l.startswith("ACQ") else ("REL" if l.startswith("REL") else None)
        if not kind:
            continue
        pid = int(l.split("pid=")[1].split()[0])
        wall = float(l.split("wall=")[1])
        intervals.setdefault(pid, {})[kind] = wall
    iv = sorted(intervals.values(), key=lambda x: x["ACQ"])
    ok = len(iv) == 2 and all("REL" in x for x in iv) and iv[1]["ACQ"] >= iv[0]["REL"] - 1e-3
    print("区间：", [(round(x["ACQ"], 3), round(x["REL"], 3)) for x in iv])
    print(("跨进程持锁区间不重叠 —— 同锁成立" if ok else "!! 两进程持锁重叠，锁失效"))
    return ok


# ------------------------------------------------------------ HTTP 双会话

class 会话:
    def __init__(self, name: str):
        self.name = name
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        安全路径 = urllib.parse.quote(path, safe="/?=&")
        req = urllib.request.Request(BASE + 安全路径, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=15) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}

    def login(self):
        st, b = self.call("POST", "/api/登录", {"账号": "admin", "密码": "123456"})
        assert st == 200 and b.get("ok"), (self.name, st, b)


def 新事件(s: 会话, 摘要: str) -> str:
    st, b = s.call("POST", "/api/事件/入队",
                   {"事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": 摘要})
    assert st == 200, (st, b)
    return b["事件号"]


def db_counts(data_dir: str):
    c = sqlite3.connect(Path(data_dir) / "投递台.db")
    try:
        成功 = c.execute("SELECT COUNT(*) FROM 投递尝试 WHERE 状态='成功'").fetchone()[0]
        总尝试 = c.execute("SELECT COUNT(*) FROM 投递尝试").fetchone()[0]
        旁路 = c.execute("SELECT COUNT(*) FROM 闭窗旁路").fetchone()[0]
        return 成功, 总尝试, 旁路
    finally:
        c.close()


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--lock-worker":
        lock_worker(sys.argv[2])
        return 0

    data_dir = tempfile.mkdtemp(prefix="hook15_dual_")
    lock_ok = cross_process_probe(data_dir)

    env = dict(os.environ, 数据目录=data_dir, 夹具目录=str(ROOT / "fixtures"),
               端口=str(PORT), PYTHONPATH=str(BACKEND))
    server = subprocess.Popen(
        [sys.executable, "-m", "app.main"], cwd=str(BACKEND), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        # 等端口起来
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(BASE + "/login", timeout=2).read()
                break
            except Exception:
                time.sleep(0.5)
        else:
            print("服务未启动"); print(server.stdout.read()[-2000:]); return 2

        甲, 乙 = 会话("甲"), 会话("乙")
        甲.login(); 乙.login()
        print("== 两个独立登录会话已建立 ==")

        # 1) 同事件双会话并发
        eid = 新事件(甲, "dual-same-event")
        bar = threading.Barrier(2)
        res = {}

        def deliver(s: 会话, key: str):
            bar.wait()
            res[key] = s.call("POST", "/api/投递", {"事件号": eid})

        t1 = threading.Thread(target=deliver, args=(甲, "甲"))
        t2 = threading.Thread(target=deliver, args=(乙, "乙"))
        t1.start(); t2.start(); t1.join(); t2.join()
        print("同事件双会话结果：", res["甲"][0], res["甲"][1].get("状态") or res["甲"][1].get("错误"),
              "|", res["乙"][0], res["乙"][1].get("状态") or res["乙"][1].get("错误"))
        codes = sorted([res["甲"][0], res["乙"][0]])
        assert codes == [200, 400], f"应为一成一拒，实际 {codes}"

        # 2) 闭窗当刻：两路不同事件 + 第三方闭窗，5 轮
        for rnd in range(5):
            甲.call("POST", "/api/日切闭窗", {"闭窗": False})
            e1 = 新事件(甲, f"race-{rnd}-1")
            e2 = 新事件(乙, f"race-{rnd}-2")
            b3 = threading.Barrier(3)
            box = {}

            def job(s, e, k):
                b3.wait()
                box[k] = s.call("POST", "/api/投递", {"事件号": e})

            def cut():
                b3.wait()
                box["cut"] = 甲.call("POST", "/api/日切闭窗", {"闭窗": True})

            ts = [threading.Thread(target=job, args=(甲, e1, "a")),
                  threading.Thread(target=job, args=(乙, e2, "b")),
                  threading.Thread(target=cut)]
            [t.start() for t in ts]; [t.join() for t in ts]
            seal = box["cut"][1]
            w = seal["水位"]
            c = sqlite3.connect(Path(data_dir) / "投递台.db")
            try:
                over = c.execute(
                    "SELECT COUNT(*) FROM 投递尝试 WHERE 事件号 IN (?,?) AND rowid>?",
                    (e1, e2, w)).fetchone()[0]
                dbl = c.execute(
                    "SELECT 事件号, COUNT(*) n FROM 投递尝试 WHERE 事件号 IN (?,?) "
                    "AND 状态='成功' GROUP BY 事件号 HAVING n>1", (e1, e2)).fetchall()
            finally:
                c.close()
            assert over == 0 and not dbl, (rnd, over, dbl)
        print("闭窗当刻 5 轮三方争用：票水位后无正式流水、无同事件双成功 —— 通过")

        # 3) 闭窗后双会话再投，必须双拒，计数不涨
        st, gate = 甲.call("GET", "/api/日切闭窗")
        assert gate["闭窗"] and gate["闭窗灯"] == "红"
        ok0, all0, bp0 = db_counts(data_dir)
        e3 = 新事件(甲, "after-cut-1")
        e4 = 新事件(乙, "after-cut-2")
        r3 = 甲.call("POST", "/api/投递", {"事件号": e3})
        r4 = 乙.call("POST", "/api/投递", {"事件号": e4})
        ok1, all1, bp1 = db_counts(data_dir)
        print("闭窗后再投：甲", r3[0], "乙", r4[0])
        print("  甲语：", r3[1].get("错误", "")[:80])
        assert r3[0] == 400 and r4[0] == 400
        assert ok1 == ok0 and all1 == all0, "正式流水不得增加"
        assert bp1 >= bp0 + 2, "两笔都应进闭窗旁路"
        st, gate = 乙.call("GET", "/api/日切闭窗")
        assert len(gate["旁路"]) >= 2 and "旁路" in gate["旁路"][0]["中文命运"]
        print(f"闭窗灯={gate['闭窗灯']} 水位={gate['水位']} 旁路新增={bp1-bp0} 正式成功数稳定={ok1}")

        # 互证同票
        st, rep = 甲.call("POST", "/api/互证/跑", {})
        assert rep["闭窗票号"] == gate["票号"] and rep["流水水位"] == gate["水位"]
        print("互证同票：", rep["闭窗票号"], "批次水位", rep["流水水位"],
              "已投递", rep["已投递数"], "票外", rep["票外回刷数"], "|", rep["说明"][:40])

        print("DUAL-SESSION-RACE: ALL GREEN；跨进程锁探针=", lock_ok)
        return 0 if lock_ok else 1
    finally:
        server.terminate()
        try:
            out = server.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            server.kill()
        # 不打印常规服务日志，除非排查需要

if __name__ == "__main__":
    raise SystemExit(main())
