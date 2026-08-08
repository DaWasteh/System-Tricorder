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
    generated_spec_dir = build_dir / "generated-spec"
    generated_spec_dir.mkdir(parents=True, exist_ok=True)

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
        "--specpath",
        str(generated_spec_dir),
        "--add-data",
        f"{root / 'assets' / 'SystemTricorder.png'}{os.pathsep}assets",
    ]
    if sys.platform == "win32":
        command.extend([
            "--icon", str(root / "assets" / "SystemTricorder.ico"),
            "--version-file", str(root / "assets" / "version_info.txt"),
        ])
    command.append(str(root / "system_tricorder.py"))
    subprocess.run(command, cwd=root, check=True)

    arch = normalized_arch()
    if sys.platform == "win32":
        source = dist_dir / "SystemTricorder.exe"
        smoke_executable = source
        artifact = release_dir / f"SystemTricorder-{args.slug}-{arch}.exe"
    else:
        app_bundle = dist_dir / "SystemTricorder.app"
        source = app_bundle if app_bundle.exists() else dist_dir / "SystemTricorder"
        smoke_executable = (
            app_bundle / "Contents" / "MacOS" / "SystemTricorder"
            if app_bundle.exists() else source
        )
        artifact = release_dir / f"SystemTricorder-{args.slug}-{arch}.tar.gz"

    smoke_home = build_dir / "self-test-home"
    smoke_home.mkdir(parents=True, exist_ok=True)
    smoke_env = os.environ.copy()
    smoke_env.update({"HOME": str(smoke_home), "USERPROFILE": str(smoke_home)})
    try:
        subprocess.run(
            [str(smoke_executable), "--self-test"], cwd=root, env=smoke_env,
            check=True, timeout=60,
        )
    finally:
        shutil.rmtree(smoke_home, ignore_errors=True)
    if sys.platform == "win32":
        shutil.copy2(source, artifact)
    else:
        with tarfile.open(artifact, "w:gz") as archive:
            archive.add(source, arcname=source.name)

    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise RuntimeError(f"Release artifact was not created: {artifact}")
    print(f"Created {artifact.relative_to(root)} ({artifact.stat().st_size / 1048576:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
