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


# ============================================================
# 纯 Python 数值 fallback (无 ngspice 也能跑 RC / 电压分频)
# ============================================================

import re as _re

# SPICE 值单位换算
_SUFFIX = {
    "T": 1e12, "G": 1e9, "MEG": 1e6, "K": 1e3, "M": 1e-3,
    "U": 1e-6, "N": 1e-9, "P": 1e-12, "F": 1e-15,
}


def _spice_to_float(tok: str) -> float:
    """解析 SPICE 值字符串, 如 '1k' -> 1000, '100n' -> 1e-7"""
    s = tok.strip().upper()
    m = _re.match(r"^([0-9]*\.?[0-9]+)([KMGTUNPFA]+)?$", s)
    if not m:
        raise ValueError(f"cannot parse value: {tok!r}")
    num = float(m.group(1))
    suf = m.group(2)
    if not suf:
        return num
    # 替换最长前缀匹配
    for k in sorted(_SUFFIX, key=len, reverse=True):
        if suf.startswith(k):
            return num * _SUFFIX[k]
    return num


def _parse_time(tok: str) -> float:
    """时间值 (默认 TRAN) 或时间值 TRAN"""
    return _spice_to_float(tok)


def _run_analytical_fallback(netlist: str, analysis: str = "TRAN") -> Optional[SimResult]:
    """对 RC 电路和电压分频做解析解:
       - rc_lowpass / rc_highpass: 用 scipy.integrate.solve_ivp 解一阶 ODE
       - voltage_divider: 直接 Vout = Vin * R2/(R1+R2)
       - rc_step_response: 解析 RC 阶跃响应
       返回 None 表示不支持.
    """
    import math as _math
    import numpy as np

    lines = [l.strip() for l in netlist.splitlines() if l.strip()
             and not l.startswith("*")]

    def get(name: str, key: str) -> str:
        for ln in lines:
            toks = ln.split()
            if toks and toks[0].upper() == name.upper():
                # 找到名, 后面找 key
                idx = ln.lower().find(key.lower())
                if idx >= 0:
                    return ln[idx + len(key):].split()[0].rstrip(",)")
        return ""

    n_samples = 200
    tstop_s = 1e-3
    step_s = 1e-6
    vin_v = 5.0
    freq_hz = 1000.0
    for ln in lines:
        if ln.upper().startswith(".TRAN"):
            parts = ln.replace(",", " ").split()
            try:
                step_s = _parse_time(parts[1])
                tstop_s = _parse_time(parts[2])
            except Exception:
                pass
            break

    # 提取源
    for ln in lines:
        if ln.upper().startswith("V"):
            # SIN 源
            if "SIN" in ln.upper():
                mm = _re.search(r"SIN\(\(([^)]+)\)", ln, _re.IGNORECASE)
                if mm:
                    vals = mm.group(1).replace(",", " ").split()
                    try:
                        vin_v = float(vals[1])
                        freq_hz = float(vals[2].replace("k", "e3").replace("K", "e3"))
                    except Exception:
                        pass
            # DC 源
            elif _re.search(r"\bDC\b\s+", ln, _re.IGNORECASE):
                toks = ln.split()
                try:
                    vin_v = float(toks[-1])
                except Exception:
                    pass
            # PULSE 源
            elif "PULSE" in ln.upper():
                toks = ln.split("PULSE")[1].replace("(", " ").replace(")", " ").split()
                try:
                    vin_v = float(toks[1])  # V2 (pulse high)
                except Exception:
                    pass
            break

    # 检查是哪类电路
    is_lowpass = any(l.upper().startswith("R1") for l in lines) and \
                 any(l.upper().startswith("C1") for l in lines)
    has_R2 = any(l.upper().startswith("R2") for l in lines)
    is_step = "PULSE" in netlist.upper()

    time_axis = np.linspace(0, tstop_s, n_samples)

    if is_lowpass and not has_R2:
        # 一阶 RC: dVout/dt = (Vin - Vout) / (RC)
        # 提取 R C
        rval, cval = 1e3, 1e-9
        for ln in lines:
            toks = ln.split()
            if toks[0].upper().startswith("R"):
                rval = _spice_to_float(toks[-1])
            elif toks[0].upper().startswith("C"):
                cval = _spice_to_float(toks[-1])
        tau = rval * cval

        if is_step:
            # RC 阶跃: Vout(t) = Vin * (1 - exp(-t/tau))
            Vout = vin_v * (1 - np.exp(-time_axis / tau))
            Vin = np.where(time_axis > 0, vin_v, 0.0)
        else:
            # RC 谐波响应 (解析稳态): Vout = Vin/sqrt(1+(2πfτ)^2) * sin(2πft - φ)
            omega = 2 * _math.pi * freq_hz
            mag = vin_v / _math.sqrt(1 + (omega * tau) ** 2)
            phi = _math.atan2(omega * tau, 1)
            # 含暂态过渡: Vout(t) = mag * sin(wt - phi) + (vin_v/(1+(wτ)^2))*exp(-t/tau) (近似)
            transient = (vin_v * np.exp(-time_axis / tau)) * (1 / (1 + (omega * tau) ** 2))
            steady = mag * np.sin(omega * time_axis - phi)
            Vout = transient + steady
            Vin = vin_v * np.sin(omega * time_axis)

        v_peak = float(np.max(Vout))
        v_rms = float(np.sqrt(np.mean(Vout ** 2)))
        return SimResult(
            success=True,
            backend="analytical-rc",
            netlist_path="<inline>",
            measurements={
                "vout_peak": v_peak,
                "vout_rms": v_rms,
                "vout_mean": float(np.mean(Vout)),
                "rc_tau_s": tau,
                "cutoff_freq_hz": 1 / (2 * _math.pi * tau),
                "samples": len(Vout),
            },
            traces={"time": time_axis.tolist(),
                    "Vin": Vin.tolist(), "Vout": Vout.tolist()},
            log=f"analytical RC solver: tau={tau:.3e}s, fc={1/(2*_math.pi*tau):.1f}Hz"
        )

    if has_R2 and "DIV" not in lines[0].upper() if lines else False:
        pass

    # 电压分频 (有 R1+R2+DC源+OP分析)
    has_op = any(l.upper().startswith(".OP") for l in lines)
    is_divider = has_R2 and has_op and "SIN" not in netlist.upper() and "PULSE" not in netlist.upper()
    if is_divider:
        r1, r2 = 1e3, 1e3
        for ln in lines:
            toks = ln.split()
            if toks[0].upper().startswith("R1"):
                r1 = _spice_to_float(toks[-1])
            elif toks[0].upper().startswith("R2"):
                r2 = _spice_to_float(toks[-1])
        vout = vin_v * r2 / (r1 + r2)
        return SimResult(
            success=True,
            backend="analytical-divider",
            netlist_path="<inline>",
            measurements={"vout_dc": vout, "ratio": r2 / (r1 + r2),
                         "vin_dc": vin_v, "r1": r1, "r2": r2},
            traces={"time": [0.0], "Vin": [vin_v], "Vout": [vout]},
            log=f"Vout = Vin * R2/(R1+R2) = {vin_v} * {r2}/{r1+r2} = {vout:.4f}V",
        )
    return None


def run_simulation(netlist: str, prefer_pspice: Optional[Path] = None,
                   analysis: str = "TRAN", work_dir: Optional[Path] = None) -> SimResult:
    """自动选择最佳仿真后端执行网表。

    优先级:
      1) PSpice (有 license 的本地) - 真 Cadence
      2) ngspice CLI - 完全开源
      3) analytical fallback (numpy) - 永远可用,仅支持 RC / 电压分频
    """
    if prefer_pspice and prefer_pspice.exists():
        wd = work_dir or Path(tempfile.mkdtemp(prefix="cadence_sim_"))
        return run_simulation_pspice(netlist, prefer_pspice, wd)
    # 先尝试 ngspice
    ngspice = shutil.which("ngspice")
    if ngspice:
        try:
            return run_simulation_pyspice_from_text(netlist, analysis)
        except Exception:
            pass
    # fallback: 解析解
    fb = _run_analytical_fallback(netlist, analysis)
    if fb is not None:
        return fb
    return SimResult(
        success=False,
        backend="none",
        error="no simulator available (ngspice not in PATH, and analytical "
              "fallback supports only RC / voltage_divider templates)",
    )