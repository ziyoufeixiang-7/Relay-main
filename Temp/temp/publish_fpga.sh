#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  打包并上传 FPGA 固件包到阿里云服务器（带 Basic Auth 保护）
#
#  用法: ./FPGADownload/publish_fpga.sh
#
#  功能:
#    1. 打包 xc3sprog + 所有 bit 文件 + 部署脚本 → fpga-pack.tar.gz
#    2. 上传到云服务器 /var/www/relay-update/fpga/
#    3. 首次运行时自动配置 nginx Basic Auth（仅 /update/fpga/）
#
#  远端部署:
#    curl -u fpga:<密码> -sL http://<IP>/update/fpga/fpga-pack.tar.gz \
#      | tar xz && sudo bash fpga-deploy.sh
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_KEY="$PROJECT_ROOT/deploy/ssh.pem"
SERVER_IP="47.98.48.17"
SERVER_USER="root"
REMOTE_BASE="/var/www/relay-update"
REMOTE_DIR="${REMOTE_BASE}/fpga"
BASE_URL="http://${SERVER_IP}/update/fpga"

# ---- Basic Auth 配置 ----
# TODO(security): 密码不应硬编码在脚本中，生产环境应从环境变量或密钥文件读取
AUTH_USER="fpga"
AUTH_PASS_FILE="$SCRIPT_DIR/.fpga_auth_pass"
# 密码优先从文件读取，其次从环境变量，最后交互输入
get_auth_pass() {
    if [ -f "$AUTH_PASS_FILE" ]; then
        cat "$AUTH_PASS_FILE"
    elif [ -n "${FPGA_AUTH_PASS:-}" ]; then
        echo "$FPGA_AUTH_PASS"
    else
        read -rsp "请设置 FPGA 下载密码（用户名固定为 ${AUTH_USER}）: " pass
        echo ""
        echo "$pass" > "$AUTH_PASS_FILE"
        chmod 600 "$AUTH_PASS_FILE"
        echo "$pass"
    fi
}

SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10)
rssh() { ssh "${SSH_OPTS[@]}" "${SERVER_USER}@${SERVER_IP}" "$@"; }
rscp() { scp "${SSH_OPTS[@]}" "$@"; }

die() { echo "ERROR: $1" >&2; exit 1; }

# ---------- 0. 前置检查 ----------
[ -f "$SSH_KEY" ]           || die "deploy/ssh.pem 不存在，请将 SSH 密钥放到 deploy/ 目录"
[ -f "$SCRIPT_DIR/xc3sprog" ] || die "xc3sprog 可执行文件不存在"

# 查找所有 bit 文件
BIT_FILES=()
shopt -s nullglob
for dir in "$SCRIPT_DIR"/*/; do
    dirname=$(basename "$dir")
    if [ -f "$dir/${dirname}.bit" ]; then
        BIT_FILES+=("$dirname/${dirname}.bit")
    fi
done
shopt -u nullglob

[ ${#BIT_FILES[@]} -gt 0 ] || die "未找到任何 FPGA 固件文件（如 0514/0514.bit）"

echo "========================================"
echo "  打包并上传 FPGA 固件包"
echo "========================================"
echo "固件文件:"
for f in "${BIT_FILES[@]}"; do
    echo "  • $f"
done

# ---------- 1. 打包 ----------
echo ""
echo "1/3  打包 FPGA 文件 ..."
TMP_ARCHIVE="/tmp/fpga-pack.tar.gz"
trap 'rm -f "$TMP_ARCHIVE"' EXIT

# 打包内容: xc3sprog + bit 文件 + 部署脚本
TAR_ARGS=(
    -czf "$TMP_ARCHIVE"
    -C "$SCRIPT_DIR"
    xc3sprog
    fpga-deploy.sh
)
for f in "${BIT_FILES[@]}"; do
    TAR_ARGS+=("$f")
done

tar "${TAR_ARGS[@]}"
PACK_SIZE=$(du -sh "$TMP_ARCHIVE" | cut -f1)
PACK_SHA=$(sha256sum "$TMP_ARCHIVE" | cut -d' ' -f1)
echo "包大小: ${PACK_SIZE}  SHA-256: ${PACK_SHA:0:16}..."

# ---------- 2. 上传 ----------
echo "2/3  上传到服务器 ..."
rssh "mkdir -p ${REMOTE_DIR}"
rscp "$TMP_ARCHIVE" "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}/fpga-pack.tar.gz"

# 写入校验文件供远端验证
rssh "cd ${REMOTE_DIR} && sha256sum fpga-pack.tar.gz > fpga-pack.tar.gz.sha256"

# ---------- 3. 配置 nginx Basic Auth（首次自动，后续跳过） ----------
echo "3/3  配置 Basic Auth ..."

AUTH_PASS=$(get_auth_pass)
HTPASSWD_FILE="/etc/nginx/.htpasswd_fpga"

rssh bash -s -- "$AUTH_USER" "$AUTH_PASS" "$HTPASSWD_FILE" "$REMOTE_BASE" << 'REMOTE_EOF'
set -euo pipefail
AUTH_USER="$1"
AUTH_PASS="$2"
HTPASSWD_FILE="$3"
REMOTE_BASE="$4"

# 安装 htpasswd 工具（如果没有）
if ! command -v htpasswd &>/dev/null; then
    apt-get update -qq && apt-get install -y -qq apache2-utils >/dev/null 2>&1
fi

# 创建/更新 htpasswd 文件
htpasswd -cb "$HTPASSWD_FILE" "$AUTH_USER" "$AUTH_PASS" 2>/dev/null
chmod 640 "$HTPASSWD_FILE"
chown root:www-data "$HTPASSWD_FILE" 2>/dev/null || true

# 检查 nginx 是否已有 fpga 的 location 块
NGINX_CONF="/etc/nginx/sites-available/relay-update"
if [ -f "$NGINX_CONF" ] && grep -q "location /update/fpga/" "$NGINX_CONF"; then
    echo "AUTH_EXISTS"
else
    # 在现有 server 块的末尾（最后一个 } 之前）插入 fpga location
    if [ -f "$NGINX_CONF" ]; then
        # 备份
        cp "$NGINX_CONF" "$NGINX_CONF.bak-fpga"

        # 在 "location / {" 之前插入 fpga location 块
        sed -i '/location \/ {/i \
    location /update/fpga/ {\
        alias '"$REMOTE_BASE"'/fpga/;\
        autoindex off;\
        auth_basic "FPGA Firmware Download";\
        auth_basic_user_file '"$HTPASSWD_FILE"';\
        limit_except GET {\
            deny all;\
        }\
        add_header X-Content-Type-Options nosniff;\
        add_header X-Frame-Options DENY;\
    }\
' "$NGINX_CONF"

        # 验证并重载
        nginx -t 2>&1 && systemctl reload nginx
        echo "AUTH_CONFIGURED"
    else
        echo "AUTH_NO_NGINX_CONF"
    fi
fi
REMOTE_EOF

echo ""
echo "========================================"
echo "  上传完成！"
echo "========================================"
echo ""
echo "远端树莓派执行以下命令即可一键部署 FPGA 固件："
echo ""
echo "  curl -u ${AUTH_USER}:<密码> -sL ${BASE_URL}/fpga-pack.tar.gz | tar xz && sudo bash fpga-deploy.sh"
echo ""
echo "下载密码用户名: ${AUTH_USER}"
echo "下载地址: ${BASE_URL}/fpga-pack.tar.gz"
echo ""
