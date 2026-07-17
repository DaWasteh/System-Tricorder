#!/usr/bin/env bash

# System Tricorder Ubuntu/Linux Starter
# Detaches itself when launched from a terminal so closing that terminal cannot
# accidentally terminate the app. Output goes to ~/.local/state/system-tricorder.

set -euo pipefail

APP_NAME="System Tricorder"
PYTHON_BIN="python3"
VENV_DIR=".venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/system-tricorder"
LOG_FILE="$LOG_DIR/start_linux.log"

cd "$SCRIPT_DIR"

# If this script was started in a terminal (e.g. double-clicked as "Run in
# Terminal"), restart it as a detached background process and close the
# terminal immediately. The detached child ignores SIGHUP and logs everything.
if [[ -t 0 && "${SYSTEM_TRICORDER_DETACHED:-0}" != "1" ]]; then
    mkdir -p "$LOG_DIR"
    echo "🚀 Starting $APP_NAME in the background..."
    echo "📝 Log: $LOG_FILE"
    nohup env SYSTEM_TRICORDER_DETACHED=1 "$0" "$@" </dev/null >>"$LOG_FILE" 2>&1 &
    disown 2>/dev/null || true
    exit 0
fi

mkdir -p "$LOG_DIR"
echo "🚀 Starting $APP_NAME on Linux..."

# 1. Check for Python 3
if ! command -v "$PYTHON_BIN" &>/dev/null; then
    echo "❌ Error: $PYTHON_BIN not found. Please install Python 3."
    exit 1
fi

# 2. Ensure venv is installed
if ! "$PYTHON_BIN" -m venv --help &>/dev/null; then
    echo "⚠️  Python venv module not found."
    if command -v apt-get &>/dev/null; then
        echo "Please run: sudo apt update && sudo apt install python3-venv python3-pip"
    elif command -v dnf &>/dev/null; then
        echo "Please run: sudo dnf install python3 python3-pip"
    elif command -v pacman &>/dev/null; then
        echo "Please run: sudo pacman -Syu python python-pip"
    else
        echo "Please install Python 3 with its venv and pip modules."
    fi
    exit 1
fi

# 3. Create virtual environment if it doesn't exist
if [[ ! -d "$VENV_DIR" ]]; then
    echo "📦 Creating virtual environment..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# 4. Activate venv and install dependencies
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "⚙️  Installing dependencies..."
# requirements.txt uses an environment marker so Windows-only pywin32 is
# skipped automatically on every Linux distribution.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 5. Run the application
echo "🌟 Launching $APP_NAME..."
exec python system_tricorder.py
