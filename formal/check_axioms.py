#!/usr/bin/env python3
"""Verify that all Lean 4 proofs in `formal/` use zero `sorry` and only
standard Lean axioms (`propext`, `Classical.choice`, `Quot.sound`)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
MIN_EXPECTED_THEOREMS = 26

FORMAL_DIR = Path(__file__).resolve().parent
TRICORDER_DIR = FORMAL_DIR / "Tricorder"


def _resolve_lake() -> str:
    found = shutil.which("lake")
    if found:
        return found
    elan_bin = Path.home() / ".elan" / "bin"
    for name in ("lake.exe", "lake"):
        candidate = elan_bin / name
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("`lake` executable not found on PATH or in ~/.elan/bin")


def _check_no_raw_sorry() -> None:
    for lean_file in sorted(TRICORDER_DIR.glob("*.lean")):
        if lean_file.name == "CheckAxioms.lean":
            continue
        text = lean_file.read_text(encoding="utf-8")
        # Strip line comments (-- ...) and block comments (/- ... -/)
        stripped = re.sub(r"/-.*?-/", "", text, flags=re.S)
        stripped = re.sub(r"--[^\n]*", "", stripped)
        if re.search(r"\bsorry\b", stripped):
            raise AssertionError(f"Found `sorry` in {lean_file}")
        if re.search(r"\baxiom\b", stripped):
            raise AssertionError(f"Found custom `axiom` declaration in {lean_file}")


def _check_printed_axioms() -> int:
    lake = _resolve_lake()
    env = os.environ.copy()
    elan_bin = str(Path.home() / ".elan" / "bin")
    env["PATH"] = elan_bin + os.pathsep + env.get("PATH", "")

    proc = subprocess.run(
        [lake, "env", "lean", "Tricorder/CheckAxioms.lean"],
        cwd=str(FORMAL_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"CheckAxioms.lean failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )

    output = proc.stdout + "\n" + proc.stderr
    # Join wrapped `[propext,\n Classical.choice,\n Quot.sound]` lines
    normalized = re.sub(r",\s*\n\s*", ", ", output)

    checked = 0
    for line in normalized.splitlines():
        line = line.strip()
        if not line:
            continue
        m_none = re.fullmatch(
            r"'([^']+)' does not depend on any axioms", line
        )
        if m_none:
            checked += 1
            continue
        m_deps = re.fullmatch(
            r"'([^']+)' depends on axioms: \[([^\]]*)\]", line
        )
        if m_deps:
            theorem = m_deps.group(1)
            axioms = {
                a.strip() for a in m_deps.group(2).split(",") if a.strip()
            }
            disallowed = axioms - ALLOWED_AXIOMS
            if disallowed:
                raise AssertionError(
                    f"Theorem `{theorem}` depends on disallowed axioms: "
                    f"{sorted(disallowed)}"
                )
            checked += 1

    if checked < MIN_EXPECTED_THEOREMS:
        raise AssertionError(
            f"Expected at least {MIN_EXPECTED_THEOREMS} verified theorems, "
            f"found {checked}.\nOutput:\n{output}"
        )
    return checked


def main() -> int:
    _check_no_raw_sorry()
    count = _check_printed_axioms()
    print(
        f"OK: {count} Lean 4 theorems verified with 0 `sorry` and "
        f"only standard axioms ({', '.join(sorted(ALLOWED_AXIOMS))})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
