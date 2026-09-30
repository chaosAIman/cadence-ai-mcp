"""跑demo并把仿真结果单独抽出来打印"""
import subprocess, json, os
from pathlib import Path

HERE = Path(__file__).parent
env = dict(os.environ)
env["PYTHONPATH"] = str(HERE)
env["CADENCE_ROOT"] = r"C:\Cadence\SPB_23.1"

# 直接 inline 测试仿真 (跳过demo的stdout污染)
import importlib, sys
sys.path.insert(0, str(HERE))
from cadence_mcp.simulator import run_simulation, generate_netlist

print("=" * 70)
print("  CadenceAI MCP - 仿真实测 (无需 ngspice, 纯数值)")
print("=" * 70)

for tname, params, label in [
    ("rc_lowpass", {"vin":"5","freq":"1k","rval":"1k","cval":"100n"}, "RC 低通滤波器"),
    ("rc_highpass", {"vin":"5","freq":"1k","rval":"1k","cval":"100n"}, "RC 高通滤波器"),
    ("rc_step_response", {"vin":"5","rval":"10k","cval":"1u"}, "RC 阶跃响应"),
    ("voltage_divider", {"vin":"12","r1":"1k","r2":"2k"}, "电压分频器"),
]:
    net = generate_netlist(tname, **params)
    r = run_simulation(net, analysis="TRAN")
    print(f"\n>>> {label} ({tname})")
    print(f"    success    : {r.success}")
    print(f"    backend    : {r.backend}")
    print(f"    log        : {r.log[:100]}")
    for k, v in r.measurements.items():
        if isinstance(v, float):
            print(f"    {k:18s}: {v:.6g}")
        else:
            print(f"    {k:18s}: {v}")
    if r.traces.get("Vout"):
        print(f"    Vout[0..3] : {[round(x,4) for x in r.traces['Vout'][:4]]}")
        print(f"    Vout[end]  : {round(r.traces['Vout'][-1], 4)}")