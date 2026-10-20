#!/usr/bin/env python3
"""一键发版：build.bat 打包 → 更新 GitHub Release（正文取 CHANGELOG 当前版本小节，替换 fpk 资产）。

直传 api/uploads.github.com（token 从 GITHUB_TOKEN 环境变量或 git 凭据库取，不入仓库）。
旧链路 dist/ + release-asset.yml（Actions 代传资产）已移除——本机 Python 可直传，
且 dist 旧包在打标签时会被误挂到新 Release（v1.0.2 实测踩过）。

用法：python release.py
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.github.com/repos/liuluuuuu/chaimi-ledger"


def die(msg):
    sys.exit(f"[error] {msg}")


def sh(cmd):
    r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode:
        die(f"{cmd}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def github_token():
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n",
                       capture_output=True, text=True, cwd=ROOT)
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    die("无 GitHub token（设 GITHUB_TOKEN 或存入 git 凭据库：git credential approve）")


def call(url, tok, data=None, method=None, ctype="application/json", raw=False):
    body = data if raw else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"token {tok}", "User-Agent": "chaimi-release",
        "Accept": "application/vnd.github+json",
        **({"Content-Type": ctype} if body is not None else {})})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read() or b"{}")


def main():
    manifest = (ROOT / "chaimi-ledger-fn" / "manifest").read_text(encoding="utf-8")
    ver = re.search(r"^version=(.+)$", manifest, re.M).group(1).strip()
    tag, fpk = f"v{ver}", ROOT / f"chaimi-ledger.{ver}.fpk"

    print(f"[1/3] build.bat 打包（含单元测试）… version={ver}")
    sh("cmd /c build.bat")
    if not fpk.exists():
        die(f"打包产物缺失：{fpk.name}")

    print("[2/3] 更新 Release 正文（CHANGELOG 小节）…")
    md = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"(## {re.escape(ver)}[^\n]*\n)(.*?)(?=\n## )", md, re.S)
    if not m:
        die(f"CHANGELOG 未找到 {ver} 小节")
    section = (m.group(1) + m.group(2)).strip()
    # 「## 1.0.2（2026-10-20）标题」→ Release 名「v1.0.2 标题」
    title = f"{tag} " + re.sub(r"^## [\d.]+（[^）]*）\s*", "", section.splitlines()[0]).strip()

    tok = github_token()
    try:
        rel = call(f"{API}/releases/tags/{tag}", tok)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        rel = call(f"{API}/releases", tok, data={
            "tag_name": tag, "target_commitish": "main", "name": title, "body": section})
    call(f"{API}/releases/{rel['id']}", tok, data={"name": title, "body": section}, method="PATCH")

    print("[3/3] 替换 fpk 资产…")
    rel = call(f"{API}/releases/tags/{tag}", tok)
    for a in rel.get("assets", []):
        if a["name"].endswith(".fpk"):
            call(f"{API}/releases/assets/{a['id']}", tok, method="DELETE")
    up = rel["upload_url"].replace("{?name,label}", "") + f"?name={fpk.name}"
    asset = call(up, tok, data=fpk.read_bytes(), method="POST",
                 ctype="application/octet-stream", raw=True)
    print(f"done: {asset['name']} {asset['size']} bytes -> {rel['html_url']}")


if __name__ == "__main__":
    main()
