#!/usr/bin/env bash
set -euo pipefail

APP_NAME="DarsManager"
IDENTIFIER="local.darsmanager.app"
VERSION="${1:-0.9}"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$ROOT_DIR/dist/macos-pkg-build"
PAYLOAD_DIR="$BUILD_DIR/payload"
SCRIPTS_DIR="$BUILD_DIR/scripts"
APP_DIR="$PAYLOAD_DIR/Applications/DarsManager"
OUT_PKG="$ROOT_DIR/dist/dars-manager-v${VERSION}-mac.pkg"

if ! command -v pkgbuild >/dev/null 2>&1; then
  echo "pkgbuild est introuvable. Ce script doit etre lance sur macOS avec Xcode Command Line Tools." >&2
  exit 1
fi

rm -rf "$BUILD_DIR" "$OUT_PKG"
mkdir -p "$APP_DIR" "$SCRIPTS_DIR"

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

cat > "$SCRIPTS_DIR/postinstall" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/Applications/DarsManager"
CONSOLE_USER="$(stat -f "%Su" /dev/console || true)"

chmod +x "$APP_DIR/drsm_mac.command" "$APP_DIR/install_macos.command" "$APP_DIR/drsm_local.sh" || true
xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true

if [ -n "$CONSOLE_USER" ] && [ "$CONSOLE_USER" != "root" ]; then
  USER_HOME="$(dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
  if [ -n "$USER_HOME" ] && [ -d "$USER_HOME/Desktop" ]; then
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

chmod +x "$SCRIPTS_DIR/postinstall"

pkgbuild \
  --root "$PAYLOAD_DIR" \
  --scripts "$SCRIPTS_DIR" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  --install-location "/" \
  "$OUT_PKG"

echo "Package cree: $OUT_PKG"
