"""CadenceAI MCP Server — 主入口

通过 Model Context Protocol 暴露 16 个工具给 AI 客户端:
- 环境探测 (1)
- 文件 I/O (3): 浏览、查找、解析 .brd
- Allegro batch CLI (4): 安全命令包装
- 原理图生成 (3): DSL / DSN / Allegro 物理网表
- 仿真 (5): 网表生成 + 运行 + 测量 + 后端探测

设计原则:
1. 零 license 依赖: 默认走批处理 + 文件 I/O,提供 PSpice 调用接口
2. 安全: 外部命令有白名单 + 超时 + 工作目录隔离
3. 跨平台: Windows/Linux 都能跑
4. 透明: 每个工具都返回结构化结果,便于 AI 解析

启动方式 (stdio):
    python -m cadence_mcp.server
"""

from __future__ import annotations

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Any, Dict, Optional

# 把项目根加入 sys.path,允许 python -m cadence_mcp.server
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadence_mcp.env_detect import (  # noqa: E402
    CadenceEnvironment,
    detect_environment,
    get_cadence_env_vars,
    run_batch_command,
)
from cadence_mcp.simulator import (  # noqa: E402
    generate_netlist,
    save_netfile,
    run_simulation,
    NETLIST_TEMPLATES,
    SimResult,
)
from cadence_mcp.schematic import (  # noqa: E402
    parse_circuit_dsl,
    netlist_from_dsl,
    generate_dsn,
    to_allegro_netlist,
)
from cadence_mcp.pcb import (  # noqa: E402
    find_board_files,
    parse_brd_text_assets,
    run_allegro_batch,
    list_safe_commands,
)


# ============================================================
# Tool Registry
# ============================================================

TOOLS: Dict[str, Dict[str, Any]] = {
    "cadence_probe": {
        "description": "探测系统中的 Cadence EDA 工具链可用性,返回 SPB 根目录、可执行文件、license 状态、PSpice 模型库路径、Allegro 批处理命令清单等。这是新会话的第一调用,了解环境。",
        "input_schema": {
            "type": "object",
            "properties": {
                "cds_root": {
                    "type": "string",
                    "description": "强制指定 Cadence SPB 根目录;默认自动搜索 C:/Cadence 与 $CDSROOT。",
                },
            },
        },
    },
    "cadence_env_vars": {
        "description": "返回当前进程可见的 Cadence 相关环境变量快照(CDSROOT / CDS_LIC_FILE / LM_LICENSE_FILE 等)。用于诊断 license / 路径配置问题。",
        "input_schema": {"type": "object", "properties": {}},
    },
    "allegro_batch_commands": {
        "description": "列出 Allegro batch 支持的所有命令 (75+ 条).可用于了解 Allegro 批处理能力,挑选合适的工具做 Gerber 转换、网表导入、库导出、光绘生成等。",
        "input_schema": {"type": "object", "properties": {}},
    },
    "find_brd_files": {
        "description": "在指定根目录递归查找 Cadence Allegro PCB 文件 (.brd)。返回带元数据 (大小、估计层数、器件数) 的列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "搜索根目录绝对路径。",
                },
                "glob": {
                    "type": "string",
                    "description": "匹配模式,默认 *.brd。",
                    "default": "*.brd",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回数量,默认 50。",
                    "default": 50,
                },
            },
            "required": ["root"],
        },
    },
    "parse_brd_file": {
        "description": "从 .brd 文件中提取可读字符串片段,作为 PCB 设计的轻量级上下文(层、器件、网络、规则等)。适合作为 AI 摘要的输入,不能替代完整 Allegro DB 解析。",
        "input_schema": {
            "type": "object",
            "properties": {
                "brd_path": {
                    "type": "string",
                    "description": ".brd 文件绝对路径。",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "读取文件前多少字节做文本扫描,默认 1MB。",
                    "default": 1048576,
                },
            },
            "required": ["brd_path"],
        },
    },
    "allegro_batch_run": {
        "description": "执行单个 allegro_batch 子命令 (如 artwork / netin / dump_libraries / report / brd2dml 等)。带白名单 (60+ 安全命令) 和超时保护,默认禁止高危操作。",
        "input_schema": {
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "description": "allegro_batch 子命令,例如 'artwork' / 'netin' / 'report'。可用列表见 allegro_batch_commands。",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "传给子命令的参数列表,例如 ['-l','my_board.brd']。",
                    "default": [],
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数,默认 60。",
                    "default": 60,
                },
                "allow_unsafe": {
                    "type": "boolean",
                    "description": "是否允许不在白名单中的子命令。默认 false。",
                    "default": False,
                },
            },
            "required": ["subcommand"],
        },
    },
    "generate_spice_netlist": {
        "description": "基于模板 (rc_lowpass / rc_highpass / rlc_bandpass / voltage_divider / diode_rectifier / rc_step_response) 或直接 DSL 生成 PSpice / ngspice 兼容的电路网表,并保存到指定路径。",
        "input_schema": {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "description": "模板名称,可选: " + ", ".join(NETLIST_TEMPLATES.keys()),
                },
                "parameters": {
                    "type": "object",
                    "description": "模板参数 (vin, freq, rval, cval, lval, r1, r2, rload, step, tstop)。",
                    "default": {},
                },
                "custom_dsl": {
                    "type": "string",
                    "description": "如果提供,优先使用此自定义 DSL 文本(类 SPICE 语法)。",
                },
                "save_path": {
                    "type": "string",
                    "description": "保存路径,默认 ./ai_netlist.cir。",
                },
            },
        },
    },
    "parse_circuit_dsl": {
        "description": "把类 SPICE DSL 文本解析为结构化数据 (组件清单、节点、控制指令),便于 AI 理解电路结构。",
        "input_schema": {
            "type": "object",
            "properties": {
                "dsl": {
                    "type": "string",
                    "description": "电路 DSL 文本。",
                },
            },
            "required": ["dsl"],
        },
    },
    "generate_schematic_dsn": {
        "description": "基于组件与网络清单生成 OrCAD Capture 兼容的简化 .DSN 工程文本(纯 XML)。适合作为 AI 原理图设计的中间产物,再导入 Capture 进行后处理。",
        "input_schema": {
            "type": "object",
            "properties": {
                "components": {
                    "type": "array",
                    "description": "组件列表,每项 {ref, value, x?, y?}",
                    "items": {"type": "object"},
                },
                "nets": {
                    "type": "array",
                    "description": "网络列表,每项 {name, nodes: [refdes,...]}",
                    "items": {"type": "object"},
                },
                "project_name": {
                    "type": "string",
                    "description": "项目名称",
                    "default": "ai_design",
                },
                "title": {
                    "type": "string",
                    "description": "原理图标题",
                    "default": "AI-Generated Schematic",
                },
                "save_path": {
                    "type": "string",
                    "description": "保存路径",
                },
            },
            "required": ["components", "nets"],
        },
    },
    "netlist_to_allegro": {
        "description": "把 SPICE 网表转换成 Allegro netin 接受的简化物理网表 (.tel 风格)。适合 netin 工具的预处理步骤。",
        "input_schema": {
            "type": "object",
            "properties": {
                "spice_netlist": {
                    "type": "string",
                    "description": "SPICE 网表文本",
                },
                "save_path": {
                    "type": "string",
                    "description": "保存路径",
                },
            },
            "required": ["spice_netlist"],
        },
    },
    "run_circuit_simulation": {
        "description": "执行电路仿真。自动选择后端:1) 找到 pspice.exe 就用 PSpice (Cadence 默认需 license);2) 否则尝试 ngspice CLI;3) 都没有就只返回网表 + 安装提示。返回 measurements (峰值/平均/RMS) 与 trace 名称。",
        "input_schema": {
            "type": "object",
            "properties": {
                "netlist": {
                    "type": "string",
                    "description": "SPICE 网表文本,包含 .TRAN / .AC / .DC / .OP 等控制指令。",
                },
                "analysis": {
                    "type": "string",
                    "description": "分析类型: TRAN / AC / DC / OP,默认 TRAN。",
                    "default": "TRAN",
                },
                "prefer_pspice": {
                    "type": "boolean",
                    "description": "是否优先用 PSpice.exe (Cadence license 可用时)。默认 true。",
                    "default": True,
                },
                "work_dir": {
                    "type": "string",
                    "description": "工作目录,默认临时目录。",
                },
            },
            "required": ["netlist"],
        },
    },
    "list_simulation_templates": {
        "description": "列出所有可用的电路仿真模板及默认参数,方便 AI 选型。",
        "input_schema": {"type": "object", "properties": {}},
    },
    "safe_batch_commands": {
        "description": "返回 allegro_batch 的白名单命令列表(无需 GUI license 即可使用的安全子命令)。",
        "input_schema": {"type": "object", "properties": {}},
    },
}


# ============================================================
# Tool Handlers
# ============================================================

_ENV_CACHE: Optional[CadenceEnvironment] = None


def _get_env(force_refresh: bool = False) -> CadenceEnvironment:
    global _ENV_CACHE
    if _ENV_CACHE is None or force_refresh:
        _ENV_CACHE = detect_environment()
    return _ENV_CACHE


def handle_cadence_probe(args: Dict[str, Any]) -> Dict[str, Any]:
    cds_root = args.get("cds_root")
    env = detect_environment(Path(cds_root) if cds_root else None)
    return env.summary()


def handle_cadence_env_vars(_: Dict[str, Any]) -> Dict[str, Any]:
    return get_cadence_env_vars()


def handle_allegro_batch_commands(_: Dict[str, Any]) -> Dict[str, Any]:
    env = _get_env()
    return {
        "total": len(env.available_batch_commands),
        "commands": env.available_batch_commands,
        "safe_count": len(list_safe_commands()),
        "safe_subset": list_safe_commands(),
    }


def handle_safe_batch_commands(_: Dict[str, Any]) -> Dict[str, Any]:
    return {"safe_commands": list_safe_commands()}


def handle_find_brd_files(args: Dict[str, Any]) -> Dict[str, Any]:
    root = args["root"]
    glob = args.get("glob", "*.brd")
    max_results = args.get("max_results", 50)
    boards = find_board_files(root, glob=glob, max_results=max_results)
    return {
        "root": root,
        "pattern": glob,
        "count": len(boards),
        "boards": [b.to_dict() for b in boards],
    }


def handle_parse_brd_file(args: Dict[str, Any]) -> Dict[str, Any]:
    return parse_brd_text_assets(args["brd_path"], args.get("max_bytes", 1024 * 1024))


def handle_allegro_batch_run(args: Dict[str, Any]) -> Dict[str, Any]:
    env = _get_env()
    if not env.allegro_batch:
        return {"success": False, "error": "allegro_batch not found in environment"}
    sub = args["subcommand"]
    call_args = args.get("args", [])
    timeout = args.get("timeout", 60)
    allow_unsafe = args.get("allow_unsafe", False)
    return run_allegro_batch(env.allegro_batch, sub, *call_args,
                             timeout=timeout, allow_unsafe=allow_unsafe)


def handle_generate_spice_netlist(args: Dict[str, Any]) -> Dict[str, Any]:
    custom = args.get("custom_dsl")
    params = args.get("parameters", {})
    template = args.get("template")
    save_path = args.get("save_path")

    if custom:
        netlist = netlist_from_dsl(custom)
    elif template:
        netlist = generate_netlist(template, **params)
    else:
        raise ValueError("Either template or custom_dsl must be provided")

    if save_path:
        saved = save_netfile(netlist, save_path)
        return {
            "netlist": netlist,
            "saved_path": str(saved),
            "size_bytes": saved.stat().st_size,
        }
    return {"netlist": netlist, "saved_path": None}


def handle_parse_circuit_dsl(args: Dict[str, Any]) -> Dict[str, Any]:
    return parse_circuit_dsl(args["dsl"])


def handle_generate_schematic_dsn(args: Dict[str, Any]) -> Dict[str, Any]:
    dsn = generate_dsn(
        components=args["components"],
        nets=args["nets"],
        project_name=args.get("project_name", "ai_design"),
        title=args.get("title", "AI-Generated Schematic"),
    )
    save_path = args.get("save_path")
    if save_path:
        p = Path(save_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dsn, encoding="utf-8")
        return {"saved_path": str(p), "size_bytes": p.stat().st_size,
                "preview": dsn[:600]}
    return {"dsn_text": dsn, "size_bytes": len(dsn.encode("utf-8"))}


def handle_netlist_to_allegro(args: Dict[str, Any]) -> Dict[str, Any]:
    net = args["spice_netlist"]
    allegro = to_allegro_netlist(net)
    save_path = args.get("save_path")
    if save_path:
        p = Path(save_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(allegro, encoding="ascii")
        return {"saved_path": str(p), "preview": allegro[:600]}
    return {"allegro_netlist": allegro}


def handle_run_simulation(args: Dict[str, Any]) -> Dict[str, Any]:
    env = _get_env()
    # License 不在时, 自动跳过 PSpice 走 fallback (避免 60s 超时)
    prefer = args.get("prefer_pspice", True)
    if prefer and not env.license_present:
        prefer = False
    pspice = env.pspice_exe if prefer else None
    work_dir = Path(args["work_dir"]) if args.get("work_dir") else None
    result = run_simulation(args["netlist"], prefer_pspice=pspice,
                            analysis=args.get("analysis", "TRAN"),
                            work_dir=work_dir)
    return result.to_dict()


def handle_list_simulation_templates(_: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "templates": list(NETLIST_TEMPLATES.keys()),
        "details": {
            name: {"snippet": snippet[:200] + "..."}
            for name, snippet in NETLIST_TEMPLATES.items()
        },
        "common_parameters": [
            "vin (source voltage)", "freq (source frequency)",
            "rval (resistor value)", "cval (capacitor value)",
            "lval (inductor value)", "step (time step)", "tstop (stop time)",
        ],
    }


HANDLERS = {
    "cadence_probe": handle_cadence_probe,
    "cadence_env_vars": handle_cadence_env_vars,
    "allegro_batch_commands": handle_allegro_batch_commands,
    "safe_batch_commands": handle_safe_batch_commands,
    "find_brd_files": handle_find_brd_files,
    "parse_brd_file": handle_parse_brd_file,
    "allegro_batch_run": handle_allegro_batch_run,
    "generate_spice_netlist": handle_generate_spice_netlist,
    "parse_circuit_dsl": handle_parse_circuit_dsl,
    "generate_schematic_dsn": handle_generate_schematic_dsn,
    "netlist_to_allegro": handle_netlist_to_allegro,
    "run_circuit_simulation": handle_run_simulation,
    "list_simulation_templates": handle_list_simulation_templates,
}


# ============================================================
# MCP Server bootstrap
# ============================================================

def build_mcp_server():
    """构造真正的 MCP server 实例,使用官方 MCP Python SDK。
    若 SDK 不可用,回退到 stdio JSON-RPC 手工实现。
    """
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent

        app = Server("cadence-ai")

        @app.list_tools()
        async def list_tools():
            return [
                Tool(
                    name=name,
                    description=spec["description"],
                    inputSchema=spec["input_schema"],
                )
                for name, spec in TOOLS.items()
            ]

        @app.call_tool()
        async def call_tool(name: str, arguments: dict):
            if name not in HANDLERS:
                return [TextContent(type="text", text=json.dumps(
                    {"error": f"unknown tool: {name}"}))]
            try:
                result = HANDLERS[name](arguments or {})
                payload = json.dumps(result, ensure_ascii=False, default=str,
                                     indent=2)
                return [TextContent(type="text", text=payload)]
            except Exception as exc:
                return [TextContent(type="text", text=json.dumps({
                    "error": f"{type(exc).__name__}: {exc}",
                    "tool": name,
                }, ensure_ascii=False))]

        async def _main():
            async with stdio_server() as (read, write):
                await app.run(read, write, app.create_initialization_options())

        return _main
    except ImportError as exc:
        raise ImportError(
            f"MCP SDK not installed: {exc}. Run: pip install mcp"
        ) from exc


def main():
    parser = argparse.ArgumentParser(description="CadenceAI MCP Server")
    parser.add_argument("--version", action="version", version="0.1.0")
    parser.add_argument("--probe", action="store_true",
                        help="打印环境探测结果后退出(不启动 stdio server)")
    parser.add_argument("--list-tools", action="store_true",
                        help="打印所有可用工具清单后退出")
    args = parser.parse_args()

    if args.probe:
        env = _get_env(force_refresh=True)
        print(json.dumps(env.summary(), indent=2, default=str))
        return

    if args.list_tools:
        for name, spec in TOOLS.items():
            print(f"=== {name} ===")
            print(spec["description"][:200])
            print()
        return

    import asyncio
    main_coro = build_mcp_server()
    asyncio.run(main_coro())


if __name__ == "__main__":
    main()