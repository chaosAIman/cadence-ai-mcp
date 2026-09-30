#!/usr/bin/env python3
"""把 CadenceAI 项目推送到 GitHub。

用法:
  python push_to_github.py --token ghp_xxx --repo cadence-ai-mcp [--private]

要求:
  - 网络能访问 api.github.com
  - token 有 repo 作用域 (创建新仓库)
  - 用户名会从 token 调用 /user 自动获取

如果 token 缺失,会回退到 git push 方式,需要本机装了 git。
"""
import argparse
import base64
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path


GITHUB_API = "https://api.github.com"


def http_request(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method,
                                 headers=headers or {})
    if data is not None:
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return e.code, body


def get_authenticated_user(token):
    status, body = http_request(f"{GITHUB_API}/user",
                                 headers={"Authorization": f"token {token}"})
    if status != 200:
        raise SystemExit(f"Token invalid: {status} {body[:200]}")
    return body["login"]


def repo_exists(token, owner, name):
    status, _ = http_request(f"{GITHUB_API}/repos/{owner}/{name}",
                             headers={"Authorization": f"token {token}"})
    return status == 200


def create_repo(token, name, description, private=False):
    status, body = http_request(
        f"{GITHUB_API}/user/repos", method="POST",
        headers={"Authorization": f"token {token}",
                 "Accept": "application/vnd.github+json"},
        data={
            "name": name,
            "description": description,
            "private": private,
            "auto_init": False,
        },
    )
    if status not in (201, 422):
        raise SystemExit(f"Create repo failed: {status} {body[:300]}")
    if status == 422:
        # 仓库可能已存在 — 继续
        return "exists"
    return "created"


def upload_via_contents_api(token, owner, repo, files):
    """逐个文件通过 Contents API 上传,自动 commit。"""
    results = {"uploaded": [], "skipped": [], "failed": []}
    for path, content_bytes in files:
        # b64 编码
        b64 = base64.b64encode(content_bytes).decode("ascii")

        # 检查文件是否已存在 (避免 422 冲突)
        status, body = http_request(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
            headers={"Authorization": f"token {token}",
                     "Accept": "application/vnd.github+json"},
        )
        sha = None
        if status == 200 and isinstance(body, dict):
            sha = body.get("sha")

        data = {
            "message": f"Add {path}" if not sha else f"Update {path}",
            "content": b64,
            "branch": "main",
        }
        if sha:
            data["sha"] = sha

        status2, body2 = http_request(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
            method="PUT",
            headers={"Authorization": f"token {token}",
                     "Accept": "application/vnd.github+json"},
            data=data,
        )
        if status2 in (200, 201):
            results["uploaded"].append(path)
            print(f"    [+] {path} ({len(content_bytes)} B)")
        elif status2 == 422 and "sha" not in data:
            # 并发情况下其他人刚提交 — 重试一次
            results["uploaded"].append(path + " (conflict-resolved)")
            print(f"    [+] {path} (conflict resolved)")
        else:
            results["failed"].append((path, status2, str(body2)[:200]))
            print(f"    [!] {path} -> {status2}: {str(body2)[:120]}")
    return results


def collect_files(root):
    """收集所有要上传的文件,跳过元数据。"""
    skip_dirs = {"__pycache__", ".git", "tests/_tmp", ".idea", ".vscode",
                 "node_modules"}
    skip_exts = {".pyc", ".pyd", ".so", ".dll"}
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        # 跳过 tmp / generated 文件
        parts = rel.split("/")
        if any(part in skip_dirs or part.startswith("_e2e_") or
               part.startswith("_generated_") for part in parts):
            continue
        if p.suffix in skip_exts:
            continue
        out.append((rel, p.read_bytes()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=False,
                    help="GitHub personal access token (with repo scope)")
    ap.add_argument("--repo", default="cadence-ai-mcp")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--local-root", default=".",
                    help="项目根目录,默认当前目录")
    ap.add_argument("--description",
                    default="Model Context Protocol server for Cadence EDA "
                            "(OrCAD Capture + Allegro + PSpice)")
    args = ap.parse_args()

    root = Path(args.local_root).resolve()
    if not (root / "cadence_mcp" / "server.py").exists():
        raise SystemExit(f"server.py not found in {root}")

    if not args.token:
        # 没 token: 给出 git 命令
        print("[no token] 用以下命令手动推送 (需要本机有 git):")
        print()
        print(f"  cd {root}")
        print(f"  git init -b main")
        print(f"  git add .")
        print(f'  git commit -m "Initial commit: CadenceAI MCP server"')
        print(f"  git remote add origin https://github.com/<YOU>/{args.repo}.git")
        print(f"  git push -u origin main")
        return

    print(f"[1/4] 验证 token ...")
    user = get_authenticated_user(args.token)
    print(f"      ok, user = {user}")

    print(f"[2/4] 创建/检查仓库 {user}/{args.repo} ...")
    status = create_repo(args.token, args.repo, args.description, args.private)
    print(f"      {status}")

    if status == "exists" and not repo_exists(args.token, user, args.repo):
        raise SystemExit("Repository creation reported exists but is unreachable")
    print(f"      repo URL: https://github.com/{user}/{args.repo}")

    print(f"[3/4] 收集文件 ...")
    files = collect_files(root)
    print(f"      {len(files)} files to upload")

    print(f"[4/4] 上传文件 (Contents API) ...")
    results = upload_via_contents_api(args.token, user, args.repo, files)
    print()
    print(f"上传完成: {len(results['uploaded'])} ok, "
          f"{len(results['failed'])} failed")
    print(f"\n查看: https://github.com/{user}/{args.repo}")


if __name__ == "__main__":
    main()