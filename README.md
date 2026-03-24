# 📊 System Tricorder

> A real-time hardware monitoring dashboard for Windows — dark mode, 20 FPS, no fluff.

![Version](https://img.shields.io/badge/version-0.2-00ff88?style=flat-square)
![Python](https://img.shields.io/badge/python-3.8%2B-blue?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## ✨ What it does

System Tricorder gives you a live, graph-based view of your entire system at a glance — CPU, RAM, GPU(s), NPU, iGPU, and disk I/O — all updating at 20 FPS in a clean dark-mode window.

It's smart about your hardware:

- **Intel Hybrid CPUs (P/E Cores)** get separate sections with distinct visual designs — Performance Cores show as top-bordered boxes, Efficiency Cores as left-bordered boxes, so you can tell them apart without reading the labels
- **Hyperthreading & AMD SMT** is visualized as paired columns — physical core on top, logical sibling below with a dimmed `HT` / `SMT` badge
- **Multiple GPUs** each get their own color-coded row with independent 3D, Copy, and VRAM graphs
- **RAM type** (DDR4 / DDR5) is auto-detected and shown in the label
- **VRAM** is read directly from the Windows Registry for accurate values (the WMI 32-bit cap workaround is handled internally)

---

## 🖥️ Screenshots

![alt text](image.png)
---

## 🚀 Installation

**1. Clone the repo**
```bash
git clone https://github.com/YOUR_USERNAME/system-tricorder.git
cd system-tricorder
```

**2. Install dependencies**
```bash
pip install PyQt5
pip install psutil
pip install pywin32        # Required for GPU/WMI metrics
```

Or all at once:
```bash
pip install PyQt5 psutil pywin32
```

**3. Run**
```bash
python system_tricorder.py
```

> ⚠️ Windows only. The GPU and RAM-type detection relies on WMI and the Windows Registry.

---

## 🔧 Requirements

| Package   | Purpose                                    |
|-----------|--------------------------------------------|
| `PyQt5`   | GUI framework                              |
| `psutil`  | CPU / RAM / Disk metrics                   |
| `pywin32` | GPU utilization & VRAM via WMI + Registry  |

Python 3.8 or newer is recommended.

---

## 📦 Building an Executable (.exe)

If you want to run this without installing Python (or share it with others), you can compile it into a standalone executable using `pyinstaller`:

1. Install PyInstaller:

```bash
pip install pyinstaller
```

2. Build the executable:

```bash
pyinstaller --noconsole --onefile system_tricorder.py
```

You will find the compiled `system_tricorder.exe` inside the newly created `dist` folder.

---

## 📐 What's monitored

### Global Grid
| Metric        | Source                        |
|---------------|-------------------------------|
| CPU Total     | psutil                        |
| DDR4/DDR5 RAM | psutil + WMI type detection   |
| SSD Read/Write| psutil disk I/O (MB/s)        |
| NPU           | WMI GPU engine counters       |
| iGPU          | WMI GPU engine counters       |

### Per GPU (up to 4)
| Metric    | Source                             |
|-----------|------------------------------------|
| 3D/Compute| WMI GPU engine utilization         |
| Copy 0/1  | WMI GPU engine utilization         |
| VRAM Used | WMI adapter memory counters        |
| VRAM Total| Windows Registry (accurate values) |

### CPU Core Topology
| CPU Type            | Display                                          |
|---------------------|--------------------------------------------------|
| Intel Hybrid (P+E)  | Two separate sections, two distinct box designs  |
| Intel / AMD with HT/SMT | Paired columns: physical core + logical sibling |
| Single-thread cores | Simple uniform grid                              |

---

## 🗂️ Changelog

### v0.2 *(current)*
- Multi-GPU support — each GPU gets its own color-coded row (up to 4)
- Intel P/E Core visual separation — different box design per core type (not just color)
- HT / AMD SMT pairs visualized as aligned columns
- Auto-detection of RAM type (DDR4/DDR5) via WMI
- Auto-detection of multi-socket systems
- VRAM detection iterates all GPU Registry entries to avoid iGPU winning
- Improved LUID tracking for stable GPU row ordering

### v0.1 *(initial release)*
- Basic 2×5 global metrics grid
- Per-thread CPU graphs
- Single GPU support
- Dark mode PyQt5 dashboard at 20 FPS

---

## 🤝 Contributing

This is my first public project — feedback, issues, and pull requests are very welcome!

If you run it on an interesting setup (dual GPU, server CPU, AMD APU, etc.) and something looks off or broken, please open an issue with your CPU model and thread count.

---

## 📄 License

MIT — do whatever you want with it.
