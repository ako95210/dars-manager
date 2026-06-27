#!/usr/bin/env bash
set -euo pipefail

APP_NAME="DarsManager"
IDENTIFIER="local.darsmanager.app"
VERSION="${1:-1.4}"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$ROOT_DIR/dist/macos-pkg-build"
PAYLOAD_DIR="$BUILD_DIR/payload"
SCRIPTS_DIR="$BUILD_DIR/scripts"
APP_DIR="$PAYLOAD_DIR/Applications/DarsManager"
LAUNCHER_APP="$PAYLOAD_DIR/Applications/Dars Manager.app"
LAUNCHER_CONTENTS="$LAUNCHER_APP/Contents"
LAUNCHER_MACOS="$LAUNCHER_CONTENTS/MacOS"
OUT_PKG="$ROOT_DIR/dist/dars-manager-v${VERSION}-mac.pkg"
SIGNED_PKG="$ROOT_DIR/dist/dars-manager-v${VERSION}-mac-signed.pkg"

if ! command -v pkgbuild >/dev/null 2>&1; then
  echo "pkgbuild est introuvable. Ce script doit etre lance sur macOS avec Xcode Command Line Tools." >&2
  exit 1
fi

mkdir -p "$ROOT_DIR/dist"

if [ -e "$BUILD_DIR" ]; then
  echo "Suppression de l'ancien dossier de build: $BUILD_DIR"
  rm -rf "$BUILD_DIR"
fi

if [ -e "$OUT_PKG" ]; then
  echo "Suppression de l'ancien package: $OUT_PKG"
  rm -f "$OUT_PKG"
fi

mkdir -p "$APP_DIR" "$SCRIPTS_DIR" "$LAUNCHER_MACOS"

copy_file() {
  mkdir -p "$APP_DIR/$(dirname "$1")"
  cp -R "$ROOT_DIR/$1" "$APP_DIR/$1"
}

copy_file ".streamlit"
copy_file "INSTALLATION_MAC.txt"
copy_file "INSTALLATION_MAC.html"
copy_file "MODE_D_EMPLOI_MAC.md"
copy_file "README.md"
copy_file "dars_manager.py"
copy_file "drsm_core.py"
copy_file "drsm_streamlit.py"
copy_file "drsm_local.sh"
copy_file "drsm_mac.command"
copy_file "install_macos.command"
copy_file "requirements.txt"
copy_file "runtime.txt"

chmod +x "$APP_DIR/drsm_mac.command" "$APP_DIR/install_macos.command" "$APP_DIR/drsm_local.sh"

cat > "$LAUNCHER_CONTENTS/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>launch</string>
  <key>CFBundleIdentifier</key>
  <string>local.darsmanager.launcher</string>
  <key>CFBundleName</key>
  <string>Dars Manager</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0.3</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>10.15</string>
</dict>
</plist>
EOF

cat > "$LAUNCHER_MACOS/launch" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/Applications/DarsManager"

osascript <<OSA
tell application "Terminal"
  activate
  do script "cd " & quoted form of "$APP_DIR" & " && ./drsm_mac.command"
end tell
OSA
EOF

chmod +x "$LAUNCHER_MACOS/launch"

cat > "$SCRIPTS_DIR/preinstall" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

rm -rf "/Applications/DarsManager" "/Applications/Dars Manager.app"

exit 0
EOF

cat > "$SCRIPTS_DIR/postinstall" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/Applications/DarsManager"
CONSOLE_USER="$(stat -f "%Su" /dev/console || true)"

chmod +x "$APP_DIR/drsm_mac.command" "$APP_DIR/install_macos.command" "$APP_DIR/drsm_local.sh" || true
chmod +x "/Applications/Dars Manager.app/Contents/MacOS/launch" 2>/dev/null || true
rm -rf "$APP_DIR/.venv" 2>/dev/null || true
xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true
xattr -dr com.apple.quarantine "/Applications/Dars Manager.app" 2>/dev/null || true

if grep -q 'VENV_DIR="$APP_DIR/.venv"' "$APP_DIR/drsm_mac.command" 2>/dev/null; then
  echo "Ancien lanceur detecte apres installation." >&2
  exit 1
fi

if [ -n "$CONSOLE_USER" ] && [ "$CONSOLE_USER" != "root" ]; then
  USER_HOME="$(dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
  if [ -n "$USER_HOME" ] && [ -d "$USER_HOME/Desktop" ]; then
    USER_STATE="$USER_HOME/Library/Application Support/DarsManager"
    USER_DATA="$USER_HOME/Documents/DarsManager"
    mkdir -p "$USER_STATE" "$USER_DATA"
    chown -R "$CONSOLE_USER" "$USER_STATE" "$USER_DATA" || true

    LAUNCHER="$USER_HOME/Desktop/Dars Manager.command"
    cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$APP_DIR"
./drsm_mac.command
LAUNCHER_EOF
    chmod +x "$LAUNCHER"
    chown "$CONSOLE_USER" "$LAUNCHER" || true
    xattr -d com.apple.quarantine "$LAUNCHER" 2>/dev/null || true
  fi
fi

exit 0
EOF

chmod +x "$SCRIPTS_DIR/preinstall" "$SCRIPTS_DIR/postinstall"

pkgbuild \
  --root "$PAYLOAD_DIR" \
  --scripts "$SCRIPTS_DIR" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  --install-location "/" \
  "$OUT_PKG"

if [ -n "${MACOS_INSTALLER_CERT:-}" ]; then
  if ! command -v productsign >/dev/null 2>&1; then
    echo "productsign est introuvable, signature impossible." >&2
    exit 1
  fi
  productsign --sign "$MACOS_INSTALLER_CERT" "$OUT_PKG" "$SIGNED_PKG"
  mv "$SIGNED_PKG" "$OUT_PKG"
fi

if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
  if ! command -v xcrun >/dev/null 2>&1; then
    echo "xcrun est introuvable, notarisation impossible." >&2
    exit 1
  fi
  xcrun notarytool submit "$OUT_PKG" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" \
    --wait
  xcrun stapler staple "$OUT_PKG"
fi

echo "Package cree: $OUT_PKG"
echo
echo "Pour signer le package:"
echo "  MACOS_INSTALLER_CERT='Developer ID Installer: ...' ./build_macos_pkg.sh $VERSION"
echo
echo "Pour signer et notariser:"
echo "  MACOS_INSTALLER_CERT='Developer ID Installer: ...' APPLE_ID='...' APPLE_TEAM_ID='...' APPLE_APP_PASSWORD='...' ./build_macos_pkg.sh $VERSION"
