#!/usr/bin/env bash
set -euo pipefail

# Backend build + package script
# Output: deploy/build/backend.tar.gz
#
# Usage:
#   ./deploy/build.sh             Full build + package
#   ./deploy/build.sh --pack-only Skip build, repackage only

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DIR="$PROJECT_ROOT/RelayProtection"
VENV="$PROJECT_ROOT/.venv"
ENTRY="$SRC_DIR/SysEngine.py"
BUILD_DIR="$SCRIPT_DIR/build"
DIST="$BUILD_DIR/SysEngine.dist"
BACKEND_ARCHIVE="$BUILD_DIR/backend.tar.gz"

check_frequency_template() {
    "$VENV/bin/python" - "$1" <<'PY_TEMPLATE'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
if data.get("schemaVersion") != 1 or data.get("channels") != {}:
    raise SystemExit("ERROR: 打包频率表必须为空模板，不能发布某台设备的实测系数。")
PY_TEMPLATE
}

PACK_ONLY=false
if [ "${1:-}" = "--pack-only" ]; then
    PACK_ONLY=true
    echo "==== Pack-Only Mode ===="
fi

if [ "$PACK_ONLY" = false ]; then

check_frequency_template "$SRC_DIR/config/calibration_ac_frequency.json"

echo "==== Nuitka Standalone Build ===="
echo "Source: $ENTRY"
echo "Output: $DIST/"
echo ""

mkdir -p "$BUILD_DIR"

"$VENV/bin/python" -m nuitka \
    --standalone \
    --output-dir="$BUILD_DIR" \
    --include-package=api \
    --include-package=calc \
    --include-package=comms \
    --include-package=logic \
    --include-package=utils \
    --include-package=pyvisa \
    --include-package=pyvisa_py \
    --include-package=usb \
    --include-package=uvloop \
    --include-package=serial_asyncio \
    --include-package=gpiozero \
    --include-module=lgpio \
    --include-package=orjson \
    --include-package=websockets \
    --include-data-file="$SRC_DIR/config/calibration.json=config/calibration.json" \
    --include-data-file="$SRC_DIR/config/calibration_ac_frequency.json=config/calibration_ac_frequency.json" \
    --lto=no \
    --assume-yes-for-downloads \
    "$ENTRY"

fi

echo ""
echo "==== Packaging ===="

[ -d "$DIST" ] || { echo "ERROR: $DIST not found, run ./deploy/build.sh first"; exit 1; }

if [ -f "$DIST/config/calibration_ac_frequency.json" ]; then
    check_frequency_template "$DIST/config/calibration_ac_frequency.json"
fi

cp "$SRC_DIR/Updater.py" "$DIST/Updater.py"

rm -f "$BACKEND_ARCHIVE"
tar -czf "$BACKEND_ARCHIVE" -C "$DIST" .

echo ""
echo "==== Build Complete! ===="
echo "Archive: $BACKEND_ARCHIVE ($(du -sh "$BACKEND_ARCHIVE" | cut -f1))"
echo ""
echo "Next: ./deploy/publish/backend.sh"
echo ""
