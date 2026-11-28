"""Differential & property-based tests coupling `system_tricorder.py` to the
formally verified Lean 4 oracle (`formal/.lake/build/bin/tricorder-oracle`).

If the compiled Lean oracle binary is absent (e.g. on machines without `elan`),
oracle-comparison tests skip cleanly while standalone Hypothesis property tests
still execute.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import system_tricorder as tricorder

REPO_ROOT = Path(__file__).resolve().parent.parent
ORACLE_CANDIDATES = (
    REPO_ROOT / "formal" / ".lake" / "build" / "bin" / "tricorder-oracle.exe",
    REPO_ROOT / "formal" / ".lake" / "build" / "bin" / "tricorder-oracle",
)


def _find_oracle_binary() -> Path | None:
    for candidate in ORACLE_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


class LeanOracleClient:
    """Persistent JSON-lines subprocess client for `tricorder-oracle`."""

    def __init__(self, binary: Path) -> None:
        self._proc = subprocess.Popen(
            [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def call(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"Lean oracle exited unexpectedly: {err}")
        data = json.loads(line)
        if "error" in data:
            raise AssertionError(f"Lean oracle returned error: {data['error']}")
        return data

    def close(self) -> None:
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        self._proc.wait(timeout=5)


@pytest.fixture(scope="module")
def oracle() -> LeanOracleClient:
    binary = _find_oracle_binary()
    if binary is None:
        pytest.skip("Lean 4 oracle binary not built (run `cd formal && lake build`)")
    client = LeanOracleClient(binary)
    try:
        yield client
    finally:
        client.close()


# ── Shared Hypothesis strategies ──────────────────────────────────────────────

TILE_UNIVERSE = [
    "cpu_total",
    "cpu_power",
    "ram",
    "igpu",
    "gpu_0_total",
    "gpu_0_3d",
    "gpu_0_vram",
    "gpu_0_power",
    "gpu_1_total",
    "gpu_1_3d",
    "gpu_1_vram",
    "gpu_1_power",
    "disk_total",
    "drive_0",
    "drive_1",
    "optional_a",
    "optional_b",
]

TILE_OR_SENTINEL = st.sampled_from([*TILE_UNIVERSE, "__row__", "stale_gpu_9", "unknown_x"])
VALID_TILE_IDS = st.lists(st.sampled_from(TILE_UNIVERSE), min_size=0, max_size=12, unique=True)

PDH_COUNTER_NAMES = [
    "RAPL_Package0_PKG",
    "RAPL_Package1_PKG",
    "RAPL_Package0_PKG#1",
    "RAPL_Package0_PP0",
    "RAPL_Package0_DRAM",
    "Current Socket Power",
    "Current Socket Energy",
    "Socket Power",
    "CPU Package Power",
    "CPU Power",
    "CPU Power#1",
    "CPU Power#2",
    "Apu Power",
    "Apu Energy",
    "RAPL_Package0_Core0_CORE",
    "RAPL_Package0_Core1_CORE",
    "RAPL_Package0_Core2_CORE",
    "RAPL_Package0_Core0_CORE#1",
    "Unknown Sensor",
    "   ",
    "###",
]


def _is_canonical_order(order: list[str]) -> bool:
    if not order:
        return True
    if order[0] == "__row__" or order[-1] == "__row__":
        return False
    return all(
        not (order[i] == "__row__" and order[i + 1] == "__row__")
        for i in range(len(order) - 1)
    )


# ── 1. Layout & Migration differential tests ──────────────────────────────────

@settings(max_examples=500, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    saved_order=st.lists(TILE_OR_SENTINEL, max_size=18),
    saved_hidden=st.lists(TILE_OR_SENTINEL, max_size=10),
    tile_ids=VALID_TILE_IDS,
    default_order=st.lists(TILE_OR_SENTINEL, max_size=14),
)
def test_diff_layout_merge_matches_lean_oracle(
    oracle: LeanOracleClient,
    saved_order: list[str],
    saved_hidden: list[str],
    tile_ids: list[str],
    default_order: list[str],
) -> None:
    py_order, py_hidden, py_changed = tricorder._merge_layout_tiles(
        saved_order, saved_hidden, tile_ids, default_order
    )
    lean = oracle.call({
        "op": "layout_merge",
        "saved_order": saved_order,
        "saved_hidden": saved_hidden,
        "tile_ids": tile_ids,
        "default_order": default_order,
    })
    assert py_order == lean["order"]
    assert py_hidden == lean["hidden"]
    assert py_changed == lean["changed"]

    # Check proven invariants directly on Python output
    assert _is_canonical_order(py_order)
    vis = [t for t in py_order if t != "__row__"]
    assert len(vis) == len(set(vis))
    assert len(py_hidden) == len(set(py_hidden))
    assert set(vis).isdisjoint(set(py_hidden))
    assert set(vis) | set(py_hidden) == set(tile_ids)

    # Idempotency
    again_order, again_hidden, again_changed = tricorder._merge_layout_tiles(
        py_order, py_hidden, tile_ids, default_order
    )
    assert (again_order, again_hidden, again_changed) == (py_order, py_hidden, False)


@settings(max_examples=500, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    active_ids=VALID_TILE_IDS,
    stored_order=st.lists(TILE_OR_SENTINEL, max_size=18),
    stored_hidden=st.lists(TILE_OR_SENTINEL, max_size=10),
    active_hidden_extra=st.lists(TILE_OR_SENTINEL, max_size=8),
)
def test_diff_layout_dormant_matches_lean_oracle(
    oracle: LeanOracleClient,
    active_ids: list[str],
    stored_order: list[str],
    stored_hidden: list[str],
    active_hidden_extra: list[str],
) -> None:
    active_order, active_hidden, _ = tricorder._merge_layout_tiles(
        stored_order, active_hidden_extra, active_ids, active_ids
    )
    py_order, py_hidden = tricorder._preserve_dormant_layout(
        active_order, active_hidden, stored_order, stored_hidden, set(active_ids)
    )
    lean = oracle.call({
        "op": "layout_dormant",
        "active_order": active_order,
        "active_hidden": active_hidden,
        "stored_order": stored_order,
        "stored_hidden": stored_hidden,
        "active_ids": active_ids,
    })
    assert py_order == lean["order"]
    assert py_hidden == lean["hidden"]
    assert _is_canonical_order(py_order)
    vis = [t for t in py_order if t != "__row__"]
    assert len(vis) == len(set(vis))
    assert len(py_hidden) == len(set(py_hidden))
    assert set(vis).isdisjoint(set(py_hidden))


@settings(max_examples=500, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    raw_order=st.lists(TILE_OR_SENTINEL, max_size=14),
    tile_id=TILE_OR_SENTINEL,
    after_row_idx=st.integers(min_value=-3, max_value=10),
)
def test_diff_layout_move_matches_lean_oracle(
    oracle: LeanOracleClient,
    raw_order: list[str],
    tile_id: str,
    after_row_idx: int,
) -> None:
    # Build a canonical, duplicate-free starting order (as maintained by TileGrid)
    order, _, _ = tricorder._merge_layout_tiles(
        raw_order, [], TILE_UNIVERSE, []
    )
    py_order = tricorder._move_tile_to_new_row(order, tile_id, after_row_idx)
    lean = oracle.call({
        "op": "layout_move",
        "tile_order": order,
        "tile_id": tile_id,
        "after_row_idx": after_row_idx,
    })
    assert py_order == lean["order"]
    assert _is_canonical_order(py_order)
    assert {t for t in py_order if t != "__row__"} == {t for t in order if t != "__row__"}


# ── 2. Power Aggregation & RAPL differential tests ────────────────────────────

@settings(max_examples=500, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    rows=st.lists(
        st.tuples(
            st.sampled_from(PDH_COUNTER_NAMES),
            st.integers(min_value=-5_000, max_value=500_000),
        ),
        max_size=15,
    )
)
def test_diff_power_pdh_matches_lean_oracle(
    oracle: LeanOracleClient,
    rows: list[tuple[str, int]],
) -> None:
    py_watts = tricorder._cpu_package_power_from_pdh([(n, float(v)) for n, v in rows])
    lean = oracle.call({
        "op": "power_pdh",
        "rows": [[n, v] for n, v in rows],
    })
    lean_mw = lean["power_mw"]
    if lean_mw is None:
        assert py_watts is None
    else:
        assert py_watts is not None
        assert py_watts == pytest.approx(lean_mw / 1000.0)


@settings(max_examples=500, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    prev_uj=st.integers(min_value=-100, max_value=10_000_000),
    curr_uj=st.integers(min_value=-100, max_value=10_000_000),
    max_range=st.one_of(st.none(), st.integers(min_value=-10, max_value=10_000_000)),
)
def test_diff_power_rapl_matches_lean_oracle(
    oracle: LeanOracleClient,
    prev_uj: int,
    curr_uj: int,
    max_range: int | None,
) -> None:
    py_delta = tricorder._linux_rapl_energy_delta(prev_uj, curr_uj, max_range)
    lean = oracle.call({
        "op": "power_rapl",
        "prev_uj": prev_uj,
        "curr_uj": curr_uj,
        "max_range": max_range,
    })
    assert py_delta == lean["delta_uj"]
    if py_delta is not None:
        assert py_delta >= 0


# ── 3. Configuration Sanitizer differential tests ─────────────────────────────

CORRUPT_STR = st.one_of(
    st.none(),
    st.sampled_from([
        "dark", "DARK", " light ", "LIGHT", "normal", "MAXIMIZED", "minimized",
        "broken", "", "#AbC", "#123456", "  #00ff88  ", "#gggggg", "rgb(1,2,3)", "red",
    ]),
)
CORRUPT_BOOL = st.one_of(st.none(), st.booleans(), st.sampled_from(["true", "false", 0, 1]))
CORRUPT_MIN_ROW_H = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-500, max_value=2000),
    st.sampled_from(["", "abc", "75", " 120 ", "-40", "999", "+250", "12.5"]),
)
COLOR_ENTRY = st.tuples(
    st.sampled_from(["", "cpu_total", "ram", "gpu_0_3d", "a" * 257]),
    CORRUPT_STR,
)


@settings(max_examples=500, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    theme=CORRUPT_STR,
    minimize_to_tray=CORRUPT_BOOL,
    close_to_tray=CORRUPT_BOOL,
    autostart=CORRUPT_BOOL,
    autostart_mode=CORRUPT_STR,
    min_row_h=CORRUPT_MIN_ROW_H,
    color_entries=st.lists(COLOR_ENTRY, max_size=8),
)
def test_diff_config_sanitize_matches_lean_oracle(
    oracle: LeanOracleClient,
    theme: Any,
    minimize_to_tray: Any,
    close_to_tray: Any,
    autostart: Any,
    autostart_mode: Any,
    min_row_h: Any,
    color_entries: list[tuple[str, Any]],
) -> None:
    # Build a Python dict with the exact last-key-wins semantics of JSON objects
    tile_colors_dict: dict[str, Any] = {}
    for k, v in color_entries:
        tile_colors_dict[k] = v
    raw_cfg = {
        "theme": theme,
        "minimize_to_tray": minimize_to_tray,
        "close_to_tray": close_to_tray,
        "autostart": autostart,
        "autostart_mode": autostart_mode,
        "min_row_h": min_row_h,
        "tile_colors": tile_colors_dict,
    }
    py_sanitised = tricorder._sanitise_config(raw_cfg)
    lean = oracle.call({
        "op": "config_sanitize",
        "theme": theme if isinstance(theme, str) else None,
        "minimize_to_tray": minimize_to_tray if isinstance(minimize_to_tray, bool) else None,
        "close_to_tray": close_to_tray if isinstance(close_to_tray, bool) else None,
        "autostart": autostart if isinstance(autostart, bool) else None,
        "autostart_mode": autostart_mode if isinstance(autostart_mode, str) else None,
        "min_row_h": min_row_h,
        "tile_colors": [[k, v] for k, v in tile_colors_dict.items()],
    })
    assert py_sanitised == lean
    assert 50 <= py_sanitised["min_row_h"] <= 400
    assert py_sanitised["max_row_h"] >= py_sanitised["min_row_h"]


# ── 4. Updater Crash-Safety differential tests ────────────────────────────────

EXE_EVENTS = st.one_of(
    st.sampled_from([
        {"type": "start_download"},
        {"type": "finish_download"},
        {"type": "download_fail"},
        {"type": "stage_copy_ok"},
        {"type": "stage_copy_fail"},
        {"type": "guard_and_clear_old_backup"},
        {"type": "move_exe_to_backup_ok"},
        {"type": "move_exe_to_backup_fail"},
        {"type": "move_new_to_exe_ok"},
        {"type": "move_new_to_exe_fail"},
        {"type": "launch_new_ok"},
        {"type": "launch_new_fail"},
        {"type": "rollback_restore_ok"},
        {"type": "rollback_restore_fail"},
        {"type": "commit_cleanup_backup"},
        {"type": "crash"},
    ]),
    st.builds(
        lambda sz, sh, pe, st_ok: {
            "type": "validate",
            "size_ok": sz,
            "sha256_ok": sh,
            "pe_version_ok": pe,
            "self_test_ok": st_ok,
        },
        st.booleans(),
        st.booleans(),
        st.booleans(),
        st.booleans(),
    ),
)


@settings(max_examples=500, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(events=st.lists(EXE_EVENTS, max_size=25))
def test_diff_updater_exe_traces_always_recoverable(
    oracle: LeanOracleClient,
    events: list[dict[str, Any]],
) -> None:
    lean = oracle.call({
        "op": "updater_exe_trace",
        "events": events,
    })
    assert lean["recoverable"] is True
    if lean["exe"] == "verified_new":
        assert lean["flags_all_passed"] is True
    if lean["phase"] == "committed":
        assert lean["exe"] == "verified_new"
        assert lean["backup"] is None


def test_bat_updater_sequence_matches_lean_protocol_order() -> None:
    """Verify the generated Windows `.bat` helper preserves the exact guarded step order
    proven in `formal/Tricorder/Updater.lean`."""
    script = tricorder._build_standalone_exe_update_bat(
        Path("C:/Apps/SystemTricorder.exe"),
        Path("C:/Temp/system-tricorder-update-99/release.exe"),
        1234,
        Path("C:/Temp/update.log"),
    )
    ordered_steps = [
        'if not exist "%UPDATE%"',
        'copy /Y /B "%UPDATE%" "%NEW%"',
        'if not exist "%EXE%"',
        'del /F /Q "%BACKUP%"',
        'move /Y "%EXE%" "%BACKUP%"',
        'move /Y "%NEW%" "%EXE%"',
        'move /Y "%BACKUP%" "%EXE%"',
        'start "" "%EXE%"',
    ]
    positions = [script.index(step) for step in ordered_steps]
    assert positions == sorted(positions)
    # A second rollback block must also exist after `start "" "%EXE%"`
    start_pos = script.index('start "" "%EXE%"')
    assert script.index('move /Y "%BACKUP%" "%EXE%"', start_pos) > start_pos


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    existed_flags=st.lists(st.booleans(), min_size=3, max_size=3),
    fail_install_at=st.one_of(st.none(), st.integers(min_value=0, max_value=2)),
    fail_rollback_at=st.lists(st.integers(min_value=0, max_value=2), max_size=3, unique=True),
    fail_cleanup_at=st.lists(st.integers(min_value=0, max_value=2), max_size=3, unique=True),
)
def test_diff_source_install_real_filesystem_matches_lean_oracle(
    oracle: LeanOracleClient,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    existed_flags: list[bool],
    fail_install_at: int | None,
    fail_rollback_at: list[int],
    fail_cleanup_at: list[int],
) -> None:
    """Run `_install_staged_source_files` on a real filesystem under fault injection
    and assert the resulting file versions match the proven Lean model."""
    root = tmp_path_factory.mktemp("src_tx")
    install = root / "install"
    staging = root / "staging"
    install.mkdir()
    staging.mkdir()

    rel_files = list(tricorder._UPDATE_SOURCE_FILES)
    old_bytes = {rel: f"OLD:{rel}".encode("ascii") for rel in rel_files}
    new_bytes = {rel: f"NEW:{rel}".encode("ascii") for rel in rel_files}

    for rel, existed in zip(rel_files, existed_flags, strict=True):
        s_path = staging / rel
        s_path.parent.mkdir(parents=True, exist_ok=True)
        s_path.write_bytes(new_bytes[rel])
        if existed:
            t_path = install / rel
            t_path.parent.mkdir(parents=True, exist_ok=True)
            t_path.write_bytes(old_bytes[rel])

    name_to_idx = {Path(rel).name: idx for idx, rel in enumerate(rel_files)}
    orig_replace = Path.replace
    orig_unlink = Path.unlink

    def faulty_replace(self: Path, target: Path) -> Path:
        # Candidate -> Target (install failure)
        if ".update-" in self.name:
            base = self.name.lstrip(".").split(".update-")[0]
            if fail_install_at is not None and name_to_idx.get(base) == fail_install_at:
                raise OSError(f"injected install failure at {fail_install_at}")
        # Backup -> Target (rollback failure)
        if ".backup-" in self.name:
            base = self.name.lstrip(".").split(".backup-")[0]
            if name_to_idx.get(base) in fail_rollback_at:
                raise OSError(f"injected rollback failure at {base}")
        return orig_replace(self, target)

    def faulty_unlink(self: Path, missing_ok: bool = False) -> None:
        if ".backup-" in self.name and self.exists():
            base = self.name.lstrip(".").split(".backup-")[0]
            if name_to_idx.get(base) in fail_cleanup_at:
                raise OSError(f"injected cleanup failure at {base}")
        if fail_install_at is not None and self.name in name_to_idx:
            # Unlinking newly created target during rollback when rollback fails on that slot
            idx = name_to_idx[self.name]
            if idx in fail_rollback_at and not existed_flags[idx]:
                raise OSError(f"injected rollback unlink failure at {idx}")
        orig_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "replace", faulty_replace)
    monkeypatch.setattr(Path, "unlink", faulty_unlink)

    try:
        tricorder._install_staged_source_files(staging, install)
        py_outcome = "committed"
    except OSError:
        py_outcome = "rolled_back"
    finally:
        monkeypatch.undo()

    lean = oracle.call({
        "op": "updater_source_tx",
        "existed_flags": existed_flags,
        "fail_install_at": fail_install_at,
        "fail_rollback_at": fail_rollback_at,
        "fail_cleanup_at": fail_cleanup_at,
    })
    assert py_outcome == lean["outcome"]

    for idx, rel in enumerate(rel_files):
        t_path = install / rel
        expected_slot = lean["slots"][idx]
        if not t_path.exists():
            py_target = None
        else:
            raw = t_path.read_bytes()
            py_target = "old" if raw == old_bytes[rel] else "new"
        backups = list(t_path.parent.glob(f".{t_path.name}.backup-*"))
        py_backup = None
        if backups:
            braw = backups[0].read_bytes()
            py_backup = "old" if braw == old_bytes[rel] else "new"

        if py_outcome == "committed":
            assert py_target == "new"
            assert py_target == expected_slot["target"]
            assert py_backup == expected_slot["backup"]
        else:
            # For rolled-back transactions: verify safety invariant & exact match on
            # all slots that completed rollback (and backup preservation on failed rollback).
            if idx not in fail_rollback_at:
                assert py_target == expected_slot["target"]
                assert py_backup == expected_slot["backup"]
            elif existed_flags[idx]:
                # Originally existing slot must NEVER lose `.old` (either in target or backup)
                assert py_target == "old" or py_backup == "old"


# ── 5. Standalone Python Hypothesis tests (run even without Lean oracle) ──────

@settings(max_examples=200)
@given(
    saved_order=st.lists(TILE_OR_SENTINEL, max_size=16),
    saved_hidden=st.lists(TILE_OR_SENTINEL, max_size=8),
    tile_ids=VALID_TILE_IDS,
    default_order=st.lists(TILE_OR_SENTINEL, max_size=12),
)
def test_property_layout_merge_invariants_standalone(
    saved_order: list[str],
    saved_hidden: list[str],
    tile_ids: list[str],
    default_order: list[str],
) -> None:
    o1, h1, _ = tricorder._merge_layout_tiles(
        saved_order, saved_hidden, tile_ids, default_order
    )
    o2, h2, c2 = tricorder._merge_layout_tiles(o1, h1, tile_ids, default_order)
    assert _is_canonical_order(o1)
    assert (o2, h2, c2) == (o1, h1, False)
    vis = [t for t in o1 if t != "__row__"]
    assert set(vis).isdisjoint(set(h1))
    assert set(vis) | set(h1) == set(tile_ids)
