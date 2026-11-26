from __future__ import annotations
from app.db import 连接
from app.services import 校验


def 签名规() -> dict:
    conn = 连接()
    try:
        rows = conn.execute("SELECT 键,值 FROM 签名规").fetchall()
        return {r["键"]: r["值"] for r in rows}
    finally:
        conn.close()


def 改签名规(body: dict) -> dict:
    # hook-15：日切闭窗键先于本连接写事务处理，避免跨连接等写锁自锁
    if "日切闭窗" in body:
        from app.services import 日切闭窗
        值 = 校验.非空("值", body.get("日切闭窗"))
        if str(值) in ("开", "1", "true", "True", "是", "闭窗", "closed", "on", "打开"):
            日切闭窗.闭窗()
        else:
            日切闭窗.开窗()
    conn = 连接()
    try:
        for k, v in body.items():
            键 = 校验.非空("键", k)
            值 = 校验.非空("值", v)
            if 键 == "日切闭窗":
                continue  # 已在开事务前推动硬闸并回写目录印记
            conn.execute(
                "INSERT INTO 签名规(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (键, 值),
            )
        conn.commit()
        return 签名规()
    finally:
        conn.close()


def 概览() -> dict:
    conn = 连接()
    try:
        def c(sql):
            return conn.execute(sql).fetchone()[0]
        from app.services import 日切闭窗
        闸 = 日切闭窗.状态(旁路条数=6)
        return {
            "端点数": c("SELECT COUNT(*) FROM 投递端点"),
            "启用端点": c("SELECT COUNT(*) FROM 投递端点 WHERE 启用=1"),
            "事件总数": c("SELECT COUNT(*) FROM 事件"),
            "待投": c("SELECT COUNT(*) FROM 事件 WHERE 状态='待投'"),
            "投递中": c("SELECT COUNT(*) FROM 事件 WHERE 状态='投递中'"),
            "已投递": c("SELECT COUNT(*) FROM 事件 WHERE 状态='已投递'"),
            "死信": c("SELECT COUNT(*) FROM 事件 WHERE 状态='死信'"),
            "尝试数": c("SELECT COUNT(*) FROM 投递尝试"),
            "死信匣": c("SELECT COUNT(*) FROM 死信 WHERE 状态='待处置'"),
            "签名规": 签名规(),
            # hook-15 闭窗灯直读
            "闭窗": 闸["闭窗"],
            "闭窗灯": 闸["闭窗灯"],
            "闭窗灯语": 闸["灯语"],
            "闭窗票号": 闸["票号"],
            "批次水位": 闸["水位"],
            "闭窗闸": 闸,
        }
    finally:
        conn.close()


def 事件列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 事件 ORDER BY 入队时 DESC").fetchall()]
    finally:
        conn.close()


def 事件一条(事件号: str) -> dict | None:
    conn = 连接()
    try:
        row = conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (事件号,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["尝试"] = [dict(x) for x in conn.execute(
            "SELECT * FROM 投递尝试 WHERE 事件号=? ORDER BY 投递时 DESC", (事件号,)
        ).fetchall()]
        dl = conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
        d["死信"] = dict(dl) if dl else None
        return d
    finally:
        conn.close()


def 投递列表(端点键: str | None = None) -> list:
    conn = 连接()
    try:
        if 端点键:
            rows = conn.execute(
                "SELECT * FROM 投递尝试 WHERE 端点键=? ORDER BY 投递时 DESC, rowid DESC LIMIT 200",
                (端点键,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM 投递尝试 ORDER BY 投递时 DESC, rowid DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def 死信列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 死信 ORDER BY 入匣时 DESC").fetchall()]
    finally:
        conn.close()


def 端点列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 投递端点 ORDER BY 端点键").fetchall()]
    finally:
        conn.close()


def 端点一条(端点键: str) -> dict | None:
    """端点名册详情：端点正身 + 该端点正式流水近十笔，库与详情同源。"""
    conn = 连接()
    try:
        row = conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (端点键,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["近期投递"] = [dict(x) for x in conn.execute(
            "SELECT 尝试号,事件号,状态,响应码,说明,投递时 FROM 投递尝试 "
            "WHERE 端点键=? ORDER BY 投递时 DESC, rowid DESC LIMIT 10",
            (端点键,),
        ).fetchall()]
        d["成功数"] = conn.execute(
            "SELECT COUNT(*) c FROM 投递尝试 WHERE 端点键=? AND 状态='成功'", (端点键,)
        ).fetchone()["c"]
        d["失败数"] = conn.execute(
            "SELECT COUNT(*) c FROM 投递尝试 WHERE 端点键=? AND 状态='失败'", (端点键,)
        ).fetchone()["c"]
        if int(row["启用"]) == 1:
            d["中文命运"] = "端点命运·启用中：事件入队与签名投递放行；闭窗灯红时仍受日切硬闸联拒旁路"
        else:
            d["中文命运"] = "端点命运·已停用：入队拒绝、投递联拒，不会产生新流水"
        return d
    finally:
        conn.close()


def 互证列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 互证批次 ORDER BY 批次号 DESC LIMIT 50").fetchall()]
    finally:
        conn.close()
