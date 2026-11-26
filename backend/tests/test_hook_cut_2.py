"""hook-15 失败测乙：投递互证对账不得把闭窗后成功当本窗已齐。

互证批次必须与流水同票：闭窗当刻封住票号与流水水位，
批次只认票内成功集（rowid <= 封窗水位），闭窗后回刷一律列为票外回刷，
不得抬高本窗已投递数与批次水位。
基线互证服务对全表现状数数，闭窗后成功被算进本窗 —— 本测必须红。
救绿服务：互证服务 app.services.互证（同票水位），与投递写口、闭窗锁分开救。
"""
import os
import tempfile
import uuid
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="hook15_cut2_")
os.environ["数据目录"] = _tmp
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

from app import seed  # noqa: E402
from app.db import 连接  # noqa: E402
from app.services import 入队, 投递, 互证, 日切闭窗, 签名目录  # noqa: E402


def setup_module():
    seed.初始化库()


@pytest.fixture(autouse=True)
def _闸复位():
    日切闭窗.开窗()
    yield
    日切闭窗.开窗()


def _插一笔票外成功回刷(eid: str) -> int:
    """绕过投递服务，模拟闭窗后回刷直接写正式流水（事故里被抬高的根源）。"""
    conn = 连接()
    try:
        import uuid as _u
        from app.services.时间 import 此刻
        t = 此刻()
        cur = conn.execute(
            "INSERT INTO 投递尝试(尝试号,事件号,端点键,签名摘要,状态,响应码,说明,投递时) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"TRY-rogue-{_u.uuid4().hex[:8]}", eid, "ep-billing", "rogue",
             "成功", 200, "闭窗后回刷", t),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_乙_夹具主线_批次与流水同票_灌投递不抬窗():
    互证.跑一次()
    seal = 日切闭窗.闭窗()
    ticket, w, n_ok = seal["票号"], seal["水位"], seal["成功数"]

    # 闭窗后灌投递：写口应联拒，全部不进正式流水
    for _ in range(3):
        eid = 入队.建事件({
            "事件类型": "invoice.paid", "端点键": "ep-billing",
            "载荷摘要": f"cut2-{uuid.uuid4().hex[:8]}",
        })["事件号"]
        with pytest.raises(ValueError):
            投递.投递一次(eid)

    rep = 互证.跑一次()
    assert rep["闭窗票号"] == ticket
    assert rep["流水水位"] == w
    assert rep["已投递数"] == n_ok, "闭窗后灌投递不得抬高互证本窗已投递数"

    rows = 签名目录.互证列表()
    latest = rows[0]
    assert latest["闭窗票号"] == ticket
    assert latest["流水水位"] == w


def test_乙_票外回刷不进本窗_且对账点名票外数():
    seal = 日切闭窗.闭窗()
    ticket, w, n_ok = seal["票号"], seal["水位"], seal["成功数"]

    eid = 入队.建事件({
        "事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": "cut2-rogue",
    })["事件号"]
    rogue_rowid = _插一笔票外成功回刷(eid)
    assert rogue_rowid > w

    rep = 互证.跑一次()
    assert rep["闭窗票号"] == ticket, "闭窗后批次仍须盖同一张票"
    assert rep["流水水位"] == w, "批次水位必须钉在封窗水位"
    assert rep["已投递数"] == n_ok, "票外回刷成功不得算本窗已齐"
    assert int(rep.get("票外回刷数") or 0) >= 1, "对账须点出票外回刷"
    assert rep.get("是否对齐") is False or rep.get("是否全部对齐") is False, \
        "存在票外回刷时不得判对齐"

    conn = 连接()
    try:
        row = conn.execute(
            "SELECT * FROM 互证批次 WHERE 闭窗票号=? ORDER BY 批次号 DESC LIMIT 1",
            (ticket,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["流水水位"] == w
    assert int(row["票外回刷数"]) >= 1
