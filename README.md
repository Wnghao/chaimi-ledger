# lulu 的记账本（lulu-ledger）

数据全在本机的家庭记账应用，飞牛 NAS（fnOS）原生应用形态。一家人共用，多账本分账，支持账单一键导入、AI 记账和微信机器人。

> 数据不出家门：所有账目存在 NAS 本机的 SQLite 单文件里，备份就是拷文件。

## ✨ 功能特性

- 📚 **多账本分账**：日常 / 旅行 / 装修各记各的，成员与预算一起管
- 📥 **账单导入**：支付宝 / 微信 / 京东 / 云闪付一键导入，自动分类 + 退款匹配 + 重复检测
- 📊 **统计看板**：日历收支热力、分类饼图、趋势曲线、月度对比、人情往来
- 🤖 **AI 记账**：一句话记一笔、小票拍照识别、对话查账、月度分析（填 Key 即用，支持百炼 / 智谱 / DeepSeek / 本地 OpenAI 兼容模型）
- 💬 **微信机器人**：微信里对话记账查账，多人多账本（iLink Bot 通道）
- 🌓 **明暗主题**：全站深色模式，多端实时同步（长轮询，任意端记账其他端 1 秒内刷新）
- 🔒 **隐私优先**：单文件 SQLite 存储，无云端上报，AI 识别仅在你配置了 Key 后调用对应服务

## 🏗️ 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · uvicorn |
| 前端 | 单 HTML + 原生 JS + Chart.js 4（按需加载，无构建链） |
| 存储 | SQLite 单文件（`lulu.db`） |
| 部署 | 飞牛 fnOS 原生应用（fpk 包），网关 `/app/lulu-ledger` 反代 |

设计细节见 [DESIGN.md](DESIGN.md)（架构 / 数据库 / API / AI / 导入 / 微信机器人全设计）。

## 🚀 部署

### 部署前提（重要）

本应用为**家庭内网自托管**设计：Web 界面没有应用层登录鉴权，访问控制完全依赖
飞牛 fnOS 网关与你的局域网边界；机器人网关接口（`/api/bot/*`）则有独立 Bearer Token。

- ✅ 适合：家庭 NAS、内网服务器、Tailscale/WireGuard 等私有组网
- ⛔ 不要：将网关反代或 8710 端口直接暴露到公网（任何人可读写全部账目）
- 依赖版本已锁定（见 `app/src/requirements.txt`）：升级依赖需先跑测试再提交

**可选：开启 Web 层 Basic Auth**（v1.0.1+，需要比内网边界多一层门时）

编辑配置文件 `lulu.conf`（飞牛在 `@PKGETC/var/`，本地开发在 `app/src/`）：

```json
"web_auth": { "enabled": true, "username": "你的用户名", "password": "你的密码" }
```

保存后重启应用。启用后浏览器首次访问弹原生登录框，输一次即记住；`/healthz` 探活不受影响。**用户名或密码留空时恒拒绝**（fail-closed），配置好后才会放行。

### 飞牛 NAS（推荐）

1. 在飞牛应用中心安装本应用的 fpk 包（或从源码自行打包，见下）
2. 打开应用，设置一个用户名即可开始记账
3. 需要机器人 / AI 时，在「设置」页按向导配置

### 本地开发

```bash
cd lulu-ledger-fn/app/src
pip install -r requirements.txt
python server.py --host 127.0.0.1 --port 8710
```

浏览器打开 <http://127.0.0.1:8710> 即可。数据库会自动创建在 `app/data/lulu.db`。

## 📦 构建 fpk

```bat
build.bat
```

脚本会做语法自检、清理开发数据（`app/data/`）、调用 fnpack 打包并按 manifest 版本号命名输出。
`fnpack.exe` 是飞牛专有打包工具，**不随本仓库分发**，请从飞牛开发者渠道获取后放在仓库根目录。

## 📁 目录结构

```
lulu-ledger/
├── DESIGN.md            # 设计文档（架构/数据库/API/AI/导入/微信机器人）
├── build.bat            # fpk 打包脚本
└── lulu-ledger-fn/
    ├── manifest         # 飞牛应用清单
    ├── cmd/             # 飞牛生命周期脚本（安装/升级/启停回调）
    ├── config/          # 权限与资源声明
    ├── wizard/          # 卸载向导
    └── app/
        ├── src/         # 后端：server / db / ai / import_bill / bot_* 等
        ├── static/      # 前端：原生 JS + Chart.js
        └── server/      # 应用图标等静态资源
```

## 🧩 开源引用

本项目直接打包分发的前端库（许可声明保留在文件内）：

| 组件 | 版本 | 许可证 | 说明 |
|---|---|---|---|
| [Chart.js](https://www.chartjs.org) | 4.4.3 | MIT | 统计图表，`app/static/chart.umd.min.js` |

运行时通过 pip 安装的依赖（见 `app/src/requirements.txt`）：

| 库 | 许可证 | 用途 |
|---|---|---|
| fastapi | MIT | Web 框架 |
| uvicorn | BSD-3-Clause | ASGI 服务器 |
| openai | Apache-2.0 | LLM / 视觉模型客户端（OpenAI 兼容协议） |
| openpyxl | MIT | 微信 xlsx 账单解析 |
| python-multipart | Apache-2.0 | 文件上传 |
| segno | BSD-3-Clause | 机器人扫码二维码 |
| pycryptodome | BSD-2-Clause / 公有领域双许可 | 凭证加密存储 |

## 📄 许可证

本项目以 [MIT License](LICENSE) 开源，© 2026 liulu。
