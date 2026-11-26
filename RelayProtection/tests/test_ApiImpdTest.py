"""
test_ApiImpdTest.py — 阻抗特性测试 端到端模拟

关键点：MockCtrl **复刻真实 EngineCtrl 的 0xFFFF 语义**——节点跳到 0xFFFF 等价于
_doStop()（清表 + onStop），而不会回调 onUpdate(0xFFFF)。早期版本的 mock 直接调用
onUpdate(0xFFFF) 并把 _test_point 整个替换掉，恰好掩盖了"PARK=0xFFFF 导致第一个点后
即终止"的致命 bug。本测试走真实节点图，按圆特性注入跳闸 DI，验证：
  1. 二分搜索完整跑通、收敛到正确边界；
  2. 动作时间为真实硬件时间戳算得（非写死的假值）；
  3. 测点之间停在真实 IDLE 节点、不被提前终止。
"""

import os
import sys
import math
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.ApiImpdTest import ApiImpdTest, IDLE_NODE, FAULT_NODE  # noqa: E402

TICKS_PER_MS = 1000.0

total = passed = 0


def check(label, ok):
    global total, passed
    total += 1
    passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} {label}")
    return ok


class RealisticCtrl:
    """逼真引擎：真实 0xFFFF 语义；FAULT 节点按 |Z|<trip_radius 决定是否跳闸。"""

    def __init__(self, trip_radius=4.0):
        self.nodes = {}
        self.reports = []
        self.msgs = []
        self.di = 0
        self.api = None
        self.stopped = False
        self.stop_reason = None
        self._ts = 10_000_000
        self.trip_radius = trip_radius
        self._cur_Z = 0.0

    def upsertNodes(self, nodes):
        self.nodes.update(nodes)
        return True

    def trigNode(self, nid):
        asyncio.create_task(self._run(nid))

    def stopTest(self, reason=None):
        self.stopped = True
        self.stop_reason = reason
        if self.api:                      # 真实 EngineCtrl: stopTest -> _doStop -> onStop
            self.api.onStop()

    def _send(self, m):
        self.msgs.append(m)

    def sendReport(self, r):
        self.reports.append(r)

    def _adv(self, ms):
        self._ts += int(ms * TICKS_PER_MS)
        return self._ts

    async def _run(self, nid):
        cur, guard = nid, 0
        while cur is not None and not self.stopped:
            guard += 1
            if guard > 50:
                break
            n = self.nodes.get(cur)
            if n is None:                  # 缺失节点 == 引擎转 0xFFFF == _doStop
                self.stopTest("missing node -> 0xFFFF")
                return
            self.api.onUpdate(cur, 0, self._adv(0.01))
            await asyncio.sleep(0.001)
            if cur == FAULT_NODE:
                if self._cur_Z < self.trip_radius:
                    self._adv(10.0)        # 10ms 后跳闸
                    self.di ^= 0x01
                    self.api.onDi(self.di, self._ts)
                    await asyncio.sleep(0.001)
                    cur = n.diMatchId
                else:
                    self._adv(n.timeoutMs)  # 不跳 → 超时
                    cur = n.timeoutId
            else:
                self._adv(n.timeoutMs or 0)
                cur = n.timeoutId
        # 到达 IDLE：onUpdate(IDLE) 已在循环顶部置位 _node_done


async def run_search(trip_radius=4.0, seg=((1.0, 8.0), (0.0, 0.0)), precision=0.05):
    ctrl = RealisticCtrl(trip_radius=trip_radius)
    api = ApiImpdTest()
    ctrl.api = api

    orig = api._test_point
    async def wrap(R, X, ft, i_f, ft_time):
        ctrl._cur_Z = math.hypot(R, X)
        return await orig(R, X, ft, i_f, ft_time)
    api._test_point = wrap

    payload = {
        "sys": {"v": 57.735, "iLoad": {"amp": 0, "ang": 0}},
        "ctrl": {"impedance": {"resetBeforeTest": True, "resetTimeMs": 50, "preFaultTimeMs": 100}},
        "di": {"A": 2},
        "model": {"klEqualsZero": True, "precision": precision},
    }
    tests = [{"points": [{"id": 1, "x": list(seg[0]), "y": list(seg[1]),
                          "ft": 0, "i": 5.0, "time": 200}]}]
    api.setup(ctrl, {"tests": tests, "payload": payload})
    await asyncio.sleep(2.0)
    return ctrl


async def main():
    print("=" * 60)
    print("阻抗特性测试 端到端模拟")
    print("=" * 60)

    ctrl = await run_search(trip_radius=4.0)

    check("未被提前终止 (有 value_update)", len(ctrl.msgs) > 2)
    check("产生了报告", len(ctrl.reports) == 1)

    hits = [m for m in ctrl.msgs if m.get("hit")]
    check("存在跳闸点", len(hits) > 0)
    check("动作时间为真实值 10.0ms (非假值 30.5)",
          all(abs(m["actionTimeMs"] - 10.0) < 0.1 for m in hits))

    row = ctrl.reports[0]["rows"][0]
    check("边界非空", row["boundaryZValue"] is not None)
    check("边界收敛到 |Z|≈4.0", row["boundaryZValue"] is not None and abs(row["boundaryZValue"] - 4.0) < 0.1)

    # 防呆：两端同侧（全程都在跳闸区内）→ 无边界，边界留空
    ctrl2 = await run_search(trip_radius=4.0, seg=((0.5, 1.0), (0.0, 0.0)))
    row2 = ctrl2.reports[0]["rows"][0]
    check("两端同动作 → 边界留空", row2["boundaryZValue"] is None)

    print("\n" + "=" * 60)
    print(f"RESULT: {passed}/{total} tests passed")
    print("✅ ALL TESTS PASSED!" if passed == total else f"❌ {total - passed} FAILED!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
