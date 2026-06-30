#!/usr/bin/env python3
import sys
import time
import math
import json
import re
import platform
import logging
import contextlib
import ctypes
from ctypes import wintypes
import psutil
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# ── High-DPI scaling helpers ──────────────────────────────────────────────────
# Enable high-DPI scaling BEFORE QApplication is created.
# These env vars MUST be set before QApplication instantiation.
import os
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR"] = ""          # let Qt auto-detect per-monitor DPI
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

# Global DPI scale factor — set AFTER QApplication is created via _init_dp_scale()
_DP_SCALE: float = 1.0


def _init_dp_scale(app: "QApplication") -> float:
    """Compute DPI scale factor from Qt's primary screen devicePixelRatio.

    Must be called AFTER QApplication is created.  Returns the scale factor
    and stores it in the module-level _DP_SCALE variable so that all
    dp() / font_size() calls throughout the codebase use the correct value.
    """
    global _DP_SCALE
    try:
        screen = app.primaryScreen()
        if screen is not None:
            scale = screen.devicePixelRatio()  # type: ignore[union-attr]
            _DP_SCALE = float(scale)
        else:
            _DP_SCALE = 1.0
    except Exception:
        _DP_SCALE = 1.0
    return _DP_SCALE


def dp(px: float) -> int:
    """Convert a logical pixel value to a DPI-aware pixel value.

    Uses the pre-computed _DP_SCALE which reflects Qt's per-monitor DPI.
    """
    return max(1, int(px * _DP_SCALE))


def font_size(pt: float) -> str:
    """Return a CSS font-size string scaled for DPI."""
    return f"{pt * _DP_SCALE:.1f}px"


def set_font_size(label: "QLabel", pt: float, **kwargs) -> None:
    """Convenience: set font-size on a QLabel with DPI scaling."""
    style = f"font-size: {font_size(pt)};"
    for k, v in kwargs.items():
        style += f" {k}: {v};"
    current = label.styleSheet()
    # Append or replace font-size
    existing = re.search(r"font-size:\s*[\d.]+px", current)
    if existing:
        label.setStyleSheet(current[:existing.start()] + style + current[existing.end():])
    else:
        label.setStyleSheet(current + " " + style if current else style)


# ── Qt imports ─────────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import (                                       # type: ignore
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QGridLayout, QSizePolicy, QPushButton,
    QScrollArea, QDialog, QCheckBox, QDialogButtonBox,
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal, QThread, QMimeData, QPoint, QByteArray  # type: ignore
from PyQt6.QtGui   import (                                         # type: ignore
    QColor, QPainter, QPainterPath, QPen, QBrush, QDrag, QPixmap,
)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(Path.home() / ".tricorder.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("tricorder")

# ── WMI / WinReg ──────────────────────────────────────────────────────────────
try:
    import pythoncom            # type: ignore
    import win32com.client      # type: ignore
    WMI_AVAILABLE = True
except ImportError:
    pythoncom  = None           # type: ignore
    win32com   = None           # type: ignore
    WMI_AVAILABLE = False

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    winreg = None               # type: ignore
    WINREG_AVAILABLE = False

# ── Layout / window config ────────────────────────────────────────────────────
CONFIG_FILE = Path.home() / ".tricorder_layout.json"
CONFIG_VERSION = "0.8"


def _load_config_file() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        if data.get('version') != CONFIG_VERSION:
            logger.warning(
                "Config version '%s' differs from '%s' — "
                "layout/window state will be kept as-is; delete %s to reset.",
                data.get('version'), CONFIG_VERSION, CONFIG_FILE,
            )
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("Config load: %s", exc)
        return {}


def _save_config_file(data: dict) -> None:
    payload = dict(data)
    payload['version'] = CONFIG_VERSION
    tmp = CONFIG_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(CONFIG_FILE)          # atomic on same filesystem

# ── GPU colour palettes (up to 4 discrete GPUs) ───────────────────────────────
GPU_PALETTES = [
    ("#ff5500", "#ff7700", "#ff9900", "#ffaa00"),   # GPU 0 — Amber
    ("#00cc66", "#00aa55", "#009944", "#00ff88"),   # GPU 1 — Emerald
    ("#aa00ff", "#8800cc", "#cc44ff", "#dd88ff"),   # GPU 2 — Violet
    ("#0088ff", "#0066cc", "#0055aa", "#44aaff"),   # GPU 3 — Sapphire
]

_VIRTUAL_NAMES = ('microsoft basic', 'remote desktop', 'parsec', 'virtual',
                  'citrix', 'vmware', 'indirect')

# Discrete Intel Arc model numbers — anything NOT in this list is the iGPU
# (Arrow Lake / Meteor Lake have an iGPU called "Intel Arc Graphics" with no model number)
_ARC_DMODEL = ('a310', 'a380', 'a580', 'a750', 'a770', 'b580', 'b770')

# AMD iGPU PCI Device IDs — only these are real integrated GPUs
# (Ryzen 5800X3D has NO iGPU; Ryzen 7040/8040+ have RDNA2 iGPU)
_AMD_IGPU_DEV_IDS = ('15d8', '15d9', '164e', '164f', '1681', '1682',  # RDNA2 iGPUs
                     '1636', '1637', '1638', '1639', '163c', '163d',  # older APU iGPUs
                     '1002',)  # fallback: VEN_1002 without DEV = not an iGPU

# Drive tile colours
DRIVE_R_COLOR = "#00ffcc"   # read  — teal
DRIVE_W_COLOR = "#ffcc00"   # write — amber


# ═══════════════════════════════════════════════════════════════════════════════
# HARDWARE DETECTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_registry_gpu_vrams() -> List[float]:
    """Returns sorted-descending list of real GPU VRAM sizes (GB) from Registry."""
    vrams: List[float] = []
    if winreg is None:
        return [8.0]
    try:
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E968-E325-11CE-BFC1-08002BE10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            for i in range(30):
                try:
                    with winreg.OpenKey(key, f"{i:04d}") as sub:
                        best = 0.0
                        for val in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                d, _ = winreg.QueryValueEx(sub, val)
                                raw = int.from_bytes(d, 'little') if isinstance(d, bytes) else int(d)
                                best = max(best, raw / (1024 ** 3))
                            except FileNotFoundError:
                                pass
                        if best >= 1.0:
                            vrams.append(float(math.ceil(best)))
                except OSError:
                    pass
    except Exception:
        pass
    vrams.sort(reverse=True)
    return vrams if vrams else [8.0]


def get_dxgi_adapter_map() -> Dict[str, Tuple[str, float]]:
    """
    Enumerate GPU adapters via DXGI and return a mapping:
        luid_string -> (device_id_hex, dedicated_vram_gb)

    The LUID string is formatted to match the LUID that appears verbatim in the
    Windows GPU performance-counter names, e.g. "luid_0x00000000_0x0001c2e3".
    DXGI is the authoritative source here: every adapter's kernel LUID is the
    same one the perf counters use, and DXGI_ADAPTER_DESC also carries the PCI
    DeviceId — so this lets us bind a perf-counter LUID directly to a physical
    GPU instead of guessing by VRAM size.

    Returns {} on any failure (non-Windows, no DXGI, etc.); callers degrade
    gracefully when the map is empty.
    """
    result: Dict[str, Tuple[str, float]] = {}
    try:
        import ctypes
        from ctypes import POINTER, byref, c_void_p

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_uint32),
                        ("Data2", ctypes.c_uint16),
                        ("Data3", ctypes.c_uint16),
                        ("Data4", ctypes.c_ubyte * 8)]

        class _LUID(ctypes.Structure):          # NB: LowPart precedes HighPart
            _fields_ = [("LowPart", ctypes.c_uint32),
                        ("HighPart", ctypes.c_int32)]

        class _DXGI_ADAPTER_DESC(ctypes.Structure):
            _fields_ = [
                ("Description", ctypes.c_wchar * 128),
                ("VendorId", ctypes.c_uint32),
                ("DeviceId", ctypes.c_uint32),
                ("SubSysId", ctypes.c_uint32),
                ("Revision", ctypes.c_uint32),
                ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID),
            ]

        # IID_IDXGIFactory {7b7166ec-21c7-44ae-b21a-c9ae321ae369}
        iid = _GUID(0x7b7166ec, 0x21c7, 0x44ae,
                    (ctypes.c_ubyte * 8)(0xb2, 0x1a, 0xc9, 0xae,
                                         0x32, 0x1a, 0xe3, 0x69))

        dxgi = ctypes.WinDLL("dxgi")            # type: ignore[attr-defined]
        factory = c_void_p()
        if dxgi.CreateDXGIFactory(byref(iid), byref(factory)) != 0 or not factory:
            return {}

        def _method(obj: c_void_p, index: int, restype, argtypes):
            vtbl = ctypes.cast(obj, POINTER(c_void_p))[0]
            fn   = ctypes.cast(vtbl, POINTER(c_void_p))[index]
            return ctypes.WINFUNCTYPE(restype, *argtypes)(fn)   # type: ignore[attr-defined]

        # IDXGIFactory vtable: 2=Release, 7=EnumAdapters
        enum_adapters   = _method(factory, 7, ctypes.c_long,
                                  [c_void_p, ctypes.c_uint32, POINTER(c_void_p)])
        factory_release = _method(factory, 2, ctypes.c_ulong, [c_void_p])

        i = 0
        while True:
            adapter = c_void_p()
            if enum_adapters(factory, i, byref(adapter)) != 0 or not adapter:
                break
            try:
                # IDXGIAdapter vtable: 2=Release, 8=GetDesc
                get_desc        = _method(adapter, 8, ctypes.c_long,
                                          [c_void_p, POINTER(_DXGI_ADAPTER_DESC)])
                adapter_release = _method(adapter, 2, ctypes.c_ulong, [c_void_p])
                desc = _DXGI_ADAPTER_DESC()
                if get_desc(adapter, byref(desc)) == 0:
                    high = desc.AdapterLuid.HighPart & 0xFFFFFFFF
                    low  = desc.AdapterLuid.LowPart  & 0xFFFFFFFF
                    luid_str = f"luid_0x{high:08x}_0x{low:08x}"
                    dev_id   = f"0x{desc.DeviceId:04X}"
                    vram_gb  = desc.DedicatedVideoMemory / (1024 ** 3)
                    result[luid_str] = (dev_id, vram_gb)
                adapter_release(adapter)
            except Exception:
                pass
            i += 1

        factory_release(factory)
    except Exception as exc:
        logger.debug("DXGI adapter enumeration failed: %s", exc)
        return {}
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PDH GPU SAMPLER  (Task-Manager-grade, cache-free engine utilization)
# ═══════════════════════════════════════════════════════════════════════════════

class _PdhFmtValue(ctypes.Structure):
    """PDH_FMT_COUNTERVALUE — CStatus (4) + pad (4) + value union (8)."""
    _fields_ = [("CStatus", wintypes.DWORD), ("_pad", wintypes.DWORD),
                ("doubleValue", ctypes.c_double)]


class _PdhCounterItem(ctypes.Structure):
    """PDH_FMT_COUNTERVALUE_ITEM_W — instance name + formatted value."""
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _PdhFmtValue)]


class _PdhGpuSampler:
    """Read GPU engine utilization straight from the Windows PDH (Performance
    Data Helper) API — the same data source Task Manager uses.

    Why not WMI?  ``Win32_PerfFormattedData_...GPUEngine`` is *cached* by the
    perflib adapter for ~1 second: at 30 FPS the same stale value is returned
    ~30 times before it jumps.  On bursty workloads (AI/compute on RDNA3/4) the
    cache window often lands on an idle gap, so the dashboard read "long
    stretches of 0 %" while Adrenalin reported a sustained load.  PDH returns a
    freshly computed value on every collect — no cache.

    A single wildcard counter is registered::

        \\GPU Engine(*)\\Utilization Percentage

    PDH maintains the highly-dynamic per-process instance list internally, so
    we never enumerate instances ourselves.  ``sample()`` returns every live
    engine as ``(name_lower, util)`` — the exact shape the old WMI rows had —
    so the aggregation pipeline is reused verbatim.

    The "GPU Engine" / "Utilization Percentage" names are English-only on
    every locale (absent from all non-009 Perflib language tables), so the
    English path is universal and safe on German/other-language Windows.
    """

    _PDH_FMT_DOUBLE = 0x00000200
    _PATH = "\\GPU Engine(*)\\Utilization Percentage"

    def __init__(self) -> None:
        self._ok = False
        self._query:  Optional[ctypes.c_void_p] = None
        self._counter: Optional[ctypes.c_void_p] = None
        try:
            self._pdh = ctypes.WinDLL("pdh")
            query = ctypes.c_void_p()
            if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
                return
            counter = ctypes.c_void_p()
            if self._pdh.PdhAddCounterW(query, self._PATH, 0, ctypes.byref(counter)) != 0:
                self._pdh.PdhCloseQuery(query)
                return
            self._query = query
            self._counter = counter
            self._pdh.PdhCollectQueryData(query)          # prime: ≥2 collects needed for a rate
            self._ok = True
        except Exception as exc:
            logger.debug("PDH GPU sampler init failed: %s", exc)
            self._ok = False

    @property
    def ok(self) -> bool:
        return self._ok

    def sample(self) -> List[Tuple[str, float]]:
        if not self._ok or self._query is None:
            return []
        try:
            self._pdh.PdhCollectQueryData(self._query)
            size = wintypes.DWORD(0)
            count = wintypes.DWORD(0)
            self._pdh.PdhGetFormattedCounterArrayW(
                self._counter, self._PDH_FMT_DOUBLE,
                ctypes.byref(size), ctypes.byref(count), None)
            if size.value == 0:
                return []
            buf = (ctypes.c_ubyte * size.value)()
            if self._pdh.PdhGetFormattedCounterArrayW(
                    self._counter, self._PDH_FMT_DOUBLE,
                    ctypes.byref(size), ctypes.byref(count), buf) != 0:
                return []
            arr = (_PdhCounterItem * count.value).from_buffer(buf)
            return [(str(it.szName).lower(), float(it.FmtValue.doubleValue))
                    for it in arr
                    if it.szName and it.FmtValue.CStatus == 0]
        except Exception as exc:
            logger.debug("PDH GPU sample failed: %s", exc)
            return []

    def close(self) -> None:
        if self._query is not None:
            with contextlib.suppress(Exception):
                self._pdh.PdhCloseQuery(self._query)
        self._ok = False
        self._query = None
        self._counter = None


def get_wmi_gpu_list() -> List[Tuple[str, bool, float, str]]:
    """
    Returns (name, is_igpu, vram_gb, pnp_device_id) for all real GPUs via WMI.
    Sorted: dGPUs first (desc VRAM), then iGPUs.

    iGPU detection rules
    --------------------
    Intel: any Intel GPU whose name does NOT contain a discrete Arc model number
           (e.g. A770, B580) is treated as iGPU.  This correctly classifies
           "Intel(R) Arc(TM) Graphics" (Arrow Lake / Meteor Lake integrated)
           as iGPU while keeping Arc A/B dGPUs as dGPU.
    AMD:   traditional integrated markers (Radeon(TM) Graphics, Vega) without RX.
    """
    result: List[Tuple[str, bool, float, str]] = []
    if not WMI_AVAILABLE:
        return result
    try:
        pythoncom.CoInitialize()                                    # type: ignore
        wmi = win32com.client.GetObject("winmgmts:root\\cimv2")    # type: ignore
        for c in wmi.ExecQuery("SELECT Name, AdapterRAM, PNPDeviceID FROM Win32_VideoController"):
            try:
                name = str(c.Name or '').strip()
                pnp_id = str(c.PNPDeviceID or '').strip()
                if not name or any(v in name.lower() for v in _VIRTUAL_NAMES):
                    continue
                nl = name.lower()
                pnp_lower = pnp_id.lower()

                # AMD iGPU detection via PNPDeviceID VEN/DEV
                # AMD integrated GPUs have VEN_1002 + specific DEV IDs
                # Ryzen 5800X3D has NO iGPU — any AMD GPU without known dGPU DEV is NOT an iGPU
                is_amd = 'amd' in nl or 'advanced micro devices' in nl
                is_igpu = False
                if is_amd:
                    # Check for discrete AMD GPU DEV IDs — if present, NOT an iGPU
                    amd_dgpu_devs = ('1001', '1002', '1003', '1004', '1005', '1006',  # RX 5000/6000
                                     '164c', '164d', '164e', '164f',  # RX 6000 series
                                     '17fd', '17fe', '17df', '17e3',  # RX 7000 series
                                     '742f', '743f', '7430', '7431',  # RX 9000 series
                                     '67df', '67d0', '67d1', '67d8',  # RX 6000M
                                     '67e0', '67e1', '67e8', '67ef')  # RX 6000M
                    dev_match = re.search(r'dev_([0-9a-f]{4})', pnp_lower)
                    if dev_match:
                        dev_id = dev_match.group(1)
                        if dev_id not in amd_dgpu_devs:
                            # Could be iGPU — check against known AMD iGPU DEV IDs
                            amd_igpu_devs = ('15d8', '15d9', '164e', '164f', '1681', '1682',
                                             '1636', '1637', '1638', '1639', '163c', '163d')
                            is_igpu = dev_id in amd_igpu_devs
                        # else: known dGPU DEV → not iGPU
                    # else: no DEV in PNPID → not an iGPU (fallback: assume dGPU or virtual)
                else:
                    is_igpu = (
                        # Intel iGPU: any Intel GPU without a known discrete Arc model number
                        ('intel' in nl and not any(m in nl for m in _ARC_DMODEL)) or
                        # AMD iGPU: integrated Radeon (Vega/RDNA-integrated, no RX prefix)
                        ('amd' in nl and ('radeon(tm) graphics' in nl or 'vega' in nl) and 'rx ' not in nl)
                    )

                vram = float(c.AdapterRAM or 0) / (1024 ** 3)
                result.append((name, is_igpu, vram, pnp_id))
            except Exception:
                pass   # skip problematic WMI rows without aborting the loop
    except Exception:
        pass
    result.sort(key=lambda x: (int(x[1]), -x[2]))
    return result


def short_gpu_name(name: str) -> str:
    """Shortens a GPU name to ~22 chars for compact display."""
    for kw in ('RTX', 'RX ', 'GTX', 'RX', 'Arc', 'Radeon', 'NVIDIA', 'AMD'):
        idx = name.find(kw)
        if idx != -1:
            return name[idx:idx + 22].strip()
    return name[:22].strip()


def build_drive_info() -> List[Tuple[str, str]]:
    """
    Returns [(psutil_disk_key, display_label), ...] for all physical drives.

    Strategy
    --------
    1. Enumerate psutil.disk_io_counters(perdisk=True) keys.
    2. On Windows + WMI: map PhysicalDriveN → drive-letter(s) via
       Win32_LogicalDiskToPartition.
    3. Fall back to friendly key renaming (PhysicalDrive0 → "Drive 0" etc.)
    4. Skip Linux loop devices.
    """
    result: List[Tuple[str, str]] = []
    try:
        io = psutil.disk_io_counters(perdisk=True)
        if not io:
            return []

        letter_map: Dict[str, str] = {}

        if platform.system() == 'Windows' and WMI_AVAILABLE:
            try:
                pythoncom.CoInitialize()                            # type: ignore
                wmi = win32com.client.GetObject("winmgmts:root\\cimv2")  # type: ignore
                for row in wmi.ExecQuery(
                    "SELECT Antecedent, Dependent "
                    "FROM Win32_LogicalDiskToPartition"
                ):
                    ant = str(row.Antecedent)
                    dep = str(row.Dependent)
                    dm = re.search(r'Disk #(\d+)', ant)
                    lm = re.search(r'"([A-Z]:)"', dep)
                    if dm and lm:
                        key = f"PhysicalDrive{dm.group(1)}"
                        letter = lm.group(1)
                        if key in letter_map:
                            letter_map[key] += f"/{letter}"
                        else:
                            letter_map[key] = letter
            except Exception:
                pass

        for key in sorted(io.keys()):
            if platform.system() == 'Linux' and key.startswith('loop'):
                continue
            if key in letter_map:
                label = letter_map[key]
            else:
                label = (key
                         .replace('PhysicalDrive', 'Drive ')
                         .replace('nvme', 'NVMe ')
                         .replace('mmcblk', 'SD ')
                         .replace('sd', 'Disk '))
                label = re.sub(r'\s+', ' ', label).strip()
            result.append((key, label))
    except Exception:
        pass
    return result


def detect_npu_present() -> bool:
    """Return True only if a real Neural Processing Unit is present.

    NPUs are enumerated as PnP devices, not as GPU performance counters, so
    presence must be probed via Win32_PnPEntity.  We match on *specific* device
    names (Intel "AI Boost", AMD "NPU Compute Accelerator"/"IPU Device",
    Qualcomm "Hexagon") rather than the bare token "npu" — "%NPU%" would match
    every "USB Input Device" ("i-NPU-t").  The leading space in "% NPU%" keeps
    "Intel(R) NPU" matchable while still excluding mid-word hits like "Input".

    Returns False on non-Windows / no WMI, so the NPU tile is simply omitted.
    """
    if not WMI_AVAILABLE:
        return False
    try:
        pythoncom.CoInitialize()                                    # type: ignore
        wmi = win32com.client.GetObject("winmgmts:root\\cimv2")    # type: ignore
        query = (
            "SELECT Name FROM Win32_PnPEntity WHERE "
            "Name LIKE '%AI Boost%' OR "
            "Name LIKE '%Neural Processor%' OR "
            "Name LIKE '%NPU Compute%' OR "
            "Name LIKE '%IPU Device%' OR "
            "Name LIKE '% NPU%' OR "
            "Name LIKE '%Hexagon%'"
        )
        for _ in wmi.ExecQuery(query):
            return True
    except Exception as exc:
        logger.debug("NPU detection failed: %s", exc)
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GPUMetrics:
    name: str
    luid: str
    gpu_3d_percent:      float = 0.0
    gpu_compute_percent: float = 0.0
    gpu_copy0_percent:   float = 0.0
    gpu_copy1_percent:   float = 0.0
    gpu_codec_percent:   float = 0.0
    gpu_vram_used_gb:    float = 0.0
    gpu_vram_total_gb:   float = 8.0


@dataclass
class DriveMetrics:
    key:        str      # psutil disk key  (e.g. "PhysicalDrive0")
    label:      str      # display label    (e.g. "C:/D:")
    read_mbps:  float    # MB/s read
    write_mbps: float    # MB/s write


@dataclass
class SystemMetrics:
    cpu_total_percent: float
    cpu_cores:         Dict[int, float]
    ram_total_gb:      float
    ram_used_gb:       float
    ram_percent:       float
    gpus:              List[GPUMetrics]
    igpu_percent:      float
    npu_percent:       float
    disk_read_mbps:    float   # aggregate (kept for compat)
    disk_write_mbps:   float   # aggregate
    drives:            List[DriveMetrics]   # per-physical-drive
    timestamp:         datetime


# ═══════════════════════════════════════════════════════════════════════════════
# HARDWARE MONITOR THREAD  (30 FPS)
# ═══════════════════════════════════════════════════════════════════════════════

class HardwareMonitorThread(QThread):
    metrics_updated = pyqtSignal(SystemMetrics)

    def __init__(self, drive_info: List[Tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self._running         = False
        self._com_initialized = False
        self._drive_info      = drive_info   # [(key, label), ...]

        # GPU static info - now with PNPDeviceID for consistent GPU identification
        reg_vrams = get_registry_gpu_vrams()
        wmi_gpus  = get_wmi_gpu_list()
        dgpu_wmi  = [(n, v, p) for n, ig, v, p in wmi_gpus if not ig]

        # Build GPU info list with device ID extracted from PNPDeviceID
        # Format: (name, vram_gb, device_id_hex) e.g., "0x7550" for RX 9070 XT
        self._dgpu_info: List[Tuple[str, float, str]] = []
        for i, (name, wv, pnp_id) in enumerate(dgpu_wmi):
            vram = reg_vrams[i] if i < len(reg_vrams) else (math.ceil(wv) if wv >= 1.0 else 8.0)
            # Extract device ID from PNPDeviceID: PCI\VEN_1002&DEV_7550&... -> 0x7550
            dev_id = ""
            dev_match = re.search(r'DEV_([0-9A-Fa-f]{4})', pnp_id)
            if dev_match:
                dev_id = "0x" + dev_match.group(1).upper()
            self._dgpu_info.append((name, float(vram), dev_id))
        if not self._dgpu_info:
            self._dgpu_info = [("GPU", reg_vrams[0], "")]

        # Authoritative LUID → (device_id, vram_gb) map from DXGI.  Static for the
        # lifetime of the process, so we build it once here.  Used to bind each
        # GPU performance-counter LUID to the correct physical GPU.
        self._luid_device_map: Dict[str, Tuple[str, float]] = get_dxgi_adapter_map()

        self._luid_order: List[str]       = []
        self._luid_vram:  Dict[str, float] = {}
        self._luid_device_id: Dict[str, str] = {}  # Map LUID → device ID for GPU name lookup

        # PDH GPU engine sampler — cache-free, Task-Manager-grade utilization.
        # Vendor-neutral: reads the same Windows "GPU Engine" counter for
        # NVIDIA, Intel and AMD, so all three improve equally.
        self._pdh: _PdhGpuSampler = _PdhGpuSampler()

    def run(self) -> None:
        self._running = True
        if WMI_AVAILABLE:
            try:
                pythoncom.CoInitialize()                            # type: ignore
                self._com_initialized = True
            except Exception as exc:
                logger.warning("CoInitialize failed: %s", exc)
        try:
            wmi = win32com.client.GetObject("winmgmts:root\\cimv2") if WMI_AVAILABLE else None  # type: ignore
        except Exception as exc:
            logger.warning("WMI connect failed: %s", exc)
            wmi = None

        self._last_io      = psutil.disk_io_counters()
        self._last_io_per  = psutil.disk_io_counters(perdisk=True) or {}
        self._last_t       = time.time()

        while self._running:
            try:
                now = time.time()
                dt  = max(now - self._last_t, 0.001)

                # ── Aggregate disk I/O ──────────────────────────────────────
                io_agg  = psutil.disk_io_counters()
                rmb = wmb = 0.0
                if io_agg and self._last_io:
                    rmb = (io_agg.read_bytes  - self._last_io.read_bytes)  / (1024 * 1024) / dt
                    wmb = (io_agg.write_bytes - self._last_io.write_bytes) / (1024 * 1024) / dt
                self._last_io = io_agg

                # ── Per-drive I/O ───────────────────────────────────────────
                io_per  = psutil.disk_io_counters(perdisk=True) or {}
                drives: List[DriveMetrics] = []
                for key, label in self._drive_info:
                    if key in io_per and key in self._last_io_per:
                        r = max(0.0, (io_per[key].read_bytes  - self._last_io_per[key].read_bytes)  / (1024 * 1024) / dt)
                        w = max(0.0, (io_per[key].write_bytes - self._last_io_per[key].write_bytes) / (1024 * 1024) / dt)
                    else:
                        r = w = 0.0
                    drives.append(DriveMetrics(key=key, label=label, read_mbps=r, write_mbps=w))
                self._last_io_per = io_per
                self._last_t = now

                # ── CPU ─────────────────────────────────────────────────────
                cpu_total = psutil.cpu_percent(interval=None)
                cpu_cores = {i: float(v) for i, v in enumerate(psutil.cpu_percent(percpu=True))}
                ram       = psutil.virtual_memory()

                # ── GPU (WMI) ───────────────────────────────────────────────
                igpu_p = npu_p = 0.0
                luid_data: Dict[str, dict] = {}

                if wmi:
                    # ── GPU engine utilization ─────────────────────────────────
                    # Windows GPU perf counters use a per-process format:
                    #   pid_PPPP_luid_0xAAAA_0xBBBB_phys_0_eng_N_engtype_TYPE
                    # We extract the GPU LUID with a regex, aggregate max util
                    # per (luid, eng_idx) across all processes, then sum across
                    # engine indices of the same type to get total GPU utilization.
                    _IGPU_MARKERS = ('hd graphics', 'uhd graphics', 'iris',
                                     'intel(r) graphics', 'arc(tm) graphics')
                    # NPU detection: only specific markers, NOT generic 'npu' which matches AMD engines
                    _NPU_MARKERS  = ('ai boost', 'npu acceleration', 'intel npu',
                                     'xe media', 'media engine')
                    _LUID_RE = re.compile(r'luid_(0x[0-9a-f]+_0x[0-9a-f]+)')
                    _ENG_RE  = re.compile(r'_eng_(\d+)_')

                    # Engine utilization — prefer PDH (cache-free, fresh every
                    # frame; the same source Task Manager uses).  WMI's formatted
                    # counter is cached ~1 s, which made bursty RDNA compute
                    # workloads read as "long stretches of 0 %".
                    _engine_rows: list = []      # list of (name_lower, util)
                    if self._pdh.ok:
                        _engine_rows = self._pdh.sample()
                    elif wmi:
                        try:
                            _engine_rows = [
                                (str(r.Name).lower(), float(r.UtilizationPercentage or 0))
                                for r in wmi.ExecQuery(
                                    "SELECT Name, UtilizationPercentage "
                                    "FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine"
                                )
                            ]
                        except Exception as exc:
                            logger.debug("GPU engine query: %s", exc)

                    # ── Step 1: seed luid_data from engine rows ────────────────
                    for _e in _engine_rows:
                        try:
                            _en = _e[0]
                            if any(x in _en for x in _IGPU_MARKERS):
                                continue
                            if any(x in _en for x in _NPU_MARKERS):
                                continue
                            _m = _LUID_RE.search(_en)
                            if _m:
                                _luid = 'luid_' + _m.group(1)
                                luid_data.setdefault(_luid, {'3d': 0.0, 'compute': 0.0,
                                                             'c0': 0.0, 'c1': 0.0, 'codec': 0.0, 'used': 0.0})
                        except Exception:
                            pass

                    # ── Step 2: fill VRAM usage from memory query ──────────────
                    try:
                        for a in wmi.ExecQuery(
                            "SELECT Name, DedicatedUsage "
                            "FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory"
                        ):
                            try:
                                luid = str(a.Name).lower().split('_phys')[0]
                                used = float(a.DedicatedUsage or 0) / (1024 ** 3)
                                ld = luid_data.setdefault(luid, {'3d': 0.0, 'compute': 0.0,
                                                                 'c0': 0.0, 'c1': 0.0, 'codec': 0.0, 'used': 0.0})
                                ld['used'] = max(ld['used'], used)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # ── Step 3: aggregate engine utilization ───────────────────
                    # Pass A: max util per (luid, eng_idx) across all processes
                    _eng_max: Dict[tuple, tuple] = {}
                    for _e in _engine_rows:
                        try:
                            _en   = _e[0]
                            _util = _e[1]
                            if _util <= 0:
                                continue
                            if any(x in _en for x in _IGPU_MARKERS):
                                igpu_p = max(igpu_p, _util)
                                continue
                            if any(x in _en for x in _NPU_MARKERS):
                                npu_p = max(npu_p, _util)
                                continue
                            _lm = _LUID_RE.search(_en)
                            if not _lm:
                                continue
                            _cl = 'luid_' + _lm.group(1)
                            if _cl not in luid_data:
                                continue
                            _em = _ENG_RE.search(_en)
                            _ei = int(_em.group(1)) if _em else 0
                            if any(x in _en for x in ('3d', 'graphics_1')):
                                _et = '3d'
                            elif any(x in _en for x in ('compute', 'cuda')):
                                _et = 'compute'
                            elif 'copy' in _en:
                                _et = 'copy'
                            elif any(x in _en for x in ('codec', 'decode', 'encode')):
                                # AMD exposes a single 'VideoCodec' engine, but
                                # NVIDIA (and Intel) split it into 'VideoDecode'
                                # and 'VideoEncode'.  Matching all three keeps
                                # the codec tile populated on every vendor.
                                _et = 'codec'
                            else:
                                continue
                            _key = (_cl, _ei)
                            _prev = _eng_max.get(_key, (0.0, _et))[0]
                            _eng_max[_key] = (max(_util, _prev), _et)
                        except Exception:
                            pass

                    # Pre-compute copy engine index order per luid
                    _copy_order: Dict[str, list] = {}
                    for (_cl2, _ei2), (_, _et2) in _eng_max.items():
                        if _et2 == 'copy':
                            _copy_order.setdefault(_cl2, [])
                            if _ei2 not in _copy_order[_cl2]:
                                _copy_order[_cl2].append(_ei2)
                    for _k in _copy_order:
                        _copy_order[_k].sort()

                    # Pass B: sum unique (luid, eng_idx) entries into luid_data
                    for (_cl3, _ei3), (_eu3, _et3) in _eng_max.items():
                        if _cl3 not in luid_data:
                            continue
                        if _et3 == '3d':
                            luid_data[_cl3]['3d'] = min(luid_data[_cl3]['3d'] + _eu3, 100.0)
                        elif _et3 == 'compute':
                            luid_data[_cl3]['compute'] = min(luid_data[_cl3]['compute'] + _eu3, 100.0)
                        elif _et3 == 'copy':
                            _co = _copy_order.get(_cl3, [])
                            if _co and _ei3 == _co[0]:
                                luid_data[_cl3]['c0'] = max(luid_data[_cl3]['c0'], _eu3)
                            elif len(_co) > 1 and _ei3 == _co[1]:
                                luid_data[_cl3]['c1'] = max(luid_data[_cl3]['c1'], _eu3)
                        elif _et3 == 'codec':
                            # Video Codec Engine: take max util per (luid, eng_idx)
                            luid_data[_cl3]['codec'] = max(luid_data[_cl3]['codec'], _eu3)

                    # ── Engine utilization: raw, unsmoothed ─────────────────────
                    # PDH returns a fresh instantaneous sample every frame.  We
                    # pass the raw reading straight through -- no EMA, no delay.
                    # The old smoothing made idle cards crawl down to 0 over a
                    # second (and never reach it), so an idle card now shows a
                    # clean, instant 0 % the moment the load is gone.

                new: List[str] = sorted(
                    [luid for luid in luid_data if luid not in self._luid_order],
                    key=lambda luid: -luid_data[luid]['used'],
                )
                self._luid_order.extend(new)

                # ── Map LUIDs to GPU device IDs via DXGI adapter LUIDs ──────────────
                # Each live perf-counter LUID is resolved to its physical GPU through
                # the DXGI adapter map (LUID → PCI DeviceId).  This replaces the old
                # "sort LUIDs by *used* VRAM and pair with devices sorted by *total*
                # VRAM" heuristic, which silently swapped GPUs whenever the busier
                # card was not the one with the larger total VRAM (exactly the dual
                # 9070 XT / R9700 case).
                luid_to_device_id: Dict[str, str] = {
                    luid: self._luid_device_map[luid][0]
                    for luid in luid_data
                    if luid in self._luid_device_map
                }
                # Remember resolved ids across frames (covers transient query gaps)
                for _luid, _dev in luid_to_device_id.items():
                    self._luid_device_id[_luid] = _dev

                # device_id → live LUID (first wins)
                device_to_luid: Dict[str, str] = {}
                for luid in luid_data:
                    dev = luid_to_device_id.get(luid) or self._luid_device_id.get(luid, "")
                    if dev and dev not in device_to_luid:
                        device_to_luid[dev] = luid

                # LUIDs we could not bind to a device id (e.g. DXGI unavailable):
                # kept only as a best-effort positional fallback for that case.
                bound = set(device_to_luid.values())
                leftover = [luid for luid in self._luid_order if luid not in bound]

                # Emit GPUs in the SAME order as detected_gpus / self._dgpu_info, since
                # the dashboard tiles (gpu_<i>_*) are keyed by that index.  Each slot is
                # filled from its matching LUID's live metrics — never positionally.
                gpus: List[GPUMetrics] = []
                for name, vram_total, dev_id in self._dgpu_info:
                    luid = device_to_luid.get(dev_id, "")
                    if not luid and leftover:
                        luid = leftover.pop(0)      # degraded fallback only
                    d = luid_data.get(luid, {}) if luid else {}
                    if luid:
                        self._luid_vram[luid]      = vram_total
                        self._luid_device_id[luid] = dev_id
                    # Clamp used VRAM to total – never inflate total from usage spikes
                    used = min(d.get('used', 0.0), vram_total)
                    gpus.append(GPUMetrics(
                        name=name, luid=luid,
                        gpu_3d_percent=d.get('3d', 0.0),
                        gpu_compute_percent=d.get('compute', 0.0),
                        gpu_copy0_percent=d.get('c0', 0.0),
                        gpu_copy1_percent=d.get('c1', 0.0),
                        gpu_codec_percent=d.get('codec', 0.0),
                        gpu_vram_used_gb=used,
                        gpu_vram_total_gb=vram_total,
                    ))

                if not gpus:
                    gpus = [GPUMetrics(name=self._dgpu_info[0][0], luid='',
                                       gpu_vram_total_gb=self._dgpu_info[0][1])]

                self.metrics_updated.emit(SystemMetrics(
                    cpu_total_percent=cpu_total,
                    cpu_cores=cpu_cores,
                    ram_total_gb=ram.total / (1024 ** 3),
                    ram_used_gb=ram.used  / (1024 ** 3),
                    ram_percent=ram.percent,
                    gpus=gpus,
                    igpu_percent=igpu_p,
                    npu_percent=npu_p,
                    disk_read_mbps=rmb,
                    disk_write_mbps=wmb,
                    drives=drives,
                    timestamp=datetime.now(),
                ))
            except Exception as exc:
                logger.debug("Monitor loop error: %s", exc)
            time.sleep(1.0 / 30.0)  # Exact 30 FPS interval

    def stop(self) -> None:
        self._running = False
        self.wait()
        self._pdh.close()
        if self._com_initialized:
            try:
                pythoncom.CoUninitialize()                          # type: ignore
            except Exception as exc:
                logger.debug("CoUninitialize: %s", exc)
            self._com_initialized = False


# ═══════════════════════════════════════════════════════════════════════════════
# CPU TOPOLOGY  (unchanged from v0.2)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_cpu_topology() -> Optional[dict]:
    """
    Reads true P/E core topology via GetLogicalProcessorInformationEx.
    Returns dict with p_cores, p_threads, e_cores, e_threads, is_hybrid,
    or None on failure.
    Higher EfficiencyClass = P-core.
    """
    try:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        RelationProcessorCore = 0
        buf_size = ctypes.c_ulong(0)
        kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, None, ctypes.byref(buf_size))
        buf = (ctypes.c_ubyte * buf_size.value)()
        if not kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, buf, ctypes.byref(buf_size)):
            return None

        cores: list = []
        offset = 0
        while offset < buf_size.value:
            rel  = int.from_bytes(buf[offset    : offset + 4], 'little')
            size = int.from_bytes(buf[offset + 4: offset + 8], 'little')
            if size == 0:
                break
            if rel == RelationProcessorCore:
                eff         = buf[offset + 9]
                group_count = int.from_bytes(buf[offset + 30: offset + 32], 'little')
                threads = 0
                gm_off  = offset + 32
                for _ in range(group_count):
                    mask     = int.from_bytes(buf[gm_off: gm_off + 8], 'little')
                    threads += bin(mask).count('1')
                    gm_off  += 16
                cores.append((eff, threads))
            offset += size

        if not cores:
            return None

        eff_classes = sorted(set(c[0] for c in cores))
        if len(eff_classes) < 2:
            total_t = sum(t for _, t in cores)
            return {'is_hybrid': False,
                    'p_cores': len(cores), 'p_threads': total_t,
                    'e_cores': 0,          'e_threads': 0}

        max_eff = max(eff_classes)
        min_eff = min(eff_classes)
        p_group = [(e, t) for e, t in cores if e == max_eff]
        e_group = [(e, t) for e, t in cores if e == min_eff]
        return {
            'is_hybrid': True,
            'p_cores':   len(p_group), 'p_threads': sum(t for _, t in p_group),
            'e_cores':   len(e_group), 'e_threads': sum(t for _, t in e_group),
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# WIDGET PRIMITIVES
# ═══════════════════════════════════════════════════════════════════════════════

class SparklineWidget(QWidget):
    """
    Single horizontal sparkline with filled area.
    Expects values 0–100 (percentage).
    """
    def __init__(self, color_hex: str, history_len: int = 90,
                 min_height: int = 70, parent=None) -> None:
        super().__init__(parent)
        self.color   = QColor(color_hex)
        self.history: deque = deque([0.0] * history_len, maxlen=history_len)
        self._dirty  = False
        # Peak-hold state for the *displayed* percentage number.
        # Rises instantly to any new peak, then decays slowly — mirrors how
        # vendor tools (AMD Adrenalin) render bursty AI/compute workloads: the
        # driver-level activity meter sees every short kernel burst that the
        # Windows per-frame engine counter underreports, so it reads a stable
        # high number.  Peak-hold reproduces that: any burst pins the value up,
        # and only a genuinely idle period lets it fall.  At 30 FPS,
        # release=0.004/frame keeps a recurring burst train at ~96-99 % while a
        # truly idle GPU decays to ~0 over a few seconds.
        self._env = 0.0
        self._ENV_RELEASE = 0.004
        self._grid_cache: Optional[Tuple[int, int, QPixmap]] = None  # (w, h, pixmap)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(dp(min_height))

    def add_value(self, value: float) -> None:
        self.history.append(value)
        # Peak-hold envelope for the displayed number: jump instantly to any
        # new peak (captures short compute bursts AMD-style), decay slowly
        # afterwards.  Raw values still drive the sparkline graph verbatim.
        self._env = max(value, self._env * (1.0 - self._ENV_RELEASE))
        # Mark dirty but defer update - parent will batch-call update() once
        self._dirty = True

    def recent_avg(self, count: int = 30) -> float:
        """Peak-hold display value for the percentage number.

        Despite the name (kept for call-site compatibility) this returns the
        peak-hold envelope state, not an arithmetic mean.  The sparkline graph
        still shows the raw, instantly-reacting history; only the number is
        smoothed so it matches a vendor tool's stable high reading on bursty
        AI/compute workloads (a single short burst pins it near 100 %, exactly
        like AMD Adrenalin's activity meter)."""
        return self._env

    def batch_update(self) -> None:
        """Call once per frame after all add_value() calls."""
        if self._dirty:
            self._dirty = False
            self.update()

    def _ensure_grid_cache(self, w: int, h: int):
        """Cache gridlines pixmap — regenerated only on resize."""
        if self._grid_cache and self._grid_cache[0] == w and self._grid_cache[1] == h:
            return self._grid_cache[2]
        px = QPixmap(w, h)
        px.fill(QColor(12, 12, 20))
        p = QPainter(px)
        p.setPen(QPen(QColor(40, 40, 52), 1))
        for x in range(0, w, 25):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, 15):
            p.drawLine(0, y, w, y)
        p.end()
        self._grid_cache = (w, h, px)
        return px

    def paintEvent(self, _) -> None:                                        # type: ignore
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(self.rect())
        w, h = self.width(), self.height()

        # Optimized: draw cached gridlines pixmap instead of per-frame line loops
        grid_px = self._ensure_grid_cache(w, h)
        painter.drawPixmap(0, 0, grid_px)

        if not self.history:
            return
        path = QPainterPath()
        step = w / max(len(self.history) - 1, 1)
        for i, val in enumerate(self.history):
            y = h - (min(max(val, 0.0), 100.0) / 100.0 * h)
            if i == 0:
                path.moveTo(0, y)
            else:
                path.lineTo(i * step, y)

        painter.setPen(QPen(self.color, 2))
        painter.drawPath(path)

        fill = QPainterPath(path)
        fill.lineTo(w, h)
        fill.lineTo(0, h)
        fc = QColor(self.color)
        fc.setAlpha(35)
        painter.setBrush(QBrush(fc))
        painter.setPen(Qt.PenStyle.NoPen)                           # type: ignore
        painter.drawPath(fill)


# ── CPU-section tile (non-draggable, variant-styled, unchanged from v0.2) ─────

class MasterMetricBox(QFrame):
    """Used exclusively for the CPU core/thread grid.  Not draggable."""
    def __init__(self, title: str, color_hex: str, variant: str = 'standard', parent=None) -> None:
        super().__init__(parent)
        # DPI-aware sizing
        self.setMinimumHeight(dp(40))
        if variant == 'efficiency':
            frame_css = (
                f"border-top: 1px solid #1a1a28;"
                f"border-right: 1px solid #1a1a28;"
                f"border-bottom: 1px solid #1a1a28;"
                f"border-left: 3px solid {color_hex};"
                f"border-radius: 3px;"
            )
            bg = "#0d0d1c"
            title_extra = ""
        elif variant in ('ht', 'smt'):
            frame_css = (
                f"border: 1px solid #1a1a22;"
                f"border-top: 2px solid {color_hex};"
                f"border-radius: 6px;"
            )
            bg = "#0c0c16"
            title_extra = (f" <span style='font-size:8px; color:{color_hex}; opacity:0.7;'>"
                           f"{'HT' if variant == 'ht' else 'SMT'}</span>")
        else:
            frame_css = (
                f"border: 1px solid #222;"
                f"border-top: 3px solid {color_hex};"
                f"border-radius: 6px;"
            )
            bg = "#121218"
            title_extra = ""

        self.setStyleSheet(f"""
            QFrame {{ background-color: {bg}; {frame_css} }}
            QLabel {{ background: transparent; border: none; }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(dp(4), dp(3), dp(4), dp(3))
        layout.setSpacing(dp(1))

        header = QHBoxLayout()
        header.setSpacing(dp(4))
        self.id_lbl  = QLabel(f"{title}{title_extra}")
        self.id_lbl.setStyleSheet(f"color: {color_hex}; font-size: {font_size(11)}; font-weight: bold;")
        self.val_lbl = QLabel("0%")
        self.val_lbl.setStyleSheet(f"color: #888; font-size: {font_size(12)};")
        header.addWidget(self.id_lbl)
        header.addStretch()
        header.addWidget(self.val_lbl)
        layout.addLayout(header)

        _spark_min_h = int(18 * _DP_SCALE) if _DP_SCALE > 0 else 18
        self.graph = SparklineWidget(color_hex, min_height=_spark_min_h)
        layout.addWidget(self.graph)

    def update_val(self, val: float, text: Optional[str] = None) -> None:
        self.graph.add_value(val)
        self.val_lbl.setText(text if text else f"{int(self.graph.recent_avg())}%")

    def batch_update(self) -> None:
        self.graph.batch_update()
# ═══════════════════════════════════════════════════════════════════════════════

class BaseTile(QFrame):
    """
    Base class for all tiles in the customisable global grid.
    Provides drag-to-reorder and edit-mode × button.
    Subclass and implement _build_content().
    """
    move_requested     = pyqtSignal(str, str, bool)  # (src_id, target_id, insert_before)
    remove_requested   = pyqtSignal(str)              # tile_id
    rowbreak_requested = pyqtSignal(str)              # tile_id — toggle row-break before this tile

    _BTN_SIZE = 18  # logical pixels — scaled in __init__

    def __init__(self, tile_id: str, color_hex: str, parent=None) -> None:
        super().__init__(parent)
        self.tile_id      = tile_id
        self._color_hex   = color_hex
        self._edit_mode   = False
        self._drop_hl     = False
        self._drop_before = True
        self._drag_pos: Optional[QPoint] = None

        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._apply_frame_style(color_hex, edit=False)

        self._build_content()

        # ── × close button (top-right) ────────────────────────────────────────
        _btn_size = dp(self._BTN_SIZE)
        self._btn_x = QPushButton("×", self)
        self._btn_x.setFixedSize(_btn_size, _btn_size)
        self._btn_x.setStyleSheet(f"""
            QPushButton {{
                background: #880000; color: #fff;
                border-radius: {int(9 * _DP_SCALE)}px; font-size: {font_size(11)}; font-weight: bold;
            }}
            QPushButton:hover {{ background: #ff2222; }}
        """)
        self._btn_x.hide()
        self._btn_x.clicked.connect(lambda: self.remove_requested.emit(self.tile_id))

        # ── ↵ row-break button (top-left) ─────────────────────────────────────
        self._btn_rn = QPushButton("↵", self)
        self._btn_rn.setFixedSize(_btn_size, _btn_size)
        self._btn_rn.setToolTip("Toggle row break before this tile")
        self._rowbreak_active = False
        self._style_rn_btn()
        self._btn_rn.hide()
        self._btn_rn.clicked.connect(lambda: self.rowbreak_requested.emit(self.tile_id))

    def _style_rn_btn(self) -> None:
        _br = int(9 * _DP_SCALE)
        _fs = font_size(9)
        if self._rowbreak_active:
            self._btn_rn.setStyleSheet(f"""
                QPushButton {{
                    background: #00aa55; color: #fff;
                    border-radius: {_br}px; font-size: {_fs}; font-weight: bold;
                }}
                QPushButton:hover {{ background: #00ff88; color: #000; }}
            """)
        else:
            self._btn_rn.setStyleSheet(f"""
                QPushButton {{
                    background: #1a2a1a; color: #336633;
                    border-radius: {_br}px; font-size: {_fs}; font-weight: bold;
                    border: 1px solid #2a3a2a;
                }}
                QPushButton:hover {{ background: #2a3a2a; color: #00ff88; }}
            """)

    def set_rowbreak_active(self, active: bool) -> None:
        """Highlight ↵ when a row break is active before this tile."""
        self._rowbreak_active = active
        self._style_rn_btn()

    def _apply_frame_style(self, accent: str, edit: bool) -> None:
        border_side = "#3a3a2a" if edit else "#222"
        self.setStyleSheet(f"""
            QFrame {{
                background-color: #121218;
                border: 1px solid {border_side};
                border-top: 3px solid {accent};
                border-radius: 6px;
            }}
            QLabel      {{ background: transparent; border: none; }}
            QPushButton {{ background: transparent; border: none; }}
        """)

    def _build_content(self) -> None:
        """Override in subclass to populate the tile layout."""
        pass

    def batch_update(self) -> None:
        """No-op base - subclasses override to batch sparkline updates."""
        pass

    def update_val(self, value: float, suffix: Optional[str] = None) -> None:
        """Override in subclass to update a single-value tile."""
        pass

    def update_3d_compute(self, gpu_3d: float, compute: float) -> None:
        """Override in subclass to update GPU 3D/Compute tile."""
        pass

    def update_copy(self, copy0: float, copy1: float) -> None:
        """Override in subclass to update GPU Copy tile."""
        pass

    def update_drive(self, read_mbps: float, write_mbps: float) -> None:
        """Override in subclass to update Drive tile."""
        pass

    def update_codec(self, codec: float) -> None:
        """Override in subclass to update GPU Video Codec Engine tile."""
        pass

    # ── Edit mode ──────────────────────────────────────────────────────────────
    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._btn_x.setVisible(enabled)
        self._btn_rn.setVisible(enabled)
        self.setCursor(Qt.CursorShape.SizeAllCursor if enabled else Qt.CursorShape.ArrowCursor)  # type: ignore
        accent = "#ffdd55" if enabled else self._color_hex
        self._apply_frame_style(accent, edit=enabled)

    def resizeEvent(self, event) -> None:                                   # type: ignore
        super().resizeEvent(event)
        self._btn_x.move(self.width() - dp(self._BTN_SIZE) - 3, 3)
        self._btn_rn.move(3, 3)

    # ── Drag source ────────────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:                               # type: ignore
        if self._edit_mode and event.button() == Qt.MouseButton.LeftButton:    # type: ignore
            # event.position() returns QPointF in PyQt6; toPoint() converts to QPoint
            # so that drag.setHotSpot() (which requires QPoint) never gets a QPointF.
            self._drag_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:                                # type: ignore
        if not (self._edit_mode and self._drag_pos and
                event.buttons() & Qt.MouseButton.LeftButton):                  # type: ignore
            return
        if ((event.position().toPoint() - self._drag_pos).manhattanLength()
                < QApplication.startDragDistance()):
            return

        drag  = QDrag(self)
        mime  = QMimeData()
        mime.setText(self.tile_id)
        drag.setMimeData(mime)

        px = self.grab()
        drag.setPixmap(px)
        drag.setHotSpot(self._drag_pos)                             # QPoint — correct
        drag.exec(Qt.DropAction.MoveAction)                         # type: ignore
        self._drag_pos = None

    # ── Drop target ────────────────────────────────────────────────────────────
    def dragEnterEvent(self, event) -> None:                                # type: ignore
        if (self._edit_mode and event.mimeData().hasText()
                and event.mimeData().text() != self.tile_id):
            event.acceptProposedAction()
            self._drop_hl    = True
            self._drop_before = event.position().x() < self.width() / 2
            self.update()

    def dragMoveEvent(self, event) -> None:                                 # type: ignore
        if self._drop_hl:
            new_before = event.position().x() < self.width() / 2
            if new_before != self._drop_before:
                self._drop_before = new_before
                self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:                                # type: ignore
        self._drop_hl = False
        self.update()

    def dropEvent(self, event) -> None:                                     # type: ignore
        src = event.mimeData().text()
        if src != self.tile_id:
            insert_before = event.position().x() < self.width() / 2
            self.move_requested.emit(src, self.tile_id, insert_before)
            event.acceptProposedAction()
        self._drop_hl = False
        self.update()

    def paintEvent(self, event) -> None:                                    # type: ignore
        super().paintEvent(event)
        if self._drop_hl:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor("#ffdd55"), 3))                    # type: ignore
            if self._drop_before:
                # Vertical bar on left edge = "insert before this tile"
                p.drawLine(2, 4, 2, self.height() - 4)
            else:
                # Vertical bar on right edge = "insert after this tile"
                p.drawLine(self.width() - 2, 4, self.width() - 2, self.height() - 4)


# ═══════════════════════════════════════════════════════════════════════════════
# METRIC TILE  — single sparkline (CPU total, RAM, GPU, NPU, iGPU …)
# ═══════════════════════════════════════════════════════════════════════════════

class MetricTile(BaseTile):
    def __init__(self, tile_id: str, title: str, color_hex: str, parent=None) -> None:
        self._title     = title
        self._color_hex = color_hex
        super().__init__(tile_id, color_hex, parent)

    def _build_content(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(dp(6), dp(5), dp(6), dp(5))
        outer.setSpacing(dp(2))

        hdr = QHBoxLayout()
        self._title_lbl = QLabel(self._title)
        self._title_lbl.setStyleSheet(
            f"color: {self._color_hex}; font-size: {font_size(13)}; font-weight: bold;")
        self._val_lbl = QLabel("0%")
        self._val_lbl.setStyleSheet(f"color: #888; font-size: {font_size(14)};")
        hdr.addWidget(self._title_lbl)
        hdr.addStretch()
        hdr.addWidget(self._val_lbl)
        outer.addLayout(hdr)

        self._graph = SparklineWidget(self._color_hex)
        outer.addWidget(self._graph)

    def update_val(self, val: float, text: Optional[str] = None) -> None:
        self._graph.add_value(val)
        # Use a ~1 s moving average for the number so it stops jittering on
        # bursty workloads; the sparkline graph still reacts instantly.
        self._val_lbl.setText(text if text else f"{int(self._graph.recent_avg())}%")

    def batch_update(self) -> None:
        self._graph.batch_update()


# ═══════════════════════════════════════════════════════════════════════════════
# DRIVE TILE  — dual sparklines  Read ↑  /  Write ↓  in landscape layout
# ═══════════════════════════════════════════════════════════════════════════════

class DriveTile(BaseTile):
    """
    Landscape drive tile: drive label as header, then two mini-sparklines
    (Read + Write) stacked vertically.  Each sparkline is the same width as
    any MetricTile so the tile fits naturally in the same grid column.

    The MB/s axis auto-scales: the peak value slowly decays when load drops,
    so the graph always fills the vertical space meaningfully.
    """
    def __init__(self, tile_id: str, label: str, parent=None) -> None:
        self._label    = label
        self._color_hex = DRIVE_R_COLOR   # primary accent
        self._peak     = 100.0            # auto-scaling peak (MB/s)
        super().__init__(tile_id, DRIVE_R_COLOR, parent)

    def _build_content(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(dp(6), dp(5), dp(6), dp(5))
        outer.setSpacing(dp(3))

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        icon_lbl = QLabel("💾")
        icon_lbl.setStyleSheet(f"font-size: {font_size(12)};")
        name_lbl = QLabel(self._label)
        name_lbl.setStyleSheet(
            f"color: {DRIVE_R_COLOR}; font-size: {font_size(13)}; font-weight: bold;")
        self._peak_lbl = QLabel("↑100 MB/s")
        self._peak_lbl.setStyleSheet(f"color: #444; font-size: {font_size(11)};")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(3))
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        hdr.addWidget(self._peak_lbl)
        outer.addLayout(hdr)

        # ── Read row ──────────────────────────────────────────────────────────
        r_row = QHBoxLayout()
        r_row.setSpacing(dp(4))
        r_lbl = QLabel("R")
        r_lbl.setStyleSheet(f"color: {DRIVE_R_COLOR}; font-size: {font_size(12)}; font-weight: bold;")
        r_lbl.setFixedWidth(dp(12))
        _r_graph_h = int(24 * _DP_SCALE) if _DP_SCALE > 0 else 24
        self._r_graph = SparklineWidget(DRIVE_R_COLOR, min_height=_r_graph_h)
        self._r_val   = QLabel("0 MB/s")
        self._r_val.setStyleSheet(f"color: {DRIVE_R_COLOR}; font-size: {font_size(12)};")
        self._r_val.setFixedWidth(dp(72))
        self._r_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)   # type: ignore
        r_row.addWidget(r_lbl)
        r_row.addWidget(self._r_graph)
        r_row.addWidget(self._r_val)
        outer.addLayout(r_row)

        # ── Write row ─────────────────────────────────────────────────────────
        w_row = QHBoxLayout()
        w_row.setSpacing(dp(4))
        w_lbl = QLabel("W")
        w_lbl.setStyleSheet(f"color: {DRIVE_W_COLOR}; font-size: {font_size(12)}; font-weight: bold;")
        w_lbl.setFixedWidth(dp(12))
        _w_graph_h = int(24 * _DP_SCALE) if _DP_SCALE > 0 else 24
        self._w_graph = SparklineWidget(DRIVE_W_COLOR, min_height=_w_graph_h)
        self._w_val   = QLabel("0 MB/s")
        self._w_val.setStyleSheet(f"color: {DRIVE_W_COLOR}; font-size: {font_size(12)};")
        self._w_val.setFixedWidth(dp(72))
        self._w_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)   # type: ignore
        w_row.addWidget(w_lbl)
        w_row.addWidget(self._w_graph)
        w_row.addWidget(self._w_val)
        outer.addLayout(w_row)

    def update_drive(self, read_mbps: float, write_mbps: float) -> None:
        # Auto-scale: peak grows immediately, decays at 0.2 % per frame
        peak = max(read_mbps, write_mbps, 1.0)
        self._peak = max(self._peak * 0.998, peak)
        if peak > self._peak:
            self._peak = peak * 1.1     # headroom burst

        r_pct = read_mbps  / self._peak * 100.0
        w_pct = write_mbps / self._peak * 100.0

        self._r_graph.add_value(r_pct)
        self._w_graph.add_value(w_pct)
        self._r_val.setText(_fmt_mbps(read_mbps))
        self._w_val.setText(_fmt_mbps(write_mbps))
        self._peak_lbl.setText(f"↑{_fmt_mbps(self._peak)}")

    def batch_update(self) -> None:
        self._r_graph.batch_update()
        self._w_graph.batch_update()


def _fmt_mbps(v: float) -> str:
    """Format MB/s → auto-unit (GB/s if >= 1000)."""
    if v >= 1000:
        return f"{v / 1000:.2f} GB/s"
    if v >= 100:
        return f"{v:.0f} MB/s"
    return f"{v:.1f} MB/s"


# ═══════════════════════════════════════════════════════════════════════════════
# GPU ENGINES TILE  — three mini-sparklines: 3D / Copy0 / Copy1
# ═══════════════════════════════════════════════════════════════════════════════

class GPUCopyTile(BaseTile):
    """
    Landscape Copy-Engines tile: Copy0 + Copy1 as two stacked sparklines.
    Layout mirrors DriveTile.  palette[1]=Copy0 colour, palette[2]=Copy1 colour.
    """
    def __init__(self, tile_id: str, gpu_name: str,
                 palette: Tuple[str, str, str, str], parent=None) -> None:
        self._gpu_name  = gpu_name
        self._palette   = palette
        self._color_hex = palette[1]   # primary accent = Copy0 colour
        super().__init__(tile_id, palette[1], parent)

    def _build_content(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(dp(6), dp(5), dp(6), dp(5))
        outer.setSpacing(dp(3))

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        icon_lbl = QLabel("📋")
        icon_lbl.setStyleSheet(f"font-size: {font_size(13)};")
        name_lbl = QLabel(f"{self._gpu_name} · Copy")
        name_lbl.setStyleSheet(
            f"color: {self._palette[1]}; font-size: {font_size(13)}; font-weight: bold;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(3))
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── Copy0 row ─────────────────────────────────────────────────────────
        c0_row = QHBoxLayout()
        c0_row.setSpacing(dp(4))
        c0_lbl = QLabel("Cp0")
        c0_lbl.setStyleSheet(
            f"color: {self._palette[1]}; font-size: {font_size(12)}; font-weight: bold;")
        c0_lbl.setFixedWidth(dp(28))
        _c0_h = int(24 * _DP_SCALE) if _DP_SCALE > 0 else 24
        self._c0_graph = SparklineWidget(self._palette[1], min_height=_c0_h)
        self._c0_val   = QLabel("0%")
        self._c0_val.setStyleSheet(f"color: {self._palette[1]}; font-size: {font_size(12)};")
        self._c0_val.setFixedWidth(dp(34))
        self._c0_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        c0_row.addWidget(c0_lbl)
        c0_row.addWidget(self._c0_graph)
        c0_row.addWidget(self._c0_val)
        outer.addLayout(c0_row)

        # ── Copy1 row ─────────────────────────────────────────────────────────
        c1_row = QHBoxLayout()
        c1_row.setSpacing(dp(4))
        c1_lbl = QLabel("Cp1")
        c1_lbl.setStyleSheet(
            f"color: {self._palette[2]}; font-size: {font_size(12)}; font-weight: bold;")
        c1_lbl.setFixedWidth(dp(28))
        _c1_h = int(24 * _DP_SCALE) if _DP_SCALE > 0 else 24
        self._c1_graph = SparklineWidget(self._palette[2], min_height=_c1_h)
        self._c1_val   = QLabel("0%")
        self._c1_val.setStyleSheet(f"color: {self._palette[2]}; font-size: {font_size(12)};")
        self._c1_val.setFixedWidth(dp(34))
        self._c1_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        c1_row.addWidget(c1_lbl)
        c1_row.addWidget(self._c1_graph)
        c1_row.addWidget(self._c1_val)
        outer.addLayout(c1_row)

    def update_copy(self, c0: float, c1: float) -> None:
        self._c0_graph.add_value(c0)
        self._c1_graph.add_value(c1)
        self._c0_val.setText(f"{int(self._c0_graph.recent_avg())}%")
        self._c1_val.setText(f"{int(self._c1_graph.recent_avg())}%")
        self._c1_val.setText(f"{int(c1)}%")

    def batch_update(self) -> None:
        self._c0_graph.batch_update()
        self._c1_graph.batch_update()


# ═══════════════════════════════════════════════════════════════════════════════
# GPU VIDEO CODEC ENGINE TILE  — single sparkline: Video Codec utilization
# ═══════════════════════════════════════════════════════════════════════════════

class GPUCodecTile(BaseTile):
    """
    Landscape Video Codec Engine tile: single sparkline for codec engine utilization.
    Layout mirrors other GPU engine tiles.  palette[3] = codec colour.
    """
    def __init__(self, tile_id: str, gpu_name: str,
                 palette: Tuple[str, str, str, str], parent=None) -> None:
        self._gpu_name  = gpu_name
        self._palette   = palette
        self._color_hex = palette[3]
        super().__init__(tile_id, palette[3], parent)

    def _build_content(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(dp(6), dp(5), dp(6), dp(5))
        outer.setSpacing(dp(3))

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        icon_lbl = QLabel("🎬")
        icon_lbl.setStyleSheet(f"font-size: {font_size(13)};")
        name_lbl = QLabel(f"{self._gpu_name} · Video Codec")
        name_lbl.setStyleSheet(
            f"color: {self._palette[3]}; font-size: {font_size(13)}; font-weight: bold;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(3))
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── Codec row ─────────────────────────────────────────────────────────
        codec_row = QHBoxLayout()
        codec_row.setSpacing(dp(4))
        codec_lbl = QLabel("Codec")
        codec_lbl.setStyleSheet(
            f"color: {self._palette[3]}; font-size: {font_size(12)}; font-weight: bold;")
        codec_lbl.setFixedWidth(dp(48))
        _codec_h = int(24 * _DP_SCALE) if _DP_SCALE > 0 else 24
        self._codec_graph = SparklineWidget(self._palette[3], min_height=_codec_h)
        self._codec_val   = QLabel("0%")
        self._codec_val.setStyleSheet(f"color: {self._palette[3]}; font-size: {font_size(12)};")
        self._codec_val.setFixedWidth(dp(34))
        self._codec_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        codec_row.addWidget(codec_lbl)
        codec_row.addWidget(self._codec_graph)
        codec_row.addWidget(self._codec_val)
        outer.addLayout(codec_row)

    def update_codec(self, codec: float) -> None:
        self._codec_graph.add_value(codec)
        self._codec_val.setText(f"{int(self._codec_graph.recent_avg())}%")
        self._codec_val.setText(f"{int(codec)}%")

    def batch_update(self) -> None:
        self._codec_graph.batch_update()


# ═══════════════════════════════════════════════════════════════════════════════
# GPU 3D / COMPUTE TILE  — two sparklines: 3D engine + Compute/CUDA engine
# ═══════════════════════════════════════════════════════════════════════════════

class GPU3DComputeTile(BaseTile):
    """
    Landscape 3D+Compute tile: two stacked sparklines for the 3D rasterisation
    engine and the Compute/CUDA engine separately.
    Layout mirrors GPUCopyTile / DriveTile.  palette[0] = 3D colour.
    """
    def __init__(self, tile_id: str, gpu_name: str,
                 palette: Tuple[str, str, str, str], parent=None) -> None:
        self._gpu_name  = gpu_name
        self._palette   = palette
        self._color_hex = palette[0]
        super().__init__(tile_id, palette[0], parent)

    def _build_content(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(dp(6), dp(5), dp(6), dp(5))
        outer.setSpacing(dp(3))

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        icon_lbl = QLabel("🎮")
        icon_lbl.setStyleSheet(f"font-size: {font_size(13)};")
        name_lbl = QLabel(f"{self._gpu_name} · 3D / Compute")
        name_lbl.setStyleSheet(
            f"color: {self._palette[0]}; font-size: {font_size(13)}; font-weight: bold;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(3))
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── 3D row ────────────────────────────────────────────────────────────
        d3_row = QHBoxLayout()
        d3_row.setSpacing(dp(4))
        d3_lbl = QLabel("3D ")
        d3_lbl.setStyleSheet(
            f"color: {self._palette[0]}; font-size: {font_size(12)}; font-weight: bold;")
        d3_lbl.setFixedWidth(dp(28))
        _d3_h = int(24 * _DP_SCALE) if _DP_SCALE > 0 else 24
        self._d3_graph = SparklineWidget(self._palette[0], min_height=_d3_h)
        self._d3_val   = QLabel("0%")
        self._d3_val.setStyleSheet(f"color: {self._palette[0]}; font-size: {font_size(12)};")
        self._d3_val.setFixedWidth(dp(34))
        self._d3_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        d3_row.addWidget(d3_lbl)
        d3_row.addWidget(self._d3_graph)
        d3_row.addWidget(self._d3_val)
        outer.addLayout(d3_row)

        # ── Compute row ───────────────────────────────────────────────────────
        cm_row = QHBoxLayout()
        cm_row.setSpacing(dp(4))
        cm_lbl = QLabel("Cmp")
        cm_lbl.setStyleSheet(
            f"color: {self._palette[1]}; font-size: {font_size(12)}; font-weight: bold;")
        cm_lbl.setFixedWidth(dp(28))
        _cm_h = int(24 * _DP_SCALE) if _DP_SCALE > 0 else 24
        self._cm_graph = SparklineWidget(self._palette[1], min_height=_cm_h)
        self._cm_val   = QLabel("0%")
        self._cm_val.setStyleSheet(f"color: {self._palette[1]}; font-size: {font_size(12)};")
        self._cm_val.setFixedWidth(dp(34))
        self._cm_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        cm_row.addWidget(cm_lbl)
        cm_row.addWidget(self._cm_graph)
        cm_row.addWidget(self._cm_val)
        outer.addLayout(cm_row)

    def update_3d_compute(self, d3: float, compute: float) -> None:
        self._d3_graph.add_value(d3)
        self._cm_graph.add_value(compute)
        self._d3_val.setText(f"{int(self._d3_graph.recent_avg())}%")
        self._cm_val.setText(f"{int(self._cm_graph.recent_avg())}%")

    def batch_update(self) -> None:
        self._d3_graph.batch_update()
        self._cm_graph.batch_update()


# ═══════════════════════════════════════════════════════════════════════════════
# ROW DROP ZONE  — accepts drops at end of each row in edit mode
# ═══════════════════════════════════════════════════════════════════════════════

class RowDropZone(QWidget):
    """
    Thin droppable area appended to the end of each tile row in edit mode.
    Drag a tile onto it to append that tile to the end of this row.
    """
    drop_received = pyqtSignal(str, int)   # (tile_id, row_index)

    def __init__(self, row_idx: int, parent=None) -> None:
        super().__init__(parent)
        self._row_idx = row_idx
        self._hover   = False
        self.setAcceptDrops(True)
        self.setFixedWidth(dp(28))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(dp(40))

    def dragEnterEvent(self, event) -> None:                                # type: ignore
        if event.mimeData().hasText():
            event.acceptProposedAction()
            self._hover = True
            self.update()

    def dragMoveEvent(self, event) -> None:                                 # type: ignore
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:                                # type: ignore
        self._hover = False
        self.update()

    def dropEvent(self, event) -> None:                                     # type: ignore
        tid = event.mimeData().text()
        self.drop_received.emit(tid, self._row_idx)
        event.acceptProposedAction()
        self._hover = False
        self.update()

    def paintEvent(self, event) -> None:                                    # type: ignore
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover:
            p.setPen(QPen(QColor("#ffdd55"), 2))
            p.setBrush(QBrush(QColor(40, 40, 10, 80)))
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 4, 4)
            p.setPen(QColor("#ffdd55"))
        else:
            p.setPen(QPen(QColor("#2a3a2a"), 1, Qt.PenStyle.DashLine))      # type: ignore
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 4, 4)
            p.setPen(QColor("#2a4a2a"))
        p.setPen(QColor("#444444") if self._hover else QColor("#2a4a2a"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "+")                # type: ignore


class InterRowDropZone(QWidget):
    """
    Thin horizontal droppable bar shown between rows (and at the bottom)
    in edit mode.  Dropping a tile here inserts it as the FIRST tile of
    a brand-new row at that position.
    Emits: new_row_requested(tile_id, after_row_idx)
      after_row_idx == -1  →  prepend a new first row
      after_row_idx ==  N  →  insert a new row after existing row N
    """
    new_row_requested = pyqtSignal(str, int)

    def __init__(self, after_row_idx: int, parent=None) -> None:
        super().__init__(parent)
        self._after  = after_row_idx
        self._hover  = False
        self.setAcceptDrops(True)
        self.setFixedHeight(dp(16))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def dragEnterEvent(self, event) -> None:                                # type: ignore
        if event.mimeData().hasText():
            event.acceptProposedAction()
            self._hover = True
            self.update()

    def dragMoveEvent(self, event) -> None:                                 # type: ignore
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:                                # type: ignore
        self._hover = False
        self.update()

    def dropEvent(self, event) -> None:                                     # type: ignore
        self.new_row_requested.emit(event.mimeData().text(), self._after)
        event.acceptProposedAction()
        self._hover = False
        self.update()

    def paintEvent(self, event) -> None:                                    # type: ignore
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() // 2
        if self._hover:
            p.setPen(QPen(QColor("#ffdd55"), 2))
            p.drawLine(0, y, self.width(), y)
            # Small centre label
            p.setPen(QColor("#ffdd55"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "── new row ──")  # type: ignore
        else:
            p.setPen(QPen(QColor("#2a3a2a"), 1, Qt.PenStyle.DashLine))          # type: ignore
            p.drawLine(4, y, self.width() - 4, y)


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSIVE CORE GRID  — CPU topology grid that auto-reflows on window resize
# ═══════════════════════════════════════════════════════════════════════════════

class ResponsiveCoreGrid(QWidget):
    """
    A QGridLayout wrapper that auto-computes column count from its own width.

    columns  — list of "column groups": each group is a list of widgets that
               belong in the same grid column (e.g. [physical, ht_sibling]).
               Groups wrap to new rows when the window is too narrow, but each
               group always stays together vertically (pairs are never split).

    min_col_w — minimum width in px per group-column before wrapping occurs.

    The grid uses QSizePolicy.Expanding/Expanding so it fills all available
    space — both horizontally and vertically — allowing the parent layout to
    distribute height proportionally.
    """
    def __init__(self, columns: List[List[QWidget]],
                 min_col_w: int = 120, max_cols: int = 0, parent=None) -> None:
        super().__init__(parent)
        self._columns    = columns
        self._min_col_w  = dp(min_col_w)  # scale the logical min width
        self._max_cols   = max_cols   # 0 = no cap
        self._last_cols  = 0
        self._last_rows  = 0
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        for group in columns:
            for w in group:
                w.setParent(self)

        self._grid = QGridLayout(self)
        self._grid.setSpacing(dp(6))
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._do_layout(max(1, len(columns)))   # sensible initial layout

    # ── Layout ─────────────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:                                   # type: ignore
        super().resizeEvent(event)
        new_cols = max(1, min(self.width() // self._min_col_w, len(self._columns)))
        if self._max_cols:
            new_cols = min(new_cols, self._max_cols)
        if new_cols != self._last_cols:
            self._do_layout(new_cols)

    def _do_layout(self, grid_cols: int) -> None:
        self._last_cols = grid_cols

        # Detach every widget from the grid
        for group in self._columns:
            for w in group:
                self._grid.removeWidget(w)

        # Clear all stretch factors from old layout dimensions
        for r in range(max(1, self._grid.rowCount())):
            self._grid.setRowStretch(r, 0)
        for c in range(max(1, self._grid.columnCount())):
            self._grid.setColumnStretch(c, 0)

        # rows_per_group: how many widgets are stacked per column group
        rows_per_group = max((len(g) for g in self._columns), default=1)

        for gi, group in enumerate(self._columns):
            grid_col      = gi % grid_cols
            grid_row_base = (gi // grid_cols) * rows_per_group
            for ri, w in enumerate(group):
                self._grid.addWidget(w, grid_row_base + ri, grid_col)

        total_rows = math.ceil(len(self._columns) / grid_cols) * rows_per_group
        for r in range(total_rows):
            self._grid.setRowStretch(r, 1)
        for c in range(grid_cols):
            self._grid.setColumnStretch(c, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# TILE GRID  — manages draggable, hideable, reorderable tile layout
# ═══════════════════════════════════════════════════════════════════════════════

class TileGrid(QWidget):
    """
    Hosts all global-metric tiles in a free-form row layout.

    Layout model
    ────────────
    _tile_order is a flat list that may contain real tile IDs and the special
    sentinel '__row__' which forces a new row.  Each row is rendered as an
    independent QHBoxLayout — so rows can have different numbers of tiles and
    each row fills the full width equally regardless of tile count.

    In edit mode every tile shows:
      • ↵  (top-left)  — toggle a row break before this tile  (green = active)
      • ×  (top-right) — hide this tile
    A small '+' drop zone appears at the end of each row; dropping a tile on it
    appends that tile to the end of that row.

    Auto-scaling
    ────────────
    The row height is automatically computed from the available vertical space.
    When many rows are visible the height shrinks (down to _min_row_h); when
    few rows are visible it grows (up to _max_row_h).  This keeps the global
    section compact on small screens and spacious on large ones.
    """

    layout_changed = pyqtSignal()   # emitted after every _relayout()

    def __init__(self, tiles: Dict[str, BaseTile],
                 tile_names: Dict[str, str],
                 default_order: List[str],
                 cols: int = 5,          # kept for API compat, ignored
                 parent=None) -> None:
        super().__init__(parent)
        self._tiles      = tiles
        self._tile_names = tile_names
        self._edit_mode  = False
        # Scale row heights by DPI — 75/180 are logical px for 100% DPI
        self._min_row_h  = int(75 * _DP_SCALE) if _DP_SCALE > 0 else 75
        self._max_row_h  = int(180 * _DP_SCALE) if _DP_SCALE > 0 else 180
        # Hard compression floor — well below the tiles' own readable minimum
        # (~105 px from the sparkline), so it never clips; it only lets the
        # layout compress rows when the user shrinks the window.
        self._ROW_FLOOR  = int(48 * _DP_SCALE) if _DP_SCALE > 0 else 48
        self._row_widgets: List[QWidget] = []
        self._current_row_h = self._min_row_h  # dynamically adjusted

        for t in self._tiles.values():
            t.setParent(self)
            t.move_requested.connect(self._on_move)
            t.remove_requested.connect(self._on_hide)
            t.rowbreak_requested.connect(self._on_rowbreak)

        cfg = self._load_config()
        if cfg:
            saved_order  = [tid for tid in cfg.get('tile_order', [])
                            if tid == '__row__' or tid in self._tiles]
            saved_hidden = [tid for tid in cfg.get('hidden_tiles', []) if tid in self._tiles]
            known = set(t for t in saved_order if t != '__row__') | set(saved_hidden)
            for tid in default_order:
                if tid not in known:
                    saved_order.append(tid)
            self._tile_order = saved_order
            self._hidden     = saved_hidden
            self._min_row_h  = int(cfg.get('min_row_h', self._min_row_h))
        else:
            self._tile_order = list(default_order)
            self._hidden     = [tid for tid in self._tiles if tid not in default_order]

        self._vbox = QVBoxLayout(self)
        self._vbox.setSpacing(dp(6))
        self._vbox.setContentsMargins(0, 0, 0, 0)
        self._relayout()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _parse_rows(self) -> List[List[str]]:
        """Split _tile_order into rows of tile IDs (sentinels consumed)."""
        rows: List[List[str]] = [[]]
        for tid in self._tile_order:
            if tid == '__row__':
                if rows[-1]:          # only break if current row has content
                    rows.append([])
            else:
                rows[-1].append(tid)
        return [r for r in rows if r]

    def _relayout(self) -> None:
        # 1. Reparent all tiles to self so row-widget deletion doesn't kill them
        for tile in self._tiles.values():
            tile.setParent(self)
            tile.hide()

        # 2. Remove and destroy old row wrapper widgets
        for rw in self._row_widgets:
            self._vbox.removeWidget(rw)
            rw.deleteLater()
        self._row_widgets.clear()

        # 3. Build rows — interleave InterRowDropZones in edit mode
        rows = self._parse_rows()

        if self._edit_mode:
            # Drop zone BEFORE first row (after_row_idx = -1)
            dz0 = InterRowDropZone(-1)
            dz0.new_row_requested.connect(self._on_new_row)
            self._vbox.addWidget(dz0)
            self._row_widgets.append(dz0)

        for row_idx, row_tiles in enumerate(rows):
            rw = QWidget(self)
            rw.setStyleSheet("background: transparent;")
            rw.setMinimumHeight(self._current_row_h)
            hbox = QHBoxLayout(rw)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(dp(6))

            for tid in row_tiles:
                tile = self._tiles[tid]
                tile.setParent(rw)
                tile.set_edit_mode(self._edit_mode)
                hbox.addWidget(tile, stretch=1)
                tile.show()

            if self._edit_mode:
                dz = RowDropZone(row_idx, rw)
                dz.drop_received.connect(self._on_drop_to_row)
                hbox.addWidget(dz, stretch=0)

            self._vbox.addWidget(rw, stretch=1)
            self._row_widgets.append(rw)

            if self._edit_mode:
                # Drop zone AFTER this row
                sep = InterRowDropZone(row_idx)
                sep.new_row_requested.connect(self._on_new_row)
                self._vbox.addWidget(sep)
                self._row_widgets.append(sep)

        self._update_rowbreak_buttons()
        self.layout_changed.emit()

    # ── Resize / auto-scale ──────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:                                    # type: ignore
        super().resizeEvent(event)
        self._auto_adjust_row_height()

    def _release_row_pins(self) -> None:
        """Drop the fixed min/max height on every row so the layout may compress
        the tiles toward their readable minimum when the window is shrunk.
        Counterpart to the 'ample' branch of _auto_adjust_row_height."""
        for rw in self._row_widgets:
            rw.setMinimumHeight(self._ROW_FLOOR)
            rw.setMaximumHeight(16_777_215)

    def _auto_adjust_row_height(self) -> None:
        """Compute an optimal row height from the widget's current height.

        Uses a target row height that favours compact layouts for various aspect ratios.
        The actual height is computed as min(target, available / n_rows) so rows
        shrink when there are many of them but grow when space allows.
        All constants are DPI-scaled.
        """
        rows = self._parse_rows()
        n_rows = len(rows)
        if n_rows == 0:
            return
        # Available height: subtract vbox spacing and edit-mode drop zones
        available = self.height()
        drop_zones = 0
        if self._edit_mode:
            # 1 before first row + 2 per existing row (after + sep)
            drop_zones = 1 + 2 * n_rows
        available -= drop_zones * int(4 * _DP_SCALE)          # drop-zone height
        available -= (n_rows + drop_zones) * dp(6)  # vbox spacing
        available = max(0, available)
        # Target: aim for ~110 px per row (compact for 16:9), DPI-scaled, but allow up to _max_row_h
        target_h = min(self._max_row_h, max(int(110 * _DP_SCALE), self._min_row_h))
        per_row = available // n_rows
        if per_row >= self._min_row_h:
            # Ample space → fix every row at the target height.
            ideal = min(target_h, per_row)
            self._current_row_h = ideal
            for rw in self._row_widgets:
                rw.setMinimumHeight(ideal)
                rw.setMaximumHeight(ideal)
        else:
            # Tight space (user shrank the window) → UNPIN the rows so Qt's
            # layout engine compresses the tiles down toward their readable
            # minimum (the sparkline height).  We only cap growth, never force
            # a height, so tiles shrink gracefully and the scrollbar appears
            # solely as a last-resort fallback when even the minimums can't fit.
            ideal = max(self._ROW_FLOOR, per_row)
            self._current_row_h = ideal
            for rw in self._row_widgets:
                rw.setMinimumHeight(self._ROW_FLOOR)
                rw.setMaximumHeight(16_777_215)   # no cap → layout compresses freely

    # ── Edit mode ─────────────────────────────────────────────────────────────

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._relayout()   # rebuild to show/hide drop zones
        self._auto_adjust_row_height()

    def set_min_row_h(self, h: int) -> None:
        """Adjust minimum row height — tiles shrink/grow vertically."""
        # Clamp to DPI-scaled bounds
        self._min_row_h = max(int(50 * _DP_SCALE), min(h, int(400 * _DP_SCALE)))
        self._current_row_h = max(self._min_row_h, self._current_row_h)
        for rw in self._row_widgets:
            rw.setMinimumHeight(self._current_row_h)
            rw.setMaximumHeight(self._current_row_h)
        self._save_config()

    @property
    def cols(self) -> int:
        """Not meaningful in free layout — returns longest row length."""
        rows = self._parse_rows()
        return max((len(r) for r in rows), default=1)

    # ── Tile management ───────────────────────────────────────────────────────

    def _on_move(self, src: str, target: str, insert_before: bool) -> None:
        """Insert src immediately before or after target in _tile_order."""
        if src not in self._tile_order or target not in self._tile_order or src == target:
            return
        self._tile_order.remove(src)
        idx = self._tile_order.index(target)
        if not insert_before:
            idx += 1
        self._tile_order.insert(idx, src)
        self._cleanup_rowbreaks()
        self._relayout()
        self._save_config()

    def _on_drop_to_row(self, tile_id: str, row_idx: int) -> None:
        """Append tile_id to the end of row_idx."""
        rows = self._parse_rows()
        if tile_id not in self._tile_order or row_idx >= len(rows):
            return
        row_tiles = rows[row_idx]
        if not row_tiles:
            return
        last_tid = row_tiles[-1]
        if tile_id == last_tid:
            return
        self._on_move(tile_id, last_tid, insert_before=False)

    def _on_new_row(self, tile_id: str, after_row_idx: int) -> None:
        """
        Move tile_id to start a brand-new row.
        after_row_idx == -1  → new row becomes the first row
        after_row_idx ==  N  → new row inserted after existing row N
        """
        if tile_id not in self._tile_order:
            return
        rows = self._parse_rows()

        # Remove tile from current position (and clean up orphaned sentinels)
        self._tile_order.remove(tile_id)
        self._cleanup_rowbreaks()

        if after_row_idx == -1:
            # Prepend: tile goes before everything else
            self._tile_order.insert(0, tile_id)
            # Add a row-break after it only if there are other tiles
            if len(self._tile_order) > 1 and self._tile_order[1] != '__row__':
                self._tile_order.insert(1, '__row__')
        else:
            # Find the last tile of the target row and insert after it
            if after_row_idx < len(rows):
                anchor = rows[after_row_idx][-1]
                idx = self._tile_order.index(anchor)
                # Insert: __row__ + tile_id after anchor
                self._tile_order.insert(idx + 1, '__row__')
                self._tile_order.insert(idx + 2, tile_id)
                # If next item is also a tile (not __row__), add another break
                next_idx = idx + 3
                if (next_idx < len(self._tile_order) and
                        self._tile_order[next_idx] != '__row__'):
                    self._tile_order.insert(next_idx, '__row__')
            else:
                # after_row_idx beyond existing rows → append new last row
                self._tile_order.append('__row__')
                self._tile_order.append(tile_id)

        self._cleanup_rowbreaks()
        self._relayout()
        self._save_config()

    def _on_rowbreak(self, tile_id: str) -> None:
        """Toggle a __row__ sentinel immediately before tile_id."""
        if tile_id not in self._tile_order:
            return
        idx = self._tile_order.index(tile_id)
        if idx > 0 and self._tile_order[idx - 1] == '__row__':
            self._tile_order.pop(idx - 1)    # remove existing break
        elif idx > 0:                         # don't break before the very first tile
            self._tile_order.insert(idx, '__row__')
        self._relayout()
        self._save_config()

    def _on_hide(self, tile_id: str) -> None:
        if tile_id in self._tile_order:
            self._tile_order.remove(tile_id)
            if tile_id not in self._hidden:
                self._hidden.append(tile_id)
            self._cleanup_rowbreaks()
            self._relayout()
            self._save_config()

    def show_tile(self, tile_id: str) -> None:
        if tile_id in self._hidden:
            self._hidden.remove(tile_id)
        if tile_id not in self._tile_order:
            self._tile_order.append(tile_id)
        self._relayout()
        self._save_config()

    def hidden_tiles(self) -> List[Tuple[str, str]]:
        return [(tid, self._tile_names.get(tid, tid))
                for tid in self._hidden if tid != '__row__']

    # ── Row-break helpers ─────────────────────────────────────────────────────

    def _cleanup_rowbreaks(self) -> None:
        """Remove leading, trailing, and consecutive __row__ sentinels."""
        result: List[str] = []
        last_was_break = True   # treat start as break → no leading break
        for item in self._tile_order:
            if item == '__row__':
                if not last_was_break:
                    result.append(item)
                last_was_break = True
            else:
                result.append(item)
                last_was_break = False
        while result and result[-1] == '__row__':
            result.pop()
        self._tile_order = result

    def _update_rowbreak_buttons(self) -> None:
        for i, tid in enumerate(self._tile_order):
            if tid == '__row__':
                continue
            tile = self._tiles.get(tid)
            if tile:
                has_break = (i > 0 and self._tile_order[i - 1] == '__row__')
                tile.set_rowbreak_active(has_break)

    # ── Config ────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_config() -> dict:
        return _load_config_file()

    def _save_config(self) -> None:
        try:
            data = _load_config_file()
            data.update({
                'min_row_h':    self._min_row_h,
                'tile_order':   self._tile_order,
                'hidden_tiles': self._hidden,
            })
            _save_config_file(data)
        except Exception as exc:
            logger.warning("Config save failed: %s", exc)

    def reset_layout(self, default_order: List[str]) -> None:
        """Restore factory layout — removes all row breaks."""
        self._tile_order = [tid for tid in default_order if tid in self._tiles]
        self._hidden     = [tid for tid in self._tiles if tid not in self._tile_order]
        self._min_row_h  = 130
        self._relayout()
        self._save_config()


# ═══════════════════════════════════════════════════════════════════════════════
# ADD TILES DIALOG
# ═══════════════════════════════════════════════════════════════════════════════

class AddTilesDialog(QDialog):
    """
    Modal dialog listing all hidden tiles as checkboxes.
    Returns selected IDs on accept().
    """
    def __init__(self, hidden: List[Tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Tiles")
        self.setMinimumWidth(300)
        self.setStyleSheet("""
            QDialog   { background: #0e0e18; color: white; }
            QCheckBox { color: #ccc; padding: 4px 0; font-size: 12px; }
            QCheckBox::indicator          { width: 14px; height: 14px; }
            QCheckBox::indicator:checked  { background: #00ff88; border-radius: 2px; }
            QCheckBox::indicator:unchecked{ background: #222; border: 1px solid #444;
                                            border-radius: 2px; }
            QDialogButtonBox QPushButton  { background: #1e1e2e; color: #ccc;
                                            border: 1px solid #333; border-radius: 4px;
                                            padding: 4px 16px; }
            QDialogButtonBox QPushButton:hover { background: #2e2e3e; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        self._checks: Dict[str, QCheckBox] = {}

        if not hidden:
            layout.addWidget(QLabel("All tiles are already visible."))
        else:
            lbl = QLabel("Select tiles to restore:")
            lbl.setStyleSheet("color: #888; font-size: 11px; font-weight: bold;")
            layout.addWidget(lbl)
            layout.addSpacing(4)
            for tid, name in hidden:
                cb = QCheckBox(name)
                layout.addWidget(cb)
                self._checks[tid] = cb

        layout.addSpacing(8)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)  # type: ignore
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_ids(self) -> List[str]:
        return [tid for tid, cb in self._checks.items() if cb.isChecked()]


# ═══════════════════════════════════════════════════════════════════════════════
# COLLAPSIBLE SECTION
# ═══════════════════════════════════════════════════════════════════════════════

class CollapsibleSection(QWidget):
    """
    Collapsible header + content widget.
    Header is a clickable row with an arrow indicator and an HTML label.
    Used for the CPU topology section.
    """
    collapsed_changed = pyqtSignal()   # emitted on every expand/collapse
    def __init__(self, header_html: str, content: QWidget, parent=None) -> None:
        super().__init__(parent)
        self._collapsed = False
        self._content   = content

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Clickable header row
        self._hdr_w = QWidget()
        self._hdr_w.setCursor(Qt.CursorShape.PointingHandCursor)    # type: ignore
        self._hdr_w.setStyleSheet(
            "QWidget { background: transparent; border-radius: 4px; padding: 2px; }"
        )
        hdr_row = QHBoxLayout(self._hdr_w)
        hdr_row.setContentsMargins(4, 2, 4, 2)
        hdr_row.setSpacing(6)

        self._arrow = QLabel("▼")
        self._arrow.setStyleSheet("color: #888; font-size: 12px; background: transparent;")
        title_lbl = QLabel()
        title_lbl.setTextFormat(Qt.TextFormat.RichText)             # type: ignore
        title_lbl.setStyleSheet("background: transparent;")
        title_lbl.setText(header_html)

        hdr_row.addWidget(self._arrow)
        hdr_row.addWidget(title_lbl)
        hdr_row.addStretch()

        layout.addWidget(self._hdr_w)
        layout.addWidget(content)

        self._hdr_w.mousePressEvent = lambda _e: self._toggle()     # type: ignore

    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        self._arrow.setText("▶" if self._collapsed else "▼")
        # Limit/release our own height so the parent layout reclaims the freed space.
        # Without this, the stretch-factor keeps allocating space even when content is hidden.
        if self._collapsed:
            self.setMaximumHeight(self._hdr_w.sizeHint().height() + 8)
        else:
            self.setMaximumHeight(16_777_215)   # Qt QWIDGETSIZE_MAX — no limit
        self.collapsed_changed.emit()

    def resizeEvent(self, event) -> None:                                    # type: ignore
        super().resizeEvent(event)
        if not self._collapsed and self._content.isVisible():
            # Ensure content gets the full available height
            self._content.update()


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def section_label(html: str) -> QLabel:
    lbl = QLabel(html)
    lbl.setStyleSheet("background: transparent; padding: 2px 0;")
    return lbl


def _toolbar_btn(text: str, checkable: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCheckable(checkable)
    btn.setStyleSheet(f"""
        QPushButton {{
            background: #1e1e2e; color: #aaa;
            border: 1px solid #333; border-radius: {int(5 * _DP_SCALE)}px;
            padding: {int(4 * _DP_SCALE)}px {int(12 * _DP_SCALE)}px; font-size: {font_size(12)};
        }}
        QPushButton:hover   {{ background: #2a2a3a; color: #fff; }}
        QPushButton:checked {{ background: #2a2a1a; color: #ffdd55;
                              border-color: #ffdd55; }}
    """)
    return btn


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DASHBOARD  v1.4
# ═══════════════════════════════════════════════════════════════════════════════

class TricorderDashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        # Dark title-bar on Windows
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass

        self.setWindowTitle("System Tricorder v1.4")
        # Scale minimum size by DPI — 1280×720 is the logical 100% DPI size
        _min_w = int(1280 * _DP_SCALE) if _DP_SCALE > 0 else 1280
        _min_h = int(720 * _DP_SCALE) if _DP_SCALE > 0 else 720
        self.setMinimumSize(_min_w, _min_h)
        self.setStyleSheet("QMainWindow, QWidget { background-color: #0a0a0f; color: white; }")

        self._analyze_hardware()

        self._tiles:      Dict[str, BaseTile]   = {}
        self._tile_names: Dict[str, str]         = {}
        self.thread_widgets: Dict[int, MasterMetricBox] = {}
        self._default_tile_order: List[str] = []

        # Auto-fit state (see _fit_window_to_content / _apply_fill_mode).
        self._fitting: bool = False
        self._fit_mode: str = 'auto'      # 'auto' (window tracks content) | 'fill' (user-controlled)
        self._auto_h: Optional[int] = None
        self._last_size: Optional[Tuple[int, int]] = None

        self._setup_ui()

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()

        self.hw_thread = HardwareMonitorThread(drive_info=self._drive_info)
        self.hw_thread.metrics_updated.connect(self._update_ui)
        self.hw_thread.start()

    # ── Hardware analysis ──────────────────────────────────────────────────────

    def _analyze_hardware(self) -> None:
        self.c_physical = psutil.cpu_count(logical=False) or 4
        self.c_logical  = psutil.cpu_count(logical=True)  or 4
        self.is_amd     = "AMD" in platform.processor()

        self.is_hybrid  = False
        self.has_ht     = False
        self.p_cores    = 0
        self.e_cores    = 0
        self.p_threads  = self.c_logical
        self.e_threads  = 0

        topo = _get_cpu_topology()
        if topo and not self.is_amd:
            self.is_hybrid = topo['is_hybrid']
            self.p_cores   = topo['p_cores']
            self.e_cores   = topo['e_cores']
            self.p_threads = topo['p_threads']
            self.e_threads = topo['e_threads']

        if not self.is_hybrid:
            self.has_ht = (self.c_logical == 2 * self.c_physical)

        self.num_sockets = 1
        if WMI_AVAILABLE:
            try:
                pythoncom.CoInitialize()                            # type: ignore
                wmi = win32com.client.GetObject("winmgmts:root\\cimv2")  # type: ignore
                self.num_sockets = max(1, len(list(
                    wmi.ExecQuery("SELECT Name FROM Win32_Processor"))))
            except Exception:
                pass

        self.ram_type = "RAM"
        if WMI_AVAILABLE:
            try:
                wmi = win32com.client.GetObject("winmgmts:root\\cimv2")  # type: ignore
                for m in wmi.ExecQuery(
                    "SELECT SMBIOSMemoryType, Speed FROM Win32_PhysicalMemory"
                ):
                    smt = int(m.SMBIOSMemoryType or 0)
                    spd = int(m.Speed or 0)
                    if smt in (34, 35):
                        self.ram_type = "DDR5"
                    elif smt == 26:
                        self.ram_type = "DDR4"
                    elif spd >= 4800:
                        self.ram_type = "DDR5"
                    elif spd > 0:
                        self.ram_type = "DDR4"
                    break
            except Exception:
                pass

        wmi_gpus  = get_wmi_gpu_list()
        reg_vrams = get_registry_gpu_vrams()
        dgpu_wmi  = [(n, v, p) for n, ig, v, p in wmi_gpus if not ig]

        # iGPU/NPU tiles are only meaningful when the hardware actually exists.
        # On a desktop AMD CPU (e.g. Ryzen 5800X3D) there is neither, so they
        # must not be created — otherwise they show up as empty 0% tiles and as
        # restorable options in the "Add Tile" dialog.
        self.has_igpu = any(ig for _, ig, _, _ in wmi_gpus)
        self.has_npu  = detect_npu_present()

        self.detected_gpus: List[Tuple[str, float, str]] = []
        for i, (name, wv, pnp_id) in enumerate(dgpu_wmi):
            vram = reg_vrams[i] if i < len(reg_vrams) else (math.ceil(wv) if wv >= 1.0 else 8.0)
            # Extract device ID from PNPDeviceID
            dev_id = ""
            dev_match = re.search(r'DEV_([0-9A-Fa-f]{4})', pnp_id)
            if dev_match:
                dev_id = "0x" + dev_match.group(1).upper()
            self.detected_gpus.append((name, float(vram), dev_id))
        if not self.detected_gpus:
            self.detected_gpus = [("GPU", reg_vrams[0], "")]

        self._drive_info: List[Tuple[str, str]] = build_drive_info()
        if not self._drive_info:
            self._drive_info = [("all", "All Drives")]

    # ── Clock ──────────────────────────────────────────────────────────────────

    def _update_clock(self) -> None:
        self._clock_lbl.setText(datetime.now().strftime("%H:%M:%S     %d.%m.%Y"))

    # ── UI setup ───────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root_w  = QWidget()
        self.setCentralWidget(root_w)
        root    = QVBoxLayout(root_w)
        root.setContentsMargins(dp(15), dp(12), dp(15), dp(12))
        root.setSpacing(dp(0))

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()

        title = QLabel(
            "📊  System Tricorder  "
            f"<span style='font-size: {font_size(18)}; color:#00aa55;'>v1.4</span>"
        )
        title.setStyleSheet(
            f"font-size: {font_size(28)}; font-weight: bold; color: #00ff88; background: transparent;")
        hdr.addWidget(title)
        hdr.addSpacing(dp(16))

        sock_txt  = f"  ·  {self.num_sockets}× Socket" if self.num_sockets > 1 else ""
        cpu_hint  = f"{self.c_physical}C / {self.c_logical}T{sock_txt}"
        if self.is_hybrid:
            cpu_hint += f"  ·  {self.p_cores}P + {self.e_cores}E"
        elif self.has_ht:
            cpu_hint += "  ·  HT"
        info = QLabel(cpu_hint)
        info.setStyleSheet(
            f"font-size: {font_size(11)}; color: #444; background: transparent; padding-top: {dp(12)}px;")
        hdr.addWidget(info)

        hdr.addStretch()

        # ── Edit-mode toolbar ─────────────────────────────────────────────────
        self._btn_edit  = _toolbar_btn("✏  Edit Layout", checkable=True)
        self._btn_add   = _toolbar_btn("＋  Add Tile")
        self._btn_minus = _toolbar_btn("‹")
        self._btn_plus  = _toolbar_btn("›")
        self._cols_lbl  = QLabel("5 Spalten")
        self._cols_lbl.setStyleSheet(f"color: #555; font-size: {font_size(11)};")
        self._btn_reset = _toolbar_btn("↺  Reset")

        self._btn_add.hide()
        self._btn_minus.hide()
        self._btn_plus.hide()
        self._cols_lbl.hide()
        self._btn_reset.hide()

        self._btn_edit.toggled.connect(self._on_edit_toggled)
        self._btn_add.clicked.connect(self._on_add_tiles)
        self._btn_minus.clicked.connect(lambda: self._change_cols(-1))
        self._btn_plus.clicked.connect(lambda: self._change_cols(+1))
        self._btn_reset.clicked.connect(self._on_reset_layout)

        for w in (self._btn_edit, self._btn_add,
                  self._btn_minus, self._cols_lbl, self._btn_plus,
                  self._btn_reset):
            hdr.addWidget(w)
        hdr.addSpacing(dp(20))

        self._clock_lbl = QLabel()
        self._clock_lbl.setStyleSheet(
            f"font-size: {font_size(36)}; font-weight: bold; color: #888; "
            "font-family: Consolas; background: transparent;")
        hdr.addWidget(self._clock_lbl)
        root.addLayout(hdr)
        root.addSpacing(dp(10))

        # ── Scrollable content ────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # The window auto-fits its content (see _fit_window_to_content), so a
        # vertical scrollbar is never needed in practice; keep it AsNeeded only
        # as a safety net for the rare content-exceeds-screen edge case.
        self._scroll = scroll
        self._content_w = None   # set below
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            f"QScrollBar:vertical {{ background: #111; width: {dp(8)}px; border: none; }}"
            f"QScrollBar::handle:vertical {{ background: #333; border-radius: {int(4 * _DP_SCALE)}px; }}"
        )
        content_w = QWidget()
        content_w.setStyleSheet("background: transparent;")
        self._content_w = content_w
        content_layout = QVBoxLayout(content_w)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        scroll.setWidget(content_w)
        root.addWidget(scroll, 1)

        # ── Global metric grid (customisable, collapsible) ────────────────────
        tiles, names, default_order = self._build_tile_registry()
        self._tile_grid = TileGrid(tiles, names, default_order, cols=5)

        global_section = CollapsibleSection(
            "<b style='color:#00ff88; font-size:14px;'>▸ Global System &amp; Graphics</b>",
            self._tile_grid,
        )
        # Global section gets moderate priority (stretch 2) — fills space but
        # leaves room for CPU topology on 16:9 displays
        content_layout.addWidget(global_section, 2)
        content_layout.addSpacing(8)

        # ── CPU topology section (collapsible) ────────────────────────────────
        cpu_content_w = QWidget()
        cpu_content_w.setStyleSheet("background: transparent;")
        cpu_inner = QVBoxLayout(cpu_content_w)
        cpu_inner.setContentsMargins(0, 0, 0, 0)
        cpu_inner.setSpacing(0)

        if self.is_hybrid:
            self._build_hybrid_cores(cpu_inner)
        elif self.has_ht:
            self._build_ht_cores(cpu_inner)
        else:
            self._build_simple_cores(cpu_inner)

        cpu_section = CollapsibleSection(
            f"<b style='color:#00d4ff; font-size: {font_size(14)};'>CPU Thread Topology</b>",
            cpu_content_w,
        )
        # CPU section gets stretch 1 — fills remaining space but shrinks first
        # when vertical space is tight on narrow aspect ratios
        content_layout.addWidget(cpu_section, 1)

        # Sections collapsing/expanding changes the content's natural height —
        # re-fit the window so no scrollbar appears.
        global_section.collapsed_changed.connect(self._fit_window_to_content)
        cpu_section.collapsed_changed.connect(self._fit_window_to_content)
        # Tile grid structural changes (add/remove/move/cols/reset) all funnel
        # through TileGrid._relayout, which emits layout_changed.
        self._tile_grid.layout_changed.connect(
            lambda: QTimer.singleShot(0, self._fit_window_to_content))

        self._default_tile_order = default_order

    # ── Tile registry ──────────────────────────────────────────────────────────

    def _build_tile_registry(self) -> Tuple[Dict[str, BaseTile], Dict[str, str], List[str]]:
        """Returns (tiles_dict, names_dict, default_order_list)."""
        tiles:         Dict[str, BaseTile] = {}
        names:         Dict[str, str]       = {}
        default_order: List[str]            = []

        def reg(tile_id: str, tile: BaseTile, display_name: str, in_default: bool = True) -> None:
            tiles[tile_id]       = tile
            names[tile_id]       = display_name
            self._tiles[tile_id] = tile
            if in_default:
                default_order.append(tile_id)

        def row() -> None:
            """Insert a row-break sentinel into the default layout."""
            if default_order and default_order[-1] != '__row__':
                default_order.append('__row__')

        # ── Row 1: CPU + RAM ──────────────────────────────────────────────────
        reg("cpu_total", MetricTile("cpu_total", "CPU Gesamt",          "#00d4ff"), "CPU Gesamt")
        reg("ram",       MetricTile("ram",       f"{self.ram_type} RAM","#ff007f"), f"{self.ram_type} RAM")

        # ── Row 2: GPU engines ────────────────────────────────────────────────
        row()
        for gi, (gname, _, _) in enumerate(self.detected_gpus):
            pal = GPU_PALETTES[gi % len(GPU_PALETTES)]
            sn  = short_gpu_name(gname)
            reg(f"gpu_{gi}_3d",
                GPU3DComputeTile(f"gpu_{gi}_3d", sn, pal),
                f"{sn} · 3D / Compute")
            reg(f"gpu_{gi}_copy",
                GPUCopyTile(f"gpu_{gi}_copy", sn, pal),
                f"{sn} · Copy")
            reg(f"gpu_{gi}_codec",
                GPUCodecTile(f"gpu_{gi}_codec", sn, pal),
                f"{sn} · Video Codec")
            reg(f"gpu_{gi}_vram",
                MetricTile(f"gpu_{gi}_vram", f"{sn} · VRAM", pal[3]),
                f"{sn} · VRAM")
            if gi < len(self.detected_gpus) - 1:
                row()   # each GPU on its own row if multiple GPUs

        # ── Row 3: iGPU + NPU (only if the hardware exists) ───────────────────
        if self.has_igpu or self.has_npu:
            row()
        if self.has_igpu:
            reg("igpu", MetricTile("igpu", "iGPU", "#0055ff"), "iGPU")
        if self.has_npu:
            reg("npu",  MetricTile("npu",  "NPU",  "#aa00ff"), "NPU")

        # ── Row 4+: Drives (all drives on one row) ────────────────────────────
        row()
        for key, label in self._drive_info:
            tid = f"drive_{key}"
            reg(tid, DriveTile(tid, label), f"Drive {label}")

        return tiles, names, default_order

    # ── Edit-mode toolbar logic ────────────────────────────────────────────────

    def _on_edit_toggled(self, active: bool) -> None:
        self._tile_grid.set_edit_mode(active)
        self._btn_add.setVisible(active)
        self._btn_minus.setVisible(active)
        self._btn_plus.setVisible(active)
        self._cols_lbl.setVisible(active)
        self._btn_reset.setVisible(active)
        self._update_cols_label()
        self._btn_edit.setText("✔  Fertig" if active else "✏  Edit Layout")

    def _on_add_tiles(self) -> None:
        hidden = self._tile_grid.hidden_tiles()
        dlg    = AddTilesDialog(hidden, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            for tid in dlg.selected_ids():
                self._tile_grid.show_tile(tid)

    def _change_cols(self, delta: int) -> None:
        # ‹ = rows taller (delta=-1 → +30px),  › = rows shorter (delta=+1 → -30px)
        self._tile_grid.set_min_row_h(self._tile_grid._min_row_h - delta * 30)
        self._update_cols_label()

    def _update_cols_label(self) -> None:
        self._cols_lbl.setText(f"Zeilenhöhe {self._tile_grid._min_row_h}px")

    def _on_reset_layout(self) -> None:
        self._tile_grid.reset_layout(self._default_tile_order)
        self._update_cols_label()

    # ── CPU core topology builders ─────────────────────────────────────────────

    def _build_hybrid_cores(self, parent: QVBoxLayout) -> None:
        P_COLOR  = "#00d4ff"
        HT_COLOR = "#0077aa"
        E_COLOR  = "#ff007f"
        p_has_ht = (self.p_threads == self.p_cores * 2)
        rows_p   = 2 if p_has_ht else 1

        parent.addWidget(section_label(
            f"<b style='color:{P_COLOR}; font-size:14px;'>⚡ Performance Cores "
            f"({self.p_cores} Cores / {self.p_threads} Threads, "
            f"Threads 0–{self.p_threads - 1})</b>"
        ))
        parent.addSpacing(4)

        p_groups: List[List[QWidget]] = []
        for ci in range(self.p_cores):
            t0 = ci * rows_p
            w0 = MasterMetricBox(f"P-Core {ci}", P_COLOR, variant='standard')
            self.thread_widgets[t0] = w0
            group: List[QWidget] = [w0]
            if p_has_ht:
                t1 = t0 + 1
                w1 = MasterMetricBox(f"P-Core {ci}", HT_COLOR, variant='ht')
                self.thread_widgets[t1] = w1
                group.append(w1)
            p_groups.append(group)
        parent.addWidget(ResponsiveCoreGrid(p_groups, min_col_w=100), 1)
        parent.addSpacing(8)

        parent.addWidget(section_label(
            f"<b style='color:{E_COLOR}; font-size:12px;'>🔋 Efficiency Cores "
            f"({self.e_cores} Cores / {self.e_threads} Threads, "
            f"Threads {self.p_threads}–{self.p_threads + self.e_threads - 1})</b>"
        ))
        parent.addSpacing(4)

        e_groups: List[List[QWidget]] = []
        for i in range(self.e_threads):
            t = self.p_threads + i
            w = MasterMetricBox(f"E-Core {i}", E_COLOR, variant='efficiency')
            self.thread_widgets[t] = w
            e_groups.append([w])
        parent.addWidget(ResponsiveCoreGrid(e_groups, min_col_w=80,
                                            max_cols=math.ceil(self.e_cores / 2)), 1)

    def _build_ht_cores(self, parent: QVBoxLayout) -> None:
        PHYS_COLOR = "#ff6600" if self.is_amd else "#00d4ff"
        SMT_COLOR  = "#aa3300" if self.is_amd else "#0077aa"
        brand_lbl  = "AMD Ryzen" if self.is_amd else "Intel Core"
        smt_label  = "SMT"      if self.is_amd else "HT"
        variant    = 'smt'      if self.is_amd else 'ht'
        n_phys     = self.c_physical

        parent.addWidget(section_label(
            f"<b style='color:{PHYS_COLOR}; font-size:12px;'>{brand_lbl} Threads "
            f"— {smt_label} Pairs (0–{self.c_logical - 1})</b>"
        ))
        parent.addSpacing(2)
        hint = QLabel(
            f"<span style='color:#333; font-size:9px;'>"
            f"Row 1 = Physical Cores &nbsp;|&nbsp; Row 2 = {smt_label} Siblings</span>"
        )
        hint.setStyleSheet("background: transparent;")
        parent.addWidget(hint)
        parent.addSpacing(2)

        col_groups: List[List[QWidget]] = []
        for ci in range(n_phys):
            t_phys = ci * 2
            t_smt  = ci * 2 + 1
            w_phys = MasterMetricBox(f"Core {ci}", PHYS_COLOR, variant='standard')
            w_smt  = MasterMetricBox(f"Core {ci}", SMT_COLOR,  variant=variant)
            self.thread_widgets[t_phys] = w_phys
            self.thread_widgets[t_smt]  = w_smt
            col_groups.append([w_phys, w_smt])
        parent.addWidget(ResponsiveCoreGrid(col_groups, min_col_w=100), 1)

    def _build_simple_cores(self, parent: QVBoxLayout) -> None:
        color = "#ff6600" if self.is_amd else "#00d4ff"
        brand = "AMD Ryzen" if self.is_amd else "Intel Core"
        label = "CCX Threads" if self.is_amd else "Threads"

        parent.addWidget(section_label(
            f"<b style='color:{color}; font-size:14px;'>{brand} {label} "
            f"(0–{self.c_logical - 1})</b>"
        ))
        parent.addSpacing(4)

        col_groups: List[List[QWidget]] = []
        for i in range(self.c_logical):
            w = MasterMetricBox(f"Thread {i}", color)
            self.thread_widgets[i] = w
            col_groups.append([w])
        parent.addWidget(ResponsiveCoreGrid(col_groups, min_col_w=120), 1)

    # ── UI update  (30 FPS) ────────────────────────────────────────────────────

    def _update_ui(self, m: SystemMetrics) -> None:
        _t = self._tiles.get

        # Optimized: direct attribute access instead of isinstance() checks
        # Tiles are registered with known types - no runtime type checking needed

        w = _t("cpu_total")
        if w:
            w.update_val(m.cpu_total_percent)
        w = _t("ram")
        if w:
            w.update_val(m.ram_percent, f"{m.ram_used_gb:.1f}/{m.ram_total_gb:.1f} GB")
        w = _t("igpu")
        if w:
            w.update_val(m.igpu_percent)
        w = _t("npu")
        if w:
            w.update_val(m.npu_percent)

        for i, gm in enumerate(m.gpus):
            w_3d = _t(f"gpu_{i}_3d")
            if w_3d:
                w_3d.update_3d_compute(gm.gpu_3d_percent, gm.gpu_compute_percent)
            w_copy = _t(f"gpu_{i}_copy")
            if w_copy:
                w_copy.update_copy(gm.gpu_copy0_percent, gm.gpu_copy1_percent)
            w_codec = _t(f"gpu_{i}_codec")
            if w_codec:
                w_codec.update_codec(gm.gpu_codec_percent)
            vp = (gm.gpu_vram_used_gb / gm.gpu_vram_total_gb * 100) if gm.gpu_vram_total_gb else 0
            w_vram = _t(f"gpu_{i}_vram")
            if w_vram:
                w_vram.update_val(vp,
                    f"{gm.gpu_vram_used_gb:.1f}/{gm.gpu_vram_total_gb:.0f} GB")

        for dm in m.drives:
            w = _t(f"drive_{dm.key}")
            if w:
                w.update_drive(dm.read_mbps, dm.write_mbps)

        for ti, val in m.cpu_cores.items():
            if ti in self.thread_widgets:
                self.thread_widgets[ti].update_val(val)

        # Batch update all sparklines once per frame instead of per-widget.
        # Global tiles AND per-thread CPU widgets both need this — the latter
        # live in self.thread_widgets and are not part of self._tiles.
        for tile in self._tiles.values():
            tile.batch_update()
        for tw in self.thread_widgets.values():
            tw.batch_update()

    # ── Window/content auto-fit ────────────────────────────────────────────────
    #
    # Two modes, auto-switched by how the resize originates:
    #   • 'auto'  — the APP is sizing the window (first show, or a structural
    #               change like adding/removing a tile or collapsing a section).
    #               The window grows/shrinks to exactly fit the content.
    #   • 'fill'  — the USER dragged the window edge.  We stop fighting them:
    #               instead of growing the window back we cap the scroll content
    #               to the viewport so the layout compresses the tiles down to
    #               their readable minimum.  Only when even the minimums can't
    #               fit does the scrollbar appear as a last-resort fallback.
    _MAX_WIDGET = 16_777_215   # Qt QWIDGETSIZE_MAX

    def _fit_window_to_content(self) -> None:
        """AUTO mode: grow the window so it exactly fits all content — no scrollbar.

        Used on first show and after structural changes (add/remove/move tile,
        column change, section collapse/expand).  Uncaps the content height,
        then iteratively resizes the window so the viewport matches the content's
        natural height.  The sparkline tiles keep their target size; only the
        window adapts.  Skipped for maximised / fullscreen windows.
        """
        if getattr(self, '_fitting', False):
            return
        if self._content_w is None or self._scroll is None:
            return
        st = self.windowState()
        if st & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):   # type: ignore[operator]
            return
        self._fitting = True
        self._fit_mode = 'auto'
        try:
            self._content_w.setMaximumHeight(self._MAX_WIDGET)          # uncap → tiles use target size
            for _ in range(12):                                   # bounded — converges fast
                content = self._content_w
                need = max(content.minimumSizeHint().height(), content.sizeHint().height())
                have = self._scroll.viewport().height()
                delta = need - have
                if abs(delta) < 3:
                    break
                target = max(self.height() + delta, self.minimumHeight())
                try:
                    avail = self.screen().availableGeometry().height()       # type: ignore[union-attr]
                    frame = self.frameGeometry().height() - self.height()
                    target = min(target, avail - frame)
                except Exception:
                    pass
                if abs(target - self.height()) < 3:
                    break
                self.resize(self.width(), target)
                QApplication.processEvents()      # let row-height / core-grid reflow settle
            self._auto_h = self.height()         # remember what we set → detect user drag
        finally:
            self._fitting = False

    def _apply_fill_mode(self) -> None:
        """FILL mode: the user resized the window — let tiles compress, no grow.

        We do NOT cap the content or fight the user.  We only release the tile
        rows' fixed-height pins so Qt's scroll-area widget resizing
        (``widgetResizable``) can compress the expanding tiles down to their own
        readable minimum (the sparkline height) Б─■ never below it, so nothing
        clips.  The window stays at the user-chosen size; the vertical scrollbar
        appears solely as a last-resort fallback when the tiles' minimums no
        longer fit ("too many tiles to keep reasonably visible").
        """
        if getattr(self, '_fitting', False):
            return
        if self._content_w is None or self._scroll is None:
            return
        st = self.windowState()
        if st & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):   # type: ignore[operator]
            return
        self._fitting = True
        self._fit_mode = 'fill'
        try:
            # Release row pins so the content's minimum height drops and the
            # scroll area can shrink the widget to the viewport (compressing
            # tiles to their readable minimum instead of scrolling).
            self._tile_grid._release_row_pins()
            for _ in range(6):
                QApplication.processEvents()
                if not self._scroll.verticalScrollBar().isVisible():
                    break
            self._auto_h = None                                 # user owns the height now
        finally:
            self._fitting = False

    def resizeEvent(self, event) -> None:                                    # type: ignore
        super().resizeEvent(event)
        if getattr(self, '_fitting', False):
            return
        st = self.windowState()
        if st & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):   # type: ignore[operator]
            return
        new_w, new_h = event.size().width(), event.size().height()
        old = getattr(self, '_last_size', None)
        self._last_size = (new_w, new_h)
        if old is None:
            return
        dh = new_h - old[1]
        dw = new_w - old[0]
        if dh != 0:
            # Vertical drag → user is taking control: tiles adapt (no grow-back).
            QTimer.singleShot(0, self._apply_fill_mode)
        elif dw != 0:
            # Width-only change → CPU grid reflowed and content height changed.
            # In auto mode re-grow to the new natural height; in fill mode just
            # re-cap so the recompressed tiles match the new width.
            QTimer.singleShot(0, self._fit_window_to_content
                              if self._fit_mode == 'auto' else self._apply_fill_mode)

    def showEvent(self, event) -> None:                                    # type: ignore
        super().showEvent(event)
        # Defer one event-loop tick so the layout has settled before measuring.
        self._fit_mode = 'auto'
        QTimer.singleShot(0, self._fit_window_to_content)

    def _restore_window_placement(self) -> bool:
        """Restore the last Windows window position/size/mode from config."""
        try:
            cfg = _load_config_file()
            win_cfg = cfg.get('window', {})
            geometry_b64 = win_cfg.get('geometry')
            if not isinstance(geometry_b64, str) or not geometry_b64:
                return False

            geometry = QByteArray.fromBase64(geometry_b64.encode('ascii'))
            if geometry.isEmpty() or not self.restoreGeometry(geometry):
                return False

            placement = win_cfg.get('placement', 'normal')
            if placement == 'fullscreen':
                self.showFullScreen()
            elif placement == 'maximized':
                self.showMaximized()
            else:
                self.show()
            return True
        except Exception as exc:
            logger.warning("Window placement restore failed: %s", exc)
            return False

    def _save_window_placement(self) -> None:
        """Persist the current Windows window position/size/mode into config."""
        try:
            state = self.windowState()
            if state & Qt.WindowState.WindowFullScreen:              # type: ignore[operator]
                placement = 'fullscreen'
            elif state & Qt.WindowState.WindowMaximized:             # type: ignore[operator]
                placement = 'maximized'
            else:
                placement = 'normal'

            data = _load_config_file()
            data['window'] = {
                'geometry': bytes(self.saveGeometry().toBase64()).decode('ascii'),
                'placement': placement,
            }
            _save_config_file(data)
        except Exception as exc:
            logger.warning("Window placement save failed: %s", exc)

    def closeEvent(self, event) -> None:                                    # type: ignore
        self._save_window_placement()
        self.hw_thread.stop()
        event.accept()                                              # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # ── Crash logging for --noconsole PyInstaller builds ──────────────────────
    # With --noconsole, sys.stderr is redirected to NUL and unhandled exceptions
    # disappear silently.  This hook routes them to ~/.tricorder.log instead.
    import traceback as _tb

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        logger.critical(
            "Unhandled exception:\n%s",
            "".join(_tb.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName("System Tricorder")
    app.setStyle("Fusion")

    # Compute DPI scale factor AFTER creating QApplication (Qt-native)
    _init_dp_scale(app)
    logging.getLogger("tricorder").debug("DPI scale factor: %.2f", _DP_SCALE)

    win = TricorderDashboard()
    if not win._restore_window_placement():
        # No saved geometry: open windowed and auto-fit to content (showEvent
        # schedules _fit_window_to_content) — guarantees no scrollbar on first
        # launch.  The user can still maximise manually if preferred.
        win.show()
    sys.exit(app.exec())
