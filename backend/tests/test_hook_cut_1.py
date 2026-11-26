"""hook-15 失败测甲：日切闭窗后，投递写口不得再把成功尝试写进现行流水。

事故夹具主线：先跑互证 -> 打开日切闭窗硬闸 -> 再灌投递。
基线代码没有闭窗挂点，闭窗后投递照旧成功，现行已投递计数上涨 —— 本测必须红。
救绿服务：投递写口服务 app.services.投递（联拒 + 中文旁路），不是单点校验凑三红。
"""
import os
import tempfile
import uuid
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="hook15_cut1_")
os.environ["数据目录"] = _tmp
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

from app import seed  # noqa: E402
from app.db import 连接  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import 入队, 投递, 互证, 日切闭窗  # noqa: E402


def setup_module():
    seed.初始化库()


@pytest.fixture(autouse=True)
def _闸复位():
    日切闭窗.开窗()
    yield
    日切闭窗.开窗()


def _事件已投递数() -> int:
    conn = 连接()
    try:
        return conn.execute("SELECT COUNT(*) c FROM 事件 WHERE 状态='已投递'").fetchone()["c"]
    finally:
        conn.close()


def _流水成功数() -> int:
    conn = 连接()
    try:
        return conn.execute("SELECT COUNT(*) c FROM 投递尝试 WHERE 状态='成功'").fetchone()["c"]
    finally:
        conn.close()


def _新事件() -> str:
    ev = 入队.建事件({
        "事件类型": "invoice.paid",
        "端点键": "ep-billing",
        "载荷摘要": f"cut1-{uuid.uuid4().hex[:8]}",
    })
    return ev["事件号"]


def test_甲_夹具主线_闭窗后再投现行已投递计数不涨():
    # 1) 先跑互证
    互证.跑一次()
    # 2) 打开日切闭窗硬闸（物化封签 + 指纹）
    seal = 日切闭窗.闭窗()
    assert seal["闭窗"] is True
    assert seal["票号"]
    assert isinstance(seal["水位"], int)
    assert seal["指纹"]

    eid = _新事件()
    n_ev_before = _事件已投递数()
    n_flow_before = _流水成功数()

    # 3) 闭窗后再投：必须被硬闸拒绝（拒收 / 旁路，不得回刷正式流水）
    with pytest.raises(ValueError) as ei:
        投递.投递一次(eid)
    assert "闭窗" in str(ei.value) or "旁路" in str(ei.value)

    # 甲之核心：现行已投递计数仍涨即红
    assert _事件已投递数() == n_ev_before, "闭窗后事件已投递数不得上涨"
    assert _流水成功数() == n_flow_before, "闭窗后现行流水成功数不得上涨"

    conn = 连接()
    try:
        st = conn.execute("SELECT 状态 FROM 事件 WHERE 事件号=?", (eid,)).fetchone()["状态"]
        formal = conn.execute(
            "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=?", (eid,)
        ).fetchone()["c"]
        bypass = conn.execute(
            "SELECT 中文命运 FROM 闭窗旁路 WHERE 事件号=?", (eid,)
        ).fetchall()
    finally:
        conn.close()
    assert st != "已投递"
    assert formal == 0, "闭窗裁决不得写入正式投递尝试集合"
    assert bypass and all(("旁路" in r["中文命运"] or "闭窗" in r["中文命运"]) for r in bypass)


def test_甲_闭窗灯为红_状态口直读():
    日切闭窗.闭窗()
    st = 日切闭窗.状态()
    assert st["闭窗"] is True
    assert st["闭窗灯"] in ("红", "红灯", "closed")
    assert st["灯语"]
    assert "闭窗" in st["灯语"]


def test_甲_HTTP_闭窗后投递口4xx且流水不增_开窗后复通():
    app = create_app()
    c = app.test_client()
    assert c.post("/api/登录", json={"账号": "admin", "密码": "123456"}).status_code == 200

    assert c.post("/api/日切闭窗", json={"闭窗": True}).status_code == 200
    lamp = c.get("/api/日切闭窗").get_json()
    assert lamp["闭窗"] is True

    r = c.post("/api/事件/入队", json={
        "事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": "cut1-http",
    })
    assert r.status_code == 200
    eid = r.get_json()["事件号"]

    before = c.get("/api/投递").get_json()
    assert not [x for x in before if x["事件号"] == eid and x["状态"] == "成功"]

    resp = c.post("/api/投递", json={"事件号": eid})
    assert resp.status_code in (400, 409, 423)
    body = resp.get_json()
    assert "闭窗" in (body.get("错误", "") + str(body))

    after = c.get("/api/投递").get_json()
    assert not [x for x in after if x["事件号"] == eid], "正式流水不得出现该笔闭窗回刷"

    # 解除闭窗后主链复通
    assert c.post("/api/日切闭窗", json={"闭窗": False}).status_code == 200
    ok = c.post("/api/投递", json={"事件号": eid})
    assert ok.status_code == 200
    assert ok.get_json()["状态"] == "成功"

    # 骨签点名的「空桩·日切闭窗」必须已出名册、进已挂硬实现
    stubs = c.get("/api/空桩").get_json()
    assert "日切闭窗硬闸" not in stubs["空桩"]
    assert "日切闭窗硬闸" in stubs["已挂硬实现"]
