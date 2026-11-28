# Formal Verification (`formal/`)

This directory contains a lightweight, self-contained **Lean 4** verification project (Core/`Std` only — no `Mathlib`, builds in a few seconds) coupled to [`system_tricorder.py`](../system_tricorder.py) via an executable JSON oracle and Hypothesis differential tests.

End-user installations and `requirements.txt` remain completely free of Lean or testing dependencies.

## Verified Modules & Theorems (26 Main Theorems, 0 `sorry`)

| Module | Python Equivalent | Proven Properties |
| :--- | :--- | :--- |
| [`Tricorder/Layout.lean`](Tricorder/Layout.lean) | `_tile_rows`, `_flatten_tile_rows`, `_move_tile_to_new_row`, `_merge_layout_tiles`, `_preserve_dormant_layout` | Canonical `__row__` normal form, duplicate-freedom, disjointness & full partition of valid tile IDs, **idempotency** of layout migration, tile-set preservation across row moves, and lossless dormant-hardware preservation. |
| [`Tricorder/Updater.lean`](Tricorder/Updater.lean) | `UpdateWorker`, `_build_standalone_exe_update_bat`, `_cleanup_previous_exe_backup`, `_install_staged_source_files` | **Crash-safety recoverability** (never loses both executable versions even after failure/crash at any step), no unvalidated release in `exe`, backup removed only after commit, atomic all-or-nothing multi-file commit, clean & resilient rollback, and backup-cleanup target isolation. |
| [`Tricorder/Power.lean`](Tricorder/Power.lean) | `_cpu_package_power_from_pdh`, `_linux_rapl_energy_delta` | Duplicate-name deduplication, **single-tier exclusivity** (no double counting across package/PP0/DRAM/core domains), core-fallback exclusivity, non-positive filtering, and non-negative bounded Linux RAPL counter wrap-around. |
| [`Tricorder/Config.lean`](Tricorder/Config.lean) | `_normalise_theme`, `_normalise_autostart_mode`, `_normalise_color_hex`, `_normalise_min_row_h`, `_sanitise_config` | **Total config sanitizer** producing a dependent `ValidConfig` with embedded bounds/format/uniqueness proofs, color-hex idempotency, and valid-config preservation. |
| [`Oracle/Main.lean`](Oracle/Main.lean) | — | Compiles to `tricorder-oracle`, used by [`tests/test_formal_differential.py`](../tests/test_formal_differential.py) to compare thousands of Hypothesis-generated inputs against `system_tricorder.py` (including real-filesystem fault injection for source updates). |

## Trusted Computing Base (What Is *Not* Proven by Lean)

Lean 4 verifies the formal models and protocols above, while Hypothesis differential tests empirically verify parity with the pure Python core. The following remain outside Lean's TCB (explicit assumptions):

* Operating system & filesystem semantics (NTFS/ext4/APFS atomic rename on one volume, `fsync`, Windows file locks, antivirus interference, `cmd.exe` script execution).
* CPython runtime, GIL, PyQt6 rendering, and PyInstaller bootloader.
* Native driver DLLs and OS thread scheduling (PDH, DXGI, ADLX, NVML, `psutil`).

## Running Locally

Requires [`elan`](https://github.com/leanprover/elan) (which automatically installs the exact Lean 4 version pinned in [`lean-toolchain`](lean-toolchain)):

```bash
# 1. Build all Lean proofs and the native JSON oracle
cd formal
lake build

# 2. Verify zero `sorry` and standard Lean axioms only
cd ..
python formal/check_axioms.py

# 3. Run Hypothesis differential tests against `system_tricorder.py`
pip install pytest hypothesis
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_formal_differential.py -v
```
