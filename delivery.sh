#!/usr/bin/env bash
set -euo pipefail

APP_NAME="dars-manager"
PLATFORM="mac"
REF="${1:-}"

cd "$(dirname "$0")"

if [ -z "$REF" ]; then
  REF="$(git describe --tags --abbrev=0)"
fi

if ! git rev-parse --verify "$REF^{commit}" >/dev/null 2>&1; then
  echo "Reference Git invalide: $REF" >&2
  exit 1
fi

VERSION="$(printf '%s' "$REF" | tr '/' '-')"
PACKAGE_DIR="${APP_NAME}-${VERSION}"
OUT_DIR="dist"
OUT_ZIP="${OUT_DIR}/${APP_NAME}-${VERSION}-${PLATFORM}.zip"

mkdir -p "$OUT_DIR"
rm -f "$OUT_ZIP"

git archive \
  --format=zip \
  --prefix="${PACKAGE_DIR}/" \
  -o "$OUT_ZIP" \
  "$REF"

echo
echo "Package Mac cree:"
echo "$OUT_ZIP"
echo
echo "Contenu principal:"
unzip -l "$OUT_ZIP" | sed -n '1,40p'
