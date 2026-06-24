#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/Applications/DarsManager"
DESKTOP_DIR="$HOME/Desktop"
LAUNCHER="$DESKTOP_DIR/Dars Manager.command"

echo
echo "Installation de Dars Manager"
echo "----------------------------"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 est nécessaire."
  echo "Une page de téléchargement va s'ouvrir."
  echo "Installe Python, puis relance ce fichier."
  if command -v open >/dev/null 2>&1; then
    open "https://www.python.org/downloads/macos/"
  fi
  read -r -p "Appuie sur Entrée pour fermer..."
  exit 1
fi

mkdir -p "$INSTALL_DIR" "$DESKTOP_DIR"

if [ "$SOURCE_DIR" != "$INSTALL_DIR" ]; then
  echo "Copie de l'application dans $INSTALL_DIR..."
  rsync -a --delete \
    --exclude ".git" \
    --exclude ".venv" \
    --exclude "__pycache__" \
    --exclude "work" \
    "$SOURCE_DIR/" "$INSTALL_DIR/"
fi

chmod +x "$INSTALL_DIR/drsm_mac.command" || true
chmod +x "$INSTALL_DIR/drsm_local.sh" || true

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$INSTALL_DIR"
./drsm_mac.command
EOF

chmod +x "$LAUNCHER"

echo
echo "Préparation des composants Python..."
"$INSTALL_DIR/drsm_mac.command"
