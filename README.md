# 📊 System Tricorder

> A real-time hardware monitoring dashboard for Windows — dark mode, 20 FPS, fully customisable free-form layout.

![Version](https://img.shields.io/badge/version-0.5-00ff88?style=flat-square)
![Python](https://img.shields.io/badge/python-3.8%2B-blue?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## 🖥️ Screenshots

![alt text](image.png)

---

## ✨ What it does

System Tricorder gives you a live, graph-based view of your entire system at a glance — CPU, RAM, GPU(s), NPU, iGPU, and per-drive disk I/O — all updating at 20 FPS in a clean dark-mode window.

Every aspect of the layout is yours to control: arrange tiles into any number of rows with any number of tiles per row, hide what you don't need, restore it later, collapse entire sections. Everything persists across restarts automatically.

---

## 🚀 Installation

```bash
git clone https://github.com/YOUR_USERNAME/system-tricorder.git
cd system-tricorder
pip install PyQt5 psutil pywin32
python system_tricorder.py
```

> ⚠️ Windows only. GPU utilisation, VRAM, and RAM-type detection rely on WMI and the Windows Registry.

---

## 📦 Building an Executable (.exe)

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile system_tricorder.py
```

---

## 🎛️ Edit Mode — Customising your layout

Press **✏ Edit Layout** in the toolbar to enter edit mode. All tiles highlight with a yellow accent border and gain two overlay buttons.

### Controls on each tile

| Button | Position | Action |
|--------|----------|--------|
| **×** | top-right | Hide the tile (moved to the hidden pool, not deleted) |
| **↵** | top-left | Toggle a row break before this tile — green = break active |

### Toolbar controls (visible in edit mode)

| Button | Action |
|--------|--------|
| **＋ Add Tile** | Opens a checklist of all hidden tiles so you can restore any of them |
| **‹ / ›** | Decrease / increase the minimum row height |
| **↺ Reset** | Restores the factory default layout and clears all row breaks |
| **✔ Fertig** | Leave edit mode — layout is saved automatically |

### Arranging tiles freely

The global grid has no fixed column count. Each row is independent and can hold any number of tiles. To build your own layout:

**Drag a tile onto another tile** — the yellow bar on the left or right edge of the target shows whether it will land before or after.

**Drag a tile onto the `── new row ──` line** that appears between rows — the tile is pulled out of its current row and placed as the first tile of a brand-new row at that position. This is how you create layouts like:

```
CPU  |  RAM
3D/Compute  |  Copy  |  VRAM
iGPU  |  NPU
SSD C:  |  SSD D:  |  SSD E:  |  HDD F:
```

**Drag a tile onto the `+` zone** at the right end of a row to append it to that row.

**Click ↵** on any tile to toggle a forced row break directly before it — useful for fine-tuning without drag operations. The button turns green when a break is active.

Any combination of row lengths is valid:

```
()
()()()()
()()
()
()
()()()
()()()()()()()
```

Your layout, including all row breaks, is saved to `~/.tricorder_layout.json` on every change.

---

## 📐 What's monitored

### Global Grid (all tiles freely arrangeable and hideable)

| Tile | What it shows | Source |
|------|--------------|--------|
| CPU Gesamt | Total CPU utilisation | psutil |
| DDR4 / DDR5 RAM | Used / total memory | psutil + WMI type detection |
| iGPU | Integrated GPU engine utilisation | WMI GPU counters |
| NPU | Neural Processing Unit utilisation | WMI GPU counters |
| GPU N · 3D / Compute | Two sparklines: rasterisation + compute/CUDA separately | WMI GPU counters |
| GPU N · Copy | Two sparklines: Copy Engine 0 + Copy Engine 1 | WMI GPU counters |
| GPU N · VRAM | Used / total VRAM | WMI + Registry |
| Drive X | Two sparklines: Read MB/s + Write MB/s | psutil per-disk I/O |

### CPU Thread Topology (collapsible section)

| CPU Type | Display |
|----------|---------|
| Intel Hybrid (P+E cores) | Two separate responsive grids — P-Cores and E-Cores |
| Intel / AMD with HT / SMT | Paired columns: physical core + logical sibling always together |
| Single-thread cores | Uniform responsive grid |

Both sections (Global System & Graphics and CPU Thread Topology) can be collapsed by clicking their header. Freed space is redistributed to whatever remains visible.

---

## 💾 Per-Drive Tiles

Each physical drive gets its own tile with two stacked sparklines and an auto-scaling axis:

```
💾  C:/D:                         ↑ 623 MB/s
R  ──────────────────────────────  847.2 MB/s
W  ──────────────────────────────    0.1 MB/s
```

Values are shown in MB/s and automatically switch to GB/s for drives exceeding 1000 MB/s. Drive tiles are labelled with their Windows drive letters (`C:`, `D:`, `C:/D:` for multi-partition drives).

---

## 🗂️ Layout Config Format

`~/.tricorder_layout.json` stores your complete layout. The `__row__` sentinel marks row breaks.

```json
{
  "version": "0.5",
  "min_row_h": 130,
  "tile_order": [
    "cpu_total", "ram",
    "__row__",
    "gpu_0_3d", "gpu_0_copy", "gpu_0_vram",
    "__row__",
    "igpu", "npu",
    "__row__",
    "drive_PhysicalDrive0", "drive_PhysicalDrive1"
  ],
  "hidden_tiles": []
}
```

Delete the file to reset to factory defaults.

---

## 🗂️ Changelog

### v0.5 *(current)*
- **Fully free row layout** — the global tile grid no longer has a fixed column count. Each row is independent and can hold any number of tiles. Any arrangement of row lengths is possible
- **`── new row ──` drop zones** — horizontal drop bars appear between every row in edit mode; dragging a tile onto one creates a brand-new row at that exact position
- **`+` row-end drop zones** — small drop targets at the right end of each row let you append tiles directly to a specific row
- **`↵` row-break button** — each tile gains a top-left button that toggles a forced row break before it; turns green when active
- **Default layout uses rows** — out of the box, tiles start on sensible separate rows (CPU/RAM, GPU engines, iGPU/NPU, drives) instead of one long line
- **E-Core grid balanced** — E-Cores now distribute evenly across rows (e.g. 8+8 instead of 12+4 for 16 E-Cores)
- **`‹ ›` buttons** now control row height instead of column count
- **Row breaks persist** across restarts via the `__row__` sentinel in the JSON config

### v0.4
- GPU 3D / Compute split — the 3D / Compute tile now shows two separate sparklines: 3D (rasterisation) and Cmp (Compute / CUDA / OpenCL), instead of a single combined value
- GPU Copy tile — Cp0 and Cp1 combined into one landscape tile (matching the Drive tile layout), instead of two separate metric tiles
- Free insert-before/after drag — tiles can be inserted before or after any other tile; left half of target = before, right half = after; yellow bar on tile edge shows live where it will land
- Dynamic column count — the global tile grid and CPU topology grid automatically adjust their column count to fit the window width
- Collapsible sections — both Global System & Graphics and CPU Thread Topology can be collapsed by clicking their header; freed space is correctly redistributed
- Responsive CPU grid — P-Core, E-Core and HT/SMT pairs reflow automatically on window resize; HT/SMT pairs always stay together in the same column
- Clock & date enlarged to 36 px

### v0.3
- Edit Mode — drag-to-reorder tiles, × to hide, ＋ to restore, ‹/› to adjust columns
- Per-drive tiles — each physical drive gets one landscape tile with dual Read/Write sparklines and auto-scaling MB/s axis (auto-switches to GB/s for fast NVMe)
- Layout persistence — order, hidden tiles, and column count saved to `~/.tricorder_layout.json`
- Collapsible CPU section — click the ▼ header to collapse/expand the thread topology grid
- WMI drive-letter mapping — tiles show `C:`, `D:` etc. instead of `PhysicalDrive0`

### v0.2
- Multi-GPU support (up to 4), each with its own colour-coded row
- Intel P/E Core visual separation — different box design per core type
- HT / AMD SMT pairs visualised as aligned columns
- Auto-detection of RAM type (DDR4/DDR5) via WMI
- Auto-detection of multi-socket systems
- Registry-based VRAM detection (avoids the 4 GB WMI cap)

### v0.1 *(initial release)*
- Basic 2×5 global metrics grid, per-thread CPU graphs, single GPU, dark mode 20 FPS

---

## 📄 License

MIT — do whatever you want with it.