#!/usr/bin/env python3
"""Build and package a native System Tricorder release artifact."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


def normalized_arch() -> str:
    machine = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine or "unknown")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slug",
        required=True,
        help="Platform/distribution name used in the release filename",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    dist_dir = root / "dist"
    build_dir = root / "build"
    release_dir = root / "release"
    shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.rmtree(build_dir, ignore_errors=True)
    release_dir.mkdir(exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--windowed",
        "--onefile",
        "--name",
        "SystemTricorder",
        "--add-data",
        f"assets/SystemTricorder.png{os.pathsep}assets",
    ]
    if sys.platform == "win32":
        command.extend(["--icon", "assets/SystemTricorder.ico"])
    command.append("system_tricorder.py")
    subprocess.run(command, cwd=root, check=True)

    arch = normalized_arch()
    if sys.platform == "win32":
        source = dist_dir / "SystemTricorder.exe"
        artifact = release_dir / f"SystemTricorder-{args.slug}-{arch}.exe"
        shutil.copy2(source, artifact)
    else:
        app_bundle = dist_dir / "SystemTricorder.app"
        source = app_bundle if app_bundle.exists() else dist_dir / "SystemTricorder"
        artifact = release_dir / f"SystemTricorder-{args.slug}-{arch}.tar.gz"
        with tarfile.open(artifact, "w:gz") as archive:
            archive.add(source, arcname=source.name)

    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise RuntimeError(f"Release artifact was not created: {artifact}")
    print(f"Created {artifact.relative_to(root)} ({artifact.stat().st_size / 1048576:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
