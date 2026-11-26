#!/usr/bin/env bash
set -euo pipefail

# Package and upload init script to cloud.
# Usage: ./deploy/publish/init.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UPLOAD="$SCRIPT_DIR/upload.sh"

source "$SCRIPT_DIR/../init.conf"
REMOTE_DIR="/var/www/relay-update/${PRODUCT}"
SERVER_IP="47.98.48.17"

echo "==== Publish Init Pack (${PRODUCT}) ===="

# Package
INIT_ARCHIVE=$(mktemp /tmp/init-pack-XXXXXX.tar.gz)
trap 'rm -f "$INIT_ARCHIVE"' EXIT

tar -czf "$INIT_ARCHIVE" -C "$PROJECT_ROOT" deploy/init.sh deploy/init.conf
echo "Packed: $(du -sh "$INIT_ARCHIVE" | cut -f1)"

# Upload
bash "$UPLOAD" "$INIT_ARCHIVE" "${REMOTE_DIR}/init-pack.tar.gz"

echo ""
echo "==== Init Pack Published ===="
echo "Setup command:"
echo "  curl -sL http://${SERVER_IP}/update/${PRODUCT}/init-pack.tar.gz | tar xz && sudo bash deploy/init.sh"
echo ""
