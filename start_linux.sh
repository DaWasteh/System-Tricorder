#!/bin/bash

# System Tricorder Ubuntu 26.04 LTS Starter
# This script sets up a virtual environment and starts the System Tricorder.

APP_NAME="System Tricorder"
PYTHON_BIN="python3"
VENV_DIR=".venv"

echo "🚀 Starting $APP_NAME on Ubuntu..."

# 1. Check for Python 3
if ! command -v $PYTHON_BIN &> /dev/null; then
    echo "❌ Error: $PYTHON_BIN not found. Please install Python 3."
    exit 1
fi

# 2. Ensure venv is installed
if ! $PYTHON_BIN -m venv --help &> /dev/null; then
    echo "⚠️  Python venv module not found."
    echo "Please run: sudo apt update && sudo apt install python3-venv python3-pip"
    exit 1
fi

# 3. Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    $PYTHON_BIN -m venv $VENV_DIR
fi

# 4. Activate venv and install dependencies
source $VENV_DIR/bin/activate

echo "⚙️  Installing dependencies..."
# Filter out pywin32 for Linux
grep -v "pywin32" requirements.txt > requirements_linux.txt
pip install --upgrade pip
pip install -r requirements_linux.txt

# 5. Run the application
echo "🌟 Launching $APP_NAME..."
python system_tricorder.py
