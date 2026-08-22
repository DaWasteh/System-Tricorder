import json
import os
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
    assert tricorder.APP_VERSION == "2.7.4"
    assert tricorder.CONFIG_VERSION == "1.0"
    root = Path(tricorder.__file__).parent
    version_info = (root / "assets" / "version_info.txt").read_text(encoding="utf-8")
    for expected in (
        "filevers=(2, 7, 4, 0)",
        "prodvers=(2, 7, 4, 0)",
        "FileVersion', '2.7.4.0'",
        "ProductVersion', '2.7.4.0'",
    ):
        assert expected in version_info
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "version-2.7.4-00ff88" in readme
    assert "### v2.7.4" in readme
    release_builder = (root / ".github" / "scripts" / "build_release.py").read_text(
        encoding="utf-8")
    assert 'generated_spec_dir = build_dir / "generated-spec"' in release_builder
    assert '"--specpath"' in release_builder


def test_gpu_palettes() -> None:
    assert len(tricorder.GPU_PALETTES) >= 4
    for palette in tricorder.GPU_PALETTES:
        assert len(palette) == 4
        assert all(color.startswith("#") for color in palette)


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
