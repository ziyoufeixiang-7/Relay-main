#!/usr/bin/env bash
set -euo pipefail

# Upload NVMe clone script to cloud.
# Usage: ./deploy/publish/nvme.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UPLOAD="$SCRIPT_DIR/upload.sh"

REMOTE_DIR="/var/www/relay-update/nvme"
SERVER_IP="47.98.48.17"

echo "==== Publish NVMe Clone Script ===="

FILE="$PROJECT_ROOT/deploy/nvme-clone.sh"

if [ -f "$FILE" ]; then
    bash "$UPLOAD" "$FILE" "${REMOTE_DIR}/$(basename "$FILE")"
    echo ""
    echo "==== NVMe Clone Script Published ===="
    echo "Run command on target:"
    echo "  curl -sL http://${SERVER_IP}/update/nvme/nvme-clone.sh -o nvme-clone.sh && chmod +x nvme-clone.sh && sudo ./nvme-clone.sh"
    echo ""
else
    echo "ERROR: nvme-clone.sh not found at $FILE" >&2
    exit 1
fi
