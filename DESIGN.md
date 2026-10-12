# lulu-ledger 设计文档

> 飞牛 NAS（fnOS）原生家庭记账应用。多账本分账 + 账单导入 + 统计看板 + AI 记账 + 微信机器人，数据全在本机 SQLite。
>
> 状态：v1.0.0（manifest 版本）已实现并稳定运行，本文描述**当前实现**，规划期版本已精简合并。

***

## 一、项目概述

- **形态**：飞牛原生应用（fpk 包），网关以 iframe 挂载 `/app/lulu-ledger`
- **存储**：SQLite 单文件（`lulu.db`），零配置，备份 = 拷文件
- **后端**：FastAPI（Python 3.12，`--target` 隔离装依赖，不污染系统）
- **前端**：单 HTML + 原生 JS + Chart.js 4（按需加载，无构建链）
- **用户模型**：家庭共用，多账本（日常/旅行/装修），「用户」是记账人维度而非登录账号，无权限隔离（内网部署信任飞牛网关鉴权）
- **AI**：OpenAI 兼容协议，设置页填 Key 即用（文本默认 DeepSeek，视觉默认 qwen-vl-max，兼容百炼/智谱/本地模型）
- **微信机器人**：同一份记账大脑，直连微信（iLink 长轮询）或经外部 Agent 网关（REST/MCP）调用

### 模块清单（app/src/）

| 文件 | 职责 |
|---|---|
| server.py | FastAPI 入口：记账 CRUD / 统计 / 导入 / 配置 / 备份 / AI / bot 路由 |
| config.py | JSON 配置（lulu.conf）+ TRIM_* 环境变量 + llm/vision/bot 段 |
| db.py | SQLite schema、迁移、公共查询、默认映射种子（DEFAULT_MAPPINGS） |
| import_bill.py | 账单导入（CSV/xlsx 解析、分类、退款匹配、去重） |
| ai.py | AI 记账：自然语言/小票/月报/对话 + edit_round 编辑回合 |
| backup.py | SQLite 文件备份 + 配置副本 |
| bot_core.py | 机器人大脑：白名单、指令路由、草稿会话、回复格式化（通道无关） |
| bot_ilink.py | 通道 A：iLink 客户端（QR 登录、长轮询、收发、媒体下载解密） |
| lulu_mcp.py | 通道 B 的 MCP server（stdio，独立进程） |

前端（app/static/）：app.html 主界面 + home.html 登录/选用户页 + core/ledger/stats/settings/ai/import .js 分页逻辑。

***

## 二、整体架构

```
飞牛桌面 ──iframe──▶ lulu-server(FastAPI)
                        │
   ┌─────────┬──────────┼──────────┬─────────────┐
   ▼         ▼          ▼          ▼             ▼
 记账CRUD   统计图表   账单导入     备份        AI 记账 ──▶ LLM / 视觉 API
 (db.py)  (Chart.js)(import_bill)(backup.py)      │
   └─────────┴──────────┴──────────┴──────┬───────┘
                                          ▼
                                    SQLite(lulu.db)
微信 ──iLink──▶ bot_ilink(A直连) ──┐
                                  ├─▶ bot_core.handle() ──▶ db / ai.chat
OpenClaw ──MCP/REST──▶ /api/bot(B网关) ──┘
```

**网关三件套**（SSE 流式与相对路径必需）：

- 响应头 `X-Accel-Buffering: no`
- `<base href="/app/lulu-ledger/">` 注入
- SSE 流内 15s 心跳注释行保活

**多端实时刷新（长轮询）**：`GET /api/rev/wait?since=N&hold=25` 挂住最多 25s（服务端钳制 1~30s），数据版本 rev 偏离立即返回；前端返回后 300ms 续下一条、出错 2s 退避、单请求 35s 强制 abort 防半开连接；页面回前台（focus/visibilitychange/pageshow）掐掉挂起请求并主动刷一次。任意端记账，其他端 1s 内刷新。写库侧统一 `_bump_rev`：交易增/改/删、待确认转正、导入应用/撤销、bot 写库全部同源。请求带 `?cv=版本信标`（build 时烘焙 `static/version.txt`，页面注入 `window.LULU_VER`），日志可分辨各客户端前端版本。

**SSE 仅用于 AI 流式**（对话/月报）：ai 内部为结构化事件流（生成器产 dict，HTTP 边界 `sse_stream` 统一序列化），bot 通道直接消费同一生成器，不二次解析文本。

***

## 三、目录结构

```
lulu-ledger/
├── DESIGN.md / README.md / LICENSE
├── build.bat                    # 读 manifest 版本 → 烘焙 version.txt → py_compile 自检
│                                #   → 清理开发数据(app/data) → fnpack 打包
└── lulu-ledger-fn/
    ├── manifest                 # 飞牛应用清单（appname/version/install_dep_apps=python312…）
    ├── config/                  # resource（api-scope）/ privilege（run-as: root）
    ├── cmd/                     # 生命周期脚本：main(start/stop/status)、install_init(装依赖)、
    │                            #   install/uninstall/upgrade/config 的 init+callback
    ├── wizard/                  # 卸载向导
    └── app/
        ├── server/              # config（桌面注册 iframe）/ images（图标、牛牛头像，禁缓存路由）
        ├── skills/lulu-ledger/  # OpenClaw 技能包（SKILL.md，B 通道备选形态）
        ├── src/                 # 后端源码（见模块清单）
        └── static/              # 前端（app.html/home.html/*.js/base.css/views.css/Chart.js）
```

***

## 四、数据库设计（SQLite）

8 张表：`ledgers`（账本）、`categories`（分类，expense/income 平行两套，支持二级 parent_id）、`accounts`（账户）、`transactions`（流水）、`import_batches`（导入批次）、`category_mappings`（类型映射）、`attribution_groups`（归属分组=用户）、`attributions`（归属名单）。完整 DDL 见 `db.py`，要点：

**不变式与口径**

- **金额一律 INTEGER 存分**（浮点累计会漂移），前端/导入层换算，展示时 ÷100
- **账户余额不增量维护**：`余额 = initial_balance + Σincome − Σexpense + Σtransfer收 − Σtransfer付`，查询时实时算（撤销导入/删行/退款不需要同步余额）
- **退款不变式**：凡 `refund_for` 非空的行必为 transfer（只进原消费扣减、不计收支）；原消费行恒为 expense
- **映射表 v6 架构**：target 为 category_id 外键（悬空映射结构上不可能），全局层取消、默认映射按账本种入（`seed_default_mappings`，数据源 `db.DEFAULT_MAPPINGS`）；分类改名/删除由外键联动；learned=1 的学习映射在整批撤销时级联清理
- **单账本单币种**：币种是账本属性（`ledgers.currency`），导入校验不一致整批拒绝

**索引与连接**

```sql
idx_tx_hash(ledger_id, source, row_hash)   -- 去重批量比对
idx_tx_date(ledger_id, date)               -- 统计一律区间比较(date>=? AND date<?)，不用 LIKE
idx_tx_order / idx_tx_batch / idx_tx_refund -- 退款匹配 / 整批撤销 / 反向扫退款
PRAGMA journal_mode=WAL; busy_timeout=5000; foreign_keys=ON;
```

**迁移**：`PRAGMA user_version` 记录版本，启动按序执行（只增不删列）；对已发布表加列走 `_ensure_column`（PRAGMA table_info 查缺 → 幂等 ALTER），老库升级/新库建表共用同一路径。

***

## 五、API 概览（前缀 /app/lulu-ledger）

```
页面     GET /                    app.html      GET /home   登录/选用户页
账本     GET/POST/PUT/DELETE /api/ledgers[/{id}]
流水     GET /api/transactions?ledger=&from=&to=&type=&category=&page=…
         POST/PUT/DELETE /api/transactions[/{id}]   （PUT 支持 refund_for 关联/取消）
待确认   GET /api/pending?ledger=&kind=     POST /api/pending/{tx_id}/resolve
分类/账户/映射  常规 CRUD（/api/categories /api/accounts /api/mappings，均带 ledger=）
统计     /api/stats/{calendar|summary|category|trend|monthly|attribution}
导入     POST /api/import/parse（预览不入库）  POST /api/import/confirm（入库+learn 回写）
         GET /api/import/batches   DELETE /api/import/batches/{id}（整批撤销）
导出     GET /api/export?format=json|csv      导出 xlsx 含流水/归属统计 sheet
备份     POST /api/backup  GET /api/backups  POST /api/restore
用户     GET/POST/PUT/DELETE /api/attribution-groups[/{id}]（=用户，含 avatar）
         GET /api/attributions   PUT /api/attributions/{aid}（仅设组；增删已下线）
AI       GET /api/ai/chat（SSE 对话：记账出草稿/查账回统计）  POST /api/ai/receipt（小票）
         GET /api/ai/analyze（SSE 月报）  POST /api/ai/parse（保留接口）
实时     GET /api/rev/wait（长轮询，见第二章）
机器人   POST /api/bot/message|receipt  GET /api/bot/status（B 通道，Bearer 鉴权）
         /api/bot/ilink/*（扫码/状态/解绑，A 通道面板用）
资源     GET /images/{name}（图标/头像，禁缓存——替换同名 PNG 即生效）
```

流水分页响应统一 `{items, total, page, page_size}`。

***

## 六、AI 记账（ai.py）

| 功能 | 输入 → 输出 | 说明 |
|---|---|---|
| 自然语言记账 | 「早餐15元」「昨天打车32 微信付」→ 草稿卡 | 解析金额/类型/分类/账户/日期/备注，确认才入库 |
| 小票识别 | 图片 → 草稿卡 | 视觉模型 OCR+结构化（App 上传/微信发图共用） |
| 月度分析 | 月度聚合 → 自然语言洞察（SSE） | 只发聚合统计，不发逐条流水 |
| 对话查账 | 自然语言 → function call 查库 → 回答 | query_stats 出总额+流水明细附件 |
| 编辑回合 | 「第二笔是30」「分类改成购物」→ 结构化修改 | 草稿与流水列表共用（见机器人章） |

**要点**：

- 账户识别：提示词注入可用账户清单，`_resolve_account` 精确→包含匹配（「微信」→账户「微信支付」）
- 名称必填/备注可空：名称=简短品项（早餐），备注=商家门店（肯德基）
- 分类兜底批量调用：按一级分组、单请求 ≤50 行、整批未命中 >200 熔断；`response_format=json_object`，temperature=0
- 礼金规则单源（`_GIFT_RULE`）：＜200 的红包/转账归红包礼金、不进人情往来，词典/映射/AI 三处同口径
- **隐私哲学**：每词只发「一句话/一张图 + 分类账户清单 + 50 条少样本 + 聚合统计」，从不发全量账单；数据存储全本地

**配置**（lulu.conf，设置页向导填写）：

```json
"llm":   {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key": ""},
"vision":{"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-vl-max", "api_key": ""}
```

***

## 七、账单导入（import_bill.py）

**支持来源**：支付宝（CSV/GBK）、微信（**xlsx**，openpyxl）、京东（CSV/UTF-8 BOM）、本应用导出（export，CSV/xlsx；'cashbook' 仅作历史存量行 source 识别）。xlsx 按首 sheet 表头分流到对应解析器。

**流程（预览制）**：

```
上传 → parse_bill 解析(不入库) → 预览页（need_confirm 高亮 / duplicate 灰显 / 疑似重复提示）
     → 用户确认 → apply_import（跳过重复行，先写批次再批量入库，learn 回写映射）
     → 低置信项照常入库打标，事后在「待确认」队列页处理；支持整批撤销（级联清理退款关联与学习映射）
```

**类型分流**：支出→expense；收入→income（微信 `-退款` 后缀强制 transfer）；不计收支（转账/理财/还款/充值）→transfer 子类（资金搬运，不计收支），退款行触发退款匹配。

**退款匹配**（分层，低置信 need_confirm 不阻断导入）：① 订单号精确（全额首选）→ ② 商户+金额（支付宝「退款-」前缀高置信；京东商品名被平台改写，降级模糊匹配）→ ③ 启发式（金额+日期窗）→ ④ AI 辅助多候选 → ⑤ 未命中独立 transfer 待手动关联。跨账本不匹配；原消费后导入时反向扫描补关联。

**去重**：row_hash 整行哈希（稳定身份字段 + 金额归一化去退款标记，同笔交易二次导入哈希一致），(ledger_id, source, row_hash) 批量比对；另有 **dup_hints 疑似重复软提示**——(日期, 类型, 金额) 三元组相同只提示不拦截（防手动/AI 记过又导入平台账单的真实重复）。

**上传安全**：20MB 上限、扩展名白名单、xlsx 先查 zip 魔数防伪装、限解压行数防炸弹、临时文件即用即删。

**导出回环**：导出 14 列（转账双账户、订单号、流水ID、退款关联），再导入零待确认零重复，退款关联/双账户/归属全部无损还原。

> 各平台逐列解析规则、默认映射表与二级关键词词表以代码为准：`import_bill.py`、`db.DEFAULT_MAPPINGS`、`SUBCATEGORY_LEXICON`。

***

## 八、分类体系与统计口径

**分类**：支出 15 一级（餐饮美食/日用百货/数码电器…）+ 二级细分；收入**独立** 8 一级（工资薪酬/兼职副业/理财收益/红包礼金/退款返还/报销/投资本金/其他收入），收支两套平行，映射表按 flow_type 分流——工资/兼职不再混进「投资理财」。种子数据见 db.py。

**统计隔离规则**：

| type | 计入支出 | 计入收入 |
|---|---|---|
| expense | ✅ | — |
| income | — | ✅ |
| transfer（资金搬运） | ❌ | ❌ |
| transfer（退款，refund_for 非空） | **扣减** | ❌ |

```
净支出 = Σ(expense) − Σ(退款行.amount WHERE refund_for 非空)
```

**退款扣减归因到原消费日期（`o.date`）**，而非退款发生日——跨月退款（9月买10月退）自然回溯，9 月饼图扣得掉、10 月不会出现负净支出。日历/趋势/饼图/汇总全部同口径。归属统计维持毛支出口径（不扣退款），与分类净口径并存。

***

## 九、流水归属与用户（记账人）

**模型**：`attributions`（归属名单，由账单导入自动产生，不可手动增删）+ `attribution_groups`（分组，即「用户」，含头像）；一归属至多一组，删组成员回未分组（FK ON DELETE SET NULL）。

**核心决策——分组成员 = 归属名，非统计键**：统计键是查询时拼接的「归属名·平台」（如 大牛·支付宝）；分组取归属名原子值，配置一次即覆盖该成员全部平台账单 + 手动记账，新平台账单导入自动继承分组。例：大牛 + 小牛 → 「老公」，统计页看「老公这个月的收支」，组行可展开成员明细。

**显示解析层**（四端统一：流水页/编辑弹窗/导出/统计）：有组显组名、无组显原始归属（`attr_display` 解析列，名单查组 + 同名组双路径）。两个关键语义：

1. **编辑未变更不重写**：改其他字段、归属没动时保留原始归属（大牛 不被「老公」覆盖），平台构成在组内仍可追溯
2. **分组改名同步改写**：手动账存的是组名，改组名时同步 UPDATE 相关流水，否则会「掉组」

**用户 = 分组的业务别名**（表/API 不变）：登录页 home.html 三态——无用户→强制设置；缓存失效/换设备→用户卡选择；缓存有效→直进记账页。当前用户按账本存 localStorage（记账默认归属，可改）。头像两只牛（m/f，默认 f），`/images` 路由禁缓存。

***

## 十、微信机器人

### 10.1 传输与大脑分离

```
微信 ──iLink──▶ [A] bot_ilink(长轮询线程) ──┐
                                            ├──▶ bot_core.handle(text, user, image?)
微信 ──iLink──▶ OpenClaw ──MCP/REST──▶ [B] /api/bot/* ──┘        │
                                                        对话式草稿(内存会话) ──「确认」──▶ db
                                                                  │
                                                        统一回复文本(各通道自行送出)
```

A（直连）/B（网关）只是「谁持有微信通道」的差别，共用 bot_core 保证行为/话术/数据一致。iLink bot 身份单会话，A、B 用 `bot.mode` 开关互斥切换（热切换不重启）。企微通道与图片外发链路已移除（微信端图文渲染效果不佳），只发文本；小票识别收图完整保留。

### 10.2 bot_core（指令路由，1~7 确定性规则 + 8 兜底）

1. **白名单**：不在名单返回 ID 提示绑定（扫码自动加入；A/B 双域白名单）
2. **草稿指令**（DRAFT_PENDING 态）：确认/丢弃/草稿重发
3. **删除确认**（PENDING_DELETE 态）：确认删除/取消删除
4. **翻页**：更多/上一页（续看上次查询）
5. **撤销**：删最近一笔 source='bot' 流水（限 24h；bot 编辑过的手工/导入流水 source 不变，不会被误删）
6. **月报**：精确匹配 `月报[ YYYY-MM]` → monthly_analysis 文本
7. **帮助**：指令清单
8. **其余一切** → 状态分流：有草稿进编辑回合；否则 `ai.chat`（模型自带记账/查账路由）

**对话式草稿**：不落库，全程对话修订，「确认」才批量入库（need_confirm=0, source='bot'）；内存态 TTL 30 分钟，超时/重启即失，丢弃零残留。会话状态 per-user。

**编辑回合**（`ai.edit_round`，草稿与流水列表共用）：输出结构化意图——
`target=draft,action=revise`（改草稿，复用 `_booking_items` 规范化）/ `target=tx,update`（改已入库流水，patch 字段解析后走 `_update_tx`，与 `PUT /api/transactions` 同一条路）/ `target=tx,delete`（两步确认）/ `target=new`（新记账替换会话）/ `target=none`（与编辑无关，走 ai.chat，草稿保留）。

**查询渲染**：明细行「▪ 日期 名称 符号金额」三要素，首屏最多 10 笔（LIST_MAX），「更多」列全部上限 30（防刷屏）；「XX明细」分类下钻本地路由（精确→包含对齐一级），`matched=false` 不编造明细；修改回执明示改前→改后。

**无分类流水的可见口径（既有行为，注意）**：机器人明细列表 / AI 对话附件 / 分类下钻取数走 `db.fetch_tx_rows`（INNER JOIN categories），**无分类的流水不出现在这些列表里**；但统计聚合（汇总/日历/分类饼图）按金额正常计入。典型场景：bot 兜底记账的条目分类解析不到时以 category_id=NULL 入库——查账总额里算得进、明细列表里看不到。处理路径：在 App 里给它补分类（待确认队列或编辑弹窗），补完即进列表。该行为由 `tests/test_db.py::test_inner_join_and_filters` 锁定。

### 10.3 通道 A：bot_ilink.py（iLink 直连）

- **QR 登录**：设置页发起 → 渲染二维码 → 手机微信扫 → 轮询状态至 logged_in，凭证 `{account_id, token, base_url}` 落 lulu.conf
- **长轮询线程**：daemon 线程循环（getupdates 挂起 ~35s），单 in-flight 守卫；**按 msg_id 去重**（最近 200 条 LRU，重连/重投只处理一次）；回复 POST 必须回传 `context_token`
- **收图**（小票识别）：广谱检测媒体线索 → CDN 下载（三种 URL 形态）→ AES-128-ECB + PKCS7 解密（密钥三形态兼容，依赖 pycryptodome）→ 落盘 receipts → parse_receipt → 草稿卡
- **凭证生命周期**：过期收 401 → 线程停、面板亮「凭证过期请重新扫码」（重扫必须人扫，无自动续期）
- **网络**：长轮询是出站连接，**无需公网穿透**，全程无回调端口

### 10.4 通道 B：REST + MCP（外部 Agent 网关）

- `POST /api/bot/message` / `POST /api/bot/receipt` / `GET /api/bot/status`，`Authorization: Bearer {bot.api_token}`；同步接口，LLM 解析 2~10s、查账可 20s+，网关侧超时设 120s
- 真机 cmd 以 `--uds` 起 server，HTTP 进不了 Unix socket——server 在 UDS 之外并行监听 TCP（`_tcp_sidecar`，`LULU_BOT_BIND` 默认 127.0.0.1，跨容器置 0.0.0.0，off 关闭）
- **lulu_mcp.py**（stdio MCP server，三工具）：`ledger_message`（原样转发）/ `ledger_receipt` / `ledger_status`。工具描述写死约束：凡涉记账查账必须原样调用 ledger_message，禁止网关 LLM 自行解析金额、编造账目——lulu 的调优提示词是唯一大脑，OpenClaw 只当管道
- `skills/lulu-ledger/`：纯 REST 技能包（SKILL.md），与 MCP 二选一，随 fpk 上机

### 10.5 配置与热切换（lulu.conf `bot` 段）

```json
"bot": {
  "mode": "off",                  // off | direct(A) | gateway(B)
  "default_ledger": 1,
  "allowed_users": [],            // A：微信ID白名单（扫码自动加入）
  "gateway_users": ["gateway"],   // B：网关标识白名单（独立保存）
  "api_token": "",                // B：Bearer 鉴权（设置页生成/重置）
  "ilink": [ {"account_id":"","token":"","base_url":"","user":"","ledger":"","label":""} ]
}
```

保存设置时 `bot_manager.apply(cfg)` 差量生效（off↔direct↔gateway 启停轮询线程/开关 API，不重启）。A⇄B 切换顺序：先停旧方再扫码新方（iLink 单会话）。

### 10.6 多人多账本绑定（一账本一用户一机器人）

绑定模型：**（账本, 用户）→ 一个 bot 实例**，凭证以 user 为唯一键（同微信重扫顶替、旧实例差量停机防僵尸轮询）。成员用自己的微信扫某账本的码，微信出现专属 bot 联系人，消息记入该账本，确认响应自动进白名单——身份即授权。绑定谁的码，记账自动写 `tags.attribution=该用户`。例：大牛扫「日常」的码→消息进日常；小牛另扫「日常」→也进日常（家庭共账）；大牛再扫「旅行」→顶替旧绑定转进旅行。二维码一次一扫；B 通道/CLI 测试走 default_ledger 不参与多账本。

### 10.7 数据量与隐私

- 长轮询 ~1KB/次 × 3000 次/天 ≈ 月 <100MB；记账一次 LLM ≈ 2500 in + 400 out ≈ 0.3 分，月成本 1~5 元
- 隐私出网口径与 AI 页一致（一句话/一张图 + 清单 + 少样本 + 聚合）；B 通道多一跳本机网关，A 直连最干净
- 设置面板遗留待办：移除「测试对话」行（联调期保留）

***

## 十一、构建与部署

```
build.bat：读 manifest 版本 → 烘焙 static/version.txt → py_compile 自检 → 清理 app/data 开发残留
          → fnpack 打包 → lulu-ledger.<版本>.fpk
NAS 安装：install_init ─▶ pip --target ${PKGVAR}/site-packages（清华/阿里源兜底）
cmd/main：系统 python3 + PYTHONPATH=site-packages 直跑；启动时依赖双保险补装
```

**备份哲学**：SQLite 开 WAL 后直接拷 .db 会得到不一致快照，必须用 `sqlite3.Connection.backup()` 或 `VACUUM INTO`（backup.py 实现）；备份 = db 副本 + lulu.conf 副本，建议每日一次、保留 7 日；恢复 = 停写 → 覆盖 → `PRAGMA integrity_check` → 重启。小票图片存 `${TRIM_PKGVAR}/receipts/`，库内只存相对路径。

***

## 十二、关键设计决策

1. **SQLite 而非 PG**：年万条级数据量，零配置、文件即库、备份=拷文件；安装只需 pip 依赖，卸载干净
2. **单页原生 JS**：交互复杂度中等，单 HTML + 原生 JS + Chart.js 足够，无前端构建链
3. **预览制导入**：解析不入库 → 用户确认 → 整批可撤销；低置信不阻断（打标事后处理），几百行账单不被几行待确认卡住
4. **退款统一口径**：原消费恒 expense、退款行恒 transfer、扣减归因原消费日期——导入顺序无关，统计口径不分叉
5. **确认才入库**：AI 草稿（App/微信同语义）、对话式修订、删除两步确认——确认前统计零污染、丢弃零残留
6. **传输与大脑分离**：bot_core 通道无关，新通道可插；A/B 互斥由配置热切换
7. **导入上传安全**：大小/白名单/魔数/行数四重校验，防伪装与解压炸弹
8. **无账号体系**：内网部署信任飞牛网关鉴权，「用户」是记账人维度非登录账号

***

## 十三、版本里程碑

| 时间 | 版本 | 内容 |
|---|---|---|
| 2026-09 | v1.0 | 初版：记账/统计/日历/三平台导入/AI 记账；4 份真实账单端到端验证（净支出与官方汇总精确到分一致） |
| 2026-09 | v1.0.1.x | 导入实测修订：退款匹配分层、row_hash 去重归一化、手动退款关联、类型不变式 |
| 2026-10 | v1.0.2.83~90 | 归属名单 → 分组 → 显示解析层 → 用户（记账人）体系与登录页 |
| 2026-10 | v1.0.5~6 | 机器人 B 通道（bot_core/API/MCP/面板）+ A 通道 iLink 直连（协议四轮真机校准） |
| 2026-10 | v1.0.7 | 图片外发链路整体移除，只留小票收图；企微通道移除 |
| 2026-10 | v1.0.8 | 多人多账本绑定；SSE→长轮询归一；全端实时刷新；疑似重复软提示 |
| 2026-10 | v1.2~1.35 | 导出 14 列回环零待确认；与外部项目解耦（export 解析器、文案与注释清理） |
| 2026-10 | v1.40 | 全仓精简重构：口径统一、死代码清理、Chart.js 按需加载（fpk 760KB→730KB），功能零变化 |
| 2026-10 | v1.0.1 | 交付加固：XSS 转义闭环（11 处）、git 基线 + 49 用例测试门禁接入 build.bat、依赖精确锁定、AI 附件分类简称归一化、Web 层可选 Basic Auth（默认关）、logging 轮转改造（applog 双写）、CHANGELOG 独立 |
