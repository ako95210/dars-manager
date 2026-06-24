#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
WORK_DIR="${DRSM_WORK_DIR:-$HOME/Documents/DarsManager}"

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

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

export DRSM_WORK_DIR="$WORK_DIR"
export DRSM_CLOUD_SAFE_DEFAULT=false
export HF_HOME="$WORK_DIR/hf_cache"
export XDG_CACHE_HOME="$WORK_DIR/cache"

echo
echo "Dars Manager démarre..."
echo "Données locales: $WORK_DIR"
echo "Adresse: http://localhost:8501"
echo

"$VENV_DIR/bin/python" -m streamlit run drsm_streamlit.py --server.address=localhost --server.port=8501
