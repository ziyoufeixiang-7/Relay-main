# 继电保护测试系统 — 部署与 OTA 更新使用手册

---

## 一、项目目录结构

```
newrelay/
├── RelayProtection/          # 后端源码
│   ├── SysEngine.py          # 主程序入口
│   ├── config/
│   │   └── calibration.json  # 校准数据（每台设备独立）
│   ├── api/                  # API 模块
│   ├── calc/                 # 计算模块
│   ├── comms/                # 通信模块（WebSocket / 串口）
│   ├── logic/                # 业务逻辑（含 SysConfig OTA 命令）
│   ├── utils/                # 工具模块
│   │   └── updater.py        # 独立 OTA 更新脚本（打包时复制到设备安装根目录）
│   └── docs/                 # 后端技术文档（api/ 下含 OTA 通讯协议）
├── deploy/                   # 编译/发布/部署/初始化（详见 deploy/README.md）
│   ├── build.sh              # 编译 + 打包 output/backend.tar.gz
│   ├── publish.sh            # 发布 后端+前端（交互选版本号）
│   ├── publish-web.sh        # 仅发布前端
│   ├── publish_init.sh       # 上传初始化安装包
│   ├── init.sh               # 全新树莓派一键初始化
│   ├── deploy.sh             # output/dist/ 部署到本机 /opt/（开发用）
│   ├── version.json          # 本地版本号（publish.sh 自动维护）
│   ├── web/
│   │   └── index.html        # 前端页面
│   ├── docs/                 # 发布指南 + 本手册
│   ├── build/                # Nuitka 编译产物（不进 git）
│   ├── output/               # dist/ + backend.tar.gz(.sha256)（不进 git）
│   ├── relay-protection.service  # systemd 服务文件
│   └── ssh.pem               # 阿里云 SSH 密钥（不上传 git）
├── .venv/                    # Python 虚拟环境
├── Temp/                     # 历史存档（旧文件备份，不参与构建）
└── .gitignore
```

---

## 二、开发环境配置

### 2.1 虚拟环境

```bash
cd ~/Desktop/Relay-protection/newrelay

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r RelayProtection/requirements.txt
```

### 2.2 运行（开发模式）

```bash
source .venv/bin/activate
cd RelayProtection
python SysEngine.py
```

按 `Ctrl+C` 停止。

---

## 三、编译与打包

### 3.1 编译

```bash
cd ~/Desktop/Relay-protection/newrelay
./deploy/build.sh
```

编译过程（Pi5 上约 15~30 分钟）：
1. Nuitka 将 Python 编译为 C → 生成 SysEngine.bin
2. 复制 config/ 到编译产物目录
3. 刷新 version.json 的 build_time
4. 将编译产物 + index.html + updater.py + version.json 复制到 `deploy/output/dist/`（本地运行测试用）
5. 打包仅后端的 `deploy/output/backend.tar.gz`（不含 index.html / version.json / calibration.json）
6. 生成 `backend.tar.gz.sha256`

### 3.2 编译产物

| 文件 | 说明 |
|------|------|
| `deploy/output/dist/` | 完整的可部署目录（本地测试） |
| `deploy/output/backend.tar.gz` | 仅后端发布包（~21M） |
| `deploy/output/backend.tar.gz.sha256` | SHA-256 校验值 |

### 3.3 版本号管理

版本号**不再手动改文件**：`./deploy/publish.sh` 发布时会读取服务器当前版本，
交互选择 大/中/小版本+1 或手动输入，并自动同步写回本地
`deploy/version.json`。

> 版本号遵循语义化版本 `主版本.次版本.修订号`。

---

## 四、首次部署（树莓派）

### 4.1 一键部署

确保已编译（`deploy/output/dist/` 存在），然后运行：

```bash
./deploy/deploy.sh
```

此脚本会：
1. 将 `deploy/output/dist/` 内容复制到 `/opt/RelayProtection/`
2. 安装 systemd 服务（开机自启 + 崩溃自动重启）
3. 配置 sudoers 免密（更新脚本需要重启服务）

### 4.2 服务管理

```bash
# 启动
sudo systemctl start relay-protection

# 停止
sudo systemctl stop relay-protection

# 重启
sudo systemctl restart relay-protection

# 查看状态
sudo systemctl status relay-protection

# 查看实时日志
journalctl -u relay-protection -f

# 禁止开机启动
sudo systemctl disable relay-protection
```

---

## 五、阿里云服务器配置（一次性）

### 5.1 安装 Nginx

SSH 登录到阿里云服务器 `47.98.48.17`：

```bash
sudo apt update && sudo apt install -y nginx
sudo systemctl start nginx
sudo systemctl enable nginx
```

### 5.2 创建更新目录

按设备型号分目录（本机型为 relay12，以后可加 relay7 等）：

```bash
sudo mkdir -p /var/www/relay-update/relay12
sudo chown -R root:root /var/www/relay-update
```

### 5.3 配置 Nginx

```bash
sudo tee /etc/nginx/sites-available/relay-update << 'EOF'
server {
    listen 80;
    server_name _;

    location /update/ {
        alias /var/www/relay-update/;
        autoindex off;
        limit_except GET {
            deny all;
        }
        add_header X-Content-Type-Options nosniff;
        add_header X-Frame-Options DENY;
    }

    location / {
        return 404;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/relay-update /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### 5.4 安全组

阿里云控制台 → ECS → 安全组 → 入方向添加：
- 协议：TCP
- 端口：80
- 源：0.0.0.0/0

---

## 六、发布更新

### 6.1 完整流程

```bash
# 1. 编译（约 15~30 分钟；只改前端可跳过）
./deploy/build.sh

# 2. 一键发布到阿里云（前后端）
./deploy/publish.sh

# 仅前端发布（无需编译）
./deploy/publish-web.sh
```

`publish.sh` 会自动：
- 查询服务器当前版本，交互选择新版本号（大/中/小 +1 或手动输入）
- 上传 `backend.tar.gz`、`index.html` 及各自 `.sha256`
- 服务器端 `sha256sum -c` 校验上传完整性
- 服务器端合包 后端+前端+版本号 → `relay12-vX.Y.Z.tar.gz` + `.sha256`
- 最后原子更新 `version.json` 并回读验证

### 6.2 验证

```bash
# 在树莓派上测试
curl http://47.98.48.17/update/relay12/version.json
```

应返回：

```json
{
    "version": "1.1.0",
    "build_time": "2026-06-09T16:30:00+08:00",
    "changelog": "",
    "sha256": "abc123...",
    "size": 25165824,
    "url": "http://47.98.48.17/update/relay12/relay12-v1.1.0.tar.gz"
}
```

---

## 七、OTA 更新使用（设备端）

### 7.1 前端操作

设备运行后，前端通过 WebSocket 发送以下命令：

| 操作 | WebSocket 消息 |
|------|----------------|
| 检查更新 | `{"cmd": "sysConfig", "action": "checkUpdate"}` |
| 在线更新 | `{"cmd": "sysConfig", "action": "startUpdate", "params": {"source": "online", "url": "...", "sha256": "...", "size": 12345}}` |
| 扫描 USB | `{"cmd": "sysConfig", "action": "usbScan"}` |
| USB 更新 | `{"cmd": "sysConfig", "action": "startUpdate", "params": {"source": "usb", "file": "/media/pi/USB/xxx.tar.gz", "sha256": "..."}}` |
| 回滚 | `{"cmd": "sysConfig", "action": "rollback"}` |

### 7.2 更新流程

```
用户点击"检查更新"
  → 后端从 http://47.98.48.17/update/relay12/version.json 获取最新版本
  → 前端显示版本信息和更新日志

用户确认更新
  → 后端启动 updater.py 子进程
  → 下载 → SHA-256 校验 → 备份当前版本 → 解压覆盖 → 恢复 calibration.json → 重启服务
  → 前端 WebSocket 断开 → 自动重连 → 刷新页面
```

### 7.3 USB 离线更新

1. 将服务器上的 `relay12-vX.Y.Z.tar.gz` 和 `relay12-vX.Y.Z.tar.gz.sha256` 复制到 U 盘根目录
2. 插入树莓派（udev + systemd 自动挂载到 `/media/pi/`）
3. 前端点击"扫描 USB" → 显示找到的更新包
4. 选择更新包 → 开始更新

### 7.4 回滚

更新后发现问题，点击"回滚"会恢复到上一个版本（备份保存在 `/opt/RelayProtection.bak/`）。

---

## 八、安全说明

| 措施 | 说明 |
|------|------|
| SHA-256 校验 | 下载后验证文件完整性，防止篡改 |
| 路径遍历防护 | 解压 tar 前验证所有路径不逃出安装目录 |
| USB 路径验证 | USB 更新文件必须在 `/media/` 下 |
| calibration.json | 更新时自动保护，不会被覆盖 |
| sudoers 最小权限 | updater.py 仅可执行 `systemctl restart/stop/start relay-protection` |
| ssh.pem 保护 | 私钥在 deploy/ 下，不上传 git（.gitignore 的 *.pem 规则） |

---

## 九、常用命令速查

```bash
# === 开发 ===
source .venv/bin/activate                  # 激活虚拟环境
python RelayProtection/SysEngine.py        # 开发模式运行

# === 编译 ===
./deploy/build.sh                 # 编译 + 打包

# === 部署 ===
./deploy/deploy.sh                      # 首次部署到 /opt/
./deploy/publish.sh                               # 发布到阿里云（前后端）
./deploy/publish-web.sh                           # 仅发布前端

# === 服务管理 ===
sudo systemctl start relay-protection      # 启动
sudo systemctl stop relay-protection       # 停止
sudo systemctl restart relay-protection    # 重启
journalctl -u relay-protection -f          # 查看日志

# === 验证 ===
curl http://47.98.48.17/update/relay12/version.json    # 检查服务器版本
/opt/RelayProtection/SysEngine.bin             # 直接运行编译后程序
```
