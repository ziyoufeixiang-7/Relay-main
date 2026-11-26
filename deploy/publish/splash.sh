#!/usr/bin/env bash
set -euo pipefail

# Upload Splash Screen image to cloud.
# Usage: ./deploy/publish/splash.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UPLOAD="$SCRIPT_DIR/upload.sh"

REMOTE_DIR="/var/www/relay-update"

echo "==== Publish Splash Screen ===="

SPLASH_FILE="$PROJECT_ROOT/deploy/splash.png"

if [ -f "$SPLASH_FILE" ]; then
    SPLASH_90_FILE="$PROJECT_ROOT/deploy/splash_90.png"
    
    echo "Generating rotated splash image (splash_90.png)..."
    python3 -c "
from PIL import Image
import sys
try:
    img = Image.open('$SPLASH_FILE')
    img_rotated = img.transpose(Image.ROTATE_90)
    img_rotated.save('$SPLASH_90_FILE')
except Exception as e:
    print('Failed to rotate image:', e)
    sys.exit(1)
"
    
    bash "$UPLOAD" "$SPLASH_FILE" "${REMOTE_DIR}/splash.png"
    bash "$UPLOAD" "$SPLASH_90_FILE" "${REMOTE_DIR}/splash_90.png"
    
    echo "==== Splash Screen Published ===="
else
    echo "ERROR: Splash image not found at $SPLASH_FILE" >&2
    exit 1
fi
