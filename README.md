# 📊 System Tricorder

> A real-time hardware monitoring dashboard for Windows — dark mode, 20 FPS, fully customisable layout.

![Version](https://img.shields.io/badge/version-0.3-00ff88?style=flat-square)
![Python](https://img.shields.io/badge/python-3.8%2B-blue?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## 🖥️ Screenshots

![alt text](image.png)

---
## ✨ What it does

System Tricorder gives you a live, graph-based view of your entire system at a glance — CPU, RAM, GPU(s), NPU, iGPU, and per-drive disk I/O — all updating at 20 FPS in a clean dark-mode window.

Everything is customisable: drag tiles to rearrange them, hide what you don't care about, restore anything later, and adjust how many columns fit on screen. The layout is saved automatically so it survives restarts.

---

## 🎛️ Edit Mode — Customising your layout

Press **✏ Edit Layout** in the top-right toolbar to enter edit mode. All tiles highlight with a yellow accent border and show a **×** close button.

While in edit mode you can:

- **Drag & drop** any tile to a new position — a dashed yellow outline shows the drop target.
- **× button** — hides a tile (not deleted, just moved to the hidden pool).
- **＋ Add Tile** — opens a dialog listing all hidden tiles with checkboxes so you can restore any of them.
- **‹ / ›** — decrease or increase the number of grid columns (1–12).
- **↺ Reset** — restores the factory default layout and column count.

Press **✔ Fertig** to leave edit mode. Your layout is saved automatically to `~/.tricorder_layout.json`.

---

## 💾 Per-Drive Tiles

Each physical drive gets its own tile showing **Read** and **Write** throughput as two stacked sparklines in a single landscape tile:

```
💾  C:/D:                         ↑ 623 MB/s
R  ──────────────────────────────  847.2 MB/s
W  ──────────────────────────────    0.1 MB/s
```

The y-axis auto-scales based on the current peak, with slow decay. Values display in MB/s; automatically switches to GB/s for NVMe Gen 5 drives doing ≥ 1000 MB/s.

On Windows, drive tiles are labelled with their drive letters (e.g. `C:`, `D:`, `C:/D:` for a multi-partition drive).

---

## 🚀 Installation

```bash
git clone https://github.com/YOUR_USERNAME/system-tricorder.git
cd system-tricorder
pip install PyQt5 psutil pywin32
python system_tricorder.py
```

> ⚠️ Windows only. GPU and RAM-type detection relies on WMI and the Windows Registry.

---

## 📦 Building an Executable (.exe)

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile system_tricorder.py
```

---

## 📐 What's monitored

### Global Grid (all tiles draggable / hideable)

| Tile | Source |
|------|--------|
| CPU Gesamt | psutil total |
| DDR4/DDR5 RAM | psutil + WMI type |
| iGPU | WMI GPU engine |
| NPU | WMI GPU engine |
| GPU N · 3D | WMI GPU engine |
| GPU N · Copy0/1 | WMI GPU engine |
| GPU N · VRAM | WMI adapter memory |
| Drive X (Read + Write) | psutil per-disk I/O |

### CPU Core Topology (collapsible section)

| CPU Type | Display |
|----------|---------|
| Intel Hybrid (P+E) | Two separate sections, two distinct box designs |
| Intel / AMD with HT/SMT | Paired columns: physical core + logical sibling |
| Single-thread cores | Simple uniform grid |

---

## 🗂️ Layout Config Format

`~/.tricorder_layout.json` example:
```json
{
  "version": "0.3",
  "cols": 5,
  "tile_order": ["cpu_total", "ram", "gpu_0_3d", "gpu_0_vram", "drive_PhysicalDrive0"],
  "hidden_tiles": ["igpu", "npu", "gpu_0_copy0", "gpu_0_copy1"]
}
```

Delete the file to reset to factory defaults.

---

## 🗂️ Changelog

### v0.3 *(current)*
- **Edit Mode** — drag-to-reorder tiles, × to hide, ＋ to restore, ‹/› to adjust columns
- **Per-drive tiles** — each physical drive gets one landscape tile with dual Read/Write sparklines and auto-scaling MB/s axis (auto-switches to GB/s for fast NVMe)
- **Layout persistence** — order, hidden tiles, and column count saved to `~/.tricorder_layout.json`
- **Collapsible CPU section** — click the ▼ header to collapse/expand the thread topology grid
- WMI drive-letter mapping — tiles show `C:`, `D:` etc. instead of `PhysicalDrive0`
- Fixed Qt CSS selectors — custom class names replaced with `QFrame` (invisible tile bug)
- Fixed `CollapsibleSection` — rebuilt as QWidget row + QLabel (QPushButton ignores HTML)

### v0.2
- Multi-GPU support (up to 4), each with its own color-coded row
- Intel P/E Core visual separation — different box design per core type
- HT / AMD SMT pairs visualized as aligned columns
- Auto-detection of RAM type (DDR4/DDR5) via WMI
- Auto-detection of multi-socket systems
- Registry-based VRAM detection (avoids 4 GB WMI cap)

### v0.1 *(initial release)*
- Basic 2×5 global metrics grid, per-thread CPU graphs, single GPU, dark mode 20 FPS

---

## 📄 License

MIT — do whatever you want with it.
