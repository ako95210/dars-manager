#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$ROOT_DIR/dist"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Ce script doit etre lance sur macOS pour creer le .pkg." >&2
  exit 1
fi

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" describe --tags --abbrev=0 >/dev/null 2>&1; then
    VERSION="$(git -C "$ROOT_DIR" describe --tags --abbrev=0)"
  else
    VERSION="v1.5"
  fi
fi

VERSION="${VERSION#v}"
PKG_NAME="dars-manager-v${VERSION}-mac.pkg"
DELIVERY_NAME="dars-manager-v${VERSION}-mac-livraison"
PKG_PATH="$DIST_DIR/$PKG_NAME"
DELIVERY_DIR="$DIST_DIR/$DELIVERY_NAME"
ZIP_PATH="$DIST_DIR/${DELIVERY_NAME}.zip"

cd "$ROOT_DIR"

echo "Construction du package macOS v${VERSION}..."
"$ROOT_DIR/build_macos_pkg.sh" "$VERSION"

if [ ! -f "$PKG_PATH" ]; then
  echo "Package introuvable apres construction: $PKG_PATH" >&2
  exit 1
fi

rm -rf "$DELIVERY_DIR" "$ZIP_PATH"
mkdir -p "$DELIVERY_DIR"

cp "$PKG_PATH" "$DELIVERY_DIR/$PKG_NAME"

cat > "$DELIVERY_DIR/INSTALLATION.txt" <<EOF
DARS MANAGER - INSTALLATION MAC
===============================

1. Double-cliquez sur:

   $PKG_NAME

2. Suivez l'assistant d'installation.

3. Apres installation, lancez Dars Manager depuis:

   Applications > Dars Manager

   ou depuis le raccourci cree sur le Bureau:

   Dars Manager.command

Vos audios, analyses et exports restent sur le Mac dans:

   Documents/DarsManager

EOF

if command -v ditto >/dev/null 2>&1; then
  ditto -c -k --sequesterRsrc --keepParent "$DELIVERY_DIR" "$ZIP_PATH"
else
  (cd "$DIST_DIR" && zip -qr "$(basename "$ZIP_PATH")" "$(basename "$DELIVERY_DIR")")
fi

echo
echo "Zip de livraison cree:"
echo "$ZIP_PATH"
echo
echo "Contenu:"
if command -v unzip >/dev/null 2>&1; then
  unzip -l "$ZIP_PATH"
else
  echo "- $PKG_NAME"
  echo "- INSTALLATION.txt"
fi
