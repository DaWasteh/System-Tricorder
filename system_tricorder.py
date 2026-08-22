#!/usr/bin/env python3
import sys
import time
import math
import threading
import json
import re
import platform
import logging
import contextlib
import ctypes
import subprocess
import shlex
import hashlib
import shutil
import tempfile
import stat
import urllib.error
import urllib.parse
import urllib.request
try:
    import pynvml                                               # type: ignore
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except Exception:
    pynvml = None                                               # type: ignore
    NVML_AVAILABLE = False
if platform.system() == 'Windows':
    from ctypes import wintypes  # pyright: ignore[reportAssignmentType]
else:
    # Define dummy wintypes on non-Windows platforms for ctypes structures.
    class wintypes:  # type: ignore[reportAssignmentType]
        DWORD = ctypes.c_uint32
        LPWSTR = ctypes.c_wchar_p
        ULONGLONG = ctypes.c_uint64

import psutil
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
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
    """Use Qt's native device-independent coordinate system.

    Qt 6 already applies each screen's DPR to widget geometry and fonts.  A
    second manual devicePixelRatio multiplier made the whole dashboard twice
    as large at 200% scaling and could not follow per-monitor DPI changes.
    ``app`` remains part of the API because initialization occurs after the
    QApplication is created.
    """
    del app
    global _DP_SCALE
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
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLayout,
    QLabel, QFrame, QGridLayout, QSizePolicy, QPushButton, QComboBox,
    QScrollArea, QDialog, QCheckBox, QDialogButtonBox, QMessageBox,
    QColorDialog, QMenu,
)
from PyQt6.QtCore  import (  # type: ignore
    Qt, QEvent, QTimer, pyqtSignal, QThread, QMimeData, QPoint, QSize, QByteArray,
)
from PyQt6.QtGui   import (                                         # type: ignore
    QColor, QPainter, QPainterPath, QPen, QBrush, QDrag, QPixmap, QIcon,
    QAction,
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
CONFIG_VERSION = "1.0"
APP_VERSION = "2.7.6"
GITHUB_REPO = "DaWasteh/System-Tricorder"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_REPO}.git"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_WINDOWS_ASSET = "SystemTricorder-windows-x86_64.exe"
_UPDATE_SOURCE_FILES = (
    "system_tricorder.py",
    "requirements.txt",
    "assets/SystemTricorder.png",
)
_UPDATE_ALLOWED_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})

# Theme state is established from the external config before dashboard widgets
# are built.  Dark CSS remains the canonical source so switching repeatedly
# never accumulates lossy colour substitutions.
_ACTIVE_THEME = "dark"
_LIGHT_STYLE_REPLACEMENTS = {
    "#0a0a0f": "#eef2f6",
    "#0e0e18": "#f6f8fb",
    "#121218": "#ffffff",
    "#0d0d1c": "#f8fafc",
    "#0c0c16": "#f4f7fa",
    "#1a1a28": "#d5dce4",
    "#1a1a22": "#d5dce4",
    "#1e1e2e": "#e3e8ee",
    "#2a2a3a": "#d8e0e8",
    "#2e2e3e": "#ced8e2",
    "#2a2a1a": "#fff2b3",
    "#3a3a2a": "#b4a24b",
    "#1a2a1a": "#e8f4ec",
    "#2a3a2a": "#d8eadf",
    "#2a4a2a": "#aec8b8",
    "#336633": "#3b7251",
    "#00ff88": "#008f4d",
    "#00aa55": "#007a3e",
    "#00d4ff": "#007f9f",
    "#00ffcc": "#007f70",
    "#ffcc00": "#8a6900",
    "#ffdd55": "#8a7500",
    "#444444": "#697582",
    "#ffffff": "#1f2933",
    "#fff": "#1f2933",
    "#ccc": "#344050",
    "#aaa": "#52606d",
    "#888": "#52606d",
    "#555": "#637080",
    "#444": "#697582",
    "#333": "#6b7785",
    "#222": "#cbd3dc",
}
_THEME_PAINT_COLORS = {
    "dark": {
        "graph_bg": "#0c0c14",
        "graph_grid": "#282834",
        "drop_border": "#2a3a2a",
        "drop_text": "#2a4a2a",
    },
    "light": {
        "graph_bg": "#f7f9fc",
        "graph_grid": "#d9e0e8",
        "drop_border": "#b8c5bd",
        "drop_text": "#71877a",
    },
}


def _normalise_theme(value: Any) -> str:
    return "light" if str(value).strip().lower() == "light" else "dark"


def _set_active_theme(theme: str) -> str:
    global _ACTIVE_THEME
    _ACTIVE_THEME = _normalise_theme(theme)
    return _ACTIVE_THEME


def _theme_color(name: str) -> str:
    return _THEME_PAINT_COLORS[_ACTIVE_THEME][name]


def _themed_css(css: str, protected_colors: Tuple[str, ...] = ()) -> str:
    """Render canonical dark CSS for the active light/dark theme.

    User-entered RGB/HEX accents are protected so Lightmode never changes an
    explicitly requested colour, even when it equals one of the factory hues.
    """
    if _ACTIVE_THEME == "dark" or not css:
        return css
    rendered = css
    placeholders: Dict[str, str] = {}
    for index, raw_color in enumerate(protected_colors):
        color = _normalise_color_hex(raw_color)
        if color is None:
            continue
        placeholder = f"__tricorder_user_color_{index}__"
        rendered = re.sub(
            re.escape(color) + r"(?![0-9a-fA-F])", placeholder, rendered,
            flags=re.IGNORECASE,
        )
        placeholders[placeholder] = color

    sources = sorted(_LIGHT_STYLE_REPLACEMENTS, key=len, reverse=True)
    pattern = re.compile(
        "|".join(re.escape(source) + r"(?![0-9a-fA-F])"
                 for source in sources),
        flags=re.IGNORECASE,
    )
    rendered = pattern.sub(
        lambda match: _LIGHT_STYLE_REPLACEMENTS[match.group(0).lower()], rendered)
    rendered = re.sub(r"\bwhite\b", "#1f2933", rendered, flags=re.IGNORECASE)
    for placeholder, color in placeholders.items():
        rendered = rendered.replace(placeholder, color)
    return rendered


def _set_themed_style(widget: QWidget, dark_css: str,
                      protected_colors: Tuple[str, ...] = ()) -> None:
    """Store drift-free canonical CSS and apply its active-theme rendering."""
    widget.setProperty("_tricorder_dark_stylesheet", dark_css)
    widget.setProperty("_tricorder_protected_colors", list(protected_colors))
    widget.setStyleSheet(_themed_css(dark_css, protected_colors))


def _apply_theme_tree(root: QWidget) -> None:
    """Re-theme existing widgets, rich text and custom-painted graph caches."""
    for widget in [root, *root.findChildren(QWidget)]:
        base_css = widget.property("_tricorder_dark_stylesheet")
        if not isinstance(base_css, str):
            base_css = widget.styleSheet()
            widget.setProperty("_tricorder_dark_stylesheet", base_css)
        protected = widget.property("_tricorder_protected_colors")
        protected_colors = tuple(
            color for color in protected if isinstance(color, str)
        ) if isinstance(protected, list) else ()
        if base_css:
            widget.setStyleSheet(_themed_css(base_css, protected_colors))

        if isinstance(widget, QLabel):
            text = widget.text()
            base_text = widget.property("_tricorder_dark_rich_text")
            if not isinstance(base_text, str) and "<" in text and "color:" in text:
                base_text = text
                widget.setProperty("_tricorder_dark_rich_text", base_text)
            if isinstance(base_text, str):
                widget.setText(_themed_css(base_text))

        invalidate_theme = getattr(widget, "_invalidate_theme", None)
        if callable(invalidate_theme):
            invalidate_theme()
    root.update()


def _normalise_color_hex(value: Any) -> Optional[str]:
    """Return a validated opaque ``#rrggbb`` colour or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    color = QColor(value.strip())
    if not color.isValid():
        return None
    return color.name(QColor.NameFormat.HexRgb).lower()


def _clock_display_mode(width: int) -> str:
    """Choose inline, stacked or hidden-date clock layout for a window width."""
    if width < 900:
        return "time-only"
    if width < 1300:
        return "stacked"
    return "inline"


def _set_windows_titlebar_theme(window: QMainWindow, theme: str) -> None:
    """Synchronise the native Windows title bar with the selected theme."""
    if platform.system() != "Windows":
        return
    value = ctypes.c_int(1 if _normalise_theme(theme) == "dark" else 0)
    for attribute in (20, 19):
        try:
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(window.winId()), attribute, ctypes.byref(value), 4)
            if result == 0:
                break
        except Exception:
            break


def _resource_path(relative_path: str) -> Path:
    """Return the asset location in a source checkout or PyInstaller bundle."""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def _app_icon() -> QIcon:
    """Load the app icon for the active platform's window manager."""
    return QIcon(str(_resource_path("assets/SystemTricorder.png")))


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

# Discrete Intel Arc model numbers — an Intel GPU without one of these is
# assumed to be the iGPU (Arrow Lake / Meteor Lake have an iGPU called
# "Intel Arc Graphics" with no model number).  On Windows this list is only
# the FALLBACK signal: get_wmi_gpu_list() primarily classifies Intel GPUs by
# dedicated VRAM from DXGI, which also covers future models.  Includes
# desktop (A3xx-A7xx, B5xx), mobile (M-suffixed models match their base
# number) and Arc Pro variants.
_ARC_DMODEL = ('a310', 'a350', 'a370', 'a380', 'a530', 'a550', 'a570',
               'a580', 'a730', 'a750', 'a770', 'b570', 'b580', 'b770',
               'pro a40', 'pro a50', 'pro a60', 'pro b50', 'pro b60')

# AMD iGPU PCI Device IDs — only these are real integrated GPUs
# (Ryzen 5800X3D has NO iGPU; Ryzen 7040/8040+ have RDNA2 iGPU)
_AMD_IGPU_DEV_IDS = ('15d8', '15d9', '164e', '164f', '1681', '1682',  # RDNA2/3 iGPUs
                     '1636', '1637', '1638', '1639', '163c', '163d')  # older APU iGPUs

# Drive tile colours
DRIVE_R_COLOR = "#00ffcc"   # read  — teal
DRIVE_W_COLOR = "#ffcc00"   # write — amber


# ═══════════════════════════════════════════════════════════════════════════════
# LINUX GPU MONITORING  (AMDGPU sysfs + optional NVML)
# ═══════════════════════════════════════════════════════════════════════════════

def _linux_norm_pci_slot(slot: str) -> str:
    """Normalize Linux PCI addresses for matching lspci, sysfs and NVML.

    lspci usually prints ``04:00.0`` while sysfs/DRM often uses
    ``0000:04:00.0`` and NVML may return ``00000000:04:00.0``.  Internally we
    keep the canonical sysfs shape: ``dddd:bb:dd.f``.
    """
    s = (slot or "").strip().lower()
    if not s:
        return ""
    m = re.match(r"^([0-9a-f]{8}):([0-9a-f]{2}:[0-9a-f]{2}\.[0-7])$", s)
    if m:
        return f"{m.group(1)[-4:]}:{m.group(2)}"
    if re.match(r"^[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$", s):
        return f"0000:{s}"
    if re.match(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$", s):
        return s
    return s


def _linux_strip_pci_id(text: str) -> str:
    """Remove a trailing PCI/device id bracket from lspci labels."""
    return re.sub(r"\s*\[[0-9a-fA-F]{4}(?::[0-9a-fA-F]{4})?\]\s*$", "", text).strip()


def _linux_lspci_gpu_records() -> Dict[str, dict]:
    """Return GPU records keyed by normalized PCI slot from ``lspci -mm -nn``.

    Important: ``lspci -mm`` is shell-quoted, not tab-separated.  The device
    model is field 3; the last field is often the subsystem device ("Device
    [2424]"), which produced useless names on Ubuntu before this parser.
    """
    records: Dict[str, dict] = {}
    try:
        out = subprocess.check_output(
            ["lspci", "-mm", "-nn"], text=True, timeout=5, stderr=subprocess.DEVNULL,
        )
        for line in out.strip().splitlines():
            try:
                parts = shlex.split(line)
            except ValueError:
                continue
            if len(parts) < 4:
                continue
            slot = _linux_norm_pci_slot(parts[0])
            cls = parts[1]
            if not cls.startswith(("VGA", "3D", "Display")):
                continue
            vendor = _linux_strip_pci_id(parts[2])
            device = _linux_strip_pci_id(parts[3])
            vendor_id = ""
            device_id = ""
            vm = re.search(r"\[([0-9a-fA-F]{4})\]", parts[2])
            dm = re.search(r"\[([0-9a-fA-F]{4})\]", parts[3])
            if vm:
                vendor_id = "0x" + vm.group(1).lower()
            if dm:
                device_id = "0x" + dm.group(1).lower()
            name = device or vendor or "GPU"
            records[slot] = {
                "slot": slot,
                "class": cls,
                "vendor": vendor,
                "vendor_id": vendor_id,
                "device": device,
                "device_id": device_id,
                "name": name,
            }
    except Exception as exc:
        logger.debug("lspci GPU lookup failed: %s", exc)
    return records


def _linux_lspci_gpu_names() -> Dict[str, str]:
    """Return ``{pci_slot: human_name}`` for all VGA/3D/Display controllers."""
    return {slot: rec.get("name", "GPU") for slot, rec in _linux_lspci_gpu_records().items()}


def _linux_detect_gpus() -> List[dict]:
    """Detect GPUs on Linux via DRM sysfs, with lspci as naming/fallback.

    Returns a list of dicts:
        ``{name, card_dir, vram_total_gb, vendor, pci_slot, is_igpu}``

    ``card_dir`` is empty for a PCI GPU that lspci sees but DRM/sysfs does not
    expose to the current session; the UI still gets a tile, but utilisation is
    reported as 0 until the driver exposes counters.
    """
    lspci_records = _linux_lspci_gpu_records()
    gpus: List[dict] = []
    drm = Path("/sys/class/drm")
    seen_slots: set = set()

    def _name_for(slot: str, fallback: str) -> str:
        rec = lspci_records.get(_linux_norm_pci_slot(slot))
        return str(rec.get("name") or fallback) if rec else fallback

    def _amd_is_igpu(name: str, device_id: str, vram_gb: float) -> bool:
        nl = name.lower()
        dev = device_id.lower().replace("0x", "")
        if vram_gb > 0:
            return False
        if dev in _AMD_IGPU_DEV_IDS:
            return True
        # Discrete AMD cards are usually RX/Pro/Navi/Instinct/FirePro and may
        # lack mem_info_vram_total on some kernels.  Do not hide them as iGPUs.
        if any(m in nl for m in (" radeon rx", "radeon rx", "radeon pro", "ai pro",
                                 "navi", "instinct", "firepro", "w6", "w7", "w8", "w9")):
            return False
        return any(m in nl for m in ("radeon graphics", "radeon(tm) graphics", "vega"))

    if drm.is_dir():
        for card in sorted(drm.glob("card[0-9]*")):
            if "-" in card.name:          # skip card0-DP-1 etc.
                continue
            dev = card / "device"
            if not dev.is_dir():
                continue

            vendor_file = dev / "vendor"
            if not vendor_file.is_file():
                continue
            try:
                vendor_hex = vendor_file.read_text().strip().lower()
            except Exception:
                continue

            pci_slot = ""
            device_id = ""
            try:
                uevent = (dev / "uevent").read_text()
                m = re.search(r"PCI_SLOT_NAME=([0-9a-fA-F:.]+)", uevent)
                if m:
                    pci_slot = _linux_norm_pci_slot(m.group(1))
                dm = re.search(r"PCI_ID=[0-9a-fA-F]{4}:([0-9a-fA-F]{4})", uevent)
                if dm:
                    device_id = "0x" + dm.group(1).lower()
            except Exception:
                pass
            if not pci_slot:
                with contextlib.suppress(Exception):
                    pci_slot = _linux_norm_pci_slot(dev.resolve().name)

            if pci_slot in seen_slots:
                continue
            if pci_slot:
                seen_slots.add(pci_slot)

            rec = lspci_records.get(pci_slot, {})
            if not device_id:
                device_id = str(rec.get("device_id", ""))

            # ── AMD ───────────────────────────────────────────────────────
            if vendor_hex == "0x1002":
                vram_file = dev / "mem_info_vram_total"
                vram_gb = 0.0
                if vram_file.is_file():
                    try:
                        vram_bytes = int(vram_file.read_text().strip())
                        if vram_bytes > 0:
                            vram_gb = vram_bytes / (1024 ** 3)
                    except Exception:
                        pass

                name = _name_for(pci_slot, "AMD GPU")
                is_igpu = _amd_is_igpu(name, device_id, vram_gb)

                busy_file = dev / "gpu_busy_percent"
                if not busy_file.is_file() and not is_igpu:
                    logger.warning(
                        "AMDGPU gpu_busy_percent not found at %s — falling back "
                        "to DRM fdinfo counters when available.", busy_file,
                    )
                elif busy_file.is_file():
                    try:
                        busy_file.read_text()
                    except PermissionError:
                        logger.warning(
                            "Cannot read %s — add your user to the 'video' group "
                            "and re-login:  sudo usermod -aG video $USER",
                            busy_file,
                        )

                gpus.append({
                    "name": name,
                    "card_dir": str(card),
                    "vram_total_gb": vram_gb,
                    "vendor": "amd",
                    "pci_slot": pci_slot,
                    "device_id": device_id,
                    "is_igpu": is_igpu,
                })

            # ── NVIDIA ────────────────────────────────────────────────────
            elif vendor_hex == "0x10de":
                gpus.append({
                    "name": _name_for(pci_slot, "NVIDIA GPU"),
                    "card_dir": str(card),
                    "vram_total_gb": 0.0,   # filled by NVML in _init_linux()
                    "vendor": "nvidia",
                    "pci_slot": pci_slot,
                    "device_id": device_id,
                    "is_igpu": False,
                })

            # ── Intel ─────────────────────────────────────────────────────
            elif vendor_hex == "0x8086":
                name = _name_for(pci_slot, "Intel GPU")
                is_arc_dgpu = any(m in name.lower() for m in _ARC_DMODEL)
                gpus.append({
                    "name": name,
                    "card_dir": str(card),
                    "vram_total_gb": 0.0,
                    "vendor": "intel",
                    "pci_slot": pci_slot,
                    "device_id": device_id,
                    "is_igpu": not is_arc_dgpu,
                })

    # lspci fallback: make every PCI GPU visible even if DRM/sysfs is missing.
    for slot, rec in lspci_records.items():
        if slot in seen_slots:
            continue
        vendor_id = str(rec.get("vendor_id", "")).lower()
        name = str(rec.get("name") or "GPU")
        if vendor_id == "0x1002":
            vendor = "amd"
            is_igpu = _amd_is_igpu(name, str(rec.get("device_id", "")), 0.0)
        elif vendor_id == "0x10de":
            vendor = "nvidia"
            is_igpu = False
        elif vendor_id == "0x8086":
            vendor = "intel"
            is_igpu = not any(m in name.lower() for m in _ARC_DMODEL)
        else:
            vendor = "gpu"
            is_igpu = False
        gpus.append({
            "name": name,
            "card_dir": "",
            "vram_total_gb": 0.0,
            "vendor": vendor,
            "pci_slot": slot,
            "device_id": str(rec.get("device_id", "")),
            "is_igpu": is_igpu,
        })

    gpus.sort(key=lambda g: (g.get("is_igpu", False), -float(g.get("vram_total_gb") or 0.0), g.get("pci_slot", "")))
    return gpus


def _linux_read_amd_gpu_busy(card_dir: str) -> float:
    """Read overall GPU utilisation (0–100 %) from AMDGPU sysfs."""
    try:
        return float(
            (Path(card_dir) / "device" / "gpu_busy_percent").read_text().strip()
        )
    except Exception:
        return 0.0


def _linux_read_amd_vram(card_dir: str) -> Tuple[float, float]:
    """Return ``(used_gb, total_gb)`` from AMDGPU sysfs."""
    base = Path(card_dir) / "device"
    try:
        used  = int((base / "mem_info_vram_used").read_text().strip())
        total = int((base / "mem_info_vram_total").read_text().strip())
        return (used / (1024 ** 3), total / (1024 ** 3))
    except Exception:
        return (0.0, 0.0)


def _linux_read_gpu_power_watts(card_dir: str) -> Optional[float]:
    """Read a DRM device's hwmon power sensor (sysfs reports microwatts)."""
    try:
        hwmon_root = Path(card_dir) / "device" / "hwmon"
        for hwmon in hwmon_root.glob("hwmon*"):
            for filename in ("power1_average", "power1_input"):
                path = hwmon / filename
                if path.is_file():
                    value = float(path.read_text().strip()) / 1_000_000.0
                    if math.isfinite(value) and value >= 0:
                        return value
    except Exception:
        pass
    return None


class _LinuxCpuPowerSampler:
    """Best-effort CPU package power from Linux powercap energy deltas."""

    def __init__(self) -> None:
        self._domains: List[Tuple[Path, Optional[int]]] = []
        self._previous: Dict[Path, Tuple[int, float]] = {}
        if platform.system() != "Linux":
            return
        powercap = Path("/sys/class/powercap")
        try:
            candidates = list(powercap.glob("intel-rapl:*")) + list(powercap.glob("amd-rapl:*"))
            for domain in candidates:
                # Keep package domains (intel-rapl:0), not subdomains such as
                # intel-rapl:0:0 which would double-count cores/DRAM.
                if domain.name.count(":") != 1:
                    continue
                name = (domain / "name").read_text(errors="ignore").strip().lower()
                energy_path = domain / "energy_uj"
                if "package" not in name or not energy_path.is_file():
                    continue
                max_range: Optional[int] = None
                with contextlib.suppress(Exception):
                    max_range = int((domain / "max_energy_range_uj").read_text().strip())
                self._domains.append((energy_path, max_range))
        except Exception:
            self._domains = []

    def sample(self) -> Optional[float]:
        if not self._domains:
            return None
        now = time.monotonic()
        total_watts = 0.0
        valid = 0
        for energy_path, max_range in self._domains:
            try:
                energy_uj = int(energy_path.read_text().strip())
                previous = self._previous.get(energy_path)
                self._previous[energy_path] = (energy_uj, now)
                if previous is None:
                    continue
                previous_uj, previous_t = previous
                delta = energy_uj - previous_uj
                if delta < 0 and max_range:
                    delta += max_range
                dt = now - previous_t
                if delta >= 0 and dt > 0:
                    total_watts += delta / 1_000_000.0 / dt
                    valid += 1
            except Exception:
                continue
        return total_watts if valid else None


def _read_nvml_gpu(handle) -> Tuple[float, float, float]:
    """Return ``(gpu_util, vram_used_gb, vram_total_gb)`` via NVML.

    Platform-neutral: used on Linux for every NVIDIA card and on Windows for
    TCC-mode cards that the WDDM perf counters cannot see.
    """
    try:
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)       # type: ignore
        mem  = pynvml.nvmlDeviceGetMemoryInfo(handle)             # type: ignore
        return (float(util.gpu), float(mem.used) / (1024 ** 3),
                float(mem.total) / (1024 ** 3))
    except Exception:
        return (0.0, 0.0, 0.0)


def _read_nvml_power_watts(handle) -> Optional[float]:
    """Return total NVIDIA board power in watts when NVML exposes it."""
    try:
        milliwatts = float(pynvml.nvmlDeviceGetPowerUsage(handle))  # type: ignore
        return max(milliwatts, 0.0) / 1000.0
    except Exception:
        return None


class _LinuxDrmFdinfoSampler:
    """Best-effort Linux GPU engine sampler using DRM fdinfo counters.

    nvtop uses DRM-facing sources instead of Ubuntu's generic monitor.  Modern
    AMD/Intel drivers expose per-client cumulative engine busy time in
    ``/proc/*/fdinfo/*``.  Sampling deltas gives engine utilisation without
    depending on a vendor GUI.  Missing permissions/counters simply return 0.
    """

    def __init__(self) -> None:
        self._prev_totals: Dict[Tuple[str, str], int] = {}
        self._prev_t: Optional[float] = None
        self._cache: Dict[str, dict] = {}
        self._cache_t: float = 0.0

    @staticmethod
    def _mem_to_gb(value: str, unit: str) -> float:
        n = float(value)
        u = (unit or "").lower()
        if u in ("kb", "kib"):
            n *= 1024
        elif u in ("mb", "mib"):
            n *= 1024 ** 2
        elif u in ("gb", "gib"):
            n *= 1024 ** 3
        return n / (1024 ** 3)

    @staticmethod
    def _engine_bucket(engine: str) -> Optional[str]:
        e = engine.lower()
        if any(x in e for x in ("render", "gfx", "3d")):
            return "3d"
        if "compute" in e or "cuda" in e:
            return "compute"
        if any(x in e for x in ("copy", "dma", "sdma", "blt")):
            return "c0"
        if any(x in e for x in ("video", "vcn", "uvd", "vce", "jpeg", "codec", "enc", "dec")):
            return "codec"
        return None

    def sample(self, min_interval: float = 0.20) -> Dict[str, dict]:
        now = time.time()
        if now - self._cache_t < min_interval:
            return self._cache

        totals: Dict[Tuple[str, str], int] = {}
        vram_used: Dict[str, float] = {}
        seen_clients: set = set()

        try:
            proc = Path("/proc")
            for pid_dir in proc.glob("[0-9]*"):
                fdinfo_dir = pid_dir / "fdinfo"
                if not fdinfo_dir.is_dir():
                    continue
                for fdinfo in fdinfo_dir.glob("[0-9]*"):
                    try:
                        text = fdinfo.read_text(errors="ignore")
                    except Exception:
                        continue
                    pm = re.search(r"^drm-pdev:\s*([^\s]+)", text, re.M)
                    if not pm:
                        continue
                    slot = _linux_norm_pci_slot(pm.group(1))
                    cm = re.search(r"^drm-client-id:\s*(\S+)", text, re.M)
                    client = cm.group(1) if cm else fdinfo.name
                    uniq = (slot, pid_dir.name, client)
                    if uniq in seen_clients:
                        continue
                    seen_clients.add(uniq)

                    for em in re.finditer(r"^drm-engine-([^:]+):\s*(\d+)\s*ns\b", text, re.M):
                        bucket = self._engine_bucket(em.group(1))
                        if bucket:
                            totals[(slot, bucket)] = totals.get((slot, bucket), 0) + int(em.group(2))
                    mm = re.search(r"^drm-memory-vram:\s*(\d+)\s*([KMGT]i?B|[KMGT]?B)?", text, re.M | re.I)
                    if mm:
                        vram_used[slot] = vram_used.get(slot, 0.0) + self._mem_to_gb(mm.group(1), mm.group(2) or "")
        except Exception as exc:
            logger.debug("DRM fdinfo sample failed: %s", exc)

        out: Dict[str, dict] = {}
        if self._prev_t is not None:
            dt = max(now - self._prev_t, 0.001)
            for (slot, bucket), total_ns in totals.items():
                prev = self._prev_totals.get((slot, bucket), total_ns)
                util = max(0.0, (total_ns - prev) / (dt * 1_000_000_000.0) * 100.0)
                d = out.setdefault(slot, {"3d": 0.0, "compute": 0.0, "c0": 0.0, "c1": 0.0, "codec": 0.0, "vram_used_gb": 0.0})
                d[bucket] = min(d.get(bucket, 0.0) + util, 100.0)
        for slot, used in vram_used.items():
            d = out.setdefault(slot, {"3d": 0.0, "compute": 0.0, "c0": 0.0, "c1": 0.0, "codec": 0.0, "vram_used_gb": 0.0})
            d["vram_used_gb"] = max(d.get("vram_used_gb", 0.0), used)

        self._prev_totals = totals
        self._prev_t = now
        self._cache = out
        self._cache_t = now
        return out

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


def get_dxgi_adapter_map() -> Dict[str, Tuple[str, float, str]]:
    """
    Enumerate GPU adapters via DXGI and return a mapping:
        luid_string -> (device_id_hex, dedicated_vram_gb, description)

    The LUID string is formatted to match the LUID that appears verbatim in the
    Windows GPU performance-counter names, e.g. "luid_0x00000000_0x0001c2e3".
    DXGI is the authoritative source here: every adapter's kernel LUID is the
    same one the perf counters use, and DXGI_ADAPTER_DESC also carries the PCI
    DeviceId and the human-readable Description -- so this lets us bind a
    perf-counter LUID directly to a physical GPU (and tell the iGPU apart from
    dGPUs by name) instead of guessing by VRAM size.

    Returns {} on any failure (non-Windows, no DXGI, etc.); callers degrade
    gracefully when the map is empty.
    """
    result: Dict[str, Tuple[str, float, str]] = {}
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
                    name     = (desc.Description or "").strip()
                    result[luid_str] = (dev_id, vram_gb, name)
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


class _PdhArraySampler:
    """Small wildcard-array PDH reader shared by fast Windows sensors."""

    _PDH_FMT_DOUBLE = 0x00000200
    _PDH_MORE_DATA = 0x800007D2

    def __init__(self, path: str, label: str, *, english_path: bool = False,
                 warn_on_failure: bool = True) -> None:
        self._path = path
        self._label = label
        self._ok = False
        self._query: Optional[ctypes.c_void_p] = None
        self._counter: Optional[ctypes.c_void_p] = None
        self._buffer = None
        self._buffer_size = 0
        try:
            self._pdh = ctypes.WinDLL("pdh")
            query = ctypes.c_void_p()
            if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
                raise RuntimeError("PdhOpenQueryW failed")
            counter = ctypes.c_void_p()
            add_counter = (self._pdh.PdhAddEnglishCounterW
                           if english_path else self._pdh.PdhAddCounterW)
            status = int(add_counter(query, path, 0, ctypes.byref(counter))) & 0xFFFFFFFF
            if status != 0:
                self._pdh.PdhCloseQuery(query)
                raise RuntimeError(f"counter add failed with 0x{status:08X}")
            self._query = query
            self._counter = counter
            # Prime rate/delta counters.  The first formatted sample otherwise
            # commonly reports zero even when the hardware is already active.
            self._pdh.PdhCollectQueryData(query)
            self._ok = True
        except Exception as exc:
            log = logger.warning if warn_on_failure else logger.info
            log("%s unavailable: %s", label, exc)
            self._ok = False

    @property
    def ok(self) -> bool:
        return self._ok

    def sample(self) -> List[Tuple[str, float]]:
        if not self._ok or self._query is None or self._counter is None:
            return []
        try:
            collect_status = int(self._pdh.PdhCollectQueryData(self._query)) & 0xFFFFFFFF
            if collect_status != 0:
                return []
            size = wintypes.DWORD(0)
            count = wintypes.DWORD(0)
            self._pdh.PdhGetFormattedCounterArrayW(
                self._counter, self._PDH_FMT_DOUBLE,
                ctypes.byref(size), ctypes.byref(count), None)
            # Dynamic per-process GPU instances can appear between the sizing
            # and data calls.  PDH then updates ``size`` and returns MORE_DATA;
            # retry rather than discarding the complete telemetry frame.
            for _ in range(3):
                if size.value == 0:
                    return []
                if self._buffer is None or size.value > self._buffer_size:
                    self._buffer_size = int(size.value)
                    self._buffer = (ctypes.c_ubyte * self._buffer_size)()
                count = wintypes.DWORD(0)
                status = int(self._pdh.PdhGetFormattedCounterArrayW(
                    self._counter, self._PDH_FMT_DOUBLE,
                    ctypes.byref(size), ctypes.byref(count), self._buffer)) & 0xFFFFFFFF
                if status == 0:
                    arr = (_PdhCounterItem * count.value).from_buffer(self._buffer)
                    return [(str(item.szName).lower(), float(item.FmtValue.doubleValue))
                            for item in arr
                            if item.szName and item.FmtValue.CStatus in (0, 1)]
                if status != self._PDH_MORE_DATA:
                    return []
            return []
        except Exception as exc:
            logger.debug("%s sample failed: %s", self._label, exc)
            return []

    def close(self) -> None:
        if self._query is not None:
            with contextlib.suppress(Exception):
                self._pdh.PdhCloseQuery(self._query)
        self._ok = False
        self._query = None
        self._counter = None
        self._buffer = None
        self._buffer_size = 0


class _PdhGpuSampler(_PdhArraySampler):
    """Task-Manager-grade WDDM engine split without WMI/perflib caching.

    ``GPU Engine`` object/counter names are invariant English even on localized
    Windows installations.  ADLX augments this per-engine stream with AMD's
    authoritative overall utilization because WDDM can undersample long RDNA4
    compute dispatches.
    """

    def __init__(self) -> None:
        super().__init__(
            r"\GPU Engine(*)\Utilization Percentage", "PDH GPU sampler")


class _PdhGpuMemorySampler(_PdhArraySampler):
    """Read dedicated GPU memory without a blocking WMI round trip."""

    def __init__(self) -> None:
        super().__init__(
            r"\GPU Adapter Memory(*)\Dedicated Usage", "PDH GPU memory sampler",
            warn_on_failure=False)


class _PdhEnergySampler(_PdhArraySampler):
    """Read Windows Energy Meter package power via a locale-neutral path."""

    def __init__(self) -> None:
        # Unlike GPU counters, Energy Meter is localized ("Energiemessung" on
        # de-DE).  PdhAddEnglishCounterW maps this canonical path correctly.
        super().__init__(r"\Energy Meter(*)\Power", "PDH CPU energy sampler",
                         english_path=True, warn_on_failure=False)


def _cpu_package_power_from_pdh(rows: List[Tuple[str, float]]) -> Optional[float]:
    """Convert Windows Energy Meter package/socket power to watts.

    Windows exposes Intel package counters as ``RAPL_PackageN_PKG``.  AMD
    firmware uses several names across Ryzen generations (for example
    ``CPU Power``, ``Socket Power`` or ``Current Socket Power``), and some Zen
    systems expose only per-core RAPL channels.  Prefer exactly one whole-
    package family and never add PP0/DRAM or core rows to a package value.
    """
    normalized: List[Tuple[str, float]] = []
    for raw_name, raw_value in rows:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        name = re.sub(r"#\d+$", "", str(raw_name).strip().lower())
        name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
        normalized.append((name, value))

    # Each entry is a priority tier.  Multiple rows in one tier represent
    # multiple sockets; aliases from lower tiers must not be added as they can
    # describe the same physical package and would double-count it.
    package_tiers = (
        lambda name: re.fullmatch(r"rapl_package\d+_pkg", name) is not None,
        lambda name: name == "current_socket_power",
        lambda name: name == "current_socket_energy",
        lambda name: name == "socket_power",
        lambda name: name == "cpu_package_power",
        lambda name: name == "cpu_power",
        lambda name: name == "apu_power",
        lambda name: name == "apu_energy",
    )
    for matches in package_tiers:
        package_mw = [value for name, value in normalized if matches(name)]
        if package_mw:
            return sum(package_mw) / 1000.0

    # Several AMD Ryzen implementations publish one CORE row per physical core
    # but no usable package row.  Their sum is a truthful CPU-core fallback;
    # keep it separate from PKG/PP0/DRAM so no domain is counted twice.
    core_mw = [
        value for name, value in normalized
        if re.fullmatch(r"rapl_package\d+_core\d+_core", name)
    ]
    if core_mw:
        return sum(core_mw) / 1000.0
    return None


class _AdlxGpuSampler:
    """Read AMD's driver-native overall load, board/GPU power and VRAM metrics.

    WDDM's ``GPU Engine`` counters are useful for the 3D/Compute/Copy/Codec
    split, but some long RDNA4 compute dispatches only appear there as a brief
    100 % pulse every few seconds.  AMD Software uses ADLX instead and reports
    the GPU as continuously busy.  This small ctypes wrapper calls the public
    ADLX C ABI from the driver-installed ``amdadlx64.dll`` directly, avoiding
    both the obsolete ADL path and third-party binary Python bindings.

    ADLX interfaces are reference counted.  Every interface obtained here is
    released before ``ADLXTerminate``; getting that order wrong can crash while
    the DLL unloads.  The sampler is created, sampled and closed in the monitor
    thread so all driver calls stay off the Qt UI thread.
    """

    # ADLX SDK V1.4 full version (1.4.0.110).  ADLX interfaces are ABI-locked,
    # and newer drivers (including ADLX 1.5 on RDNA4) accept this client version.
    _CLIENT_VERSION = (1 << 48) | (4 << 32) | 110
    _SUCCESS = (0, 1, 2)  # ADLX_OK / ALREADY_ENABLED / ALREADY_INITIALIZED

    def __init__(self) -> None:
        self._dll = None
        self._terminate = None
        self._initialized = False
        self._started = False
        self._ok = False
        self._system = ctypes.c_void_p()
        self._perf = ctypes.c_void_p()
        self._gpus: List[dict] = []

        if platform.system() != "Windows":
            return
        try:
            dll_name = "amdadlx64.dll" if ctypes.sizeof(ctypes.c_void_p) == 8 else "amdadlx32.dll"
            system_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / (
                "System32" if ctypes.sizeof(ctypes.c_void_p) == 8 else "SysWOW64"
            )
            dll_path = system_dir / dll_name
            # An absolute System32 path prevents cwd-based DLL hijacking.  Some
            # driver packages expose ADLX only through the normal secure loader
            # path, so retain a name-only fallback when the file is not present.
            self._dll = ctypes.CDLL(str(dll_path if dll_path.is_file() else dll_name))

            init2 = getattr(self._dll, "ADLXInitialize2", None)
            mapping = ctypes.c_void_p()
            if init2 is not None:
                init2.argtypes = [ctypes.c_uint64, ctypes.POINTER(ctypes.c_void_p),
                                  ctypes.POINTER(ctypes.c_void_p)]
                init2.restype = ctypes.c_int32
                result = int(init2(self._CLIENT_VERSION, ctypes.byref(self._system),
                                   ctypes.byref(mapping)))
            else:
                init1 = self._dll.ADLXInitialize
                init1.argtypes = [ctypes.c_uint64, ctypes.POINTER(ctypes.c_void_p)]
                init1.restype = ctypes.c_int32
                result = int(init1(self._CLIENT_VERSION, ctypes.byref(self._system)))
            if result not in self._SUCCESS or not self._system:
                raise RuntimeError(f"ADLXInitialize failed with result {result}")
            self._initialized = True

            self._terminate = self._dll.ADLXTerminate
            self._terminate.argtypes = []
            self._terminate.restype = ctypes.c_int32

            gpu_list = ctypes.c_void_p()
            if self._call(self._system, 1, ctypes.c_int32,
                          [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(gpu_list)) != 0:
                raise RuntimeError("ADLX GetGPUs failed")
            try:
                begin = int(self._call(gpu_list, 5, ctypes.c_uint32, []))
                end = int(self._call(gpu_list, 6, ctypes.c_uint32, []))
                for index in range(begin, end):
                    gpu = ctypes.c_void_p()
                    result = self._call(
                        gpu_list, 11, ctypes.c_int32,
                        [ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
                        index, ctypes.byref(gpu),
                    )
                    if result != 0 or not gpu:
                        continue
                    self._gpus.append({
                        "ptr": gpu,
                        "name": self._gpu_string(gpu, 7),
                        "device_id": self._normalise_device_id(self._gpu_string(gpu, 14)),
                    })
            finally:
                self._release(gpu_list)

            if not self._gpus:
                raise RuntimeError("ADLX returned no AMD GPUs")
            if self._call(
                self._system, 9, ctypes.c_int32,
                [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(self._perf),
            ) != 0 or not self._perf:
                raise RuntimeError("ADLX GetPerformanceMonitoringServices failed")

            # 250 ms is the minimum advertised by current ADLX drivers.  A
            # rejected interval is harmless: the driver's existing interval is
            # retained and GetCurrentGPUMetrics still returns valid samples.
            self._call(self._perf, 4, ctypes.c_int32, [ctypes.c_int32], 250)
            result = self._call(self._perf, 11, ctypes.c_int32, [])
            if result not in self._SUCCESS:
                raise RuntimeError(f"ADLX tracking start failed with result {result}")
            self._started = True

            for gpu_info in self._gpus:
                support = ctypes.c_void_p()
                result = self._call(
                    self._perf, 21, ctypes.c_int32,
                    [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                    gpu_info["ptr"], ctypes.byref(support),
                )
                if result == 0 and support:
                    try:
                        gpu_info["usage_supported"] = self._support_flag(support, 3)
                        gpu_info["gpu_power_supported"] = self._support_flag(support, 8)
                        gpu_info["board_power_supported"] = self._support_flag(support, 9)
                        gpu_info["vram_supported"] = self._support_flag(support, 11)
                    finally:
                        self._release(support)
                else:
                    gpu_info.update({
                        "usage_supported": False,
                        "gpu_power_supported": False,
                        "board_power_supported": False,
                        "vram_supported": False,
                    })
            self._ok = any(
                g.get("usage_supported") or g.get("board_power_supported")
                or g.get("gpu_power_supported") or g.get("vram_supported")
                for g in self._gpus
            )
            if self._ok:
                logger.info("ADLX telemetry active for %d AMD GPU(s)", len(self._gpus))
        except Exception as exc:
            logger.warning("ADLX GPU telemetry unavailable; using WDDM counters: %s", exc)
            self.close()

    @staticmethod
    def _method(obj: ctypes.c_void_p, index: int, restype, argtypes):
        if not obj:
            raise RuntimeError("null ADLX interface")
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0]
        address = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[index]
        if not address:
            raise RuntimeError(f"null ADLX vtable entry {index}")
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(address)  # type: ignore[attr-defined]

    @classmethod
    def _call(cls, obj: ctypes.c_void_p, index: int, restype, argtypes, *args):
        return cls._method(obj, index, restype, argtypes)(obj, *args)

    @classmethod
    def _release(cls, obj: ctypes.c_void_p) -> None:
        if obj:
            cls._call(obj, 1, ctypes.c_long, [])

    @classmethod
    def _gpu_string(cls, gpu: ctypes.c_void_p, index: int) -> str:
        value = ctypes.c_char_p()
        result = cls._call(
            gpu, index, ctypes.c_int32,
            [ctypes.POINTER(ctypes.c_char_p)], ctypes.byref(value),
        )
        if result != 0 or not value.value:
            return ""
        return value.value.decode("utf-8", errors="replace").strip()

    @classmethod
    def _support_flag(cls, support: ctypes.c_void_p, index: int) -> bool:
        value = ctypes.c_uint8()
        result = cls._call(
            support, index, ctypes.c_int32,
            [ctypes.POINTER(ctypes.c_uint8)], ctypes.byref(value),
        )
        return result == 0 and bool(value.value)

    @staticmethod
    def _normalise_device_id(device_id: str) -> str:
        value = (device_id or "").strip().lower().removeprefix("0x")
        return f"0x{value.upper()}" if value else ""

    @classmethod
    def _metric_double(cls, metrics: ctypes.c_void_p, index: int) -> Optional[float]:
        value = ctypes.c_double()
        result = cls._call(
            metrics, index, ctypes.c_int32,
            [ctypes.POINTER(ctypes.c_double)], ctypes.byref(value),
        )
        return float(value.value) if result == 0 and math.isfinite(value.value) else None

    @classmethod
    def _metric_int(cls, metrics: ctypes.c_void_p, index: int) -> Optional[int]:
        value = ctypes.c_int32()
        result = cls._call(
            metrics, index, ctypes.c_int32,
            [ctypes.POINTER(ctypes.c_int32)], ctypes.byref(value),
        )
        return int(value.value) if result == 0 else None

    @property
    def ok(self) -> bool:
        return self._ok

    def sample(self) -> List[dict]:
        """Return one metrics dictionary per ADLX GPU in driver order."""
        if not self._ok or not self._perf:
            return []
        samples: List[dict] = []
        for gpu_info in self._gpus:
            metrics = ctypes.c_void_p()
            try:
                result = self._call(
                    self._perf, 18, ctypes.c_int32,
                    [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                    gpu_info["ptr"], ctypes.byref(metrics),
                )
                if result != 0 or not metrics:
                    continue
                usage = (self._metric_double(metrics, 4)
                         if gpu_info.get("usage_supported") else None)
                if gpu_info.get("board_power_supported"):
                    power = self._metric_double(metrics, 10)
                elif gpu_info.get("gpu_power_supported"):
                    power = self._metric_double(metrics, 9)
                else:
                    power = None
                vram_mb = (self._metric_int(metrics, 12)
                           if gpu_info.get("vram_supported") else None)
                samples.append({
                    "name": gpu_info["name"],
                    "device_id": gpu_info["device_id"],
                    "usage_percent": min(max(usage, 0.0), 100.0) if usage is not None else None,
                    "power_watts": max(power, 0.0) if power is not None else None,
                    "vram_used_gb": max(vram_mb, 0) / 1024.0 if vram_mb is not None else None,
                })
            except Exception as exc:
                logger.debug("ADLX sample failed for %s: %s", gpu_info.get("name"), exc)
            finally:
                if metrics:
                    with contextlib.suppress(Exception):
                        self._release(metrics)
        return samples

    def close(self) -> None:
        self._ok = False
        if self._started and self._perf:
            with contextlib.suppress(Exception):
                self._call(self._perf, 12, ctypes.c_int32, [])
        self._started = False
        for gpu_info in self._gpus:
            with contextlib.suppress(Exception):
                self._release(gpu_info["ptr"])  # type: ignore[arg-type]
        self._gpus.clear()
        if self._perf:
            with contextlib.suppress(Exception):
                self._release(self._perf)
        self._perf = ctypes.c_void_p()
        if self._initialized and self._terminate is not None:
            with contextlib.suppress(Exception):
                self._terminate()
        self._initialized = False
        self._system = ctypes.c_void_p()
        self._terminate = None
        self._dll = None


def get_wmi_gpu_list() -> List[Tuple[str, bool, float, str]]:
    """
    Returns (name, is_igpu, vram_gb, pnp_device_id) for all real GPUs via WMI.
    Sorted: dGPUs first (desc VRAM), then iGPUs.

    iGPU detection rules
    --------------------
    Intel: primary signal is dedicated VRAM from DXGI (matched via PCI DEV id):
           >= 2 GB dedicated VRAM = discrete Arc.  iGPUs only carve out
           ~128 MB dedicated memory, every Arc dGPU ships with >= 4 GB, and the
           VRAM check keeps working for model numbers that postdate this code.
           The _ARC_DMODEL name list remains as fallback when DXGI is
           unavailable.  This classifies "Intel(R) Arc(TM) Graphics"
           (Arrow Lake / Meteor Lake integrated) as iGPU.
    AMD:   traditional integrated markers (Radeon(TM) Graphics, Vega) without RX.
    """
    result: List[Tuple[str, bool, float, str]] = []
    if not WMI_AVAILABLE:
        return result
    # Dedicated VRAM per PCI device id (lower-case hex, no 0x) from DXGI.
    dxgi_vram_by_dev: Dict[str, float] = {}
    for _devid, _vram, _ in get_dxgi_adapter_map().values():
        _key = _devid.lower().replace("0x", "")
        dxgi_vram_by_dev[_key] = max(dxgi_vram_by_dev.get(_key, 0.0), _vram)
    com_initialized = False
    wmi = None
    c = None
    try:
        pythoncom.CoInitialize()                                    # type: ignore
        com_initialized = True
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
                                     '164c', '164d',  # discrete Radeon models
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
                    is_intel_dgpu = False
                    if 'intel' in nl:
                        dev_match = re.search(r'dev_([0-9a-f]{4})', pnp_lower)
                        ded_vram = dxgi_vram_by_dev.get(dev_match.group(1), 0.0) if dev_match else 0.0
                        # >= 2 GB dedicated VRAM = discrete Arc (model-number
                        # list only as fallback when DXGI gave no data).
                        is_intel_dgpu = ded_vram >= 2.0 or any(m in nl for m in _ARC_DMODEL)
                    is_igpu = (
                        ('intel' in nl and not is_intel_dgpu) or
                        # AMD iGPU: integrated Radeon (Vega/RDNA-integrated, no RX prefix)
                        ('amd' in nl and ('radeon(tm) graphics' in nl or 'vega' in nl) and 'rx ' not in nl)
                    )

                vram = float(c.AdapterRAM or 0) / (1024 ** 3)
                result.append((name, is_igpu, vram, pnp_id))
            except Exception:
                pass   # skip problematic WMI rows without aborting the loop
    except Exception:
        pass
    finally:
        # Release COM proxies before tearing down this apartment; otherwise
        # pywin32 may attempt IUnknown::Release after CoUninitialize.
        c = None
        wmi = None
        if com_initialized:
            with contextlib.suppress(Exception):
                pythoncom.CoUninitialize()                          # type: ignore
    result.sort(key=lambda x: (int(x[1]), -x[2]))
    return result


def _nvml_tcc_gpus() -> List[Tuple[str, float, str]]:
    """Return (name, vram_gb, "nvml:<index>") for NVIDIA GPUs in TCC mode.

    Compute/render servers often run their GPUs in TCC instead of WDDM.  TCC
    devices are invisible to DXGI and to the "GPU Engine" performance
    counters (both WDDM-only), so the regular Windows pipeline never sees
    them — NVML is the only data source.  The "nvml:<index>" device key tells
    the monitor loop to read these GPUs via NVML instead of LUID binding.
    """
    result: List[Tuple[str, float, str]] = []
    if not NVML_AVAILABLE:
        return result
    tcc_model = getattr(pynvml, "NVML_DRIVER_WDM", 1)   # WDM == TCC in NVML terms
    try:
        for i in range(pynvml.nvmlDeviceGetCount()):                # type: ignore
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)       # type: ignore
                if pynvml.nvmlDeviceGetCurrentDriverModel(handle) != tcc_model:  # type: ignore
                    continue
                raw = pynvml.nvmlDeviceGetName(handle)              # type: ignore
                name = raw.decode() if isinstance(raw, bytes) else str(raw)
                vram = pynvml.nvmlDeviceGetMemoryInfo(handle).total / (1024 ** 3)  # type: ignore
                result.append((name or "NVIDIA GPU", float(math.ceil(vram)), f"nvml:{i}"))
            except Exception:
                continue
    except Exception as exc:
        logger.debug("NVML TCC enumeration failed: %s", exc)
    return result


def _windows_detect_dgpus() -> Tuple[List[Tuple[str, float, str]], bool]:
    """Single source of truth for the Windows dGPU list.

    Returns ``(dgpus, has_igpu)`` where each dGPU is ``(name, vram_gb,
    dev_key)``; ``dev_key`` is either a PCI device id ("0x1E02", bound to a
    perf-counter LUID at runtime) or "nvml:<index>" for TCC-mode NVIDIA GPUs.
    Used by BOTH the tile builder (_analyze_hardware) and the monitor thread
    (_init_windows) so the tiles and the metrics stream always agree.
    """
    reg_vrams = get_registry_gpu_vrams()
    wmi_gpus  = get_wmi_gpu_list()
    dgpu_wmi  = [(n, v, p) for n, ig, v, p in wmi_gpus if not ig]
    has_igpu  = any(ig for _, ig, _, _ in wmi_gpus)

    dgpus: List[Tuple[str, float, str]] = []
    for i, (name, wv, pnp_id) in enumerate(dgpu_wmi):
        vram = reg_vrams[i] if i < len(reg_vrams) else (math.ceil(wv) if wv >= 1.0 else 8.0)
        dev_id = ""
        dev_match = re.search(r'DEV_([0-9A-Fa-f]{4})', pnp_id)
        if dev_match:
            dev_id = "0x" + dev_match.group(1).upper()
        dgpus.append((name, float(vram), dev_id))

    dgpus.extend(_nvml_tcc_gpus())

    if not dgpus:
        dgpus = [("GPU", reg_vrams[0], "")]
    return dgpus, has_igpu


def short_gpu_name(name: str) -> str:
    """Shortens a GPU name to ~22 chars for compact display."""
    for kw in ('RTX', 'RX ', 'GTX', 'RX', 'Arc', 'Radeon', 'NVIDIA', 'AMD', 'Intel'):
        idx = name.find(kw)
        if idx != -1:
            return name[idx:idx + 22].strip()
    return name[:22].strip()


def _linux_base_block_name(name: str) -> str:
    """Return the physical block-device name for a Linux disk or partition."""
    n = Path(name).name
    for pat in (r"^(nvme\d+n\d+)", r"^(mmcblk\d+)", r"^(vd[a-z]+)", r"^(xvd[a-z]+)", r"^(sd[a-z]+)", r"^(hd[a-z]+)"):
        m = re.match(pat, n)
        if m:
            return m.group(1)
    return n


def _linux_is_partition_key(key: str) -> bool:
    """True if a psutil disk key is a partition, false for whole disks."""
    if key.startswith(("loop", "ram", "zram", "sr")):
        return True
    sys_part = Path("/sys/class/block") / key / "partition"
    if sys_part.exists():
        return True
    return bool(re.search(r"(?:nvme\d+n\d+p\d+|mmcblk\d+p\d+|[svxh]d[a-z]+\d+)$", key))


def _fmt_linux_size(num_bytes: int) -> str:
    try:
        tb = num_bytes / (1000 ** 4)
        if tb >= 0.95:
            return f"{tb:.1f} TB"
        gb = num_bytes / (1000 ** 3)
        if gb >= 1:
            return f"{gb:.0f} GB"
    except Exception:
        pass
    return ""


def _clean_linux_model(model: str) -> str:
    s = re.sub(r"\s+", " ", (model or "").strip())
    s = re.sub(r"\b(NVMe SSD Controller|Non-Volatile memory controller|Device)\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -_")
    return s[:28].strip() or "Disk"


def _linux_lsblk_disks() -> Dict[str, dict]:
    """Return lsblk metadata for physical disks keyed by KNAME/NAME."""
    disks: Dict[str, dict] = {}
    try:
        out = subprocess.check_output(
            ["lsblk", "-J", "-b", "-o", "NAME,KNAME,TYPE,PKNAME,MOUNTPOINTS,LABEL,MODEL,SIZE,ROTA,TRAN,FSTYPE"],
            text=True, timeout=5, stderr=subprocess.DEVNULL,
        )
        data = json.loads(out)

        def _mounts(v) -> List[str]:
            if not v:
                return []
            if isinstance(v, list):
                return [str(x) for x in v if x]
            return [x for x in str(v).splitlines() if x]

        def _walk(node: dict) -> Tuple[List[str], List[str]]:
            labels: List[str] = []
            mounts = _mounts(node.get("mountpoints"))
            lab = str(node.get("label") or "").strip()
            if lab:
                labels.append(lab)
            for ch in node.get("children") or []:
                cl, cm = _walk(ch)
                labels.extend(cl)
                mounts.extend(cm)
            # keep order but remove duplicates
            labels = list(dict.fromkeys(labels))
            mounts = list(dict.fromkeys(mounts))
            return labels, mounts

        for node in data.get("blockdevices") or []:
            if node.get("type") != "disk":
                continue
            kname = str(node.get("kname") or node.get("name") or "").strip()
            name = str(node.get("name") or kname).strip()
            if not kname:
                continue
            labels, mounts = _walk(node)
            info = dict(node)
            info["labels"] = labels
            info["mounts"] = mounts
            disks[kname] = info
            disks[name] = info
    except Exception as exc:
        logger.debug("lsblk disk lookup failed: %s", exc)
    return disks


def _linux_drive_label(key: str, info: Optional[dict], fallback_mount: str, data_letter_ord: int) -> str:
    mounts = list(info.get("mounts") or []) if info else ([fallback_mount] if fallback_mount else [])
    labels = [str(x).strip() for x in (info.get("labels") or []) if str(x).strip()] if info else []
    is_system = any(m in ("/", "/usr", "/home", "/home/dawasteh", "/etc", "/bin", "/sbin", "/lib", "/lib64")
                    or m.startswith("/home/") for m in mounts)
    if is_system:
        return "C: Ubuntu"

    media_label = ""
    for m in mounts:
        parts = Path(m).parts
        if len(parts) >= 4 and parts[1] == "run" and parts[2] == "media":
            media_label = parts[4] if len(parts) >= 5 else Path(m).name
            break
        if len(parts) >= 3 and parts[1] == "media":
            media_label = parts[3] if len(parts) >= 4 else Path(m).name
            break
    label = labels[0] if labels else media_label
    if label:
        letter = chr(data_letter_ord)
        return f"{letter}: {label[:24]}"

    model = _clean_linux_model(str(info.get("model") or "")) if info else ""
    size = _fmt_linux_size(int(info.get("size") or 0)) if info else ""
    if model and size:
        return f"{model} {size}"
    if model:
        return model
    return (key.replace('nvme', 'NVMe ').replace('mmcblk', 'SD ').replace('sd', 'Disk ')).strip()


def build_drive_info() -> List[Tuple[str, str]]:
    """
    Returns [(psutil_disk_key, display_label), ...] for all physical drives.

    Windows keeps the original WMI drive-letter mapping.  Linux now uses lsblk
    and sysfs-style partition checks so only whole physical disks become tiles;
    labels/mounts are converted to compact Windows-like names (``C: Ubuntu``,
    ``D: DataLabel``) instead of ambiguous per-partition names.
    """
    result: List[Tuple[str, str]] = []
    try:
        io = psutil.disk_io_counters(perdisk=True)
        if not io:
            return []

        # ── Windows drive letter mapping ──────────────────────────────────────
        letter_map: Dict[str, str] = {}
        if platform.system() == 'Windows' and WMI_AVAILABLE:
            com_initialized = False
            wmi = None
            row = None
            try:
                pythoncom.CoInitialize()                            # type: ignore
                com_initialized = True
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
            finally:
                row = None
                wmi = None
                if com_initialized:
                    with contextlib.suppress(Exception):
                        pythoncom.CoUninitialize()                  # type: ignore

        linux_disk_mount: Dict[str, str] = {}
        linux_lsblk = _linux_lsblk_disks() if platform.system() == 'Linux' else {}
        if platform.system() == 'Linux':
            try:
                for p in psutil.disk_partitions(all=False):
                    base_dev = _linux_base_block_name(p.device)
                    linux_disk_mount.setdefault(base_dev, p.mountpoint)
            except Exception:
                pass

        next_data_letter = ord('D')
        for key in sorted(io.keys()):
            if platform.system() == 'Linux':
                if _linux_is_partition_key(key):
                    continue
                if linux_lsblk and key not in linux_lsblk:
                    # psutil can expose device-mapper/loop/partition counters;
                    # keep only real lsblk disks when metadata is available.
                    continue
                label = _linux_drive_label(
                    key, linux_lsblk.get(key), linux_disk_mount.get(key, ""), next_data_letter,
                )
                if not label.startswith("C:"):
                    next_data_letter += 1
            else:
                # Windows path (original logic)
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
    gpu_3d_percent:      float = 0.0
    gpu_compute_percent: float = 0.0
    gpu_copy0_percent:   float = 0.0
    gpu_copy1_percent:   float = 0.0
    gpu_codec_percent:   float = 0.0
    gpu_vram_used_gb:    float = 0.0
    gpu_vram_total_gb:   float = 8.0
    gpu_total_percent:   float = 0.0
    gpu_power_watts:     Optional[float] = None


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
    disk_read_mbps:    float   # aggregate (kept for compat)
    disk_write_mbps:   float   # aggregate
    drives:            List[DriveMetrics]   # per-physical-drive
    timestamp:         datetime
    cpu_power_watts:   Optional[float] = None


# ═══════════════════════════════════════════════════════════════════════════════
# HARDWARE MONITOR THREAD  (30 FPS)
# ═══════════════════════════════════════════════════════════════════════════════

class HardwareMonitorThread(QThread):
    # Metrics are handed to the UI via a lock-protected latest-value slot
    # instead of a queued pyqtSignal: with a queued signal, every frame the UI
    # thread can't process within 33 ms piles up in the event queue, so on
    # load spikes the dashboard falls behind and replays stale frames
    # (visible stutter).  With the slot, a slow UI simply skips frames.

    def __init__(self, drive_info: List[Tuple[str, str]], parent=None,
                 dgpu_info: Optional[List[Tuple[str, float, str]]] = None) -> None:
        super().__init__(parent)
        self._running         = False
        self._latest:      Optional[SystemMetrics] = None
        self._latest_lock = threading.Lock()
        # Diagnostics: consecutive loop failures / empty GPU samples.  Logged
        # as (throttled) WARNINGs so field reports actually contain evidence
        # in ~/.tricorder.log instead of silent debug-level drops.
        self._loop_err_count   = 0
        self._empty_rows_count = 0
        self._sensor_error_last: Dict[str, float] = {}
        self._last_slow_loop_warning = 0.0
        self._drive_info      = drive_info
        self._provided_dgpu_info = list(dgpu_info) if dgpu_info is not None else None
        self._platform        = platform.system()
        self._is_linux        = self._platform == "Linux"
        self._is_windows      = self._platform == "Windows"

        # Shared fields (both platforms)
        self._dgpu_info:      List[Tuple[str, float, str]] = []
        self._luid_order:     List[str]       = []
        self._luid_vram:      Dict[str, float] = {}
        self._luid_device_id: Dict[str, str]  = {}
        self._luid_device_map: Dict[str, Tuple[str, float, str]] = {}
        self._igpu_luid:      Optional[str]   = None
        self._pdh:            Optional[_PdhGpuSampler] = None
        self._pdh_memory:     Optional[_PdhGpuMemorySampler] = None
        self._pdh_energy:     Optional[_PdhEnergySampler] = None
        self._adlx:           Optional[_AdlxGpuSampler] = None
        self._adlx_cache:     List[dict] = []
        self._last_adlx_query = 0.0
        self._last_adlx_success = 0.0
        self._next_adlx_retry = 0.0
        self._last_memory_query = 0.0
        self._last_memory_success = 0.0
        self._last_power_query = 0.0
        self._last_power_success = 0.0
        self._cpu_power_watts: Optional[float] = None

        # Linux-specific fields
        self._linux_gpus:     List[dict] = []
        self._nvml_handles:   Dict[str, Any] = {}
        self._linux_fdinfo:   Optional[_LinuxDrmFdinfoSampler] = None
        self._linux_cpu_power: Optional[_LinuxCpuPowerSampler] = None

        if self._is_linux:
            self._init_linux()
        elif self._is_windows:
            self._init_windows()
        else:
            self._init_generic()

    # ── Linux init ─────────────────────────────────────────────────────────
    def _init_linux(self) -> None:
        self._linux_gpus = _linux_detect_gpus()
        self._linux_fdinfo = _LinuxDrmFdinfoSampler()
        self._linux_cpu_power = _LinuxCpuPowerSampler()

        # Fill NVIDIA VRAM via NVML if available.  Match by PCI bus-id first;
        # handle index order is not guaranteed to match DRM card order.
        if NVML_AVAILABLE:
            try:
                by_slot: Dict[str, Any] = {}
                dev_count = pynvml.nvmlDeviceGetCount()          # type: ignore
                for idx in range(dev_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(idx)  # type: ignore
                    with contextlib.suppress(Exception):
                        pci = pynvml.nvmlDeviceGetPciInfo(handle)    # type: ignore
                        bus_id = pci.busId.decode() if isinstance(pci.busId, bytes) else str(pci.busId)
                        by_slot[_linux_norm_pci_slot(bus_id)] = handle
                nvidia_idx = 0
                for gpu in self._linux_gpus:
                    if gpu["vendor"] != "nvidia":
                        continue
                    handle = by_slot.get(_linux_norm_pci_slot(gpu.get("pci_slot", "")))
                    if handle is None and nvidia_idx < dev_count:
                        handle = pynvml.nvmlDeviceGetHandleByIndex(nvidia_idx)  # type: ignore
                    nvidia_idx += 1
                    if handle is not None:
                        handle_key = gpu.get("card_dir") or gpu.get("pci_slot") or str(nvidia_idx)
                        self._nvml_handles[handle_key] = handle
                        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)            # type: ignore
                        gpu["vram_total_gb"] = float(mem.total) / (1024 ** 3)
            except Exception as exc:
                logger.debug("NVML init: %s", exc)

        # Separate dGPUs and iGPU
        self._dgpu_info: List[Tuple[str, float, str]] = []
        self._igpu_info: Optional[dict] = None

        for gpu in self._linux_gpus:
            if gpu.get("is_igpu", False):
                if self._igpu_info is None:
                    self._igpu_info = gpu
            else:
                vram = float(gpu.get("vram_total_gb") or 0.0)
                if vram <= 0 and gpu.get("vendor") in ("amd", "nvidia"):
                    vram = 8.0
                self._dgpu_info.append((
                    gpu["name"],
                    vram,
                    gpu.get("pci_slot", ""),
                ))

        if not self._dgpu_info and not self._igpu_info:
            self._dgpu_info = [("GPU", 8.0, "")]

    # ── Generic Unix init (currently macOS) ────────────────────────────────
    def _init_generic(self) -> None:
        """Use portable psutil metrics without probing Windows/Linux GPU APIs."""
        self._dgpu_info = []
        self._igpu_info = None

    # ── Windows init ────────────────────────────────────────────────────────
    def _init_windows(self) -> None:
        if self._provided_dgpu_info is not None:
            self._dgpu_info = list(self._provided_dgpu_info)
        else:
            self._dgpu_info, _ = _windows_detect_dgpus()
        amd_device_ids = [
            dev_key.lower()
            for name, _, dev_key in self._dgpu_info
            if "amd" in name.lower() or "radeon" in name.lower()
        ]
        self._has_amd_gpu = bool(amd_device_ids)
        self._ambiguous_adlx_device_ids = {
            device_id for device_id in amd_device_ids
            if amd_device_ids.count(device_id) > 1
        }
        if self._ambiguous_adlx_device_ids:
            logger.warning(
                "ADLX per-card merge disabled for ambiguous AMD device id(s): %s",
                ", ".join(sorted(self._ambiguous_adlx_device_ids)),
            )

        # NVML handles for TCC-mode GPUs, keyed by their "nvml:<index>" dev key.
        self._nvml_win_handles: Dict[str, Any] = {}
        for _, _, dev_key in self._dgpu_info:
            if dev_key.startswith("nvml:"):
                with contextlib.suppress(Exception):
                    self._nvml_win_handles[dev_key] = pynvml.nvmlDeviceGetHandleByIndex(  # type: ignore
                        int(dev_key.split(":", 1)[1]))

        # Standard WDDM NVIDIA cards are visible to PDH but still need NVML for
        # total-board power.  Bind handles once by PCI device id (name fallback)
        # and sample them at the same 4 Hz cadence as ADLX.
        self._nvml_wddm_handles: Dict[int, Any] = {}
        if NVML_AVAILABLE:
            try:
                records: List[dict] = []
                for nvml_index in range(pynvml.nvmlDeviceGetCount()):  # type: ignore
                    handle = pynvml.nvmlDeviceGetHandleByIndex(nvml_index)  # type: ignore
                    raw_name = pynvml.nvmlDeviceGetName(handle)  # type: ignore
                    nvml_name = (raw_name.decode(errors="replace")
                                 if isinstance(raw_name, bytes) else str(raw_name))
                    device_id = ""
                    with contextlib.suppress(Exception):
                        pci = pynvml.nvmlDeviceGetPciInfo(handle)  # type: ignore
                        device_id = f"0x{(int(pci.pciDeviceId) >> 16) & 0xFFFF:04X}"
                    records.append({
                        "handle": handle,
                        "device_id": device_id.lower(),
                        "name": re.sub(r"\s+", " ", nvml_name.lower()).strip(),
                    })
                for gpu_index, (gpu_name, _, dev_key) in enumerate(self._dgpu_info):
                    name_key = re.sub(r"\s+", " ", gpu_name.lower()).strip()
                    if dev_key.startswith("nvml:") or not any(
                        marker in name_key for marker in
                        ("nvidia", "geforce", "quadro", "tesla")
                    ):
                        continue
                    match_index = next(
                        (i for i, rec in enumerate(records)
                         if rec["device_id"] and rec["device_id"] == dev_key.lower()),
                        None,
                    )
                    if match_index is None:
                        match_index = next(
                            (i for i, rec in enumerate(records) if rec["name"] == name_key),
                            None,
                        )
                    if match_index is None and records:
                        match_index = 0
                    if match_index is not None:
                        self._nvml_wddm_handles[gpu_index] = records.pop(match_index)["handle"]
            except Exception as exc:
                logger.debug("Windows NVML handle mapping: %s", exc)

        self._nvml_win_cache: Dict[int, dict] = {}
        self._last_nvml_query = 0.0
        self._last_nvml_success = 0.0

        self._luid_device_map = get_dxgi_adapter_map()
        self._last_dxgi_refresh = time.monotonic()
        self._vram_cache: Dict[str, float] = {}     # luid → dedicated usage (GB)
        _igpu_name_markers = ('hd graphics', 'uhd graphics', 'iris',
                              'intel(r) graphics', 'arc(tm) graphics',
                              'radeon graphics', 'radeon(tm) graphics')
        self._igpu_luid = next(
            (luid for luid, (_devid, _vram, name) in self._luid_device_map.items()
             if any(m in name.lower() for m in _igpu_name_markers)),
            None,
        )

    def _log_loop_error(self, prefix: str, exc: Exception) -> None:
        """Log monitor-loop failures visibly, but throttled.

        A single hiccup is normal; a persistent streak means the dashboard
        is frozen and the user needs evidence in ~/.tricorder.log.  Warn on
        the 1st failure of a streak, again when it persists (~10 s at
        30 FPS), then roughly every 5 minutes.
        """
        self._loop_err_count += 1
        n = self._loop_err_count
        if n == 1 or n == 300 or n % 9000 == 0:
            logger.warning("%s (streak of %d): %s", prefix, n, exc)
        else:
            logger.debug("%s: %s", prefix, exc)

    def _log_sensor_error(self, sensor: str, exc: Exception) -> None:
        """Warn once per minute per sensor while retaining cached values."""
        now = time.monotonic()
        if now - self._sensor_error_last.get(sensor, 0.0) >= 60.0:
            logger.warning("%s telemetry failed; keeping last value: %s", sensor, exc)
            self._sensor_error_last[sensor] = now
        else:
            logger.debug("%s telemetry failed: %s", sensor, exc)

    # ── Latest-value hand-over ─────────────────────────────────────────────
    def _publish(self, m: SystemMetrics) -> None:
        with self._latest_lock:
            self._latest = m

    def take_latest(self) -> Optional[SystemMetrics]:
        """Return the newest metrics frame (or None) and clear the slot."""
        with self._latest_lock:
            m = self._latest
            self._latest = None
            return m

    # ── run() dispatcher ───────────────────────────────────────────────────
    def run(self) -> None:
        self._running = True
        if self._is_windows:
            self._run_windows()
        else:
            # The Linux loop's CPU, RAM, and disk sampling is psutil-based.
            # Generic Unix platforms use it with Linux-only GPU probes disabled.
            self._run_linux()

    # ══════════════════════════════════════════════════════════════════════
    # LINUX MONITORING LOOP
    # ══════════════════════════════════════════════════════════════════════
    def _run_linux(self) -> None:
        self._last_io     = psutil.disk_io_counters()
        self._last_io_per = psutil.disk_io_counters(perdisk=True) or {}
        self._last_t      = time.time()

        while self._running:
            try:
                now = time.time()
                dt  = max(now - self._last_t, 0.001)

                # ── Disk I/O (identical to Windows path) ───────────────────
                io_agg = psutil.disk_io_counters()
                rmb = wmb = 0.0
                if io_agg and self._last_io:
                    rmb = (io_agg.read_bytes  - self._last_io.read_bytes)  / (1024 * 1024) / dt
                    wmb = (io_agg.write_bytes - self._last_io.write_bytes) / (1024 * 1024) / dt
                self._last_io = io_agg

                io_per = psutil.disk_io_counters(perdisk=True) or {}
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

                # ── CPU / RAM (psutil — cross-platform) ────────────────────
                cpu_total = psutil.cpu_percent(interval=None)
                cpu_cores = {i: float(v) for i, v in enumerate(psutil.cpu_percent(percpu=True))}
                ram       = psutil.virtual_memory()
                if (self._linux_cpu_power is not None
                        and now - self._last_power_query >= 0.25):
                    self._last_power_query = now
                    cpu_power = self._linux_cpu_power.sample()
                    if cpu_power is not None:
                        self._cpu_power_watts = cpu_power
                        self._last_power_success = now
                    elif now - self._last_power_success >= 2.0:
                        self._cpu_power_watts = None

                # ── GPU (Linux-specific) ───────────────────────────────────
                fdinfo = self._linux_fdinfo.sample() if self._linux_fdinfo is not None else {}
                gpus: List[GPUMetrics] = []
                for name, vram_total, dev_id in self._dgpu_info:
                    gpu_util  = 0.0
                    gpu_3d_util = 0.0
                    compute_util = 0.0
                    copy0_util = 0.0
                    copy1_util = 0.0
                    codec_util = 0.0
                    vram_used = 0.0
                    power_watts: Optional[float] = None

                    # Find matching Linux GPU entry
                    linux_gpu = None
                    for g in self._linux_gpus:
                        if not g.get("is_igpu", False) and g["name"] == name and abs(g["vram_total_gb"] - vram_total) < 0.5:
                            linux_gpu = g
                            break
                    # Fallback: match by position
                    if linux_gpu is None:
                        idx = self._dgpu_info.index((name, vram_total, dev_id))
                        # Try to find a dGPU at that index (skipping iGPUs)
                        dgpus_only = [g for g in self._linux_gpus if not g.get("is_igpu", False)]
                        if idx < len(dgpus_only):
                            linux_gpu = dgpus_only[idx]

                    if linux_gpu is not None:
                        slot = _linux_norm_pci_slot(linux_gpu.get("pci_slot", ""))
                        fd = fdinfo.get(slot, {}) if slot else {}
                        gpu_3d_util = float(fd.get("3d", 0.0))
                        compute_util = float(fd.get("compute", 0.0))
                        copy0_util = float(fd.get("c0", 0.0))
                        copy1_util = float(fd.get("c1", 0.0))
                        codec_util = float(fd.get("codec", 0.0))
                        if linux_gpu["vendor"] == "amd":
                            gpu_util  = _linux_read_amd_gpu_busy(linux_gpu.get("card_dir", ""))
                            power_watts = _linux_read_gpu_power_watts(linux_gpu.get("card_dir", ""))
                            if gpu_util <= 0:
                                gpu_util = float(fd.get("3d", 0.0))
                            vram_used, vram_total_actual = _linux_read_amd_vram(linux_gpu.get("card_dir", ""))
                            if vram_used <= 0:
                                vram_used = float(fd.get("vram_used_gb", 0.0))
                            if vram_total_actual > 0:
                                vram_total = vram_total_actual
                        elif linux_gpu["vendor"] == "nvidia":
                            handle = self._nvml_handles.get(linux_gpu.get("card_dir") or "")
                            if handle is None:
                                handle = self._nvml_handles.get(linux_gpu.get("pci_slot") or "")
                            if handle is not None:
                                gpu_util, vram_used, vram_total_nv = _read_nvml_gpu(handle)
                                power_watts = _read_nvml_power_watts(handle)
                                if vram_total_nv > 0:
                                    vram_total = vram_total_nv
                        elif linux_gpu["vendor"] == "intel":
                            gpu_util = gpu_3d_util
                            vram_used = float(fd.get("vram_used_gb", 0.0))
                            power_watts = _linux_read_gpu_power_watts(linux_gpu.get("card_dir", ""))

                    gpus.append(GPUMetrics(
                        name=name,
                        luid="",
                        gpu_total_percent=gpu_util,
                        gpu_3d_percent=gpu_3d_util,
                        gpu_compute_percent=compute_util,
                        gpu_copy0_percent=copy0_util,
                        gpu_copy1_percent=copy1_util,
                        gpu_codec_percent=codec_util,
                        gpu_vram_used_gb=min(vram_used, vram_total),
                        gpu_vram_total_gb=vram_total,
                        gpu_power_watts=power_watts,
                    ))

                if not gpus and self._dgpu_info:
                    gpus = [GPUMetrics(
                        name=self._dgpu_info[0][0], luid="",
                        gpu_vram_total_gb=self._dgpu_info[0][1],
                    )]

                # ── iGPU Utilization ─────────────────────────────────────────
                igpu_util = 0.0
                if self._igpu_info:
                    slot = _linux_norm_pci_slot(self._igpu_info.get("pci_slot", ""))
                    fd = fdinfo.get(slot, {}) if slot else {}
                    # AMD/Xe may expose gpu_busy_percent; Intel i915 usually
                    # needs DRM fdinfo.  Use the hottest engine as the single
                    # iGPU tile value.
                    igpu_util = _linux_read_amd_gpu_busy(self._igpu_info.get("card_dir", ""))
                    igpu_util = max(igpu_util, float(fd.get("3d", 0.0)), float(fd.get("compute", 0.0)),
                                    float(fd.get("c0", 0.0)), float(fd.get("codec", 0.0)))

                self._publish(SystemMetrics(
                    cpu_total_percent=cpu_total,
                    cpu_cores=cpu_cores,
                    cpu_power_watts=self._cpu_power_watts,
                    ram_total_gb=ram.total / (1024 ** 3),
                    ram_used_gb=ram.used  / (1024 ** 3),
                    ram_percent=ram.percent,
                    gpus=gpus,
                    igpu_percent=igpu_util,
                    disk_read_mbps=rmb,
                    disk_write_mbps=wmb,
                    drives=drives,
                    timestamp=datetime.now(),
                ))
                self._loop_err_count = 0
            except Exception as exc:
                self._log_loop_error("Linux monitor loop error", exc)
            time.sleep(1.0 / 30.0)

    def _read_nvml_win_gpu(self, name: str, vram_total: float, dev_key: str) -> GPUMetrics:
        """Build GPUMetrics for a TCC-mode NVIDIA GPU via NVML.

        TCC cards have no 3D engine exposed — overall utilization is CUDA
        work, so it feeds the Compute row of the 3D/Compute tile; the codec
        row comes from NVML's encoder/decoder utilization.
        """
        util = vram_used = 0.0
        codec = 0.0
        power_watts: Optional[float] = None
        handle = self._nvml_win_handles.get(dev_key)
        if handle is not None:
            util, vram_used, vram_total_nv = _read_nvml_gpu(handle)
            power_watts = _read_nvml_power_watts(handle)
            if vram_total_nv > 0:
                vram_total = vram_total_nv
            with contextlib.suppress(Exception):
                enc, _ = pynvml.nvmlDeviceGetEncoderUtilization(handle)   # type: ignore
                dec, _ = pynvml.nvmlDeviceGetDecoderUtilization(handle)   # type: ignore
                codec = float(max(enc, dec))
        return GPUMetrics(
            name=name, luid=dev_key,
            gpu_total_percent=util,
            gpu_compute_percent=util,
            gpu_codec_percent=codec,
            gpu_vram_used_gb=min(vram_used, vram_total) if vram_total else vram_used,
            gpu_vram_total_gb=vram_total,
            gpu_power_watts=power_watts,
        )

    # ══════════════════════════════════════════════════════════════════════
    # WINDOWS MONITORING LOOP
    # ══════════════════════════════════════════════════════════════════════
    def _run_windows(self) -> None:
        # Driver/performance-counter APIs are initialized inside this worker
        # thread so startup and the Qt event loop never wait on sensor setup.
        self._pdh = _PdhGpuSampler()
        self._pdh_memory = _PdhGpuMemorySampler()
        self._pdh_energy = _PdhEnergySampler()
        self._adlx = _AdlxGpuSampler() if self._has_amd_gpu else None
        self._adlx_cache = []
        self._last_adlx_query = 0.0
        self._last_adlx_success = time.monotonic()
        self._next_adlx_retry = self._last_adlx_success + 30.0
        self._nvml_win_cache = {}
        self._last_nvml_query = 0.0
        self._last_nvml_success = 0.0
        self._last_memory_query = 0.0
        self._last_memory_success = 0.0
        self._last_power_query = 0.0
        self._last_power_success = 0.0
        self._cpu_power_watts = None

        # WMI remains a one-shot inventory source only.  It is deliberately
        # absent from this hot loop: COM/perflib calls can block for seconds and
        # were the main source of visible telemetry pauses under heavy AI load.

        try:
            self._last_io = psutil.disk_io_counters()
            self._last_io_per = psutil.disk_io_counters(perdisk=True) or {}
        except Exception as exc:
            self._log_sensor_error("Disk", exc)
            self._last_io = None
            self._last_io_per = {}
        self._last_disk_t = time.monotonic()
        self._last_disk_query = self._last_disk_t
        rmb = wmb = 0.0
        drives = [DriveMetrics(key=key, label=label, read_mbps=0.0, write_mbps=0.0)
                  for key, label in self._drive_info]
        cpu_total = 0.0
        cpu_cores: Dict[int, float] = {}
        ram_total_gb = ram_used_gb = ram_percent = 0.0
        next_tick = time.monotonic()

        while self._running:
            loop_started = time.monotonic()
            try:
                now = loop_started

                # Disk enumeration is comparatively expensive on a nine-drive
                # workstation and does not benefit from 30 Hz polling.  Read it
                # at 10 Hz, isolate failures (including WinError 1450), and keep
                # publishing the last good frame instead of freezing all tiles.
                if now - self._last_disk_query >= 0.1:
                    self._last_disk_query = now
                    disk_dt = max(now - self._last_disk_t, 0.001)
                    try:
                        io_agg = psutil.disk_io_counters()
                        io_per = psutil.disk_io_counters(perdisk=True) or {}
                        next_rmb = next_wmb = 0.0
                        if io_agg and self._last_io:
                            next_rmb = max(0.0, (io_agg.read_bytes - self._last_io.read_bytes)
                                           / (1024 * 1024) / disk_dt)
                            next_wmb = max(0.0, (io_agg.write_bytes - self._last_io.write_bytes)
                                           / (1024 * 1024) / disk_dt)
                        next_drives: List[DriveMetrics] = []
                        for key, label in self._drive_info:
                            if key in io_per and key in self._last_io_per:
                                read_mbps = max(
                                    0.0, (io_per[key].read_bytes - self._last_io_per[key].read_bytes)
                                    / (1024 * 1024) / disk_dt)
                                write_mbps = max(
                                    0.0, (io_per[key].write_bytes - self._last_io_per[key].write_bytes)
                                    / (1024 * 1024) / disk_dt)
                            else:
                                read_mbps = write_mbps = 0.0
                            next_drives.append(DriveMetrics(
                                key=key, label=label,
                                read_mbps=read_mbps, write_mbps=write_mbps))
                        self._last_io = io_agg
                        self._last_io_per = io_per
                        self._last_disk_t = now
                        rmb, wmb, drives = next_rmb, next_wmb, next_drives
                    except Exception as exc:
                        self._log_sensor_error("Disk", exc)

                # CPU and RAM failures are independent from GPU/disk telemetry;
                # keep the last values and still publish a complete frame.
                try:
                    cpu_total = psutil.cpu_percent(interval=None)
                    cpu_cores = {
                        i: float(value)
                        for i, value in enumerate(psutil.cpu_percent(percpu=True))
                    }
                except Exception as exc:
                    self._log_sensor_error("CPU", exc)
                try:
                    ram = psutil.virtual_memory()
                    ram_total_gb = ram.total / (1024 ** 3)
                    ram_used_gb = ram.used / (1024 ** 3)
                    ram_percent = float(ram.percent)
                except Exception as exc:
                    self._log_sensor_error("RAM", exc)

                # ADLX updates at a driver-defined cadence (minimum 250 ms).
                # Cache its latest frame rather than making redundant calls at
                # the 30 FPS UI/PDH rate.
                if (self._adlx is not None and self._adlx.ok
                        and now - self._last_adlx_query >= 0.25):
                    self._last_adlx_query = now
                    adlx_sample = self._adlx.sample()
                    if adlx_sample:
                        self._adlx_cache = adlx_sample
                        self._last_adlx_success = now
                        self._next_adlx_retry = now + 5.0
                    elif now - self._last_adlx_success >= 1.0:
                        # Never leave a stale 100 % / high-power value visible
                        # after a driver reset or failed ADLX session.
                        self._adlx_cache = []
                adlx_stale = now - self._last_adlx_success >= 5.0
                if (self._has_amd_gpu
                        and (self._adlx is None or not self._adlx.ok or adlx_stale)
                        and now >= self._next_adlx_retry):
                    if self._adlx is not None:
                        self._adlx.close()
                    self._adlx = _AdlxGpuSampler()
                    self._next_adlx_retry = now + (5.0 if self._adlx.ok else 30.0)
                    if not self._adlx.ok:
                        self._adlx_cache = []

                if (self._nvml_wddm_handles
                        and now - self._last_nvml_query >= 0.25):
                    self._last_nvml_query = now
                    nvml_cache: Dict[int, dict] = {}
                    for gpu_index, handle in self._nvml_wddm_handles.items():
                        util, used_gb, total_gb = _read_nvml_gpu(handle)
                        if total_gb > 0:
                            nvml_cache[gpu_index] = {
                                "usage_percent": util,
                                "vram_used_gb": used_gb,
                                "power_watts": _read_nvml_power_watts(handle),
                            }
                    if nvml_cache:
                        self._nvml_win_cache = nvml_cache
                        self._last_nvml_success = now
                    elif now - self._last_nvml_success >= 2.0:
                        self._nvml_win_cache = {}

                # Dedicated VRAM and CPU package power change much more slowly
                # than engine utilization.  Native PDH reads at 2/4 Hz replace
                # the old synchronous WMI query that could stall for seconds.
                if (self._pdh_memory is not None and self._pdh_memory.ok
                        and now - self._last_memory_query >= 0.5):
                    self._last_memory_query = now
                    memory_rows = self._pdh_memory.sample()
                    if memory_rows:
                        self._last_memory_success = now
                        vram_cache: Dict[str, float] = {}
                        for instance, dedicated_bytes in memory_rows:
                            luid = instance.split('_phys')[0]
                            used_gb = max(dedicated_bytes, 0.0) / (1024 ** 3)
                            vram_cache[luid] = max(vram_cache.get(luid, 0.0), used_gb)
                        self._vram_cache = vram_cache
                    elif now - self._last_memory_success >= 5.0:
                        self._vram_cache = {}
                if (self._pdh_energy is not None and self._pdh_energy.ok
                        and now - self._last_power_query >= 0.25):
                    self._last_power_query = now
                    cpu_power = _cpu_package_power_from_pdh(self._pdh_energy.sample())
                    if cpu_power is not None:
                        self._cpu_power_watts = cpu_power
                        self._last_power_success = now
                    elif now - self._last_power_success >= 2.0:
                        self._cpu_power_watts = None

                # ── GPU (PDH engine split + ADLX overall AMD load) ─────────
                igpu_p = 0.0
                luid_data: Dict[str, dict] = {}

                if self._pdh and self._pdh.ok:
                    _LUID_RE = re.compile(r'luid_(0x[0-9a-f]+_0x[0-9a-f]+)')
                    _ENG_RE  = re.compile(r'_eng_(\d+)_')

                    _engine_rows: list = []
                    _engine_rows = self._pdh.sample()

                    # Persistent empty samples are the "tiles frozen at 0"
                    # symptom (e.g. after a driver reset) — leave evidence.
                    if _engine_rows:
                        self._empty_rows_count = 0
                    else:
                        self._empty_rows_count += 1
                        if self._empty_rows_count == 300 or self._empty_rows_count % 9000 == 0:
                            logger.warning(
                                "GPU engine sampler returned no data %d times in a row "
                                "— GPU tiles stuck at 0 (driver reset / counters gone?)",
                                self._empty_rows_count,
                            )

                    # ── Step 1: seed luid_data from engine rows ────────────────
                    for _e in _engine_rows:
                        try:
                            _en = _e[0]
                            _m = _LUID_RE.search(_en)
                            if _m:
                                _luid = 'luid_' + _m.group(1)
                                if _luid == self._igpu_luid:
                                    continue
                                luid_data.setdefault(_luid, {'3d': 0.0, 'compute': 0.0,
                                                             'c0': 0.0, 'c1': 0.0, 'codec': 0.0, 'used': 0.0})
                        except Exception:
                            pass

                    # ── Step 2: apply the native PDH VRAM cache ────────────────
                    for luid, used in self._vram_cache.items():
                        ld = luid_data.setdefault(luid, {'3d': 0.0, 'compute': 0.0,
                                                         'c0': 0.0, 'c1': 0.0, 'codec': 0.0, 'used': 0.0})
                        ld['used'] = max(ld['used'], used)

                    # ── Step 3: aggregate engine utilization ───────────────────
                    _eng_max: Dict[tuple, tuple] = {}
                    for _e in _engine_rows:
                        try:
                            _en   = _e[0]
                            _util = _e[1]
                            if _util <= 0:
                                continue
                            _lm = _LUID_RE.search(_en)
                            if not _lm:
                                continue
                            _cl = 'luid_' + _lm.group(1)
                            if _cl == self._igpu_luid:
                                igpu_p = max(igpu_p, _util)
                                continue
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
                                _et = 'codec'
                            else:
                                continue
                            _key = (_cl, _ei)
                            _prev = _eng_max.get(_key, (0.0, _et))[0]
                            _eng_max[_key] = (max(_util, _prev), _et)
                        except Exception:
                            pass

                    _copy_order: Dict[str, list] = {}
                    for (_cl2, _ei2), (_, _et2) in _eng_max.items():
                        if _et2 == 'copy':
                            _copy_order.setdefault(_cl2, [])
                            if _ei2 not in _copy_order[_cl2]:
                                _copy_order[_cl2].append(_ei2)
                    for _k in _copy_order:
                        _copy_order[_k].sort()

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
                            luid_data[_cl3]['codec'] = max(luid_data[_cl3]['codec'], _eu3)

                new: List[str] = sorted(
                    [luid for luid in luid_data if luid not in self._luid_order],
                    key=lambda luid: -luid_data[luid]['used'],
                )
                self._luid_order.extend(new)

                # After a driver reset/TDR the adapter re-enumerates with a NEW
                # LUID that the init-time DXGI map has never seen — refresh the
                # map (throttled) so the device-id binding below can re-attach
                # instead of leaving the GPU tile stuck at 0.
                if (any(luid not in self._luid_device_map for luid in new)
                        and now - self._last_dxgi_refresh >= 5.0):
                    self._last_dxgi_refresh = now
                    refreshed = get_dxgi_adapter_map()
                    if refreshed:
                        self._luid_device_map.update(refreshed)

                luid_to_device_id: Dict[str, str] = {
                    luid: self._luid_device_map[luid][0]
                    for luid in luid_data
                    if luid in self._luid_device_map
                }
                for _luid, _dev in luid_to_device_id.items():
                    self._luid_device_id[_luid] = _dev

                # Per-device LUID *lists*: identical GPUs (e.g. 8× the same
                # card in a render server) share one PCI device id, so a
                # single dev→luid entry would bind every tile to GPU 0.  Each
                # tile pops its own LUID instead.  Iterating _luid_order keeps
                # the tile↔LUID assignment stable across frames.
                device_to_luids: Dict[str, List[str]] = {}
                for luid in self._luid_order:
                    if luid not in luid_data:
                        continue
                    dev = luid_to_device_id.get(luid) or self._luid_device_id.get(luid, "")
                    if dev:
                        device_to_luids.setdefault(dev, []).append(luid)

                bound = {luid for luids in device_to_luids.values() for luid in luids}
                # Only LUIDs alive in THIS sample are usable fallbacks: after a
                # driver reset the dead LUID stays in _luid_order forever, and
                # popping it here would permanently bind a tile to no data.
                leftover = [luid for luid in self._luid_order
                            if luid not in bound and luid in luid_data]

                # ADLX enumerates only AMD GPUs and may use a different order
                # than WMI (for example PCI order versus descending VRAM).
                # Bind by PCI device id.  Ambiguous identical-card IDs are
                # deliberately excluded below rather than risking swapped data.
                adlx_by_device: Dict[str, List[dict]] = {}
                adlx_by_name: Dict[str, List[dict]] = {}
                for adlx_gpu in self._adlx_cache:
                    adlx_dev = str(adlx_gpu.get("device_id") or "").lower()
                    adlx_name = re.sub(r"\s+", " ", str(adlx_gpu.get("name") or "").lower()).strip()
                    if adlx_dev:
                        adlx_by_device.setdefault(adlx_dev, []).append(adlx_gpu)
                    if adlx_name:
                        adlx_by_name.setdefault(adlx_name, []).append(adlx_gpu)

                gpus: List[GPUMetrics] = []
                for gpu_index, (name, vram_total, dev_id) in enumerate(self._dgpu_info):
                    # TCC-mode NVIDIA GPUs: invisible to DXGI/perf counters —
                    # read utilization and VRAM directly via NVML.
                    if dev_id.startswith("nvml:"):
                        gpus.append(self._read_nvml_win_gpu(name, vram_total, dev_id))
                        continue

                    candidates = device_to_luids.get(dev_id)
                    luid = candidates.pop(0) if candidates else ""
                    if not luid and leftover:
                        luid = leftover.pop(0)
                    d = luid_data.get(luid, {}) if luid else {}
                    if luid:
                        self._luid_vram[luid]      = vram_total
                        self._luid_device_id[luid] = dev_id
                    used = min(d.get('used', 0.0), vram_total)
                    engine_total = max(
                        d.get('3d', 0.0), d.get('compute', 0.0),
                        d.get('c0', 0.0), d.get('c1', 0.0), d.get('codec', 0.0),
                    )
                    name_key = re.sub(r"\s+", " ", name.lower()).strip()
                    is_amd_gpu = "amd" in name_key or "radeon" in name_key
                    adlx_identity_safe = (
                        is_amd_gpu and dev_id.lower() not in self._ambiguous_adlx_device_ids
                    )
                    adlx_candidates = (adlx_by_device.get(dev_id.lower(), [])
                                       if adlx_identity_safe else [])
                    if not adlx_candidates and adlx_identity_safe:
                        adlx_candidates = adlx_by_name.get(name_key, [])
                    adlx_gpu = adlx_candidates.pop(0) if adlx_candidates else {}
                    adlx_usage = adlx_gpu.get("usage_percent")
                    adlx_vram = adlx_gpu.get("vram_used_gb")
                    nvml_gpu = self._nvml_win_cache.get(gpu_index, {})
                    nvml_usage = nvml_gpu.get("usage_percent")
                    driver_usage = adlx_usage if adlx_usage is not None else nvml_usage
                    driver_vram = adlx_vram if adlx_vram is not None else nvml_gpu.get("vram_used_gb")
                    if driver_vram is not None:
                        used = min(float(driver_vram), vram_total)
                    driver_power = adlx_gpu.get("power_watts")
                    if driver_power is None:
                        driver_power = nvml_gpu.get("power_watts")
                    gpus.append(GPUMetrics(
                        name=name, luid=luid,
                        gpu_total_percent=(float(driver_usage)
                                           if driver_usage is not None else engine_total),
                        gpu_3d_percent=d.get('3d', 0.0),
                        gpu_compute_percent=d.get('compute', 0.0),
                        gpu_copy0_percent=d.get('c0', 0.0),
                        gpu_copy1_percent=d.get('c1', 0.0),
                        gpu_codec_percent=d.get('codec', 0.0),
                        gpu_vram_used_gb=used,
                        gpu_vram_total_gb=vram_total,
                        gpu_power_watts=driver_power,
                    ))

                if not gpus:
                    gpus = [GPUMetrics(name=self._dgpu_info[0][0], luid='',
                                       gpu_vram_total_gb=self._dgpu_info[0][1])]

                self._publish(SystemMetrics(
                    cpu_total_percent=cpu_total,
                    cpu_cores=cpu_cores,
                    cpu_power_watts=self._cpu_power_watts,
                    ram_total_gb=ram_total_gb,
                    ram_used_gb=ram_used_gb,
                    ram_percent=ram_percent,
                    gpus=gpus,
                    igpu_percent=igpu_p,
                    disk_read_mbps=rmb,
                    disk_write_mbps=wmb,
                    drives=drives,
                    timestamp=datetime.now(),
                ))
                self._loop_err_count = 0
            except Exception as exc:
                self._log_loop_error("Monitor loop error", exc)

            elapsed = time.monotonic() - loop_started
            if (elapsed >= 0.25
                    and time.monotonic() - self._last_slow_loop_warning >= 60.0):
                logger.warning("Slow Windows telemetry iteration: %.3f s", elapsed)
                self._last_slow_loop_warning = time.monotonic()
            next_tick += 1.0 / 30.0
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # Do not run a catch-up burst after the machine was saturated.
                next_tick = time.monotonic()

        self._close_windows_samplers()

    def _close_windows_samplers(self) -> None:
        """Close native sensors idempotently, preferably in the worker thread."""
        for attr in ("_adlx", "_pdh", "_pdh_memory", "_pdh_energy"):
            sampler = getattr(self, attr, None)
            if sampler is not None:
                with contextlib.suppress(Exception):
                    sampler.close()
                setattr(self, attr, None)

    def stop(self) -> None:
        self._running = False
        self.wait()
        if not self._is_linux:
            # Normally already closed at the end of _run_windows.  This also
            # covers a monitor object that was constructed but never started.
            self._close_windows_samplers()
        if NVML_AVAILABLE:
            with contextlib.suppress(Exception):
                pynvml.nvmlShutdown()                                  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
# CPU TOPOLOGY  (unchanged from v0.2)
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_ranges(indices):
    """Compress ints to a compact range string, e.g. [0,1,10,11,12,13,22,23] -> '0-1,10-13,22-23'."""
    idx = sorted(set(int(i) for i in indices))
    if not idx:
        return ""
    out, start, prev = [], idx[0], idx[0]
    for n in idx[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(out)


def _linux_get_cpu_topology() -> Optional[dict]:
    """Detect P/E-core topology on Linux via ``/sys/devices/system/cpu``.

    Uses ``cpu_capacity`` (or ``cpu_efficiency``) exported by the kernel
    scheduler to distinguish P-cores (higher capacity) from E-cores.
    Intel Arrow Lake / Meteor Lake hybrid CPUs expose these files; pure
    P-core or pure E-core designs get ``is_hybrid=False``.
    """
    try:
        cpu_base = Path("/sys/devices/system/cpu")
        cores: Dict[int, List[int]] = {}      # core_id → [logical indices]

        for cpu_d in cpu_base.glob("cpu[0-9]*"):
            idx = int(cpu_d.name[3:])
            cid_file = cpu_d / "topology" / "core_id"
            if not cid_file.is_file():
                continue
            cid = int(cid_file.read_text().strip())
            cores.setdefault(cid, []).append(idx)

        if not cores:
            return None

        # ── Read per-CPU capacity / efficiency ────────────────────────────
        capacities: Dict[int, int] = {}
        for cpu_d in cpu_base.glob("cpu[0-9]*"):
            idx = int(cpu_d.name[3:])
            for fname in ("cpu_capacity", "cpu_efficiency"):
                f = cpu_d / fname
                if f.is_file():
                    with contextlib.suppress(Exception):
                        capacities[idx] = int(f.read_text().strip())
                    break

        # ── No hybrid info → non-hybrid ───────────────────────────────────
        if not capacities:
            total_t = sum(len(v) for v in cores.values())
            return {
                "is_hybrid": False,
                "p_cores": len(cores), "p_threads": total_t,
                "e_cores": 0,            "e_threads": 0,
                "p_logical": list(cores.values()),
                "e_logical": [],
            }

        unique_caps = sorted(set(capacities.values()), reverse=True)
        if len(unique_caps) < 2:
            total_t = sum(len(v) for v in cores.values())
            return {
                "is_hybrid": False,
                "p_cores": len(cores), "p_threads": total_t,
                "e_cores": 0,            "e_threads": 0,
                "p_logical": list(cores.values()),
                "e_logical": [],
            }

        p_cap = unique_caps[0]     # highest  → P-core
        e_cap = unique_caps[-1]    # lowest   → E-core

        p_logical: List[List[int]] = []
        e_logical: List[int]       = []

        for cid in sorted(cores):
            threads = cores[cid]
            rep_cap = capacities.get(threads[0], 0)
            if rep_cap >= p_cap:
                p_logical.append(threads)
            elif rep_cap <= e_cap:
                e_logical.extend(threads)
            else:
                # mid-range — treat as P-core
                p_logical.append(threads)

        return {
            "is_hybrid": True,
            "p_cores":   len(p_logical),
            "p_threads": sum(len(t) for t in p_logical),
            "e_cores":   len(set(e_logical)) if e_logical else 0,
            "e_threads": len(e_logical),
            "p_logical": p_logical,
            "e_logical": e_logical,
        }
    except Exception as exc:
        logger.debug("Linux CPU topology detection: %s", exc)
        return None

def _get_cpu_topology() -> Optional[dict]:
    current_platform = platform.system()
    if current_platform == "Linux":
        return _linux_get_cpu_topology()
    if current_platform != "Windows":
        return None
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

        cores: list = []   # (eff, threads, [logical indices...])
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
                logical_idx: list = []   # logical CPU indices of THIS physical core
                gm_off  = offset + 32
                for _ in range(group_count):
                    mask = int.from_bytes(buf[gm_off: gm_off + 8], 'little')
                    grp  = int.from_bytes(buf[gm_off + 8: gm_off + 10], 'little')
                    threads += bin(mask).count('1')
                    for b in range(64):
                        if mask & (1 << b):
                            logical_idx.append(grp * 64 + b)
                    gm_off += 16
                cores.append((eff, threads, logical_idx))
            offset += size

        if not cores:
            return None

        eff_classes = sorted(set(c[0] for c in cores))
        if len(eff_classes) < 2:
            total_t = sum(t for _, t, _ in cores)
            return {'is_hybrid': False,
                    'p_cores': len(cores), 'p_threads': total_t,
                    'e_cores': 0,          'e_threads': 0,
                    'p_logical': [idx for _, _, idx in cores],
                    'e_logical': []}

        max_eff = max(eff_classes)
        min_eff = min(eff_classes)
        p_group = [(e, t, idx) for e, t, idx in cores if e == max_eff]
        e_group = [(e, t, idx) for e, t, idx in cores if e == min_eff]
        return {
            'is_hybrid': True,
            'p_cores':   len(p_group), 'p_threads': sum(t for _, t, _ in p_group),
            'e_cores':   len(e_group), 'e_threads': sum(t for _, t, _ in e_group),
            # Real logical CPU indices.  On Arrow Lake (e.g. Core Ultra 9 285K)
            # P and E are INTERLEAVED (P=[0,1,10-13,22,23], E=[2-9,14-21]), so the
            # old "first N = P" assumption mislabelled ~half the bars.
            # p_logical is a list of per-core groups (a P-core may have 2 HT
            # siblings); e_logical is flat (E-cores never have HT).
            'p_logical': [idx for _, _, idx in p_group],
            'e_logical': [i for _, _, idx in e_group for i in idx],
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# WIDGET PRIMITIVES
# ═══════════════════════════════════════════════════════════════════════════════


def _set_responsive_label_width(label: QLabel, maximum: int) -> None:
    """Let compact tiles shrink labels instead of forcing horizontal clipping."""
    label.setMinimumWidth(0)
    label.setMaximumWidth(dp(maximum))
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)


class SparklineWidget(QWidget):
    """
    Single horizontal sparkline with filled area.
    Expects values 0–100 (percentage).
    """
    #: Hard lower bound below which a sparkline stops being readable.  The
    #: per-instance ``min_height`` parameter is only the PREFERRED height
    #: (exposed via sizeHint) -- the layout may compress the widget down to
    #: this floor when the window shrinks, but never below it.  Keeping the
    #: hard minimum small guarantees the widget is never taller than the
    #: space its tile can give it, so it is never clipped: 0 % is ALWAYS the
    #: visible bottom edge and 100 % the visible top edge.  (Previously
    #: ``min_height`` was enforced as a hard Qt minimum; when the window was
    #: shrunk the rows compressed below that minimum, the sparkline
    #: overflowed its tile and the bottom of the graph -- the 0 % baseline
    #: and every low value -- was silently cut off.)
    _HARD_MIN_H = 4

    def __init__(self, color_hex: str, history_len: int = 90,
                 min_height: int = 70, parent=None) -> None:
        super().__init__(parent)
        self.color   = QColor(color_hex)
        self.history: deque = deque([0.0] * history_len, maxlen=history_len)
        self._dirty  = False
        self._grid_cache: Optional[Tuple[int, int, QPixmap]] = None  # (w, h, pixmap)
        self._pref_h = dp(min_height)   # preferred height, see sizeHint()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(dp(self._HARD_MIN_H))

    def sizeHint(self) -> QSize:                                            # type: ignore
        """Preferred size.  Qt lays the widget out at this height when space
        allows and shrinks it toward minimumHeight when the window gets
        tight -- instead of overflowing (and clipping) the parent tile."""
        return QSize(dp(120), self._pref_h)

    def set_color(self, color_hex: str) -> None:
        """Change only the rendering colour; metric history stays intact."""
        color = QColor(color_hex)
        if color.isValid() and color != self.color:
            self.color = color
            self.update()

    def _invalidate_theme(self) -> None:
        self._grid_cache = None
        self.update()

    def add_value(self, value: float) -> None:
        self.history.append(value)
        # Mark dirty but defer update - parent will batch-call update() once
        self._dirty = True

    def recent_avg(self, count: int = 30) -> float:
        """Raw, unsmoothed value for the percentage number.

        Despite the name (kept for call-site compatibility) this returns the
        latest instantaneous sample straight through -- no EMA, no peak-hold,
        no averaging.  The number reacts on the very next frame and drops to
        0 instantly when a core/engine goes idle.  (Previously this returned a
        peak-hold envelope with a ~0.4 %/frame release, which took tens of
        seconds to decay and made every % feel sluggish.)
        """
        return self.history[-1] if self.history else 0.0

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
        px.fill(QColor(_theme_color("graph_bg")))
        p = QPainter(px)
        p.setPen(QPen(QColor(_theme_color("graph_grid")), 1))
        # Time axis: fixed, DPI-scaled pitch.
        pitch = dp(25)
        for x in range(pitch, w, pitch):
            p.drawLine(x, 0, x, h)
        # Value axis: gridlines at fixed 25 % steps so the bottom edge is
        # always 0 % and the top edge always 100 %, at ANY tile height.  The
        # grid rescales with the tile and is identical across the global
        # tiles and the CPU core boxes (previously a fixed 15 px texture was
        # tiled from the top, leaving a partial cell at the bottom and a
        # different line count in every tile).
        for q in (1, 2, 3):
            y = round(h * q / 4)
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
        # Keep core boxes compressible so every active core remains on screen.
        self.setMinimumHeight(0)
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

        self.graph = SparklineWidget(color_hex, min_height=18)
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
    move_requested       = pyqtSignal(str, str, bool)  # (src_id, target_id, insert_before)
    remove_requested     = pyqtSignal(str)              # tile_id
    rowbreak_requested   = pyqtSignal(str)              # tile_id — toggle row-break before this tile
    color_requested      = pyqtSignal(str, str)         # (tile_id, #rrggbb)
    color_reset_requested = pyqtSignal(str)             # tile_id

    _BTN_SIZE = 18  # logical pixels — scaled in __init__

    def __init__(self, tile_id: str, color_hex: str, parent=None) -> None:
        super().__init__(parent)
        self.tile_id      = tile_id
        self._default_color_hex = _normalise_color_hex(color_hex) or "#00ff88"
        self._custom_color_hex: Optional[str] = None
        self._color_hex   = self._default_color_hex
        self._edit_mode   = False
        self._drop_hl     = False
        self._drop_before = True
        self._drag_pos: Optional[QPoint] = None

        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._apply_frame_style(color_hex, edit=False)

        self._build_content()

        # ── × close button (top-right) ────────────────────────────────────────
        _btn_size = dp(self._BTN_SIZE)
        self._btn_x = QPushButton("×", self)
        self._btn_x.setFixedSize(_btn_size, _btn_size)
        _set_themed_style(self._btn_x, f"""
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
            _set_themed_style(self._btn_rn, f"""
                QPushButton {{
                    background: #00aa55; color: #fff;
                    border-radius: {_br}px; font-size: {_fs}; font-weight: bold;
                }}
                QPushButton:hover {{ background: #00ff88; color: #000; }}
            """)
        else:
            _set_themed_style(self._btn_rn, f"""
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
        protected = (
            (accent,) if self._custom_color_hex is not None and not edit else ())
        _set_themed_style(self, f"""
            QFrame {{
                background-color: #121218;
                border: 1px solid {border_side};
                border-top: 3px solid {accent};
                border-radius: 6px;
            }}
            QLabel      {{ background: transparent; border: none; }}
            QPushButton {{ background: transparent; border: none; }}
        """, protected)

    def _build_content(self) -> None:
        """Override in subclass to populate the tile layout."""
        pass

    def batch_update(self) -> None:
        """No-op base - subclasses override to batch sparkline updates."""
        pass

    def update_val(self, value: float, suffix: Optional[str] = None) -> None:
        """Override in subclass to update a single-value tile."""
        pass

    def update_power(self, watts: Optional[float]) -> None:
        """Override in subclass to update a power tile."""
        pass

    def update_3d_compute(self, gpu_3d: float, compute: float) -> None:
        """Override in subclass to update the 3D/Compute engine tile."""
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

    @property
    def custom_color(self) -> Optional[str]:
        return self._custom_color_hex

    def _accent_protection(self) -> Tuple[str, ...]:
        return ((self._custom_color_hex,)
                if self._custom_color_hex is not None else ())

    def set_custom_color(self, color_hex: Optional[str]) -> None:
        """Apply or reset this tile's persisted user-selected accent."""
        normalised = _normalise_color_hex(color_hex) if color_hex is not None else None
        if color_hex is not None and normalised is None:
            return
        self._custom_color_hex = normalised
        self._color_hex = normalised or self._default_color_hex
        self._apply_accent_colors()

    def _apply_accent_colors(self) -> None:
        """Apply the active accent; subclasses update their labels/graphs too."""
        accent = "#ffdd55" if self._edit_mode else self._color_hex
        self._apply_frame_style(accent, edit=self._edit_mode)

    def _choose_custom_color(self) -> None:
        dialog = QColorDialog(QColor(self._color_hex), self)
        dialog.setWindowTitle("Kachelfarbe auswählen")
        # Qt's non-native picker guarantees the spectrum plus RGB and HTML/HEX
        # inputs on every supported OS; native platform dialogs do not.
        dialog.setOption(
            QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dialog.setOption(
            QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = _normalise_color_hex(
            dialog.selectedColor().name(QColor.NameFormat.HexRgb))
        if selected is not None:
            self.color_requested.emit(self.tile_id, selected)

    def contextMenuEvent(self, event) -> None:                              # type: ignore
        if not self._edit_mode:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        _set_themed_style(menu, """
            QMenu { background: #1e1e2e; color: #ccc; border: 1px solid #333; }
            QMenu::item { padding: 6px 18px; }
            QMenu::item:selected { background: #2e2e3e; color: #fff; }
            QMenu::item:disabled { color: #555; }
        """)
        choose = QAction("🎨  Farbe ändern …", menu)
        choose.setToolTip("Farbspektrum mit RGB- und HEX-Eingabe")
        menu.addAction(choose)
        reset = QAction("↺  Standardfarbe", menu)
        reset.setEnabled(self._custom_color_hex is not None)
        menu.addAction(reset)
        selected = menu.exec(event.globalPos())
        if selected is choose:
            self._choose_custom_color()
        elif selected is reset:
            self.color_reset_requested.emit(self.tile_id)
        event.accept()

    # ── Edit mode ──────────────────────────────────────────────────────────────
    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._btn_x.setVisible(enabled)
        self._btn_rn.setVisible(enabled)
        self.setCursor(Qt.CursorShape.SizeAllCursor if enabled else Qt.CursorShape.ArrowCursor)  # type: ignore
        self.setToolTip(
            "Ziehen zum Verschieben · Rechtsklick für Farbe"
            if enabled else "")
        self._apply_accent_colors()

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
# METRIC TILE  — single sparkline (CPU total, RAM, GPU, iGPU …)
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

    def _apply_accent_colors(self) -> None:
        super()._apply_accent_colors()
        _set_themed_style(
            self._title_lbl,
            f"color: {self._color_hex}; font-size: {font_size(13)}; font-weight: bold;",
            self._accent_protection(),
        )
        self._graph.set_color(self._color_hex)

    def update_val(self, value: float, suffix: Optional[str] = None) -> None:
        self._graph.add_value(value)
        # Use a ~1 s moving average for the number so it stops jittering on
        # bursty workloads; the sparkline graph still reacts instantly.
        self._val_lbl.setText(suffix if suffix else f"{int(self._graph.recent_avg())}%")

    def batch_update(self) -> None:
        self._graph.batch_update()


class PowerTile(BaseTile):
    """Single sparkline for CPU-package or GPU power in watts."""

    def __init__(self, tile_id: str, title: str, color_hex: str,
                 baseline_watts: float, parent=None) -> None:
        self._title = title
        self._color_hex = color_hex
        self._baseline = max(float(baseline_watts), 1.0)
        self._peak_watts = self._baseline
        super().__init__(tile_id, color_hex, parent)

    def _build_content(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(dp(6), dp(5), dp(6), dp(5))
        outer.setSpacing(dp(2))

        header = QHBoxLayout()
        self._title_lbl = QLabel(self._title)
        self._title_lbl.setStyleSheet(
            f"color: {self._color_hex}; font-size: {font_size(13)}; font-weight: bold;")
        self._scale_lbl = QLabel(f"Skala {self._peak_watts:.0f} W")
        self._scale_lbl.setStyleSheet(f"color: #444; font-size: {font_size(10)};")
        self._value_lbl = QLabel("k.A.")
        self._value_lbl.setStyleSheet(
            f"color: {self._color_hex}; font-size: {font_size(14)}; font-weight: bold;")
        header.addWidget(self._title_lbl)
        header.addSpacing(dp(6))
        header.addWidget(self._scale_lbl)
        header.addStretch()
        header.addWidget(self._value_lbl)
        outer.addLayout(header)

        self._graph = SparklineWidget(self._color_hex)
        outer.addWidget(self._graph)

    def _apply_accent_colors(self) -> None:
        super()._apply_accent_colors()
        _set_themed_style(
            self._title_lbl,
            f"color: {self._color_hex}; font-size: {font_size(13)}; font-weight: bold;",
            self._accent_protection(),
        )
        _set_themed_style(
            self._value_lbl,
            f"color: {self._color_hex}; font-size: {font_size(14)}; font-weight: bold;",
            self._accent_protection(),
        )
        self._graph.set_color(self._color_hex)

    def update_power(self, watts: Optional[float]) -> None:
        if watts is None or not math.isfinite(watts) or watts < 0:
            # Absence is not a 0 W measurement; leave history untouched.
            self._value_lbl.setText("k.A.")
            return
        value = float(watts)
        target_peak = max(self._baseline, value * 1.15)
        if target_peak > self._peak_watts:
            self._peak_watts = math.ceil(target_peak / 25.0) * 25.0
        else:
            # Very slow release keeps the graph scale stable while still
            # recovering after a temporary over-power spike.
            self._peak_watts = max(self._baseline, self._peak_watts * 0.9995)
        self._graph.add_value(min(value / self._peak_watts * 100.0, 100.0))
        self._value_lbl.setText(f"{value:.0f} W" if value >= 100 else f"{value:.1f} W")
        self._scale_lbl.setText(f"Skala {self._peak_watts:.0f} W")

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
        self._name_lbl = QLabel(self._label)
        self._name_lbl.setStyleSheet(
            f"color: {DRIVE_R_COLOR}; font-size: {font_size(13)}; font-weight: bold;")
        self._peak_lbl = QLabel("↑100 MB/s")
        self._peak_lbl.setStyleSheet(f"color: #444; font-size: {font_size(11)};")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(3))
        hdr.addWidget(self._name_lbl)
        hdr.addStretch()
        hdr.addWidget(self._peak_lbl)
        outer.addLayout(hdr)

        # ── Read row ──────────────────────────────────────────────────────────
        r_row = QHBoxLayout()
        r_row.setSpacing(dp(4))
        self._r_lbl = QLabel("R")
        self._r_lbl.setStyleSheet(
            f"color: {DRIVE_R_COLOR}; font-size: {font_size(12)}; font-weight: bold;")
        _set_responsive_label_width(self._r_lbl, 12)
        self._r_graph = SparklineWidget(DRIVE_R_COLOR, min_height=24)
        self._r_val   = QLabel("0 MB/s")
        self._r_val.setStyleSheet(f"color: {DRIVE_R_COLOR}; font-size: {font_size(12)};")
        _set_responsive_label_width(self._r_val, 72)
        self._r_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)   # type: ignore
        r_row.addWidget(self._r_lbl)
        r_row.addWidget(self._r_graph)
        r_row.addWidget(self._r_val)
        outer.addLayout(r_row)

        # ── Write row ─────────────────────────────────────────────────────────
        w_row = QHBoxLayout()
        w_row.setSpacing(dp(4))
        self._w_lbl = QLabel("W")
        self._w_lbl.setStyleSheet(
            f"color: {DRIVE_W_COLOR}; font-size: {font_size(12)}; font-weight: bold;")
        _set_responsive_label_width(self._w_lbl, 12)
        self._w_graph = SparklineWidget(DRIVE_W_COLOR, min_height=24)
        self._w_val   = QLabel("0 MB/s")
        self._w_val.setStyleSheet(f"color: {DRIVE_W_COLOR}; font-size: {font_size(12)};")
        _set_responsive_label_width(self._w_val, 72)
        self._w_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)   # type: ignore
        w_row.addWidget(self._w_lbl)
        w_row.addWidget(self._w_graph)
        w_row.addWidget(self._w_val)
        outer.addLayout(w_row)

    def _apply_accent_colors(self) -> None:
        super()._apply_accent_colors()
        read_color = self._color_hex
        write_color = self._color_hex if self._custom_color_hex else DRIVE_W_COLOR
        _set_themed_style(
            self._name_lbl,
            f"color: {read_color}; font-size: {font_size(13)}; font-weight: bold;",
            self._accent_protection(),
        )
        for label, color, bold in (
            (self._r_lbl, read_color, True),
            (self._r_val, read_color, False),
            (self._w_lbl, write_color, True),
            (self._w_val, write_color, False),
        ):
            weight = " font-weight: bold;" if bold else ""
            _set_themed_style(
                label, f"color: {color}; font-size: {font_size(12)};{weight}",
                self._accent_protection())
        self._r_graph.set_color(read_color)
        self._w_graph.set_color(write_color)

    def update_drive(self, read_mbps: float, write_mbps: float) -> None:
        # Auto-scale: peak grows immediately with headroom, then decays slowly.
        peak = max(read_mbps, write_mbps, 1.0)
        if peak > self._peak:
            self._peak = peak * 1.1
        else:
            self._peak = max(1.0, self._peak * 0.998, peak)

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
        self._name_lbl = QLabel(f"{self._gpu_name} · Copy")
        self._name_lbl.setStyleSheet(
            f"color: {self._palette[1]}; font-size: {font_size(13)}; font-weight: bold;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(3))
        hdr.addWidget(self._name_lbl)
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── Copy0 row ─────────────────────────────────────────────────────────
        c0_row = QHBoxLayout()
        c0_row.setSpacing(dp(4))
        self._c0_lbl = QLabel("Cp0")
        self._c0_lbl.setStyleSheet(
            f"color: {self._palette[1]}; font-size: {font_size(12)}; font-weight: bold;")
        _set_responsive_label_width(self._c0_lbl, 28)
        self._c0_graph = SparklineWidget(self._palette[1], min_height=24)
        self._c0_val   = QLabel("0%")
        self._c0_val.setStyleSheet(f"color: {self._palette[1]}; font-size: {font_size(12)};")
        _set_responsive_label_width(self._c0_val, 34)
        self._c0_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        c0_row.addWidget(self._c0_lbl)
        c0_row.addWidget(self._c0_graph)
        c0_row.addWidget(self._c0_val)
        outer.addLayout(c0_row)

        # ── Copy1 row ─────────────────────────────────────────────────────────
        c1_row = QHBoxLayout()
        c1_row.setSpacing(dp(4))
        self._c1_lbl = QLabel("Cp1")
        self._c1_lbl.setStyleSheet(
            f"color: {self._palette[2]}; font-size: {font_size(12)}; font-weight: bold;")
        _set_responsive_label_width(self._c1_lbl, 28)
        self._c1_graph = SparklineWidget(self._palette[2], min_height=24)
        self._c1_val   = QLabel("0%")
        self._c1_val.setStyleSheet(f"color: {self._palette[2]}; font-size: {font_size(12)};")
        _set_responsive_label_width(self._c1_val, 34)
        self._c1_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        c1_row.addWidget(self._c1_lbl)
        c1_row.addWidget(self._c1_graph)
        c1_row.addWidget(self._c1_val)
        outer.addLayout(c1_row)

    def _apply_accent_colors(self) -> None:
        super()._apply_accent_colors()
        color0 = self._color_hex if self._custom_color_hex else self._palette[1]
        color1 = self._color_hex if self._custom_color_hex else self._palette[2]
        _set_themed_style(
            self._name_lbl,
            f"color: {color0}; font-size: {font_size(13)}; font-weight: bold;",
            self._accent_protection(),
        )
        for label, color, bold in (
            (self._c0_lbl, color0, True),
            (self._c0_val, color0, False),
            (self._c1_lbl, color1, True),
            (self._c1_val, color1, False),
        ):
            weight = " font-weight: bold;" if bold else ""
            _set_themed_style(
                label, f"color: {color}; font-size: {font_size(12)};{weight}",
                self._accent_protection())
        self._c0_graph.set_color(color0)
        self._c1_graph.set_color(color1)

    def update_copy(self, copy0: float, copy1: float) -> None:
        self._c0_graph.add_value(copy0)
        self._c1_graph.add_value(copy1)
        self._c0_val.setText(f"{int(self._c0_graph.recent_avg())}%")
        self._c1_val.setText(f"{int(self._c1_graph.recent_avg())}%")

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
        self._name_lbl = QLabel(f"{self._gpu_name} · Video Codec")
        self._name_lbl.setStyleSheet(
            f"color: {self._palette[3]}; font-size: {font_size(13)}; font-weight: bold;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(3))
        hdr.addWidget(self._name_lbl)
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── Codec row ─────────────────────────────────────────────────────────
        codec_row = QHBoxLayout()
        codec_row.setSpacing(dp(4))
        self._codec_lbl = QLabel("Codec")
        self._codec_lbl.setStyleSheet(
            f"color: {self._palette[3]}; font-size: {font_size(12)}; font-weight: bold;")
        _set_responsive_label_width(self._codec_lbl, 48)
        self._codec_graph = SparklineWidget(self._palette[3], min_height=24)
        self._codec_val   = QLabel("0%")
        self._codec_val.setStyleSheet(f"color: {self._palette[3]}; font-size: {font_size(12)};")
        _set_responsive_label_width(self._codec_val, 34)
        self._codec_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        codec_row.addWidget(self._codec_lbl)
        codec_row.addWidget(self._codec_graph)
        codec_row.addWidget(self._codec_val)
        outer.addLayout(codec_row)

    def _apply_accent_colors(self) -> None:
        super()._apply_accent_colors()
        for label, size, bold in (
            (self._name_lbl, 13, True),
            (self._codec_lbl, 12, True),
            (self._codec_val, 12, False),
        ):
            weight = " font-weight: bold;" if bold else ""
            _set_themed_style(
                label,
                f"color: {self._color_hex}; font-size: {font_size(size)};{weight}",
                self._accent_protection(),
            )
        self._codec_graph.set_color(self._color_hex)

    def update_codec(self, codec: float) -> None:
        self._codec_graph.add_value(codec)
        self._codec_val.setText(f"{int(self._codec_graph.recent_avg())}%")
        self._codec_val.setText(f"{int(codec)}%")

    def batch_update(self) -> None:
        self._codec_graph.batch_update()


# ═══════════════════════════════════════════════════════════════════════════════
# GPU ENGINE TILE  — separate 3D + Compute/CUDA engine graphs
# ═══════════════════════════════════════════════════════════════════════════════

class GPU3DComputeTile(BaseTile):
    """Two engine graphs kept separate from driver-native overall GPU load."""

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
        self._name_lbl = QLabel(f"{self._gpu_name} · 3D / Compute")
        self._name_lbl.setStyleSheet(
            f"color: {self._palette[0]}; font-size: {font_size(13)}; font-weight: bold;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(3))
        hdr.addWidget(self._name_lbl)
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── 3D row ────────────────────────────────────────────────────────────
        d3_row = QHBoxLayout()
        d3_row.setSpacing(dp(4))
        self._d3_lbl = QLabel("3D ")
        self._d3_lbl.setStyleSheet(
            f"color: {self._palette[0]}; font-size: {font_size(12)}; font-weight: bold;")
        _set_responsive_label_width(self._d3_lbl, 28)
        self._d3_graph = SparklineWidget(self._palette[0], min_height=24)
        self._d3_val   = QLabel("0%")
        self._d3_val.setStyleSheet(f"color: {self._palette[0]}; font-size: {font_size(12)};")
        _set_responsive_label_width(self._d3_val, 34)
        self._d3_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        d3_row.addWidget(self._d3_lbl)
        d3_row.addWidget(self._d3_graph)
        d3_row.addWidget(self._d3_val)
        outer.addLayout(d3_row)

        # ── Compute row ───────────────────────────────────────────────────────
        cm_row = QHBoxLayout()
        cm_row.setSpacing(dp(4))
        self._cm_lbl = QLabel("Cmp")
        self._cm_lbl.setStyleSheet(
            f"color: {self._palette[1]}; font-size: {font_size(12)}; font-weight: bold;")
        _set_responsive_label_width(self._cm_lbl, 28)
        self._cm_graph = SparklineWidget(self._palette[1], min_height=24)
        self._cm_val   = QLabel("0%")
        self._cm_val.setStyleSheet(f"color: {self._palette[1]}; font-size: {font_size(12)};")
        _set_responsive_label_width(self._cm_val, 34)
        self._cm_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # type: ignore
        cm_row.addWidget(self._cm_lbl)
        cm_row.addWidget(self._cm_graph)
        cm_row.addWidget(self._cm_val)
        outer.addLayout(cm_row)

    def _apply_accent_colors(self) -> None:
        super()._apply_accent_colors()
        color_3d = self._color_hex if self._custom_color_hex else self._palette[0]
        color_compute = self._color_hex if self._custom_color_hex else self._palette[1]
        _set_themed_style(
            self._name_lbl,
            f"color: {color_3d}; font-size: {font_size(13)}; font-weight: bold;",
            self._accent_protection(),
        )
        for label, color, bold in (
            (self._d3_lbl, color_3d, True),
            (self._d3_val, color_3d, False),
            (self._cm_lbl, color_compute, True),
            (self._cm_val, color_compute, False),
        ):
            weight = " font-weight: bold;" if bold else ""
            _set_themed_style(
                label, f"color: {color}; font-size: {font_size(12)};{weight}",
                self._accent_protection())
        self._d3_graph.set_color(color_3d)
        self._cm_graph.set_color(color_compute)

    def update_3d_compute(self, gpu_3d: float, compute: float) -> None:
        self._d3_graph.add_value(gpu_3d)
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
        self.setMinimumWidth(1)
        self.setMaximumWidth(dp(28))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(1)

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
            p.setPen(QPen(QColor(_theme_color("drop_border")), 1,
                          Qt.PenStyle.DashLine))      # type: ignore
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 4, 4)
            p.setPen(QColor(_theme_color("drop_text")))
        p.setPen(QColor("#444444") if self._hover
                 else QColor(_theme_color("drop_text")))
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
            p.setPen(QPen(QColor(_theme_color("drop_border")), 1,
                          Qt.PenStyle.DashLine))          # type: ignore
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
        # Qt already lays widgets out in device-independent coordinates; using
        # devicePixelRatio here doubled the wrap threshold on high-DPI screens.
        self._min_col_w  = max(1, min_col_w)
        self._max_cols   = max_cols   # 0 = no cap
        self._last_cols  = 0
        self._last_rows  = 0
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        for group in columns:
            for w in group:
                w.setParent(self)

        self._grid = QGridLayout(self)
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
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
        self._update_compact_spacing()

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
        self._last_rows = total_rows
        for r in range(total_rows):
            self._grid.setRowStretch(r, 1)
        for c in range(grid_cols):
            self._grid.setColumnStretch(c, 1)
        self._update_compact_spacing()

    def _update_compact_spacing(self) -> None:
        """Reduce gaps before they can displace active core widgets."""
        nominal = dp(6)
        horizontal = nominal
        vertical = nominal
        if self._last_cols > 1:
            horizontal = min(
                nominal,
                max(0, (self.width() - self._last_cols) // (self._last_cols - 1)),
            )
        if self._last_rows > 1:
            vertical = min(
                nominal,
                max(0, (self.height() - self._last_rows) // (self._last_rows - 1)),
            )
        self._grid.setHorizontalSpacing(horizontal)
        self._grid.setVerticalSpacing(vertical)


# ═══════════════════════════════════════════════════════════════════════════════
# TILE GRID  — manages draggable, hideable, reorderable tile layout
# ═══════════════════════════════════════════════════════════════════════════════

def _tile_rows(tile_order: List[str]) -> List[List[str]]:
    """Return non-empty rows from a flat tile order."""
    rows: List[List[str]] = [[]]
    for tile_id in tile_order:
        if tile_id == '__row__':
            if rows[-1]:
                rows.append([])
        else:
            rows[-1].append(tile_id)
    return [row for row in rows if row]


def _flatten_tile_rows(rows: List[List[str]]) -> List[str]:
    """Flatten non-empty rows with exactly one sentinel between them."""
    order: List[str] = []
    for row in rows:
        if not row:
            continue
        if order:
            order.append('__row__')
        order.extend(row)
    return order


def _move_tile_to_new_row(tile_order: List[str], tile_id: str,
                          after_row_idx: int) -> List[str]:
    """Move one tile transactionally into a new row at an original boundary.

    ``after_row_idx`` is emitted by an inter-row drop zone built from the
    pre-move layout.  Working on parsed rows avoids retaining a stale flat-list
    anchor when the dragged tile was itself the final tile in the target row.
    """
    rows = _tile_rows(tile_order)
    source_row_idx = next(
        (index for index, row in enumerate(rows) if tile_id in row), None)
    if source_row_idx is None:
        return list(tile_order)

    boundary = 0 if after_row_idx < 0 else min(after_row_idx + 1, len(rows))
    source_row = rows[source_row_idx]
    source_row.remove(tile_id)
    if not source_row:
        rows.pop(source_row_idx)
        if source_row_idx < boundary:
            boundary -= 1

    boundary = max(0, min(boundary, len(rows)))
    rows.insert(boundary, [tile_id])
    return _flatten_tile_rows(rows)


def _layout_anchor(tile_id: str) -> Optional[Tuple[str, bool]]:
    """Return ``(anchor_id, insert_before)`` for additive layout migrations."""
    if tile_id == "cpu_power":
        return "cpu_total", False
    match = re.fullmatch(r"gpu_(\d+)_(total|power)", tile_id)
    if not match:
        return None
    gpu_index, metric = match.groups()
    if metric == "total":
        return f"gpu_{gpu_index}_3d", True
    return f"gpu_{gpu_index}_vram", False


def _merge_layout_tiles(saved_order: List[str], saved_hidden: List[str],
                        tile_ids: List[str], default_order: List[str]
                        ) -> Tuple[List[str], List[str], bool]:
    """Merge new tiles and repair malformed state without losing custom rows.

    New overall-GPU tiles are inserted before their existing 3D/Compute tile;
    power tiles stay after CPU-total or GPU-VRAM.  Anchored tiles inherit a
    hidden anchor's state.  The two-pass merge first restores missing anchors,
    making recovery independent of registry order and idempotent.
    """
    valid_ids = set(tile_ids)
    order: List[str] = []
    visible: set[str] = set()
    last_was_break = True
    for item in saved_order:
        if item == '__row__':
            if not last_was_break:
                order.append(item)
            last_was_break = True
        elif item in valid_ids and item not in visible:
            order.append(item)
            visible.add(item)
            last_was_break = False
    while order and order[-1] == '__row__':
        order.pop()

    hidden: List[str] = []
    for item in saved_hidden:
        if item in valid_ids and item not in visible and item not in hidden:
            hidden.append(item)

    changed = order != saved_order or hidden != saved_hidden
    known = visible | set(hidden)
    default_ids = {item for item in default_order if item != '__row__'}
    anchored: List[str] = []

    for tile_id in tile_ids:
        if tile_id in known:
            continue
        if _layout_anchor(tile_id) is not None:
            anchored.append(tile_id)
            continue
        if tile_id in default_ids:
            # A previous failed move may have removed a semantic anchor while
            # leaving its dependent tile behind.  Restore the anchor beside that
            # visible dependent instead of dumping it after the final drive row.
            dependent = next((
                candidate for candidate in tile_ids
                if candidate in visible
                and (anchor_info := _layout_anchor(candidate)) is not None
                and anchor_info[0] == tile_id
            ), None)
            if dependent is None:
                order.append(tile_id)
            else:
                dependent_anchor = _layout_anchor(dependent)
                insert_after = bool(dependent_anchor and dependent_anchor[1])
                index = order.index(dependent) + (1 if insert_after else 0)
                order.insert(index, tile_id)
            visible.add(tile_id)
        else:
            hidden.append(tile_id)
        known.add(tile_id)
        changed = True

    for tile_id in anchored:
        anchor_info = _layout_anchor(tile_id)
        if anchor_info is None:
            continue
        anchor, insert_before = anchor_info
        if anchor in visible:
            index = order.index(anchor) + (0 if insert_before else 1)
            order.insert(index, tile_id)
            visible.add(tile_id)
        else:
            hidden.append(tile_id)
        known.add(tile_id)
        changed = True

    return order, hidden, changed


def _preserve_dormant_layout(active_order: List[str], active_hidden: List[str],
                              stored_order: List[str], stored_hidden: List[str],
                              active_ids: set[str]) -> Tuple[List[str], List[str]]:
    """Overlay active edits while retaining temporarily unavailable tile IDs.

    Dormant visible tiles follow their nearest still-visible neighbor from the
    stored row; fully dormant rows remain grouped at the end.  Hidden dormant
    tiles keep their hidden state.  This prevents a row-height change or other
    harmless save from deleting layouts for a disconnected GPU or drive.
    """
    rows = [list(row) for row in _tile_rows(active_order)]
    active_visible = {tile_id for tile_id in active_order
                      if tile_id != '__row__'}
    stored_rows = _tile_rows([
        item for item in stored_order if isinstance(item, str)])
    dormant_visible: set[str] = set()
    orphan_rows: List[List[str]] = []
    after_tails: Dict[str, str] = {}

    def locate(tile_id: str) -> Optional[Tuple[int, int]]:
        for row_index, row in enumerate(rows):
            if tile_id in row:
                return row_index, row.index(tile_id)
        return None

    for stored_row in stored_rows:
        index = 0
        while index < len(stored_row):
            if stored_row[index] in active_ids:
                index += 1
                continue
            start = index
            segment: List[str] = []
            while index < len(stored_row) and stored_row[index] not in active_ids:
                tile_id = stored_row[index]
                if tile_id != '__row__' and tile_id not in dormant_visible:
                    segment.append(tile_id)
                    dormant_visible.add(tile_id)
                index += 1
            if not segment:
                continue

            previous = next((
                tile_id for tile_id in reversed(stored_row[:start])
                if tile_id in active_visible
            ), None)
            following = next((
                tile_id for tile_id in stored_row[index:]
                if tile_id in active_visible
            ), None)

            if previous is not None:
                anchor = after_tails.get(previous, previous)
                location = locate(anchor)
                if location is not None:
                    row_index, item_index = location
                    rows[row_index][item_index + 1:item_index + 1] = segment
                    after_tails[previous] = segment[-1]
                    continue
            if following is not None:
                location = locate(following)
                if location is not None:
                    row_index, item_index = location
                    rows[row_index][item_index:item_index] = segment
                    continue
            orphan_rows.append(segment)

    rows.extend(orphan_rows)
    persisted_order = _flatten_tile_rows(rows)
    persisted_hidden = list(dict.fromkeys(
        tile_id for tile_id in active_hidden if tile_id in active_ids))
    for tile_id in stored_hidden:
        if (isinstance(tile_id, str)
                and tile_id not in active_ids
                and tile_id not in dormant_visible
                and tile_id not in persisted_hidden):
            persisted_hidden.append(tile_id)
    return persisted_order, persisted_hidden


def _factory_tile_row_heights() -> Tuple[int, int]:
    """Return the same row-height bounds used by a config-free launch."""
    scale = _DP_SCALE if _DP_SCALE > 0 else 1.0
    return int(75 * scale), int(180 * scale)


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
    When many rows are visible the preferred minimum shrinks; when space is
    available, every row expands equally with its section.  This keeps the
    global section compact on small screens and gap-free on tall windows.
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
        # Scale row heights by DPI — 75/180 are logical px for 100% DPI.
        self._min_row_h, self._max_row_h = _factory_tile_row_heights()
        # A visual row must remain present, but it must never force scrolling.
        # Headers/graphs may become compact in a very small window; every active
        # tile still receives geometry and grows back continuously with space.
        self._ROW_FLOOR  = 1
        self._row_widgets: List[QWidget] = []
        self._current_row_h = self._min_row_h  # dynamically adjusted

        for t in self._tiles.values():
            t.setParent(self)
            t.move_requested.connect(self._on_move)
            t.remove_requested.connect(self._on_hide)
            t.rowbreak_requested.connect(self._on_rowbreak)
            t.color_requested.connect(self._on_color_change)
            t.color_reset_requested.connect(self._on_color_reset)

        cfg = self._load_config()
        raw_order = cfg.get('tile_order')
        raw_hidden = cfg.get('hidden_tiles', [])
        raw_tile_colors = cfg.get('tile_colors', {})
        has_saved_layout = isinstance(raw_order, list)
        layout_changed = cfg.get('version') != CONFIG_VERSION

        self._tile_colors: Dict[str, str] = {}
        if not isinstance(raw_tile_colors, dict):
            raw_tile_colors = {}
            layout_changed = True
        for raw_id, raw_color in raw_tile_colors.items():
            color = _normalise_color_hex(raw_color)
            if (not isinstance(raw_id, str) or not raw_id
                    or len(raw_id) > 256 or color is None):
                layout_changed = True
                continue
            self._tile_colors[raw_id] = color
        for tile_id, tile in self._tiles.items():
            color = self._tile_colors.get(tile_id)
            if color is not None:
                tile.set_custom_color(color)
        if not isinstance(raw_hidden, list):
            raw_hidden = []
            layout_changed = True
        if has_saved_layout:
            saved_order = [tile_id for tile_id in raw_order
                           if tile_id == '__row__' or tile_id in self._tiles]
            saved_hidden = [tile_id for tile_id in raw_hidden if tile_id in self._tiles]
            saved_order, saved_hidden, merged = _merge_layout_tiles(
                saved_order, saved_hidden, list(self._tiles), default_order)
            self._tile_order = saved_order
            self._hidden = saved_hidden
            self._min_row_h = int(cfg.get('min_row_h', self._min_row_h))
            self._max_row_h = max(self._max_row_h, self._min_row_h)
            layout_changed = layout_changed or merged
        else:
            # A window-only config is not a custom layout.  Preserve the exact
            # factory rows rather than reconstructing one flat row from IDs.
            self._tile_order = list(default_order)
            self._hidden = [tile_id for tile_id in self._tiles
                            if tile_id not in default_order]
            layout_changed = True

        if layout_changed:
            persisted_order, persisted_hidden = _preserve_dormant_layout(
                self._tile_order, self._hidden,
                raw_order if isinstance(raw_order, list) else [], raw_hidden,
                set(self._tiles),
            )
            cfg.update({
                'min_row_h': self._min_row_h,
                'tile_order': persisted_order,
                'hidden_tiles': persisted_hidden,
                'tile_colors': self._tile_colors,
            })
            try:
                _save_config_file(cfg)
            except Exception as exc:
                # A read-only home/config target must not prevent app startup.
                logger.warning("Config migration save failed: %s", exc)

        self.setMinimumSize(0, 0)
        self._vbox = QVBoxLayout(self)
        self._vbox.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._vbox.setSpacing(dp(6))
        self._vbox.setContentsMargins(0, 0, 0, 0)
        self._relayout()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _parse_rows(self) -> List[List[str]]:
        """Split _tile_order into rows of tile IDs (sentinels consumed)."""
        return _tile_rows(self._tile_order)

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
            rw.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            rw.setMinimumHeight(self._current_row_h)
            hbox = QHBoxLayout(rw)
            hbox.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
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

    def _auto_adjust_row_height(self) -> None:
        """Compute an optimal row height from the widget's current height.

        Uses a DPI-scaled preferred minimum that favours compact layouts.  Row
        wrappers retain an expanding size policy and no maximum-height pin, so
        Qt divides surplus vertical space evenly without blank bands.
        """
        rows = self._parse_rows()
        n_rows = len(rows)
        if n_rows == 0:
            return
        # Available height: subtract layout spacing and fixed edit drop zones.
        # The row wrappers themselves stay vertically expanding: pinning their
        # maximum height to the preferred target made QVBoxLayout distribute
        # surplus height as large blank bands between tiles.
        available = self.height()
        drop_zone_widgets = [
            widget for widget in self._row_widgets
            if isinstance(widget, InterRowDropZone)
        ]
        drop_zones = len(drop_zone_widgets)
        item_count = n_rows + drop_zones
        gap_count = max(0, item_count - 1)
        spacing = dp(6)
        if gap_count:
            spacing = min(
                spacing,
                max(0, (available - item_count) // gap_count),
            )
        self._vbox.setSpacing(spacing)
        available = max(0, available - gap_count * spacing)

        if drop_zone_widgets:
            drop_height = min(
                dp(16),
                max(1, (available - n_rows * self._ROW_FLOOR) // drop_zones),
            )
            for widget in drop_zone_widgets:
                widget.setFixedHeight(drop_height)
            available = max(0, available - drop_zones * drop_height)

        target_h = min(
            self._max_row_h,
            max(int(110 * _DP_SCALE), self._min_row_h),
        )
        per_row = available // n_rows
        ideal = max(self._ROW_FLOOR, min(target_h, per_row))
        self._current_row_h = ideal

        for rw in self._row_widgets:
            if isinstance(rw, (RowDropZone, InterRowDropZone)):
                continue
            rw.setMinimumHeight(ideal)
            # Never cap an expanding row.  Qt now gives every row an equal
            # share of the available height, so tiles grow continuously in a
            # tall/maximised window instead of leaving gaps or jumping between
            # fixed-height states.
            rw.setMaximumHeight(16_777_215)

    # ── Edit mode ─────────────────────────────────────────────────────────────

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._relayout()   # rebuild to show/hide drop zones
        self._auto_adjust_row_height()

    def set_min_row_h(self, h: int) -> None:
        """Adjust minimum row height — tiles shrink/grow vertically."""
        # Clamp to DPI-scaled bounds
        self._min_row_h = max(int(50 * _DP_SCALE), min(h, int(400 * _DP_SCALE)))
        self._max_row_h = max(int(180 * _DP_SCALE), self._min_row_h)
        self._current_row_h = self._min_row_h
        self._auto_adjust_row_height()
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
        new_order = _move_tile_to_new_row(
            self._tile_order, tile_id, after_row_idx)
        if new_order == self._tile_order:
            return
        self._tile_order = new_order
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

    def _on_color_change(self, tile_id: str, color_hex: str) -> None:
        tile = self._tiles.get(tile_id)
        color = _normalise_color_hex(color_hex)
        if tile is None or color is None:
            return
        tile.set_custom_color(color)
        self._tile_colors[tile_id] = color
        self._save_config()

    def _on_color_reset(self, tile_id: str) -> None:
        tile = self._tiles.get(tile_id)
        if tile is None:
            return
        tile.set_custom_color(None)
        self._tile_colors.pop(tile_id, None)
        self._save_config()

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

    def _save_config(self, *, preserve_dormant: bool = True) -> None:
        try:
            data = _load_config_file()
            if preserve_dormant:
                stored_order = data.get('tile_order', [])
                stored_hidden = data.get('hidden_tiles', [])
                if not isinstance(stored_order, list):
                    stored_order = []
                if not isinstance(stored_hidden, list):
                    stored_hidden = []
                persisted_order, persisted_hidden = _preserve_dormant_layout(
                    self._tile_order, self._hidden, stored_order, stored_hidden,
                    set(self._tiles),
                )
            else:
                # A factory reset intentionally discards dormant hardware state,
                # matching a config-free launch instead of resurrecting old rows.
                persisted_order = list(self._tile_order)
                persisted_hidden = list(self._hidden)
            stored_colors = data.get('tile_colors', {})
            persisted_colors: Dict[str, str] = {}
            if isinstance(stored_colors, dict):
                for tile_id, raw_color in stored_colors.items():
                    color = _normalise_color_hex(raw_color)
                    if (isinstance(tile_id, str) and tile_id
                            and len(tile_id) <= 256 and color is not None):
                        persisted_colors[tile_id] = color
            for tile_id, tile in self._tiles.items():
                if tile.custom_color is None:
                    persisted_colors.pop(tile_id, None)
                else:
                    persisted_colors[tile_id] = tile.custom_color
            self._tile_colors = persisted_colors
            data.update({
                'min_row_h': self._min_row_h,
                'tile_order': persisted_order,
                'hidden_tiles': persisted_hidden,
                'tile_colors': persisted_colors,
            })
            _save_config_file(data)
        except Exception as exc:
            logger.warning("Config save failed: %s", exc)

    def reset_layout(self, default_order: List[str]) -> None:
        """Restore exactly the tile state used by a config-free launch."""
        self._tile_order = [
            tile_id for tile_id in default_order
            if tile_id == '__row__' or tile_id in self._tiles
        ]
        self._hidden = [
            tile_id for tile_id in self._tiles
            if tile_id not in self._tile_order
        ]
        self._min_row_h, self._max_row_h = _factory_tile_row_heights()
        self._current_row_h = self._min_row_h
        self._relayout()
        self._auto_adjust_row_height()
        self._save_config(preserve_dormant=False)


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
        _set_themed_style(self, """
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
            _set_themed_style(
                lbl, "color: #888; font-size: 11px; font-weight: bold;")
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
        self.setMinimumSize(0, 0)
        content.setMinimumSize(0, 0)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
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

def _app_start_dir() -> Path:
    """Return the directory the app was launched from (source or frozen exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _cleanup_previous_exe_backup() -> None:
    """Remove the rollback EXE only after the replacement app stayed alive."""
    if platform.system() != "Windows" or not getattr(sys, "frozen", False):
        return
    backup = Path(str(Path(sys.executable).resolve()) + ".previous")
    try:
        backup.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove successful update backup %s: %s", backup, exc)


def _is_system_tricorder_source(path: Path) -> bool:
    """Recognize the tracked project source without trusting only its filename."""
    try:
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return False
        source = path.read_text(encoding="utf-8")
        return (
            "DaWasteh/System-Tricorder" in source
            and "class TricorderDashboard(QMainWindow):" in source
            and "class UpdateWorker(QThread):" in source
        )
    except (OSError, UnicodeError):
        return False


def _find_git_checkout(start: Path) -> Optional[Path]:
    """Find the nearest verified System Tricorder checkout."""
    for candidate in (start, *start.parents):
        if ((candidate / ".git").exists()
                and _is_system_tricorder_source(
                    candidate / "system_tricorder.py")):
            return candidate
    return None


def _short_sha(sha: str) -> str:
    return sha[:7] if sha else "unknown"


def _version_tuple(value: str) -> Optional[Tuple[int, int, int]]:
    """Parse a stable ``vMAJOR.MINOR[.PATCH]`` version without guessing."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", value.strip())
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _validate_update_url(url: str) -> str:
    """Allow only HTTPS downloads hosted by GitHub's fixed update surface."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"Ungueltige Update-URL: {exc}") from exc
    if (parsed.scheme.lower() != "https" or not host
            or host not in _UPDATE_ALLOWED_HOSTS
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443)):
        raise RuntimeError("Update-URL liegt nicht auf einem erlaubten GitHub-Host")
    return url


def _git_blob_sha(data: bytes) -> str:
    """Return the Git object id used by GitHub's Contents API."""
    prefix = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(prefix + data, usedforsecurity=False).hexdigest()


class _VsFixedFileInfo(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "dwSignature", "dwStrucVersion", "dwFileVersionMS",
        "dwFileVersionLS", "dwProductVersionMS", "dwProductVersionLS",
        "dwFileFlagsMask", "dwFileFlags", "dwFileOS", "dwFileType",
        "dwFileSubtype", "dwFileDateMS", "dwFileDateLS",
    )]


def _windows_file_version(path: Path) -> Optional[Tuple[int, int, int, int]]:
    """Read a PE file version through the native Windows version API."""
    if os.name != "nt":
        return None
    version = ctypes.WinDLL("version", use_last_error=True)
    get_size = version.GetFileVersionInfoSizeW
    get_size.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]
    get_size.restype = wintypes.DWORD
    get_info = version.GetFileVersionInfoW
    get_info.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD,
                         ctypes.c_void_p]
    get_info.restype = ctypes.c_int
    query = version.VerQueryValueW
    query.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                      ctypes.POINTER(ctypes.c_void_p),
                      ctypes.POINTER(ctypes.c_uint)]
    query.restype = ctypes.c_int

    ignored = wintypes.DWORD(0)
    size = int(get_size(str(path), ctypes.byref(ignored)))
    if size <= 0:
        return None
    buffer = (ctypes.c_ubyte * size)()
    if not get_info(str(path), 0, size, buffer):
        return None
    value = ctypes.c_void_p()
    value_size = ctypes.c_uint(0)
    if not query(buffer, "\\", ctypes.byref(value), ctypes.byref(value_size)):
        return None
    if value_size.value < ctypes.sizeof(_VsFixedFileInfo):
        return None
    fixed = ctypes.cast(value, ctypes.POINTER(_VsFixedFileInfo)).contents
    if fixed.dwSignature != 0xFEEF04BD:
        return None
    return (
        int(fixed.dwFileVersionMS >> 16),
        int(fixed.dwFileVersionMS & 0xFFFF),
        int(fixed.dwFileVersionLS >> 16),
        int(fixed.dwFileVersionLS & 0xFFFF),
    )


def _install_staged_source_files(staging: Path, install_root: Path) -> bool:
    """Atomically install the allowlisted source files with rollback.

    Returns whether ``requirements.txt`` changed.  All paths are resolved under
    ``install_root`` so a symlink cannot redirect an update outside the app.
    """
    root = install_root.resolve()
    planned: List[Tuple[Path, Path, bool, Optional[int]]] = []
    requirements_changed = False
    for relative in _UPDATE_SOURCE_FILES:
        source = staging / relative
        if not source.is_file():
            raise RuntimeError(f"Update-Datei fehlt: {relative}")
        target = root / relative
        if not target.resolve(strict=False).is_relative_to(root):
            raise RuntimeError(f"Unsicherer Update-Pfad: {relative}")
        existed = target.is_file()
        if target.exists() and not existed:
            raise RuntimeError(f"Update-Ziel ist keine Datei: {relative}")
        if relative == "requirements.txt":
            old_data = target.read_bytes() if existed else b""
            requirements_changed = old_data != source.read_bytes()
        old_mode = stat.S_IMODE(target.stat().st_mode) if existed else None
        planned.append((source, target, existed, old_mode))

    token = f"{os.getpid()}-{threading.get_ident()}"
    installed: List[Tuple[Path, Path, Path, bool]] = []
    try:
        for source, target, existed, old_mode in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            candidate = target.with_name(f".{target.name}.update-{token}")
            backup = target.with_name(f".{target.name}.backup-{token}")
            candidate.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
            with source.open("rb") as src, candidate.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            if old_mode is not None:
                os.chmod(candidate, old_mode)
            installed.append((target, candidate, backup, existed))
            if existed:
                target.replace(backup)
            candidate.replace(target)
    except Exception:
        for target, candidate, backup, existed in reversed(installed):
            candidate.unlink(missing_ok=True)
            if backup.exists():
                target.unlink(missing_ok=True)
                backup.replace(target)
            elif not existed:
                target.unlink(missing_ok=True)
        raise

    # Every target now contains the same release, so the transaction is
    # committed.  Backup cleanup must never roll some files back after others'
    # backups have already been removed.
    for _, _, backup, _ in installed:
        try:
            backup.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove source update backup %s: %s", backup, exc)
    return requirements_changed


def _build_rebuild_bat(repo_root: Path, exe_path: Path, pid: int,
                       log_path: Path, pull_branch: str = "") -> str:
    """Build the Windows .bat that updates, rebuilds and relaunches the EXE.

    The helper waits for `pid` (the currently running Tricorder process) to
    exit so the .exe file lock is released, ensures a `.venv` exists (creating
    one in the repo folder if necessary), refreshes requirements + PyInstaller,
    rebuilds the `--noconsole --onefile` EXE as documented in the README,
    relaunches it, logs everything to `log_path`, and finally self-deletes.

    Only invoked on Windows when running from a frozen PyInstaller build.
    """
    lines = [
        "@echo off",
        "chcp 65001 >NUL",
        "set PYTHONUTF8=1",
        "setlocal enableextensions",
        f'set "REPO={repo_root}"',
        f'set "PID={pid}"',
        f'set "EXE={exe_path}"',
        f'set "LOG={log_path}"',
        f'set "PULL_BRANCH={pull_branch}"',
        'set "BUILT=%REPO%\\dist\\system_tricorder.exe"',
        'set "PYEXE=%REPO%\\.venv\\Scripts\\python.exe"',
        'echo [%date% %time%] tricorder rebuild helper started >> "%LOG%"',
        "",
        "REM --- Wait for the running Tricorder process to exit (max ~90s, then force-kill) ---",
        "set /a WAIT_N=0",
        ":waitloop",
        'tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL',
        "if errorlevel 1 goto procgone",
        "set /a WAIT_N+=1",
        "if %WAIT_N% GEQ 90 goto forcekill",
        "ping 127.0.0.1 -n 2 >NUL",
        "goto waitloop",
        ":forcekill",
        'echo [%date% %time%] force-killing PID %PID% >> "%LOG%"',
        "taskkill /PID %PID% /F >NUL 2>&1",
        ":procgone",
        'echo [%date% %time%] process gone, installing deferred update >> "%LOG%"',
        "",
        "REM --- Pull only after the running tracked EXE has released its lock ---",
        'if "%PULL_BRANCH%"=="" goto pull_done',
        "where git >NUL 2>&1",
        "if errorlevel 1 (",
        '    echo [%date% %time%] ERROR: git not found, aborting update >> "%LOG%"',
        "    goto cleanup",
        ")",
        'pushd "%REPO%"',
        f'git pull --ff-only --autostash "{GITHUB_REPO_URL}" "%PULL_BRANCH%" >> "%LOG%" 2>&1',
        'set "PULLRC=%errorlevel%"',
        "popd",
        'if not "%PULLRC%"=="0" (',
        '    echo [%date% %time%] ERROR: deferred git pull failed (rc=%PULLRC%) >> "%LOG%"',
        "    goto cleanup",
        ")",
        ":pull_done",
        "",
        "REM --- Ensure .venv exists (create one in the repo folder if missing) ---",
        'if not exist "%PYEXE%" (',
        '    echo [%date% %time%] creating .venv >> "%LOG%"',
        '    py -3 -m venv "%REPO%\\.venv" >> "%LOG%" 2>&1',
        "    if errorlevel 1 (",
        '        echo [%date% %time%] py launcher failed, trying python >> "%LOG%"',
        '        python -m venv "%REPO%\\.venv" >> "%LOG%" 2>&1',
        "    )",
        ")",
        'if not exist "%PYEXE%" (',
        '    echo [%date% %time%] ERROR: no .venv python found, aborting rebuild >> "%LOG%"',
        "    goto cleanup",
        ")",
        "",
        "REM --- Refresh deps + PyInstaller (requirements may have changed upstream) ---",
        'echo [%date% %time%] installing requirements + pyinstaller >> "%LOG%"',
        '"%PYEXE%" -m pip install --upgrade pip >> "%LOG%" 2>&1',
        '"%PYEXE%" -m pip install -r "%REPO%\\requirements.txt" pyinstaller >> "%LOG%" 2>&1',
        "",
        "REM --- Build from the tracked spec (icon, PNG and version metadata) ---",
        'echo [%date% %time%] building EXE >> "%LOG%"',
        'pushd "%REPO%"',
        '"%PYEXE%" -m PyInstaller --clean --noconfirm system_tricorder.spec >> "%LOG%" 2>&1',
        'set "BUILDRC=%errorlevel%"',
        "popd",
        'if not "%BUILDRC%"=="0" (',
        '    echo [%date% %time%] ERROR: PyInstaller failed (rc=%BUILDRC%) >> "%LOG%"',
        "    goto cleanup",
        ")",
        "",
        "REM --- Replace a renamed checkout EXE with the freshly built binary ---",
        'if not exist "%BUILT%" (',
        '    echo [%date% %time%] ERROR: built EXE missing: %BUILT% >> "%LOG%"',
        "    goto cleanup",
        ")",
        'if /I not "%BUILT%"=="%EXE%" (',
        '    copy /Y "%BUILT%" "%EXE%" >> "%LOG%" 2>&1',
        "    if errorlevel 1 (",
        '        echo [%date% %time%] ERROR: could not replace %EXE% >> "%LOG%"',
        "        goto cleanup",
        "    )",
        ")",
        "",
        "REM --- Relaunch the exact executable path that initiated the update ---",
        'if exist "%EXE%" (',
        '    echo [%date% %time%] relaunching %EXE% >> "%LOG%"',
        '    start "" "%EXE%"',
        ") else (",
        '    echo [%date% %time%] WARNING: %EXE% missing after build >> "%LOG%"',
        ")",
        "",
        ":cleanup",
        'echo [%date% %time%] helper done, self-deleting >> "%LOG%"',
        '(goto) 2>NUL & del "%~f0"',
    ]
    return "\r\n".join(lines) + "\r\n"


def _build_standalone_exe_update_bat(
    exe_path: Path, update_path: Path, pid: int, log_path: Path,
) -> str:
    """Build a rollback-capable helper for a downloaded Windows release EXE."""
    for path in (exe_path, update_path, log_path):
        if any(character in str(path) for character in ("%", "\r", "\n")):
            raise ValueError("Windows update helper path contains unsafe characters")
    if pid <= 0:
        raise ValueError("Windows update helper PID must be positive")
    lines = [
        "@echo off",
        "chcp 65001 >NUL",
        "set PYTHONUTF8=1",
        "setlocal enableextensions",
        f'set "PID={pid}"',
        f'set "EXE={exe_path}"',
        f'set "UPDATE={update_path}"',
        f'set "LOG={log_path}"',
        'for %%D in ("%UPDATE%") do set "UPDATEDIR=%%~dpD"',
        'set "NEW=%EXE%.update-new"',
        'set "BACKUP=%EXE%.previous"',
        'echo [%date% %time%] standalone update helper started >> "%LOG%"',
        "",
        "REM --- Wait until Windows releases the running EXE (max ~90s) ---",
        "set /a WAIT_N=0",
        ":waitloop",
        'tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL',
        "if errorlevel 1 goto procgone",
        "set /a WAIT_N+=1",
        "if %WAIT_N% GEQ 90 goto forcekill",
        "ping 127.0.0.1 -n 2 >NUL",
        "goto waitloop",
        ":forcekill",
        'echo [%date% %time%] force-killing PID %PID% >> "%LOG%"',
        "taskkill /PID %PID% /F >NUL 2>&1",
        ":procgone",
        'echo [%date% %time%] process gone, replacing EXE >> "%LOG%"',
        "",
        'if not exist "%UPDATE%" (',
        '    echo [%date% %time%] ERROR: verified download is missing >> "%LOG%"',
        "    goto cleanup",
        ")",
        'del /F /Q "%NEW%" >NUL 2>&1',
        'copy /Y /B "%UPDATE%" "%NEW%" >> "%LOG%" 2>&1',
        "if errorlevel 1 (",
        '    echo [%date% %time%] ERROR: staging next to EXE failed >> "%LOG%"',
        "    goto cleanup",
        ")",
        'del /F /Q "%BACKUP%" >NUL 2>&1',
        'move /Y "%EXE%" "%BACKUP%" >> "%LOG%" 2>&1',
        "if errorlevel 1 (",
        '    echo [%date% %time%] ERROR: current EXE could not be backed up >> "%LOG%"',
        "    goto cleanup",
        ")",
        'move /Y "%NEW%" "%EXE%" >> "%LOG%" 2>&1',
        "if errorlevel 1 (",
        '    echo [%date% %time%] ERROR: replacement failed, restoring backup >> "%LOG%"',
        '    move /Y "%BACKUP%" "%EXE%" >> "%LOG%" 2>&1',
        "    goto cleanup",
        ")",
        "",
        'echo [%date% %time%] relaunching verified release >> "%LOG%"',
        'start "" "%EXE%"',
        "if errorlevel 1 (",
        '    echo [%date% %time%] ERROR: relaunch failed, restoring previous EXE >> "%LOG%"',
        '    del /F /Q "%EXE%" >NUL 2>&1',
        '    move /Y "%BACKUP%" "%EXE%" >> "%LOG%" 2>&1',
        ")",
        "",
        ":cleanup",
        'del /F /Q "%NEW%" >NUL 2>&1',
        'del /F /Q "%UPDATE%" >NUL 2>&1',
        'rd "%UPDATEDIR%" >NUL 2>&1',
        'echo [%date% %time%] helper done, self-deleting >> "%LOG%"',
        '(goto) 2>NUL & del "%~f0"',
    ]
    return "\r\n".join(lines) + "\r\n"


class UpdateWorker(QThread):
    """Update Git checkouts, standalone Python copies, and Windows release EXEs.

    Git installations keep their fast-forward/autostash workflow.  A standalone
    source copy is updated from immutable release-tag blobs, while a downloaded
    Windows EXE is replaced only after GitHub's SHA-256, PE version, and frozen
    self-test all pass.  ``CONFIG_FILE`` lives outside every install target.
    """

    update_finished = pyqtSignal(bool, str)
    _MAX_JSON_BYTES = 2 * 1024 * 1024

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rebuild_needed = False
        self.deferred_pull_branch = ""
        self.prepared_exe_update = ""
        self.target_version = ""
        self.requirements_changed = False

    def run(self) -> None:                                                # type: ignore[override]
        self.rebuild_needed = False
        self.deferred_pull_branch = ""
        self.prepared_exe_update = ""
        self.target_version = ""
        self.requirements_changed = False
        try:
            ok, message = self._check_and_install()
        except Exception as exc:
            ok = False
            message = f"Update fehlgeschlagen: {exc}"
        self.update_finished.emit(ok, message)

    @staticmethod
    def _request_headers(accept: str) -> Dict[str, str]:
        return {
            "Accept": accept,
            "User-Agent": f"SystemTricorder/{APP_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request_json(self, url: str, timeout: int = 30) -> dict:
        _validate_update_url(url)
        request = urllib.request.Request(
            url, headers=self._request_headers("application/vnd.github+json"))
        payload = b""
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    _validate_update_url(response.geturl())
                    raw_length = response.headers.get("Content-Length")
                    if raw_length:
                        try:
                            if int(raw_length) > self._MAX_JSON_BYTES:
                                raise RuntimeError(
                                    "GitHub-Antwort ist unerwartet gross")
                        except ValueError as exc:
                            raise RuntimeError(
                                "Ungueltige Content-Length von GitHub") from exc
                    payload = response.read(self._MAX_JSON_BYTES + 1)
                break
            except (urllib.error.URLError, ConnectionError,
                    TimeoutError, OSError):
                if attempt >= 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
        if len(payload) > self._MAX_JSON_BYTES:
            raise RuntimeError("GitHub-Antwort ist unerwartet gross")
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("GitHub lieferte keine gueltige JSON-Antwort") from exc
        if not isinstance(data, dict):
            raise RuntimeError("GitHub-Antwort hat ein unerwartetes Format")
        return data

    def _download_to_path(
        self, url: str, destination: Path, *, expected_size: int,
        max_size: int, expected_sha256: str = "", timeout: int = 120,
    ) -> str:
        """Stream one bounded GitHub file and atomically publish it locally."""
        _validate_update_url(url)
        if expected_size <= 0 or expected_size > max_size:
            raise RuntimeError("Update-Dateigroesse liegt ausserhalb des erlaubten Bereichs")
        expected_hash = expected_sha256.lower()
        if expected_hash and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
            raise RuntimeError("GitHub-Release enthaelt keinen gueltigen SHA-256")

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        request = urllib.request.Request(
            url, headers=self._request_headers("application/octet-stream"))
        for attempt in range(3):
            partial.unlink(missing_ok=True)
            digest = hashlib.sha256()
            total = 0
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    _validate_update_url(response.geturl())
                    raw_length = response.headers.get("Content-Length")
                    if raw_length:
                        try:
                            response_size = int(raw_length)
                        except ValueError as exc:
                            raise RuntimeError(
                                "Ungueltige Content-Length im Download") from exc
                        if response_size != expected_size or response_size > max_size:
                            raise RuntimeError("GitHub-Downloadgroesse stimmt nicht")
                    with partial.open("xb") as output:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > max_size or total > expected_size:
                                raise RuntimeError(
                                    "GitHub-Download ueberschreitet die erwartete Groesse")
                            output.write(chunk)
                            digest.update(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                if total != expected_size:
                    partial.unlink(missing_ok=True)
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise RuntimeError(
                        f"GitHub-Download ist unvollstaendig ({total}/{expected_size} Bytes)")
                actual_hash = digest.hexdigest()
                if expected_hash and actual_hash != expected_hash:
                    raise RuntimeError(
                        "SHA-256-Pruefung des GitHub-Downloads fehlgeschlagen")
                partial.replace(destination)
                return actual_hash
            except (urllib.error.URLError, ConnectionError,
                    TimeoutError, OSError):
                partial.unlink(missing_ok=True)
                if attempt >= 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
            except Exception:
                partial.unlink(missing_ok=True)
                raise
        raise RuntimeError("GitHub-Download konnte nicht abgeschlossen werden")

    def _latest_release(self) -> Tuple[dict, str, Tuple[int, int, int]]:
        release = self._request_json(f"{GITHUB_API_URL}/releases/latest")
        tag = str(release.get("tag_name") or "").strip()
        version = _version_tuple(tag)
        if (version is None or release.get("draft") is True
                or release.get("prerelease") is True):
            raise RuntimeError("GitHub lieferte kein gueltiges stabiles Release")
        return release, tag, version

    def _run_git(self, repo_root: Path, args: List[str], timeout: int = 60) -> str:
        cmd = ["git", "-C", str(repo_root), *args]
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            raise RuntimeError(err or out or f"git {' '.join(args)} returned {proc.returncode}")
        return out

    def _remote_sha_for_branch(self, branch: str) -> Tuple[str, str]:
        refs = [f"refs/heads/{branch}"] if branch else []
        if branch != "main":
            refs.append("refs/heads/main")
        refs.append("HEAD")

        last_error = ""
        for ref in refs:
            proc = subprocess.run(
                ["git", "ls-remote", GITHUB_REPO_URL, ref],
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if proc.returncode != 0:
                last_error = (proc.stderr or proc.stdout or "").strip()
                continue
            line = (proc.stdout or "").strip().splitlines()
            if not line:
                continue
            sha = line[0].split()[0].strip()
            resolved_branch = branch if ref == f"refs/heads/{branch}" else "main"
            return sha, resolved_branch
        raise RuntimeError(last_error or "GitHub-Remote konnte nicht gelesen werden")

    def _check_git_checkout(self, repo_root: Path) -> Tuple[bool, str]:
        branch = self._run_git(repo_root, ["branch", "--show-current"]) or "main"
        current_sha = self._run_git(repo_root, ["rev-parse", "HEAD"])
        remote_sha, remote_branch = self._remote_sha_for_branch(branch)

        if current_sha == remote_sha:
            return (
                True,
                f"System Tricorder ist aktuell ({_short_sha(current_sha)}). "
                f"Lokale Settings bleiben in {CONFIG_FILE}.",
            )

        if platform.system() == "Windows" and getattr(sys, "frozen", False):
            # The tracked EXE can be part of the pull and is locked while this
            # process runs.  Pull/rebuild only after the app has closed.
            self.deferred_pull_branch = remote_branch
            self.rebuild_needed = True
            return (
                True,
                "Update bereit: "
                f"{_short_sha(current_sha)} -> {_short_sha(remote_sha)}. "
                "Download und EXE-Neubau starten nach dem Schliessen. "
                f"Lokale Settings bleiben unveraendert ({CONFIG_FILE}).",
            )

        self._run_git(
            repo_root,
            ["pull", "--ff-only", "--autostash", GITHUB_REPO_URL, remote_branch],
            timeout=180,
        )
        new_sha = self._run_git(repo_root, ["rev-parse", "HEAD"])
        rebuild_changed = self._rebuild_inputs_changed(
            repo_root, current_sha, new_sha)
        self.rebuild_needed = rebuild_changed
        if rebuild_changed:
            extra = (
                " App- oder Paketdateien haben sich geaendert - bitte die App "
                "neu starten bzw. das native Paket neu bauen."
            )
        else:
            extra = " Keine rebuild-relevanten Aenderungen - kein Paket-Rebuild noetig."
        return (
            True,
            "Update installiert: "
            f"{_short_sha(current_sha)} -> {_short_sha(new_sha)}. "
            f"Lokale Settings wurden nicht veraendert ({CONFIG_FILE})."
            + extra,
        )

    def _validate_windows_candidate(
        self, candidate: Path, release_version: Tuple[int, int, int],
    ) -> None:
        with candidate.open("rb") as executable:
            if executable.read(2) != b"MZ":
                raise RuntimeError("GitHub-Release ist keine gueltige Windows-EXE")
        file_version = _windows_file_version(candidate)
        if file_version is None or file_version[:3] != release_version:
            raise RuntimeError("EXE-Dateiversion stimmt nicht mit dem GitHub-Release ueberein")

        isolated_home = candidate.parent / "self-test-home"
        shutil.rmtree(isolated_home, ignore_errors=True)
        isolated_home.mkdir(parents=True)
        env = os.environ.copy()
        env.update({
            "HOME": str(isolated_home),
            "USERPROFILE": str(isolated_home),
            "TEMP": str(isolated_home),
            "TMP": str(isolated_home),
            "QT_QPA_PLATFORM": "offscreen",
        })
        try:
            proc = subprocess.run(
                [str(candidate), "--self-test"],
                cwd=str(candidate.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=90,
                check=False,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Self-Test der neuen EXE schlug fehl (Code {proc.returncode})")
        finally:
            shutil.rmtree(isolated_home, ignore_errors=True)

    def _prepare_standalone_exe_update(
        self, release: dict, tag: str, release_version: Tuple[int, int, int],
    ) -> None:
        machine = platform.machine().lower()
        if machine not in ("amd64", "x86_64"):
            raise RuntimeError(f"Kein Windows-Release fuer Architektur {machine or 'unknown'}")
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise RuntimeError("GitHub-Release enthaelt keine Assets")
        matches = [asset for asset in assets if isinstance(asset, dict)
                   and asset.get("name") == GITHUB_WINDOWS_ASSET]
        if len(matches) != 1:
            raise RuntimeError(f"Release-Asset fehlt: {GITHUB_WINDOWS_ASSET}")
        asset = matches[0]
        size = asset.get("size")
        digest = str(asset.get("digest") or "").lower()
        url = str(asset.get("browser_download_url") or "")
        if not isinstance(size, int) or not 5_000_000 <= size <= 200_000_000:
            raise RuntimeError("Windows-Release hat eine unplausible Groesse")
        match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest)
        if match is None:
            raise RuntimeError("Windows-Release besitzt keinen SHA-256-Digest")
        _validate_update_url(url)
        expected_path = f"/{GITHUB_REPO}/releases/download/{tag}/{GITHUB_WINDOWS_ASSET}"
        if urllib.parse.unquote(urllib.parse.urlparse(url).path).lower() != expected_path.lower():
            raise RuntimeError("Windows-Release verweist auf einen unerwarteten Downloadpfad")

        exe_path = Path(sys.executable).resolve()
        probe = exe_path.parent / f".tricorder-update-probe-{os.getpid()}.tmp"
        try:
            probe.write_bytes(b"update-write-test")
        except OSError as exc:
            raise RuntimeError(
                "Der EXE-Ordner ist nicht beschreibbar; Update bitte als Benutzer "
                "mit Schreibrechten starten") from exc
        finally:
            probe.unlink(missing_ok=True)

        update_dir = Path(tempfile.mkdtemp(prefix="system-tricorder-update-"))
        candidate = update_dir / GITHUB_WINDOWS_ASSET
        try:
            self._download_to_path(
                url, candidate, expected_size=size, max_size=200_000_000,
                expected_sha256=match.group(1), timeout=300,
            )
            self._validate_windows_candidate(candidate, release_version)
            self.prepared_exe_update = str(candidate)
            self.target_version = tag
        except Exception:
            shutil.rmtree(update_dir, ignore_errors=True)
            raise

    def _stage_source_release(
        self, tag: str, release_version: Tuple[int, int, int],
    ) -> Path:
        limits = {
            "system_tricorder.py": 2 * 1024 * 1024,
            "requirements.txt": 128 * 1024,
            "assets/SystemTricorder.png": 8 * 1024 * 1024,
        }
        staging = Path(tempfile.mkdtemp(prefix="system-tricorder-source-update-"))
        try:
            for relative in _UPDATE_SOURCE_FILES:
                encoded_path = urllib.parse.quote(relative, safe="/")
                query = urllib.parse.urlencode({"ref": tag})
                metadata = self._request_json(
                    f"{GITHUB_API_URL}/contents/{encoded_path}?{query}")
                size = metadata.get("size")
                blob_sha = str(metadata.get("sha") or "").lower()
                download_url = str(metadata.get("download_url") or "")
                if (metadata.get("type") != "file" or metadata.get("path") != relative
                        or not isinstance(size, int) or not 0 < size <= limits[relative]
                        or re.fullmatch(r"[0-9a-f]{40}", blob_sha) is None):
                    raise RuntimeError(f"Ungueltige GitHub-Metadaten fuer {relative}")
                _validate_update_url(download_url)
                expected_raw_path = f"/{GITHUB_REPO}/{tag}/{relative}"
                raw_path = urllib.parse.unquote(
                    urllib.parse.urlparse(download_url).path)
                if raw_path.lower() != expected_raw_path.lower():
                    raise RuntimeError(f"Unerwarteter GitHub-Quellpfad fuer {relative}")
                destination = staging / relative
                self._download_to_path(
                    download_url, destination, expected_size=size,
                    max_size=limits[relative], timeout=120,
                )
                if _git_blob_sha(destination.read_bytes()) != blob_sha:
                    raise RuntimeError(f"Git-Blob-Pruefung fehlgeschlagen: {relative}")

            source_text = (staging / "system_tricorder.py").read_text(
                encoding="utf-8")
            release_text = tag[1:] if tag.lower().startswith("v") else tag
            if _version_tuple(release_text) != release_version:
                raise RuntimeError("Release-Version wurde waehrend des Updates veraendert")
            version_pattern = re.compile(
                rf'^APP_VERSION\s*=\s*["\']{re.escape(release_text)}["\']\s*$',
                re.MULTILINE,
            )
            if (version_pattern.search(source_text) is None
                    or "class UpdateWorker(QThread):" not in source_text):
                raise RuntimeError("Heruntergeladene Python-Quelle ist unvollstaendig")
            requirements = (staging / "requirements.txt").read_text(
                encoding="utf-8")
            if "PyQt6" not in requirements or "psutil" not in requirements:
                raise RuntimeError("Heruntergeladene requirements.txt ist unplausibel")
            png = (staging / "assets" / "SystemTricorder.png").read_bytes()
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("Heruntergeladenes App-Icon ist keine PNG-Datei")
            return staging
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _install_standalone_source_update(
        self, tag: str, release_version: Tuple[int, int, int],
    ) -> None:
        staging = self._stage_source_release(tag, release_version)
        try:
            self.requirements_changed = _install_staged_source_files(
                staging, _app_start_dir())
            self.target_version = tag
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _check_and_install(self) -> Tuple[bool, str]:
        repo_root = _find_git_checkout(_app_start_dir())
        if repo_root is not None:
            return self._check_git_checkout(repo_root)

        release, tag, release_version = self._latest_release()
        current_version = _version_tuple(APP_VERSION)
        if current_version is None:
            raise RuntimeError(f"Lokale App-Version ist ungueltig: {APP_VERSION}")
        if release_version <= current_version:
            return (
                True,
                f"System Tricorder ist aktuell (v{APP_VERSION}). "
                f"Lokale Settings bleiben in {CONFIG_FILE}.",
            )

        if getattr(sys, "frozen", False):
            if platform.system() != "Windows":
                return (
                    False,
                    "Automatische Standalone-Paketupdates werden derzeit nur "
                    "fuer die Windows-EXE angeboten. Python-/Git-Installationen "
                    "koennen direkt aktualisiert werden.",
                )
            self._prepare_standalone_exe_update(release, tag, release_version)
            return (
                True,
                f"Verifiziertes GitHub-Release {tag} wurde heruntergeladen. "
                "Die EXE wird nach dem Schliessen atomar ersetzt und neu gestartet. "
                f"Lokale Settings bleiben unveraendert ({CONFIG_FILE}).",
            )

        self._install_standalone_source_update(tag, release_version)
        dependency_note = (
            " requirements.txt wurde aktualisiert; bei fehlenden Modulen bitte "
            "'python -m pip install -r requirements.txt' ausfuehren."
            if self.requirements_changed else ""
        )
        return (
            True,
            f"Standalone-Python wurde auf {tag} aktualisiert. Bitte die App neu "
            f"starten. Lokale Settings blieben unveraendert ({CONFIG_FILE})."
            + dependency_note,
        )

    def _rebuild_inputs_changed(self, repo_root: Path,
                                old_sha: str, new_sha: str) -> bool:
        """Return whether source or packaging inputs changed between commits."""
        if not old_sha or not new_sha or old_sha == new_sha:
            return False
        try:
            out = self._run_git(
                repo_root, ["diff", "--name-only", old_sha, new_sha], timeout=30)
        except Exception:
            return True  # if the diff fails, assume a rebuild is safer
        for line in out.splitlines():
            path = line.strip().replace('\\', '/').lower()
            if (path.endswith(('.py', '.spec'))
                    or path == 'requirements.txt'
                    or path.startswith('assets/')):
                return True
        return False


def section_label(html: str) -> QLabel:
    lbl = QLabel(html)
    lbl.setStyleSheet("background: transparent; padding: 2px 0;")
    return lbl


def _toolbar_btn(text: str, checkable: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCheckable(checkable)
    _set_themed_style(btn, f"""
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
# MAIN DASHBOARD  v2.7.6
# ═══════════════════════════════════════════════════════════════════════════════

class TricorderDashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self._theme = _set_active_theme(
            _load_config_file().get('theme', 'dark'))
        _set_windows_titlebar_theme(self, self._theme)

        self.setWindowTitle(f"System Tricorder v{APP_VERSION}")
        self.setWindowIcon(_app_icon())
        # Qt window coordinates are already device-independent.  Keep only a
        # compact usability floor; active tiles scale to fit without scrollbars.
        self.setMinimumSize(640, 360)
        _set_themed_style(self, """
            QMainWindow, QWidget {
                background-color: #0a0a0f;
                color: white;
            }
            QComboBox {
                background: #1e1e2e; color: #aaa;
                border: 1px solid #333; border-radius: 5px;
                padding: 4px 8px;
            }
            QComboBox:hover { background: #2a2a3a; color: #fff; }
            QComboBox QAbstractItemView {
                background: #1e1e2e; color: #ccc;
                selection-background-color: #2e2e3e;
            }
            QToolTip {
                background: #1e1e2e; color: #ccc; border: 1px solid #333;
            }
        """)

        self._analyze_hardware()

        self._tiles:      Dict[str, BaseTile]   = {}
        self._tile_names: Dict[str, str]         = {}
        self.thread_widgets: Dict[int, MasterMetricBox] = {}
        self._default_tile_order: List[str] = []

        # Auto-fit state (see _fit_window_to_content / _apply_fill_mode).
        self._fitting: bool = False
        self._fit_mode: str = 'auto'      # 'auto' (window tracks content) | 'fill' (user-controlled)
        self._last_size: Optional[Tuple[int, int]] = None
        self._restored_window_geometry = False
        self._pending_resize_action = 'fill'
        self._resize_settle_timer = QTimer(self)
        self._resize_settle_timer.setSingleShot(True)
        self._resize_settle_timer.setInterval(50)
        self._resize_settle_timer.timeout.connect(self._settle_responsive_layout)

        self._setup_ui()
        _apply_theme_tree(self)
        self._update_clock_layout(self.width())

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()

        self.hw_thread = HardwareMonitorThread(
            drive_info=self._drive_info, dgpu_info=self.detected_gpus)
        self._metric_frames = 0
        self.hw_thread.start()

        # Poll the monitor thread's latest-value slot at ~30 FPS.  If a UI
        # update overruns the interval, the next tick simply picks up the
        # newest frame — no event-queue backlog, no replayed stale frames.
        self._metrics_timer = QTimer(self)
        self._metrics_timer.timeout.connect(self._poll_metrics)
        self._metrics_timer.start(33)

    # ── Hardware analysis ──────────────────────────────────────────────────────

    def _analyze_hardware(self) -> None:
        self.c_physical = psutil.cpu_count(logical=False) or 4
        self.c_logical  = psutil.cpu_count(logical=True)  or 4
        cpu_brand = platform.processor()
        if platform.system() == 'Linux':
            with contextlib.suppress(Exception):
                txt = Path('/proc/cpuinfo').read_text(errors='ignore')
                m = re.search(r'^(?:model name|Hardware|Processor|vendor_id)\s*:\s*(.+)$', txt, re.M)
                if m:
                    cpu_brand = m.group(1)
        self.is_amd     = "AMD" in cpu_brand or "AuthenticAMD" in cpu_brand

        self.is_hybrid  = False
        self.has_ht     = False
        self.p_cores    = 0
        self.e_cores    = 0
        self.p_threads  = self.c_logical
        self.e_threads  = 0
        # Real logical-CPU indices per core (from topology).  Defaults keep the
        # non-hybrid / fallback paths working; _build_hybrid_cores registers the
        # per-thread widgets under these TRUE indices so the per-frame update
        # loop feeds each bar the correct core's load.
        self.p_logical: List[List[int]] = []
        self.e_logical: List[int]        = []

        topo = _get_cpu_topology()
        if topo and not self.is_amd:
            self.is_hybrid = topo['is_hybrid']
            self.p_cores   = topo['p_cores']
            self.e_cores   = topo['e_cores']
            self.p_threads = topo['p_threads']
            self.e_threads = topo['e_threads']
            self.p_logical = topo.get('p_logical', [])
            self.e_logical = topo.get('e_logical', [])

        if not self.is_hybrid:
            self.has_ht = (self.c_logical == 2 * self.c_physical)

        self.num_sockets = 1
        com_initialized = False
        wmi = None
        m = None
        if WMI_AVAILABLE:
            try:
                pythoncom.CoInitialize()                            # type: ignore
                com_initialized = True
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
        m = None
        wmi = None
        if com_initialized:
            with contextlib.suppress(Exception):
                pythoncom.CoUninitialize()                          # type: ignore

        self.detected_gpus: List[Tuple[str, float, str]] = []
        current_platform = platform.system()
        self.current_platform = current_platform
        if current_platform == 'Linux':
            linux_gpus = _linux_detect_gpus()
            self.has_igpu = any(g.get("is_igpu", False) for g in linux_gpus)
            for g in linux_gpus:
                if g.get("is_igpu", False):
                    continue
                vram = float(g.get("vram_total_gb") or 0.0)
                # Keep a sane display total until NVML/sysfs fills the real value.
                if vram <= 0 and g.get("vendor") in ("amd", "nvidia"):
                    vram = 8.0
                self.detected_gpus.append((
                    str(g.get("name") or "GPU"),
                    vram,
                    str(g.get("pci_slot") or g.get("device_id") or ""),
                ))
            # If the machine really has no separately detected dGPU, do not add
            # a fake Linux dGPU tile beside the real iGPU tile.  If GPU
            # detection failed entirely, keep the old fallback so the UI still
            # has a GPU section instead of crashing.
            if not self.detected_gpus and not self.has_igpu:
                self.detected_gpus = [("GPU", 8.0, "")]
        elif current_platform == 'Windows':
            # Shared detection with HardwareMonitorThread._init_windows so the
            # tiles and the metrics stream always describe the same GPU list
            # (incl. TCC-mode NVIDIA cards that only NVML can see).
            # has_igpu: the iGPU tile is only meaningful when the hardware
            # actually exists — a desktop AMD CPU (e.g. Ryzen 5800X3D) has
            # none, and a fake tile would sit at 0% forever.
            self.detected_gpus, self.has_igpu = _windows_detect_dgpus()
        else:
            # macOS has no DRM/PDH equivalent here yet. Keep CPU, RAM and disk
            # monitoring available without presenting a permanently fake GPU.
            self.has_igpu = False

        self._drive_info: List[Tuple[str, str]] = build_drive_info()
        if not self._drive_info:
            self._drive_info = [("all", "All Drives")]

    # ── Clock ──────────────────────────────────────────────────────────────────

    def _update_clock(self) -> None:
        now = datetime.now()
        self._time_lbl.setText(now.strftime("%H:%M:%S"))
        self._date_lbl.setText(now.strftime("%d.%m.%Y"))

    def _update_header_responsiveness(self, width: int) -> None:
        """Keep the clock and edit actions usable in the compact header."""
        if not hasattr(self, '_theme_combo'):
            return
        compact = width < 1050
        editing = self._btn_edit.isChecked()
        self._title_lbl.setVisible(not compact)
        self._info_lbl.setVisible(width >= 1200)
        self._btn_update.setVisible(not (compact and editing))
        self._btn_edit.setText(
            ("✔" if editing else "✏")
            if compact else ("✔  Fertig" if editing else "✏  Edit Layout"))
        self._btn_add.setText("＋" if compact else "＋  Add Tile")
        self._btn_reset.setText("↺" if compact else "↺  Reset")
        self._theme_combo.setMinimumWidth(dp(88 if compact else 0))
        self._theme_combo.setMaximumWidth(dp(95 if compact else 105))
        self._clock_panel.setMinimumWidth(dp(135 if compact else 0))
        self._time_lbl.setMinimumWidth(dp(135 if compact else 0))
        _set_themed_style(
            self._time_lbl,
            f"font-size: {font_size(30 if compact else 34)}; font-weight: bold; "
            "color: #888; font-family: Consolas; background: transparent;",
        )
        self._update_cols_label()

    def _update_clock_layout(self, width: int) -> None:
        """Move the date below the clock, then hide it as width gets tight."""
        if not hasattr(self, '_clock_grid'):
            return
        self._update_header_responsiveness(width)
        mode = _clock_display_mode(width)
        if getattr(self, '_clock_display_mode', None) == mode:
            return
        self._clock_display_mode = mode
        self._clock_grid.removeWidget(self._time_lbl)
        self._clock_grid.removeWidget(self._date_lbl)
        self._clock_grid.addWidget(self._time_lbl, 0, 0)
        self._clock_grid.setAlignment(
            self._time_lbl,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        if mode == "inline":
            self._clock_grid.addWidget(self._date_lbl, 0, 1)
            self._date_lbl.show()
        elif mode == "stacked":
            self._clock_grid.addWidget(self._date_lbl, 1, 0)
            self._date_lbl.show()
        else:
            self._date_lbl.hide()
        if mode != "time-only":
            self._clock_grid.setAlignment(
                self._date_lbl,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        self._clock_panel.updateGeometry()

    def _on_theme_changed(self, _index: int) -> None:
        theme = self._theme_combo.currentData()
        self._apply_theme_selection(str(theme), persist=True)

    def _apply_theme_selection(self, theme: str, *, persist: bool) -> None:
        self._theme = _set_active_theme(theme)
        combo_index = self._theme_combo.findData(self._theme)
        if combo_index >= 0 and combo_index != self._theme_combo.currentIndex():
            self._theme_combo.blockSignals(True)
            self._theme_combo.setCurrentIndex(combo_index)
            self._theme_combo.blockSignals(False)
        _set_windows_titlebar_theme(self, self._theme)
        _apply_theme_tree(self)
        if persist:
            try:
                data = _load_config_file()
                data['theme'] = self._theme
                _save_config_file(data)
            except Exception as exc:
                logger.warning("Theme save failed: %s", exc)

    # ── UI setup ───────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root_w  = QWidget()
        self.setCentralWidget(root_w)
        root    = QVBoxLayout(root_w)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(dp(15), dp(12), dp(15), dp(12))
        root.setSpacing(dp(0))

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_app_icon().pixmap(dp(36), dp(36)))
        icon_lbl.setStyleSheet("background: transparent;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(dp(10))

        self._title_lbl = QLabel(
            "System Tricorder  "
            f"<span style='font-size: {font_size(18)}; color:#00aa55;'>v{APP_VERSION}</span>"
        )
        self._title_lbl.setStyleSheet(
            f"font-size: {font_size(28)}; font-weight: bold; color: #00ff88; background: transparent;")
        hdr.addWidget(self._title_lbl)
        hdr.addSpacing(dp(16))

        sock_txt  = f"  ·  {self.num_sockets}× Socket" if self.num_sockets > 1 else ""
        cpu_hint  = f"{self.c_physical}C / {self.c_logical}T{sock_txt}"
        if self.is_hybrid:
            cpu_hint += f"  ·  {self.p_cores}P + {self.e_cores}E"
        elif self.has_ht:
            cpu_hint += "  ·  HT"
        self._info_lbl = QLabel(cpu_hint)
        self._info_lbl.setStyleSheet(
            f"font-size: {font_size(11)}; color: #444; background: transparent; padding-top: {dp(12)}px;")
        hdr.addWidget(self._info_lbl)

        hdr.addStretch()

        # ── Edit-mode toolbar ─────────────────────────────────────────────────
        self._btn_update = _toolbar_btn("⬇  Update")
        self._btn_edit  = _toolbar_btn("✏  Edit Layout", checkable=True)
        self._btn_edit.setToolTip(
            "Kacheln verschieben; per Rechtsklick Farbe über RGB/HEX wählen")
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

        self._update_worker: Optional[UpdateWorker] = None
        self._btn_update.clicked.connect(self._on_update_clicked)
        self._btn_edit.toggled.connect(self._on_edit_toggled)
        self._btn_add.clicked.connect(self._on_add_tiles)
        self._btn_minus.clicked.connect(lambda: self._change_cols(-1))
        self._btn_plus.clicked.connect(lambda: self._change_cols(+1))
        self._btn_reset.clicked.connect(self._on_reset_layout)

        for w in (self._btn_update, self._btn_edit, self._btn_add,
                  self._btn_minus, self._cols_lbl, self._btn_plus,
                  self._btn_reset):
            hdr.addWidget(w)

        self._theme_combo = QComboBox()
        self._theme_combo.addItem("Darkmode", "dark")
        self._theme_combo.addItem("Lightmode", "light")
        self._theme_combo.setToolTip("Farbschema auswählen")
        self._theme_combo.setMaximumWidth(dp(105))
        theme_index = self._theme_combo.findData(self._theme)
        self._theme_combo.setCurrentIndex(max(0, theme_index))
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        hdr.addSpacing(dp(8))
        hdr.addWidget(self._theme_combo)
        hdr.addSpacing(dp(12))

        self._clock_panel = QWidget()
        self._clock_panel.setStyleSheet("background: transparent;")
        self._clock_panel.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self._clock_grid = QGridLayout(self._clock_panel)
        self._clock_grid.setContentsMargins(0, 0, 0, 0)
        self._clock_grid.setHorizontalSpacing(dp(10))
        self._clock_grid.setVerticalSpacing(0)
        self._time_lbl = QLabel()
        self._time_lbl.setStyleSheet(
            f"font-size: {font_size(34)}; font-weight: bold; color: #888; "
            "font-family: Consolas; background: transparent;")
        self._date_lbl = QLabel()
        self._date_lbl.setStyleSheet(
            f"font-size: {font_size(17)}; font-weight: bold; color: #555; "
            "font-family: Consolas; background: transparent;")
        self._time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._date_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hdr.addWidget(self._clock_panel)
        root.addLayout(hdr)
        root.addSpacing(dp(10))

        # ── Scrollable content ────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The dashboard is a scale-to-fit surface: every active tile stays in
        # the viewport and no scrollbars are introduced at compact sizes.
        self._scroll = scroll
        self._content_w = None   # set below
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        content_w = QWidget()
        content_w.setMinimumSize(0, 0)
        content_w.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        content_w.setStyleSheet("background: transparent;")
        self._content_w = content_w
        content_layout = QVBoxLayout(content_w)
        content_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
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
        cpu_content_w.setMinimumSize(0, 0)
        cpu_inner = QVBoxLayout(cpu_content_w)
        cpu_inner.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
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
        global_section.collapsed_changed.connect(self._schedule_content_refit)
        cpu_section.collapsed_changed.connect(self._schedule_content_refit)
        # Tile grid structural changes (add/remove/move/cols/reset) all funnel
        # through TileGrid._relayout, which emits layout_changed.
        self._tile_grid.layout_changed.connect(self._schedule_content_refit)

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
        if self.current_platform in ("Windows", "Linux"):
            reg("cpu_power", PowerTile("cpu_power", "CPU · Leistungsaufnahme",
                                        "#ffcc00", 250.0),
                "CPU · Leistungsaufnahme")

        # ── Row 2: GPU engines ────────────────────────────────────────────────
        row()
        for gi, (gname, _, _) in enumerate(self.detected_gpus):
            pal = GPU_PALETTES[gi % len(GPU_PALETTES)]
            sn  = short_gpu_name(gname)
            reg(f"gpu_{gi}_total",
                MetricTile(f"gpu_{gi}_total", f"{sn} · GPU", pal[3]),
                f"{sn} · GPU")
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
            reg(f"gpu_{gi}_power",
                PowerTile(f"gpu_{gi}_power", f"{sn} · Leistungsaufnahme",
                          pal[2], 350.0),
                f"{sn} · Leistungsaufnahme")
            if gi < len(self.detected_gpus) - 1:
                row()   # each GPU on its own row if multiple GPUs

        # ── Row 3: iGPU (only if the hardware exists) ─────────────────────
        if self.has_igpu:
            row()
            reg("igpu", MetricTile("igpu", "iGPU", "#0055ff"), "iGPU")

        # ── Row 4+: Drives (all drives on one row) ────────────────────────────
        row()
        for key, label in self._drive_info:
            tid = f"drive_{key}"
            reg(tid, DriveTile(tid, label), f"Drive {label}")

        return tiles, names, default_order

    # ── Edit-mode toolbar logic ────────────────────────────────────────────────

    def _on_update_clicked(self) -> None:
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        self._btn_update.setEnabled(False)
        self._btn_update.setText("⏳  Update...")
        self._update_worker = UpdateWorker(self)
        self._update_worker.update_finished.connect(self._on_update_finished)
        self._update_worker.start()

    def _on_update_finished(self, ok: bool, message: str) -> None:
        self._btn_update.setEnabled(True)
        self._btn_update.setText("⬇  Update")
        worker = self._update_worker
        rebuild_needed = bool(
            worker is not None and getattr(worker, "rebuild_needed", False))
        deferred_pull_branch = str(
            getattr(worker, "deferred_pull_branch", "") if worker is not None else "")
        prepared_exe_update = str(
            getattr(worker, "prepared_exe_update", "") if worker is not None else "")
        target_version = str(
            getattr(worker, "target_version", "") if worker is not None else "")
        self._update_worker = None
        if worker is not None:
            worker.deleteLater()
        if not ok:
            QMessageBox.warning(self, "System Tricorder Update", message)
            return
        if prepared_exe_update:
            QMessageBox.information(
                self, "System Tricorder Update",
                message + "\n\nDie App schliesst sich jetzt und startet "
                f"anschliessend als {target_version or 'neue Version'} neu. "
                "Details stehen in tricorder_release_update_<pid>.log im "
                "TEMP-Ordner.",
            )
            update_path = Path(prepared_exe_update)
            if self._spawn_standalone_exe_update(update_path):
                self.close()
            else:
                try:
                    update_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Could not remove prepared EXE update %s: %s",
                                   update_path, exc)
                with contextlib.suppress(OSError):
                    update_path.parent.rmdir()
                QMessageBox.warning(
                    self, "System Tricorder Update",
                    "Der verifizierte Download konnte nicht zur Installation "
                    "uebergeben werden. Die laufende EXE blieb unveraendert. "
                    "Bitte das Release notfalls manuell von GitHub laden.",
                )
            return
        # Successful update.  If we're running from a frozen EXE and the pull
        # changed Python source, trigger a hands-off rebuild: spawn a detached
        # helper .bat that waits for us to exit, rebuilds via PyInstaller, and
        # relaunches the new EXE.
        if (rebuild_needed and getattr(sys, "frozen", False)
                and platform.system() == "Windows"):
            QMessageBox.information(
                self, "System Tricorder Update",
                message + "\n\nDer EXE-Rebuild startet jetzt: diese App schliesst "
                "sich, baut die neue system_tricorder.exe (ca. 1-3 Min) und "
                "startet sie danach automatisch neu. Details stehen in der "
                "Datei tricorder_rebuild_<pid>.log im TEMP-Ordner.",
            )
            if self._spawn_rebuild_and_restart(deferred_pull_branch):
                self.close()  # releases the .exe lock so PyInstaller can overwrite it
            else:
                QMessageBox.warning(
                    self, "System Tricorder Update",
                    "Der automatische Rebuild konnte nicht gestartet werden. "
                    "Bitte System Tricorder manuell schliessen und neu bauen:\n"
                    ".venv\\Scripts\\python.exe -m PyInstaller --clean --noconfirm "
                    "system_tricorder.spec",
                )
        else:
            QMessageBox.information(self, "System Tricorder Update", message)

    def _spawn_standalone_exe_update(self, update_path: Path) -> bool:
        """Hand a verified release EXE to the detached replacement helper."""
        try:
            update_path = update_path.resolve()
            temp_root = Path(tempfile.gettempdir()).resolve()
            if (not update_path.is_file()
                    or not update_path.is_relative_to(temp_root)
                    or not update_path.parent.name.startswith(
                        "system-tricorder-update-")):
                return False
            exe_path = Path(sys.executable).resolve()
            pid = os.getpid()
            tmp_dir = Path(os.environ.get("TEMP", tempfile.gettempdir()))
            bat_path = tmp_dir / f"tricorder_release_update_{pid}.bat"
            log_path = tmp_dir / f"tricorder_release_update_{pid}.log"
            bat_path.write_text(
                _build_standalone_exe_update_bat(
                    exe_path, update_path.resolve(), pid, log_path),
                encoding="utf-8",
            )
            detached_process = 0x00000008
            new_process_group = 0x00000200
            create_no_window = 0x08000000
            subprocess.Popen(
                ["cmd.exe", "/c", str(bat_path)],
                cwd=str(exe_path.parent),
                creationflags=(detached_process | new_process_group
                               | create_no_window),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Standalone EXE update helper spawned (log: %s)", log_path)
            return True
        except Exception as exc:
            logger.warning("Standalone EXE update helper failed: %s", exc)
            return False

    def _spawn_rebuild_and_restart(self, pull_branch: str = "") -> bool:
        """Spawn the detached Windows update/rebuild/relaunch helper."""
        try:
            repo_root = _find_git_checkout(_app_start_dir())
            if repo_root is None:
                return False
            exe_path = Path(sys.executable).resolve()
            pid = os.getpid()
            tmp_dir = Path(os.environ.get("TEMP", str(repo_root)))
            bat_path = tmp_dir / f"tricorder_rebuild_{pid}.bat"
            log_path = tmp_dir / f"tricorder_rebuild_{pid}.log"
            bat_text = _build_rebuild_bat(
                repo_root, exe_path, pid, log_path, pull_branch)
            # The helper switches cmd.exe to code page 65001 before any paths
            # are consumed, so non-ASCII user/repo names remain intact.
            bat_path.write_text(bat_text, encoding="utf-8")
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                ["cmd.exe", "/c", str(bat_path)],
                cwd=str(repo_root),
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Rebuild helper spawned (log: %s)", log_path)
            return True
        except Exception as exc:
            logger.warning("Rebuild helper spawn failed: %s", exc)
            return False

    def _on_edit_toggled(self, active: bool) -> None:
        self._tile_grid.set_edit_mode(active)
        self._btn_add.setVisible(active)
        self._btn_minus.setVisible(active)
        self._btn_plus.setVisible(active)
        self._cols_lbl.setVisible(active)
        self._btn_reset.setVisible(active)
        self._update_cols_label()
        self._update_header_responsiveness(self.width())

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
        row_height = self._tile_grid._min_row_h
        self._cols_lbl.setText(
            f"{row_height}px" if self.width() < 1050
            else f"Zeilenhöhe {row_height}px")

    def _on_reset_layout(self) -> None:
        self._tile_grid.reset_layout(self._default_tile_order)
        self._update_cols_label()

    # ── CPU core topology builders ─────────────────────────────────────────────

    def _build_hybrid_cores(self, parent: QVBoxLayout) -> None:
        P_COLOR  = "#00d4ff"
        HT_COLOR = "#0077aa"
        E_COLOR  = "#ff007f"
        parent.addWidget(section_label(
            f"<b style='color:{P_COLOR}; font-size:14px;'>⚡ Performance Cores "
            f"({self.p_cores} Cores / {self.p_threads} Threads, "
            f"log. CPUs {_fmt_ranges([i for g in self.p_logical for i in g])})</b>"
        ))
        parent.addSpacing(4)

        p_groups: List[List[QWidget]] = []
        # p_logical is a per-core group of real logical indices; a group of 2
        # means this P-core has a Hyper-Threading sibling.
        for ci, pcore_idx in enumerate(self.p_logical):
            w0 = MasterMetricBox(f"P-Core {ci}", P_COLOR, variant='standard')
            self.thread_widgets[pcore_idx[0]] = w0
            group: List[QWidget] = [w0]
            if len(pcore_idx) > 1:
                w1 = MasterMetricBox(f"P-Core {ci}", HT_COLOR, variant='ht')
                self.thread_widgets[pcore_idx[1]] = w1
                group.append(w1)
            p_groups.append(group)
        parent.addWidget(ResponsiveCoreGrid(p_groups, min_col_w=100), 1)
        parent.addSpacing(8)

        parent.addWidget(section_label(
            f"<b style='color:{E_COLOR}; font-size:12px;'>🔋 Efficiency Cores "
            f"({self.e_cores} Cores / {self.e_threads} Threads, "
            f"log. CPUs {_fmt_ranges(self.e_logical)})</b>"
        ))
        parent.addSpacing(4)

        e_groups: List[List[QWidget]] = []
        for i, t in enumerate(self.e_logical):
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

    def _poll_metrics(self) -> None:
        m = self.hw_thread.take_latest()
        if m is not None:
            self._update_ui(m)

    def _update_ui(self, m: SystemMetrics) -> None:
        self._metric_frames += 1
        _t = self._tiles.get

        # Optimized: direct attribute access instead of isinstance() checks
        # Tiles are registered with known types - no runtime type checking needed

        w = _t("cpu_total")
        if w:
            w.update_val(m.cpu_total_percent)
        w = _t("ram")
        if w:
            w.update_val(m.ram_percent, f"{m.ram_used_gb:.1f}/{m.ram_total_gb:.1f} GB")
        w = _t("cpu_power")
        if w:
            w.update_power(m.cpu_power_watts)
        w = _t("igpu")
        if w:
            w.update_val(m.igpu_percent)

        for i, gm in enumerate(m.gpus):
            w_total = _t(f"gpu_{i}_total")
            if w_total:
                w_total.update_val(gm.gpu_total_percent)
            w_3d = _t(f"gpu_{i}_3d")
            if w_3d:
                w_3d.update_3d_compute(
                    gm.gpu_3d_percent, gm.gpu_compute_percent)
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
            w_power = _t(f"gpu_{i}_power")
            if w_power:
                w_power.update_power(gm.gpu_power_watts)

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
    #   • 'fill'  — the USER owns the window size.  Content is fixed to the
    #               viewport and every active tile/core box receives a share of
    #               that space; scrollbars are never introduced.
    _MAX_WIDGET = 16_777_215   # Qt QWIDGETSIZE_MAX

    def _fit_window_to_content(self) -> None:
        """AUTO mode: grow the window so it exactly fits all content — no scrollbar.

        Used on first show and after structural changes (add/remove/move tile,
        column change, section collapse/expand).  The coalescing timer lets Qt
        settle first, so one size-hint pass is sufficient and no nested event
        processing can re-enter the resize path.  Skipped for maximised or
        fullscreen windows.
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
        needs_fill = False
        try:
            content = self._content_w
            content.setMinimumSize(0, 0)
            content.setMaximumSize(self._MAX_WIDGET, self._MAX_WIDGET)
            layout = content.layout()
            if layout is not None:
                layout.activate()
            minimum_hint = content.minimumSizeHint()
            size_hint = content.sizeHint()
            need = max(
                minimum_hint.height() if minimum_hint is not None else 0,
                size_hint.height() if size_hint is not None else 0,
            )
            viewport = self._scroll.viewport()
            if viewport is not None:
                delta = need - viewport.height()
                if abs(delta) >= 3:
                    target = max(self.height() + delta, self.minimumHeight())
                    try:
                        screen = self.screen()
                        if screen is not None:
                            available = screen.availableGeometry().height()
                            frame = self.frameGeometry().height() - self.height()
                            screen_target = available - frame
                            if target > screen_target:
                                target = screen_target
                                needs_fill = True
                    except Exception:
                        pass
                    if abs(target - self.height()) >= 3:
                        self.resize(self.width(), target)
        finally:
            self._fitting = False
        if needs_fill:
            self._schedule_layout_settle('fill')

    def _sync_content_to_viewport(self) -> None:
        """Synchronously keep scale-to-fit content inside the viewport."""
        if self._content_w is None or self._scroll is None:
            return
        viewport = self._scroll.viewport()
        if viewport is None:
            return
        viewport_size = viewport.size()
        # Leave a two-pixel rounding guard so the final stretched grid row is
        # never placed exactly beyond the viewport's last drawable pixel.
        content_size = QSize(
            viewport_size.width(), max(1, viewport_size.height() - 2))
        self._content_w.setMinimumSize(0, 0)
        self._content_w.setMaximumSize(content_size)
        self._content_w.resize(content_size)

    def _apply_fill_mode(self) -> None:
        """FILL mode: make the content follow the user-selected viewport.

        This mode also applies to maximised and fullscreen windows.  Releasing
        row maximums lets the global tiles share all available vertical space;
        no nested event processing is needed, which avoids re-entrant resize
        storms and visible flicker while a window edge is being dragged.
        """
        if getattr(self, '_fitting', False):
            return
        if self._content_w is None or self._scroll is None:
            return
        self._fitting = True
        self._fit_mode = 'fill'
        try:
            content = self._content_w
            self._sync_content_to_viewport()
            viewport = self._scroll.viewport()
            self._tile_grid._auto_adjust_row_height()
            self._tile_grid.updateGeometry()
            content.updateGeometry()
            if viewport is not None:
                viewport.update()
        finally:
            self._fitting = False

    def _schedule_layout_settle(self, action: str) -> None:
        """Coalesce resize bursts into one non-reentrant layout update."""
        self._pending_resize_action = action
        self._resize_settle_timer.start()

    def _schedule_content_refit(self) -> None:
        state = self.windowState()
        maximised = bool(
            state
            & (Qt.WindowState.WindowMaximized
               | Qt.WindowState.WindowFullScreen)  # type: ignore[operator]
        )
        self._schedule_layout_settle('fill' if maximised else 'auto')

    def _settle_responsive_layout(self) -> None:
        if self._pending_resize_action == 'auto':
            self._fit_window_to_content()
        else:
            self._apply_fill_mode()

    def resizeEvent(self, event) -> None:                                    # type: ignore
        super().resizeEvent(event)
        new_w, new_h = event.size().width(), event.size().height()
        self._update_clock_layout(new_w)
        old = getattr(self, '_last_size', None)
        self._last_size = (new_w, new_h)
        if getattr(self, '_fitting', False) or old is None:
            return

        state = self.windowState()
        if state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):  # type: ignore[operator]
            self._fit_mode = 'fill'
            self._sync_content_to_viewport()
            self._schedule_layout_settle('fill')
            return

        dh = new_h - old[1]
        dw = new_w - old[0]
        if dh != 0 or dw != 0:
            # Any user resize owns both dimensions and scales continuously.
            self._fit_mode = 'fill'
            self._sync_content_to_viewport()
            self._schedule_layout_settle('fill')

    def changeEvent(self, event) -> None:                                    # type: ignore
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange
                and hasattr(self, '_resize_settle_timer')):
            # Window-state transitions can arrive before or after resizeEvent
            # depending on the platform.  Handle both paths idempotently.
            self._fit_mode = 'fill'
            self._sync_content_to_viewport()
            self._schedule_layout_settle('fill')

    def showEvent(self, event) -> None:                                      # type: ignore
        super().showEvent(event)
        state = self.windowState()
        maximised = bool(
            state
            & (Qt.WindowState.WindowMaximized
               | Qt.WindowState.WindowFullScreen)  # type: ignore[operator]
        )
        # A valid restored geometry belongs to the user and must not be resized
        # back to the content hint during startup.  Only a first launch without
        # saved placement uses auto-fit.
        preserve_geometry = self._restored_window_geometry
        self._restored_window_geometry = False
        self._fit_mode = 'fill' if maximised or preserve_geometry else 'auto'
        self._schedule_layout_settle(self._fit_mode)

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

            self._restored_window_geometry = True
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
                'geometry': self.saveGeometry().toBase64().data().decode('ascii'),
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
    _self_test = "--self-test" in sys.argv
    if _self_test:
        # Frozen-artifact CI smoke test: no display server or user interaction,
        # and never overwrite the developer's real layout/geometry file.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _self_test_tmp = os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or "/tmp"
        CONFIG_FILE = Path(_self_test_tmp) / f"system-tricorder-selftest-{os.getpid()}.json"
        with contextlib.suppress(FileNotFoundError):
            CONFIG_FILE.unlink()

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

    app = QApplication([sys.argv[0]] if _self_test else sys.argv)
    app.setApplicationName("System Tricorder")
    app.setWindowIcon(_app_icon())
    app.setStyle("Fusion")

    # Compute DPI scale factor AFTER creating QApplication (Qt-native)
    _init_dp_scale(app)
    logging.getLogger("tricorder").debug("DPI scale factor: %.2f", _DP_SCALE)

    win = TricorderDashboard()
    if _self_test:
        app.setQuitOnLastWindowClosed(False)
        win.show()

        def _finish_self_test() -> None:
            ok = (
                win._metric_frames > 0
                and f"v{APP_VERSION}" in win.windowTitle()
                and "cpu_total" in win._tiles
            )
            if ok:
                logger.info("Frozen self-test passed (%d metric frames)", win._metric_frames)
            else:
                logger.error("Frozen self-test failed (%d metric frames)", win._metric_frames)
            win.close()
            with contextlib.suppress(FileNotFoundError):
                CONFIG_FILE.unlink()
            app.exit(0 if ok else 1)

        QTimer.singleShot(3000, _finish_self_test)
    elif not win._restore_window_placement():
        # No saved geometry: open windowed and auto-fit to content (showEvent
        # schedules _fit_window_to_content) — guarantees no scrollbar on first
        # launch.  The user can still maximise manually if preferred.
        win.show()
    if (not _self_test and platform.system() == "Windows"
            and getattr(sys, "frozen", False)):
        # The detached updater leaves the previous EXE as a rollback until the
        # new dashboard has survived startup for a few seconds.
        QTimer.singleShot(5000, _cleanup_previous_exe_backup)
    sys.exit(app.exec())
