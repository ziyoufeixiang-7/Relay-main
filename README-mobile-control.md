# 移动控制后端

main 与前端 relay-protect/master 的三相、六相、单片机页面配套。默认在
8080 提供页面和多客户端控制协议；本机先允许远控，手机再申请控制。
`RELAY_MOBILE_CONTROL=0` 仅用于显式选择旧直连协议，前端也必须使用相应配置。

系统配置的已有 Wi-Fi 操作允许当前控制者在空闲时调用。网络修改完成前
阻止新试验。远端自校准、更新检查、USB 更新扫描、升级和回滚均被服务端拒绝，
不依赖菜单隐藏。原本的校准认证保留，旧 AdminSession 认证已移除。

生产实例无需测试设备注册文件。如果设置 `RELAY_TEST_DEVICE_CONFIG`，仍会
检查注册设备的序列号。硬件执行与时序继续由原 EngineBridge/USEEngine 负责。
母差 ext-5i 问题本次不修复。初始化脚本不再传入 Chromium 无痕启动参数。

## 无硬件开发

安装 `requirements-mobile-test.txt`，设置 `RELAY_OFFLINE_UI=1` 和
`RELAY_OFFLINE_FRONTEND`（前端构建 index.html 的绝对路径），运行：

```text
python RelayProtection/tests/mobile_control/run_ui_simulator.py
```

本机使用 http://127.0.0.1:8080/ ，手机角色使用 http://127.0.0.2:8080/ 。
仅有模拟引擎，不导入串口、GPIO 或真实网络管理服务。运行测试时显式将
RelayProtection 和 RelayProtection/comms/mobile_control 加入 PYTHONPATH，
然后用 unittest discovery 执行 RelayProtection/tests/mobile_control。

实际设备可继续源码部署，无需为每次界面或控制修改生成 Nuitka 二进制。
源码、设备参数、页面及虚拟环境分别保存在版本目录，切换前备份原版本。
源码推送不等于设备更新或云端发布。模拟结果不能作为真实输出验收。

网络服务打包与安装说明见 deploy/mobile-network.md；Pi50 迁移和部署后验证结果见
deploy/pi50-network-deployment-2026-11-27.md。首次云端构建须完整运行 build.sh，不能用
--pack-only 代替修改后源码的编译。
