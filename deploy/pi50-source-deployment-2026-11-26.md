# Pi50 六相源码测试部署（2026-11-26）

已部署，仅指定测试机本地使用；未上传云端包、未发布其他产品。

- 设备：192.168.1.50，树莓派 5 / aarch64；页面模式 `6phase-test`（6U6I）。
- 入口：http://192.168.1.50:8080/ ，页面与控制 WebSocket 共用 8080。
- 后端运行代码来源：`6ea8511e18742ff606dc9e1b1469faed0c22e488`；旧管理员认证已移除。
- 前端构建来源：`5962f37d2c6fbd43dc6c3c53cb385150eb1e3d96`。
- 前端 SHA256：`6cc232d4a6cdd66a34cc81870d8034534b54eee3983faeb3d68dfec01f7c9e8a`。
- 按用户后续要求使用 Python 源码部署；未部署新的 Nuitka 二进制，原生编译已停止。

## 运行与回退

当前版本为 `/opt/relay-integrated-test/releases/20261126-6ea8511-5962f37-b4bb31`，`current` 指向该目录。`app/` 保存源码和沿用的设备校准文件，`runtime/` 为独立虚拟环境。`relay-integrated-test.service` 已启用，执行 `current/runtime/bin/python current/app/SysEngine.py`。本机 Chromium 已按原参数和桌面会话重启并加载新页面。

原 `relay-protection.service` 和用户级 `relay-ui-mirror-test.service` 已停止、禁用。控制核心在后端同一进程直接调用 EngineCtrl。网络管理、GPS、IEC103 保持各自服务边界；网络服务只配置下次启动的源码路径，本次没有重启它或切换 Wi-Fi。

备份位于 `/opt/relay-integrated-test/backups/20261126-6ea8511-5962f37-b4bb31`，包含原程序/页面归档、服务状态、校验值和 `rollback.py`。空闲时以管理员身份运行该回退脚本，再运行 `/home/pi/relay-integrated-build/refresh-pi-kiosk.py` 刷新页面。

原二进制哈希为 `e6cf9ae474307145356d7415c2f10af6e7fd2f5bb9ca866cbc714c2c3aa3e1b2`，本次备份保留了用户更新后的版本。

## 部署后验证

先安装后端与页面、刷新本机浏览器，再统一验证：

- 源码自检通过：`mobile_control=integrated port=8080 admin_auth=removed`。
- 串口连接成功，GPIO 硬件保护启用、功放关闭；服务无异常重启。
- 健康状态有效：`phase=local`、`active=false`；监听 8080，8181 不再监听。
- 实际空闲控制申请、收回、会话撤销通过；应用心跳超时约 6.051 秒收回。未发送 start 或加量指令。
- 原始 adminAuth 请求被 `gateway_protocol_required` 拒绝。
- 局域网 Chromium 手动连接通过：`integratedBackend=true`、`simulation=false`、远端角色正确、设备空闲、页面脚本错误为零、HTTP 页面哈希一致。
- 原二进制和原校准文件哈希保持不变；GPS、IEC103、网络服务 PID/状态、Wi-Fi UUID、NetworkManager 配置汇总哈希均与部署前一致。
- 临时编译交换空间已停用，未修改 fstab 或设备时钟。

此前离线验证：149 项引擎/校准、87 项移动控制、59 项前端专项及纯模拟集成控制流程通过。原有浏览器布局回归仍有三项失败，不声明全部浏览器回归通过。

源码运行的实际输出性能、保护动作精度、手机热点实测及昂立一致性验收未完成。设备日历时间与客户端日期不一致；本记录使用客户端日期，未调整设备时钟。
