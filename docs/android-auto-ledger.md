# 柴米记账 · Android 自动记账 App 开发文档

> 交付对象：负责开发的 AI Agent / 开发者。
> 目标：一个 Android App，通过**无障碍服务（AccessibilityService）**监听微信 / 支付宝的支付完成页面，自动解析交易要素并记入柴米记账服务端。
> 本文给出完整的系统边界、API 契约、识别规则与分期计划。实现时若与服务端行为有出入，以服务端代码为准（仓库 `chaimi-ledger-fn/app/src/server.py`）。

---

## 1. 目标与范围

**做什么：**
- 监听微信（`com.tencent.mm`）与支付宝（`com.eg.android.AlipayGphone`）的「支付成功」页面
- 解析：金额、交易对方/商户、支付方式（零钱/银行卡尾号）、时间
- 自动调用柴米记账 API 创建流水；分类不确定的进「待确认」队列（服务端已有 UI 承接）

**不做什么（边界）：**
- 不读取、不上传支付页面之外的任何屏幕内容
- 不拦截、不修改任何支付行为（只读监听）
- 不做 iOS 端、不做 root/Xposed 方案

## 2. 总体架构

```
┌─ Android App ────────────────────────────────┐
│  AccessibilityService（无障碍监听层）          │
│    ├─ 窗口事件过滤（包名 + 页面特征）          │
│    ├─ 节点树文本抓取 → 交易要素解析器          │
│    └─ 幂等键生成（包名+时间+金额 hash）        │
│  Room 本地队列（待上传/已上传，防丢防重）       │
│  WorkManager（网络重试，退避上传）             │
│  设置页（服务端地址 / 账号登录 / 账本与映射配置）│
└──────────────┬───────────────────────────────┘
               │ HTTPS/HTTP（局域网）
┌──────────────▼───────────────┐
│  柴米记账服务端（FastAPI）     │
│  POST /api/login  → 会话 Cookie│
│  POST /api/transactions → 记账 │
│  need_confirm → 待确认队列 UI  │
└──────────────────────────────┘
```

**核心设计原则：**
1. **本地先落库再上传**：解析成功先写 Room 队列，上传成功标记已完成。App 被杀/断网都不丢单。
2. **幂等键防重**：`pay_id = sha256(包名 + 支付时间 + 金额 + 商户前8字)`，上传前查队列与远端，重复跳过。
3. **不确定不硬猜**：解析不出分类 → `need_confirm=true` 直接进服务端待确认队列，由人在网页上一键确认。

## 3. 服务端 API 契约

### 3.1 基础约定
- Base URL 由用户在 App 设置页填写，如 `http://192.168.68.103:18710`。
- 服务端可能带**网关前缀**（飞牛 fnOS 形态为 `/app/chaimi-ledger`）。App 需支持可选的「路径前缀」设置项，拼接为 `{base}{prefix}/api/...`。
- 服务端开启了登录（`web_auth.enabled`）时，所有 API 需携带会话 Cookie。未登录调用一律 `401 {"detail":"unauthorized"}`（响应**不带** `WWW-Authenticate` 头）。

### 3.2 登录（签发会话）

```
POST /api/login
Content-Type: application/json
{"username": "hao", "password": "***"}

200 → Set-Cookie: chaimi_sess=<token>; HttpOnly; SameSite=Lax; Max-Age=2592000
      body: {"ok": true}
401 → {"detail": "用户名或密码错误"}
400 → {"detail": "登录未启用或凭证未配置"}
```

- 客户端用 CookieJar（OkHttp 自带）自动携带，**30 天有效**；过期会重新收到 401 → 引导重新登录。
- 凭证用 `EncryptedSharedPreferences` 存储。

### 3.3 账本 / 分类 / 账户（App 设置页与映射用）

```
GET /api/ledgers                      → [{"id":1,"name":"日常账本",...}]
GET /api/categories?ledger={id}       → [{"id":27,"name":"三餐","type":"expense","parent_id":26},...]
GET /api/accounts?ledger={id}         → [{"id":1,"name":"支付宝","type":"e-wallet"},...]
```

- 分类是**任意层级树**（`parent_id` 自引用），映射选择器要支持树形展示；每个节点都可被指定。
- 用户选择当前账本后，App 记住 `ledger_id`。

### 3.4 创建流水（核心）

```
POST /api/transactions?ledger={id}
Content-Type: application/json
{
  "type": "expense",            // expense | income | transfer
  "amount": 2580,               // 【分】整数。¥25.80 → 2580。解析时必须精确转分，禁用浮点
  "date": "2026-10-30 12:31",   // "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM"
  "name": "微信支付",            // 交易名称（支付方式名等）
  "merchant": "瑞幸咖啡",        // 交易对方/商户
  "note": "",                   // 备注
  "category_id": 27,            // 可为 null（→ need_confirm=true 进待确认）
  "account_id": 3,              // 支付账户 id（按支付方式映射；映射不到留 null）
  "source": "auto",             // 固定 "auto"，便于网页端识别自动记账来源
  "tags": {"auto_pay_id": "sha256hex", "channel": "wechat"},
  "need_confirm": false         // 分类未确定时置 true
}

200 → {"id": 12345}
401 → 会话过期，重新登录后重试
```

- 响应只回 `id`；创建成功后网页端实时刷新（服务端长轮询自动推送）。
- **退款**：若识别到「退款」场景，可查目标流水后用 `refund_for` 关联（M4 再做，一期不实现）。

### 3.5 待确认队列（兜底承接）

分类解析失败/低置信的流水以 `need_confirm=true` 入库，服务端网页端已有完整 UI：
- 流水页「待确认」筛选 + 一键确认全部
- 待确认徽标计数

App 无需自己实现确认界面，只需保证入库时语义正确。

## 4. 无障碍服务设计

### 4.1 服务配置（accessibility_service_config.xml）

```xml
<accessibility-service
    android:accessibilityEventTypes="typeWindowStateChanged|typeWindowContentChanged"
    android:packageNames="com.tencent.mm,com.eg.android.AlipayGphone"
    android:accessibilityFeedbackType="feedbackGeneric"
    android:accessibilityFlags="flagReportViewIds|flagRetrieveInteractiveWindows"
    android:canRetrieveWindowContent="true"
    android:notificationTimeout="100"
    android:description="@string/a11y_desc" />
```

要点：
- `packageNames` 锁死两个支付 App，其他界面事件直接丢弃（性能与隐私双保险）
- `TYPE_WINDOW_CONTENT_CHANGED` 高频触发，必须做**事件节流**（300ms 内合并）与**页面指纹缓存**（同一页面内容 hash 不重复解析）

### 4.2 支付页识别规则（v1 样本规则，需持续维护）

| 维度 | 微信支付成功页 | 支付宝付款成功页 |
|---|---|---|
| 包名 | `com.tencent.mm` | `com.eg.android.AlipayGphone` |
| 页面锚点 | 文本含「支付成功」或「已支付」 | 文本含「付款成功」或「支付成功」 |
| 金额 | `¥` 后数字（如 `¥25.80`），节点 class 常为 TextView 且字重最大 | `￥` 或 `¥` 后数字 |
| 商户 | 「收款方」/「商家」相邻节点文本 | 「收款方」/「对方账户」相邻节点 |
| 支付方式 | 「支付方式」/「零钱」/「招商银行(1234)」相邻节点 | 「付款方式」相邻节点 |
| 时间 | 页面常无时间 → 用抓取时刻；可尝试「交易时间」节点 | 「交易时间」节点常有 |

解析器实现为**规则表驱动**（JSON 配置，可远程更新）：
```json
{
  "package": "com.tencent.mm",
  "page_anchors": ["支付成功", "已支付"],
  "amount": {"regex": "[¥￥]\\s*([0-9,]+\\.?[0-9]{0,2})"},
  "merchant": {"anchor": ["收款方", "商家"], "offset": "next"},
  "pay_method": {"anchor": ["支付方式"], "offset": "next"}
}
```
- 节点树遍历：`rootInActiveWindow` DFS，收集 `(text, viewIdResourceName, bounds)` 三元组，先按锚点定位再取邻接节点（同层 next / 父子相邻）。
- **双确认再入库**：页面锚点命中 **且** 金额解析成功，缺一不可。只有锚点没金额 → 忽略（多为支付中页面）。
- 微信「支付成功」存在通知栏（服务通知）路径：M4 用 `NotificationListenerService` 作兜底通道，规则独立维护。

### 4.3 防抖 / 防重

- 幂等键：`sha256(package|页面时间或抓取时间截断到秒|金额分|商户前8字)`，Room 表 `pay_id` 唯一索引。
- 同一 pay_id 的上传失败重试（WorkManager 指数退避，最多 24h），成功即标记。
- 支付成功页可能停留数秒且内容多次变化 → 内容 hash 去重 + 解析完成后 5s 内同页面不再解析。

## 5. 分类 / 账户映射策略

- 设置页内建「映射规则表」（本地 Room，可导入导出 JSON）：
  - 商户关键词 → category_id（如「瑞幸|星巴克|喜茶」→ 咖啡茶饮类分类）
  - 支付方式关键词 → account_id（「零钱」→ 微信零钱账户；「招商银行」→ 对应银行卡账户）
- 匹配顺序：用户自定义规则 > 无 → `category_id=null` 且 `need_confirm=true`。
- 规则表初始为空时 App 完全可用（全部进待确认），映射是增量优化不是前置依赖。

## 6. 权限与用户引导

1. **无障碍权限**：首次启动引导页 → 跳转系统无障碍设置 → 检测 `AccessibilityService` 是否 enabled，未开启常驻状态条提示。
2. **电池优化**：请求加入白名单（`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`），否则后台监听可能被杀。
3. **通知监听（M4）**：`NotificationListenerService` 需用户单独授权。
4. 每次自动记账成功发一条**自行管理的本地通知**（可关），让用户感知并可在网页端修正——信任来自透明。

## 7. 隐私与安全红线

- 只解析支付页文本，抓取的原始文本**不落盘**，解析完即弃（仅保留结构化字段）。
- 服务端地址默认仅限局域网；如暴露公网必须走 HTTPS 反代（文档提示用户）。
- 日志不输出金额/商户明文（debug 构建除外）。
- App 不请求 INTERNET 之外的危险权限（无障碍权限除外，其用途在引导页明示）。

## 8. 分期里程碑

| 期 | 内容 | 验收标准 |
|---|---|---|
| M1 | App 骨架 + 设置页 + 登录 + 手动记账测试按钮 | 能登录、能在网页看到 App 记的账 |
| M2 | 微信支付成功页无障碍监听 + 解析 + 自动入库 | 真机微信付款 10 笔，≥9 笔自动入库且金额/商户正确 |
| M3 | 支付宝支持 + 映射规则表 | 同上；零钱/银行卡正确映射到账户 |
| M4 | 通知栏兜底通道 + 退款识别 | 微信「支付成功」服务通知可解析；退款行关联原流水 |
| M5 | 打磨：规则远程更新、统计页（本月自动记账笔数）、异常上报 | 断网 24h 后恢复，队列全部补传无重复 |

## 9. 测试要点

- 解析器用**真实页面文本快照**做单元测试（把各版本微信/支付宝成功页的节点文本存 fixtures）
- 金额边界：千分位（¥1,234.56）、无小数（¥25）、大额（¥12,345.67）必须全对——金额错是记账 App 最不可接受的 bug
- 幂等：同一条支付事件重复触发解析 5 次 → 服务端只 1 笔
- 会话过期：30 天后首次上传 401 → 自动重登 → 重传成功
- 服务被杀：`adb shell am force-stop` 后重新打开支付页面，监听自动恢复

---

*服务端对接以 `Wnghao/chaimi-ledger` 仓库代码为准；API 变更时同步更新本文档第 3 节。*
