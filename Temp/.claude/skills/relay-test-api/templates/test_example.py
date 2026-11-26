"""
test_example.py — 实验 API 端到端测试骨架（mock EngineCtrl）

复刻引擎关键语义并断言报告。最重要的是 mock 要忠实于真实引擎：
  - 跳到不存在的节点 / 0xFFFF == _doStop()（清表 + onStop）—— 用来暴露"点间误跳 0xFFFF"的致命 bug。
  - 沿节点的 timeout/diMatch 出口自走图。
  - onUpdate(IDLE, 0) 置位完成事件；用硬件时间戳推进算动作时间。

把 RelayDeviceLike 的"继电器判据"换成你实验的预期物理行为即可。
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 指到仓库根/RelayProtection

from api.ApiExampleTest import ApiExampleTest, IDLE, FAULT, PREFAULT

TPS = 327680.0  # ticks/秒


class MockCtrl:
    """复刻 EngineCtrl 语义的测试桩。继电器在 value < threshold 时动作（示例）。"""

    # 继电器: 被测值 >= threshold 时动作（过量类，与模板 hi=mid 搜索方向一致）
    def __init__(self, threshold=5.0):
        self.nodes = {}
        self.di = 0
        self.do = 0
        self.reports = []
        self.msgs = []
        self.api = None
        self.stopped = False
        self._ts = 10_000_000
        self.threshold = threshold
        self._cur_value = 0.0           # 由测试注入当前被测量，决定继电器是否动作

    # —— API 层会调用的函数 ——
    def upsertNodes(self, n):
        self.nodes.update(n)
        return True

    def trigNode(self, nid):
        asyncio.create_task(self._walk(nid))

    def stopTest(self, reason=None):
        self.stopped = True
        if self.api:                    # 真实引擎: 0xFFFF/stopTest → _doStop → onStop
            self.api.onStop()

    def sendReport(self, data):
        self.reports.append(data)

    def _send(self, m):
        self.msgs.append(m)

    def _adv(self, ms):
        self._ts += int(ms * TPS / 1000)
        return self._ts

    # —— 引擎自走节点图 ——
    async def _walk(self, nid):
        cur, guard = nid, 0
        while cur is not None and cur not in (0x0000, 0xFFFF) and not self.stopped:
            guard += 1
            if guard > 50:
                break
            n = self.nodes.get(cur)
            if n is None:               # 不存在节点 == 转 0xFFFF == _doStop
                self.stopTest("missing node -> 0xFFFF")
                return
            self.api.onUpdate(cur, 0, self._adv(0.01))
            await asyncio.sleep(0.001)
            if cur == FAULT:
                if self._cur_value >= self.threshold:       # 过量类：值越大越动作 → 跳闸 DI
                    self._adv(12.0)
                    self.di ^= 0x01
                    self.api.onDi(self.di, self._ts)
                    await asyncio.sleep(0.001)
                    cur = n.diMatchId
                else:                                       # 不动作 → 超时
                    self._adv(n.timeoutMs)
                    cur = n.timeoutId
            else:
                self._adv(n.timeoutMs or 0)
                cur = n.timeoutId
        # 到达 IDLE：onUpdate(IDLE,0) 已置位完成事件


async def main():
    ctrl = MockCtrl(threshold=5.0)
    api = ApiExampleTest()
    ctrl.api = api
    api.ctrl = ctrl
    api.isActive = True
    api.physDictToReg = lambda d, **k: d        # identity: node.base = 物理字典

    # 让 ctrl 知道每点当前被测值（包一层 _test_one）
    orig = api._test_one
    async def wrap(pt, value):
        ctrl._cur_value = value
        return await orig(pt, value)
    api._test_one = wrap

    payload = {
        "sys": {"v": 57.735, "f": 50.0},
        "di": {"mask": 0x07},
        "precision": 0.05,
        "points": [{"id": 1, "range": [1.0, 9.0], "z": 1.0, "ang": 75.0,
                    "time": 200, "preTime": 50}],
    }
    api._onSetup({"payload": payload})
    await asyncio.sleep(2.0)

    print(f"stopped={ctrl.stopped}  value_updates={len(ctrl.msgs)}  reports={len(ctrl.reports)}")
    assert ctrl.msgs, "无 value_update —— 可能点间误跳 0xFFFF 导致提前停止！"
    assert len(ctrl.reports) == 1, "应有 1 条报告"
    row = ctrl.reports[0]["rows"][0]
    print(f"边界={row['boundary']}  动作时间={row['actionTimeMs']}ms")
    assert row["boundary"] is not None and abs(row["boundary"] - 5.0) < 0.1, "边界应≈threshold 5.0"
    assert abs(row["actionTimeMs"] - 12.0) < 0.5, "动作时间应为真实 SOE 12.0ms"
    print("✅ 端到端通过：二分收敛到边界≈5.0，SOE 动作时间正确，无提前终止")


if __name__ == "__main__":
    asyncio.run(main())
