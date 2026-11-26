#!/usr/bin/env bash
set -euo pipefail

# Upload FPGA files (xc3sprog + fpga.bit) to cloud.
# Usage: ./deploy/publish/fpga.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UPLOAD="$SCRIPT_DIR/upload.sh"

REMOTE_DIR="/var/www/relay-update/fpga"

echo "==== Publish FPGA ===="

UPLOADED=0

FILES=("$PROJECT_ROOT/deploy/fpga/xc3sprog" "$PROJECT_ROOT/deploy/fpga/fpga.bit" "$PROJECT_ROOT/deploy/fpga/flash")

for FILE in "${FILES[@]}"; do
    if [ -f "$FILE" ]; then
        bash "$UPLOAD" "$FILE" "${REMOTE_DIR}/$(basename "$FILE")"
        UPLOADED=$((UPLOADED + 1))
    else
        echo "WARNING: $(basename "$FILE") not found, skipping"
    fi
done

[ $UPLOADED -eq 0 ] && { echo "ERROR: No files to upload" >&2; exit 1; }

echo "==== FPGA Published ===="
