from __future__ import annotations
from app.db import 连接
from app.services import 投递, 签名目录


def 批量重试待投(限制: int = 20) -> dict:
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT 事件号 FROM 事件 WHERE 状态 IN ('待投','投递中') ORDER BY 入队时 LIMIT ?",
            (限制,),
        ).fetchall()
    finally:
        conn.close()
    outs = []
    闭窗旁路数 = 0
    for r in rows:
        try:
            outs.append(投递.投递一次(r["事件号"]))
        except ValueError as e:
            语 = str(e)
            if "闭窗" in 语 or "旁路" in 语:
                闭窗旁路数 += 1
            outs.append({"事件号": r["事件号"], "错误": 语,
                         "命运": "闭窗旁路：未入现行流水" if ("闭窗" in 语 or "旁路" in 语) else ""})
    if 闭窗旁路数:
        汇总 = f"日切闭窗硬闸中：{闭窗旁路数} 笔待投重试被旁路，未写现行流水，互证窗不抬；开窗后可再批量重试"
    else:
        汇总 = f"批量重试 {len(outs)} 笔"
    return {"条数": len(outs), "结果": outs, "闭窗旁路数": 闭窗旁路数,
            "汇总": 汇总, "签名规": 签名目录.签名规()}
