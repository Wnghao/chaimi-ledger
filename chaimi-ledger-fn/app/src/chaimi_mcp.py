"""chaimi-ledger MCP server（微信机器人 B 通道，DESIGN 10.4）。

stdio JSON-RPC（MCP 规范），零第三方依赖；内部经 HTTP 调 chaimi-server 的 /api/bot/*。
挂载（OpenClaw 示例）：
  CHAIMI_BOT_URL=http://127.0.0.1:18710 CHAIMI_BOT_TOKEN=xxx python3 chaimi_mcp.py

工具：
  ledger_message(text)      原样转发用户消息，返回机器人回复文本（记账/查账/草稿全链路）
  ledger_receipt(path_url)  小票图片（本地路径或 URL）→ 记账草稿
  ledger_status()           模式 / 当前是否有草稿
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid

BASE = os.environ.get("CHAIMI_BOT_URL", "http://127.0.0.1:18710").rstrip("/")
TOKEN = os.environ.get("CHAIMI_BOT_TOKEN", "")

TOOLS = [{
    "name": "ledger_message",
    "description": (
        "柴米记账对话入口。凡用户消息涉及记账、查账、账单、月报、撤销、确认、丢弃，"
        "必须原样调用本工具并把返回文本回复用户；禁止自行解析金额/分类，禁止编造账目数据。"
        "草稿确认流程由本工具内部状态机维护（用户说「确认」也原样传入即可）。"),
    "inputSchema": {"type": "object",
                    "properties": {"text": {"type": "string", "description": "用户的原始消息"}},
                    "required": ["text"]},
}, {
    "name": "ledger_receipt",
    "description": "小票/账单照片记账：传入图片路径或 URL，返回解析出的草稿回复（未入库，用户确认后才入账）。",
    "inputSchema": {"type": "object",
                    "properties": {"path_url": {"type": "string", "description": "图片本地路径或 URL"}},
                    "required": ["path_url"]},
}, {
    "name": "ledger_status",
    "description": "查询机器人状态：模式、当前是否有待确认草稿。",
    "inputSchema": {"type": "object", "properties": {}},
}]


def _post(path: str, payload: dict | None = None, data: bytes | None = None,
          content_type: str = "application/json") -> dict:
    req = urllib.request.Request(BASE + path, data=data if data else json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {TOKEN}",
                                          "Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=120) as resp:   # LLM 链路可达 20s+，与技能侧 120s 对齐
        return json.loads(resp.read().decode())


def _receipt_bytes(path_url: str) -> bytes:
    if path_url.startswith(("http://", "https://")):
        with urllib.request.urlopen(path_url, timeout=60) as r:
            return r.read()
    with open(path_url, "rb") as f:
        return f.read()


def _call_tool(name: str, args: dict) -> dict:
    if name == "ledger_message":
        r = _post("/api/bot/message", {"text": str(args.get("text") or ""), "user": "gateway"})
        return {"reply": r.get("reply", "")}
    if name == "ledger_receipt":
        b = _receipt_bytes(str(args.get("path_url") or ""))
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"receipt.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode() + b \
            + f"\r\n--{boundary}--\r\n".encode()
        r = _post("/api/bot/receipt", data=body, content_type=f"multipart/form-data; boundary={boundary}")
        return {"reply": r.get("reply", "")}
    if name == "ledger_status":
        return _post("/api/bot/status")
    raise ValueError(f"未知工具: {name}")


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    if not TOKEN:
        print("chaimi_mcp: 缺少 CHAIMI_BOT_TOKEN 环境变量", file=sys.stderr)
        sys.exit(1)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "chaimi-ledger", "version": "1.0.0"}}})
        elif method == "notifications/initialized":
            continue   # 通知无响应
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            try:
                out = _call_tool(req["params"]["name"], req["params"].get("arguments") or {})
                _send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                    "isError": False}})
            except Exception as e:
                _send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"chaimi 调用失败: {e}"}], "isError": True}})
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid,
                   "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
