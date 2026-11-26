#!/usr/bin/env bash
set -euo pipefail

# Upload a local file to the cloud server with SHA-256 verification.
# Usage: ./deploy/publish/upload.sh <local_file> <remote_path>

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH_KEY="$(cd "$SCRIPT_DIR/.." && pwd)/ssh.pem"
SERVER_IP="47.98.48.17"
SERVER_USER="root"

SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10)
rssh() { ssh "${SSH_OPTS[@]}" "${SERVER_USER}@${SERVER_IP}" "$@"; }
rscp() { scp "${SSH_OPTS[@]}" "$@"; }

[ $# -ge 2 ] || { echo "Usage: $0 <local_file> <remote_path>" >&2; exit 1; }

LOCAL_FILE="$1"
REMOTE_PATH="$2"
REMOTE_DIR="$(dirname "$REMOTE_PATH")"
REMOTE_NAME="$(basename "$REMOTE_PATH")"

[ -f "$LOCAL_FILE" ] || { echo "ERROR: File not found: $LOCAL_FILE" >&2; exit 1; }
[ -f "$SSH_KEY" ] || { echo "ERROR: ssh.pem not found" >&2; exit 1; }

LOCAL_SHA=$(sha256sum "$LOCAL_FILE" | cut -d' ' -f1)

# Upload
rssh "mkdir -p '${REMOTE_DIR}'"
rscp "$LOCAL_FILE" "${SERVER_USER}@${SERVER_IP}:${REMOTE_PATH}"

# Remote verification
VERIFY_OUT=$(rssh bash -s <<EOF
set -euo pipefail
cd "${REMOTE_DIR}"
echo "${LOCAL_SHA}  ${REMOTE_NAME}" > "${REMOTE_NAME}.sha256"
sha256sum -c "${REMOTE_NAME}.sha256" --quiet && echo "VERIFY_OK" || echo "VERIFY_FAIL"
chmod 644 "${REMOTE_NAME}" "${REMOTE_NAME}.sha256"
EOF
) || true

if echo "$VERIFY_OUT" | grep -q "VERIFY_OK"; then
    echo "  ✓ $REMOTE_NAME"
else
    echo "ERROR: Remote SHA-256 mismatch: $VERIFY_OUT" >&2; exit 1
fi
