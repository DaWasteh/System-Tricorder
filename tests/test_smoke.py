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
    assert tricorder.APP_VERSION == "2.7"
    version_info = (Path(tricorder.__file__).parent / "assets" / "version_info.txt").read_text(
        encoding="utf-8")
    assert "filevers=(2, 7, 0, 0)" in version_info
    assert "ProductVersion', '2.7.0.0'" in version_info


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


def test_layout_migration_inserts_power_tiles_idempotently() -> None:
    order = [
        "gpu_0_vram", "gpu_1_vram", "__row__",
        "cpu_total", "igpu",
    ]
    hidden = ["gpu_2_vram"]
    tile_ids = [
        "gpu_0_vram", "gpu_1_vram", "gpu_2_vram",
        "cpu_total", "igpu", "gpu_0_power", "gpu_1_power",
        "gpu_2_power", "cpu_power", "optional_tile",
    ]
    default_order = [
        "cpu_total", "cpu_power", "__row__",
        "gpu_0_vram", "gpu_0_power", "gpu_1_vram", "gpu_1_power",
        "gpu_2_vram", "gpu_2_power",
    ]

    migrated_order, migrated_hidden, changed = tricorder._merge_layout_tiles(
        order, hidden, tile_ids, default_order)
    assert changed
    assert migrated_order[:4] == [
        "gpu_0_vram", "gpu_0_power", "gpu_1_vram", "gpu_1_power"
    ]
    assert migrated_order[-3:] == ["cpu_total", "cpu_power", "igpu"]
    assert "gpu_2_power" in migrated_hidden
    assert "optional_tile" in migrated_hidden

    again_order, again_hidden, changed_again = tricorder._merge_layout_tiles(
        migrated_order, migrated_hidden, tile_ids, default_order)
    assert not changed_again
    assert again_order == migrated_order
    assert again_hidden == migrated_hidden


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
