"""日切闭窗硬闸（hook-15）。

工程形态择定：**物化文件封住闭窗开关指纹，并拒闭窗后回刷投递**（路径甲）。
- 开关正身落在 SQLite 单例行 ``日切闭窗闸``；
- 闭窗当刻把开关指纹物化到 ``数据目录/日切闭窗封签.json``；
- 投递写口在 :func:`闸锁` 内读开关、验指纹、写正式尝试或落中文旁路，
  “读闭窗开关与写尝试”同一临界区、同成败；
- flock 打同一个 ``数据目录/日切闭窗.lock``，同进程双会话线程与跨进程都互斥。

本模块只负责“闸、封签、锁、旁路”；投递计数由投递服务救，互证同票由互证服务救，
禁止在任一处用单点校验替三红一起圆场。
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import uuid
from pathlib import Path

from app import db
from app.db import 连接
from app.services.时间 import 此刻

骨签 = "hook-15-日切闭窗硬闸"
锁文件名 = "日切闭窗.lock"
封签文件名 = "日切闭窗封签.json"


# ---------------------------------------------------------------- 路径与锁

def _数据目录() -> Path:
    # 运行时取模块属性，尊重测试在 import app.db 前注入的 数据目录
    p = Path(getattr(db, "数据目录"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def 锁路径() -> Path:
    return _数据目录() / 锁文件名


def 封签路径() -> Path:
    return _数据目录() / 封签文件名


@contextlib.contextmanager
def 闸锁():
    """全仓同一把日切闭窗锁。每次新开 fd，保证同进程线程间 flock 也冲突。"""
    p = 锁路径()
    f = open(p, "a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield f
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


def 抢闸():
    """非阻塞抢同一把锁。抢到返回持锁文件对象（用完 ``.close()`` 即放锁），抢不到返回 None。"""
    p = 锁路径()
    f = open(p, "a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except BlockingIOError:
        f.close()
        return None


# ---------------------------------------------------------------- 指纹

def _素材(票号: str, 闭窗: int, 时刻: str, 水位: int, 成功数: int) -> dict:
    return {
        "票号": 票号 or "",
        "闭窗": int(闭窗),
        "闭窗时刻": 时刻 or "",
        "水位": int(水位 or 0),
        "成功数": int(成功数 or 0),
    }


def 指纹Of(素材: dict) -> str:
    raw = json.dumps(素材, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((骨签 + "::").encode("utf-8") + raw.encode("utf-8")).hexdigest()


def _写封签文件(素材: dict, 指纹: str) -> str:
    p = 封签路径()
    body = dict(素材)
    body["指纹"] = 指纹
    body["骨签"] = 骨签
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)  # 原子物化，避免并发读到半张封签
    return str(p)


# ---------------------------------------------------------------- 水位

def 流水水位(conn) -> int:
    """现行投递流水的单调水位：投递尝试表隐式 rowid 高水位。"""
    return int(conn.execute("SELECT COALESCE(MAX(rowid),0) m FROM 投递尝试").fetchone()["m"])


def 票内成功数(conn, 水位: int) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) c FROM 投递尝试 WHERE 状态='成功' AND rowid<=?", (int(水位),)
    ).fetchone()["c"])


def 票外回刷数(conn, 水位: int) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) c FROM 投递尝试 WHERE 状态='成功' AND rowid>?", (int(水位),)
    ).fetchone()["c"])


# ---------------------------------------------------------------- 开关

def _确保单行(conn) -> None:
    conn.execute(
        "INSERT INTO 日切闭窗闸(闸号,闭窗,更新时) VALUES (1,0,?) ON CONFLICT(闸号) DO NOTHING",
        (此刻(),),
    )


def 闭窗() -> dict:
    """落下硬闸：同锁内封票、定水位、物化指纹封签。重复闭窗沿用同一张票。"""
    t = 此刻()
    with 闸锁():
        conn = 连接()
        try:
            _确保单行(conn)
            row = conn.execute("SELECT * FROM 日切闭窗闸 WHERE 闸号=1").fetchone()
            if int(row["闭窗"]) == 1 and row["票号"]:
                conn.commit()
                return _状态_dict(conn)
            w = 流水水位(conn)
            n_ok = 票内成功数(conn, w)
            票号 = f"CUT-{uuid.uuid4().hex[:12]}"
            素材 = _素材(票号, 1, t, w, n_ok)
            fp = 指纹Of(素材)
            文件 = _写封签文件(素材, fp)
            conn.execute(
                "UPDATE 日切闭窗闸 SET 闭窗=1,票号=?,闭窗时刻=?,水位=?,成功数=?,"
                "指纹=?,封签文件=?,更新时=? WHERE 闸号=1",
                (票号, t, w, n_ok, fp, 文件, t),
            )
            conn.execute(
                "INSERT INTO 签名规(键,值) VALUES ('日切闭窗','开') "
                "ON CONFLICT(键) DO UPDATE SET 值='开'",
            )
            conn.execute(
                "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                (t, "日切闭窗", "", f"{票号} 水位{w} 成功{n_ok} 封签已物化"),
            )
            conn.commit()
            return _状态_dict(conn)
        finally:
            conn.close()


def 开窗() -> dict:
    """解除硬闸（次日开窗）。闸行保留上一票备查，活跃票清空语义由 闭窗=0 表达。"""
    t = 此刻()
    with 闸锁():
        conn = 连接()
        try:
            _确保单行(conn)
            row = conn.execute("SELECT * FROM 日切闭窗闸 WHERE 闸号=1").fetchone()
            旧票 = row["票号"] if row else ""
            w = 流水水位(conn)
            n_ok = 票内成功数(conn, w)
            素材 = _素材(旧票, 0, t, w, n_ok)
            fp = 指纹Of(素材)
            文件 = _写封签文件(素材, fp)
            conn.execute(
                "UPDATE 日切闭窗闸 SET 闭窗=0,闭窗时刻=?,水位=?,成功数=?,"
                "指纹=?,封签文件=?,更新时=? WHERE 闸号=1",
                (t, w, n_ok, fp, 文件, t),
            )
            conn.execute(
                "INSERT INTO 签名规(键,值) VALUES ('日切闭窗','关') "
                "ON CONFLICT(键) DO UPDATE SET 值='关'",
            )
            conn.execute(
                "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                (t, "解除闭窗", "", f"上一票 {旧票 or '无'}，现行闸恢复营业"),
            )
            conn.commit()
            return _状态_dict(conn)
        finally:
            conn.close()


# ---------------------------------------------------------------- 核验

def _验封签文件(row) -> tuple[bool, str]:
    p = Path(row["封签文件"]) if row["封签文件"] else 封签路径()
    if not p.is_file():
        return False, "封签文件缺失"
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
        fp_file = body.pop("指纹", "")
        body.pop("骨签", None)
        素材 = _素材(body.get("票号"), body.get("闭窗"), body.get("闭窗时刻"),
                    body.get("水位"), body.get("成功数"))
        if 指纹Of(素材) != fp_file:
            return False, "封签指纹自洽校验失败"
        if fp_file != row["指纹"] or body.get("票号") != row["票号"] \
                or int(body.get("水位")) != int(row["水位"]):
            return False, "封签与闸行指纹/票号/水位不一致"
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"封签解析失败：{e}"


def 核验闸况(conn) -> dict:
    """调用方必须已持有 :func:`闸锁`。返回放行裁决；闭窗一律 fail-closed。"""
    _确保单行(conn)
    row = conn.execute("SELECT * FROM 日切闭窗闸 WHERE 闸号=1").fetchone()
    if row is None or int(row["闭窗"]) != 1:
        return {"放行": True, "闭窗": False, "票号": "", "水位": 0,
                "成功数": 0, "指纹": "", "拒绝语": "", "封签合规": True}
    ok, why = _验封签文件(row)
    票号, w = row["票号"], int(row["水位"])
    if not ok:
        语 = (f"日切闭窗硬闸：开关指纹封签异常（{why}），按 fail-closed 拒投；"
              f"票 {票号} 水位 {w}，本笔不得回刷现行流水")
        return {"放行": False, "闭窗": True, "票号": 票号, "水位": w,
                "成功数": int(row["成功数"]), "指纹": row["指纹"],
                "拒绝语": 语, "封签合规": False}
    语 = (f"日切闭窗硬闸已落下：票 {票号} 封窗水位 {w}；"
          f"闭窗后回刷一律拒入现行流水，仅记中文旁路，不抬互证窗")
    return {"放行": False, "闭窗": True, "票号": 票号, "水位": w,
            "成功数": int(row["成功数"]), "指纹": row["指纹"],
            "拒绝语": 语, "封签合规": True}


def 准投() -> dict:
    """不写库的准入核验（给死信重投等需要提前判闸的入口用）。"""
    with 闸锁():
        conn = 连接()
        try:
            return 核验闸况(conn)
        finally:
            conn.close()


def 落旁路(conn, *, 事件号: str, 端点键: str, 拟尝试号: str, 闸况: dict,
           补充: str = "") -> dict:
    """把闭窗后的投递请求落到独立的「闭窗旁路」集合——不是对账影子，也绝不混进正式流水。"""
    t = 此刻()
    旁路号 = f"BYP-{uuid.uuid4().hex[:10]}"
    命运 = (
        f"闭窗旁路：日切硬闸 {闸况.get('票号') or ''} 水位{闸况.get('水位', 0)}，"
        f"本笔成功回刷被拒于现行流水之外，正式已投递计数不涨、互证窗不抬。"
        + (f" {补充}" if 补充 else "")
    )
    conn.execute(
        "INSERT INTO 闭窗旁路(旁路号,时刻,事件号,端点键,拟尝试号,裁决,中文命运,票号,水位) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (旁路号, t, 事件号 or "", 端点键 or "", 拟尝试号 or "", "闭窗旁路",
         命运, 闸况.get("票号", ""), int(闸况.get("水位") or 0)),
    )
    conn.execute(
        "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
        (t, "闭窗旁路", 事件号, f"{旁路号} {闸况.get('票号','')} {拟尝试号}"),
    )
    return {"旁路号": 旁路号, "中文命运": 命运, "时刻": t,
            "票号": 闸况.get("票号", ""), "水位": 闸况.get("水位", 0)}


def 旁路列表(条数: int = 20) -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 闭窗旁路 ORDER BY 时刻 DESC, rowid DESC LIMIT ?", (int(条数),)
        ).fetchall()]
    finally:
        conn.close()


def _状态_dict(conn, 旁路条数: int = 12) -> dict:
    _确保单行(conn)
    row = conn.execute("SELECT * FROM 日切闭窗闸 WHERE 闸号=1").fetchone()
    closed = bool(row and int(row["闭窗"]) == 1)
    闸况 = 核验闸况(conn)
    bypass = [dict(r) for r in conn.execute(
        "SELECT * FROM 闭窗旁路 ORDER BY 时刻 DESC, rowid DESC LIMIT ?",
        (int(旁路条数),),
    ).fetchall()]
    if closed:
        灯, 灯色 = "红", "red"
        灯语 = f"闭窗灯红：日切硬闸已落下，票 {row['票号']}，封窗水位 {row['水位']}，投递回刷只走中文旁路"
        水位文案 = f"封窗水位 {row['水位']} · 票内成功 {row['成功数']} · 同票 {row['票号']}"
    else:
        灯, 灯色 = "绿", "green"
        灯语 = "闭窗灯绿：现行闸开窗营业，读开关与写尝试同锁同成败"
        水位文案 = "尚未闭窗，批次水位随现行流水实时增长"
    return {
        "闭窗": closed,
        "闭窗灯": 灯,
        "灯色": 灯色,
        "灯语": 灯语,
        "骨签": 骨签,
        "票号": row["票号"] if closed else "",
        "上一票号": row["票号"] if row else "",
        "闭窗时刻": row["闭窗时刻"] if row else "",
        "水位": int(row["水位"]) if row else 0,
        "成功数": int(row["成功数"]) if row else 0,
        "指纹": row["指纹"] if row else "",
        "封签文件": row["封签文件"] if row else "",
        "封签合规": 闸况.get("封签合规", True),
        "批次水位文案": 水位文案,
        "旁路": bypass,
    }


def 状态(旁路条数: int = 12) -> dict:
    with 闸锁():
        conn = 连接()
        try:
            return _状态_dict(conn, 旁路条数)
        finally:
            conn.close()


# 兼容不同命名习惯的别名
def 打开日切闭窗开关() -> dict:
    return 闭窗()


def 解除日切闭窗() -> dict:
    return 开窗()


def 日切闭窗() -> dict:  # 与主题同名的动作别名：默认落闸
    return 闭窗()
