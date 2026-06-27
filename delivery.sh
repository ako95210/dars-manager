#!/usr/bin/env bash
set -euo pipefail

APP_NAME="dars-manager"
PLATFORM="mac"
REF=""

cd "$(dirname "$0")"

if [ "${1:-}" = "mac" ] || [ "${1:-}" = "windows" ]; then
  PLATFORM="$1"
  REF="${2:-}"
elif [ -n "${1:-}" ]; then
  REF="$1"
fi

if [ -z "$REF" ]; then
  REF="$(git describe --tags --abbrev=0)"
fi

case "$PLATFORM" in
  mac|windows)
    ;;
  *)
    echo "Plateforme invalide: $PLATFORM" >&2
    echo "Usage: ./delivery.sh [mac|windows] [tag-ou-commit]" >&2
    exit 1
    ;;
esac

if ! git rev-parse --verify "$REF^{commit}" >/dev/null 2>&1; then
  echo "Reference Git invalide: $REF" >&2
  exit 1
fi

if [ "$REF" = "HEAD" ] && ! git diff --quiet; then
  echo "Le repo contient des modifications non commitées." >&2
  echo "Committe avant de packager HEAD, ou utilise un tag existant." >&2
  exit 1
fi

VERSION="$(printf '%s' "$REF" | tr '/' '-')"
PACKAGE_DIR="${APP_NAME}-${VERSION}"
OUT_DIR="dist"
OUT_ZIP="${OUT_DIR}/${APP_NAME}-${VERSION}-${PLATFORM}.zip"
STAGING_DIR="${OUT_DIR}/.staging-${APP_NAME}-${VERSION}-${PLATFORM}"
COMMON_FILES=(
  ".streamlit/config.toml"
  "README.md"
  "dars_manager.py"
  "drsm_core.py"
  "drsm_streamlit.py"
  "requirements.txt"
  "runtime.txt"
)

case "$PLATFORM" in
  mac)
    PLATFORM_FILES=(
      "INSTALLATION_MAC.txt"
      "INSTALLATION_MAC.html"
      "MODE_D_EMPLOI_MAC.md"
      "drsm_local.sh"
      "drsm_mac.command"
      "install_macos.command"
    )
    ;;
  windows)
    PLATFORM_FILES=(
      "INSTALLATION_WINDOWS.txt"
      "drsm_windows.bat"
      "install_windows.bat"
    )
    ;;
esac

mkdir -p "$OUT_DIR"
rm -f "$OUT_ZIP"
rm -rf "$STAGING_DIR"

git archive \
  --format=zip \
  --prefix="${PACKAGE_DIR}/" \
  -o "$OUT_ZIP" \
  "$REF" \
  -- "${COMMON_FILES[@]}" "${PLATFORM_FILES[@]}"

if [ "$PLATFORM" = "windows" ]; then
  mkdir -p "$STAGING_DIR"
  unzip -q "$OUT_ZIP" -d "$STAGING_DIR"
  while IFS= read -r -d '' file; do
    perl -0pi -e 's/\r?\n/\r\n/g' "$file"
  done < <(find "$STAGING_DIR" -type f \( -name '*.bat' -o -name '*.txt' \) -print0)
  rm -f "$OUT_ZIP"
  (cd "$STAGING_DIR" && zip -qr "../$(basename "$OUT_ZIP")" .)
  rm -rf "$STAGING_DIR"
fi

echo
echo "Package ${PLATFORM} cree:"
echo "$OUT_ZIP"
echo
echo "Contenu principal:"
unzip -l "$OUT_ZIP" | sed -n '1,40p'
