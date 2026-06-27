#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
WORK_DIR="${DRSM_WORK_DIR:-$HOME/Documents/DarsManager}"

cd "$APP_DIR"
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
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

"$VENV_DIR/bin/python" -m streamlit run drsm_streamlit.py --server.address=localhost --server.port=8501 --browser.gatherUsageStats=false
