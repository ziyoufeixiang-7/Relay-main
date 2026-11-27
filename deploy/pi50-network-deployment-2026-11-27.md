# Pi50 后端网络服务迁移部署记录

客户端日期：2026-11-27。目标：192.168.1.50，普通 6phase，页面与控制入口 8080。

## 已部署来源

- 后端：b9fa5bf2e38bf505caec6320adac860a12853ce2，Python 源码部署。
- 前端：fb578173937d180c9a0ac76dc17de8997b79556c，保持已有页面。
- 页面 SHA256：98976354d529ea544847dfc671021dea6e9d0003357f5ca5ea313b42732c12f5。
- 版本目录：/opt/relay-integrated-test/releases/20261127-b9fa5bf-fb57817-f28421。
- 备份：/opt/relay-integrated-test/backups/20261127-b9fa5bf-fb57817-f28421。

## 部署结果

实际网络进程运行于上述版本 app/services/mobile_network/network_manager.py。
relay-mobile-network-test.service 为已注册测试实例；旧前端 relay-network-manager.service
已停用。后端及网络服务均 active，自动重启次数为 0。现有校准、前端文件哈希均未改变。

## 验证（全部在部署之后执行）

- 网络管理、安装及有线测试边界：33 个 unittest 通过；均 mock 系统网络调用。
- 移动控制回归：90 个 unittest 通过；未连接真实硬件。
- 原有 OTA 模拟：24/24 检查通过。
- 新增 OTA 网络预检、成功安装及安装失败恢复旧应用：3 个 unittest 通过。
- 打包 fixture：--pack-only 包内包含全部五个网络运行文件。fixture 为临时假二进制，
  未部署、未上传，不能视为 Nuitka 原生编译通过。
- Bash 语法检查通过。
- 实机经本机 8080 WebSocket 网络操作切换到热点，确认 192.168.50.1/24、DHCP 服务、
  转发隔离、选择持久化及热点地址 HTTP 健康入口；再切回原 Wi-Fi 192.168.1.50/24。
- 原网络选择文件已还原，独立恢复定时器已取消（0 个待执行恢复任务）。
- 全程未发送启动电压/电流输出命令。未进行手机实际热点接入及 DHCP 获取验收。

## 后续编译

使用包含本次迁移的提交，在与仪器相同的 Linux ARM64 环境、依赖齐全的 .venv 中执行
bash deploy/build.sh。首次使用完整编译，不使用 --pack-only 替代。
初始化会安装网络依赖；已有设备 OTA 若缺少依赖，会在替换应用前明确拒绝，需先安装：

    sudo apt-get install python3 network-manager dnsmasq-base nftables iproute2

本次没有运行完整 Nuitka 编译，没有发布云端后端包，也没有自动运行全产品发布脚本。
Git 新提交仅保存在本地；使用其他构建机前需同步源码。

## 回滚

在确认仪器空闲后，以 root 执行备份目录内的 rollback.py。它恢复先前源码指针、
页面、后端/网络服务配置及旧服务状态；不是将测试机强制切回旧二进制。
