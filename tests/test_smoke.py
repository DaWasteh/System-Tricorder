import os

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
    assert gpu.gpu_3d_percent == 0.0
    assert gpu.gpu_vram_total_gb == 8.0


def test_gpu_palettes() -> None:
    assert len(tricorder.GPU_PALETTES) >= 4
    for palette in tricorder.GPU_PALETTES:
        assert len(palette) == 4
        assert all(color.startswith("#") for color in palette)


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


def test_dashboard_constructs_headlessly(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tricorder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tricorder, "_get_cpu_topology", lambda: None)
    monkeypatch.setattr(tricorder, "build_drive_info", lambda: [])
    monkeypatch.setattr(tricorder.HardwareMonitorThread, "start", lambda self: None)

    dashboard = tricorder.TricorderDashboard()
    assert "System Tricorder" in dashboard.windowTitle()
    assert dashboard.minimumWidth() > 0
    dashboard.hw_thread.stop()
    dashboard.close()
    qapp.processEvents()
