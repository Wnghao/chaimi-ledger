---
name: lulu-ledger
description: lulu的记账本(NAS)微信记账助手:一句话记账、小票识别、查账统计、月报、流水修改。凡涉及记账/查账/账单的消息必须经本技能转发,禁止自行编造账目数据。
---

# lulu-ledger 记账技能

把用户的记账/查账消息**原样**转发给 NAS 上的 lulu-server,并把它返回的文本回复给用户。
所有记账智能(分类、礼金规则、少样本习惯)都在 lulu 侧,本技能只做转发。

## 硬性约束

1. 用户消息涉及**记账、查账、账单、月报、撤销、确认、丢弃**时,必须调用工具转发,
   将返回文本原样回复;**禁止**自己解析金额/分类、编造账本数据。
2. 草稿确认是 lulu 的内部状态:用户说「确认」「丢弃」「第2笔是30」这类话也原样传入。
3. 与记账无关的请求(天气、闲聊等)不经过本技能,正常自行处理。

## 配置

先在 lulu App「设置→微信机器人」选**网关**模式并生成 Token,然后:

**方式一:MCP(推荐)**

```
LULU_BOT_URL=http://127.0.0.1:18710       # 同机默认;跑在 Docker 容器里连不到宿主机的 127.0.0.1
LULU_BOT_TOKEN=<生成的Token>
command: python3 /usr/local/apps/@appcenter/lulu-ledger/src/lulu_mcp.py
```

- 真机 lulu-server 网关模式下在 UDS 之外并行监听 TCP 18710(默认仅 127.0.0.1,其余模式不监听);
  OpenClaw 跑 Docker 时:容器用 `--network host`,或给 lulu 设 `LULU_BOT_BIND=0.0.0.0`
  后把 URL 改成 NAS 内网 IP;lulu_mcp.py 路径对容器需可见(挂载或拷出)。
- 技能调用超时设 **120 秒**(LLM 解析 2~10s,查账多轮可 20s+)。
- 工具:`ledger_message(text)` / `ledger_receipt(path_url)` / `ledger_status()`

**方式二:REST(任何脚本语言)**

```bash
curl -s http://127.0.0.1:18710/api/bot/message \
  -H "Authorization: Bearer <Token>" \
  -H "Content-Type: application/json" \
  -d '{"text":"午饭25","user":"gateway"}'
# → {"reply":"📝 解析到草稿(未入库):\n1. 餐饮美食 午饭 ¥25.00\n…"}
```

小票:`POST /api/bot/receipt`(multipart 图片,同款 Token)。

## 对话示例

- 「午饭25」→ 返回草稿 → 「第2笔是30」→ 更新 → 「确认」→ 入账
- 「上周餐饮多少钱」→ 总额 + 流水列表 → 「更多」翻页
- 「月报」「月报 2026-09」「撤销」「帮助」
