# ============================================================
#  Windows 前端发布脚本（仅上传网页，服务器端用已有后端包合包）
#
#  用法（PowerShell）:
#    .\publish-web.ps1                  # 用脚本旁边的 index.html
#    .\publish-web.ps1 -Html C:\xx.html # 指定 html 文件
#
#  准备: 本脚本 + ssh.pem 放同一文件夹（Win10/11 自带 ssh/scp）
#  说明见 docs/Windows前端发布.md
# ============================================================

param([string]$Html = (Join-Path $PSScriptRoot "index.html"))

$ErrorActionPreference = "Stop"

$ServerIP  = "47.98.48.17"
$User      = "root"
$Product   = "relay12"
$RemoteDir = "/var/www/relay-update/$Product"
$BaseUrl   = "http://$ServerIP/update/$Product"
$Key       = Join-Path $PSScriptRoot "ssh.pem"
$SshOpts   = @("-i", $Key, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10")

function Die($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# ---------- 0. 前置检查 ----------
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { Die "未找到 ssh 命令，请安装 Windows OpenSSH 客户端（设置→应用→可选功能）" }
if (-not (Test-Path $Key)) { Die "ssh.pem 不存在，请将密钥放到脚本同目录: $Key" }
if (-not (Test-Path $Html)) {
    $alt = Join-Path $PSScriptRoot "web\index.html"
    if (Test-Path $alt) { $Html = $alt } else { Die "找不到网页文件: $Html（可用 -Html 参数指定）" }
}
Write-Host "前端文件: $Html"

# 服务器上必须已有后端包（至少完整发布过一次）
ssh @SshOpts "$User@$ServerIP" "test -f $RemoteDir/backend.tar.gz"
if ($LASTEXITCODE -ne 0) { Die "服务器上没有 backend.tar.gz，请先在树莓派上执行一次完整发布 ./deploy/publish.sh" }

# ---------- 1. 服务器当前版本 ----------
try { $serverVer = (Invoke-RestMethod "$BaseUrl/version.json" -TimeoutSec 10).version } catch { $serverVer = "0.0.0" }
Write-Host "服务器当前版本: v$serverVer"

# ---------- 2. 选择新版本号 ----------
$v = $serverVer.Split('.'); while ($v.Count -lt 3) { $v += "0" }
$optPatch = "$($v[0]).$($v[1]).$([int]$v[2] + 1)"
$optMinor = "$($v[0]).$([int]$v[1] + 1).0"
$optMajor = "$([int]$v[0] + 1).0.0"

Write-Host ""
Write-Host "请选择新版本号:"
Write-Host "  1) 补丁 +1   ->  v$optPatch   (默认)"
Write-Host "  2) 次版本 +1 ->  v$optMinor"
Write-Host "  3) 主版本 +1 ->  v$optMajor"
Write-Host "  4) 手动输入"
$choice = Read-Host "请输入 [1/2/3/4] (默认 1)"
switch ($choice) {
    "2"     { $newVer = $optMinor }
    "3"     { $newVer = $optMajor }
    "4"     { $newVer = Read-Host "请输入版本号 (如 1.2.3)" }
    default { $newVer = $optPatch }
}
if ($newVer -notmatch '^\d+\.\d+\.\d+$') { Die "版本号格式无效: $newVer (应为 X.Y.Z)" }

$changelog = Read-Host "更新说明 changelog (可留空)"
$pkg = "$Product-v$newVer.tar.gz"
Write-Host ""
Write-Host "========================================"
Write-Host "  发布前端  v$serverVer -> v$newVer"
Write-Host "========================================"

# ---------- 3. 准备本地文件（LF、无 BOM，否则服务器校验/设备解析会失败） ----------
$tmp = Join-Path $env:TEMP "relay-publish-$(Get-Random)"
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Copy-Item $Html (Join-Path $tmp "index.html")
    $sha = (Get-FileHash (Join-Path $tmp "index.html") -Algorithm SHA256).Hash.ToLower()
    [IO.File]::WriteAllText((Join-Path $tmp "index.html.sha256"), "$sha  index.html`n")
    Write-Host "前端 SHA-256: $sha"

    $buildTime = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
    $baseJson = [ordered]@{ version = $newVer; build_time = $buildTime; changelog = $changelog } | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $tmp "version.base.json"), ($baseJson -replace "`r", "") + "`n")

    # ---------- 4. 上传 ----------
    Write-Host "1/4  上传文件到服务器 ..."
    scp @SshOpts (Join-Path $tmp "index.html") (Join-Path $tmp "index.html.sha256") (Join-Path $tmp "version.base.json") "${User}@${ServerIP}:$RemoteDir/"
    if ($LASTEXITCODE -ne 0) { Die "上传失败" }

    # ---------- 5. 服务器端: 校验 + 合包 ----------
    Write-Host "2/4  服务器校验 SHA-256 并合包 ..."
    $bash = @"
set -euo pipefail
cd $RemoteDir
sha256sum -c backend.tar.gz.sha256 --quiet
sha256sum -c index.html.sha256 --quiet
rm -rf .pack && mkdir .pack
tar -xzf backend.tar.gz -C .pack
cp index.html .pack/index.html
cp version.base.json .pack/version.json
rm -f $Product-v*.tar.gz $Product-v*.tar.gz.sha256
tar -czf $pkg -C .pack .
rm -rf .pack
sha256sum $pkg > $pkg.sha256
echo PACK_SHA=`$(cut -d' ' -f1 $pkg.sha256)
echo PACK_SIZE=`$(stat -c%s $pkg)
echo PACK_OK
"@
    $out = ($bash -replace "`r", "") | ssh @SshOpts "$User@$ServerIP" "bash -s"
    if (($LASTEXITCODE -ne 0) -or -not ($out -match "PACK_OK")) { Die "服务器端校验/合包失败: $out" }
    $pkgSha  = ($out | Select-String "^PACK_SHA=").Line.Split("=")[1]
    $pkgSize = [long]($out | Select-String "^PACK_SIZE=").Line.Split("=")[1]
    Write-Host "更新包: $pkg ($pkgSize bytes)"
    Write-Host "SHA-256: $pkgSha"

    # ---------- 6. 更新服务器 version.json（最后一步原子生效） ----------
    Write-Host "3/4  更新服务器 version.json ..."
    $finalJson = [ordered]@{
        version    = $newVer
        build_time = $buildTime
        changelog  = $changelog
        sha256     = $pkgSha
        size       = $pkgSize
        url        = "$BaseUrl/$pkg"
    } | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $tmp "version.json"), ($finalJson -replace "`r", "") + "`n")
    scp @SshOpts (Join-Path $tmp "version.json") "${User}@${ServerIP}:$RemoteDir/version.json.tmp"
    if ($LASTEXITCODE -ne 0) { Die "version.json 上传失败" }
    ssh @SshOpts "$User@$ServerIP" "mv $RemoteDir/version.json.tmp $RemoteDir/version.json && rm -f $RemoteDir/version.base.json"
    if ($LASTEXITCODE -ne 0) { Die "version.json 生效失败" }

    # ---------- 7. 最终验证 ----------
    Write-Host "4/4  最终验证 ..."
    $check = Invoke-RestMethod "$BaseUrl/version.json" -TimeoutSec 10
    if (($check.version -ne $newVer) -or ($check.sha256 -ne $pkgSha)) { Die "最终验证失败! 服务器返回 v$($check.version)" }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  发布完成!  $Product v$newVer" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "版本检查: $BaseUrl/version.json"
    Write-Host "设备端可在 系统设置->检查更新 中获取此版本"
}
finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
