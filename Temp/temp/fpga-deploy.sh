#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  FPGA 固件一键部署脚本（在目标树莓派上执行）
#
#  功能:
#    1. 安装 xc3sprog + bit 固件到 /opt/FPGADownload/
#    2. 安装运行依赖 libftdi
#    3. 创建 systemd 开机自启服务（oneshot）
#    4. 立即执行一次 FPGA 烧录验证
#
#  用法:
#    curl -u fpga:<密码> -sL http://<IP>/update/fpga/fpga-pack.tar.gz \
#      | tar xz && sudo bash fpga-deploy.sh
#
#  前提: 树莓派 GPIO JTAG 已正确连接 FPGA 并共地
# ============================================================

INSTALL_DIR="/opt/FPGADownload"
SERVICE_NAME="fpga-download"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# --- 颜色输出 ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# --- 权限检查 ---
if [ "$(id -u)" -ne 0 ]; then
    error "请使用 sudo 运行此脚本"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================"
echo "  FPGA 固件部署"
echo "========================================"

# ---------- 1. 查找 bit 文件 ----------
BIT_FILES=()
shopt -s nullglob
for dir in "$SCRIPT_DIR"/*/; do
    dirname=$(basename "$dir")
    if [ -f "$dir/${dirname}.bit" ]; then
        BIT_FILES+=("$dirname")
    fi
done
shopt -u nullglob

if [ ${#BIT_FILES[@]} -eq 0 ]; then
    error "未找到任何 FPGA 固件文件（如 0514/0514.bit）"
    exit 1
fi

# 如果有多个 bit 文件，让用户选择
if [ ${#BIT_FILES[@]} -eq 1 ]; then
    SELECTED="${BIT_FILES[0]}"
    info "找到固件: ${SELECTED}/${SELECTED}.bit"
else
    echo "找到多个固件版本:"
    for i in "${!BIT_FILES[@]}"; do
        echo "  $((i+1))) ${BIT_FILES[$i]}"
    done
    read -rp "请选择要部署的固件版本 [1-${#BIT_FILES[@]}] (默认 1): " choice
    choice=${choice:-1}
    SELECTED="${BIT_FILES[$((choice-1))]}"
fi

BIT_FILE="${SELECTED}/${SELECTED}.bit"
info "将部署固件: $BIT_FILE"

# ---------- 2. 安装依赖 ----------
echo ""
info "检查运行依赖 ..."

# libftdi 是 xc3sprog 的运行时依赖
if ! ldconfig -p 2>/dev/null | grep -q libftdi; then
    info "安装 libftdi ..."
    apt-get update -qq || warn "APT 更新失败（离线？继续尝试）"
    apt-get install -y --no-install-recommends libftdi-dev || {
        error "libftdi 安装失败，xc3sprog 可能无法运行"
        error "请手动执行: sudo apt-get install -y libftdi-dev"
    }
else
    info "libftdi 已安装"
fi

# ---------- 3. 安装文件 ----------
echo ""
info "安装文件到 ${INSTALL_DIR} ..."

mkdir -p "$INSTALL_DIR"

# 复制 xc3sprog
cp "$SCRIPT_DIR/xc3sprog" "$INSTALL_DIR/xc3sprog"
chmod +x "$INSTALL_DIR/xc3sprog"

# 复制 bit 文件
mkdir -p "$INSTALL_DIR/$SELECTED"
cp "$SCRIPT_DIR/$BIT_FILE" "$INSTALL_DIR/$BIT_FILE"

info "已安装: xc3sprog + $BIT_FILE"

# ---------- 4. 创建 systemd 服务 ----------
echo ""
info "配置开机自启服务 ..."

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=FPGA Bitstream Download (xc3sprog)
# GPIO sysfs 就绪后再执行
After=sysinit.target
# 文件不存在则不启动（未部署时不空转）
ConditionPathExists=${INSTALL_DIR}/xc3sprog
ConditionPathExists=${INSTALL_DIR}/${BIT_FILE}

[Service]
Type=oneshot
# 跑一次就完，systemd 记为 active（不会反复重启）
RemainAfterExit=yes
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/xc3sprog -c sysfsgpio_rpi -v -p 0 ${BIT_FILE}
# FPGA 下载需要直接访问 GPIO，必须 root
User=root
# GPIO bit-banging JTAG 速度慢，6Mbit 固件约需 2~3 分钟，给 5 分钟超时
TimeoutStartSec=300
# 失败不重试（硬件问题需人工排查）
Restart=no

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
info "已创建并启用 ${SERVICE_NAME}.service"

# ---------- 5. 立即执行烧录验证 ----------
echo ""
info "立即执行 FPGA 烧录验证 ..."
echo "----------------------------------------"

if "$INSTALL_DIR/xc3sprog" -c sysfsgpio_rpi -v -p 0 "$INSTALL_DIR/$BIT_FILE"; then
    echo "----------------------------------------"
    info "✅ FPGA 烧录成功！"
else
    echo "----------------------------------------"
    warn "⚠️  FPGA 烧录失败！请检查 GPIO JTAG 连线是否正确"
    warn "可手动重试: sudo ${INSTALL_DIR}/xc3sprog -c sysfsgpio_rpi -v -p 0 ${INSTALL_DIR}/${BIT_FILE}"
fi

# ---------- 6. 清理临时文件 ----------
echo ""
info "清理部署临时文件 ..."
# 只清理解压出来的临时文件，不删 /opt 下的安装
rm -f "$SCRIPT_DIR/xc3sprog"
rm -rf "$SCRIPT_DIR/$SELECTED"
rm -f "$SCRIPT_DIR/fpga-deploy.sh"

echo ""
echo "========================================"
echo "  部署完成！"
echo "========================================"
echo ""
echo "安装目录:  ${INSTALL_DIR}"
echo "固件文件:  ${INSTALL_DIR}/${BIT_FILE}"
echo "开机自启:  ${SERVICE_NAME}.service (enabled)"
echo ""
echo "常用命令:"
echo "  sudo systemctl status ${SERVICE_NAME}    # 查看状态"
echo "  sudo systemctl start ${SERVICE_NAME}     # 手动烧录"
echo "  sudo systemctl disable ${SERVICE_NAME}   # 禁用开机烧录"
echo "  journalctl -u ${SERVICE_NAME}            # 查看烧录日志"
echo ""
