"""Live MCP demo v5: 全部参数名修正, 真实产出 + 真实仿真数值"""
import json
import subprocess
import os
import time
from pathlib import Path

HERE = Path(__file__).parent
env = dict(os.environ)
env["PYTHONPATH"] = str(HERE)
env["CADENCE_ROOT"] = r"C:\Cadence\SPB_23.1"

p = subprocess.Popen(
    [r"C:\Users\Administrator.USER-20260707BR\AppData\Local\Programs\Python\Python312\python.exe",
     "-m", "cadence_mcp.server"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env=env, cwd=str(HERE))

_next = [0]


def call(method, params=None, timeout=60):
    _next[0] += 1
    i = _next[0]
    obj = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        obj["params"] = params
    p.stdin.write((json.dumps(obj) + "\n").encode())
    p.stdin.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            return None
        try:
            d = json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            continue
        if d.get("id") == i:
            return d
    return None


def invoke(name, args):
    print(f"\n>> {name}({json.dumps(args, ensure_ascii=False)[:180]})")
    r = call("tools/call", {"name": name, "arguments": args}, 60)
    if r is None:
        print("   NO RESPONSE"); return None
    if "error" in r:
        print(f"   ERROR: {r['error'].get('message','')[:200]}"); return None
    txts = [c.get("text", "") for c in r.get("result", {}).get("content", [])]
    out = "\n".join(txts)
    if len(out) > 1000:
        print("   " + out[:500] + "\n   ...\n   " + out[-200:])
    else:
        print("   " + out)
    try:
        return json.loads(txts[0]) if txts else None
    except Exception:
        return out


print("=" * 70)
print("  CadenceAI MCP server - LIVE DEMO v5 (full coverage)")
print("=" * 70)

r = call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "demo", "version": "1.0"}})
print(f"\n[init] {r.get('result',{}).get('serverInfo')}")
p.stdin.write((json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}) + "\n").encode())
p.stdin.flush()

# [任务 1] 环境探测
invoke("cadence_probe", {})

# [任务 2] 生成 RC 低通网表 -> save_path 自动落盘
print("\n" + "-" * 70)
print("[任务 2] AI 生成 RC 低通 SPICE 网表 + 自动落盘")
print("-" * 70)
r2 = invoke("generate_spice_netlist", {
    "template": "rc_lowpass",
    "parameters": {"vin": "5", "freq": "1k", "rval": "1k", "cval": "100n"},
    "save_path": str(HERE / "examples" / "_demo_rc.cir")
})

# [任务 3] 电压分频器
print("\n" + "-" * 70)
print("[任务 3] AI 生成电压分频器 + 落盘")
print("-" * 70)
invoke("generate_spice_netlist", {
    "template": "voltage_divider",
    "parameters": {"vin": "12", "r1": "1k", "r2": "2k"},
    "save_path": str(HERE / "examples" / "_demo_div.cir")
})

# [任务 4] RC 阶跃
print("\n" + "-" * 70)
print("[任务 4] AI 生成 RC 阶跃响应网表")
print("-" * 70)
invoke("generate_spice_netlist", {
    "template": "rc_step_response",
    "parameters": {"vin": "5", "rval": "10k", "cval": "1u"},
    "save_path": str(HERE / "examples" / "_demo_step.cir")
})

# [任务 5] 解析网表
print("\n" + "-" * 70)
print("[任务 5] AI 解析生成的 RC 网表")
print("-" * 70)
with open(HERE / "examples" / "_demo_rc.cir", encoding="utf-8") as f:
    invoke("parse_circuit_dsl", {"dsl": f.read()})

# [任务 6] SPICE -> Allegro TEL
print("\n" + "-" * 70)
print("[任务 6] AI 把 SPICE 网表转 Allegro netin 格式")
print("-" * 70)
with open(HERE / "examples" / "_demo_rc.cir", encoding="utf-8") as f:
    invoke("netlist_to_allegro", {
        "spice_netlist": f.read(),
        "save_path": str(HERE / "examples" / "_demo_rc.tel")
    })

# [任务 7] DSN 原理图 (正确参数: components(ref/value) + nets(name/nodes))
print("\n" + "-" * 70)
print("[任务 7] AI 生成 DSN 原理图 XML")
print("-" * 70)
invoke("generate_schematic_dsn", {
    "components": [
        {"ref": "R1", "type": "resistor", "value": "1k"},
        {"ref": "C1", "type": "capacitor", "value": "100n"},
        {"ref": "V1", "type": "vsource", "value": "5"},
    ],
    "nets": [
        {"name": "in", "nodes": ["V1", "R1"]},
        {"name": "out", "nodes": ["R1", "C1"]},
        {"name": "0", "nodes": ["C1", "V1"]},
    ],
    "project_name": "ai_rc_lpf",
    "title": "AI Generated RC LPF",
    "save_path": str(HERE / "examples" / "_demo_amp.dsn")
})

# [任务 8] 真跑 RC 仿真 (参数名: netlist 不是 spice_netlist)
print("\n" + "-" * 70)
print("[任务 8] AI 真跑 RC 仿真 (analytical fallback)")
print("-" * 70)
with open(HERE / "examples" / "_demo_rc.cir", encoding="utf-8") as f:
    r8 = invoke("run_circuit_simulation", {
        "netlist": f.read(),
        "analysis": "TRAN"
    })
if isinstance(r8, dict):
    m = r8.get("measurements", {})
    if m:
        print(f"   *** 仿真结果: Vout peak={m.get('vout_peak'):.3f}V, "
              f"cutoff={m.get('cutoff_freq_hz'):.1f}Hz, tau={m.get('rc_tau_s'):.2e}s ***")

# [任务 9] 真跑电压分频器仿真
print("\n" + "-" * 70)
print("[任务 9] AI 真跑电压分频器仿真")
print("-" * 70)
with open(HERE / "examples" / "_demo_div.cir", encoding="utf-8") as f:
    r9 = invoke("run_circuit_simulation", {
        "netlist": f.read(),
        "analysis": "OP"
    })
if isinstance(r9, dict):
    m = r9.get("measurements", {})
    if m:
        print(f"   *** Vout = {m.get('vout_dc'):.3f}V (R2/(R1+R2) = {m.get('ratio'):.3f}) ***")

# [任务 10] RC 阶跃响应
print("\n" + "-" * 70)
print("[任务 10] AI 跑 RC 阶跃响应")
print("-" * 70)
with open(HERE / "examples" / "_demo_step.cir", encoding="utf-8") as f:
    r10 = invoke("run_circuit_simulation", {
        "netlist": f.read(),
        "analysis": "TRAN"
    })
if isinstance(r10, dict):
    m = r10.get("measurements", {})
    if m:
        print(f"   *** Vout_final={m.get('vout_peak'):.3f}V (期望 ~{5:.3f}V) ***")

# [任务 11] allegro_batch help (非白名单 -> 安全拒绝)
print("\n" + "-" * 70)
print("[任务 11] AI 尝试调用非白名单命令 (预期安全拒绝)")
print("-" * 70)
invoke("allegro_batch_run", {"subcommand": "help"})

# [任务 12] allegro_batch 白名单
print("\n" + "-" * 70)
print("[任务 12] AI 查询白名单命令")
print("-" * 70)
invoke("safe_batch_commands", {})

# [任务 13] 找 .brd
print("\n" + "-" * 70)
print("[任务 13] AI 递归找 .brd 文件")
print("-" * 70)
invoke("find_brd_files", {"root": str(HERE / "tests")})

# [任务 14] 列仿真模板
print("\n" + "-" * 70)
print("[任务 14] AI 列所有可用仿真模板")
print("-" * 70)
invoke("list_simulation_templates", {})

p.stdin.close()
try:
    err = p.stderr.read().decode("utf-8", errors="ignore")
    lines = [l for l in err.splitlines() if l.strip() and "Tool" in l]
    if lines:
        print("\n[server 警告]:")
        for l in lines[-3:]:
            print(" ", l[:200])
except Exception:
    pass
p.terminate()

# 汇总
print("\n" + "=" * 70)
print("  实际生成的产物 (来自 AI 调用)")
print("=" * 70)
for f in ["_demo_rc.cir", "_demo_div.cir", "_demo_step.cir",
           "_demo_rc.tel", "_demo_amp.dsn"]:
    p = HERE / "examples" / f
    if p.exists():
        print(f"\n[+] {p.relative_to(HERE)} ({p.stat().st_size} B)")
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
        for line in lines[:10]:
            print("    |", line)
        if len(lines) > 10:
            print(f"    | ... 共 {len(lines)} 行")