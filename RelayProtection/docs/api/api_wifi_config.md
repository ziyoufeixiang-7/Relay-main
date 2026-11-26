# API: WiFi Configuration

**Module Covered**: System Level `EngineCtrl` (WiFi Settings)

本部分 API 定义了通过 WebSocket 与底层控制网关 (`WSGateway` 在 `8080` 端口) 交互，以进行设备的 WiFi 网络扫描与连接。此功能属于系统全局配置，与任何具体的测试模块（例如 ac_test 等）独立。

---

## 1. 扫描 WiFi 网络 (`wifiScan`)

前端发送扫描指令，后端将异步调用底层的 `nmcli` 工具搜索附近的 WiFi 网络，并在几秒后推送结果。

**前端发送**:
```json
{
  "cmd": "sysConfig",
  "action": "wifiScan"
}
```

**后端异步响应**:
搜索完成后，后端将返回去重后的网络列表。
```json
{
  "type": "wifiScanResults",
  "data": [
    {
      "ssid": "MyCompanyWiFi",
      "signal": "85",
      "active": true,
      "saved": true
    },
    {
      "ssid": "Guest_Network",
      "signal": "60",
      "active": false,
      "saved": false
    }
  ]
}
```

- `ssid`: 网络的名称。
- `signal`: 信号强度 (0-100)。
- `active`: 该网络当前是否为正在使用的连接。
- `saved`: 该网络是否已保存（存在 NetworkManager 连接 profile，可免密码直连）。

---

## 2. 连接 WiFi (`wifiConnect`)

发送指定的 `ssid` 以及 `password` 给底层尝试连接。由于连接过程需要验证密码、协商密钥、获取 IP 地址等步骤，因此后端的响应会在大约 3~10 秒后返回。

**前端发送**:
```json
{
  "cmd": "sysConfig",
  "action": "wifiConnect",
  "params": {
    "ssid": "MyCompanyWiFi",
    "password": "my_secret_password"
  }
}
```

**后端异步响应**:

连接成功时：
```json
{
  "type": "wifiConnectResult",
  "status": "success",
  "msg": "Device 'wlan0' successfully activated with '...'"
}
```

连接失败时：
```json
{
  "type": "wifiConnectResult",
  "status": "error",
  "msg": "Error: Connection activation failed: (53) The Wi-Fi network could not be found."
}
```

- `status`: 取值为 `"success"` 或 `"error"`。
- `msg`: 底层 `nmcli` 返回的具体描述或失败原因，可用于在界面上展示给用户。

> **说明**：`wifiConnect` 成功后会**自动生成一个已保存的连接 profile**（NetworkManager 记住凭据）。
> 之后即可用 `wifiConnectSaved` 免密码重连，或用 `wifiForget` 删除。

---

## 3. 查看已保存网络 (`wifiSavedList`)

列出 NetworkManager 中已保存的 WiFi 连接 profile（即记住了凭据、可免密码直连的网络）。
与扫描列表不同：已保存网络即使当前不在信号范围内也会列出。

**前端发送**:
```json
{
  "cmd": "sysConfig",
  "action": "wifiSavedList"
}
```

**后端响应**:
```json
{
  "type": "wifiSavedResults",
  "data": [
    {
      "name": "MyCompanyWiFi",
      "uuid": "a1b2c3d4-...",
      "active": true,
      "autoconnect": true
    },
    {
      "name": "HomeWiFi",
      "uuid": "e5f6g7h8-...",
      "active": false,
      "autoconnect": true
    }
  ]
}
```

- `name`: profile 名称（通常等于 SSID）。
- `uuid`: profile 唯一标识，**推荐前端用它作为后续操作的参数**（避免重名 SSID 歧义）。
- `active`: 当前是否正在使用该连接。
- `autoconnect`: 是否开机/进入信号范围时自动重连（工控机无人值守的关键）。

---

## 4. 连接已保存网络 (`wifiConnectSaved`)

直接激活一个已保存的 profile，**无需再输密码**（凭据已存）。

**前端发送**（参数为 `uuid`）:
```json
{
  "cmd": "sysConfig",
  "action": "wifiConnectSaved",
  "params": { "uuid": "a1b2c3d4-..." }
}
```

**后端响应**：与 `wifiConnect` 相同，复用 `wifiConnectResult`：
```json
{ "type": "wifiConnectResult", "status": "success", "msg": "Connection successfully activated ..." }
```

---

## 5. 删除/忘记已保存网络 (`wifiForget`)

删除一个已保存的 profile，连同存储的密码一并清除。

**前端发送**（参数为 `uuid`）:
```json
{
  "cmd": "sysConfig",
  "action": "wifiForget",
  "params": { "uuid": "a1b2c3d4-..." }
}
```

**后端响应**:
```json
{ "type": "wifiForgetResult", "status": "success", "msg": "" }
```
- `status`: `"success"` 或 `"error"`。

---

## 6. 当前连接状态 (`wifiStatus`)

查询当前 WiFi 连接及其 IP（界面顶栏/设置页常用）。

**前端发送**:
```json
{
  "cmd": "sysConfig",
  "action": "wifiStatus"
}
```

**后端响应**:
```json
{
  "type": "wifiStatus",
  "connected": true,
  "ssid": "MyCompanyWiFi",
  "signal": "90",
  "ip": "192.168.1.50"
}
```
- `connected`: 是否已连接 WiFi；未连接时 `ssid`、`signal`、`ip` 等字段可能不存在或为空。

---

## 7. 错误处理与说明

- 所有命令均通过 `nmcli`（NetworkManager）实现，调用带超时保护，不会阻塞测试事件循环。
- `uuid`（用于连接/删除已保存网络）接受 **UUID 或 profile 名**；推荐用 UUID 以避免重名歧义。
- 若设备未安装 NetworkManager（`nmcli` 不存在），相关命令返回 `error` 且 `msg` 提示 `nmcli not found`。
