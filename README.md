# CadenceAI MCP — Cadence EDA Tools for AI Assistants

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)
[![Tools](https://img.shields.io/badge/tools-13-orange.svg)](cadence_mcp/server.py)

A **Model Context Protocol (MCP)** server that exposes Cadence EDA tools
(OrCAD Capture, Allegro PCB Designer, PSpice) to AI assistants like Claude,
Cursor, and Cline — letting them **browse PCBs, generate schematics, and run
circuit simulations** through a single, license-free, cross-platform interface.

---

## ✨ Features

| Domain            | What AI can do                                                                 |
|-------------------|--------------------------------------------------------------------------------|
| **Environment**   | Auto-detect SPB installation, license status, available batch commands         |
| **PCB files**     | Find `.brd` files, parse text assets from the Allegro binary DB                 |
| **Batch CLI**     | Invoke 60+ safe `allegro_batch` subcommands (artwork, netin, dump_libraries…) |
| **Schematics**    | Generate SPICE netlists, `.DSN` project XML, Allegro physical netlists         |
| **Simulation**    | Generate circuits from templates, run via PSpice / ngspice / analytical fallback |
| **Netlist flow**  | DSL → SPICE → Allegro tel-format in one end-to-end pipeline                     |

**13 MCP tools**, **75+ Allegro batch subcommands** wrapped with safety
allow-list, **6 circuit templates**, all available out of the box.

---

## 🚀 Quick Start

### 1. Install

```bash
git clone https://github.com/cadenceai/cadence-mcp.git
cd cadence-mcp
pip install -e .
```

### 2. Probe your environment

```bash
python -m cadence_mcp.server --probe
```

```json
{
  "cds_root": "C:\\Cadence\\SPB_23.1",
  "allegro_batch": "C:\\Cadence\\SPB_23.1\\tools\\bin\\allegro_batch.exe",
  "pspice_exe": "C:\\Cadence\\SPB_23.1\\tools\\bin\\pspice.exe",
  "license_present": false,
  "batch_commands_count": 75,
  ...
}
```

### 3. Register in your MCP client (e.g. Cursor / Claude Desktop)

Edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "cadence": {
      "command": "python",
      "args": ["-m", "cadence_mcp.server"],
      "cwd": "D:\\AIBench\\CadenceAI"
    }
  }
}
```

Restart the client. AI now has 13 Cadence tools at its disposal.

---

## 🛠 The 13 Tools

### Environment & Diagnostics

| Tool                       | Purpose                                              |
|----------------------------|------------------------------------------------------|
| `cadence_probe`            | Detect SPB install, license, batch commands          |
| `cadence_env_vars`         | Dump CDSROOT / CDS_LIC_FILE / LM_LICENSE_FILE        |
| `allegro_batch_commands`   | List all available Allegro batch subcommands          |
| `safe_batch_commands`      | List the 56 commands safe to invoke                  |

### PCB Design

| Tool                       | Purpose                                              |
|----------------------------|------------------------------------------------------|
| `find_brd_files`           | Recursively find `.brd` files with metadata          |
| `parse_brd_file`           | Extract readable strings from binary `.brd`          |
| `allegro_batch_run`        | Run one batch subcommand (timeout + allow-list)      |

### Schematic & Netlist

| Tool                       | Purpose                                              |
|----------------------------|------------------------------------------------------|
| `generate_spice_netlist`   | From template or DSL → SPICE netlist file            |
| `parse_circuit_dsl`        | Decompose DSL into components / directives           |
| `generate_schematic_dsn`   | OrCAD Capture-compatible `.DSN` XML                  |
| `netlist_to_allegro`       | SPICE → Allegro `tel` physical netlist               |

### Simulation

| Tool                       | Purpose                                              |
|----------------------------|------------------------------------------------------|
| `list_simulation_templates`| Enumerate 6 circuit templates (RC, RLC, diode, ...)  |
| `run_circuit_simulation`   | Auto-route to PSpice / ngspice + extract measurements|

---

## 📐 Architecture

```
                  ┌──────────────────────────┐
   AI Client ───► │  MCP Server (stdio)      │
  (Claude /       │   13 tools               │
   Cursor)        │                          │
                  └────────────┬─────────────┘
                               │
       ┌───────────────────────┼─────────────────────────┐
       │                       │                         │
       ▼                       ▼                         ▼
 ┌──────────────┐      ┌──────────────────┐      ┌──────────────┐
 │ env_detect   │      │ simulator        │      │ schematic    │
 │ pcb.py       │      │   (PySpice/PSpice)│      │ pcb.py       │
 │  (read-only) │      │                  │      │  (text gen)  │
 └──────────────┘      └──────────────────┘      └──────────────┘
       │                       │                         │
       ▼                       ▼                         ▼
   Allegro .brd            PSpice / ngspice           Allegro netin
   files / dirs            binary backend             physical netlist
```

Three independent modules + one MCP server bootstrap. No monolithic
controller, no hidden state beyond an env cache.

---

## 🧪 Testing

```bash
python tests/test_all.py
```

Covers 10 scenarios end-to-end:

```
TEST  1: 环境探测                [OK] CDSROOT 已识别, 75+ batch commands
TEST  2: Allegro .brd 扫描       [OK] 找到 N 个 .brd 文件
TEST  3: SPICE 网表生成          [OK] .TRAN / .END / 文件已保存
TEST  4: 电路 DSL 解析           [OK] 识别元件、控制指令
TEST  5: 生成 OrCAD DSN 文本     [OK] XML 写入成功
TEST  6: SPICE → Allegro 转换    [OK] 包含 R1 / C1 等 refdes
TEST  7: 安全命令清单            [OK] 56+ 命令白名单
TEST  8: allegro_batch help 调用 [OK] exit=0
TEST  9: MCP 工具注册表          [OK] 13 tools / 13 handlers
TEST 10: 端到端电路设计流程      [OK] .cir / .tel / .dsn 全部产出
```

### Live MCP demo

For a real MCP-protocol walkthrough (spawns the server, calls every tool
via JSON-RPC stdio, shows actual outputs and writes real files):

```bash
python demo_mcp_live.py
```

Expected outcome: **all 14 tasks** succeed; produces `_demo_rc.cir`,
`_demo_div.cir`, `_demo_step.cir`, `_demo_rc.tel`, `_demo_amp.dsn` in
`examples/`.

For just the simulation numeric results without MCP overhead:

```bash
python demo_summary.py
```

---

## 🧩 End-to-End Example

Tell your AI assistant:

> "Design a low-pass RC filter with fc=160 Hz and simulate it."

The AI calls, in order:

```
1. cadence_probe                       # learn environment
2. list_simulation_templates           # see what's available
3. generate_spice_netlist              # template=rc_lowpass, fc≈160Hz
4. run_circuit_simulation              # execute on PSpice/ngspice
5. netlist_to_allegro                  # convert for PCB layout
6. allegro_batch_run subcommand=netin  # import into Allegro
```

Output flow:

```
ai_design.cir    PSpice/ngspice  →  ai_design.out (measurements)
ai_design.tel    SPICE → Allegro  →  ready for `allegro_batch netin`
ai_design.dsn    OrCAD Capture   →  optional: hand-edit in Capture
```

---

## 🔧 Simulation Backends

The server **auto-selects** the best available backend in this order:

| Priority | Backend              | Requirements                                          |
|----------|----------------------|-------------------------------------------------------|
| 1        | `pspice.exe -b`      | PSpice on PATH **+ valid license**                    |
| 2        | `ngspice -b`         | `ngspice` on PATH (download separately)               |
| 3        | `analytical-rc`      | Built-in numpy solver for RC / divider / step (no deps) |
| 4        | `analytical-divider` | Closed-form R1+R2 voltage divider                     |

**Auto-skip behavior**: if `cadence_probe` reports `license_present: false`,
the server **skips PSpice** automatically (avoids the 60s timeout) and
goes straight to the analytical fallback. So you get instant simulation
results even without any license.

Tested backends:

- **PSpice** binary present at `C:\Cadence\SPB_23.1\tools\bin\pspice.exe`;
  `-b` mode requires an active license. When license is missing, server
  falls back gracefully.
- **ngspice** is the recommended open-source fallback. Download from
  <https://ngspice.sourceforge.io/download.html> and put `ngspice.exe` on
  PATH. (On some networks SourceForge blocks direct downloads; try MSYS2
  `mingw-w64-ucrt-x86_64-ngspice` instead.)
- **PySpice 1.5** Python wrapper is auto-detected (`pyspice_available: true`).
- **Analytical fallback** (numpy only) — solves RC low/high-pass, step
  response, and voltage dividers in closed form / closed-form-plus-numerical.
  Always available, no external binaries required.

### Demonstrated simulation results (no license required)

Run `python demo_summary.py`:

```
RC 低通滤波器 (rc_lowpass)     Vout_peak=4.36V   tau=100μs   fc=1.59kHz
RC 高通滤波器 (rc_highpass)    Vout_peak=4.36V   tau=100μs   fc=1.59kHz
RC 阶跃响应  (rc_step)         Vout_end=0.48V    tau=10ms    fc=15.9Hz
电压分频器   (voltage_divider) Vout=8.0V  (Vin=12V, R1=1k, R2=2k)
```

---

## 🛡 Security & Sandboxing

- All external commands run through `subprocess.run` with **hard timeout**.
- `allegro_batch_run` enforces a **56-command allow-list** unless
  `allow_unsafe=true` is explicitly set.
- File I/O paths are normalised; netlist files are written with **ASCII
  encoding** to avoid encoding edge cases.
- No network calls; pure local execution.
- License state is reported (`license_present: false/true`) but never assumed.

---

## 📚 Reference: Useful Allegro Batch Subcommands

Selected from the 75+ available:

| Command              | Purpose                                          |
|----------------------|--------------------------------------------------|
| `artwork`            | Generate Gerber photoplot files                  |
| `netin`              | Import netlist into board                        |
| `netrev`             | Forward-annotate schematic changes               |
| `report`             | Generate design reports (DRC, BOM, etc.)         |
| `dump_libraries`     | Extract padstacks, symbols, devices from board   |
| `brd2dml`            | Convert `.brd` to DML (Design Markup Language)  |
| `convert_gerber`     | Convert Gerber format versions                   |
| `ipc2581_out/in`     | IPC-2581 manufacturing data exchange              |
| `odbpp_out/in`       | ODB++ manufacturing data exchange                |
| `dfmwebextract`      | DFM web extraction for fabrication               |
| `perf_reports`       | Performance / signal-integrity reports           |
| `qvextract`          | Quantus verification extraction                  |
| `placement`          | Component placement (constraint-driven)          |
| `swap`               | Component / pin swap                             |

Run `allegro_batch.exe help` on your machine to get the full list.

---

## 🤝 Contributing

PRs should:

1. Run `python tests/test_all.py` cleanly (no failures).
2. Add tests for any new tool.
3. Update this README's tool table.
4. Keep the allow-list curated — every batch command added must be safe.

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

## 🔗 Related Projects

- [allegro-mcp](https://github.com/dcmcshan/allegro-mcp) — read-only MCP for `.brd` files (MIT, 4 stars).
- [skill-script-generator](https://github.com/karthikeyankcse/skill-script-generator) — NL → SKILL script generator.
- [Python-Skill Bridge](https://github.com/unihd-cag) — Python ↔ Virtuoso SKILL bridge.
- [mcp4eda.cn](https://www.mcp4eda.cn) — Chinese EDA MCP hub (no Cadence entries yet).

CadenceAI MCP is the **first combined schematic + PCB + simulation** MCP for
the Cadence SPB toolchain.