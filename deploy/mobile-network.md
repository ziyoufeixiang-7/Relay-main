# 后端移动端网络服务

来源：前端 develop 的 services/control_gateway/network_manager.py、wired_network.py、
test_device_config.py，以及 scripts/setup-relay-hotspot.sh（核对工作区提交 3eaba10）。
Python 服务先前已复制，现补齐热点脚本、安装入口、打包及 OTA/初始化接入。

后端包内目录为 services/mobile_network，包含五个运行文件。该目录使用设备的
/usr/bin/python3 和标准库，不需要额外的 Python 虚拟环境。系统依赖为 NetworkManager、
dnsmasq-base、nftables、iproute2、python3，初始化模块 1 会安装。

build.sh 完成主程序编译后显式复制这些文件，--pack-only 同样带入；它不会编译
前端页面或启动服务。初始化模块 5 安装并启用 relay-network-manager.service。
OTA 在切换目录前检查依赖，成功切换后安装/重启网络服务再重启主程序；失败保留错误，
网络安装失败时恢复旧服务配置及旧应用。回滚至无网络服务的版本会清理本工具管理的服务。

安装命令（root）：

    python3 /opt/RelayProtection/services/mobile_network/install_service.py --install-dir /opt/RelayProtection

--check 只检查安装前提；--enable-only 仅注册/启用，不启动或停止网络服务。
首次热点准备仍由本机界面显式操作，不修改已有 Wi-Fi、热点名称、密码和 DHCP 配置。
本机网络操作使用权限受限的 Unix socket，页面与控制端口仍为 8080。

安装/升级重启使用 /run 下的一次性标记，跳过当次网络恢复，保留当前连接；正常重启
设备时 /run 标记消失，继续恢复已保存选择。热点切换仍需要本机、试验停止及控制权已收回。
有线直连继续保留原有测试开关，未默认向生产开启。

Pi50 源码部署使用已注册的测试实例 relay-mobile-network-test.service 和测试 socket，
代码直接运行于当前后端 app/services/mobile_network。停用不用的旧前端生产网络实例，
避免两个管理器同时操作 wlan0。前端 index.html、现有校准与网络配置保持不变。
源码部署失败的 rollback.py 会恢复旧代码入口、网络/后端服务配置及页面。

测试在部署完成后执行；离线 mock 测试不连接实际网络。实机热点切换需使用独立恢复任务，
恢复原 Wi-Fi 后核对状态。没有手机接入的测试不能视为手机 DHCP/热点接入验收通过。

标准二进制首次启动（包括由旧 Updater 执行的第一轮升级）会运行 --ensure 检查；
已正确注册且运行的服务保持不动，缺失服务会补装，同时启用本次进程的网络轮询。
显式 RELAY_MIRROR_NETWORK=0 保留关闭功能。首次引导失败会报告具体原因。
--self-check 同时检查包内五个网络运行文件，不注册服务、不访问硬件。
