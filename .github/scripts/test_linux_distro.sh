#!/usr/bin/env bash
# Install native runtime prerequisites, run the shared smoke suite, and
# optionally build a distribution-specific PyInstaller artifact.
set -euo pipefail

if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    echo "Testing ${PRETTY_NAME:-${ID:-unknown Linux}}"
fi

install_apt_dependencies() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    local glib_package="libglib2.0-0"
    if apt-cache show libglib2.0-0t64 >/dev/null 2>&1; then
        glib_package="libglib2.0-0t64"
    fi
    apt-get install -y --no-install-recommends \
        binutils ca-certificates fontconfig libpython3 python3 python3-pip python3-venv \
        libdbus-1-3 libegl1 libgl1 "$glib_package" \
        libxkbcommon-x11-0 libxcb-cursor0
    rm -rf /var/lib/apt/lists/*
}

install_dnf_dependencies() {
    dnf install -y \
        binutils ca-certificates fontconfig glib2 python3 python3-pip \
        dbus-libs libglvnd-egl libglvnd-glx libxkbcommon-x11 xcb-util-cursor
    dnf clean all
}

install_pacman_dependencies() {
    pacman -Syu --noconfirm --needed \
        binutils ca-certificates fontconfig glib2 python python-pip \
        dbus libglvnd libxkbcommon-x11 xcb-util-cursor
    pacman -Scc --noconfirm
}

if command -v apt-get >/dev/null 2>&1; then
    install_apt_dependencies
elif command -v dnf >/dev/null 2>&1; then
    install_dnf_dependencies
elif command -v pacman >/dev/null 2>&1; then
    install_pacman_dependencies
else
    echo "Unsupported package manager in CI image" >&2
    exit 1
fi

rm -rf .ci-venv build dist
python3 -m venv .ci-venv
# shellcheck disable=SC1091
source .ci-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pytest

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
python -m pytest tests/test_smoke.py -v --tb=short

if [[ "${BUILD_PACKAGE:-0}" == "1" ]]; then
    if [[ -z "${BUILD_SLUG:-}" ]]; then
        echo "BUILD_SLUG must be set when BUILD_PACKAGE=1" >&2
        exit 1
    fi
    python -m pip install pyinstaller
    python .github/scripts/build_release.py --slug "$BUILD_SLUG"
fi
