#!/usr/bin/env python3
"""
System Tricorder v0.2 - Hardware Monitoring Dashboard
Dark Mode | 20 FPS | Multi-GPU | P/E Core Design | HT/SMT Pair Columns
"""

import sys
import time
import math
import platform
import psutil
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from PyQt5.QtWidgets import (   # type: ignore
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QGridLayout, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread   # type: ignore
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QBrush   # type: ignore

# ─── WMI & WinReg ────────────────────────────────────────────────────────────
try:
    import pythoncom   # type: ignore
    import win32com.client   # type: ignore
    WMI_AVAILABLE = True
except ImportError:
    pythoncom = None    # type: ignore
    win32com = None # type: ignore
    WMI_AVAILABLE = False

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    winreg = None   # type: ignore
    WINREG_AVAILABLE = False

# ─── GPU Color Palettes (up to 4 discrete GPUs) ──────────────────────────────
GPU_PALETTES = [
    ("#ff5500", "#ff7700", "#ff9900", "#ffaa00"),  # GPU 0 — Orange / Amber
    ("#00cc66", "#00aa55", "#009944", "#00ff88"),  # GPU 1 — Emerald / Green
    ("#aa00ff", "#8800cc", "#cc44ff", "#dd88ff"),  # GPU 2 — Violet / Purple
    ("#0088ff", "#0066cc", "#0055aa", "#44aaff"),  # GPU 3 — Sapphire
]

# Virtual / software adapters to ignore
_VIRTUAL_NAMES = ('microsoft basic', 'remote desktop', 'parsec', 'virtual', 'citrix', 'vmware', 'indirect')


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
    Returns list of (name, is_igpu, vram_gb) for all real GPUs via WMI.
    Sorted: dGPUs first (descending VRAM), then iGPUs.
    """
    result: List[Tuple[str, bool, float]] = []
    if not WMI_AVAILABLE:
        return result
    try:
        pythoncom.CoInitialize()  # type: ignore
        wmi = win32com.client.GetObject("winmgmts:root\\cimv2")  # type: ignore
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
    # dGPUs first, then iGPUs; within each group sort by VRAM desc
    result.sort(key=lambda x: (int(x[1]), -x[2]))
    return result


def short_gpu_name(name: str) -> str:
    """Shortens a GPU name to ~18 chars for compact display."""
    for kw in ('RTX', 'RX ', 'GTX', 'RX', 'Arc', 'Radeon', 'NVIDIA', 'AMD'):
        idx = name.find(kw)
        if idx != -1:
            return name[idx:idx + 18].strip()
    return name[:18].strip()


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GPUMetrics:
    name: str
    luid: str
    gpu_3d_percent: float = 0.0
    gpu_copy0_percent: float = 0.0
    gpu_copy1_percent: float = 0.0
    gpu_vram_used_gb: float = 0.0
    gpu_vram_total_gb: float = 8.0


@dataclass
class SystemMetrics:
    cpu_total_percent: float
    cpu_cores: Dict[int, float]
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    gpus: List[GPUMetrics]
    igpu_percent: float
    npu_percent: float
    disk_read_mbps: float
    disk_write_mbps: float
    timestamp: datetime


# ═══════════════════════════════════════════════════════════════════════════════
# HARDWARE MONITOR THREAD (20 FPS)
# ═══════════════════════════════════════════════════════════════════════════════

class HardwareMonitorThread(QThread):
    metrics_updated = pyqtSignal(SystemMetrics)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

        # Pre-compute static GPU info for name/VRAM association
        reg_vrams = get_registry_gpu_vrams()
        wmi_gpus = get_wmi_gpu_list()
        dgpu_wmi = [(n, v) for n, ig, v in wmi_gpus if not ig]

        self._dgpu_info: List[Tuple[str, float]] = []
        for i, (name, wv) in enumerate(dgpu_wmi):
            vram = reg_vrams[i] if i < len(reg_vrams) else (math.ceil(wv) if wv >= 1.0 else 8.0)
            self._dgpu_info.append((name, float(vram)))
        if not self._dgpu_info:
            self._dgpu_info = [("GPU", reg_vrams[0])]

        self._luid_order: List[str] = []
        self._luid_vram: Dict[str, float] = {}

    def run(self):
        self._running = True
        if WMI_AVAILABLE:
            pythoncom.CoInitialize()  # type: ignore
        try:
            wmi = win32com.client.GetObject("winmgmts:root\\cimv2") if WMI_AVAILABLE else None  # type: ignore
        except Exception:
            wmi = None

        self.last_io = psutil.disk_io_counters()
        self.last_t = time.time()

        while self._running:
            try:
                now = time.time()
                io = psutil.disk_io_counters()
                dt = max(now - self.last_t, 0.001)
                rmb = wmb = 0.0
                if io and self.last_io:
                    rmb = (io.read_bytes  - self.last_io.read_bytes)  / (1024 * 1024) / dt
                    wmb = (io.write_bytes - self.last_io.write_bytes) / (1024 * 1024) / dt
                self.last_io, self.last_t = io, now

                cpu_total = psutil.cpu_percent(interval=None)
                cpu_cores = {i: float(v) for i, v in enumerate(psutil.cpu_percent(percpu=True))}
                ram = psutil.virtual_memory()

                igpu_p = npu_p = 0.0
                luid_data: Dict[str, dict] = {}

                if wmi:
                    # VRAM usage per adapter LUID
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

                    # Engine utilization per LUID
                    try:
                        for e in wmi.ExecQuery(
                            "SELECT Name, UtilizationPercentage "
                            "FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine"
                        ):
                            en = str(e.Name).lower()
                            util = float(e.UtilizationPercentage or 0)
                            if util <= 0:
                                continue
                            # iGPU
                            if any(x in en for x in ('hd graphics', 'uhd graphics', 'iris', 'intel(r) graphics')):
                                igpu_p = max(igpu_p, util)
                                continue
                            # NPU / AI
                            if any(x in en for x in ('ai boost', 'npu', 'xe media')):
                                npu_p = max(npu_p, util)
                                continue
                            # Match to dGPU by LUID prefix
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

                # Maintain stable LUID order (new LUIDs appended by VRAM desc)
                new = sorted(
                    [l for l in luid_data if l not in self._luid_order],
                    key=lambda l: -luid_data[l]['used']
                )
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
                    cpu_total_percent=cpu_total, cpu_cores=cpu_cores,
                    ram_total_gb=ram.total / (1024 ** 3),
                    ram_used_gb=ram.used / (1024 ** 3),
                    ram_percent=ram.percent,
                    gpus=gpus, igpu_percent=igpu_p, npu_percent=npu_p,
                    disk_read_mbps=rmb, disk_write_mbps=wmb, timestamp=datetime.now(),
                ))
            except Exception:
                pass
            time.sleep(0.05)

    def stop(self):
        self._running = False
        self.wait()


def _get_cpu_topology() -> Optional[dict]:
    """
    Reads true P/E core topology via GetLogicalProcessorInformationEx.
    Returns dict with p_cores, p_threads, e_cores, e_threads, is_hybrid — or None on failure.

    EfficiencyClass convention on Intel hybrid (Alder/Raptor/Meteor/Arrow Lake):
      P-cores = EfficiencyClass 1  (higher perf, reported as "less efficient")
      E-cores = EfficiencyClass 0  (lower perf, higher power efficiency)
    Higher EfficiencyClass value → P-Core.
    """
    try:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        RelationProcessorCore = 0

        buf_size = ctypes.c_ulong(0)
        kernel32.GetLogicalProcessorInformationEx(
            RelationProcessorCore, None, ctypes.byref(buf_size)
        )
        buf = (ctypes.c_ubyte * buf_size.value)()
        if not kernel32.GetLogicalProcessorInformationEx(
            RelationProcessorCore, buf, ctypes.byref(buf_size)
        ):
            return None

        # SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX layout per entry:
        #   +0  DWORD  Relationship
        #   +4  DWORD  Size
        #   +8  BYTE   Flags          (ProcessorRelationship.Flags)
        #   +9  BYTE   EfficiencyClass
        #  +10  BYTE[20] Reserved
        #  +30  WORD   GroupCount
        #  +32  GROUP_AFFINITY[]  (each = 8-byte KAFFINITY + 2-byte Group + 6-byte Reserved = 16 bytes)
        cores: list = []   # list of (efficiency_class: int, thread_count: int)
        offset = 0
        while offset < buf_size.value:
            rel  = int.from_bytes(buf[offset    : offset + 4], 'little')
            size = int.from_bytes(buf[offset + 4: offset + 8], 'little')
            if size == 0:
                break
            if rel == RelationProcessorCore:
                eff = buf[offset + 9]
                group_count = int.from_bytes(buf[offset + 30: offset + 32], 'little')
                threads = 0
                gm_off = offset + 32
                for _ in range(group_count):
                    mask = int.from_bytes(buf[gm_off: gm_off + 8], 'little')
                    threads += bin(mask).count('1')
                    gm_off += 16
                cores.append((eff, threads))
            offset += size

        if not cores:
            return None

        eff_classes = sorted(set(c[0] for c in cores))
        if len(eff_classes) < 2:
            total_t = sum(t for _, t in cores)
            return {
                'is_hybrid': False,
                'p_cores': len(cores), 'p_threads': total_t,
                'e_cores': 0,          'e_threads': 0,
            }

        # Intel convention: higher EfficiencyClass = P-core
        max_eff = max(eff_classes)
        min_eff = min(eff_classes)
        p_group = [(e, t) for e, t in cores if e == max_eff]
        e_group = [(e, t) for e, t in cores if e == min_eff]

        return {
            'is_hybrid':  True,
            'p_cores':    len(p_group),
            'p_threads':  sum(t for _, t in p_group),
            'e_cores':    len(e_group),
            'e_threads':  sum(t for _, t in e_group),
        }
    except Exception:
        return None




class MasterGraphWidget(QWidget):
    def __init__(self, color_hex: str, history_len: int = 60, parent=None):
        super().__init__(parent)
        self.color = QColor(color_hex)
        self.history: deque = deque([0.0] * history_len, maxlen=history_len)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(50)

    def add_value(self, value: float):
        self.history.append(value)
        self.update()

    def paintEvent(self, _):  # type: ignore
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))  # transparent bg (inherited from frame)
        painter.setPen(QPen(QColor(40, 40, 52), 1))
        for x in range(0, w, 25):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, 15):
            painter.drawLine(0, y, w, y)

        if not self.history:
            return
        path = QPainterPath()
        step_x = w / max(len(self.history) - 1, 1)
        for i, val in enumerate(self.history):
            y = h - (min(max(val, 0.0), 100.0) / 100.0 * h)
            if i == 0:
                path.moveTo(0, y)
            else:
                path.lineTo(i * step_x, y)

        painter.setPen(QPen(self.color, 2))
        painter.drawPath(path)

        fill_path = QPainterPath(path)
        fill_path.lineTo(w, h)
        fill_path.lineTo(0, h)
        fill = QColor(self.color)
        fill.setAlpha(35)
        painter.setBrush(QBrush(fill))
        painter.setPen(Qt.NoPen)  # type: ignore
        painter.drawPath(fill_path)


# ─── Box variants ─────────────────────────────────────────────────────────────
#   'standard'   – 3px top accent  (P-Cores, global stats, physical threads)
#   'efficiency' – 3px LEFT accent (E-Cores — visually distinct from P-Cores)
#   'ht'/'smt'   – 2px top accent  (HT/SMT sibling threads — dimmer, badge)

class MasterMetricBox(QFrame):
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
            title_extra = f" <span style='font-size:8px; color:{color_hex}; opacity:0.7;'>{'HT' if variant == 'ht' else 'SMT'}</span>"
        else:
            frame_css = (
                f"border: 1px solid #222;"
                f"border-top: 3px solid {color_hex};"
                f"border-radius: 6px;"
            )
            bg = "#121218"
            title_extra = ""

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg};
                {frame_css}
            }}
            QLabel {{ background: transparent; border: none; }}
        """)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(2)

        header = QHBoxLayout()
        self.id_lbl = QLabel(f"{title}{title_extra}")
        self.id_lbl.setStyleSheet(f"color: {color_hex}; font-size: 11px; font-weight: bold;")
        self.val_lbl = QLabel("0%")
        self.val_lbl.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self.id_lbl)
        header.addStretch()
        header.addWidget(self.val_lbl)
        layout.addLayout(header)

        self.graph = MasterGraphWidget(color_hex)
        layout.addWidget(self.graph)

    def update_val(self, val: float, text: Optional[str] = None):
        self.graph.add_value(val)
        self.val_lbl.setText(text if text else f"{int(val)}%")


# ─── Section label helper ─────────────────────────────────────────────────────
def section_label(html_content: str) -> QLabel:
    lbl = QLabel(html_content)
    lbl.setStyleSheet("background: transparent; padding: 2px 0;")
    return lbl


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DASHBOARD WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

class TricorderDashboard(QMainWindow):
    def __init__(self):
        super().__init__()

        # Dark title-bar on Windows
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), 4
            )
        except Exception:
            pass

        self.setWindowTitle("System Tricorder v0.2")
        self.setMinimumSize(1280, 900)
        self.setStyleSheet("QMainWindow, QWidget { background-color: #0a0a0f; color: white; }")

        self._analyze_hardware()

        self.gpu_widget_rows: List[Dict] = []
        self.thread_widgets: Dict[int, MasterMetricBox] = {}

        self._setup_ui()

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()

        self.hw_thread = HardwareMonitorThread()
        self.hw_thread.metrics_updated.connect(self._update_ui)
        self.hw_thread.start()

    # ─── Hardware Analysis ────────────────────────────────────────────────────
    def _analyze_hardware(self):
        self.c_physical = psutil.cpu_count(logical=False) or 4
        self.c_logical  = psutil.cpu_count(logical=True)  or 4
        self.is_amd     = "AMD" in platform.processor()

        self.is_hybrid = False
        self.has_ht    = False
        self.p_cores   = 0
        self.e_cores   = 0
        self.p_threads = self.c_logical
        self.e_threads = 0

        # ── True topology via GetLogicalProcessorInformationEx ────────────────
        # This reads EfficiencyClass per core directly from the Win32 API —
        # the same source Task Manager uses. Works regardless of HT on/off.
        topo = _get_cpu_topology()
        if topo and not self.is_amd:
            self.is_hybrid = topo['is_hybrid']
            self.p_cores   = topo['p_cores']
            self.e_cores   = topo['e_cores']
            self.p_threads = topo['p_threads']
            self.e_threads = topo['e_threads']

        if not self.is_hybrid:
            self.has_ht = (self.c_logical == 2 * self.c_physical)

        # Multi-socket detection
        self.num_sockets = 1
        if WMI_AVAILABLE:
            try:
                pythoncom.CoInitialize()  # type: ignore
                wmi = win32com.client.GetObject("winmgmts:root\\cimv2")  # type: ignore
                self.num_sockets = max(1, len(list(wmi.ExecQuery("SELECT Name FROM Win32_Processor"))))
            except Exception:
                pass

        # RAM type
        self.ram_type = "RAM"
        if WMI_AVAILABLE:
            try:
                wmi = win32com.client.GetObject("winmgmts:root\\cimv2")  # type: ignore
                for m in wmi.ExecQuery("SELECT SMBIOSMemoryType, Speed FROM Win32_PhysicalMemory"):
                    smt = int(m.SMBIOSMemoryType or 0)
                    spd = int(m.Speed or 0)
                    if smt in (34, 35):    self.ram_type = "DDR5"
                    elif smt == 26:         self.ram_type = "DDR4"
                    elif spd >= 4800:       self.ram_type = "DDR5"
                    elif spd > 0:           self.ram_type = "DDR4"
                    break
            except Exception:
                pass

        # Discrete GPU list: (display_name, vram_total_gb)
        wmi_gpus  = get_wmi_gpu_list()
        reg_vrams = get_registry_gpu_vrams()
        dgpu_wmi  = [(n, v) for n, ig, v in wmi_gpus if not ig]

        self.detected_gpus: List[Tuple[str, float]] = []
        for i, (name, wv) in enumerate(dgpu_wmi):
            vram = reg_vrams[i] if i < len(reg_vrams) else (math.ceil(wv) if wv >= 1.0 else 8.0)
            self.detected_gpus.append((name, float(vram)))
        if not self.detected_gpus:
            self.detected_gpus = [("GPU", reg_vrams[0])]

    # ─── Clock ────────────────────────────────────────────────────────────────
    def _update_clock(self):
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S     %d.%m.%Y"))

    # ─── UI Setup ─────────────────────────────────────────────────────────────
    def _setup_ui(self):
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root = QVBoxLayout(root_widget)
        root.setContentsMargins(15, 12, 15, 12)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        h = QHBoxLayout()
        title = QLabel("📊  System Tricorder  <span style='font-size:18px; color:#00aa55;'>v0.2</span>")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #00ff88; background: transparent;")
        h.addWidget(title)
        h.addSpacing(16)

        # CPU / socket info subtitle
        sock_txt = f"  ·  {self.num_sockets}× Socket" if self.num_sockets > 1 else ""
        cpu_hint = f"{self.c_physical}C / {self.c_logical}T{sock_txt}"
        if self.is_hybrid:
            cpu_hint += f"  ·  {self.p_cores}P + {self.e_cores}E"
        elif self.has_ht:
            cpu_hint += "  ·  HT"
        info = QLabel(cpu_hint)
        info.setStyleSheet("font-size: 11px; color: #444; background: transparent; padding-top: 12px;")
        h.addWidget(info)

        h.addStretch()
        self.clock_label = QLabel()
        self.clock_label.setStyleSheet(
            "font-size: 24px; font-weight: bold; color: #888; "
            "font-family: Consolas; background: transparent;"
        )
        h.addWidget(self.clock_label)
        root.addLayout(h)
        root.addSpacing(10)

        # ── Global Metrics Grid ───────────────────────────────────────────────
        root.addWidget(section_label(
            "<b style='color:#00ff88; font-size:14px;'>▸ Global System Core &amp; Graphics</b>"
        ))
        root.addSpacing(4)
        self._build_global_grid(root)
        root.addSpacing(14)

        # ── CPU Core Topology ─────────────────────────────────────────────────
        if self.is_hybrid:
            self._build_hybrid_cores(root)
        elif self.has_ht:
            self._build_ht_cores(root)
        else:
            self._build_simple_cores(root)

    # ── Global Grid ───────────────────────────────────────────────────────────
    def _build_global_grid(self, parent: QVBoxLayout):
        grid = QGridLayout()
        grid.setSpacing(6)
        num_gpus = len(self.detected_gpus)

        if num_gpus <= 1:
            # ── Classic 2×5 layout (compatible with single GPU) ──────────────
            pal = GPU_PALETTES[0]
            self.w_cpu_total = MasterMetricBox("CPU Gesamt",  "#00d4ff")
            self.w_gpu_3d    = MasterMetricBox("3D / Compute", pal[0])
            self.w_igpu      = MasterMetricBox("iGPU",        "#0055ff")
            self.w_gpu_c0    = MasterMetricBox("GPU Copy 0",  pal[1])
            self.w_ssd_r     = MasterMetricBox("SSD Read",    "#00ffcc")
            self.w_ram       = MasterMetricBox(f"{self.ram_type} RAM", "#ff007f")
            self.w_vram      = MasterMetricBox("VRAM",        pal[3])
            self.w_npu       = MasterMetricBox("NPU",         "#aa00ff")
            self.w_gpu_c1    = MasterMetricBox("GPU Copy 1",  pal[2])
            self.w_ssd_w     = MasterMetricBox("SSD Write",   "#ffcc00")

            for i, w in enumerate([
                self.w_cpu_total, self.w_gpu_3d, self.w_igpu, self.w_gpu_c0, self.w_ssd_r,
                self.w_ram, self.w_vram, self.w_npu, self.w_gpu_c1, self.w_ssd_w,
            ]):
                grid.addWidget(w, i // 5, i % 5)

            self.gpu_widget_rows = [{'3d': self.w_gpu_3d, 'c0': self.w_gpu_c0,
                                     'c1': self.w_gpu_c1, 'vram': self.w_vram}]
            for r in range(2): grid.setRowStretch(r, 1)

        else:
            # ── Multi-GPU layout ─────────────────────────────────────────────
            # Row 0: system-wide stats
            self.w_cpu_total = MasterMetricBox("CPU Gesamt",       "#00d4ff")
            self.w_ram       = MasterMetricBox(f"{self.ram_type} RAM", "#ff007f")
            self.w_ssd_r     = MasterMetricBox("SSD Read",         "#00ffcc")
            self.w_ssd_w     = MasterMetricBox("SSD Write",        "#ffcc00")
            self.w_npu       = MasterMetricBox("NPU",              "#aa00ff")
            self.w_igpu      = MasterMetricBox("iGPU",             "#0055ff")
            grid.addWidget(self.w_cpu_total, 0, 0)
            grid.addWidget(self.w_ram,       0, 1)
            grid.addWidget(self.w_ssd_r,     0, 2)
            grid.addWidget(self.w_ssd_w,     0, 3)
            grid.addWidget(self.w_npu,       0, 4)

            self.gpu_widget_rows = []
            for gi, (gname, _) in enumerate(self.detected_gpus):
                pal  = GPU_PALETTES[gi % len(GPU_PALETTES)]
                sn   = short_gpu_name(gname)
                row  = gi + 1
                w3d  = MasterMetricBox(f"{sn} · 3D",    pal[0])
                wc0  = MasterMetricBox(f"{sn} · Copy0", pal[1])
                wc1  = MasterMetricBox(f"{sn} · Copy1", pal[2])
                wvr  = MasterMetricBox(f"{sn} · VRAM",  pal[3])
                grid.addWidget(w3d, row, 0)
                grid.addWidget(wc0, row, 1)
                grid.addWidget(wc1, row, 2)
                grid.addWidget(wvr, row, 3)
                if gi == 0:
                    grid.addWidget(self.w_igpu, row, 4)
                self.gpu_widget_rows.append({'3d': w3d, 'c0': wc0, 'c1': wc1, 'vram': wvr})

            for r in range(num_gpus + 1):
                grid.setRowStretch(r, 1)

        for c in range(5):
            grid.setColumnStretch(c, 1)
        parent.addLayout(grid, 1)

    # ── CPU Core Topology: Intel Hybrid (P/E Cores) ───────────────────────────
    def _build_hybrid_cores(self, parent: QVBoxLayout):
        P_COLOR  = "#00d4ff"   # Blue  — P-Cores (high-performance)
        HT_COLOR = "#0077aa"   # Dimmed blue — P-Core HT siblings
        E_COLOR  = "#ff007f"   # Pink  — E-Cores (efficiency)

        p_has_ht = (self.p_threads == self.p_cores * 2)
        rows_p   = 2 if p_has_ht else 1
        ht_suffix = " · HT" if p_has_ht else ""

        # ── P-Core section ────────────────────────────────────────────────────
        parent.addWidget(section_label(
            f"<b style='color:{P_COLOR}; font-size:14px;'>⚡ Performance Cores "
            f"({self.p_cores} Cores / {self.p_threads} Threads, "
            f"Threads 0–{self.p_threads - 1})</b>"
        ))
        parent.addSpacing(4)

        p_grid = QGridLayout()
        p_grid.setSpacing(6)
        for ci in range(self.p_cores):
            t0 = ci * rows_p
            w0 = MasterMetricBox(f"P-Core {ci}", P_COLOR, variant='standard')
            p_grid.addWidget(w0, 0, ci)
            self.thread_widgets[t0] = w0
            if p_has_ht:
                t1 = t0 + 1
                w1 = MasterMetricBox(f"P-Core {ci}", HT_COLOR, variant='ht')
                p_grid.addWidget(w1, 1, ci)
                self.thread_widgets[t1] = w1

        for r in range(rows_p):          p_grid.setRowStretch(r, 1)
        for c in range(self.p_cores):    p_grid.setColumnStretch(c, 1)
        parent.addLayout(p_grid)
        parent.addSpacing(14)

        # ── E-Core section ────────────────────────────────────────────────────
        parent.addWidget(section_label(
            f"<b style='color:{E_COLOR}; font-size:14px;'>🔋 Efficiency Cores "
            f"({self.e_cores} Cores / {self.e_threads} Threads, "
            f"Threads {self.p_threads}–{self.p_threads + self.e_threads - 1})</b>"
        ))
        parent.addSpacing(4)

        e_grid = QGridLayout()
        e_grid.setSpacing(6)
        # Aim for ~2 rows of E-cores; if <= 8 E-cores use 1 row
        cols_e = self.e_cores if self.e_cores <= 8 else math.ceil(self.e_cores / 2)
        for i in range(self.e_threads):
            t = self.p_threads + i
            w = MasterMetricBox(f"E-Core {i}", E_COLOR, variant='efficiency')
            e_grid.addWidget(w, i // cols_e, i % cols_e)
            self.thread_widgets[t] = w
        rows_e = math.ceil(self.e_threads / cols_e)
        for r in range(rows_e):    e_grid.setRowStretch(r, 1)
        for c in range(cols_e):    e_grid.setColumnStretch(c, 1)
        parent.addLayout(e_grid)

    # ── CPU Core Topology: HT / SMT pairs ─────────────────────────────────────
    def _build_ht_cores(self, parent: QVBoxLayout):
        if self.is_amd:
            PHYS_COLOR = "#ff6600"   # AMD orange — physical
            SMT_COLOR  = "#aa3300"   # Dimmed — SMT sibling
            brand_lbl  = "AMD Ryzen"
            smt_label  = "SMT"
            variant    = 'smt'
        else:
            PHYS_COLOR = "#00d4ff"   # Intel cyan — physical
            SMT_COLOR  = "#0077aa"   # Dimmed — HT sibling
            brand_lbl  = "Intel Core"
            smt_label  = "HT"
            variant    = 'ht'

        n_phys = self.c_physical

        parent.addWidget(section_label(
            f"<b style='color:{PHYS_COLOR}; font-size:14px;'>{brand_lbl} Threads "
            f"— {smt_label} Pairs (0–{self.c_logical - 1})</b>"
        ))
        parent.addSpacing(2)

        hint = QLabel(
            f"<span style='color:#333; font-size:10px;'>"
            f"Row 1 = Physical Cores &nbsp;|&nbsp; Row 2 = {smt_label} Siblings"
            f"</span>"
        )
        hint.setStyleSheet("background: transparent;")
        parent.addWidget(hint)
        parent.addSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(6)

        for ci in range(n_phys):
            # Thread pairing: (2i, 2i+1) are HT/SMT partners on most Intel/AMD
            t_phys = ci * 2
            t_smt  = ci * 2 + 1
            w_phys = MasterMetricBox(f"Core {ci}",  PHYS_COLOR, variant='standard')
            w_smt  = MasterMetricBox(f"Core {ci}",  SMT_COLOR,  variant=variant)
            grid.addWidget(w_phys, 0, ci)
            grid.addWidget(w_smt,  1, ci)
            self.thread_widgets[t_phys] = w_phys
            self.thread_widgets[t_smt]  = w_smt

        for r in range(2):         grid.setRowStretch(r, 1)
        for c in range(n_phys):   grid.setColumnStretch(c, 1)
        parent.addLayout(grid)

    # ── CPU Core Topology: No HT / No SMT ────────────────────────────────────
    def _build_simple_cores(self, parent: QVBoxLayout):
        color = "#ff6600" if self.is_amd else "#00d4ff"
        brand = "AMD Ryzen" if self.is_amd else "Intel Core"
        label = "CCX Threads" if self.is_amd else "Threads"

        parent.addWidget(section_label(
            f"<b style='color:{color}; font-size:14px;'>{brand} {label} "
            f"(0–{self.c_logical - 1})</b>"
        ))
        parent.addSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(6)
        cols = max(1, (self.c_logical + 1) // 2)

        for i in range(self.c_logical):
            w = MasterMetricBox(f"Thread {i}", color)
            grid.addWidget(w, i // cols, i % cols)
            self.thread_widgets[i] = w

        for r in range(2):      grid.setRowStretch(r, 1)
        for c in range(cols):   grid.setColumnStretch(c, 1)
        parent.addLayout(grid)

    # ─── UI Update (called at 20 FPS) ─────────────────────────────────────────
    def _update_ui(self, m: SystemMetrics):
        self.w_cpu_total.update_val(m.cpu_total_percent)
        self.w_ram.update_val(m.ram_percent, f"{m.ram_used_gb:.1f} / {m.ram_total_gb:.1f} GB")
        self.w_npu.update_val(m.npu_percent)
        self.w_igpu.update_val(m.igpu_percent)
        self.w_ssd_r.update_val(min((m.disk_read_mbps  / 1000) * 100, 100), f"{m.disk_read_mbps:.1f} MB/s")
        self.w_ssd_w.update_val(min((m.disk_write_mbps / 1000) * 100, 100), f"{m.disk_write_mbps:.1f} MB/s")

        # GPU rows
        for i, gm in enumerate(m.gpus):
            if i >= len(self.gpu_widget_rows):
                break
            row = self.gpu_widget_rows[i]
            row['3d'].update_val(gm.gpu_3d_percent)
            row['c0'].update_val(gm.gpu_copy0_percent)
            row['c1'].update_val(gm.gpu_copy1_percent)
            vp = (gm.gpu_vram_used_gb / gm.gpu_vram_total_gb * 100) if gm.gpu_vram_total_gb else 0
            row['vram'].update_val(vp, f"{gm.gpu_vram_used_gb:.1f} / {gm.gpu_vram_total_gb:.0f} GB")

        # CPU thread widgets
        for ti, val in m.cpu_cores.items():
            if ti in self.thread_widgets:
                self.thread_widgets[ti].update_val(val)

    def closeEvent(self, event):  # type: ignore
        self.hw_thread.stop()
        event.accept()  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TricorderDashboard()
    win.showMaximized()
    sys.exit(app.exec_())