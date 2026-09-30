# CadenceAI 项目管理手册

> MCP server for Cadence EDA — AI 自动化原理图、PCB、仿真。
> 仓库：https://github.com/chaosAIman/cadence-ai-mcp

---

## 1. 项目目标

把 Cadence SPB 23.1（OrCAD Capture + Allegro PCB + PSpice）暴露为 **MCP (Model Context Protocol)** 服务，
让 AI 能：

| 能力 | 工具 |
|------|------|
| 探测环境 | `detect_cadence`, `list_installation` |
| 生成原理图网表 | `generate_schematic_netlist`, `generate_allegro_tel` |
| 生成 DSN 工程 | `generate_dsn_project` |
| 运行仿真 | `run_simulation`（基于 PySpice） |
| PCB 工具链 | `pcb_*`（封装 allegro_batch 命令） |
| 仿真数据分析 | `parse_sim_results` |

---

## 2. 仓库结构

```
cadence-ai-mcp/
├── cadence_mcp/              # 核心包
│   ├── __init__.py
│   ├── env_detect.py         # Cadence 安装探测
│   ├── simulator.py          # 仿真（PySpice 封装）
│   ├── schematic.py          # 网表 / DSN 生成
│   ├── pcb.py                # PCB 工具包装
│   └── server.py             # MCP server 入口
├── tests/
│   └── test_all.py           # 端到端测试
├── examples/
│   └── rc_filter.cir         # SPICE 网表示例
├── README.md                 # 项目说明
├── PROJECT.md                # 本文件
├── LICENSE                   # MIT
├── setup.py                  # 安装入口
├── pyproject.toml            # 现代构建
├── requirements.txt          # 依赖
├── .gitignore                # 排除 gh_* 凭证脚本
├── push_to_github.py         # 同步本地到远端
├── allegro_batch_commands.txt # 75+ Allegro CLI 命令清单
└── pspice_help.txt           # PSpice 帮助
```

---

## 3. 本地环境

### 3.1 必备

| 软件 | 版本 | 用途 |
|------|------|------|
| Windows | 10/11 | PSpice / Allegro 仅 Windows |
| Python | ≥ 3.10 | MCP runtime + PySpice |
| Cadence SPB | 23.1 (已装) | Capture + Allegro + PSpice |
| Node.js | ≥ 20 LTS | 跑 MCP 客户端 / 其他 MCP server |

### 3.2 Python 依赖

```
mcp>=1.0
PySpice>=1.5
```

### 3.3 探测命令

```python
from cadence_mcp.env_detect import detect_cadence, list_installation
print(detect_cadence())          # -> "C:/Cadence/SPB_23.1"
print(list_installation())       # -> {capture, allegro, pspice: True/False, ...}
```

---

## 4. 运行 MCP server

### 4.1 直连

```bash
python -m cadence_mcp.server
```

stdio 协议，被任何 MCP 客户端（CodeBuddy / Claude Desktop / Cline）自动 spawn。

### 4.2 CodeBuddy MCP 配置

`~/.codebuddy/mcp.json`：

```json
{
  "mcpServers": {
    "cadence": {
      "command": "python",
      "args": ["-m", "cadence_mcp.server"],
      "env": {
        "CADENCE_ROOT": "C:/Cadence/SPB_23.1"
      },
      "disabled": false
    },
    "github": {
      "command": "C:/Program Files/nodejs/npx.cmd",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "<your-token>"
      },
      "disabled": false
    },
    "gitlab": {
      "command": "C:/Program Files/nodejs/npx.cmd",
      "args": ["-y", "@zereight/mcp-gitlab"],
      "env": {
        "GITLAB_PERSONAL_ACCESS_TOKEN": "<your-token>",
        "GITLAB_API_URL": "https://gitlab.com/api/v4"
      },
      "disabled": false
    }
  }
}
```

> GitHub / GitLab MCP 在本机器已注册并通过握手测试，详见第 8 节。

---

## 5. 工具清单（MCP）

| 工具名 | 输入 | 输出 | 备注 |
|--------|------|------|------|
| `detect_cadence` | — | 安装根目录字符串 | 自动探测 |
| `list_installation` | — | {capture, allegro, pspice, spectre, skill} | 各组件 True/False |
| `generate_schematic_netlist` | 拓扑参数 | SPICE 网表字符串 | RC / RLC / MOSFET 等模板 |
| `generate_allegro_tel` | 拓扑参数 | Allegro .tel 物理网表 | 文本格式 |
| `generate_dsn_project` | 名称 | DSN 工程框架文件 | 用于 Capture |
| `run_simulation` | 网表 + 类型 | {nodes, vectors, plot} | transient / ac / dc / op; PSpice / ngspice / numpy fallback |
| `save_netfile` | 网表 + 路径 | 文件路径 | 落盘 |

> **真实工具名（已上线）**：`cadence_probe`, `cadence_env_vars`, `allegro_batch_commands`, `safe_batch_commands`, `find_brd_files`, `parse_brd_file`, `allegro_batch_run`, `generate_spice_netlist`, `parse_circuit_dsl`, `generate_schematic_dsn`, `netlist_to_allegro`, `run_circuit_simulation`, `list_simulation_templates`。共 **13 个** MCP 工具，**全部通过 stdio JSON-RPC 实测可用**。
| `pcb_artwork` | 命令 + 选项 | stdout/stderr | 包装 allegro_batch |
| `parse_sim_results` | 数据点 + kind | {metrics, summary} | 提取峰值 / 稳定值等 |

---

## 6. 端到端测试

```bash
python tests/test_all.py
```

测试项：
- [A] 环境探测
- [B] 网表生成（RC / RLC）
- [C] Allegro TEL 生成
- [D] DSN 工程生成
- [E] 仿真运行（PySpice + ngspice）
- [F] 结果解析
- [G] 工具清单（MCP）

---

## 7. GitHub 推送流程

### 7.1 一次性凭证准备

1. 浏览器登录 github.com
2. 生成 PAT：
   - **classic**：`Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate`
     勾选 `repo` 作用域 → 生成 `ghp_…` token
   - **fine-grained**：`Settings → Personal access tokens → Fine-grained tokens` →
     Repository access: `All repositories`（或指定 cadence-ai-mcp）→
     Permissions: **Contents: Read and write** + **Metadata: Read-only**
3. 填到本仓库的 `gh_token.txt` 或直接写到 mcp.json

### 7.2 自动同步脚本

```bash
python push_to_github.py   # 上传 whitelist 文件
python gh_resync.py        # 对比远端 tree，补缺失文件
```

`push_to_github.py` 默认 whitelist：

- `cadence_mcp/*.py`
- `tests/test_all.py`
- `examples/*.cir|.tel|.dsn|.net`
- 根文件：`README.md`, `PROJECT.md`, `LICENSE`, `setup.py`, `pyproject.toml`, `requirements.txt`, `.gitignore`, `allegro_batch_commands.txt`, `pspice_help.txt`, `push_to_github.py`

**绝不上传**：
- `gh_*.py` / `gh_*.json` / `gh_token.txt`（含密码 / token）

### 7.3 用 GitHub MCP 推送（推荐路径）

让 AI 直接通过 MCP 调用：

```
create_repository(name="cadence-ai-mcp", private=false, auto_init=true)
push_files(owner, repo, branch="main", files=[{path, content}], commit_message)
```

### 7.4 实际推送结果（2026-09-30）

- ✅ 仓库 `chaosAIman/cadence-ai-mcp` 已建立
- ✅ 16 + 1 文件全部上传（17 包含 PROJECT.md）
- ✅ 凭证：`fine-grained PAT`，Contents: Read and write
- ⚠️ 之前遇到 fine-grained PAT 默认无 Administration 权限 → 无法 API 建仓（已通过浏览器建仓绕过）

---

## 8. MCP 生态（CodeBuddy 中已注册）

| Server | 命令 | 工具数 | 凭证状态 |
|--------|------|--------|----------|
| `github` | `npx -y @modelcontextprotocol/server-github` | 26 | ✅ Token 已配 |
| `gitlab` | `npx -y @zereight/mcp-gitlab` | 118 | ⚠️ 待填 token |
| `autocad` | `python d:/南瑞CAD图/autocad-mcp/server.py` | — | disabled |
| `autocad-full` | `autocad-mcp.exe` | — | disabled |

### 8.1 GitHub MCP 工具样例

- `create_or_update_file` — 单文件增改
- `push_files` — 多文件一次 commit
- `create_repository` — 建仓
- `search_repositories` / `search_code` / `search_issues` — 全文搜索
- `create_issue` / `create_pull_request` — issue / PR
- `fork_repository` / `get_file_contents` — 读取操作

### 8.2 GitLab MCP 工具样例

- `merge_merge_request` / `approve_merge_request`
- `list_merge_request_pipelines`
- `get_branch` / `list_branches`
- 项目 / issue / 用户 增删改查 全套

---

## 9. 故障排查

| 症状 | 根因 | 处置 |
|------|------|------|
| `Bad credentials` (401) | token 无效 / 设备验证未完成 | 浏览器重新生成 PAT |
| `Resource not accessible` (403) | fine-grained PAT 权限不够 | token 设置勾 **Contents: Read and write** |
| `404` on contents | 仓库未建 / token 未关联 | 浏览器建仓 + token 配 Repository access |
| Allegro 调用退出码非 0 | license 缺失或环境变量 | 设置 `CDSROOT=C:\Cadence\SPB_23.1` |
| PySpice 仿真无结果 | 未装 ngspice 二进制 | 下载 ngspice + 配 PATH |
| MCP server 启动后立即退出 | stdio 被占用 | 检查 mcp.json 中 args/env |
| 网路到 github.com 超时 | DNS 污染 / 间歇封锁 | 多 IP 轮换或挂代理 |

### 9.1 Token 401 的特殊处理

命令行脚本创建的 PAT 经常因 **设备验证未完成** 被服务端立即拒绝。
**绕开办法**：用户在浏览器里（已验证环境）创建 PAT → 拷贝给 AI → 写到 `gh_token.txt`。

---

## 10. 版本与发布

| 版本 | 日期 | 备注 |
|------|------|------|
| 0.1.0 | 2026-09-30 | 初版：env_detect / simulator / schematic / pcb / server / tests |

发布步骤：
1. 在 GitHub 网页 `Releases → Draft a new release`
2. Tag: `v0.1.0`，标题：`Initial Release`
3. 描述：自动从 README 抽取
4. 上传 wheel：`python -m build` → `dist/*.whl`

---

## 11. 路线图

- [ ] Allegro 实际 PCB 放置（需 license）
- [ ] SKILL 脚本封装层
- [ ] Spectre 仿真支持
- [ ] Web UI（FastAPI + WebSocket）
- [ ] Docker 镜像
- [ ] GitHub Action CI（lint + test）

---

## 12. 联系 / 维护

- 项目所有者：chaosAIman
- 主要开发：AI 助手 + 用户协作
- 反馈方式：GitHub Issues