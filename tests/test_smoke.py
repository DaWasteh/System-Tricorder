import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import system_tricorder as tricorder


def test_fmt_mbps_ranges() -> None:
    assert tricorder._fmt_mbps(0.0) == "0.0 MB/s"
    assert tricorder._fmt_mbps(99.9) == "99.9 MB/s"
    assert tricorder._fmt_mbps(100.0) == "100 MB/s"
    assert tricorder._fmt_mbps(500.0) == "500 MB/s"
    assert tricorder._fmt_mbps(1000.0) == "1.00 GB/s"
    assert tricorder._fmt_mbps(2500.0) == "2.50 GB/s"


def test_short_gpu_name() -> None:
    result = tricorder.short_gpu_name("NVIDIA GeForce RTX 4090 24GB")
    assert result.startswith("RTX")
    assert len(result) <= 22
    assert len(tricorder.short_gpu_name("SomeUnknownGPU")) <= 22


def test_metric_defaults() -> None:
    drive = tricorder.DriveMetrics(
        key="PhysicalDrive0", label="C:", read_mbps=123.4, write_mbps=45.6
    )
    assert drive.key == "PhysicalDrive0"
    assert drive.read_mbps == 123.4

    gpu = tricorder.GPUMetrics(name="Test GPU", luid="abc")
    assert gpu.gpu_total_percent == 0.0
    assert gpu.gpu_3d_percent == 0.0
    assert gpu.gpu_vram_total_gb == 8.0
    assert gpu.gpu_power_watts is None

    # Preserve the pre-v2.7 positional API: new fields belong after every
    # legacy optional GPU field so old callers cannot silently shift values.
    legacy = tricorder.GPUMetrics("Legacy", "luid", 1, 2, 3, 4, 5, 6, 7)
    assert legacy.gpu_3d_percent == 1
    assert legacy.gpu_compute_percent == 2
    assert legacy.gpu_vram_total_gb == 7
    assert legacy.gpu_total_percent == 0.0
    assert legacy.gpu_power_watts is None

    system = tricorder.SystemMetrics(
        cpu_total_percent=0.0,
        cpu_cores={},
        ram_total_gb=1.0,
        ram_used_gb=0.5,
        ram_percent=50.0,
        gpus=[],
        igpu_percent=0.0,
        disk_read_mbps=0.0,
        disk_write_mbps=0.0,
        drives=[],
        timestamp=datetime.now(),
    )
    assert system.cpu_power_watts is None


def test_release_versions_are_synchronized() -> None:
    assert tricorder.APP_VERSION == "2.7.9"
    assert tricorder.CONFIG_VERSION == "1.0"
    root = Path(tricorder.__file__).parent
    version_info = (root / "assets" / "version_info.txt").read_text(encoding="utf-8")
    for expected in (
        "filevers=(2, 7, 9, 0)",
        "prodvers=(2, 7, 9, 0)",
        "FileVersion', '2.7.9.0'",
        "ProductVersion', '2.7.9.0'",
    ):
        assert expected in version_info
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "version-2.7.9-00ff88" in readme
    assert "### v2.7.9" in readme
    if os.name == "nt":
        assert tricorder._windows_file_version(
            root / "dist" / "system_tricorder.exe") == (2, 7, 9, 0)
    release_builder = (root / ".github" / "scripts" / "build_release.py").read_text(
        encoding="utf-8")
    assert 'generated_spec_dir = build_dir / "generated-spec"' in release_builder
    assert '"--specpath"' in release_builder


def test_gpu_palettes() -> None:
    assert len(tricorder.GPU_PALETTES) >= 4
    for palette in tricorder.GPU_PALETTES:
        assert len(palette) == 4
        assert all(color.startswith("#") for color in palette)


def test_color_and_clock_helpers() -> None:
    assert tricorder._normalise_color_hex("#AbC") == "#aabbcc"
    assert tricorder._normalise_color_hex("rgb(1, 2, 3)") is None
    assert tricorder._normalise_color_hex("not-a-colour") is None
    assert tricorder._normalise_color_hex(None) is None
    assert tricorder._normalise_theme("LIGHT") == "light"
    assert tricorder._normalise_theme("unknown") == "dark"
    assert tricorder._clock_display_mode(1400) == "inline"
    assert tricorder._clock_display_mode(1100) == "stacked"
    assert tricorder._clock_display_mode(800) == "time-only"


def test_cpu_package_power_uses_pkg_without_double_counting() -> None:
    rows = [
        ("rapl_package0_pkg", 42_500.0),
        ("rapl_package0_pp0", 31_000.0),
        ("rapl_package0_dram", 2_000.0),
        ("rapl_package1_pkg", 50_000.0),
    ]
    assert tricorder._cpu_package_power_from_pdh(rows) == pytest.approx(92.5)
    assert tricorder._cpu_package_power_from_pdh([
        ("rapl_package0_pp0", 31_000.0)
    ]) is None


def test_cpu_power_supports_amd_socket_names_and_core_fallback() -> None:
    assert tricorder._cpu_package_power_from_pdh([
        ("Current Socket Power", 87_500.0),
        ("RAPL_Package0_Core0_CORE", 20_000.0),
        ("RAPL_Package0_Core1_CORE", 21_000.0),
    ]) == pytest.approx(87.5)
    assert tricorder._cpu_package_power_from_pdh([
        ("CPU Power", 65_250.0),
    ]) == pytest.approx(65.25)
    assert tricorder._cpu_package_power_from_pdh([
        ("Apu Power", 28_000.0),
    ]) == pytest.approx(28.0)
    for alias in (
        "Socket Power", "Current Socket Energy",
        "CPU Package Power", "Apu Energy",
    ):
        assert tricorder._cpu_package_power_from_pdh([
            (alias, 42_000.0),
        ]) == pytest.approx(42.0)

    # Some Zen systems, including reported Ryzen 5000 configurations, expose
    # only one Energy Meter power row per physical core.
    assert tricorder._cpu_package_power_from_pdh([
        ("RAPL_Package0_Core0_CORE", 11_500.0),
        ("RAPL_Package0_Core1_CORE", 12_500.0),
        ("RAPL_Package0_Core2_CORE", 13_000.0),
        ("RAPL_Package0_Core3_CORE", 14_000.0),
    ]) == pytest.approx(51.0)
    assert tricorder._cpu_package_power_from_pdh([
        ("Current Socket Power", 0.0),
        ("RAPL_Package0_Core0_CORE", 0.0),
    ]) is None


def test_layout_migration_places_total_and_power_tiles_idempotently() -> None:
    order = [
        "gpu_0_3d", "gpu_0_vram", "gpu_1_vram", "__row__",
        "cpu_power", "igpu",
    ]
    hidden = ["gpu_1_3d"]
    tile_ids = [
        "gpu_0_3d", "gpu_0_vram", "gpu_1_vram", "gpu_1_3d",
        "cpu_total", "igpu", "gpu_0_total", "gpu_1_total",
        "gpu_0_power", "gpu_1_power", "cpu_power", "optional_tile",
    ]
    default_order = [
        "cpu_total", "cpu_power", "__row__",
        "gpu_0_total", "gpu_0_3d", "gpu_0_vram", "gpu_0_power",
        "gpu_1_total", "gpu_1_3d", "gpu_1_vram", "gpu_1_power",
    ]

    migrated_order, migrated_hidden, changed = tricorder._merge_layout_tiles(
        order, hidden, tile_ids, default_order)
    assert changed
    assert migrated_order[:6] == [
        "gpu_0_total", "gpu_0_3d", "gpu_0_vram", "gpu_0_power",
        "gpu_1_vram", "gpu_1_power",
    ]
    assert migrated_order[-3:] == ["cpu_total", "cpu_power", "igpu"]
    assert "gpu_1_total" in migrated_hidden
    assert "optional_tile" in migrated_hidden

    again_order, again_hidden, changed_again = tricorder._merge_layout_tiles(
        migrated_order, migrated_hidden, tile_ids, default_order)
    assert not changed_again
    assert again_order == migrated_order
    assert again_hidden == migrated_hidden


def test_new_row_move_preserves_last_tile_and_row_boundaries() -> None:
    cpu_order = ["ram", "cpu_total", "__row__", "igpu"]
    assert tricorder._move_tile_to_new_row(cpu_order, "cpu_total", 0) == [
        "ram", "__row__", "cpu_total", "__row__", "igpu"
    ]

    igpu_order = ["cpu_total", "__row__", "gpu_0_power", "igpu"]
    assert tricorder._move_tile_to_new_row(igpu_order, "igpu", 1) == [
        "cpu_total", "__row__", "gpu_0_power", "__row__", "igpu"
    ]

    singleton_rows = ["cpu_total", "__row__", "igpu"]
    assert tricorder._move_tile_to_new_row(singleton_rows, "cpu_total", 0) == singleton_rows
    assert tricorder._move_tile_to_new_row(singleton_rows, "igpu", 1) == singleton_rows
    assert tricorder._move_tile_to_new_row(singleton_rows, "missing", 0) == singleton_rows


def test_rebuild_helper_uses_versioned_spec_and_replaces_renamed_exe() -> None:
    script = tricorder._build_rebuild_bat(
        Path("C:/Benutzer/Jörg Repo"),
        Path("C:/Benutzer/Jörg Repo/SystemTricorder-v2.7.1.exe"),
        42,
        Path("C:/Benutzer/Jörg Temp/rebuild.log"),
        "main",
    )
    assert script.startswith("@echo off\r\nchcp 65001 >NUL\r\nset PYTHONUTF8=1\r\n")
    assert "PyInstaller --clean --noconfirm system_tricorder.spec" in script
    assert 'set "PULL_BRANCH=main"' in script
    assert 'git pull --ff-only --autostash' in script
    assert 'set "BUILT=%REPO%\\dist\\system_tricorder.exe"' in script
    assert 'copy /Y "%BUILT%" "%EXE%"' in script
    assert "Jörg Repo" in script.encode("utf-8").decode("utf-8")
    assert "--add-data" not in script


def test_linux_gpu_power_hwmon(tmp_path) -> None:
    hwmon = tmp_path / "device" / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    (hwmon / "power1_average").write_text("250000000", encoding="utf-8")
    assert tricorder._linux_read_gpu_power_watts(str(tmp_path)) == pytest.approx(250.0)

    (hwmon / "power1_average").unlink()
    (hwmon / "power1_input").write_text("125000000", encoding="utf-8")
    assert tricorder._linux_read_gpu_power_watts(str(tmp_path)) == pytest.approx(125.0)


def test_linux_cpu_power_energy_delta_and_wrap(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    energy = tmp_path / "energy_uj"
    sampler = tricorder._LinuxCpuPowerSampler.__new__(tricorder._LinuxCpuPowerSampler)
    sampler._domains = [(energy, 10_000_000)]
    sampler._previous = {}
    times = iter((10.0, 12.0))
    monkeypatch.setattr(tricorder.time, "monotonic", lambda: next(times))
    energy.write_text("1000000", encoding="utf-8")
    assert sampler.sample() is None
    energy.write_text("5000000", encoding="utf-8")
    assert sampler.sample() == pytest.approx(2.0)

    sampler._previous = {}
    times = iter((20.0, 21.0))
    energy.write_text("9000000", encoding="utf-8")
    assert sampler.sample() is None
    energy.write_text("1000000", encoding="utf-8")
    assert sampler.sample() == pytest.approx(2.0)


def test_adlx_device_id_normalization() -> None:
    assert tricorder._AdlxGpuSampler._normalise_device_id("7551") == "0x7551"
    assert tricorder._AdlxGpuSampler._normalise_device_id("0x7550") == "0x7550"
    assert tricorder._AdlxGpuSampler._normalise_device_id("") == ""


def test_macos_uses_generic_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Darwin")
    monitor = tricorder.HardwareMonitorThread(drive_info=[])
    assert monitor._platform == "Darwin"
    assert not monitor._is_windows
    assert not monitor._is_linux
    assert monitor._dgpu_info == []


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    tricorder._init_dp_scale(app)
    return app


def test_tile_custom_colors_update_every_graph_and_reset(
    qapp: QApplication,
) -> None:
    tricorder._set_active_theme("dark")
    palette = tricorder.GPU_PALETTES[0]
    tiles = [
        tricorder.MetricTile("metric", "Metric", "#00d4ff"),
        tricorder.PowerTile("power", "Power", "#ffcc00", 250.0),
        tricorder.DriveTile("drive", "C:"),
        tricorder.GPUCopyTile("copy", "GPU", palette),
        tricorder.GPUCodecTile("codec", "GPU", palette),
        tricorder.GPU3DComputeTile("engines", "GPU", palette),
    ]
    graph_groups = [
        [tiles[0]._graph],
        [tiles[1]._graph],
        [tiles[2]._r_graph, tiles[2]._w_graph],
        [tiles[3]._c0_graph, tiles[3]._c1_graph],
        [tiles[4]._codec_graph],
        [tiles[5]._d3_graph, tiles[5]._cm_graph],
    ]
    tiles[0]._graph.add_value(37.0)
    history_before = list(tiles[0]._graph.history)

    for tile, graphs in zip(tiles, graph_groups, strict=True):
        tile.set_custom_color("#123456")
        assert tile.custom_color == "#123456"
        assert all(graph.color.name() == "#123456" for graph in graphs)
    assert list(tiles[0]._graph.history) == history_before

    for tile in tiles:
        tile.set_custom_color(None)
        assert tile.custom_color is None
    assert tiles[0]._graph.color.name() == "#00d4ff"
    assert tiles[2]._r_graph.color.name() == tricorder.DRIVE_R_COLOR
    assert tiles[2]._w_graph.color.name() == tricorder.DRIVE_W_COLOR
    assert tiles[3]._c0_graph.color.name() == palette[1]
    assert tiles[3]._c1_graph.color.name() == palette[2]
    assert tiles[5]._d3_graph.color.name() == palette[0]
    assert tiles[5]._cm_graph.color.name() == palette[1]

    # Exact user-entered HEX colours must not be contrast-remapped by Lightmode.
    tricorder._set_active_theme("light")
    tiles[0].set_custom_color("#00ff88")
    assert "#00ff88" in tiles[0].styleSheet()
    assert "#00ff88" in tiles[0]._title_lbl.styleSheet()
    tricorder._set_active_theme("dark")
    tricorder._apply_theme_tree(tiles[0])
    tricorder._set_active_theme("light")
    tricorder._apply_theme_tree(tiles[0])
    assert "#00ff88" in tiles[0].styleSheet()
    assert tiles[0]._graph.color.name() == "#00ff88"
    tricorder._set_active_theme("dark")

    for tile in tiles:
        tile.deleteLater()
    qapp.processEvents()


def test_color_picker_forces_spectrum_rgb_hex_dialog(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    seen: dict[str, bool] = {}

    def fake_exec(dialog: tricorder.QColorDialog) -> tricorder.QDialog.DialogCode:
        seen["non_native"] = dialog.testOption(
            tricorder.QColorDialog.ColorDialogOption.DontUseNativeDialog)
        seen["no_alpha"] = not dialog.testOption(
            tricorder.QColorDialog.ColorDialogOption.ShowAlphaChannel)
        return tricorder.QDialog.DialogCode.Rejected

    monkeypatch.setattr(tricorder.QColorDialog, "exec", fake_exec)
    tile = tricorder.MetricTile("cpu_total", "CPU", "#00d4ff")
    tile._choose_custom_color()
    assert seen == {"non_native": True, "no_alpha": True}
    tile.deleteLater()


def test_gpu_total_and_engine_tiles_are_separate(qapp: QApplication) -> None:
    palette = tricorder.GPU_PALETTES[0]
    total = tricorder.MetricTile("gpu_0_total", "Test GPU · GPU", palette[3])
    engines = tricorder.GPU3DComputeTile("gpu_0_3d", "Test GPU", palette)

    total.update_val(91.0)
    engines.update_3d_compute(32.0, 77.0)

    assert total._graph.history[-1] == pytest.approx(91.0)
    assert engines._d3_graph.history[-1] == pytest.approx(32.0)
    assert engines._cm_graph.history[-1] == pytest.approx(77.0)
    assert not hasattr(engines, "_gpu_graph")

    total.deleteLater()
    engines.deleteLater()
    qapp.processEvents()


def test_registry_keeps_total_and_engine_tiles_adjacent(qapp: QApplication) -> None:
    class RegistryHost:
        ram_type = "DDR5"
        current_platform = "Windows"
        detected_gpus = [("AMD Radeon Test", 16.0, "0x1234")]
        has_igpu = True
        _drive_info = []
        _tiles = {}

    host = RegistryHost()
    tiles, names, default_order = tricorder.TricorderDashboard._build_tile_registry(host)
    total_index = default_order.index("gpu_0_total")
    assert default_order[total_index + 1] == "gpu_0_3d"
    assert isinstance(tiles["gpu_0_total"], tricorder.MetricTile)
    assert isinstance(tiles["gpu_0_3d"], tricorder.GPU3DComputeTile)
    assert names["gpu_0_total"].endswith(" · GPU")
    assert names["gpu_0_3d"].endswith(" · 3D / Compute")

    for tile in tiles.values():
        tile.deleteLater()
    qapp.processEvents()


def test_dashboard_routes_total_and_engine_metrics_to_separate_tiles() -> None:
    class RecordingTotal:
        def __init__(self) -> None:
            self.values = []
            self.batches = 0

        def update_val(self, value: float, suffix=None) -> None:
            self.values.append((value, suffix))

        def batch_update(self) -> None:
            self.batches += 1

    class RecordingEngines:
        def __init__(self) -> None:
            self.values = []
            self.batches = 0

        def update_3d_compute(self, gpu_3d: float, compute: float) -> None:
            self.values.append((gpu_3d, compute))

        def batch_update(self) -> None:
            self.batches += 1

    total = RecordingTotal()
    engines = RecordingEngines()

    class DashboardHost:
        _metric_frames = 0
        _tiles = {"gpu_0_total": total, "gpu_0_3d": engines}
        thread_widgets = {}

    metrics = tricorder.SystemMetrics(
        cpu_total_percent=0.0,
        cpu_cores={},
        ram_total_gb=1.0,
        ram_used_gb=0.5,
        ram_percent=50.0,
        gpus=[tricorder.GPUMetrics(
            name="Test GPU", luid="abc", gpu_3d_percent=32.0,
            gpu_compute_percent=77.0, gpu_total_percent=91.0,
        )],
        igpu_percent=0.0,
        disk_read_mbps=0.0,
        disk_write_mbps=0.0,
        drives=[],
        timestamp=datetime.now(),
    )
    host = DashboardHost()
    tricorder.TricorderDashboard._update_ui(host, metrics)

    assert total.values == [(91.0, None)]
    assert engines.values == [(32.0, 77.0)]
    assert total.batches == 1
    assert engines.batches == 1


def test_tile_grid_repairs_and_persists_layout(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "layout.json"
    config_path.write_text(json.dumps({
        "version": "0.9",
        "window": {"geometry": "test", "placement": "normal"},
    }), encoding="utf-8")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", config_path)

    default_order = ["cpu_total", "ram", "__row__", "igpu"]
    tiles = {
        tile_id: tricorder.MetricTile(tile_id, tile_id, "#00ff88")
        for tile_id in ("cpu_total", "ram", "igpu")
    }
    grid = tricorder.TileGrid(tiles, {tile_id: tile_id for tile_id in tiles}, default_order)
    assert grid._tile_order == default_order

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["version"] == "1.0"
    assert saved["tile_order"] == default_order
    assert saved["window"]["placement"] == "normal"

    grid.deleteLater()
    qapp.processEvents()


def test_tile_colors_persist_and_keep_dormant_hardware(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "layout.json"
    config_path.write_text(json.dumps({
        "version": "1.0",
        "tile_order": ["cpu_total"],
        "hidden_tiles": [],
        "tile_colors": {
            "cpu_total": "#ABCDEF",
            "drive_PhysicalDrive9": "#112233",
            "broken": "not-a-colour",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", config_path)

    tile = tricorder.MetricTile("cpu_total", "CPU", "#00d4ff")
    grid = tricorder.TileGrid(
        {"cpu_total": tile}, {"cpu_total": "CPU"}, ["cpu_total"])
    assert tile.custom_color == "#abcdef"
    assert tile._graph.color.name() == "#abcdef"

    grid.set_min_row_h(160)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tile_colors"] == {
        "cpu_total": "#abcdef",
        "drive_PhysicalDrive9": "#112233",
    }

    grid._on_color_change("cpu_total", "#fedcba")
    assert tile._graph.color.name() == "#fedcba"
    grid._on_color_reset("cpu_total")
    assert tile.custom_color is None
    assert tile._graph.color.name() == "#00d4ff"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tile_colors"] == {"drive_PhysicalDrive9": "#112233"}

    grid.deleteLater()
    qapp.processEvents()


def test_reset_layout_matches_config_free_factory(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "layout.json"
    config_path.write_text(json.dumps({
        "version": "1.0",
        "min_row_h": 360,
        "tile_order": ["optional", "__row__", "cpu_total", "ghost_gpu"],
        "hidden_tiles": ["ram"],
        "window": {"geometry": "saved", "placement": "normal"},
    }), encoding="utf-8")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", config_path)

    default_order = ["cpu_total", "ram", "__row__", "gpu_total"]
    tile_ids = ("cpu_total", "ram", "gpu_total", "optional")
    tiles = {
        tile_id: tricorder.MetricTile(tile_id, tile_id, "#00ff88")
        for tile_id in tile_ids
    }
    grid = tricorder.TileGrid(
        tiles, {tile_id: tile_id for tile_id in tile_ids}, default_order)

    grid.reset_layout(default_order)

    factory_min, factory_max = tricorder._factory_tile_row_heights()
    assert grid._tile_order == default_order
    assert grid._parse_rows() == [["cpu_total", "ram"], ["gpu_total"]]
    assert grid._hidden == ["optional"]
    assert grid._min_row_h == factory_min
    assert grid._max_row_h == factory_max

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tile_order"] == default_order
    assert saved["hidden_tiles"] == ["optional"]
    assert saved["min_row_h"] == factory_min
    assert saved["window"]["placement"] == "normal"
    assert "ghost_gpu" not in saved["tile_order"]

    grid.deleteLater()
    qapp.processEvents()


def test_tile_rows_expand_vertically_without_blank_bands(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tricorder, "CONFIG_FILE", tmp_path / "layout.json")
    tile_ids = ("cpu_total", "ram", "gpu_total", "gpu_vram")
    tiles = {
        tile_id: tricorder.MetricTile(tile_id, tile_id, "#00ff88")
        for tile_id in tile_ids
    }
    grid = tricorder.TileGrid(
        tiles,
        {tile_id: tile_id for tile_id in tile_ids},
        ["cpu_total", "ram", "__row__", "gpu_total", "gpu_vram"],
    )
    grid.resize(1000, 800)
    grid.show()
    qapp.processEvents()
    grid._auto_adjust_row_height()
    qapp.processEvents()

    rows = grid._row_widgets
    assert len(rows) == 2
    assert all(row.maximumHeight() == tricorder.TricorderDashboard._MAX_WIDGET
               for row in rows)
    assert all(row.height() > grid._max_row_h for row in rows)
    occupied = sum(row.height() for row in rows) + grid._vbox.spacing()
    assert occupied >= grid.height() - 4

    grid.resize(320, 80)
    qapp.processEvents()
    grid._auto_adjust_row_height()
    qapp.processEvents()
    compact_occupied = sum(row.height() for row in rows) + grid._vbox.spacing()
    assert all(row.isVisible() and row.height() > 0 for row in rows)
    assert compact_occupied <= grid.height() + 4

    grid.close()
    grid.deleteLater()
    qapp.processEvents()


def test_qt_dpi_uses_device_independent_coordinates(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tricorder, "_DP_SCALE", 2.0)
    assert tricorder._init_dp_scale(qapp) == 1.0
    assert tricorder.dp(24) == 24


def test_sparkline_preferred_heights_are_dpi_scaled_once(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tricorder, "_DP_SCALE", 2.0)
    core = tricorder.MasterMetricBox("P-Core 0", "#00d4ff")
    drive = tricorder.DriveTile("drive_test", "C:")

    assert core.graph._pref_h == 36
    assert drive._r_graph._pref_h == 48
    assert drive._w_graph._pref_h == 48

    core.deleteLater()
    drive.deleteLater()
    qapp.processEvents()


def test_maximised_dashboard_uses_coalesced_fill_layout(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tricorder, "CONFIG_FILE", tmp_path / "layout.json")
    monkeypatch.setattr(tricorder, "_DP_SCALE", 2.0)

    def fake_hardware(self: tricorder.TricorderDashboard) -> None:
        self.c_physical = 24
        self.c_logical = 24
        self.num_sockets = 1
        self.is_amd = False
        self.is_hybrid = False
        self.has_ht = False
        self.p_cores = 0
        self.e_cores = 0
        self.p_threads = 24
        self.e_threads = 0
        self.p_logical = []
        self.e_logical = []
        self.ram_type = "RAM"
        self.current_platform = "Windows"
        self.detected_gpus = [
            ("AMD Radeon Test 0", 16.0, "0x1000"),
            ("AMD Radeon Test 1", 32.0, "0x2000"),
        ]
        self.has_igpu = True
        self._drive_info = [
            ("drive0", "C:"), ("drive1", "D:"), ("drive2", "E:")
        ]

    monkeypatch.setattr(tricorder.TricorderDashboard, "_analyze_hardware", fake_hardware)
    monkeypatch.setattr(tricorder.HardwareMonitorThread, "start", lambda _self: None)
    monkeypatch.setattr(tricorder.HardwareMonitorThread, "stop", lambda _self: None)

    dashboard = tricorder.TricorderDashboard()
    try:
        dashboard._schedule_layout_settle("auto")
        dashboard._schedule_layout_settle("fill")
        assert dashboard._resize_settle_timer.isSingleShot()
        assert dashboard._resize_settle_timer.interval() == 50
        assert dashboard._resize_settle_timer.isActive()
        assert dashboard._pending_resize_action == "fill"
        dashboard._resize_settle_timer.stop()

        dashboard._pending_resize_action = "auto"
        dashboard.showMaximized()
        qapp.processEvents()
        assert dashboard._pending_resize_action == "fill"

        dashboard._resize_settle_timer.stop()
        dashboard._settle_responsive_layout()
        qapp.processEvents()
        assert dashboard._fit_mode == "fill"
        assert all(
            row.maximumHeight() == dashboard._MAX_WIDGET
            for row in dashboard._tile_grid._row_widgets
        )

        dashboard._pending_resize_action = "auto"
        dashboard.showFullScreen()
        qapp.processEvents()
        assert dashboard._pending_resize_action == "fill"

        dashboard._pending_resize_action = "auto"
        dashboard.showNormal()
        qapp.processEvents()
        assert dashboard._pending_resize_action == "fill"
        dashboard._resize_settle_timer.stop()

        dashboard.resize(640, 360)
        dashboard._apply_fill_mode()
        qapp.processEvents()
        viewport = dashboard._scroll.viewport()
        assert dashboard.minimumSize().width() == 640
        assert dashboard.minimumSize().height() == 360
        assert (dashboard._scroll.verticalScrollBarPolicy()
                == tricorder.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        assert (dashboard._scroll.horizontalScrollBarPolicy()
                == tricorder.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        assert not dashboard._scroll.verticalScrollBar().isVisible()
        assert not dashboard._scroll.horizontalScrollBar().isVisible()
        assert dashboard._content_w.width() == viewport.width()
        assert viewport.height() - dashboard._content_w.height() == 2
        for tile_id in dashboard._tile_grid._tile_order:
            if tile_id == "__row__":
                continue
            tile = dashboard._tiles[tile_id]
            top_left = tile.mapTo(viewport, tricorder.QPoint(0, 0))
            assert tile.isVisible() and tile.width() > 0 and tile.height() > 0
            assert 0 <= top_left.x() < viewport.width()
            assert top_left.x() + tile.width() <= viewport.width() + 1
            assert 0 <= top_left.y() < viewport.height()
            assert top_left.y() + tile.height() <= viewport.height()
            for graph in tile.findChildren(tricorder.SparklineWidget):
                graph_pos = graph.mapTo(viewport, tricorder.QPoint(0, 0))
                assert graph.width() > 0 and graph.height() > 0
                assert 0 <= graph_pos.x() < viewport.width()
                assert graph_pos.x() + graph.width() <= viewport.width() + 1
                assert 0 <= graph_pos.y() < viewport.height()
                assert graph_pos.y() + graph.height() <= viewport.height() + 1
        for thread_widget in dashboard.thread_widgets.values():
            top_left = thread_widget.mapTo(viewport, tricorder.QPoint(0, 0))
            assert (thread_widget.isVisible() and thread_widget.width() > 0
                    and thread_widget.height() > 0)
            assert 0 <= top_left.x() < viewport.width()
            assert top_left.x() + thread_widget.width() <= viewport.width() + 1
            assert 0 <= top_left.y() < viewport.height()
            # Qt may round the final stretched grid row one pixel past the
            # viewport edge at 200% DPR; no complete row/widget is clipped.
            assert top_left.y() + thread_widget.height() <= viewport.height() + 1

        dashboard._tile_grid.set_edit_mode(True)
        dashboard._resize_settle_timer.stop()
        dashboard._apply_fill_mode()
        qapp.processEvents()
        assert dashboard._content_w.width() == viewport.width()
        assert viewport.height() - dashboard._content_w.height() == 2
        for row_widget in dashboard._tile_grid._row_widgets:
            top_left = row_widget.mapTo(viewport, tricorder.QPoint(0, 0))
            assert (row_widget.isVisible() and row_widget.width() > 0
                    and row_widget.height() > 0)
            assert 0 <= top_left.x() < viewport.width()
            assert top_left.x() + row_widget.width() <= viewport.width() + 1
            assert 0 <= top_left.y() < viewport.height()
            assert top_left.y() + row_widget.height() <= viewport.height() + 1
        dashboard._tile_grid.set_edit_mode(False)
        dashboard._resize_settle_timer.stop()
        dashboard._apply_fill_mode()
        qapp.processEvents()

        with monkeypatch.context() as no_nested_events:
            def fail_process_events(*_args: object, **_kwargs: object) -> None:
                raise AssertionError("responsive fitting must not process nested events")

            no_nested_events.setattr(
                tricorder.QApplication, "processEvents", fail_process_events)
            dashboard._fit_window_to_content()

        dashboard.resize(1350, 900)
        qapp.processEvents()
    finally:
        dashboard.close()
        dashboard.deleteLater()
        qapp.processEvents()

    restored = tricorder.TricorderDashboard()
    try:
        assert restored._restore_window_placement()
        qapp.processEvents()
        assert restored._fit_mode == "fill"
        restored_size = restored.size()
        restored._resize_settle_timer.stop()
        restored._settle_responsive_layout()
        qapp.processEvents()
        assert restored.size() == restored_size
    finally:
        restored.close()
        restored.deleteLater()
        qapp.processEvents()


def test_layout_saves_preserve_temporarily_absent_hardware(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "layout.json"
    original = {
        "version": "0.9",
        "tile_order": ["gpu_0_3d", "__row__", "gpu_1_3d"],
        "hidden_tiles": ["drive_PhysicalDrive9"],
    }
    config_path.write_text(json.dumps(original, indent=2), encoding="utf-8")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", config_path)

    tiles = {
        "gpu_0_total": tricorder.MetricTile(
            "gpu_0_total", "GPU", "#00ff88"),
        "gpu_0_3d": tricorder.MetricTile(
            "gpu_0_3d", "3D / Compute", "#00ff88"),
    }
    default_order = ["gpu_0_total", "gpu_0_3d"]
    grid = tricorder.TileGrid(
        tiles, {tile_id: tile_id for tile_id in tiles}, default_order)

    assert grid._tile_order == default_order
    migrated = json.loads(config_path.read_text(encoding="utf-8"))
    assert migrated["version"] == "1.0"
    assert migrated["tile_order"] == [
        "gpu_0_total", "gpu_0_3d", "__row__", "gpu_1_3d"
    ]
    assert migrated["hidden_tiles"] == ["drive_PhysicalDrive9"]

    grid.set_min_row_h(160)
    saved_again = json.loads(config_path.read_text(encoding="utf-8"))
    assert "gpu_1_3d" in saved_again["tile_order"]
    assert "drive_PhysicalDrive9" in saved_again["hidden_tiles"]

    grid.deleteLater()
    qapp.processEvents()


def test_layout_migration_save_failure_does_not_block_startup(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tricorder, "CONFIG_FILE", tmp_path / "layout.json")

    def fail_save(_data: dict) -> None:
        raise OSError("read-only config target")

    monkeypatch.setattr(tricorder, "_save_config_file", fail_save)
    tile = tricorder.MetricTile("cpu_total", "CPU", "#00ff88")
    grid = tricorder.TileGrid(
        {"cpu_total": tile}, {"cpu_total": "CPU"}, ["cpu_total"])
    assert grid._tile_order == ["cpu_total"]

    grid.deleteLater()
    qapp.processEvents()


def test_update_version_url_and_git_blob_validation() -> None:
    assert tricorder._version_tuple("v2.7.5") == (2, 7, 5)
    assert tricorder._version_tuple("2.7") == (2, 7, 0)
    assert tricorder._version_tuple("v2.7.5-rc1") is None
    assert tricorder._git_blob_sha(b"hello") == "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0"

    assert tricorder._validate_update_url(
        "https://api.github.com/repos/DaWasteh/System-Tricorder/releases/latest"
    ).startswith("https://")
    for rejected in (
        "http://github.com/DaWasteh/System-Tricorder",
        "https://evil.example/SystemTricorder.exe",
        "https://user:secret@github.com/SystemTricorder.exe",
        "https://github.com:444/SystemTricorder.exe",
    ):
        with pytest.raises(RuntimeError):
            tricorder._validate_update_url(rejected)


def test_bounded_download_checks_size_and_sha256(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    payload = b"verified update payload"

    class FakeResponse:
        def __init__(self) -> None:
            self.headers = {"Content-Length": str(len(payload))}
            self._sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://raw.githubusercontent.com/DaWasteh/System-Tricorder/v2.7.5/test.bin"

        def read(self, _size: int = -1) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return payload

    attempts = 0

    def flaky_urlopen(*_args: object, **_kwargs: object) -> FakeResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("transient GitHub reset")
        return FakeResponse()

    monkeypatch.setattr(
        tricorder.urllib.request, "urlopen", flaky_urlopen)
    monkeypatch.setattr(tricorder.time, "sleep", lambda _seconds: None)
    worker = tricorder.UpdateWorker()
    destination = tmp_path / "update.bin"
    expected_hash = hashlib.sha256(payload).hexdigest()
    assert worker._download_to_path(
        "https://raw.githubusercontent.com/DaWasteh/System-Tricorder/v2.7.5/test.bin",
        destination,
        expected_size=len(payload),
        max_size=1024,
        expected_sha256=expected_hash,
    ) == expected_hash
    assert destination.read_bytes() == payload
    assert attempts == 2

    bad_destination = tmp_path / "bad.bin"
    with pytest.raises(RuntimeError, match="SHA-256"):
        worker._download_to_path(
            "https://raw.githubusercontent.com/DaWasteh/System-Tricorder/v2.7.5/test.bin",
            bad_destination,
            expected_size=len(payload),
            max_size=1024,
            expected_sha256="0" * 64,
        )
    assert not bad_destination.exists()
    worker.deleteLater()


def test_successful_frozen_update_removes_only_expected_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    executable = tmp_path / "SystemTricorder.exe"
    backup = Path(str(executable) + ".previous")
    executable.write_bytes(b"new")
    backup.write_bytes(b"old")
    unrelated = tmp_path / "keep.previous"
    unrelated.write_bytes(b"keep")
    monkeypatch.setattr(tricorder.sys, "executable", str(executable))
    monkeypatch.setattr(tricorder.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Windows")

    tricorder._cleanup_previous_exe_backup()

    assert not backup.exists()
    assert unrelated.read_bytes() == b"keep"


def test_git_checkout_discovery_ignores_unrelated_parent_repo(tmp_path: Path) -> None:
    root = tmp_path / "unrelated"
    start = root / "downloads" / "tricorder"
    (root / ".git").mkdir(parents=True)
    start.mkdir(parents=True)
    assert tricorder._find_git_checkout(start) is None
    (root / "system_tricorder.py").write_text(
        "# unrelated project with the same filename\n", encoding="utf-8")
    assert tricorder._find_git_checkout(start) is None

    (root / "system_tricorder.py").write_text(
        "# DaWasteh/System-Tricorder\n"
        "class UpdateWorker(QThread):\n    pass\n"
        "class TricorderDashboard(QMainWindow):\n    pass\n",
        encoding="utf-8",
    )
    assert tricorder._find_git_checkout(start) == root


def test_standalone_exe_helper_is_rollback_capable() -> None:
    script = tricorder._build_standalone_exe_update_bat(
        Path("C:/Benutzer/Jörg/SystemTricorder.exe"),
        Path("C:/Benutzer/Jörg/Temp/system-tricorder-update-1/release.exe"),
        42,
        Path("C:/Benutzer/Jörg/Temp/release-update.log"),
    )
    assert 'set "BACKUP=%EXE%.previous"' in script
    assert 'copy /Y /B "%UPDATE%" "%NEW%"' in script
    assert 'move /Y "%EXE%" "%BACKUP%"' in script
    assert 'move /Y "%BACKUP%" "%EXE%"' in script
    assert 'start "" "%EXE%"' in script
    assert "git pull" not in script
    assert "PyInstaller" not in script
    with pytest.raises(ValueError, match="unsafe"):
        tricorder._build_standalone_exe_update_bat(
            Path("C:/Users/100%/SystemTricorder.exe"),
            Path("C:/Temp/update.exe"), 42, Path("C:/Temp/update.log"),
        )


def _write_source_update_fixture(root: Path, version: str) -> None:
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "system_tricorder.py").write_text(
        f'APP_VERSION = "{version}"\nclass UpdateWorker(QThread):\n    pass\n',
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text(
        f"PyQt6>=6.11.0\npsutil>=7.2.2\n# fixture {version}\n",
        encoding="utf-8",
    )
    (root / "assets" / "SystemTricorder.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + version.encode("ascii"))


def test_source_update_install_is_atomic_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_source_update_fixture(install, "2.7.4")
    _write_source_update_fixture(staging, "2.7.5")
    os.chmod(install / "system_tricorder.py", 0o755)
    original_mode = tricorder.stat.S_IMODE(
        (install / "system_tricorder.py").stat().st_mode)
    before = {
        relative: (install / relative).read_bytes()
        for relative in tricorder._UPDATE_SOURCE_FILES
    }

    original_replace = Path.replace

    def fail_final_png_replace(self: Path, target: Path) -> Path:
        if self.name.startswith(".SystemTricorder.png.update-"):
            raise OSError("simulated final replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_final_png_replace)
    with pytest.raises(OSError, match="simulated"):
        tricorder._install_staged_source_files(staging, install)
    assert {
        relative: (install / relative).read_bytes()
        for relative in tricorder._UPDATE_SOURCE_FILES
    } == before
    assert not list(install.rglob("*.backup-*"))
    assert not list(install.rglob("*.update-*"))

    monkeypatch.setattr(Path, "replace", original_replace)
    assert tricorder._install_staged_source_files(staging, install)
    assert 'APP_VERSION = "2.7.5"' in (
        install / "system_tricorder.py").read_text(encoding="utf-8")
    assert (install / "assets" / "SystemTricorder.png").read_bytes().endswith(
        b"2.7.5")
    assert tricorder.stat.S_IMODE(
        (install / "system_tricorder.py").stat().st_mode) == original_mode


def test_source_backup_cleanup_failure_does_not_mix_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    install = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_source_update_fixture(install, "2.7.4")
    _write_source_update_fixture(staging, "2.7.5")
    original_unlink = Path.unlink

    def fail_one_backup(self: Path, missing_ok: bool = False) -> None:
        if self.name.startswith(".requirements.txt.backup-") and self.exists():
            raise OSError("simulated backup cleanup failure")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_one_backup)
    assert tricorder._install_staged_source_files(staging, install)
    assert 'APP_VERSION = "2.7.5"' in (
        install / "system_tricorder.py").read_text(encoding="utf-8")
    assert "fixture 2.7.5" in (
        install / "requirements.txt").read_text(encoding="utf-8")
    assert (install / "assets" / "SystemTricorder.png").read_bytes().endswith(
        b"2.7.5")
    assert list(install.rglob(".requirements.txt.backup-*"))


def test_source_release_stage_verifies_tagged_git_blobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {
        "system_tricorder.py": (
            b'APP_VERSION = "2.7.5"\nclass UpdateWorker(QThread):\n    pass\n'),
        "requirements.txt": b"PyQt6>=6.11.0\npsutil>=7.2.2\n",
        "assets/SystemTricorder.png": b"\x89PNG\r\n\x1a\nnew-icon",
    }
    worker = tricorder.UpdateWorker()

    def relative_from_url(url: str) -> str:
        decoded = tricorder.urllib.parse.unquote(url)
        return next(relative for relative in files if relative in decoded)

    def fake_json(url: str, timeout: int = 30) -> dict:
        del timeout
        relative = relative_from_url(url)
        data = files[relative]
        return {
            "type": "file",
            "path": relative,
            "size": len(data),
            "sha": tricorder._git_blob_sha(data),
            "download_url": (
                "https://raw.githubusercontent.com/DaWasteh/"
                f"System-Tricorder/v2.7.5/{relative}"
            ),
        }

    def fake_download(
        url: str, destination: Path, *, expected_size: int,
        max_size: int, expected_sha256: str = "", timeout: int = 120,
    ) -> str:
        del max_size, expected_sha256, timeout
        data = files[relative_from_url(url)]
        assert expected_size == len(data)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    monkeypatch.setattr(worker, "_request_json", fake_json)
    monkeypatch.setattr(worker, "_download_to_path", fake_download)
    staging = worker._stage_source_release("v2.7.5", (2, 7, 5))
    try:
        for relative, expected in files.items():
            assert (staging / relative).read_bytes() == expected
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        worker.deleteLater()


def test_non_git_updater_dispatches_exe_and_python_release_paths(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    release = {"tag_name": "v2.7.5", "assets": []}
    monkeypatch.setattr(tricorder, "APP_VERSION", "2.7.4")
    monkeypatch.setattr(tricorder, "_find_git_checkout", lambda _start: None)

    exe_worker = tricorder.UpdateWorker()
    monkeypatch.setattr(
        exe_worker, "_latest_release", lambda: (release, "v2.7.5", (2, 7, 5)))
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tricorder.sys, "frozen", True, raising=False)

    def fake_prepare(_release: dict, tag: str, _version: tuple[int, int, int]) -> None:
        exe_worker.prepared_exe_update = "C:/Temp/verified.exe"
        exe_worker.target_version = tag

    monkeypatch.setattr(exe_worker, "_prepare_standalone_exe_update", fake_prepare)
    ok, message = exe_worker._check_and_install()
    assert ok and "Verifiziertes GitHub-Release v2.7.5" in message
    assert exe_worker.prepared_exe_update.endswith("verified.exe")

    source_worker = tricorder.UpdateWorker()
    monkeypatch.setattr(
        source_worker, "_latest_release", lambda: (release, "v2.7.5", (2, 7, 5)))
    monkeypatch.delattr(tricorder.sys, "frozen", raising=False)

    def fake_source_install(tag: str, _version: tuple[int, int, int]) -> None:
        source_worker.target_version = tag

    monkeypatch.setattr(
        source_worker, "_install_standalone_source_update", fake_source_install)
    ok, message = source_worker._check_and_install()
    assert ok and "Standalone-Python wurde auf v2.7.5 aktualisiert" in message
    assert source_worker.target_version == "v2.7.5"

    exe_worker.deleteLater()
    source_worker.deleteLater()


def test_prepare_standalone_exe_requires_release_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    current_exe = tmp_path / "SystemTricorder.exe"
    current_exe.write_bytes(b"MZ-current")
    monkeypatch.setattr(tricorder.sys, "executable", str(current_exe))
    monkeypatch.setattr(tricorder.platform, "machine", lambda: "AMD64")
    worker = tricorder.UpdateWorker()
    release = {
        "assets": [{
            "name": tricorder.GITHUB_WINDOWS_ASSET,
            "size": 6_000_000,
            "digest": "",
            "browser_download_url": (
                "https://github.com/DaWasteh/System-Tricorder/releases/"
                "download/v2.7.5/SystemTricorder-windows-x86_64.exe"
            ),
        }],
    }
    with pytest.raises(RuntimeError, match="SHA-256"):
        worker._prepare_standalone_exe_update(
            release, "v2.7.5", (2, 7, 5))

    update_dir = tmp_path / "system-tricorder-update-test"

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix == "system-tricorder-update-"
        update_dir.mkdir()
        return str(update_dir)

    download_call: dict[str, object] = {}

    def fake_download(
        url: str, destination: Path, *, expected_size: int,
        max_size: int, expected_sha256: str = "", timeout: int = 120,
    ) -> str:
        download_call.update({
            "url": url, "size": expected_size, "max": max_size,
            "sha": expected_sha256, "timeout": timeout,
        })
        destination.write_bytes(b"MZ-verified-candidate")
        return expected_sha256

    validated: list[tuple[Path, tuple[int, int, int]]] = []
    release["assets"][0]["digest"] = "sha256:" + "a" * 64
    monkeypatch.setattr(tricorder.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(worker, "_download_to_path", fake_download)
    monkeypatch.setattr(
        worker, "_validate_windows_candidate",
        lambda path, version: validated.append((path, version)),
    )
    worker._prepare_standalone_exe_update(
        release, "v2.7.5", (2, 7, 5))
    assert Path(worker.prepared_exe_update).read_bytes() == b"MZ-verified-candidate"
    assert worker.target_version == "v2.7.5"
    assert download_call["sha"] == "a" * 64
    assert validated == [(Path(worker.prepared_exe_update), (2, 7, 5))]
    shutil.rmtree(update_dir, ignore_errors=True)
    worker.deleteLater()


def test_frozen_windows_update_defers_pull_until_exe_exits(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = tricorder.UpdateWorker()
    old_sha = "a" * 40
    new_sha = "b" * 40

    def fake_run_git(_repo: Path, args: list[str], timeout: int = 60) -> str:
        del timeout
        if args == ["branch", "--show-current"]:
            return "main"
        if args == ["rev-parse", "HEAD"]:
            return old_sha
        raise AssertionError(f"unexpected eager git command: {args}")

    monkeypatch.setattr(worker, "_run_git", fake_run_git)
    monkeypatch.setattr(worker, "_remote_sha_for_branch", lambda _branch: (new_sha, "main"))
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tricorder.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        tricorder, "_find_git_checkout", lambda _start: Path(".").resolve())

    ok, message = worker._check_and_install()
    assert ok
    assert "Update bereit" in message
    assert worker.rebuild_needed
    assert worker.deferred_pull_branch == "main"
    worker.deleteLater()
    qapp.processEvents()


def test_rebuild_detection_includes_packaging_inputs(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = tricorder.UpdateWorker()

    for changed_path in (
        "system_tricorder.py",
        "system_tricorder.spec",
        "requirements.txt",
        "assets/version_info.txt",
        "assets/SystemTricorder.ico",
        "assets/SystemTricorder.png",
    ):
        monkeypatch.setattr(
            worker, "_run_git",
            lambda *_args, path=changed_path, **_kwargs: path,
        )
        assert worker._rebuild_inputs_changed(Path("."), "old", "new")

    monkeypatch.setattr(worker, "_run_git", lambda *_args, **_kwargs: "README.md")
    assert not worker._rebuild_inputs_changed(Path("."), "old", "new")
    worker.deleteLater()
    qapp.processEvents()


def test_tile_grid_new_row_moves_do_not_lose_cpu_or_igpu(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "layout.json"
    initial_order = [
        "ram", "cpu_total", "__row__", "gpu_0_power", "igpu"
    ]
    config_path.write_text(json.dumps({
        "version": "1.0",
        "tile_order": initial_order,
        "hidden_tiles": [],
    }), encoding="utf-8")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", config_path)

    tiles = {
        tile_id: tricorder.MetricTile(tile_id, tile_id, "#00ff88")
        for tile_id in ("ram", "cpu_total", "gpu_0_power", "igpu")
    }
    grid = tricorder.TileGrid(tiles, {tile_id: tile_id for tile_id in tiles}, initial_order)
    grid._on_new_row("cpu_total", 0)
    grid._on_new_row("igpu", 2)

    expected = [
        "ram", "__row__", "cpu_total", "__row__",
        "gpu_0_power", "__row__", "igpu",
    ]
    assert grid._tile_order == expected
    assert sorted(tile_id for tile_id in grid._tile_order if tile_id != "__row__") == [
        "cpu_total", "gpu_0_power", "igpu", "ram"
    ]
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tile_order"] == expected

    grid.deleteLater()
    qapp.processEvents()


def test_power_tile_normalizes_watts_and_preserves_missing_history(
    qapp: QApplication,
) -> None:
    tile = tricorder.PowerTile("power", "Power", "#ffcc00", 350.0)
    tile.update_power(250.0)
    assert tile._graph.history[-1] == pytest.approx(250.0 / 350.0 * 100.0)
    before = list(tile._graph.history)
    tile.update_power(None)
    assert list(tile._graph.history) == before
    assert tile._value_lbl.text() == "k.A."
    tile.deleteLater()
    qapp.processEvents()


def test_monitor_reuses_provided_windows_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        tricorder, "_windows_detect_dgpus",
        lambda: (_ for _ in ()).throw(AssertionError("inventory was re-enumerated")),
    )
    monkeypatch.setattr(tricorder, "get_dxgi_adapter_map", lambda: {})
    monitor = tricorder.HardwareMonitorThread(
        drive_info=[], dgpu_info=[("AMD Radeon Test", 16.0, "0x1234")])
    assert monitor._dgpu_info == [("AMD Radeon Test", 16.0, "0x1234")]
    monitor.stop()


def test_identical_amd_device_ids_disable_ambiguous_adlx_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tricorder, "NVML_AVAILABLE", False)
    monkeypatch.setattr(tricorder, "get_dxgi_adapter_map", lambda: {})
    monitor = tricorder.HardwareMonitorThread(
        drive_info=[],
        dgpu_info=[
            ("AMD Radeon Test", 16.0, "0x1234"),
            ("AMD Radeon Test", 16.0, "0x1234"),
        ],
    )
    assert monitor._ambiguous_adlx_device_ids == {"0x1234"}
    monitor.stop()


def test_windows_nvml_wddm_mapping_and_power(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = object()

    class FakePci:
        pciDeviceId = (0x2684 << 16) | 0x10DE

    class FakeNvml:
        @staticmethod
        def nvmlDeviceGetCount():
            return 1

        @staticmethod
        def nvmlDeviceGetHandleByIndex(index):
            assert index == 0
            return handle

        @staticmethod
        def nvmlDeviceGetName(gpu_handle):
            assert gpu_handle is handle
            return b"NVIDIA GeForce RTX 4090"

        @staticmethod
        def nvmlDeviceGetPciInfo(gpu_handle):
            assert gpu_handle is handle
            return FakePci()

        @staticmethod
        def nvmlDeviceGetPowerUsage(gpu_handle):
            assert gpu_handle is handle
            return 425_000

        @staticmethod
        def nvmlShutdown():
            return None

    monkeypatch.setattr(tricorder.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tricorder, "NVML_AVAILABLE", True)
    monkeypatch.setattr(tricorder, "pynvml", FakeNvml)
    monkeypatch.setattr(tricorder, "get_dxgi_adapter_map", lambda: {})
    monitor = tricorder.HardwareMonitorThread(
        drive_info=[],
        dgpu_info=[("NVIDIA GeForce RTX 4090", 24.0, "0x2684")],
    )
    assert monitor._nvml_wddm_handles[0] is handle
    assert tricorder._read_nvml_power_watts(handle) == pytest.approx(425.0)
    monitor.stop()


def test_theme_dropdown_and_responsive_date_persist(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "layout.json"
    config_path.write_text(json.dumps({
        "version": "1.0",
        "theme": "light",
    }), encoding="utf-8")
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", config_path)
    monkeypatch.setattr(tricorder, "_get_cpu_topology", lambda: None)
    monkeypatch.setattr(tricorder, "build_drive_info", lambda: [])
    monkeypatch.setattr(tricorder.HardwareMonitorThread, "start", lambda self: None)
    monkeypatch.setattr(tricorder.HardwareMonitorThread, "stop", lambda self: None)

    dashboard = tricorder.TricorderDashboard()
    try:
        assert dashboard._theme == "light"
        assert dashboard._theme_combo.currentData() == "light"
        assert "#eef2f6" in dashboard.styleSheet()
        assert "#ffffff" in dashboard._tiles["cpu_total"].styleSheet()

        graph = dashboard._tiles["cpu_total"]._graph
        graph._grid_cache = (1, 1, tricorder.QPixmap(1, 1))
        dashboard._apply_theme_selection("dark", persist=True)
        assert dashboard._theme_combo.currentData() == "dark"
        assert "#0a0a0f" in dashboard.styleSheet()
        assert graph._grid_cache is None
        dashboard._apply_theme_selection("light", persist=True)
        assert "#eef2f6" in dashboard.styleSheet()
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["theme"] == "light"
        assert saved["tile_order"]

        dashboard._update_clock()
        assert dashboard._time_lbl.text().count(":") == 2
        dashboard._update_clock_layout(1400)
        time_index = dashboard._clock_grid.indexOf(dashboard._time_lbl)
        date_index = dashboard._clock_grid.indexOf(dashboard._date_lbl)
        assert dashboard._clock_grid.getItemPosition(time_index)[:2] == (0, 0)
        assert dashboard._clock_grid.getItemPosition(date_index)[:2] == (0, 1)
        assert not dashboard._date_lbl.isHidden()

        dashboard._update_clock_layout(1100)
        date_index = dashboard._clock_grid.indexOf(dashboard._date_lbl)
        assert dashboard._clock_grid.getItemPosition(date_index)[:2] == (1, 0)
        assert not dashboard._date_lbl.isHidden()

        dashboard._update_clock_layout(800)
        assert dashboard._clock_grid.indexOf(dashboard._time_lbl) >= 0
        assert dashboard._date_lbl.isHidden()
        assert dashboard._title_lbl.isHidden()
        assert dashboard._time_lbl.minimumWidth() >= 135
        dashboard._btn_edit.setChecked(True)
        assert dashboard._btn_update.isHidden()
        assert dashboard._btn_edit.text() == "✔"
        dashboard._btn_edit.setChecked(False)
        assert not dashboard._btn_update.isHidden()
    finally:
        dashboard.close()
        dashboard.deleteLater()
        tricorder._set_active_theme("dark")
        qapp.processEvents()


def test_dashboard_constructs_headlessly(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", tmp_path / "layout.json")
    monkeypatch.setattr(tricorder, "_get_cpu_topology", lambda: None)
    monkeypatch.setattr(tricorder, "build_drive_info", lambda: [])
    monkeypatch.setattr(tricorder.HardwareMonitorThread, "start", lambda self: None)

    dashboard = tricorder.TricorderDashboard()
    assert "System Tricorder" in dashboard.windowTitle()
    assert dashboard.minimumWidth() > 0
    assert "cpu_power" not in dashboard._tiles
    dashboard.hw_thread.stop()
    dashboard.close()
    qapp.processEvents()


def test_sensor_poller_keeps_last_value_while_driver_call_blocks() -> None:
    import threading
    import time

    release = threading.Event()
    calls = []
    teardown_threads = []

    def poll():
        calls.append(1)
        if len(calls) == 2:
            release.wait(5.0)      # simulated ~5 s ADLX stall
        return len(calls)

    poller = tricorder._SensorPoller(
        "test", 0.01, poll,
        teardown=lambda: teardown_threads.append(threading.current_thread().name))
    poller.start()
    deadline = time.monotonic() + 2.0
    while poller.latest() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert poller.latest() == 1

    # The blocked call must not block readers; the value simply ages.
    started = time.monotonic()
    assert poller.latest() == 1
    assert time.monotonic() - started < 0.1
    time.sleep(0.05)
    assert poller.latest(max_age=0.01) is None
    assert poller.latest(max_age=10.0) == 1

    release.set()
    poller.request_stop()
    poller.join(2.0)
    assert not poller.is_alive()
    assert teardown_threads == ["tricorder-test"]


def test_windows_disk_poller_computes_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(tricorder.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tricorder, "NVML_AVAILABLE", False)
    monkeypatch.setattr(tricorder, "get_dxgi_adapter_map", lambda: {})
    monitor = tricorder.HardwareMonitorThread(
        drive_info=[("PhysicalDrive0", "C:")],
        dgpu_info=[("AMD Radeon Test", 16.0, "0x1234")],
    )
    mib = 1024 * 1024
    samples = iter([
        (SimpleNamespace(read_bytes=0, write_bytes=0),
         {"PhysicalDrive0": SimpleNamespace(read_bytes=0, write_bytes=0)}),
        (SimpleNamespace(read_bytes=20 * mib, write_bytes=10 * mib),
         {"PhysicalDrive0": SimpleNamespace(read_bytes=20 * mib, write_bytes=10 * mib)}),
    ])
    current = {}

    def disk_io_counters(perdisk: bool = False):
        if not perdisk:
            current["pair"] = next(samples)
            return current["pair"][0]
        return current["pair"][1]

    times = iter((100.0, 102.0))
    monkeypatch.setattr(tricorder.psutil, "disk_io_counters", disk_io_counters)
    monkeypatch.setattr(tricorder.time, "monotonic", lambda: next(times))
    monitor._setup_disk()
    rmb, wmb, drives = monitor._poll_disk()
    monkeypatch.undo()
    assert rmb == pytest.approx(10.0)
    assert wmb == pytest.approx(5.0)
    assert drives[0].label == "C:"
    assert drives[0].read_mbps == pytest.approx(10.0)
    assert drives[0].write_mbps == pytest.approx(5.0)


def test_adlx_close_unloads_dll_for_safe_reinit(monkeypatch: pytest.MonkeyPatch) -> None:
    freed = []

    class FakeFreeLibrary:
        argtypes = None

        def __call__(self, handle):
            freed.append(handle.value)
            return 1

    class FakeKernel32:
        FreeLibrary = FakeFreeLibrary()

    monkeypatch.setattr(tricorder.ctypes, "WinDLL", lambda _name: FakeKernel32(),
                        raising=False)
    sampler = tricorder._AdlxGpuSampler.__new__(tricorder._AdlxGpuSampler)
    sampler._ok = True
    sampler._started = False
    sampler._initialized = True
    sampler._terminate = lambda: 0
    sampler._gpus = []
    sampler._perf = tricorder.ctypes.c_void_p()
    sampler._system = tricorder.ctypes.c_void_p()
    sampler._dll = type("FakeDll", (), {"_handle": 0x1234})()
    sampler.close()
    assert freed == [0x1234]
    assert sampler._dll is None
    assert not sampler.ok


def test_autostart_mode_and_cli_parsing() -> None:
    assert tricorder._normalise_autostart_mode("MAXIMIZED") == "maximized"
    assert tricorder._normalise_autostart_mode("minimized") == "minimized"
    assert tricorder._normalise_autostart_mode("invalid") == "normal"

    assert tricorder._parse_startup_mode_arg([]) is None
    assert tricorder._parse_startup_mode_arg(["--start-minimized"]) == "minimized"
    assert tricorder._parse_startup_mode_arg(["--minimized"]) == "minimized"
    assert tricorder._parse_startup_mode_arg(["--start-maximized"]) == "maximized"
    assert tricorder._parse_startup_mode_arg(["--maximized"]) == "maximized"
    assert tricorder._parse_startup_mode_arg(["--start-tray"]) == "tray"
    assert tricorder._parse_startup_mode_arg(
        ["--start-minimized", "--start-maximized"]) == "maximized"

    assert tricorder._autostart_platform_label("Windows") == "Windows"
    assert tricorder._autostart_platform_label("Darwin") == "macOS"
    assert tricorder._autostart_platform_label("Linux") == "Linux"


def test_autostart_command_and_desktop_escaping(tmp_path: Path) -> None:
    exe_dir = tmp_path / "Program Files"
    exe_dir.mkdir()
    py_exe = exe_dir / "python.exe"
    pyw_exe = exe_dir / "pythonw.exe"
    script = exe_dir / "system_tricorder.py"
    py_exe.write_bytes(b"")
    pyw_exe.write_bytes(b"")
    script.write_text("", encoding="utf-8")

    cmd_win_src = tricorder._autostart_command(
        "minimized",
        system_name="Windows",
        frozen=False,
        executable=str(py_exe),
        script_path=script,
    )
    assert cmd_win_src == [
        str(pyw_exe.resolve()), str(script.resolve()), "--start-minimized"
    ]

    frozen_bin = exe_dir / "SystemTricorder"
    frozen_bin.write_bytes(b"")
    cmd_frozen_max = tricorder._autostart_command(
        "maximized",
        system_name="Linux",
        frozen=True,
        executable=str(frozen_bin),
    )
    assert cmd_frozen_max == [str(frozen_bin.resolve()), "--start-maximized"]

    cmd_normal = tricorder._autostart_command(
        "normal",
        system_name="Darwin",
        frozen=True,
        executable=str(frozen_bin),
    )
    assert cmd_normal == [str(frozen_bin.resolve())]

    escaped = tricorder._format_desktop_exec(
        ["/opt/System Tricorder/app", "--flag=$HOME", "100%"])
    assert escaped == '"/opt/System Tricorder/app" "--flag=\\$HOME" 100%%'


def test_cross_platform_os_autostart_enable_and_disable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fake_exe = tmp_path / "SystemTricorder"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr(tricorder.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tricorder.sys, "executable", str(fake_exe))

    # Linux XDG autostart (.desktop)
    xdg_dir = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    tricorder._set_os_autostart(True, "minimized", system_name="Linux")
    desktop_file = xdg_dir / "autostart" / "system-tricorder.desktop"
    assert desktop_file.is_file()
    assert tricorder._is_os_autostart_enabled(system_name="Linux")
    desktop_text = desktop_file.read_text(encoding="utf-8")
    assert "--start-minimized" in desktop_text
    assert "X-GNOME-Autostart-enabled=true" in desktop_text
    tricorder._set_os_autostart(False, system_name="Linux")
    assert not desktop_file.exists()
    assert not tricorder._is_os_autostart_enabled(system_name="Linux")

    # macOS LaunchAgent (.plist)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    tricorder._set_os_autostart(True, "maximized", system_name="Darwin")
    plist_file = (
        tmp_path / "Library" / "LaunchAgents" / "com.dawasteh.system-tricorder.plist"
    )
    assert plist_file.is_file()
    assert tricorder._is_os_autostart_enabled(system_name="Darwin")
    plist_data = tricorder.plistlib.loads(plist_file.read_bytes())
    assert plist_data["Label"] == "com.dawasteh.system-tricorder"
    assert plist_data["RunAtLoad"] is True
    assert plist_data["ProgramArguments"][-1] == "--start-maximized"
    tricorder._set_os_autostart(False, system_name="Darwin")
    assert not plist_file.exists()
    assert not tricorder._is_os_autostart_enabled(system_name="Darwin")

    # Windows Registry (HKCU\...\Run)
    reg_store: dict[str, str] = {}

    class FakeRegKey:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeWinReg:
        HKEY_CURRENT_USER = object()
        KEY_SET_VALUE = 2
        KEY_QUERY_VALUE = 1
        REG_SZ = 1

        @staticmethod
        def CreateKeyEx(_root, _subkey, _reserved, _access):
            return FakeRegKey()

        @staticmethod
        def OpenKey(_root, _subkey, _reserved, _access):
            return FakeRegKey()

        @staticmethod
        def SetValueEx(_key, name, _reserved, _typ, value):
            reg_store[name] = value

        @staticmethod
        def QueryValueEx(_key, name):
            if name not in reg_store:
                raise FileNotFoundError(name)
            return reg_store[name], 1

        @staticmethod
        def DeleteValue(_key, name):
            if name not in reg_store:
                raise FileNotFoundError(name)
            del reg_store[name]

    monkeypatch.setattr(tricorder, "WINREG_AVAILABLE", True)
    monkeypatch.setattr(tricorder, "winreg", FakeWinReg)
    tricorder._set_os_autostart(True, "minimized", system_name="Windows")
    assert tricorder._is_os_autostart_enabled(system_name="Windows")
    assert "--start-minimized" in reg_store["SystemTricorder"]
    tricorder._set_os_autostart(False, system_name="Windows")
    assert not tricorder._is_os_autostart_enabled(system_name="Windows")


def test_options_dialog_controls_and_values(qapp: QApplication) -> None:
    dlg = tricorder.OptionsDialog(
        minimize_to_tray=False,
        close_to_tray=False,
        autostart_enabled=False,
        autostart_mode="normal",
        tray_available=True,
    )
    try:
        assert not dlg._combo_mode.isEnabled()
        assert dlg.values() == (False, False, False, "normal")

        dlg._cb_autostart.setChecked(True)
        assert dlg._combo_mode.isEnabled()
        min_idx = dlg._combo_mode.findData("minimized")
        dlg._combo_mode.setCurrentIndex(min_idx)
        assert "Taskleiste" in dlg._mode_hint.text()

        dlg._cb_close_tray.setChecked(True)
        assert "System-Tray" in dlg._mode_hint.text()
        assert dlg.values() == (False, True, True, "minimized")

        dlg._cb_tray.setChecked(True)
        assert dlg.values() == (True, True, True, "minimized")
    finally:
        dlg.deleteLater()
        qapp.processEvents()


def test_dashboard_tray_minimize_restore_and_startup_modes(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    config_path = tmp_path / "layout.json"
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", config_path)
    monkeypatch.setattr(tricorder, "_get_cpu_topology", lambda: None)
    monkeypatch.setattr(tricorder, "build_drive_info", lambda: [])
    monkeypatch.setattr(tricorder.HardwareMonitorThread, "start", lambda self: None)
    monkeypatch.setattr(tricorder.HardwareMonitorThread, "stop", lambda self: None)
    monkeypatch.setattr(tricorder, "_system_tray_available", lambda: True)

    recorded_autostart: list[tuple[bool, str]] = []
    monkeypatch.setattr(
        tricorder, "_set_os_autostart",
        lambda enabled, mode="normal", **_kw: recorded_autostart.append((enabled, mode)),
    )

    class FakeTray:
        def __init__(self, icon, parent=None) -> None:
            self.icon = icon
            self.parent = parent
            self.visible = False
            self.tooltip = ""
            self.menu = None
            self.activated = type(
                "Sig", (), {"connect": lambda _self, _fn: None})()

        def setToolTip(self, text: str) -> None:
            self.tooltip = text

        def setContextMenu(self, menu) -> None:
            self.menu = menu

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

    monkeypatch.setattr(tricorder, "QSystemTrayIcon", FakeTray)

    dashboard = tricorder.TricorderDashboard()
    try:
        assert dashboard._tray_icon is None
        assert dashboard._apply_app_options(
            minimize_to_tray=True,
            close_to_tray=True,
            autostart_enabled=True,
            autostart_mode="minimized",
            sync_os_autostart=True,
        )
        assert recorded_autostart == [(True, "minimized")]
        assert dashboard._tray_icon is not None
        assert dashboard._tray_icon.visible is True

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["minimize_to_tray"] is True
        assert saved["close_to_tray"] is True
        assert saved["autostart"] is True
        assert saved["autostart_mode"] == "minimized"

        # Show normally, save geometry, close (X) hides to tray when close_to_tray=True.
        dashboard.show()
        qapp.processEvents()
        dashboard.close()
        qapp.processEvents()
        assert dashboard._in_tray is True
        assert dashboard.isHidden()
        assert dashboard._tray_icon.visible is True
        assert dashboard._tray_toggle_action is not None
        assert dashboard._tray_toggle_action.text() == "Fenster anzeigen"

        dashboard._restore_from_tray()
        qapp.processEvents()
        assert dashboard._in_tray is False
        assert not dashboard.isHidden()
        assert dashboard._tray_toggle_action.text() == "In den Tray minimieren"
    finally:
        dashboard._force_close()
        assert dashboard._tray_icon is not None and not dashboard._tray_icon.visible
        dashboard.deleteLater()
        qapp.processEvents()

    # Starting with --start-minimized while minimize_to_tray=True starts directly
    # in the tray and preserves previously saved window placement on close.
    saved_before = json.loads(config_path.read_text(encoding="utf-8"))
    saved_window_before = saved_before["window"]

    tray_start = tricorder.TricorderDashboard()
    try:
        assert tray_start.apply_startup_mode("minimized") == "tray"
        assert tray_start._in_tray is True
        assert tray_start.isHidden()
        assert tray_start._tray_icon is not None and tray_start._tray_icon.visible
    finally:
        tray_start.close()
        assert tray_start._tray_icon is not None and not tray_start._tray_icon.visible
        tray_start.deleteLater()
        qapp.processEvents()

    saved_after = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved_after["window"] == saved_window_before

    # Starting with --start-maximized launches maximized.
    max_start = tricorder.TricorderDashboard()
    try:
        assert max_start.apply_startup_mode("maximized") == "maximized"
        qapp.processEvents()
        assert max_start.isMaximized()
    finally:
        max_start._force_close()
        max_start.deleteLater()
        qapp.processEvents()


def test_config_sanitizer_and_corrupt_min_row_h_recovery(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    assert tricorder._normalise_min_row_h("abc") == 75
    assert tricorder._normalise_min_row_h(None) == 75
    assert tricorder._normalise_min_row_h(True) == 75
    assert tricorder._normalise_min_row_h(-200) == 50
    assert tricorder._normalise_min_row_h(9999) == 400
    assert tricorder._normalise_min_row_h("120") == 120
    assert tricorder._normalise_min_row_h(float("nan")) == 75

    sanitised = tricorder._sanitise_config({
        "theme": "LIGHT",
        "minimize_to_tray": "false",
        "close_to_tray": True,
        "autostart": 1,
        "autostart_mode": "MAXIMIZED",
        "min_row_h": "corrupt",
        "tile_colors": {
            "cpu_total": "#AbC",
            "bad": "red",
            "": "#112233",
        },
    })
    assert sanitised == {
        "theme": "light",
        "minimize_to_tray": False,
        "close_to_tray": True,
        "autostart": False,
        "autostart_mode": "maximized",
        "min_row_h": 75,
        "max_row_h": 180,
        "tile_colors": {"cpu_total": "#aabbcc"},
    }
    assert tricorder._sanitise_config(["not", "a", "dict"])["theme"] == "dark"

    config_path = tmp_path / "layout.json"
    config_path.write_text(json.dumps({
        "version": "1.0",
        "tile_order": ["cpu_total"],
        "hidden_tiles": [],
        "min_row_h": "not-an-int",
    }), encoding="utf-8")
    monkeypatch.setattr(tricorder, "CONFIG_FILE", config_path)
    tile = tricorder.MetricTile("cpu_total", "CPU", "#00d4ff")
    grid = tricorder.TileGrid(
        {"cpu_total": tile}, {"cpu_total": "CPU"}, ["cpu_total"])
    try:
        assert grid._min_row_h == 75
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["min_row_h"] == 75
    finally:
        grid.deleteLater()
        qapp.processEvents()


def test_tile_grid_rejects_sentinel_and_unknown_tile_mutations(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(tricorder, "CONFIG_FILE", tmp_path / "layout.json")
    t1 = tricorder.MetricTile("cpu_total", "CPU", "#00d4ff")
    t2 = tricorder.MetricTile("ram", "RAM", "#00ff88")
    grid = tricorder.TileGrid(
        {"cpu_total": t1, "ram": t2},
        {"cpu_total": "CPU", "ram": "RAM"},
        ["cpu_total", "__row__", "ram"],
    )
    try:
        before = list(grid._tile_order)
        grid.show_tile("__row__")
        grid.show_tile("unknown_tile")
        grid._on_move("__row__", "ram", True)
        grid._on_move("cpu_total", "__row__", False)
        assert grid._tile_order == before
    finally:
        grid.deleteLater()
        qapp.processEvents()


def test_pdh_power_deduplicates_duplicate_instances_and_rapl_delta() -> None:
    # Duplicate #1 suffix aliases of the same counter name must not double-count,
    # while distinct multi-socket package names still sum.
    assert tricorder._cpu_package_power_from_pdh([
        ("CPU Power", 65_000.0),
        ("CPU Power#1", 65_000.0),
    ]) == pytest.approx(65.0)
    assert tricorder._cpu_package_power_from_pdh([
        ("RAPL_Package0_PKG", 40_000.0),
        ("RAPL_Package0_PKG#1", 40_000.0),
        ("RAPL_Package1_PKG", 55_000.0),
    ]) == pytest.approx(95.0)

    assert tricorder._linux_rapl_energy_delta(100, 350, 1000) == 250
    assert tricorder._linux_rapl_energy_delta(900, 100, 1000) == 200
    assert tricorder._linux_rapl_energy_delta(900, 100, None) is None
    assert tricorder._linux_rapl_energy_delta(-1, 100, 1000) is None


def test_linux_disk_io_clamps_negative_aggregate_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(tricorder.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tricorder, "NVML_AVAILABLE", False)
    monkeypatch.setattr(tricorder, "_linux_detect_gpus", lambda: [])
    monitor = tricorder.HardwareMonitorThread(drive_info=[("sda", "sda")])
    mib = 1024 * 1024
    agg_samples = iter([
        SimpleNamespace(read_bytes=50 * mib, write_bytes=40 * mib),
        SimpleNamespace(read_bytes=10 * mib, write_bytes=5 * mib),
    ])
    per_samples = iter([
        {"sda": SimpleNamespace(read_bytes=50 * mib, write_bytes=40 * mib)},
        {"sda": SimpleNamespace(read_bytes=10 * mib, write_bytes=5 * mib)},
    ])

    def fake_disk_io(perdisk: bool = False):
        return next(per_samples) if perdisk else next(agg_samples)

    times = iter((10.0, 11.0))
    monkeypatch.setattr(tricorder.psutil, "disk_io_counters", fake_disk_io)
    monkeypatch.setattr(tricorder.psutil, "cpu_percent",
                        lambda interval=None, percpu=False: [10.0] if percpu else 10.0)
    monkeypatch.setattr(tricorder.psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=8 * 1024**3, used=4 * 1024**3, percent=50.0))
    monkeypatch.setattr(tricorder.time, "monotonic", lambda: next(times))

    def stop_after_one_publish(metrics):
        monitor._latest = metrics
        monitor._running = False

    monkeypatch.setattr(monitor, "_publish", stop_after_one_publish)
    monkeypatch.setattr(tricorder.time, "sleep", lambda _s: None)
    monitor._running = True
    monitor._run_linux()
    assert monitor._latest is not None
    assert monitor._latest.disk_read_mbps == 0.0
    assert monitor._latest.disk_write_mbps == 0.0
    assert monitor._latest.drives[0].read_mbps == 0.0
    assert monitor._latest.drives[0].write_mbps == 0.0


def test_updater_guards_backup_deletion_and_resilient_source_rollback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    script = tricorder._build_standalone_exe_update_bat(
        Path("C:/Apps/SystemTricorder.exe"),
        Path("C:/Temp/system-tricorder-update-1/release.exe"),
        42,
        Path("C:/Temp/release-update.log"),
    )
    exe_check_idx = script.index('if not exist "%EXE%"')
    del_backup_idx = script.index('del /F /Q "%BACKUP%"')
    assert exe_check_idx < del_backup_idx

    install = tmp_path / "install"
    staging = tmp_path / "staging"
    _write_source_update_fixture(install, "2.7.7")
    _write_source_update_fixture(staging, "2.7.8")
    before = {
        rel: (install / rel).read_bytes()
        for rel in tricorder._UPDATE_SOURCE_FILES
    }
    original_replace = Path.replace

    def fail_third_install_and_second_rollback(self: Path, target: Path) -> Path:
        if self.name.startswith(".SystemTricorder.png.update-"):
            raise OSError("install failed on 3rd file")
        if self.name.startswith(".requirements.txt.backup-"):
            raise OSError("rollback locked on 2nd file")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_third_install_and_second_rollback)
    with pytest.raises(OSError, match="install failed"):
        tricorder._install_staged_source_files(staging, install)
    # Even though requirements.txt rollback failed, system_tricorder.py (1st file)
    # must still be rolled back and never left unrolled.
    assert (install / "system_tricorder.py").read_bytes() == before["system_tricorder.py"]
    assert (install / "assets" / "SystemTricorder.png").read_bytes() == before["assets/SystemTricorder.png"]
