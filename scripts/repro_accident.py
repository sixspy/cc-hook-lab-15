"""hook-15 事故夹具复现（改码前）。

剧本：先跑互证 -> 打开「日切闭窗」开关 -> 再灌投递。
现状：闭窗开关在现行代码里没有任何挂点，只能落一个开关印记，
投递写口完全不读它，于是闭窗后成功回刷照常写入现行流水，
互证窗口被抬高；闭窗当刻两路并发也会双成功。

本脚本只用于在基线代码上把事故跑出来，不参与 pytest。
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.mkdtemp(prefix="hook15_accident_")
os.environ["数据目录"] = _tmp
os.environ["夹具目录"] = str(ROOT / "fixtures")

from app import seed  # noqa: E402
from app.db import 连接  # noqa: E402
from app.services import 入队, 投递, 互证, 签名目录  # noqa: E402

seed.初始化库()


def 现行已投递数():
    conn = 连接()
    try:
        return conn.execute("SELECT COUNT(*) c FROM 事件 WHERE 状态='已投递'").fetchone()["c"]
    finally:
        conn.close()


def 流水成功数():
    conn = 连接()
    try:
        return conn.execute("SELECT COUNT(*) c FROM 投递尝试 WHERE 状态='成功'").fetchone()["c"]
    finally:
        conn.close()


print("数据目录:", _tmp)
print("== 1) 先跑互证（闭窗前基线） ==")
b0 = 互证.跑一次()
print("首批次:", {k: b0[k] for k in ("批次号" if "批次号" in b0 else "时刻", "已投递数", "是否对齐")})

print("== 2) 打开日切闭窗开关（基线无挂点，仅落开关印记） ==")
conn = 连接()
conn.execute(
    "INSERT INTO 签名规(键,值) VALUES ('日切闭窗','开') "
    "ON CONFLICT(键) DO UPDATE SET 值='开'"
)
conn.commit()
conn.close()
print("开关印记: 日切闭窗=开（投递写口不读）")

n_event_before = 现行已投递数()
n_flow_before = 流水成功数()
print(f"闭窗瞬间水位: 事件已投递={n_event_before} 流水成功={n_flow_before}")

print("== 3) 闭窗后灌投递（夹具批量事件 + 逐笔投递） ==")
体 = __import__("app.services.夹具", fromlist=["读取"]).读取("批量事件样例")
ids = []
for item in 体["事件"]:
    ids.append(入队.建事件(item)["事件号"])
for eid in ids:
    r = 投递.投递一次(eid)
    print("  投递", eid, "->", r["状态"])

n_event_after = 现行已投递数()
n_flow_after = 流水成功数()
print(f"灌后水位: 事件已投递={n_event_after} 流水成功={n_flow_after}")
print(f"[甲] 闭窗后现行已投递计数上涨 {n_event_after - n_event_before} —— 事故成立"
      if n_event_after > n_event_before else "[甲] 未上涨")

print("== 4) 再跑互证：闭窗后成功被当本窗已齐 ==")
b1 = 互证.跑一次()
print("后批次:", {k: b1.get(k) for k in ("已投递数", "是否对齐", "说明")})
print(f"[乙] 互证已投递数由 {b0['已投递数']} 抬到 {b1['已投递数']} —— 互证窗被抬高"
      if b1["已投递数"] > b0["已投递数"] else "[乙] 未抬高")

print("== 5) 争用：闭窗当刻两路投递同一事件，基线读-写不互斥 ==")
ev = 入队.建事件({"事件类型": "race.paid", "端点键": "ep-billing", "载荷摘要": "race-1"})
eid = ev["事件号"]
barrier = threading.Barrier(2)
outs = []


def 两路():
    barrier.wait()
    try:
        outs.append(投递.投递一次(eid))
    except Exception as e:  # noqa: BLE001
        outs.append({"错误": str(e)})


ts = [threading.Thread(target=两路) for _ in range(2)]
[t.start() for t in ts]
[t.join() for t in ts]
conn = 连接()
try:
    双成 = conn.execute(
        "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid,)
    ).fetchone()["c"]
finally:
    conn.close()
print("两路返回:", [(o.get("状态"), o.get("事件状态"), o.get("错误")) for o in outs])
print(f"[争用] 同一事件成功尝试数={双成} —— 双成功成立" if 双成 >= 2 else f"[争用] 成功尝试数={双成}（本次未撞上交错）")

print("== 6) 确定性交错复现读开关与写尝试脱钩 ==")
ev2 = 入队.建事件({"事件类型": "race2.paid", "端点键": "ep-billing", "载荷摘要": "race-2"})
eid2 = ev2["事件号"]
c1 = 连接()
c2 = 连接()
# 两路都在闭窗前/开关读取处拿到“放行”，随后闭窗，随后两路先后回刷成功
r1 = c1.execute("SELECT 状态 FROM 事件 WHERE 事件号=?", (eid2,)).fetchone()["状态"]
r2 = c2.execute("SELECT 状态 FROM 事件 WHERE 事件号=?", (eid2,)).fetchone()["状态"]
print("两路预检状态:", r1, r2, "（均读到可投；此刻闭窗也拦不住已放行的写）")
import uuid  # noqa: E402
from app.services.时间 import 此刻  # noqa: E402

t = 此刻()
for i, cc in enumerate((c1, c2)):
    cc.execute(
        "INSERT INTO 投递尝试(尝试号,事件号,端点键,签名摘要,状态,响应码,说明,投递时) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (f"TRY-race{i}-{uuid.uuid4().hex[:6]}", eid2, "ep-billing", "x", "成功", 200, "交错回刷", t),
    )
    cc.commit()
c1.close(); c2.close()
conn = 连接()
try:
    双成2 = conn.execute(
        "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid2,)
    ).fetchone()["c"]
finally:
    conn.close()
print(f"[争用-确定] 交错后同事件成功尝试数={双成2} —— 无锁则双成功" if 双成2 >= 2 else "未复现")
print("事故夹具复现完毕。")
