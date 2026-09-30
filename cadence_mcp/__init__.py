"""Cadence EDA MCP Server

提供 Model Context Protocol 接口,让 AI 助手能够:
- 浏览/解析 Cadence Allegro PCB 设计文件 (.brd)
- 生成 SPICE 网表 (OrCAD Capture 兼容格式)
- 运行电路仿真 (基于 PySpice + Cadence 模型库)
- 调用 Allegro 批处理命令 (artwork、netin、placement 等 75+ 种)
- 生成 Gerber/光绘数据
- 导出库文件 (符号、封装、padstack)

设计目标:
- 零 license 依赖 (使用命令行批处理 + 文件 I/O)
- 跨平台 (Windows/Linux)
- 安全沙箱 (所有外部命令有超时和参数白名单)
"""

__version__ = "0.1.0"
__author__ = "CadenceAI"