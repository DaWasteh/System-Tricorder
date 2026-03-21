#!/usr/bin/env python3
"""
System Tricorder v0.1 - Hardware Monitoring Dashboard
Dark Mode | 20 FPS
"""

import sys
import time
import platform
import psutil
from collections import deque
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QGridLayout, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QBrush

# --- KONFIGURATION ---
DEFAULT_VRAM_GB = 16.0

# --- WMI Initialisierung für GPU Sensoren ---
try:
    import pythoncom
    import win32com.client
    WMI_AVAILABLE = True
except ImportError:
    WMI_AVAILABLE = False

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
        self.vram_total_gb = DEFAULT_VRAM_GB
        
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

        if wmi:
            try:
                controllers = wmi.ExecQuery("SELECT AdapterRAM FROM Win32_VideoController")
                for c in controllers:
                    if c.AdapterRAM:
                        vgb = float(c.AdapterRAM) / (1024**3)
                        if vgb > 2.0 and (vgb % 2 == 0 or vgb % 2 == 1):
                            self.vram_total_gb = max(self.vram_total_gb, round(vgb))
            except: pass

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

                if vram_used_g > self.vram_total_gb:
                    self.vram_total_gb = vram_used_g * 1.1

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
# HARDWARE INFO WIDGETS
# ============================================================================
class HardwareInfoWidget(QFrame):
    """Widget zur Anzeige von Hardware-Details (CPU, GPU, RAM, etc.)"""
    def __init__(self, title: str, color_hex: str, info_text: str, parent=None):
        super().__init__(parent)
        self.info_text = info_text
        self.setStyleSheet(f"""
            QFrame {{ 
                background-color: #121218; 
                border: 1px solid #222; 
                border-radius: 6px; 
                border-top: 3px solid {color_hex};
            }}
            QLabel {{ background: transparent; border: none; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        
        # Header
        header = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {color_hex}; font-size: 12px; font-weight: bold;")
        header.addWidget(title_lbl)
        header.addStretch()
        layout.addLayout(header)
        
        # Info Text
        info_lbl = QLabel(info_text)
        info_lbl.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(info_lbl)

class HardwareInfoContainer(QFrame):
    """Container für Hardware-Info Widgets"""
    def __init__(self, title: str, color_hex: str, widgets: list, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{ 
                background-color: #0f0f14; 
                border: 1px solid #252525; 
                border-radius: 8px; 
                border-top: 4px solid {color_hex};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Title
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {color_hex}; font-size: 14px; font-weight: bold;")
        layout.addWidget(title_lbl)
        
        # Grid für Widgets
        grid = QGridLayout()
        grid.setSpacing(8)
        for widget in widgets:
            grid.addWidget(widget, 0, grid.columnCount())
        layout.addLayout(grid)

# ============================================================================
# MAIN WINDOW
# ============================================================================
class TricorderDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        
        import ctypes
        try: ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.winId()), 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception: pass
        
        self.setWindowTitle("System Tricorder v2.0")
        self.setMinimumSize(1280, 900)
        
        self.setStyleSheet("""
            QMainWindow { background-color: #0a0a0f; }
            QWidget { background-color: #0a0a0f; color: white; }
        """)
        
        # --- HARDWARE IDENTIFICATION ---
        self.cpu_brand, self.cpu_model, self.cpu_cores_count, self.cpu_threads_count = self._get_cpu_info()
        self.gpu_brand, self.gpu_model, self.gpu_memory, self.gpu_arch = self._get_gpu_info()
        self.ram_brand, self.ram_model, self.ram_speed, self.ram_total_gb = self._get_ram_info()
        
        # --- INITIALISIERUNG ---
        self.p_items = []
        self.e_items = []
        
        self._setup_ui()
        
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        
        self.hw_thread = HardwareMonitorThread()
        self.hw_thread.metrics_updated.connect(self._update_ui)
        self.hw_thread.start()

    def _get_cpu_info(self) -> Tuple[str, str, int, int]:
        """Erhalte CPU-Informationen (Marke, Modell, Cores, Threads)"""
        cpu_info = platform.processor()
        uname = platform.uname()
        
        # CPU Marke und Modell
        cpu_brand = "Unknown"
        cpu_model = "Unknown"
        
        if uname.system == "Windows":
            try:
                import wmi  # type: ignore
                c = wmi.WMI()
                for cpu in c.Win32_Processor():
                    cpu_brand = cpu.Manufacturer.strip()
                    cpu_model = cpu.Name.strip()
                    break
            except:
                pass
        
        # Fallback auf platform
        if cpu_brand == "Unknown":
            cpu_brand = "Intel" if "Intel" in cpu_info else "AMD" if "AMD" in cpu_info else "Unknown"
            cpu_model = cpu_info
        
        # Core/Thread Count
        cpu_cores_count = psutil.cpu_count(logical=False) or 4
        cpu_threads_count = psutil.cpu_count(logical=True) or 8
        
        return cpu_brand, cpu_model, cpu_cores_count, cpu_threads_count

    def _get_gpu_info(self) -> Tuple[str, str, str, str]:
        """Erhalte GPU-Informationen (Marke, Modell, Speicher, Architektur)"""
        gpu_brand = "Unknown"
        gpu_model = "Unknown"
        gpu_memory = "Unknown"
        gpu_arch = "Unknown"
        
        if WMI_AVAILABLE:
            try:
                pythoncom.CoInitialize()  # type: ignore
                wmi = win32com.client.GetObject("winmgmts:root\\cimv2")  # type: ignore
                
                # GPU Details
                for adapter in wmi.ExecQuery("SELECT Name, AdapterRAM, VideoProcessor FROM Win32_VideoController"):
                    gpu_model = adapter.Name.strip()
                    if adapter.AdapterRAM:
                        vgb = float(adapter.AdapterRAM) / (1024**3)
                        gpu_memory = f"{round(vgb)} GB"
                    break
                
                # GPU Architektur (über WMI oder psutil)
                try:
                    import wmi  # type: ignore
                    c = wmi.WMI()
                    for gpu in c.Win32_VideoController():
                        gpu_arch = gpu.DriverVersion.split('.')[0] if gpu.DriverVersion else "Unknown"
                        break
                except:
                    pass
                    
            except Exception as e:
                pass
        
        # Fallback auf psutil
        if gpu_model == "Unknown":
            try:
                import pynvml  # type: ignore
                pynvml.nvmlInit()
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                gpu_model = pynvml.nvmlDeviceGetName(handle).decode('utf-8')
                gpu_memory = pynvml.nvmlDeviceGetMemoryInfo(handle).total / (1024**3)
                gpu_memory = f"{round(gpu_memory)} GB"
                pynvml.nvmlShutdown()
            except:
                pass
        
        # Brand erkennen
        if "nvidia" in gpu_model.lower():
            gpu_brand = "NVIDIA"
        elif "intel" in gpu_model.lower() or "iris" in gpu_model.lower() or "arc" in gpu_model.lower():
            gpu_brand = "Intel"
        elif "amd" in gpu_model.lower() or "radeon" in gpu_model.lower() or "rx" in gpu_model.lower():
            gpu_brand = "AMD"
        
        return gpu_brand, gpu_model, gpu_memory, gpu_arch

    def _get_ram_info(self) -> Tuple[str, str, str, float]:
        """Erhalte RAM-Informationen (Marke, Modell, Geschwindigkeit, Total GB)"""
        ram_brand = "Unknown"
        ram_model = "Unknown"
        ram_speed = "Unknown"
        ram_total_gb = 0.0
        
        ram = psutil.virtual_memory()
        ram_total_gb = ram.total / (1024**3)
        
        # RAM Geschwindigkeit (über WMI auf Windows)
        if WMI_AVAILABLE:
            try:
                pythoncom.CoInitialize()  # type: ignore
                wmi = win32com.client.GetObject("winmgmts:root\\cimv2")  # type: ignore
                for mem in wmi.ExecQuery("SELECT Speed FROM Win32_PhysicalMemory"):
                    if mem.Speed:
                        ram_speed = f"{mem.Speed} MHz"
                        break
            except:
                pass
        
        # Fallback: DDR4/DDR5 basierend auf Geschwindigkeit
        if ram_speed == "Unknown":
            # Schätzung basierend auf typischen Geschwindigkeiten
            if ram_total_gb > 64:
                ram_speed = "DDR5"
            else:
                ram_speed = "DDR4"
        
        return ram_brand, ram_model, ram_speed, ram_total_gb

    def _update_clock(self):
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S     %d.%m.%Y"))

    def _setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # --- HEADER ---
        h_layout = QHBoxLayout()
        header = QLabel("📊 System Tricorder v2.0")
        header.setStyleSheet("font-size: 28px; font-weight: bold; color: #00ff88; margin: 5px; background: transparent;")
        h_layout.addWidget(header)
        h_layout.addStretch()
        
        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #aaa; font-family: Consolas; background: transparent; padding-right: 10px;")
        h_layout.addWidget(self.clock_label)
        layout.addLayout(h_layout)
        layout.addSpacing(10)
        
        # --- HARDWARE INFO SECTIONS ---
        
        # 1. CPU Info
        cpu_color = "#ff5500" if self.cpu_brand == "AMD" else "#ff007f"
        cpu_info_text = f"Brand: {self.cpu_brand} | Model: {self.cpu_model} | Cores: {self.cpu_cores_count} | Threads: {self.cpu_threads_count}"
        cpu_widget = HardwareInfoWidget("🖥️ CPU Hardware", cpu_color, cpu_info_text)
        layout.addWidget(cpu_widget)
        
        # 2. GPU Info
        gpu_color = "#ffaa00"
        gpu_info_text = f"Brand: {self.gpu_brand} | Model: {self.gpu_model} | Memory: {self.gpu_memory} | Arch: {self.gpu_arch}"
        gpu_widget = HardwareInfoWidget("🎮 GPU Hardware", gpu_color, gpu_info_text)
        layout.addWidget(gpu_widget)
        
        # 3. RAM Info
        ram_color = "#ff007f"
        ram_info_text = f"Total: {self.ram_total_gb:.1f} GB | Type: {self.ram_speed}"
        ram_widget = HardwareInfoWidget("💾 RAM Hardware", ram_color, ram_info_text)
        layout.addWidget(ram_widget)
        
        # --- GLOBAL SYSTEMS GRID ---
        lbl_global = QLabel("<b style='color:#00ff88; font-size: 15px;'>Global System Core & Graphics</b>")
        lbl_global.setStyleSheet("background: transparent;")
        layout.addWidget(lbl_global)
        
        g_grid = QGridLayout()
        g_grid.setSpacing(6)
        
        # Row 1
        self.w_cpu_total = MasterMetricBox("CPU Gesamt", "#00d4ff")
        self.w_ram = MasterMetricBox("DDR5 RAM", "#ff007f")
        self.w_npu = MasterMetricBox("Intel NPU", "#aa00ff")
        self.w_igpu = MasterMetricBox("Intel iGPU", "#0055ff")
        self.w_ssd_r = MasterMetricBox("SSD Read", "#00ffcc")
        
        # Row 2
        self.w_gpu_3d = MasterMetricBox("AMD 3D/Compute", "#ff5500")
        self.w_vram = MasterMetricBox("VRAM Total", "#ffaa00")
        self.w_gpu_c0 = MasterMetricBox("GPU Copy 0", "#ff7700")
        self.w_gpu_c1 = MasterMetricBox("GPU Copy 1", "#ff9900")
        self.w_ssd_w = MasterMetricBox("SSD Write", "#ffcc00")
        
        glob_widgets = [self.w_cpu_total, self.w_ram, self.w_npu, self.w_igpu, self.w_ssd_r, 
                        self.w_gpu_3d, self.w_vram, self.w_gpu_c0, self.w_gpu_c1, self.w_ssd_w]
        
        for i, w in enumerate(glob_widgets): 
            g_grid.addWidget(w, i // 5, i % 5)
        
        for i in range(2): g_grid.setRowStretch(i, 1)
        for i in range(5): g_grid.setColumnStretch(i, 1)
        layout.addLayout(g_grid)
        layout.addSpacing(15)
        
        # --- P-CORES ---
        if self.cpu_brand == "AMD":
            lbl_p = QLabel(f"<b style='color:#ff5500; font-size: 15px;'>AMD Ryzen Threads (0-{self.cpu_cores_count-1})</b>")
        else:
            lbl_p = QLabel(f"<b style='color:#ff007f; font-size: 15px;'>Performance Cores (0-{self.cpu_cores_count-1})</b>")
        
        lbl_p.setStyleSheet("background: transparent;")
        layout.addWidget(lbl_p)
        
        p_grid = QGridLayout()
        p_grid.setSpacing(6)
        
        cols_p = max(1, (self.cpu_cores_count + 1) // 2)
        p_color = "#ff5500" if self.cpu_brand == "AMD" else "#ff007f"
        
        for i in range(self.cpu_cores_count):
            title = f"Thread {i}" if self.cpu_brand == "AMD" else f"P-Core {i}"
            w = MasterMetricBox(title, p_color)
            p_grid.addWidget(w, i // cols_p, i % cols_p)
            self.p_items.append(w)
            
        for i in range(2): p_grid.setRowStretch(i, 1)
        for i in range(cols_p): p_grid.setColumnStretch(i, 1)
        layout.addLayout(p_grid)
        
        # --- E-CORES (nur wenn nicht AMD und E-Cores vorhanden) ---
        if not self.cpu_brand == "AMD" and self.cpu_threads_count > self.cpu_cores_count:
            layout.addSpacing(15)
            lbl_e = QLabel(f"<b style='color:#00d4ff; font-size: 15px;'>Efficiency Cores ({self.cpu_cores_count}-{self.cpu_threads_count-1})</b>")
            lbl_e.setStyleSheet("background: transparent;")
            layout.addWidget(lbl_e)
            
            e_grid = QGridLayout()
            e_grid.setSpacing(6)
            
            cols_e = max(1, (self.cpu_threads_count - self.cpu_cores_count + 1) // 2)
            
            for i in range(self.cpu_threads_count - self.cpu_cores_count):
                w = MasterMetricBox(f"E-Core {i + self.cpu_cores_count}", "#00d4ff")
                e_grid.addWidget(w, i // cols_e, i % cols_e)
                self.e_items.append(w)
                
            for i in range(2): e_grid.setRowStretch(i, 1)
            for i in range(cols_e): e_grid.setColumnStretch(i, 1)
            layout.addLayout(e_grid)

    def _update_ui(self, m: SystemMetrics):
        self.w_cpu_total.update_val(m.cpu_total_percent)
        self.w_ram.update_val(m.ram_percent, f"{m.ram_total_gb:.1f} GB / {m.ram_used_gb:.1f} GB")
        
        self.w_npu.update_val(m.npu_percent)
        self.w_igpu.update_val(m.igpu_percent)
        
        vram_percent = (m.gpu_vram_used_gb / m.gpu_vram_total_gb) * 100 if m.gpu_vram_total_gb else 0
        self.w_vram.update_val(vram_percent, f"{m.gpu_vram_total_gb:.0f} GB / {m.gpu_vram_used_gb:.1f} GB")
        
        self.w_gpu_3d.update_val(m.gpu_3d_percent)
        self.w_gpu_c0.update_val(m.gpu_copy0_percent)
        self.w_gpu_c1.update_val(m.gpu_copy1_percent)
        
        self.w_ssd_r.update_val(min((m.disk_read_mbps/1000)*100, 100), f"{m.disk_read_mbps:.1f} MB/s")
        self.w_ssd_w.update_val(min((m.disk_write_mbps/1000)*100, 100), f"{m.disk_write_mbps:.1f} MB/s")
        
        for i, val in m.cpu_cores.items():
            if i < len(self.p_items):
                self.p_items[i].update_val(val)
            elif i < len(self.e_items):
                self.e_items[i].update_val(val)

    def closeEvent(self, a0):  # type: ignore
        self.hw_thread.stop()
        a0.accept()  # type: ignore

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TricorderDashboard()
    win.showMaximized()
    sys.exit(app.exec_())
