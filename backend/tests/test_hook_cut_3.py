"""hook-15 争用失败测：闭窗当刻两路投递，读开关与写尝试必须同成败。

无锁时两路会同时读到“开窗可投”，闭窗后双双回刷成功（基线夹具已复现双成功）。
本文件只救争用：闭窗闸锁服务 app.services.日切闭窗（flock 同锁，跨线程/跨进程），
不靠投递口或互证里的单点校验凑红。
"""
import os
import tempfile
import threading
import uuid
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="hook15_cut3_")
os.environ["数据目录"] = _tmp
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

from app import seed  # noqa: E402
from app.db import 连接  # noqa: E402
from app.services import 入队, 投递, 日切闭窗  # noqa: E402


def setup_module():
    seed.初始化库()


@pytest.fixture(autouse=True)
def _闸复位():
    日切闭窗.开窗()
    yield
    日切闭窗.开窗()


def test_丙_闸锁本身互斥_持锁时他路抢不到同一把锁():
    with 日切闭窗.闸锁():
        assert 日切闭窗.抢闸() is None, "主路持锁期间，旁路非阻塞抢闸必须失败"
    fd = 日切闭窗.抢闸()
    assert fd is not None, "放锁后必须能抢到同一把锁"
    fd.close()


def test_丙_同一事件两路并发_至多一笔正式成功():
    eid = 入队.建事件({
        "事件类型": "invoice.paid", "端点键": "ep-billing",
        "载荷摘要": f"cut3-same-{uuid.uuid4().hex[:8]}",
    })["事件号"]
    barrier = threading.Barrier(2)
    outs = []

    def job():
        barrier.wait(timeout=10)
        try:
            outs.append(投递.投递一次(eid))
        except Exception as e:  # noqa: BLE001
            outs.append({"错误": str(e)})

    ts = [threading.Thread(target=job) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(timeout=15) for t in ts]
    assert all(not t.is_alive() for t in ts), "投递线程不得挂死"

    conn = 连接()
    try:
        n_ok = conn.execute(
            "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid,)
        ).fetchone()["c"]
        n_all = conn.execute(
            "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=?", (eid,)
        ).fetchone()["c"]
    finally:
        conn.close()
    assert n_ok == 1, f"同事件双请求不得双成功，实际成功 {n_ok}"
    assert n_all == 1, f"锁内须完成状态复核，不得写两笔尝试，实际 {n_all}"
    assert sum(1 for o in outs if o.get("状态") == "成功") == 1


def test_丙_闭窗当刻两路投递_无越票成功_票水位封住全部成功():
    for rnd in range(12):
        日切闭窗.开窗()
        eids = [
            入队.建事件({
                "事件类型": "invoice.paid", "端点键": "ep-billing",
                "载荷摘要": f"cut3-r{rnd}-{i}-{uuid.uuid4().hex[:6]}",
            })["事件号"]
            for i in range(2)
        ]
        barrier = threading.Barrier(3)
        box = {"outs": [], "seal": None}

        def deliver(eid):
            barrier.wait(timeout=10)
            try:
                box["outs"].append(("ok", eid, 投递.投递一次(eid)))
            except Exception as e:  # noqa: BLE001
                box["outs"].append(("reject", eid, str(e)))

        def close_job():
            barrier.wait(timeout=10)
            box["seal"] = 日切闭窗.闭窗()

        ts = [threading.Thread(target=deliver, args=(e,)) for e in eids]
        tc = threading.Thread(target=close_job)
        for t in ts + [tc]:
            t.start()
        for t in ts + [tc]:
            t.join(timeout=20)
        assert all(not t.is_alive() for t in ts + [tc]), "争用线程不得挂死"

        seal = box["seal"]
        assert seal and seal["票号"]
        w = seal["水位"]

        conn = 连接()
        try:
            for eid in eids:
                n_ok = conn.execute(
                    "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid,)
                ).fetchone()["c"]
                assert n_ok <= 1, "同事件不得双成功"
                # 票水位之后不得有本事件的正式流水行（闭窗后写口只能写旁路）
                late = conn.execute(
                    "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=? AND rowid>?",
                    (eid, w),
                ).fetchone()["c"]
                assert late == 0, "封窗水位之后不得再落正式投递流水"
            # 全表不变量：成功集恰为票内集
            over = conn.execute(
                "SELECT COUNT(*) c FROM 投递尝试 WHERE 状态='成功' AND rowid>?", (w,)
            ).fetchone()["c"]
        finally:
            conn.close()
        assert over == 0, "闭窗票外不得有任何正式成功尝试"

    # 闭窗返回后再来的投递一律联拒
    eid = 入队.建事件({
        "事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": "cut3-after",
    })["事件号"]
    with pytest.raises(ValueError):
        投递.投递一次(eid)
