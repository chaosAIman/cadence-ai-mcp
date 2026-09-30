"""Package metadata for CadenceAI MCP.

Allow `pip install -e .` for development install.
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="cadence-ai-mcp",
    version="0.1.0",
    description="Model Context Protocol server for Cadence EDA tools "
                "(OrCAD Capture, Allegro PCB Designer, PSpice)",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="CadenceAI Bot",
    author_email="ai@cadence-mcp.local",
    license="MIT",
    url="https://github.com/cadenceai/cadence-mcp",
    packages=find_packages(exclude=["tests*", "examples*", "docs*"]),
    python_requires=">=3.10",
    install_requires=[
        "mcp>=1.0,<2.0",
        "PySpice>=1.5,<2.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "black",
            "flake8",
        ],
        "ngspice": [
            # Note: ngspice itself is shipped as a system binary, not a pip package.
            # Users must download from https://ngspice.sourceforge.io/download.html
        ],
    },
    entry_points={
        "console_scripts": [
            "cadence-mcp=cadence_mcp.server:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Electronic Design Automation (EDA)",
        "Topic :: System :: Hardware :: EDA",
    ],
)