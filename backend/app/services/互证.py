from __future__ import annotations
from app.db import 连接
from app.services.时间 import 此刻
from app.services import 日切闭窗


def 跑一次() -> dict:
    """投递账互证。

    hook-15：闭窗后批次与流水同票——批次盖闭窗票号、钉封窗水位，
    已投递数只认票内正式成功集（rowid <= 水位）；闭窗后混进来的成功回刷
    点名为“票外回刷数”，只读联查、影子不写入任何正式集合。
    整段与闭窗/投递共用同一把闸锁取一致快照，不会把闭窗当刻的写撕裂进批次。
    """
    t = 此刻()
    with 日切闭窗.闸锁():
        conn = 连接()
        try:
            总 = conn.execute("SELECT COUNT(*) AS c FROM 事件").fetchone()["c"]
            死 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='死信'").fetchone()["c"]
            待 = conn.execute(
                "SELECT COUNT(*) AS c FROM 事件 WHERE 状态 IN ('待投','投递中')"
            ).fetchone()["c"]
            匣 = conn.execute(
                "SELECT COUNT(*) AS c FROM 死信 WHERE 状态='待处置'"
            ).fetchone()["c"]

            闸况 = 日切闭窗.核验闸况(conn)
            if 闸况["闭窗"]:
                票号 = 闸况["票号"]
                水位 = int(闸况["水位"])
                已 = 日切闭窗.票内成功数(conn, 水位)
                票外 = 日切闭窗.票外回刷数(conn, 水位)
                窗态 = "闭窗同票"
            else:
                票号 = ""
                水位 = 日切闭窗.流水水位(conn)
                已 = int(conn.execute(
                    "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 状态='成功'"
                ).fetchone()["c"])
                票外 = 0
                窗态 = "开窗流水"

            口径对齐 = (死 == 匣 and 已 + 死 + 待 <= 总)
            对齐 = 1 if (口径对齐 and 票外 == 0) else 0
            if 票外:
                说明 = (f"闭窗票 {票号} 水位{水位} 之外查出 {票外} 笔成功回刷，"
                        f"票外影子未入本窗，互证撕裂")
            elif not 口径对齐:
                说明 = "死信匣与事件死信数不一致或水位异常"
            else:
                说明 = "事件/投递/死信口径对齐，批次与流水同票"
            conn.execute(
                "INSERT INTO 互证批次(时刻,事件总数,已投递数,死信数,待投数,是否对齐,说明,"
                "闭窗票号,流水水位,票外回刷数) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t, 总, 已, 死, 待, 对齐, 说明, 票号, 水位, 票外),
            )
            conn.commit()
            批次号 = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
            return {
                "批次号": 批次号,
                "时刻": t,
                "事件总数": 总,
                "已投递数": 已,
                "死信数": 死,
                "待投数": 待,
                "死信匣待处置": 匣,
                "是否对齐": bool(对齐),
                "是否全部对齐": bool(对齐),
                "说明": 说明,
                "闭窗票号": 票号,
                "流水水位": 水位,
                "票外回刷数": 票外,
                "窗态": 窗态,
                "明细": {"总": 总, "已": 已, "死": 死, "待": 待, "匣": 匣,
                         "水位": 水位, "票外": 票外, "票号": 票号},
            }
        finally:
            conn.close()
