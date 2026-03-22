#!/usr/bin/env python3
"""
System Tricorder v1.1 - Hardware Monitoring Dashboard
Dark Mode | 20 FPS 
"""

import sys
import time
import math
import platform
import psutil
from collections import deque
from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QGridLayout, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QBrush

# --- WMI & WinReg Initialisierung ---
try:
    import pythoncom
    import win32com.client
    WMI_AVAILABLE = True
except ImportError:
    WMI_AVAILABLE = False

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    WINREG_AVAILABLE = False


def get_real_vram_gb() -> float:
    """
    Liest den VRAM über die Registry aus und iteriert über ALLE GPUs,
    um die stärkste Karte zu finden (verhindert dass eine iGPU gewinnt).
    Konvertiert Binary-Daten korrekt.
    """
    max_vram = 0.0
    if WINREG_AVAILABLE:
        try:
            base_key = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E968-E325-11CE-BFC1-08002BE10318}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_key) as key:
                for i in range(20):
                    try:
                        subkey_name = f"{i:04d}"
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            vram = 0.0
                            for val_name in ["HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"]:
                                try:
                                    vdata, _ = winreg.QueryValueEx(subkey, val_name)
                                    if isinstance(vdata, bytes):
                                        vbytes = int.from_bytes(vdata, byteorder='little')
                                    else:
                                        vbytes = int(vdata)
                                        
                                    temp_vram = float(vbytes) / (1024**3)
                                    if temp_vram > vram:
                                        vram = temp_vram
                                except FileNotFoundError:
                                    pass
                            if vram > max_vram:
                                max_vram = vram
                    except OSError:
                        pass
        except Exception:
            pass

    if max_vram >= 1.0:
        return float(math.ceil(max_vram)) # Rundet z.B. 15.8 GB auf glatte 16.0 GB auf

    # Fallback auf WMI
    if WMI_AVAILABLE:
        try:
            pythoncom.CoInitialize() # type: ignore
            wmi = win32com.client.GetObject("winmgmts:root\\cimv2") # type: ignore
            controllers = wmi.ExecQuery("SELECT AdapterRAM FROM Win32_VideoController")
            for c in controllers:
                if c.AdapterRAM:
                    vgb = float(c.AdapterRAM) / (1024**3)
                    if vgb > max_vram:
                        max_vram = vgb
        except Exception:
            pass
            
    if max_vram >= 1.0:
        return float(math.ceil(max_vram))
        
    return 8.0 # Absoluter Notfall-Fallback


@dataclass
class SystemMetrics:
    cpu_total_percent: float
    cpu_cores: Dict[int, float]
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    gpu_3d_percent: float
    gpu_copy0_percent: float
    gpu_copy1_percent: float
    gpu_vram_used_gb: float
    gpu_vram_total_gb: float
    igpu_percent: float
    npu_percent: float
    disk_read_mbps: float
    disk_write_mbps: float
    timestamp: datetime

# ============================================================================
# THREAD: HARDWARE MONITORING (20 FPS)
# ============================================================================
class HardwareMonitorThread(QThread):
    metrics_updated = pyqtSignal(SystemMetrics)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.vram_total_gb = get_real_vram_gb()
        
    def run(self):
        self._running = True
        if WMI_AVAILABLE:
            pythoncom.CoInitialize()  # type: ignore
        
        try:
            wmi = win32com.client.GetObject("winmgmts:root\\cimv2") if WMI_AVAILABLE else None  # type: ignore
        except Exception:
            wmi = None

        self.last_disk_io = psutil.disk_io_counters()
        self.last_time = time.time()

        while self._running:
            try:
                current_time = time.time()
                current_io = psutil.disk_io_counters()
                dt = max(current_time - self.last_time, 0.001)
                
                disk_read_mbps = 0.0
                disk_write_mbps = 0.0
                if current_io and self.last_disk_io:
                    disk_read_mbps = ((current_io.read_bytes - self.last_disk_io.read_bytes) / (1024 * 1024)) / dt
                    disk_write_mbps = ((current_io.write_bytes - self.last_disk_io.write_bytes) / (1024 * 1024)) / dt
                    
                self.last_disk_io = current_io
                self.last_time = current_time
                
                cpu_total = psutil.cpu_percent(interval=None)
                cpu_cores = {i: float(v) for i, v in enumerate(psutil.cpu_percent(percpu=True))}
                ram = psutil.virtual_memory()
                
                gpu_3d, gpu_c0, gpu_c1, vram_used_g, igpu_p, npu_p = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
                
                if wmi:
                    amd_luid = ""
                    try:
                        adapters = wmi.ExecQuery("SELECT Name, DedicatedUsage FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory")
                        for a in adapters:
                            u_gb = float(a.DedicatedUsage) / (1024**3)
                            if u_gb > vram_used_g: 
                                vram_used_g = u_gb
                                amd_luid = str(a.Name).split('_phys')[0]
                    except: pass
                    
                    try:
                        engines = wmi.ExecQuery("SELECT Name, UtilizationPercentage FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine")
                        for e in engines:
                            name, util = str(e.Name).lower(), float(e.UtilizationPercentage)
                            if util > 0:
                                if amd_luid and amd_luid.lower() in name:
                                    if "3d" in name or "compute" in name: gpu_3d = min(gpu_3d + util, 100.0)
                                    elif "copy" in name:
                                        if " 0" in name: gpu_c0 = max(gpu_c0, util)
                                        elif " 1" in name: gpu_c1 = max(gpu_c1, util)
                                elif "intel" in name and "graphics" in name: igpu_p = max(igpu_p, util)
                                elif "ai boost" in name or "npu" in name: npu_p = max(npu_p, util)
                    except: pass

                # Fallback Auto-Scaler falls wider Erwarten die GPU doch mehr nutzt als erkannt
                if vram_used_g > self.vram_total_gb:
                    self.vram_total_gb = math.ceil(vram_used_g)

                self.metrics_updated.emit(SystemMetrics(
                    cpu_total_percent=cpu_total, cpu_cores=cpu_cores,
                    ram_total_gb=ram.total / (1024 ** 3), ram_used_gb=ram.used / (1024 ** 3), ram_percent=ram.percent,
                    gpu_3d_percent=gpu_3d, gpu_copy0_percent=gpu_c0, gpu_copy1_percent=gpu_c1,
                    gpu_vram_used_gb=vram_used_g, gpu_vram_total_gb=self.vram_total_gb,
                    igpu_percent=igpu_p, npu_percent=npu_p,
                    disk_read_mbps=disk_read_mbps, disk_write_mbps=disk_write_mbps, timestamp=datetime.now()
                ))
            except Exception: pass
            time.sleep(0.05)

    def stop(self):
        self._running = False
        self.wait()

# ============================================================================
# GUI KOMPONENTEN
# ============================================================================
class MasterGraphWidget(QWidget):
    def __init__(self, color_hex: str, history_len: int = 60, parent=None):
        super().__init__(parent)
        self.color = QColor(color_hex)
        self.history = deque([0.0] * history_len, maxlen=history_len)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(60)

    def add_value(self, value: float):
        self.history.append(value)
        self.update()

    def paintEvent(self, a0):  # type: ignore
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        
        painter.fillRect(self.rect(), QColor(18, 18, 24))
        painter.setPen(QPen(QColor(45, 45, 55), 1))
        for x in range(0, w, 25): painter.drawLine(x, 0, x, h)
        for y in range(0, h, 15): painter.drawLine(0, y, w, y)
        
        if not self.history: return
        path = QPainterPath()
        step_x = w / (max(len(self.history) - 1, 1))
        for i, val in enumerate(self.history):
            y = h - (min(max(val, 0.0), 100.0) / 100.0 * h)
            if i == 0: path.moveTo(0, y)
            else: path.lineTo(i * step_x, y)
            
        painter.setPen(QPen(self.color, 2))
        painter.drawPath(path)
        path.lineTo(w, h); path.lineTo(0, h)
        fill = QColor(self.color); fill.setAlpha(35)
        painter.setBrush(QBrush(fill))
        painter.setPen(Qt.NoPen)  # type: ignore
        painter.drawPath(path)

class MasterMetricBox(QFrame):
    def __init__(self, title: str, color_hex: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{ 
                background-color: #121218; 
                border: 1px solid #222; 
                border-radius: 6px; 
                border-top: 3px solid {color_hex};
            }}
            QLabel {{ background: transparent; border: none; }}
        """)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        
        header = QHBoxLayout()
        self.id_lbl = QLabel(title)
        self.id_lbl.setStyleSheet(f"color: {color_hex}; font-size: 11px; font-weight: bold;")
        self.val_lbl = QLabel("0%")
        self.val_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        
        header.addWidget(self.id_lbl)
        header.addStretch()
        header.addWidget(self.val_lbl)
        layout.addLayout(header)
        
        self.graph = MasterGraphWidget(color_hex)
        layout.addWidget(self.graph)

    def update_val(self, val: float, text: Optional[str] = None):
        self.graph.add_value(val)
        self.val_lbl.setText(text if text else f"{int(val)}%")

# ============================================================================
# MAIN WINDOW
# ============================================================================
class TricorderDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        
        import ctypes
        try: ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception: pass
        
        self.setWindowTitle("System Tricorder v0.1")
        self.setMinimumSize(1280, 900)
        
        self.setStyleSheet("""
            QMainWindow { background-color: #0a0a0f; }
            QWidget { background-color: #0a0a0f; color: white; }
        """)
        
        self._analyze_hardware()
        
        self.block1_items = []
        self.block2_items = []
        
        self._setup_ui()
        
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        
        self.hw_thread = HardwareMonitorThread()
        self.hw_thread.metrics_updated.connect(self._update_ui)
        self.hw_thread.start()

    def _analyze_hardware(self):
        """Erkennt selbständig P/E Cores und RAM-Typ"""
        self.c_physical = psutil.cpu_count(logical=False) or 4
        self.c_logical = psutil.cpu_count(logical=True) or 4
        self.is_amd = "AMD" in platform.processor()
        
        self.is_hybrid = False
        self.p_threads = self.c_logical
        self.e_threads = 0

        # --- CPU Topologie erkennen ---
        if not self.is_amd:
            # Bei Intel Hybrid: Threads sind größer als Cores, aber kleiner als 2 * Cores. 
            if self.c_logical > self.c_physical and self.c_logical < 2 * self.c_physical:
                self.is_hybrid = True
                p_cores = self.c_logical - self.c_physical
                self.e_threads = 2 * self.c_physical - self.c_logical
                self.p_threads = p_cores * 2

        # --- RAM Typ erkennen ---
        self.ram_type = "RAM"
        if WMI_AVAILABLE:
            try:
                wmi = win32com.client.GetObject("winmgmts:root\\cimv2") # type: ignore
                for mem in wmi.ExecQuery("SELECT SMBIOSMemoryType, Speed FROM Win32_PhysicalMemory"):
                    if mem.SMBIOSMemoryType == 34 or mem.SMBIOSMemoryType == 35:
                        self.ram_type = "DDR5"
                    elif mem.SMBIOSMemoryType == 26:
                        self.ram_type = "DDR4"
                    elif mem.Speed:
                        if int(mem.Speed) >= 4800: self.ram_type = "DDR5"
                        elif int(mem.Speed) > 0: self.ram_type = "DDR4"
                    break
            except: pass

    def _update_clock(self):
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S     %d.%m.%Y"))

    def _setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # --- HEADER ---
        h_layout = QHBoxLayout()
        header = QLabel("📊 System Tricorder v0.1")
        header.setStyleSheet("font-size: 28px; font-weight: bold; color: #00ff88; margin: 5px; background: transparent;")
        h_layout.addWidget(header)
        h_layout.addStretch()
        
        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #aaa; font-family: Consolas; background: transparent; padding-right: 10px;")
        h_layout.addWidget(self.clock_label)
        layout.addLayout(h_layout)
        layout.addSpacing(10)
        
        # --- GLOBAL SYSTEMS GRID ---
        lbl_global = QLabel("<b style='color:#00ff88; font-size: 15px;'>Global System Core & Graphics</b>")
        lbl_global.setStyleSheet("background: transparent;")
        layout.addWidget(lbl_global)
        
        g_grid = QGridLayout()
        g_grid.setSpacing(6)
        
        # Row 0
        self.w_cpu_total = MasterMetricBox("CPU Gesamt", "#00d4ff")
        self.w_gpu_3d = MasterMetricBox("3D/Compute", "#ff5500")
        self.w_igpu = MasterMetricBox("iGPU", "#0055ff")
        self.w_gpu_c0 = MasterMetricBox("GPU Copy 0", "#ff7700")
        self.w_ssd_r = MasterMetricBox("SSD Read", "#00ffcc")
        
        # Row 1 (CPU & RAM übereinander, 3D/Compute & VRAM übereinander, etc.)
        self.w_ram = MasterMetricBox(f"{self.ram_type} RAM", "#ff007f")
        self.w_vram = MasterMetricBox("VRAM", "#ffaa00")
        self.w_npu = MasterMetricBox("NPU", "#aa00ff")
        self.w_gpu_c1 = MasterMetricBox("GPU Copy 1", "#ff9900")
        self.w_ssd_w = MasterMetricBox("SSD Write", "#ffcc00")
        
        glob_widgets = [self.w_cpu_total, self.w_gpu_3d, self.w_igpu, self.w_gpu_c0, self.w_ssd_r, 
                        self.w_ram, self.w_vram, self.w_npu, self.w_gpu_c1, self.w_ssd_w]
        
        for i, w in enumerate(glob_widgets): 
            g_grid.addWidget(w, i // 5, i % 5)
        
        for i in range(2): g_grid.setRowStretch(i, 1)
        for i in range(5): g_grid.setColumnStretch(i, 1)
        layout.addLayout(g_grid)
        layout.addSpacing(15)
        
        # --- CORE TOPOLOGY GRIDS ---
        if self.is_hybrid:
            # P-Cores und E-Cores vorhanden (Blau / Pink)
            b1_count, b2_count = self.p_threads, self.e_threads
            b1_title, b2_title = "Performance Cores", "Efficiency Cores"
            b1_lbl_pfx, b2_lbl_pfx = "P-Thread", "E-Core"
            b1_color, b2_color = "#00d4ff", "#ff007f" # Blau für P-Cores, Pink für E-Cores
        else:
            # Nur P-Cores vorhanden (Alles Blau)
            b1_count, b2_count = self.c_logical, 0
            b1_title = "AMD Ryzen Threads" if self.is_amd else "Intel Core Threads"
            b1_lbl_pfx = "Thread"
            b1_color = "#00d4ff" # Einheitlich Blau

        # Block 1 Rendern
        lbl_b1 = QLabel(f"<b style='color:{b1_color}; font-size: 15px;'>{b1_title} (0-{b1_count-1})</b>")
        lbl_b1.setStyleSheet("background: transparent;")
        layout.addWidget(lbl_b1)
        
        b1_grid = QGridLayout()
        b1_grid.setSpacing(6)
        cols_b1 = max(1, (b1_count + 1) // 2)
        
        for i in range(b1_count):
            w = MasterMetricBox(f"{b1_lbl_pfx} {i}", b1_color)
            b1_grid.addWidget(w, i // cols_b1, i % cols_b1)
            self.block1_items.append(w)
            
        for i in range(2): b1_grid.setRowStretch(i, 1)
        for i in range(cols_b1): b1_grid.setColumnStretch(i, 1)
        layout.addLayout(b1_grid)
        
        # Block 2 Rendern (falls E-Cores vorhanden)
        if b2_count > 0:
            layout.addSpacing(15)
            lbl_b2 = QLabel(f"<b style='color:{b2_color}; font-size: 15px;'>{b2_title} ({b1_count}-{self.c_logical-1})</b>")
            lbl_b2.setStyleSheet("background: transparent;")
            layout.addWidget(lbl_b2)
            
            b2_grid = QGridLayout()
            b2_grid.setSpacing(6)
            cols_b2 = max(1, (b2_count + 1) // 2)
            
            for i in range(b2_count):
                w = MasterMetricBox(f"{b2_lbl_pfx} {i + b1_count}", b2_color)
                b2_grid.addWidget(w, i // cols_b2, i % cols_b2)
                self.block2_items.append(w)
                
            for i in range(2): b2_grid.setRowStretch(i, 1)
            for i in range(cols_b2): b2_grid.setColumnStretch(i, 1)
            layout.addLayout(b2_grid)

    def _update_ui(self, m: SystemMetrics):
        self.w_cpu_total.update_val(m.cpu_total_percent)
        self.w_ram.update_val(m.ram_percent, f"{m.ram_used_gb:.1f} / {m.ram_total_gb:.1f} GB")
        
        self.w_npu.update_val(m.npu_percent)
        self.w_igpu.update_val(m.igpu_percent)
        
        vram_percent = (m.gpu_vram_used_gb / m.gpu_vram_total_gb) * 100 if m.gpu_vram_total_gb else 0
        self.w_vram.update_val(vram_percent, f"{m.gpu_vram_used_gb:.1f} / {m.gpu_vram_total_gb:.0f} GB")
        
        self.w_gpu_3d.update_val(m.gpu_3d_percent)
        self.w_gpu_c0.update_val(m.gpu_copy0_percent)
        self.w_gpu_c1.update_val(m.gpu_copy1_percent)
        
        self.w_ssd_r.update_val(min((m.disk_read_mbps/1000)*100, 100), f"{m.disk_read_mbps:.1f} MB/s")
        self.w_ssd_w.update_val(min((m.disk_write_mbps/1000)*100, 100), f"{m.disk_write_mbps:.1f} MB/s")
        
        for i, val in m.cpu_cores.items():
            if i < len(self.block1_items):
                self.block1_items[i].update_val(val)
            else:
                idx_b2 = i - len(self.block1_items)
                if idx_b2 < len(self.block2_items):
                    self.block2_items[idx_b2].update_val(val)

    def closeEvent(self, a0):  # type: ignore
        self.hw_thread.stop()
        a0.accept()  # type: ignore

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TricorderDashboard()
    win.showMaximized()
    sys.exit(app.exec_())