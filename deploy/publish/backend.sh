#!/usr/bin/env bash
set -euo pipefail

# Publish backend to cloud server.
# Usage: ./deploy/publish/backend.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UPLOAD="$SCRIPT_DIR/upload.sh"

SERVER_IP="47.98.48.17"
source "$SCRIPT_DIR/../init.conf"
REMOTE_DIR="/var/www/relay-update/${PRODUCT}"
BASE_URL="http://${SERVER_IP}/update/${PRODUCT}"

BACKEND_ARCHIVE="$PROJECT_ROOT/deploy/build/backend.tar.gz"

[ -f "$BACKEND_ARCHIVE" ] || { echo "ERROR: backend.tar.gz not found, run ./deploy/build.sh first" >&2; exit 1; }

echo "==== Publish Backend (${PRODUCT}) ===="
echo "Archive: $BACKEND_ARCHIVE ($(du -sh "$BACKEND_ARCHIVE" | cut -f1))"

# Get current server version
SERVER_VER=$(curl -sf --connect-timeout 10 --max-time 15 "${BASE_URL}/version.json" 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('version','0.0.0'))" 2>/dev/null \
    || echo "0.0.0")
echo "Current: v${SERVER_VER}"

# Version selection
IFS='.' read -r V_MAJOR V_MINOR V_PATCH <<< "$SERVER_VER"
V_MAJOR=${V_MAJOR:-0}; V_MINOR=${V_MINOR:-0}; V_PATCH=${V_PATCH:-0}

OPT_PATCH="${V_MAJOR}.${V_MINOR}.$((V_PATCH + 1))"
OPT_MINOR="${V_MAJOR}.$((V_MINOR + 1)).0"
OPT_MAJOR="$((V_MAJOR + 1)).0.0"

echo ""
echo "Select version:"
echo "  1) Patch  -> v${OPT_PATCH} (default)"
echo "  2) Minor  -> v${OPT_MINOR}"
echo "  3) Major  -> v${OPT_MAJOR}"
echo "  4) Custom"
read -rp "[1/2/3/4] (default 1): " VER_CHOICE
VER_CHOICE="${VER_CHOICE:-1}"

case "$VER_CHOICE" in
    1) NEW_VERSION="$OPT_PATCH" ;;
    2) NEW_VERSION="$OPT_MINOR" ;;
    3) NEW_VERSION="$OPT_MAJOR" ;;
    4) read -rp "Version (e.g. 1.2.3): " NEW_VERSION ;;
    *) echo "ERROR: Invalid choice" >&2; exit 1 ;;
esac

echo "$NEW_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "ERROR: Invalid version: $NEW_VERSION" >&2; exit 1; }

read -rp "Changelog (optional): " CHANGELOG

echo ""
echo "==== Publishing v${SERVER_VER} -> v${NEW_VERSION} ===="

# Upload backend archive
bash "$UPLOAD" "$BACKEND_ARCHIVE" "${REMOTE_DIR}/backend.tar.gz"

# Update remote version.json directly via piping
SSH_KEY="$(cd "$SCRIPT_DIR/.." && pwd)/ssh.pem"
python3 -c '
import json, sys
json.dump({
    "version": sys.argv[1],
    "build_time": sys.argv[2],
    "changelog": sys.argv[3]
}, sys.stdout, indent=4, ensure_ascii=False)
' "$NEW_VERSION" "$(date -Iseconds)" "$CHANGELOG" | ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@"${SERVER_IP}" "cat > '${REMOTE_DIR}/version.json'"

# Verify
CHECK_VER=$(curl -sf --connect-timeout 10 "${BASE_URL}/version.json" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])" 2>/dev/null \
    || echo "FAIL")

if [ "$CHECK_VER" = "$NEW_VERSION" ]; then
    echo ""
    echo "==== Published ${PRODUCT} v${NEW_VERSION} ===="
else
    echo "ERROR: Verification failed! Got: $CHECK_VER, expected: $NEW_VERSION" >&2
    exit 1
fi
