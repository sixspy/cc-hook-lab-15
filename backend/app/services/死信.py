from __future__ import annotations
from app.db import 连接
from app.services.时间 import 此刻
from app.services import 投递, 日切闭窗


class 闭窗拒绝重投(ValueError):
    """闭窗硬闸期间，死信重投也不得把事件翻回待投后再被闸拒，故前置联拒。"""


def 作废(事件号: str, 原因: str = "人工作废") -> dict:
    t = 此刻()
    conn = 连接()
    try:
        dl = conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
        if not dl:
            raise ValueError("不在死信匣")
        if dl["状态"] != "待处置":
            raise ValueError("死信已处置")
        conn.execute("UPDATE 死信 SET 状态=? WHERE 事件号=?", ("已作废", 事件号))
        conn.execute("UPDATE 事件 SET 状态=?, 更新时=? WHERE 事件号=?", ("已作废", t, 事件号))
        conn.execute("INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                     (t, "死信作废", 事件号, 原因))
        conn.commit()
        return dict(conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone())
    finally:
        conn.close()


def 重投(事件号: str) -> dict:
    # hook-15：闭窗时前置联拒，死信仍留“待处置”，不做半截状态翻转
    闸况 = 日切闭窗.准投()
    if not 闸况["放行"]:
        raise 投递.闭窗拒绝(闸况["拒绝语"] + "（死信重投口前置联拒，死信未出匣）")
    t = 此刻()
    conn = 连接()
    try:
        dl = conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
        if not dl:
            raise ValueError("不在死信匣")
        if dl["状态"] != "待处置":
            raise ValueError("死信已处置")
        conn.execute("UPDATE 死信 SET 状态=? WHERE 事件号=?", ("已重投", 事件号))
        conn.execute("UPDATE 事件 SET 状态=?, 更新时=? WHERE 事件号=?", ("待投", t, 事件号))
        conn.execute("INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                     (t, "死信重投", 事件号, "回待投"))
        conn.commit()
    finally:
        conn.close()
    return 投递.投递一次(事件号)


def 列表():
    from app.services import 签名目录
    return 签名目录.死信列表()
