# 前端接入指南：在原 8080 WebSocket 上验证设备管理员

这是设备应用自己的 `admin` 身份，不是 Linux root 登录。实验、Wi-Fi、OTA 协议不变，也没有新增端口或 shell 命令。后端校验成功只表示当前 WS 已通过管理员验证；它不会让现有接口自动获得新权限。

## 先弄清楚：“签名”是谁给的？

**管理员输入设备密钥；后端给一道随机的题（`nonce`）；前端用密钥算出这道题的答案（`proof`），只把答案发回去。** 后端用本机保存的同一把密钥独立计算，两个答案一致才认可这条连接。管理员不需要手算，也不需要输入 Linux 用户名密码。

| 名称 | 谁提供 | 前端如何使用 |
|---|---|---|
| 设备密钥 | 部署人员在设备上生成，再经可信渠道交给管理员 | 管理员临时输入；不通过 WS 获取，也不发回设备 |
| `nonce`（挑战） | 后端为当前连接随机生成 | 30 秒内有效，只可尝试一次；不是密码 |
| `proof`（签名） | 前端针对这个 nonce 计算 | 只随本次 `login` 发送，不能用于下次登录 |

协议所说的“签名”具体是 **HMAC-SHA-256 消息认证码**，不是 RSA 签名，也不是把密码加密后发送。计算规则如下：

```text
密钥字节 = 将设备密钥从 64 个小写十六进制字符解码为 32 字节
待签文本 = "relay-ws-admin:v1:admin:" + nonce
proof    = HMAC-SHA-256(密钥字节, UTF-8 编码的待签文本)，输出 64 个小写十六进制字符
```

特别注意：**密钥要做 hex 解码，nonce 不要解码。** 文本前缀最后有一个冒号；不要加入空格、引号或换行，也不要对整个请求 JSON 做签名。

## 每台设备配置一次

在目标设备的可信管理终端执行（不是通过 WS）。下面的命令只在文件不存在时生成密钥，不覆盖已有密钥：

```sh
sudo sh -c 'umask 077; set -C; openssl rand -hex 32 > /etc/relay-ws-admin.key'
```

管理员通过可信渠道保管这串 64 位小写十六进制数。不要使用人选密码、校准口令，不要把它硬编码到网页、URL、日志或浏览器持久存储。文件必须是 root 所有的普通文件（不能是符号链接），权限 0400 或 0600。未配置时此功能默认关闭，不影响其他协议。撤销时移除密钥，轮换时以新生成的密钥原子替换该文件；无需重启。文件在 OTA 安装目录之外。

## 第一步：连接设备

设备的 `file:` 网页复用**已经连到** `ws://127.0.0.1:8080` 的那条 WS。若原来用本机的局域网 IP，请将这个本机网页的 WS 地址改为回环地址，不要另开一条连接。后端按实际 TCP 对端判断，不信任 JSON 中的 IP 或角色；仅允许回环连接且 Origin 是 `null` 或 `file://`。Origin 本身不是凭据。

已部署的 kiosk 默认打开 `file:///opt/RelayProtection/index.html`。如果前端已经有全局 `ws` 对象，把它直接传给下面的例子即可。**仅在页面还没有 WS 时**，在应用初始化处连接一次：

```javascript
const ws = new WebSocket("ws://127.0.0.1:8080");
ws.addEventListener("open", () => { /* 此时才启用登录按钮 */ });
ws.addEventListener("close", () => { /* 清除已登录标志，交给应用原有重连逻辑 */ });
```

- 不要为了登录再打开一条 WS 或新标签页。网关只保留一个活动客户端，新连接会顶掉旧连接，断线还可能触发实验停止。
- 在开发电脑上打开网页时，`127.0.0.1` 指向开发电脑；改连树莓派局域网 IP 也不满足本机认证条件。在树莓派上通过 `http://localhost:开发端口` 打开页面同样不等于 `file:` 页面。浏览器自动设置 Origin，无须手动传。

## 第二步：登录消息和签名函数

1. 发送 `{"cmd":"adminAuth","action":"challenge"}`。回复示意：`{"type":"adminAuthChallenge","nonce":"<64位小写hex>","expiresIn":30}`。
2. 由管理员临时输入设备密钥，用下面的函数计算签名。**密钥按 hex 解码为 32 个字节；nonce 不解码，是原样拼入字符串的 ASCII 文本。**

```javascript
async function makeAdminProof(input, nonce) {
  const keyHex = input.trim();
  if (!/^[0-9a-f]{64}$/.test(keyHex) || !/^[0-9a-f]{64}$/.test(nonce))
    throw new Error("管理员密钥或挑战格式不正确");
  const raw = Uint8Array.from(keyHex.match(/../g), byte => parseInt(byte, 16));
  const key = await crypto.subtle.importKey("raw", raw,
    {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  const data = new TextEncoder().encode("relay-ws-admin:v1:admin:" + nonce);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, data));
  return Array.from(mac, b => b.toString(16).padStart(2, "0")).join("");
}
```

3. 发送 `{"cmd":"adminAuth","action":"login","user":"admin","proof":"<签名hex>"}`，不要发送 `password` 或密钥本身。验证成功返回 `{"type":"adminAuthResult","success":true,"authenticated":true,"user":"admin","expiresIn":300}`。前端用完后清空密钥输入框。该连接内不需要携带 bearer token。
4. 受保护的身份查询：`{"cmd":"adminAuth","action":"identity"}`，成功时返回 `success:true, authenticated:true, user:"admin"`。未登录或过期返回 `success:false,error:"not_authenticated"`。
5. 退出：`{"cmd":"adminAuth","action":"logout"}`，返回 `success:true,authenticated:false`。断开、换连接或重新申请挑战后原登录失效。一次挑战最多尝试一次；过期、签名错或重放都返回 `authentication_failed`，需重新申请挑战。

### 给前端的固定测试样例（不用连设备）

以下是**公开的测试数据，不能当现场密钥**。复制上面的 `makeAdminProof` 函数后运行；若结果不同，先排查编码，再查 WS：

```javascript
const testKey = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f";
const testNonce = "202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f";
const expected = "bfc3f9a8a31a58428f914e55c236b76ed5f57e4c353fb7ed7e46b34aa59d48cb";
makeAdminProof(testKey, testNonce).then(actual => console.assert(actual === expected, actual));
```

## 第三步：把登录按钮接到已有 WS

下面的示例与上面的 `makeAdminProof` 配套。它用 `addEventListener`，不会替换实验或 Wi-Fi 页面的消息处理器。协议没有请求 ID，**同一连接上的管理员操作应串行**；等待登录结果时不要并发发出 `identity`、`logout` 或另一条 `challenge`。

```javascript
// 只等待当前认证请求的回复，忽略实验进度、Wi-Fi 等其他消息。
function adminRequest(ws, body, expectedType) {
  return new Promise((resolve, reject) => {
    if (ws.readyState !== WebSocket.OPEN) return reject(new Error("设备未连接"));
    const done = (cb, value) => {
      clearTimeout(timer);
      ws.removeEventListener("message", onMessage);
      ws.removeEventListener("close", onClose);
      cb(value);
    };
    const onClose = () => done(reject, new Error("连接已断开，请在重连后重新登录"));
    const onMessage = event => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (msg?.type === "adminAuthResult" && msg.success === false)
        return done(reject, new Error(msg.error));
      if (msg?.type === expectedType) done(resolve, msg);
    };
    const timer = setTimeout(() => done(reject, new Error("未收到认证回复")), 10000);
    ws.addEventListener("message", onMessage);
    ws.addEventListener("close", onClose);
    try { ws.send(JSON.stringify({cmd: "adminAuth", ...body})); }
    catch (error) { done(reject, error); }
  });
}

async function loginAdmin(ws, keyText) {
  const {nonce} = await adminRequest(ws, {action: "challenge"}, "adminAuthChallenge");
  const proof = await makeAdminProof(keyText, nonce);
  return await adminRequest(ws, {action: "login", user: "admin", proof}, "adminAuthResult");
}

// ws 是应用已有的连接；loginButton、keyInput、statusLabel 是页面控件。
// keyInput 使用 type="password"，先输入密钥再点登录，避免输入时挑战就过期。
loginButton.onclick = async () => {
  loginButton.disabled = true;
  const keyText = keyInput.value;
  keyInput.value = "";
  try {
    const result = await loginAdmin(ws, keyText);
    statusLabel.textContent = result.authenticated ? "管理员已验证（5 分钟）" : "未登录";
  } catch (error) {
    statusLabel.textContent = error.message;
  } finally {
    loginButton.disabled = ws.readyState !== WebSocket.OPEN;
  }
};
```

超时不要自动重发旧的 `login`；也不要在实验中为了重试强行断开 WS。页面上的“已登录”只是显示：断线时应清除，5 分钟到期也应清除；需要确认时用 `identity` 查询，查询不会续期。

## 联调时如何判断问题在哪

| 看到的结果 | 含义 / 先检查什么 |
|---|---|
| 无法建立 WS | 确认页面和后端在同一台设备、后端服务在 8080 运行；WS 未打开前不要发送 |
| 普通实验正常，但认证一直没有回复 | 确认设备已部署含 `adminAuth` 的后端，且前端监听的是原 WS 的 `message` |
| `local_kiosk_only` | 检查是否为本机回环连接、从部署的本地文件页面打开；不要从远程电脑或 HTTP 开发服务器验证 |
| `not_configured` | 设备没有有效密钥；核对文件格式、所有者和权限，不要让前端改用校准口令 |
| `authentication_failed` | 检查设备密钥、固定样例、用户名是否为 `admin`；确认挑战未过期、未重复使用且属于同一连接，然后重新申请挑战 |
| `not_authenticated` | 此连接未登录、会话过期、已登出或设备密钥已更换；重新登录 |
| `unknown_action` | `action` 只支持 `challenge`、`login`、`identity`、`logout`，大小写必须一致 |

上述协议错误均通过 `{"type":"adminAuthResult","success":false,"error":"..."}` 返回。验收时可按“固定样例一致 → 实机登录成功 → identity 返回 admin → logout 后 identity 返回 not_authenticated”检查；不需要运行任何实验。

**边界：**HMAC 用于验证持有设备密钥，不给普通 WS 流量加密，也不能保护已被篡改的网页。只有在设备上运行的可信网页才应让管理员输入密钥。后续增加受保护操作时，必须在服务端执行前对**同一连接**调用 `AdminSession.authorized()`；不要依赖前端自报的认证状态或在共享 EngineCtrl 上保存全局登录状态。
