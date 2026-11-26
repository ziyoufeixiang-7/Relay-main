# Windows 前端发布（只更新网页）

在 Windows 电脑上直接把新网页发布到云端，**不需要树莓派**。

## 准备（只做一次）

1. 在电脑上建一个文件夹，例如 `D:\relay-publish\`
2. 放入两个文件：
   - `publish-web.ps1`（在仓库 `deploy/` 目录下）
   - `ssh.pem`（服务器密钥，找管理员要）
3. Win10/11 自带 ssh/scp，无需安装其他软件

## 发布步骤

1. 把新的网页文件改名为 `index.html`，放进 `D:\relay-publish\`
2. 在该文件夹空白处 Shift+右键 →「在此处打开 PowerShell 窗口」
3. 执行：

```powershell
.\publish-web.ps1
```

4. 按提示操作：选版本号（直接回车 = 补丁号+1）→ 填更新说明（可留空）→ 等待完成

看到绿色「发布完成!」即成功，设备端在 系统设置 → 检查更新 就能收到新版本。

## 常见问题

| 问题 | 解决 |
|------|------|
| 提示"禁止运行脚本" | 改用 `powershell -ExecutionPolicy Bypass -File .\publish-web.ps1` |
| 提示"未找到 ssh 命令" | 设置 → 应用 → 可选功能 → 添加「OpenSSH 客户端」 |
| 提示"服务器上没有 backend.tar.gz" | 先在树莓派上完整发布一次 `./deploy/publish.sh` |
| html 不叫 index.html / 在别处 | `.\publish-web.ps1 -Html C:\路径\文件.html` |
