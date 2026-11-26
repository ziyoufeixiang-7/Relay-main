# 四模式波形执行流程（含 SYS_SYNC）

> FPGA 新指令 `SYS_SYNC (0x06)`: Active → Base + Shadow（全通道全层级）。
> 每次 Update 后 Active 已是最新值，SYS_SYNC 将这个最新值写回 Base 和 Shadow，实现三缓冲区对齐。

---

## 常量

```python
_UP   = HWCodec.FRAME_SYS_UPDATE   # Ping-Pong 翻转
_SYNC = HWCodec.FRAME_SYS_SYNC     # Active → Base + Shadow
```

## Sleep 函数

```python
Sleep(end_time)
# end_time: 绝对时间 (perf_counter), -1 = 永不结束
# 内部轮询触发器 (DI/Timeout/ManualTrig)
# 返回 (cond, nextId) 或 None (到达 end_time 未触发)
```

---

## 节点数据

```python
nd = (mode, chunks, trigs, acts, params, Ih)
```

| 模式 | chunks | params |
|:-----|:-------|:-------|
| Static | `[Ss]` | `()` |
| Sweep | `[Ss, Steps[0], ..., Steps[N-1]]` | `(iv, count)` |
| Reset | `[Ss, Rs, Steps[1], ..., Steps[N]]` | `(iv, count, rT, rD, oD)` |
| DcComp | `[Ss, Steps[0], ..., Steps[M-1], Pg[0], ..., Pg[M]]` | `(iv, M)` |

哨兵节点：
```python
nodes[0x0000] = (0, [[RESET, DBNC, START]], (), (), (), {})
nodes[0xFFFF] = (0, [[RESET]],              (), (), (), {})
```

---

## FSM 主循环

**SYS_SYNC 在这里发送**——每次状态跳转时，CoreLoop 负责 Sync + 清理。
运行器只管波形执行，触发后直接 return nextId。

```python
async def CoreLoop():
    nid = TERMINAL_ID

    while True:
        if nid == IDLE_ID:
            nd = nodes[IDLE_ID]
            await Flush(nd.Ss)              # [RESET, DBNC, START]
            await Ack(Up)
            ResetState()                    # 清空 currentState, doShadow, 计数器
            WaitInitDi()                    # 等初始 DI
            isRunning = True
            nid = await WaitManualTrig()
            continue

        if nid == TERMINAL_ID:
            isRunning = False
            nd = nodes[TERMINAL_ID]
            await Flush(nd.Ss)              # [RESET]
            await Ack(Up)
            ResetState()
            CancelBgTasks()
            nid = await WaitManualTrig()
            continue

        # ── 普通节点 ──
        nd = nodes.get(nid)
        if nd is None:
            nid = TERMINAL_ID; continue

        UpdateCurrentState(nd.Ih)           # HWProtect 电流通道
        nid = await RunNode(nd)             # 执行波形, 返回 nextId

        # ── 状态转换: Sync + 清理 ──
        CancelBgTasks()
        await Ack(Sync)                     # Active → Base + Shadow, 三缓冲区对齐
        EmitEvtTrigger(cond, nid, tUp)      # 触发报告
```

---

## Mode 0: Static

```python
async def RunStatic(nd):
    Ss = nd[1][0]

    await Flush(Ss)
    t0 = await Ack(Up)                      # Active = Ss_Base
    SpawnDo(t0)
    r = await Sleep(-1)                     # 无限等待
       → Report: ValueUpdate(nodeId, 0, do, t0)
    return r.nextId                         # 直接返回, CoreLoop 负责 Sync
```

---

## Mode 1: Sweep

```python
async def RunSweep(nd):
    iv, cnt = nd[4]
    Ss = nd[1][0]
    def st(i): return nd[1][min(1+i, len(nd[1])-1)]

    # ── Tick 0 ──
    await Flush(Ss)
    t0 = await Ack(Up)                      # Active = Out(0)
    SpawnDo(t0)
    await Ack(Sync)                         # Active → Base + Shadow, 清除旧数据
    await Flush(st(0))                      # Shadow = Out(0) + Steps[0] = Out(1)
    r = await Sleep(t0 + iv)
       → Report: ValueUpdate(nodeId, 0, do, t0)
    if r: return r.nextId

    # ── Tick 1..count ──
    for tick in range(1, cnt + 1):
        tUp = await Ack(Up)                 # Active = Out(tick)
        await Flush(st(tick))               # 预载 Steps[tick]
        r = await Sleep(t0 + (tick+1)*iv)
           → Report: ValueUpdate(nodeId, tick, do, tUp)
        if r: return r.nextId

    return COUNT_OVER.nextId
```

---

## Mode 2: Reset

```python
async def RunReset(nd):
    iv, cnt, rT, rD, oD = nd[4]
    Ss, Rs = nd[1][0], nd[1][1]
    def st(tick): return nd[1][min(1+tick, len(nd[1])-1)]  # Steps[tick], tick≥1
    cy = rT + iv

    # ── 初始化: Ss + Rs + Up → Active = Rst ──
    await Flush(Ss + Rs)
    t0 = await Ack(Up)
    SpawnDo(t0)

    # ── T0 复归期 ──
    await SetDo(rD)
    await Flush(Ss)                         # 预载 Out(0) 到 Shadow
    r = await Sleep(t0 + rT)
       → Report: ValueUpdate(nodeId, -1, do, t0)
    if r: return r.nextId

    # ── T0 输出期 ──
    await SetDo(oD)
    tUp = await Ack(Up)                     # Active = Out(0)
    r = await Sleep(t0 + rT + iv)
       → Report: ValueUpdate(nodeId, 0, do, tUp)
    if r: return r.nextId

    # ── Tick 1..count ──
    for tick in range(1, cnt + 1):
        # 复归期
        await SetDo(rD)
        tUp = await Ack(Up)                 # Active = Rst
        await Flush(st(tick))               # 预载 Steps[tick]
        r = await Sleep(t0 + tick*cy + rT)
           → Report: ValueUpdate(nodeId, -1, do, tUp)
        if r: return r.nextId

        # 输出期
        await SetDo(oD)
        tUp = await Ack(Up)                 # Active = Out(tick)
        r = await Sleep(t0 + (tick+1)*cy)
           → Report: ValueUpdate(nodeId, tick, do, tUp)
        if r: return r.nextId

    return COUNT_OVER.nextId
```

---

## Mode 3: DcComp

```python
async def RunDcComp(nd):
    iv, M = nd[4]
    Ss = nd[1][0]
    def step(i): return nd[1][1 + i]            # Steps[i], i ∈ [0, M-1]
    def pg(i):   return [nd[1][1 + M + i]]      # Pg[i], i ∈ [0, M]

    # ── 首帧突发 (NoAck) ──
    burst = Ss + step(0) + pg(0) + [_SYNC] + (step(1) if M > 1 else [])
    await FlushNA(burst)
    t0 = await WaitForAck(pg0_seq)              # 等 Pg[0] 硬件触发
    SpawnDo(t0)
    r = CheckTrigsNow()
       → Report: ValueUpdate(nodeId, 0, do, t0)
    if r: return r.nextId

    # ── Pg[1..M-1] ──
    for N in range(1, M):
        b = pg(N) + (step(N+1) if N+1 < M else [])
        await FlushNA(b)
        tUp = await WaitForAck(pgN_seq)
        r = CheckTrigsNow()
           → Report: ValueUpdate(nodeId, N, do, tUp)
        if r: return r.nextId

    # ── Pg[M] 尾步 ──
    await FlushNA(pg(M))
    tUp = await WaitForAck(pgM_seq)
    r = await Sleep(-1)                         # DC 归零, 等触发
       → Report: ValueUpdate(nodeId, M, do, tUp)
    return r.nextId
```

---

## 总结

| 关注点 | 负责方 |
|:-------|:-------|
| 波形帧发送 + Sleep + 报告 | **运行器** (RunStatic/Sweep/Reset/DcComp) |
| SYS_SYNC + BgTask清理 + 触发报告 | **CoreLoop** (状态转换时) |
| 功放控制 | **HDL 层** (收到触发报告后) |

运行器只做：发帧 → Sleep → 触发了就 return nextId。**零 teardown 逻辑。**
