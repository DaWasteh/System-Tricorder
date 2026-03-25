#!/usr/bin/env python3
"""
System Tricorder v0.3 — Hardware Monitoring Dashboard
Dark Mode | 20 FPS | Multi-GPU | P/E Cores | Customisable Layout | Per-Drive Tiles
"""

import sys
import time
import math
import json
import re
import platform
import psutil
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from PyQt5.QtWidgets import (                                       # type: ignore
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QGridLayout, QSizePolicy, QPushButton,
    QScrollArea, QDialog, QCheckBox, QDialogButtonBox,
)
from PyQt5.QtCore  import Qt, QTimer, pyqtSignal, QThread, QMimeData, QPoint  # type: ignore
from PyQt5.QtGui   import (                                         # type: ignore
    QColor, QPainter, QPainterPath, QPen, QBrush, QDrag, QPixmap,
)

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

# ── Layout config ──────────────────────────────────────────────────────────────
CONFIG_FILE = Path.home() / ".tricorder_layout.json"

# ── GPU colour palettes (up to 4 discrete GPUs) ───────────────────────────────
GPU_PALETTES = [
    ("#ff5500", "#ff7700", "#ff9900", "#ffaa00"),   # GPU 0 — Amber
    ("#00cc66", "#00aa55", "#009944", "#00ff88"),   # GPU 1 — Emerald
    ("#aa00ff", "#8800cc", "#cc44ff", "#dd88ff"),   # GPU 2 — Violet
    ("#0088ff", "#0066cc", "#0055aa", "#44aaff"),   # GPU 3 — Sapphire
]

_VIRTUAL_NAMES = ('microsoft basic', 'remote desktop', 'parsec', 'virtual',
                  'citrix', 'vmware', 'indirect')

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


def get_wmi_gpu_list() -> List[Tuple[str, bool, float]]:
    """
    Returns (name, is_igpu, vram_gb) for all real GPUs via WMI.
    Sorted: dGPUs first (desc VRAM), then iGPUs.
    """
    result: List[Tuple[str, bool, float]] = []
    if not WMI_AVAILABLE:
        return result
    try:
        pythoncom.CoInitialize()                                    # type: ignore
        wmi = win32com.client.GetObject("winmgmts:root\\cimv2")    # type: ignore
        for c in wmi.ExecQuery("SELECT Name, AdapterRAM FROM Win32_VideoController"):
            name = str(c.Name or '').strip()
            if not name or any(v in name.lower() for v in _VIRTUAL_NAMES):
                continue
            nl = name.lower()
            is_igpu = (
                ('intel' in nl and 'arc' not in nl and 'xe' not in nl) or
                ('amd' in nl and ('radeon(tm) graphics' in nl or 'vega' in nl) and 'rx ' not in nl)
            )
            vram = float(c.AdapterRAM or 0) / (1024 ** 3)
            result.append((name, is_igpu, vram))
    except Exception:
        pass
    result.sort(key=lambda x: (int(x[1]), -x[2]))
    return result


def short_gpu_name(name: str) -> str:
    """Shortens a GPU name to ~18 chars for compact display."""
    for kw in ('RTX', 'RX ', 'GTX', 'RX', 'Arc', 'Radeon', 'NVIDIA', 'AMD'):
        idx = name.find(kw)
        if idx != -1:
            return name[idx:idx + 18].strip()
    return name[:18].strip()


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


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GPUMetrics:
    name: str
    luid: str
    gpu_3d_percent:   float = 0.0
    gpu_copy0_percent: float = 0.0
    gpu_copy1_percent: float = 0.0
    gpu_vram_used_gb:  float = 0.0
    gpu_vram_total_gb: float = 8.0


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
# HARDWARE MONITOR THREAD  (20 FPS)
# ═══════════════════════════════════════════════════════════════════════════════

class HardwareMonitorThread(QThread):
    metrics_updated = pyqtSignal(SystemMetrics)

    def __init__(self, drive_info: List[Tuple[str, str]], parent=None):
        super().__init__(parent)
        self._running    = False
        self._drive_info = drive_info   # [(key, label), ...]

        # GPU static info
        reg_vrams = get_registry_gpu_vrams()
        wmi_gpus  = get_wmi_gpu_list()
        dgpu_wmi  = [(n, v) for n, ig, v in wmi_gpus if not ig]

        self._dgpu_info: List[Tuple[str, float]] = []
        for i, (name, wv) in enumerate(dgpu_wmi):
            vram = reg_vrams[i] if i < len(reg_vrams) else (math.ceil(wv) if wv >= 1.0 else 8.0)
            self._dgpu_info.append((name, float(vram)))
        if not self._dgpu_info:
            self._dgpu_info = [("GPU", reg_vrams[0])]

        self._luid_order: List[str]       = []
        self._luid_vram:  Dict[str, float] = {}

    def run(self):
        self._running = True
        if WMI_AVAILABLE:
            pythoncom.CoInitialize()                                # type: ignore
        try:
            wmi = win32com.client.GetObject("winmgmts:root\\cimv2") if WMI_AVAILABLE else None  # type: ignore
        except Exception:
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
                    try:
                        for a in wmi.ExecQuery(
                            "SELECT Name, DedicatedUsage "
                            "FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory"
                        ):
                            luid = str(a.Name).split('_phys')[0]
                            used = float(a.DedicatedUsage or 0) / (1024 ** 3)
                            ld = luid_data.setdefault(luid, {'3d': 0.0, 'c0': 0.0, 'c1': 0.0, 'used': 0.0})
                            ld['used'] = max(ld['used'], used)
                    except Exception:
                        pass

                    try:
                        for e in wmi.ExecQuery(
                            "SELECT Name, UtilizationPercentage "
                            "FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine"
                        ):
                            en   = str(e.Name).lower()
                            util = float(e.UtilizationPercentage or 0)
                            if util <= 0:
                                continue
                            if any(x in en for x in ('hd graphics', 'uhd graphics', 'iris', 'intel(r) graphics')):
                                igpu_p = max(igpu_p, util)
                                continue
                            if any(x in en for x in ('ai boost', 'npu', 'xe media')):
                                npu_p = max(npu_p, util)
                                continue
                            for luid in luid_data:
                                if luid.lower() in en:
                                    if any(x in en for x in ('3d', 'compute', 'cuda', 'graphics_1')):
                                        luid_data[luid]['3d'] = min(luid_data[luid]['3d'] + util, 100.0)
                                    elif 'copy' in en:
                                        tail = en.split('copy')[-1]
                                        if tail.strip().startswith(('_0', ' 0', '0')):
                                            luid_data[luid]['c0'] = max(luid_data[luid]['c0'], util)
                                        else:
                                            luid_data[luid]['c1'] = max(luid_data[luid]['c1'], util)
                                    break
                    except Exception:
                        pass

                new = sorted([l for l in luid_data if l not in self._luid_order],
                             key=lambda l: -luid_data[l]['used'])
                self._luid_order.extend(new)

                gpus: List[GPUMetrics] = []
                for i, luid in enumerate(self._luid_order):
                    d = luid_data.get(luid, {})
                    if luid not in self._luid_vram:
                        self._luid_vram[luid] = self._dgpu_info[min(i, len(self._dgpu_info) - 1)][1]
                    used = d.get('used', 0.0)
                    if used > self._luid_vram[luid]:
                        self._luid_vram[luid] = math.ceil(used)
                    name = self._dgpu_info[min(i, len(self._dgpu_info) - 1)][0]
                    gpus.append(GPUMetrics(
                        name=name, luid=luid,
                        gpu_3d_percent=d.get('3d', 0.0),
                        gpu_copy0_percent=d.get('c0', 0.0),
                        gpu_copy1_percent=d.get('c1', 0.0),
                        gpu_vram_used_gb=used,
                        gpu_vram_total_gb=self._luid_vram[luid],
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
            except Exception:
                pass
            time.sleep(0.05)

    def stop(self):
        self._running = False
        self.wait()


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
    def __init__(self, color_hex: str, history_len: int = 60,
                 min_height: int = 50, parent=None):
        super().__init__(parent)
        self.color   = QColor(color_hex)
        self.history: deque = deque([0.0] * history_len, maxlen=history_len)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(min_height)

    def add_value(self, value: float):
        self.history.append(value)
        self.update()

    def paintEvent(self, _):                                        # type: ignore
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setPen(QPen(QColor(40, 40, 52), 1))
        for x in range(0, w, 25):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, 15):
            painter.drawLine(0, y, w, y)

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
        painter.setPen(Qt.NoPen)                                    # type: ignore
        painter.drawPath(fill)


# ── CPU-section tile (non-draggable, variant-styled, unchanged from v0.2) ─────

class MasterMetricBox(QFrame):
    """Used exclusively for the CPU core/thread grid.  Not draggable."""
    def __init__(self, title: str, color_hex: str, variant: str = 'standard', parent=None):
        super().__init__(parent)
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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(2)

        header = QHBoxLayout()
        self.id_lbl  = QLabel(f"{title}{title_extra}")
        self.id_lbl.setStyleSheet(f"color: {color_hex}; font-size: 11px; font-weight: bold;")
        self.val_lbl = QLabel("0%")
        self.val_lbl.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self.id_lbl)
        header.addStretch()
        header.addWidget(self.val_lbl)
        layout.addLayout(header)

        self.graph = SparklineWidget(color_hex)
        layout.addWidget(self.graph)

    def update_val(self, val: float, text: Optional[str] = None):
        self.graph.add_value(val)
        self.val_lbl.setText(text if text else f"{int(val)}%")


# ═══════════════════════════════════════════════════════════════════════════════
# DRAGGABLE TILE BASE
# ═══════════════════════════════════════════════════════════════════════════════

class BaseTile(QFrame):
    """
    Base class for all tiles in the customisable global grid.
    Provides drag-to-reorder and edit-mode × button.
    Subclass and implement _build_content().
    """
    swap_requested   = pyqtSignal(str, str)   # (source_id, target_id)
    remove_requested = pyqtSignal(str)         # tile_id

    _BTN_SIZE = 18

    def __init__(self, tile_id: str, color_hex: str, parent=None):
        super().__init__(parent)
        self.tile_id    = tile_id
        self._color_hex = color_hex
        self._edit_mode = False
        self._drop_hl   = False
        self._drag_pos: Optional[QPoint] = None

        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._apply_frame_style(color_hex, edit=False)

        # Build tile-specific content (implemented by subclass)
        self._build_content()

        # Remove (×) overlay button — hidden until edit mode
        self._btn_x = QPushButton("×", self)
        self._btn_x.setFixedSize(self._BTN_SIZE, self._BTN_SIZE)
        self._btn_x.setStyleSheet("""
            QPushButton {
                background: #880000; color: #fff;
                border-radius: 9px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #ff2222; }
        """)
        self._btn_x.hide()
        self._btn_x.clicked.connect(lambda: self.remove_requested.emit(self.tile_id))

    def _apply_frame_style(self, accent: str, edit: bool):
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

    def _build_content(self):
        """Override in subclass to populate the tile layout."""
        pass

    # ── Edit mode ──────────────────────────────────────────────────────────────
    def set_edit_mode(self, enabled: bool):
        self._edit_mode = enabled
        self._btn_x.setVisible(enabled)
        self.setCursor(Qt.SizeAllCursor if enabled else Qt.ArrowCursor)  # type: ignore
        accent = "#ffdd55" if enabled else self._color_hex
        self._apply_frame_style(accent, edit=enabled)

    def resizeEvent(self, event):                                   # type: ignore
        super().resizeEvent(event)
        self._btn_x.move(self.width() - self._BTN_SIZE - 3, 3)

    # ── Drag source ────────────────────────────────────────────────────────────
    def mousePressEvent(self, event):                               # type: ignore
        if self._edit_mode and event.button() == Qt.LeftButton:    # type: ignore
            self._drag_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):                                # type: ignore
        if not (self._edit_mode and self._drag_pos and
                event.buttons() & Qt.LeftButton):                  # type: ignore
            return
        if ((event.pos() - self._drag_pos).manhattanLength()
                < QApplication.startDragDistance()):
            return

        drag  = QDrag(self)
        mime  = QMimeData()
        mime.setText(self.tile_id)
        drag.setMimeData(mime)

        px = self.grab()
        img = px.toImage()
        for y in range(img.height()):
            for x in range(img.width()):
                col = img.pixel(x, y)
                img.setPixel(x, y, (col & 0x00FFFFFF) | 0xA0000000)
        drag.setPixmap(QPixmap.fromImage(img))
        drag.setHotSpot(self._drag_pos)
        drag.exec_(Qt.MoveAction)                                   # type: ignore
        self._drag_pos = None

    # ── Drop target ────────────────────────────────────────────────────────────
    def dragEnterEvent(self, event):                                # type: ignore
        if (self._edit_mode and event.mimeData().hasText()
                and event.mimeData().text() != self.tile_id):
            event.acceptProposedAction()
            self._drop_hl = True
            self.update()

    def dragLeaveEvent(self, event):                                # type: ignore
        self._drop_hl = False
        self.update()

    def dropEvent(self, event):                                     # type: ignore
        src = event.mimeData().text()
        if src != self.tile_id:
            self.swap_requested.emit(src, self.tile_id)
            event.acceptProposedAction()
        self._drop_hl = False
        self.update()

    def paintEvent(self, event):                                    # type: ignore
        super().paintEvent(event)
        if self._drop_hl:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor("#ffdd55"), 2, Qt.DashLine))       # type: ignore
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 5, 5)


# ═══════════════════════════════════════════════════════════════════════════════
# METRIC TILE  — single sparkline (CPU total, RAM, GPU, NPU, iGPU …)
# ═══════════════════════════════════════════════════════════════════════════════

class MetricTile(BaseTile):
    def __init__(self, tile_id: str, title: str, color_hex: str, parent=None):
        self._title     = title
        self._color_hex = color_hex
        super().__init__(tile_id, color_hex, parent)

    def _build_content(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 5, 6, 5)
        outer.setSpacing(2)

        hdr = QHBoxLayout()
        self._title_lbl = QLabel(self._title)
        self._title_lbl.setStyleSheet(
            f"color: {self._color_hex}; font-size: 11px; font-weight: bold;")
        self._val_lbl = QLabel("0%")
        self._val_lbl.setStyleSheet("color: #888; font-size: 11px;")
        hdr.addWidget(self._title_lbl)
        hdr.addStretch()
        hdr.addWidget(self._val_lbl)
        outer.addLayout(hdr)

        self._graph = SparklineWidget(self._color_hex)
        outer.addWidget(self._graph)

    def update_val(self, val: float, text: Optional[str] = None):
        self._graph.add_value(val)
        self._val_lbl.setText(text if text else f"{int(val)}%")


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
    def __init__(self, tile_id: str, label: str, parent=None):
        self._label    = label
        self._color_hex = DRIVE_R_COLOR   # primary accent
        self._peak     = 100.0            # auto-scaling peak (MB/s)
        super().__init__(tile_id, DRIVE_R_COLOR, parent)

    def _build_content(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 5, 6, 5)
        outer.setSpacing(3)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        icon_lbl = QLabel("💾")
        icon_lbl.setStyleSheet("font-size: 12px;")
        name_lbl = QLabel(self._label)
        name_lbl.setStyleSheet(
            f"color: {DRIVE_R_COLOR}; font-size: 11px; font-weight: bold;")
        self._peak_lbl = QLabel("↑100 MB/s")
        self._peak_lbl.setStyleSheet("color: #444; font-size: 9px;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(3)
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        hdr.addWidget(self._peak_lbl)
        outer.addLayout(hdr)

        # ── Read row ──────────────────────────────────────────────────────────
        r_row = QHBoxLayout()
        r_row.setSpacing(4)
        r_lbl = QLabel("R")
        r_lbl.setStyleSheet(f"color: {DRIVE_R_COLOR}; font-size: 10px; font-weight: bold;")
        r_lbl.setFixedWidth(12)
        self._r_graph = SparklineWidget(DRIVE_R_COLOR, min_height=24)
        self._r_val   = QLabel("0 MB/s")
        self._r_val.setStyleSheet(f"color: {DRIVE_R_COLOR}; font-size: 10px;")
        self._r_val.setFixedWidth(72)
        self._r_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)   # type: ignore
        r_row.addWidget(r_lbl)
        r_row.addWidget(self._r_graph)
        r_row.addWidget(self._r_val)
        outer.addLayout(r_row)

        # ── Write row ─────────────────────────────────────────────────────────
        w_row = QHBoxLayout()
        w_row.setSpacing(4)
        w_lbl = QLabel("W")
        w_lbl.setStyleSheet(f"color: {DRIVE_W_COLOR}; font-size: 10px; font-weight: bold;")
        w_lbl.setFixedWidth(12)
        self._w_graph = SparklineWidget(DRIVE_W_COLOR, min_height=24)
        self._w_val   = QLabel("0 MB/s")
        self._w_val.setStyleSheet(f"color: {DRIVE_W_COLOR}; font-size: 10px;")
        self._w_val.setFixedWidth(72)
        self._w_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)   # type: ignore
        w_row.addWidget(w_lbl)
        w_row.addWidget(self._w_graph)
        w_row.addWidget(self._w_val)
        outer.addLayout(w_row)

    def update_drive(self, read_mbps: float, write_mbps: float):
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
                 palette: Tuple[str, str, str, str], parent=None):
        self._gpu_name  = gpu_name
        self._palette   = palette
        self._color_hex = palette[1]   # primary accent = Copy0 colour
        super().__init__(tile_id, palette[1], parent)

    def _build_content(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 5, 6, 5)
        outer.setSpacing(3)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        icon_lbl = QLabel("📋")
        icon_lbl.setStyleSheet("font-size: 11px;")
        name_lbl = QLabel(f"{self._gpu_name} · Copy")
        name_lbl.setStyleSheet(
            f"color: {self._palette[1]}; font-size: 11px; font-weight: bold;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(3)
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── Copy0 row ─────────────────────────────────────────────────────────
        c0_row = QHBoxLayout()
        c0_row.setSpacing(4)
        c0_lbl = QLabel("Cp0")
        c0_lbl.setStyleSheet(
            f"color: {self._palette[1]}; font-size: 10px; font-weight: bold;")
        c0_lbl.setFixedWidth(28)
        self._c0_graph = SparklineWidget(self._palette[1], min_height=24)
        self._c0_val   = QLabel("0%")
        self._c0_val.setStyleSheet(f"color: {self._palette[1]}; font-size: 10px;")
        self._c0_val.setFixedWidth(34)
        self._c0_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)  # type: ignore
        c0_row.addWidget(c0_lbl)
        c0_row.addWidget(self._c0_graph)
        c0_row.addWidget(self._c0_val)
        outer.addLayout(c0_row)

        # ── Copy1 row ─────────────────────────────────────────────────────────
        c1_row = QHBoxLayout()
        c1_row.setSpacing(4)
        c1_lbl = QLabel("Cp1")
        c1_lbl.setStyleSheet(
            f"color: {self._palette[2]}; font-size: 10px; font-weight: bold;")
        c1_lbl.setFixedWidth(28)
        self._c1_graph = SparklineWidget(self._palette[2], min_height=24)
        self._c1_val   = QLabel("0%")
        self._c1_val.setStyleSheet(f"color: {self._palette[2]}; font-size: 10px;")
        self._c1_val.setFixedWidth(34)
        self._c1_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)  # type: ignore
        c1_row.addWidget(c1_lbl)
        c1_row.addWidget(self._c1_graph)
        c1_row.addWidget(self._c1_val)
        outer.addLayout(c1_row)

    def update_copy(self, c0: float, c1: float):
        self._c0_graph.add_value(c0)
        self._c1_graph.add_value(c1)
        self._c0_val.setText(f"{int(c0)}%")
        self._c1_val.setText(f"{int(c1)}%")


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
    """
    def __init__(self, columns: List[List[QWidget]],
                 min_col_w: int = 120, parent=None):
        super().__init__(parent)
        self._columns    = columns
        self._min_col_w  = min_col_w
        self._last_cols  = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        for group in columns:
            for w in group:
                w.setParent(self)

        self._grid = QGridLayout(self)
        self._grid.setSpacing(6)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._do_layout(max(1, len(columns)))   # sensible initial layout

    # ── Layout ─────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):                                   # type: ignore
        super().resizeEvent(event)
        new_cols = max(1, min(self.width() // self._min_col_w, len(self._columns)))
        if new_cols != self._last_cols:
            self._do_layout(new_cols)

    def _do_layout(self, grid_cols: int):
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
    Hosts all global-metric tiles in a configurable grid.

    • tile_order  — list of currently visible tile IDs in display order
    • _hidden     — list of hidden tile IDs
    • _cols       — number of grid columns (adjustable in edit mode)

    Layout is recomputed on every change.  Config is auto-saved to
    CONFIG_FILE after each user-initiated change.
    """

    def __init__(self, tiles: Dict[str, BaseTile],
                 tile_names: Dict[str, str],
                 default_order: List[str],
                 cols: int = 5,
                 parent=None):
        super().__init__(parent)
        self._tiles      = tiles
        self._tile_names = tile_names
        self._edit_mode  = False
        self._last_cols  = 0    # tracks last rendered col-count to avoid redundant relayouts

        for t in self._tiles.values():
            t.setParent(self)
            t.swap_requested.connect(self._on_swap)
            t.remove_requested.connect(self._on_hide)

        cfg = self._load_config()
        if cfg:
            saved_order  = [tid for tid in cfg.get('tile_order', []) if tid in self._tiles]
            saved_hidden = [tid for tid in cfg.get('hidden_tiles', []) if tid in self._tiles]
            known = set(saved_order) | set(saved_hidden)
            for tid in default_order:
                if tid not in known:
                    saved_order.append(tid)
            self._tile_order  = saved_order
            self._hidden      = saved_hidden
            # min_tile_w stored in config; fall back to old 'cols'-based estimate
            saved_mtw = cfg.get('min_tile_w')
            if saved_mtw:
                self._min_tile_w = int(saved_mtw)
            else:
                old_cols = cfg.get('cols', cols)
                self._min_tile_w = max(100, 1280 // max(1, old_cols))
        else:
            self._tile_order  = list(default_order)
            self._hidden      = [tid for tid in self._tiles if tid not in default_order]
            self._min_tile_w  = 220   # default: ~5–6 cols at 1280 px

        self._grid = QGridLayout(self)
        self._grid.setSpacing(6)
        self._relayout()

    # ── Layout helpers ─────────────────────────────────────────────────────────

    def _compute_cols(self) -> int:
        """Compute column count from current widget width and min tile width."""
        w = self.width()
        if w <= 0:
            w = 1280   # pre-layout fallback
        return max(1, w // self._min_tile_w)

    def resizeEvent(self, event):                                   # type: ignore
        super().resizeEvent(event)
        new_cols = self._compute_cols()
        if new_cols != self._last_cols:
            self._relayout()

    def _relayout(self):
        cols = self._compute_cols()
        self._last_cols = cols

        for tile in self._tiles.values():
            self._grid.removeWidget(tile)
            tile.hide()

        for r in range(max(self._grid.rowCount(), 1)):
            self._grid.setRowStretch(r, 0)
        for c in range(max(self._grid.columnCount(), 1)):
            self._grid.setColumnStretch(c, 0)

        for i, tid in enumerate(self._tile_order):
            tile = self._tiles[tid]
            self._grid.addWidget(tile, i // cols, i % cols)
            tile.show()

        total_rows = max(1, math.ceil(len(self._tile_order) / cols))
        for r in range(total_rows):
            self._grid.setRowStretch(r, 1)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

    # ── Edit mode ──────────────────────────────────────────────────────────────

    def set_edit_mode(self, enabled: bool):
        self._edit_mode = enabled
        for tile in self._tiles.values():
            tile.set_edit_mode(enabled)

    def set_min_tile_w(self, w: int):
        """Adjust minimum tile width (controls auto-column count on resize)."""
        self._min_tile_w = max(100, min(w, 800))
        self._relayout()
        self._save_config()

    @property
    def cols(self) -> int:
        """Current auto-computed column count."""
        return self._compute_cols()

    # ── Tile management ────────────────────────────────────────────────────────

    def _on_swap(self, id_a: str, id_b: str):
        if id_a in self._tile_order and id_b in self._tile_order:
            i, j = self._tile_order.index(id_a), self._tile_order.index(id_b)
            self._tile_order[i], self._tile_order[j] = self._tile_order[j], self._tile_order[i]
            self._relayout()
            self._save_config()

    def _on_hide(self, tile_id: str):
        if tile_id in self._tile_order:
            self._tile_order.remove(tile_id)
            if tile_id not in self._hidden:
                self._hidden.append(tile_id)
            self._tiles[tile_id].hide()
            self._relayout()
            self._save_config()

    def show_tile(self, tile_id: str):
        if tile_id in self._hidden:
            self._hidden.remove(tile_id)
        if tile_id not in self._tile_order:
            self._tile_order.append(tile_id)
        tile = self._tiles.get(tile_id)
        if tile:
            tile.set_edit_mode(self._edit_mode)
            tile.show()
        self._relayout()
        self._save_config()

    def hidden_tiles(self) -> List[Tuple[str, str]]:
        """Returns [(tile_id, display_name)] for all hidden tiles."""
        return [(tid, self._tile_names.get(tid, tid)) for tid in self._hidden]

    # ── Config ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_config() -> dict:
        try:
            return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _save_config(self):
        try:
            CONFIG_FILE.write_text(
                json.dumps({
                    'version':      '0.3',
                    'min_tile_w':   self._min_tile_w,
                    'tile_order':   self._tile_order,
                    'hidden_tiles': self._hidden,
                }, indent=2, ensure_ascii=False),
                encoding='utf-8',
            )
        except Exception:
            pass

    def reset_layout(self, default_order: List[str]):
        """Restore factory layout."""
        self._tile_order  = [tid for tid in default_order if tid in self._tiles]
        self._hidden      = [tid for tid in self._tiles if tid not in self._tile_order]
        self._min_tile_w  = 220
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
    def __init__(self, hidden: List[Tuple[str, str]], parent=None):
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
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)  # type: ignore
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
    def __init__(self, header_html: str, content: QWidget, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._content   = content

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Clickable header row
        self._hdr_w = QWidget()
        self._hdr_w.setCursor(Qt.PointingHandCursor)                # type: ignore
        self._hdr_w.setStyleSheet(
            "QWidget { background: transparent; border-radius: 4px; padding: 2px; }"
        )
        hdr_row = QHBoxLayout(self._hdr_w)
        hdr_row.setContentsMargins(4, 2, 4, 2)
        hdr_row.setSpacing(6)

        self._arrow = QLabel("▼")
        self._arrow.setStyleSheet("color: #888; font-size: 12px; background: transparent;")
        title_lbl = QLabel()
        title_lbl.setTextFormat(Qt.RichText)                        # type: ignore
        title_lbl.setStyleSheet("background: transparent;")
        title_lbl.setText(header_html)

        hdr_row.addWidget(self._arrow)
        hdr_row.addWidget(title_lbl)
        hdr_row.addStretch()

        layout.addWidget(self._hdr_w)
        layout.addWidget(content)

        self._hdr_w.mousePressEvent = lambda _e: self._toggle()     # type: ignore

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        self._arrow.setText("▶" if self._collapsed else "▼")
        # Limit/release our own height so the parent layout reclaims the freed space.
        # Without this, the stretch-factor keeps allocating space even when content is hidden.
        if self._collapsed:
            self.setMaximumHeight(self._hdr_w.sizeHint().height() + 8)
        else:
            self.setMaximumHeight(16_777_215)   # Qt QWIDGETSIZE_MAX — no limit


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
    btn.setStyleSheet("""
        QPushButton {
            background: #1e1e2e; color: #aaa;
            border: 1px solid #333; border-radius: 5px;
            padding: 4px 12px; font-size: 12px;
        }
        QPushButton:hover   { background: #2a2a3a; color: #fff; }
        QPushButton:checked { background: #2a2a1a; color: #ffdd55;
                              border-color: #ffdd55; }
    """)
    return btn


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DASHBOARD  v0.3
# ═══════════════════════════════════════════════════════════════════════════════

class TricorderDashboard(QMainWindow):
    def __init__(self):
        super().__init__()

        # Dark title-bar on Windows
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass

        self.setWindowTitle("System Tricorder v0.3")
        self.setMinimumSize(1280, 900)
        self.setStyleSheet("QMainWindow, QWidget { background-color: #0a0a0f; color: white; }")

        self._analyze_hardware()

        self._tiles:      Dict[str, BaseTile]   = {}
        self._tile_names: Dict[str, str]         = {}
        self.thread_widgets: Dict[int, MasterMetricBox] = {}

        self._setup_ui()

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()

        self.hw_thread = HardwareMonitorThread(drive_info=self._drive_info)
        self.hw_thread.metrics_updated.connect(self._update_ui)
        self.hw_thread.start()

    # ── Hardware analysis ──────────────────────────────────────────────────────

    def _analyze_hardware(self):
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
                    if smt in (34, 35):   self.ram_type = "DDR5"
                    elif smt == 26:        self.ram_type = "DDR4"
                    elif spd >= 4800:      self.ram_type = "DDR5"
                    elif spd > 0:          self.ram_type = "DDR4"
                    break
            except Exception:
                pass

        wmi_gpus  = get_wmi_gpu_list()
        reg_vrams = get_registry_gpu_vrams()
        dgpu_wmi  = [(n, v) for n, ig, v in wmi_gpus if not ig]
        self.detected_gpus: List[Tuple[str, float]] = []
        for i, (name, wv) in enumerate(dgpu_wmi):
            vram = reg_vrams[i] if i < len(reg_vrams) else (math.ceil(wv) if wv >= 1.0 else 8.0)
            self.detected_gpus.append((name, float(vram)))
        if not self.detected_gpus:
            self.detected_gpus = [("GPU", reg_vrams[0])]

        self._drive_info: List[Tuple[str, str]] = build_drive_info()
        if not self._drive_info:
            self._drive_info = [("all", "All Drives")]

    # ── Clock ──────────────────────────────────────────────────────────────────

    def _update_clock(self):
        self._clock_lbl.setText(datetime.now().strftime("%H:%M:%S     %d.%m.%Y"))

    # ── UI setup ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root_w  = QWidget()
        self.setCentralWidget(root_w)
        root    = QVBoxLayout(root_w)
        root.setContentsMargins(15, 12, 15, 12)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()

        title = QLabel(
            "📊  System Tricorder  "
            "<span style='font-size:18px; color:#00aa55;'>v0.3</span>"
        )
        title.setStyleSheet(
            "font-size: 28px; font-weight: bold; color: #00ff88; background: transparent;")
        hdr.addWidget(title)
        hdr.addSpacing(16)

        sock_txt  = f"  ·  {self.num_sockets}× Socket" if self.num_sockets > 1 else ""
        cpu_hint  = f"{self.c_physical}C / {self.c_logical}T{sock_txt}"
        if self.is_hybrid:
            cpu_hint += f"  ·  {self.p_cores}P + {self.e_cores}E"
        elif self.has_ht:
            cpu_hint += "  ·  HT"
        info = QLabel(cpu_hint)
        info.setStyleSheet(
            "font-size: 11px; color: #444; background: transparent; padding-top: 12px;")
        hdr.addWidget(info)

        hdr.addStretch()

        # ── Edit-mode toolbar ─────────────────────────────────────────────────
        self._btn_edit  = _toolbar_btn("✏  Edit Layout", checkable=True)
        self._btn_add   = _toolbar_btn("＋  Add Tile")
        self._btn_minus = _toolbar_btn("‹")
        self._btn_plus  = _toolbar_btn("›")
        self._cols_lbl  = QLabel("5 Spalten")
        self._cols_lbl.setStyleSheet("color: #555; font-size: 11px;")
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
        hdr.addSpacing(20)

        self._clock_lbl = QLabel()
        self._clock_lbl.setStyleSheet(
            "font-size: 36px; font-weight: bold; color: #888; "
            "font-family: Consolas; background: transparent;")
        hdr.addWidget(self._clock_lbl)
        root.addLayout(hdr)
        root.addSpacing(10)

        # ── Scrollable content ────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #111; width: 8px; border: none; }"
            "QScrollBar::handle:vertical { background: #333; border-radius: 4px; }"
        )
        content_w = QWidget()
        content_w.setStyleSheet("background: transparent;")
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
        content_layout.addWidget(global_section, 1)
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
            "<b style='color:#00d4ff; font-size:14px;'>CPU Thread Topology</b>",
            cpu_content_w,
        )
        content_layout.addWidget(cpu_section, 1)

        self._default_tile_order = default_order

    # ── Tile registry ──────────────────────────────────────────────────────────

    def _build_tile_registry(self):
        """Returns (tiles_dict, names_dict, default_order_list)."""
        tiles:         Dict[str, BaseTile] = {}
        names:         Dict[str, str]       = {}
        default_order: List[str]            = []

        def reg(tile_id: str, tile: BaseTile, display_name: str, in_default: bool = True):
            tiles[tile_id]       = tile
            names[tile_id]       = display_name
            self._tiles[tile_id] = tile
            if in_default:
                default_order.append(tile_id)

        # ── System-wide ───────────────────────────────────────────────────────
        reg("cpu_total", MetricTile("cpu_total", "CPU Gesamt",          "#00d4ff"), "CPU Gesamt")
        reg("ram",       MetricTile("ram",       f"{self.ram_type} RAM","#ff007f"), f"{self.ram_type} RAM")
        reg("igpu",      MetricTile("igpu",      "iGPU",                "#0055ff"), "iGPU")
        reg("npu",       MetricTile("npu",       "NPU",                 "#aa00ff"), "NPU")

        # ── GPUs ──────────────────────────────────────────────────────────────
        for gi, (gname, _) in enumerate(self.detected_gpus):
            pal = GPU_PALETTES[gi % len(GPU_PALETTES)]
            sn  = short_gpu_name(gname)
            reg(f"gpu_{gi}_3d",
                MetricTile(f"gpu_{gi}_3d", f"{sn} · 3D / Compute", pal[0]),
                f"{sn} · 3D / Compute")
            reg(f"gpu_{gi}_copy",
                GPUCopyTile(f"gpu_{gi}_copy", sn, pal),
                f"{sn} · Copy")
            reg(f"gpu_{gi}_vram",
                MetricTile(f"gpu_{gi}_vram", f"{sn} · VRAM", pal[3]),
                f"{sn} · VRAM")

        # ── Drives ────────────────────────────────────────────────────────────
        for key, label in self._drive_info:
            tid = f"drive_{key}"
            reg(tid, DriveTile(tid, label), f"Drive {label}")

        return tiles, names, default_order

    # ── Edit-mode toolbar logic ────────────────────────────────────────────────

    def _on_edit_toggled(self, active: bool):
        self._tile_grid.set_edit_mode(active)
        self._btn_add.setVisible(active)
        self._btn_minus.setVisible(active)
        self._btn_plus.setVisible(active)
        self._cols_lbl.setVisible(active)
        self._btn_reset.setVisible(active)
        self._update_cols_label()
        self._btn_edit.setText("✔  Fertig" if active else "✏  Edit Layout")

    def _on_add_tiles(self):
        hidden = self._tile_grid.hidden_tiles()
        dlg    = AddTilesDialog(hidden, parent=self)
        if dlg.exec_() == AddTilesDialog.Accepted:
            for tid in dlg.selected_ids():
                self._tile_grid.show_tile(tid)

    def _change_cols(self, delta: int):
        # delta=+1 → more columns (narrower tiles), delta=-1 → fewer columns (wider tiles)
        self._tile_grid.set_min_tile_w(self._tile_grid._min_tile_w - delta * 30)
        self._update_cols_label()

    def _update_cols_label(self):
        self._cols_lbl.setText(f"{self._tile_grid.cols} Spalten")

    def _on_reset_layout(self):
        self._tile_grid.reset_layout(self._default_tile_order)
        self._update_cols_label()

    # ── CPU core topology builders ─────────────────────────────────────────────

    def _build_hybrid_cores(self, parent: QVBoxLayout):
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
        parent.addWidget(ResponsiveCoreGrid(p_groups, min_col_w=120), 1)
        parent.addSpacing(14)

        parent.addWidget(section_label(
            f"<b style='color:{E_COLOR}; font-size:14px;'>🔋 Efficiency Cores "
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
        parent.addWidget(ResponsiveCoreGrid(e_groups, min_col_w=100), 1)

    def _build_ht_cores(self, parent: QVBoxLayout):
        PHYS_COLOR = "#ff6600" if self.is_amd else "#00d4ff"
        SMT_COLOR  = "#aa3300" if self.is_amd else "#0077aa"
        brand_lbl  = "AMD Ryzen" if self.is_amd else "Intel Core"
        smt_label  = "SMT"      if self.is_amd else "HT"
        variant    = 'smt'      if self.is_amd else 'ht'
        n_phys     = self.c_physical

        parent.addWidget(section_label(
            f"<b style='color:{PHYS_COLOR}; font-size:14px;'>{brand_lbl} Threads "
            f"— {smt_label} Pairs (0–{self.c_logical - 1})</b>"
        ))
        parent.addSpacing(2)
        hint = QLabel(
            f"<span style='color:#333; font-size:10px;'>"
            f"Row 1 = Physical Cores &nbsp;|&nbsp; Row 2 = {smt_label} Siblings</span>"
        )
        hint.setStyleSheet("background: transparent;")
        parent.addWidget(hint)
        parent.addSpacing(4)

        col_groups: List[List[QWidget]] = []
        for ci in range(n_phys):
            t_phys = ci * 2
            t_smt  = ci * 2 + 1
            w_phys = MasterMetricBox(f"Core {ci}", PHYS_COLOR, variant='standard')
            w_smt  = MasterMetricBox(f"Core {ci}", SMT_COLOR,  variant=variant)
            self.thread_widgets[t_phys] = w_phys
            self.thread_widgets[t_smt]  = w_smt
            col_groups.append([w_phys, w_smt])
        parent.addWidget(ResponsiveCoreGrid(col_groups, min_col_w=120), 1)

    def _build_simple_cores(self, parent: QVBoxLayout):
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

    # ── UI update  (20 FPS) ────────────────────────────────────────────────────

    def _update_ui(self, m: SystemMetrics):
        _t = self._tiles.get

        def upd(tid, val, text=None):
            w = _t(tid)
            if w and isinstance(w, MetricTile):
                w.update_val(val, text)

        upd("cpu_total", m.cpu_total_percent)
        upd("ram",  m.ram_percent,   f"{m.ram_used_gb:.1f}/{m.ram_total_gb:.1f} GB")
        upd("igpu", m.igpu_percent)
        upd("npu",  m.npu_percent)

        for i, gm in enumerate(m.gpus):
            upd(f"gpu_{i}_3d", gm.gpu_3d_percent)
            w_copy = _t(f"gpu_{i}_copy")
            if w_copy and isinstance(w_copy, GPUCopyTile):
                w_copy.update_copy(gm.gpu_copy0_percent, gm.gpu_copy1_percent)
            vp = (gm.gpu_vram_used_gb / gm.gpu_vram_total_gb * 100) if gm.gpu_vram_total_gb else 0
            upd(f"gpu_{i}_vram", vp,
                f"{gm.gpu_vram_used_gb:.1f}/{gm.gpu_vram_total_gb:.0f} GB")

        for dm in m.drives:
            w = _t(f"drive_{dm.key}")
            if w and isinstance(w, DriveTile):
                w.update_drive(dm.read_mbps, dm.write_mbps)

        for ti, val in m.cpu_cores.items():
            if ti in self.thread_widgets:
                self.thread_widgets[ti].update_val(val)

    def closeEvent(self, event):                                    # type: ignore
        self.hw_thread.stop()
        event.accept()                                              # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TricorderDashboard()
    win.showMaximized()
    sys.exit(app.exec_())