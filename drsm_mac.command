#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
WORK_DIR="${DRSM_WORK_DIR:-$HOME/Documents/DarsManager}"
STAMP_FILE="$VENV_DIR/.drsm_requirements.sha256"

cd "$APP_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 est introuvable."
  echo "Installe Python depuis https://www.python.org/downloads/macos/ puis relance ce fichier."
  read -r -p "Appuie sur Entrée pour fermer..."
  exit 1
fi

mkdir -p "$WORK_DIR"

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

CURRENT_REQUIREMENTS="$(shasum -a 256 requirements.txt | awk '{print $1}')"
INSTALLED_REQUIREMENTS=""
if [ -f "$STAMP_FILE" ]; then
  INSTALLED_REQUIREMENTS="$(cat "$STAMP_FILE")"
fi

if [ "$CURRENT_REQUIREMENTS" != "$INSTALLED_REQUIREMENTS" ]; then
  echo "Installation ou mise à jour des composants..."
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r requirements.txt
  echo "$CURRENT_REQUIREMENTS" > "$STAMP_FILE"
fi

export DRSM_WORK_DIR="$WORK_DIR"
export DRSM_CLOUD_SAFE_DEFAULT=false
export HF_HOME="$WORK_DIR/hf_cache"
export XDG_CACHE_HOME="$WORK_DIR/cache"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

echo
echo "Dars Manager démarre..."
echo "Données locales: $WORK_DIR"
echo "Adresse: http://localhost:8501"
echo

if command -v open >/dev/null 2>&1; then
  (sleep 3 && open "http://localhost:8501") >/dev/null 2>&1 &
fi

"$VENV_DIR/bin/python" -m streamlit run drsm_streamlit.py --server.address=localhost --server.port=8501 --browser.gatherUsageStats=false
