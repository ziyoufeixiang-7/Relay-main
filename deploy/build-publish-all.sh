#!/usr/bin/env bash
set -Eeuo pipefail

# Build the backend once, then package and publish it for all relay products.
# Each product gets its own Updater.py PRODUCT value and publish destination.
# Existing init.conf and Updater.py contents are restored when the script exits.
#
# Usage:
#   ./deploy/build-publish-all.sh
#   ./deploy/build-publish-all.sh "changelog text"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INIT_CONF="$SCRIPT_DIR/init.conf"
UPDATER="$PROJECT_ROOT/RelayProtection/Updater.py"
BUILD_SCRIPT="$SCRIPT_DIR/build.sh"
PUBLISH_SCRIPT="$SCRIPT_DIR/publish/backend.sh"

PRODUCTS=(relay12 relay7 relay6)
SCREENS=(10_1 10_1 7_0)
CHANGELOG="${*:-}"

for required_file in "$INIT_CONF" "$UPDATER" "$BUILD_SCRIPT" "$PUBLISH_SCRIPT"; do
    [ -f "$required_file" ] || {
        echo "ERROR: Required file not found: $required_file" >&2
        exit 1
    }
done

if [[ "$CHANGELOG" == *$'\n'* ]]; then
    echo "ERROR: Changelog must be a single line" >&2
    exit 1
fi

BACKUP_DIR="$(mktemp -d)"
cp -- "$INIT_CONF" "$BACKUP_DIR/init.conf"
cp -- "$UPDATER" "$BACKUP_DIR/Updater.py"

restore_config() {
    local exit_code=$?
    trap - EXIT INT TERM
    cp -- "$BACKUP_DIR/init.conf" "$INIT_CONF"
    cp -- "$BACKUP_DIR/Updater.py" "$UPDATER"
    rm -rf -- "$BACKUP_DIR"
    if [ "$exit_code" -ne 0 ]; then
        echo ""
        echo "ERROR: Build/publish stopped; original configuration has been restored." >&2
    fi
    exit "$exit_code"
}
trap restore_config EXIT INT TERM

configure_product() {
    local product="$1"
    local screen="$2"

    grep -qE '^PRODUCT="[^"]+"$' "$INIT_CONF" || {
        echo "ERROR: PRODUCT setting not found in $INIT_CONF" >&2
        return 1
    }
    grep -qE '^SCREEN="[^"]+"$' "$INIT_CONF" || {
        echo "ERROR: SCREEN setting not found in $INIT_CONF" >&2
        return 1
    }
    grep -qE '^PRODUCT[[:space:]]*=[[:space:]]*"[^"]+"[[:space:]]*$' "$UPDATER" || {
        echo "ERROR: PRODUCT setting not found in $UPDATER" >&2
        return 1
    }

    sed -i -E "s/^PRODUCT=\"[^\"]+\"$/PRODUCT=\"${product}\"/" "$INIT_CONF"
    sed -i -E "s/^SCREEN=\"[^\"]+\"$/SCREEN=\"${screen}\"/" "$INIT_CONF"
    sed -i -E "s/^PRODUCT[[:space:]]*=[[:space:]]*\"[^\"]+\"[[:space:]]*$/PRODUCT = \"${product}\"/" "$UPDATER"

    grep -qxF "PRODUCT=\"${product}\"" "$INIT_CONF"
    grep -qxF "SCREEN=\"${screen}\"" "$INIT_CONF"
    grep -qxF "PRODUCT = \"${product}\"" "$UPDATER"
}

echo "==== Build and publish all relay products ===="
echo "Products: relay12/10_1, relay7/10_1, relay6/7_0"
echo "Version: each product's next patch version"
echo ""

for index in "${!PRODUCTS[@]}"; do
    product="${PRODUCTS[$index]}"
    screen="${SCREENS[$index]}"

    echo "==== [$((index + 1))/${#PRODUCTS[@]}] ${product} (${screen}) ===="
    configure_product "$product" "$screen"

    if [ "$index" -eq 0 ]; then
        bash "$BUILD_SCRIPT"
    else
        bash "$BUILD_SCRIPT" --pack-only
    fi

    # backend.sh prompts for version choice and changelog. Select its default
    # patch version and provide the common changelog without extra interaction.
    printf '\n%s\n' "$CHANGELOG" | bash "$PUBLISH_SCRIPT"
    echo ""
done

echo "==== All products published successfully ===="
