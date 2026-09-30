"""SPICE 仿真引擎

支持两种后端:
- PySpice + ngspice (无需 Cadence license,完全开源)
- 直接生成 PSpice 网表文件 (兼容 OrCAD PSpice)

主要仿真类型:.TRAN (瞬态) / .AC (交流) / .DC (直流扫描) / .OP (工作点)
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

try:
    from PySpice.Spice.Netlist import Circuit, SubCircuitFactory
    from PySpice.Unit import *
    PYSPICE_OK = True
except Exception:  # pragma: no cover
    PYSPICE_OK = False


# ============================================================
# 基础 SPICE 网表生成器
# ============================================================

NETLIST_TEMPLATES = {
    "rc_lowpass": """* RC Low-Pass Filter
V1  in  0  SIN(0 {vin} {freq} 0 0 0)
R1  in  out  {rval}
C1  out 0   {cval}
.TRAN {step} {tstop}
.PROBE
.END
""",
    "rc_highpass": """* RC High-Pass Filter
V1  in  0  SIN(0 {vin} {freq} 0 0 0)
C1  in  out  {cval}
R1  out 0    {rval}
.TRAN {step} {tstop}
.PROBE
.END
""",
    "rlc_bandpass": """* RLC Band-Pass Filter
V1  in  0  SIN(0 {vin} {freq} 0 0 0)
R1  in  out  {rval}
L1  out mid  {lval}
C1  mid 0    {cval}
.TRAN {step} {tstop}
.AC DEC 10 1 1Meg
.PROBE
.END
""",
    "voltage_divider": """* Voltage Divider
V1  in  0  DC {vin}
R1  in  out  {r1}
R2  out 0    {r2}
.OP
.END
""",
    "diode_rectifier": """* Half-Wave Diode Rectifier
V1  in  0  SIN(0 {vin} {freq} 0 0 0)
D1  in  out  D1N4148
R1  out 0    {rload}
.TRAN {step} {tstop}
.PROBE
.END
""",
    "rc_step_response": """* RC Step Response
V1  in  0  PULSE(0 {vin} 0 1ns 1ns 1ms 2ms)
R1  in  out  {rval}
C1  out 0    {cval}
.TRAN {step} {tstop}
.PROBE
.END
""",
}


def generate_netlist(template: str, **kwargs) -> str:
    """基于模板生成 SPICE 网表字符串"""
    if template not in NETLIST_TEMPLATES:
        raise ValueError(f"Unknown template '{template}'. "
                         f"Choose from: {list(NETLIST_TEMPLATES)}")
    defaults = {
        "vin": "5", "freq": "1k",
        "rval": "1k", "cval": "1n", "lval": "10m",
        "r1": "1k", "r2": "1k", "rload": "1k",
        "step": "1us", "tstop": "3ms",
    }
    defaults.update(kwargs)
    return NETLIST_TEMPLATES[template].format(**defaults)


def save_netfile(netlist: str, path: Union[str, Path]) -> Path:
    """把网表写到磁盘,确保 ASCII 编码"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="ascii") as f:
        f.write(netlist)
    return p


# ============================================================
# 仿真执行器
# ============================================================

@dataclass
class SimResult:
    success: bool
    backend: str
    netlist_path: Optional[str] = None
    log: str = ""
    traces: Dict[str, List[float]] = field(default_factory=dict)
    time_axis: List[float] = field(default_factory=list)
    measurements: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "success": self.success,
            "backend": self.backend,
            "netlist_path": self.netlist_path,
            "log": self.log[:6000],
            "measurements": self.measurements,
            "trace_count": len(self.traces),
            "trace_names": list(self.traces.keys()),
            "time_points": len(self.time_axis),
            "error": self.error,
        }


def _safe_float(v) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return 0.0
        return x
    except (TypeError, ValueError):
        return 0.0


def _compute_measurements(time_axis: List[float],
                          traces: Dict[str, List[float]]) -> Dict[str, float]:
    """基础测量:峰值、平均、RMS"""
    measurements: Dict[str, float] = {}
    for name, vals in traces.items():
        if not vals or not time_axis:
            continue
        try:
            vmin = min(vals)
            vmax = max(vals)
            vpp = vmax - vmin
            vavg = sum(vals) / len(vals)
            vrms = math.sqrt(sum(v * v for v in vals) / len(vals))
            if "time" in name.lower():
                continue
            measurements[f"{name}_min"] = round(vmin, 6)
            measurements[f"{name}_max"] = round(vmax, 6)
            measurements[f"{name}_peak_to_peak"] = round(vpp, 6)
            measurements[f"{name}_avg"] = round(vavg, 6)
            measurements[f"{name}_rms"] = round(vrms, 6)
        except Exception:
            continue
    if time_axis:
        measurements["duration"] = round(time_axis[-1] - time_axis[0], 9)
        measurements["points"] = float(len(time_axis))
    return measurements


def run_simulation_pyspice_from_text(netlist: str, analysis: str = "TRAN") -> SimResult:
    """把已有 SPICE 网表交给 PySpice 解析,然后运行仿真。

    使用 PySpice.Spice.Simulation 中的 simulator 创建方式。
    PySpice 1.5 依赖 IC 中 `subprocess` 调用 ngspice.exe。
    """
    if not PYSPICE_OK:
        return SimResult(success=False, backend="pyspice",
                         error="PySpice not available")

    cir_path = Path(tempfile.mkdtemp(prefix="cadence_ai_")) / "circuit.cir"
    save_netfile(netlist, cir_path)

    # 直接用 subprocess 调本地 ngspice(若存在)
    ngspice = shutil.which("ngspice")
    if not ngspice:
        return SimResult(
            success=False,
            backend="pyspice",
            netlist_path=str(cir_path),
            error="ngspice executable not in PATH. Install ngspice to run analyses. "
                  "Netlist was generated successfully and is ready for any SPICE-compatible simulator.",
        )

    # ngspice -b batch, -o output
    out_path = cir_path.with_suffix(".out")
    proc = subprocess.run(
        [ngspice, "-b", "-o", str(out_path), str(cir_path)],
        capture_output=True, text=True, timeout=60,
    )
    log = (proc.stdout or "") + (proc.stderr or "")
    if not out_path.exists() or proc.returncode != 0:
        return SimResult(
            success=False, backend="ngspice-cli",
            netlist_path=str(cir_path), log=log[:4000],
            error=f"ngspice exit={proc.returncode}",
        )

    # 解析 .out 文件(简化):取 REAL 数字表
    raw = out_path.read_text(encoding="latin-1", errors="ignore")
    traces, time_axis = _parse_ngspice_output(raw)
    measurements = _compute_measurements(time_axis, traces)
    return SimResult(
        success=True, backend="ngspice-cli",
        netlist_path=str(cir_path), log=log[:4000],
        traces=traces, time_axis=time_axis, measurements=measurements,
    )


def _parse_ngspice_output(raw: str) -> tuple:
    """解析 ngspice -b 的 .out 文件,提取瞬态数据的简化版本"""
    traces: Dict[str, List[float]] = {}
    time_axis: List[float] = []

    # 找 "Transient Analysis" 之后的 Index 列头
    m_idx = raw.lower().find("index")
    if m_idx < 0:
        return traces, time_axis
    # 跳过列头
    lines = raw.splitlines()
    in_data = False
    column_names: List[str] = []
    for line in lines:
        low = line.lower().strip()
        if low.startswith("index") and "time" in low:
            tokens = line.split()
            column_names = tokens[1:]  # 跳过 "Index"
            in_data = True
            continue
        if in_data:
            if not low or "----" in low:
                if traces:
                    break
                continue
            tokens = line.split()
            try:
                vals = [float(t) for t in tokens]
            except ValueError:
                continue
            if len(vals) - 1 != len(column_names):
                continue
            if not time_axis:
                for i, name in enumerate(column_names, start=1):
                    if name.lower() == "time":
                        time_axis.append(vals[i - 1])
                    traces.setdefault(name, []).append(vals[i - 1])
            else:
                for i, name in enumerate(column_names, start=1):
                    if name.lower() == "time":
                        time_axis.append(vals[i - 1])
                    else:
                        traces.setdefault(name, []).append(vals[i - 1])
    return traces, time_axis


def run_simulation_pspice(netlist: str, pspice_exe: Path, work_dir: Path,
                          timeout: int = 60) -> SimResult:
    """调用本地 PSpice.exe -b 运行(需 license)"""
    cir_path = work_dir / "ai_circuit.cir"
    save_netfile(netlist, cir_path)
    try:
        proc = subprocess.run(
            [str(pspice_exe), "-b", str(cir_path)],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        ok = proc.returncode == 0
        return SimResult(
            success=ok,
            backend="pspice-binary",
            netlist_path=str(cir_path),
            log=(proc.stdout + proc.stderr)[:6000],
            error=None if ok else f"PSpice exit={proc.returncode}",
        )
    except subprocess.TimeoutExpired:
        return SimResult(success=False, backend="pspice-binary", error=f"timeout {timeout}s")
    except FileNotFoundError:
        return SimResult(success=False, backend="pspice-binary", error="pspice.exe not found")


def run_simulation(netlist: str, prefer_pspice: Optional[Path] = None,
                   analysis: str = "TRAN", work_dir: Optional[Path] = None) -> SimResult:
    """自动选择最佳仿真后端执行网表。

    优先级: PSpice (有 license 的本地) -> PySpice/ngspice (开源 fallback)
    """
    if prefer_pspice and prefer_pspice.exists():
        wd = work_dir or Path(tempfile.mkdtemp(prefix="cadence_sim_"))
        return run_simulation_pspice(netlist, prefer_pspice, wd)
    return run_simulation_pyspice_from_text(netlist, analysis)