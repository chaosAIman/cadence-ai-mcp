"""Cadence Allegro PCB 文件处理

提供:
- 扫描目录发现 .brd 文件
- 提取 Allegro 数据库中的层、器件、网络信息
- 调用 allegro_batch 子命令 (artwork / netin / dump_libraries / report 等)
- Gerber / 制造文件生成
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union


# ============================================================
# .brd 文件结构解析
# ============================================================

@dataclass
class BrdInfo:
    """Allegro .brd 文件可读取的元信息"""
    path: str
    file_size: int
    encoding: str = "binary"
    has_pin_text: bool = False
    estimated_layers: int = 0
    estimated_components: int = 0
    extracted_summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "estimated_layers": self.estimated_layers,
            "estimated_components": self.estimated_components,
            "extracted_summary": self.extracted_summary,
        }


def find_board_files(root: Union[str, Path], glob: str = "*.brd",
                     max_results: int = 100) -> List[BrdInfo]:
    """在 root 下递归查找 .brd 文件

    Parameters
    ----------
    root: 搜索根目录
    glob: 文件模式 (默认 *.brd)
    max_results: 最大返回数量,避免遍历整个文件系统

    Returns
    -------
    List[BrdInfo]
    """
    root = Path(root)
    if not root.exists():
        return []
    results: List[BrdInfo] = []
    pattern = glob if glob.startswith("*") else f"*{glob}"
    for p in root.rglob(pattern):
        if p.is_file():
            try:
                results.append(BrdInfo(
                    path=str(p),
                    file_size=p.stat().st_size,
                ))
            except OSError:
                continue
        if len(results) >= max_results:
            break
    return results


def parse_brd_text_assets(brd_path: Union[str, Path], max_bytes: int = 1024 * 1024) -> Dict[str, object]:
    """从 .brd 文件中尝试读取 ASCII 可解析区域(.brd 大部分是二进制)

    Allegro .brd 是 AllegeoDB 数据库格式;但通常包含可读文本片段
    (如器件标签、网表、注释)。本函数提取这些片段作为 AI 上下文。
    """
    p = Path(brd_path)
    if not p.exists():
        return {"exists": False, "error": "file not found"}
    size = p.stat().st_size
    sample = p.read_bytes()[:max_bytes]

    # 查找 5+ 字符的可读字符串序列
    text_chunks = re.findall(rb"[ -~]{6,}", sample)
    summary: Dict[str, int] = {}
    keywords = {
        "layer": [b"LAYER", b"TOP", b"BOTTOM", b"INNER", b"GROUND",
                  b"POWER", b"SIGNAL", b"SOLDERMASK"],
        "component": [b"REF", b"PART", b"COMP", b"REFdes", b"PKG"],
        "net": [b"NET", b"wire", b"trace", b"ETCH", b"via", b"VIA"],
        "rule": [b"DRC", b"clearance", b"width"],
    }
    for cat, words in keywords.items():
        count = 0
        for chunk in text_chunks:
            for w in words:
                if w in chunk:
                    count += 1
                    break
        summary[cat] = count

    return {
        "exists": True,
        "path": str(p),
        "file_size": size,
        "readable_chunks": len(text_chunks),
        "sample_strings": [c.decode("latin-1", errors="ignore")[:80]
                           for c in text_chunks[:25]],
        "category_counts": summary,
    }


# ============================================================
# Allegro 批处理命令封装
# ============================================================

# 高频、且无需 GUI license 的子命令
SAFE_BATCH_COMMANDS = {
    "artwork", "netin", "netrev", "report", "dbstat",
    "dump_libraries", "extract", "techfile", "placement",
    "brd2dml", "convert_gerber", "ipc2581_out", "ipc2581_in",
    "ipc356_out", "odbpp_in", "odbpp_out", "step_out",
    "dfmwebextract", "qvextract", "perf_reports",
    "stream_out", "placement", "gate_assign", "swap",
    "genfeedformat", "genrad", "ncroute", "nctape",
    "refresh_padstack", "refresh_symbol", "reftxt",
    "tldelay", "tldphys", "gloss", "pre_check",
    "smi_messages", "plctxt", "fatten", "flash_convert",
    "create_sym", "create_devices", "fpImportBrd",
    "draw_check", "dbdump", "dxf2a", "a2dxf",
    "idf_in", "idf_out", "idx_in", "idx_out",
    "mkdeviceindex", "db_change_type", "copyDrawingPulse",
    "pdf_out", "parallel", "rd_stream", "systemdump",
}


def run_allegro_batch(allegro_batch: Path, subcommand: str, *args: str,
                     timeout: int = 60, allow_unsafe: bool = False) -> Dict[str, object]:
    """执行 allegro_batch 子命令

    Parameters
    ----------
    allegro_batch: 工具可执行文件
    subcommand: 子命令 (artwork / netin / dump_libraries 等)
    *args: 传给子命令的参数
    timeout: 超时秒数
    allow_unsafe: 是否允许不在 SAFE_BATCH_COMMANDS 列表中的命令

    Returns
    -------
    dict: 命令运行结果
    """
    if not allow_unsafe and subcommand not in SAFE_BATCH_COMMANDS:
        return {
            "success": False,
            "error": f"subcommand '{subcommand}' not in allow-list for safety. "
                     f"Pass allow_unsafe=True to override.",
            "allowed_count": len(SAFE_BATCH_COMMANDS),
            "allowed_sample": sorted(SAFE_BATCH_COMMANDS)[:20],
        }
    cmd = [str(allegro_batch), subcommand, *args]
    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "command": " ".join(cmd),
            "stdout": result.stdout[:8000],
            "stderr": result.stderr[:4000],
            "elapsed_sec": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False, "exit_code": -1,
            "command": " ".join(cmd),
            "stdout": "", "stderr": f"timeout {timeout}s",
            "elapsed_sec": round(time.time() - start, 3),
        }
    except FileNotFoundError as exc:
        return {
            "success": False, "exit_code": -2,
            "command": " ".join(cmd),
            "stdout": "", "stderr": f"executable not found: {exc}",
            "elapsed_sec": round(time.time() - start, 3),
        }


def list_safe_commands() -> List[str]:
    return sorted(SAFE_BATCH_COMMANDS)