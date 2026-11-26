# [实验名称] API 协议

> **模块标识**: `"module": "xxx"`  
> **Handler 文件**: `api/XxxHdl.py`  
> **MODULE_KEYS**: `["xxx"]`

---

## 1. 实验概述

简述实验的 FSM 节点流程和核心功能。

```
状态 1 ──→ 状态 2 ──→ ... ──→ 实验结束 (next_id = -2)
```

---

## 2. 前端 → 后端：启动命令

```json
{
  "module": "xxx",
  "cmd": "start",
  "params": {
    "sys": {
      "debounce": 15
    },
    // ... 实验特定参数
  }
}
```

### 2.1 params 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `sys.debounce` | INT | 开入防抖时间 (ms) |
| ... | ... | ... |

---

## 3. 前端 → 后端：运行中控制命令

### 3.1 停止实验

```json
{
  "module": "xxx",
  "cmd": "stop"
}
```

### 3.2 按键触发（如需要）

```json
{
  "module": "xxx",
  "cmd": "next_node"
}
```

---

## 4. 后端 → 前端：遥测事件

### 4.1 实时数据更新

```json
{
  "module": "xxx",
  "type": "value_update",
  // ... 实验特定字段
}
```

### 4.2 测试报告

```json
{
  "module": "xxx",
  "type": "report",
  "data": {
    // ... 报告字段
  }
}
```

### 4.3 实验结束

```json
{
  "module": "xxx",
  "type": "stop"
}
```

### 4.4 错误通知

```json
{
  "module": "xxx",
  "type": "error",
  "msg": "错误描述"
}
```

---

## 5. 编译映射规则

描述 HDL 如何将前端参数编译为 `engine_protocol.md` 中定义的 Node Object。

### 5.1 节点生成

| 引擎 Node | 对应前端状态 | 触发条件 |
|-----------|-------------|---------|
| Node 0 | ... | ... |
| Node 1 | ... | ... |

### 5.2 特殊处理

（描述 TIMER 类条件转 MANUAL_TRIG、DI_RECOVER 转 bit10 等特殊编译逻辑）
