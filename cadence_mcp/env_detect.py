"""Cadence 环境探测和路径解析

处理 SPB_23.1 安装路径、工具路径、环境变量配置。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class CadenceEnvironment:
    """探测到的 Cadence EDA 工具环境"""

    cds_root: Optional[Path] = None
    tools_bin: Optional[Path] = None
    pspice_dir: Optional[Path] = None
    capture_dir: Optional[Path] = None
    pcb_dir: Optional[Path] = None
    pspice_libs: Optional[Path] = None
    allegro_batch: Optional[Path] = None
    pspice_exe: Optional[Path] = None
    capture_exe: Optional[Path] = None
    allegro_exe: Optional[Path] = None
    python_exe: Optional[Path] = None
    ngspice_available: bool = False
    pyspice_available: bool = False
    license_present: bool = False
    available_batch_commands: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, object]:
        return {
            "cds_root": str(self.cds_root) if self.cds_root else None,
            "tools_bin": str(self.tools_bin) if self.tools_bin else None,
            "allegro_batch": str(self.allegro_batch) if self.allegro_batch else None,
            "pspice_exe": str(self.pspice_exe) if self.pspice_exe else None,
            "capture_exe": str(self.capture_exe) if self.capture_exe else None,
            "pspice_libs": str(self.pspice_libs) if self.pspice_libs else None,
            "license_present": self.license_present,
            "ngspice_available": self.ngspice_available,
            "pyspice_available": self.pyspice_available,
            "batch_commands_count": len(self.available_batch_commands),
            "sample_batch_commands": self.available_batch_commands[:25],
        }


# 默认搜索候选根路径
DEFAULT_SEARCH_ROOTS = [
    Path("C:/Cadence"),
    Path("C:/Program Files/Cadence"),
    Path("C:/Program Files (x86)/Cadence"),
    Path("D:/Cadence"),
    Path("/opt/cadence"),
    Path("/usr/local/cadence"),
    Path("/Applications/Cadence"),
]


def _find_sp_root() -> Optional[Path]:
    """在常见位置查找 SPB 根目录 (含 SPB_xx.x 子目录)"""
    env_root = os.environ.get("CDSROOT") or os.environ.get("CADENCE_HOME")
    if env_root:
        p = Path(env_root)
        if p.exists():
            return p
    for root in DEFAULT_SEARCH_ROOTS:
        if not root.exists():
            continue
        for sub in root.iterdir():
            if sub.is_dir() and sub.name.startswith("SPB_"):
                return sub
    return None


def _probe_python_extras(env: CadenceEnvironment) -> None:
    """探测 Python 端的仿真替代品"""
    env.ngspice_available = shutil.which("ngspice") is not None
    try:
        import PySpice  # noqa
        env.pyspice_available = True
    except Exception:
        env.pyspice_available = False


def _list_batch_commands(allegro_batch: Path) -> List[str]:
    """运行 allegro_batch help 取出可用子命令"""
    try:
        out = subprocess.run(
            [str(allegro_batch), "help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        cmds: List[str] = []
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("Allegro") or line.startswith("To ") or line.startswith("Available"):
                continue
            if line.replace("_", "").isalnum():
                cmds.append(line)
        return cmds
    except Exception:
        return []


def detect_environment(cds_root: Optional[Path] = None) -> CadenceEnvironment:
    """探测当前系统的 Cadence 工具链可用情况。

    Parameters
    ----------
    cds_root:
        强制指定 Cadence SPB 根目录;若为 None 则自动搜索。

    Returns
    -------
    CadenceEnvironment
        描述各组件是否存在、可执行路径等。
    """
    env = CadenceEnvironment()
    root = cds_root or _find_sp_root()
    if not root:
        return env
    env.cds_root = root
    bin_dir = root / "tools" / "bin"
    if bin_dir.exists():
        env.tools_bin = bin_dir
        for name, target in [
            ("allegro_batch.exe", "allegro_batch"),
            ("allegro.exe", "allegro"),
            ("Capture.exe", "capture"),
            ("pspice.exe", "pspice_exe"),
        ]:
            p = bin_dir / name
            if p.exists():
                setattr(env, target, p)
        env.pspice_dir = root / "tools" / "pspice"
        env.capture_dir = root / "tools" / "capture"
        env.pcb_dir = root / "tools" / "pcb"

    # PSpice 模型库路径
    candidate_libs = [
        root / "tools" / "pspice" / "Library",
        root / "tools" / "capture" / "library",
    ]
    for c in candidate_libs:
        if c.exists():
            env.pspice_libs = c
            break

    # license 检测
    lic_dir = root / "licensing"
    if lic_dir.exists():
        env.license_present = any(lic_dir.glob("*.dat"))

    # Python 备用仿真
    _probe_python_extras(env)

    # 列出可用批处理命令
    if env.allegro_batch and env.allegro_batch.exists():
        env.available_batch_commands = _list_batch_commands(env.allegro_batch)

    return env


def run_batch_command(
    allegro_batch: Path,
    subcommand: str,
    *args: str,
    timeout: int = 60,
) -> Dict[str, object]:
    """安全执行 allegro_batch 子命令,统一捕获输出。

    Returns
    -------
    dict with keys: success, stderr, stdout, exit_code, elapsed_sec
    """
    import time

    start = time.time()
    cmd = [str(allegro_batch), subcommand, *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout[:8000],
            "stderr": result.stderr[:4000],
            "command": " ".join(cmd),
            "elapsed_sec": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "command": " ".join(cmd),
            "elapsed_sec": round(time.time() - start, 3),
        }
    except FileNotFoundError as exc:
        return {
            "success": False,
            "exit_code": -2,
            "stdout": "",
            "stderr": f"Executable not found: {exc}",
            "command": " ".join(cmd),
            "elapsed_sec": round(time.time() - start, 3),
        }


def get_cadence_env_vars() -> Dict[str, str]:
    """返回当前进程相关的 Cadence 环境变量快照。"""
    keys = ("CDSROOT", "CADENCE_HOME", "CDS_LIC_FILE", "LM_LICENSE_FILE",
            "PATH", "PSPICE_DIR", "CDS_SITE", "CDS_INSTALLED")
    return {k: v for k, v in os.environ.items() if k in keys or k.startswith("CDS_")}