# 股票智能分析系统 (daily_stock_analysis) 项目深度分析与上下文指南

> **使用说明**：本文档是对 `daily_stock_analysis` 项目的完整架构、模块职责、数据流向、开发契约与核心机制的深度分析说明。每次需要让 AI Agent 或新开发者快速理解本项目上下文时，可直接读取或加载本文件。

---

## 1. 项目定位与核心能力

`daily_stock_analysis` 是一套全流程、多市场（A股、港股、美股、台股等）的**股票智能分析与决策系统**。系统融合了传统技术分析（MA、RSI、MACD、筹码集中度、乖离率等）、基本面数据、资讯/RSS情报拉取，并结合 LLM（大语言模型）与 Multi-Agent（多智能体）架构，实现自动化分析、决策信号生成、后验效果评估及多渠道通知推送。

### 核心能力覆盖：
1. **多数据源抓取与 Fallback 降级**：整合 Tushare、AkShare、Efinance、Longbridge（长桥）、YFinance、PyTdx、TickFlow、Baostock 等多个数据源，具备自动熔断降级机制。
2. **多模式分析编排**：
   - **单股/批量分析**：技术面+筹码面+基本面+AI综合研报生成。
   - **大盘复盘 (Market Review)**：自动统计沪深港美市场整体走势、涨跌分布、热门板块与资金流向。
   - **Agent 深度推理 (Orchestrator)**：多 Agent 协作（技术 Agent、情报 Agent、风险 Agent、专家 Agent、决策 Agent）。
   - **策略回测 (Backtest Engine)**：基于历史信号和 k 线走势评估 AI 建议准确率。
3. **实时告警与监控 (Alert Center)**：基于 EventMonitor 规则引擎轮询关注标的，触发策略信号即刻告警。
4. **情报与资讯检索 (Intelligence Source)**：合规 RSS/Atom 资讯拉取、向量/语义去重、关联股票提取。
5. **多端交互与接入**：
   - **FastAPI REST API** (`api/v1`)
   - **React Web UI** (`apps/dsa-web`)
   - **Electron 桌面客户端** (`apps/dsa-desktop`)
   - **多平台 IM Bot** (`bot/` 支持飞书、钉钉、Telegram、Discord、企业微信、Slack)
6. **多渠道富媒体通知**：支持 Markdown/HTML 报告转精美分享长图 (Image Render)，推送到企业微信、飞书、Telegram、邮件等 10+ 渠道。

---

## 2. 整体系统架构与分层

```
+-----------------------------------------------------------------------------------+
|                                  用户交互 / 入口层                                 |
| CLI (main.py) | FastAPI (server.py) | Web UI (dsa-web) | Desktop (dsa-desktop) | Bot |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                                    应用与服务层                                    |
| AnalysisService | AlertWorker | MarketReview | IntelligenceService | Backtest      |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                                 核心编排与 Agent 引擎                              |
| Pipeline (StockAnalysisPipeline) | AgentOrchestrator (Technical/Intel/Risk/Decision)|
+-----------------------------------------------------------------------------------+
                       /                  |                  \
                      v                   v                   v
+---------------------------+   +-------------------+   +---------------------------+
|       数据提供层          |   |    LLM 适配抽象层  |   |        持久化存储层       |
| data_provider/ (Tushare,  |   | src/llm/ (LiteLLM |   | src/storage.py (SQLite,   |
| AkShare, Longbridge, etc.)|   | Local CLI, Hermes)|   | SQLAlchemy ORM, Repos)    |
+---------------------------+   +-------------------+   +---------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                                 推送与通知引擎                                    |
| src/notification.py | md2img | share_image | Enterprise WeChat, Feishu, Telegram  |
+-----------------------------------------------------------------------------------+
```

---

## 3. 目录结构与模块职责

```
daily_stock_analysis/
├── main.py                        # CLI 主调度程序、服务启动与模式分支
├── server.py                      # FastAPI Web/API 服务入口
├── webui.py                       # Web UI 前端资源启动辅助脚本
├── AGENTS.md                      # AI 协作与开发硬规则真源
├── CLAUDE.md                      # 软链接至 AGENTS.md
├── pyproject.toml / requirements.txt # Python 依赖定义
│
├── api/                           # FastAPI REST API 接口层
│   ├── app.py                     # FastAPI 实例创建、中间件与全局异常捕获
│   ├── deps.py                    # API 依赖注入 (认证、数据库 Session)
│   └── v1/
│       ├── endpoints/             # REST 端点 (analysis, alert, agent, stocks, system_config, etc.)
│       └── router.py              # API v1 路由汇总
│
├── apps/                          # 前端与桌面客户端
│   ├── dsa-web/                   # React + Vite + TypeScript 前端项目
│   └── dsa-desktop/               # Electron 桌面打包客户端
│
├── bot/                           # 多平台 IM 机器人接入层
│   ├── dispatcher.py              # Bot 消息分发与指令解析
│   ├── handler.py                 # 消息处理逻辑
│   └── platforms/                 # 各平台适配器 (feishu, dingtalk, telegram, discord, etc.)
│
├── data_provider/                 # 多数据源适配与 Fallback 层
│   ├── base.py                    # 数据源抽象基类、标准化代码转换、Fallback 规则
│   ├── tushare_fetcher.py         # Tushare 数据源
│   ├── akshare_fetcher.py         # AkShare 免密公有数据源
│   ├── efinance_fetcher.py        # Efinance 数据源
│   ├── longbridge_fetcher.py      # 长桥 OpenAPI (港/美/A股)
│   ├── yfinance_fetcher.py        # Yahoo Finance 全球数据源
│   ├── pytdx_fetcher.py           # 通达信 L1/L2 高频行情
│   ├── tickflow_fetcher.py        # TickFlow 资金流与 Tick 数据
│   ├── baostock_fetcher.py        # Baostock 历史 K 线
│   └── tw_institutional_fetcher.py# 台股筹码数据
│
├── src/                           # 后端核心业务逻辑
│   ├── agent/                     # Autonomous Multi-Agent 框架
│   │   ├── orchestrator.py        # AgentOrchestrator 编排器 (quick/standard/full/specialist 模式)
│   │   ├── executor.py            # Agent 单步执行器与工具调用
│   │   ├── codex_agent_backend.py # Codex / Claude Code CLI Agent 后端适配
│   │   ├── tools/                 # Agent 可调用的工具定义 (技术指标、新闻检索等)
│   │   ├── agents/                # 专家 Agent 定义 (technical, intel, risk, decision)
│   │   └── skills/                # Agent Skill 与策略评分引擎
│   ├── core/                      # 主流程与核心引擎
│   │   ├── pipeline.py            # StockAnalysisPipeline 分析流水线编排
│   │   ├── market_review.py       # 大盘复盘引擎
│   │   ├── backtest_engine.py     # 策略与决策信号回测引擎
│   │   ├── config_registry.py     # 系统配置项注册表 (配置 UI 元数据唯一真源)
│   │   └── trading_calendar.py    # 交易日历计算 (沪深、港、美、台)
│   ├── llm/                       # LLM Provider 多模型抽象与 Token 统计
│   │   ├── generation_backend.py  # GenerationBackend 抽象基类
│   │   ├── backend_factory.py     # LLM 后端工厂
│   │   ├── litellm_backend.py     # LiteLLM 统一适配器 (OpenAI, DeepSeek, Qwen, Claude, etc.)
│   │   ├── local_cli_backend.py   # 本地 CLI 模型后端
│   │   └── usage.py               # LLM 消耗 Token 与成本计算器
│   ├── repositories/              # 数据访问层 (Repository Pattern)
│   │   ├── stock_repo.py / alert_repo.py / analysis_repo.py / decision_signal_repo.py ...
│   ├── schemas/                   # Pydantic 数据契约 Schema
│   │   ├── report_schema.py       # 分析报告结构 Schema
│   │   ├── decision_action.py     # 决策建议 (买入/卖出/观望) Schema
│   │   └── analysis_context_pack.py# 上下文数据包契约 Schema
│   ├── services/                  # 领域服务层
│   │   ├── analysis_service.py    # 分析任务服务
│   │   ├── alert_service.py       # 告警规则与实时监听服务
│   │   ├── intelligence_service.py# 情报与 RSS 资讯服务
│   │   ├── screening_service.py   # 选股筛选引擎服务
│   │   ├── portfolio_service.py   # 自选股组合与风险服务
│   │   ├── system_config_service.py# 系统动态配置服务 (3层合并)
│   │   └── run_flow.py            # 执行流辅助
│   ├── notification.py            # 多渠道通知发送器
│   ├── notification_sender/       # 各渠道发送实现 (wechat, feishu, telegram, mail, etc.)
│   ├── md2img.py / share_image.py # Markdown 报告转图片渲染器
│   └── storage.py                 # SQLite ORM 模型定义与单例 Session 管理
│
├── scripts/                       # 运维、构建、门禁与脚本工具
│   ├── ci_gate.sh                 # 后端门禁检查脚本 (flake8 + pytest)
│   ├── check_ai_assets.py         # AI 协作治理文件一致性校验
│   └── test.sh                    # 本地测试与 Quick 测试脚本
└── docs/                          # 模块与专题文档中心
```

---

## 4. 数据源与 Fallback 降级架构

项目不依赖单一数据源，而是构建了一套兼具**高可用**与**容错降级**的 Provider 映射关系。

### 4.1 标的代码规范化 (`canonical_stock_code`)
- **A 股**：6 位数字（如 `600519`、`000001`、`300750`）
- **港股**：`hk` + 5 位数字（如 `hk00700`、`hk09988`）
- **美股**：纯大写字母（如 `AAPL`、`TSLA`、`NVDA`）
- **台股**：`tw` + 数字（如 `tw2330`）
- **指数标的**：以 `sh`/`sz`/`hk`/`us` 标识（如 `sh000001` 上证指数, `us.DJI` 道琼斯）

### 4.2 典型 Fallback 链路
```
                +-------------------+
                |   数据请求发起    |
                +-------------------+
                          |
                          v
                +-------------------+
                | 优先数据源 (如    | ---- 成功 ----> 返回标准化 Dataframe / Standard Record
                |  Tushare / 商业API)|
                +-------------------+
                          |
                        失败
                          v
                +-------------------+
                | 降级数据源 (如    | ---- 成功 ----> 返回数据 (标记 degraded 状态)
                |  AkShare/Efinance)|
                +-------------------+
                          |
                        失败
                          v
                +-------------------+
                |  备用源 / 离线缓存 | ---- 成功 ----> 返回兜底数据
                +-------------------+
                          |
                        全败
                          v
                静默抛出/记录风险事件，不拖垮主流程
```

---

## 5. Multi-Agent 引擎架构与模式

在 `src/agent/orchestrator.py` 中，系统支持 4 种 depth/cost 递增的 Agent 运行模式：

| 运行模式 | 阶段链路 (Stages) | 适用场景 | LLM 调用成本 |
| --- | --- | --- | --- |
| `quick` | Technical -> Decision | 快速行情扫描与技术面评估 | 最低 (~2 LLM Calls) |
| `standard` | Technical -> Intel -> Decision | 默认分析模式 (结合最新新闻资讯) | 中等 (~3 LLM Calls) |
| `full` | Technical -> Intel -> Risk -> Decision | 包含风控拦截与追高防守校验 | 较高 (~4 LLM Calls) |
| `specialist` | Technical -> Intel -> Risk -> Specialist Evaluation -> Decision | 深度分析，包含筹码、财报专家评价 | 最高 (多 LLM 深度 Reasoning) |

### Agent 角色划分：
1. **Technical Agent**：计算 MA5/10/20 排列、乖离率 (Bias)、RSI、MACD、支撑/压力位、缩量/放量突破。
2. **Intel Agent**：拉取个股相关新闻、公告、RSS 研报，判定利好/利空情绪分阶。
3. **Risk Agent**：评估乖离率 > 5% 追高风险、解禁压力、大股东减持、大盘下行贝塔风险。
4. **Specialist Agent**：针对筹码集中度、基本面估值 (PE/PB percentile) 给出专业加权评分。
5. **Decision Agent**：汇总各 Agent 输出，产生标准 **DecisionSignal**（操作建议：买入/加仓/减持/观望，目标价，止损价，分仓比例与置信度）。

---

## 6. 数据存储与 Pydantic Schema 契约

### 6.1 SQLAlchemy 数据库模型 (`src/storage.py`)
数据库使用 SQLite（默认文件 `stock_analysis.db`），核心表包括：
- `stocks_daily`: 每日 K 线行情与技术指标。
- `analysis_records`: 股票分析生成的研报历史、LLM Token 消耗、操作建议 JSON。
- `market_review_records`: 大盘复盘研报与情绪走势。
- `decision_signals`: AI 建议池与决策信号记录（用于后验准确率评估）。
- `intelligence_items`: 抓取的合规 RSS / 新闻资讯条目。
- `alert_rules` / `alert_records`: 告警规则定义与触发记录。
- `portfolio_items` / `portfolio_records`: 用户自选股组合与仓位管理。
- `system_configs`: 数据库持久化的系统配置覆盖表。

### 6.2 3 层配置解析架构 (`src/services/system_config_service.py`)
系统配置采取三层优先级的合并策略：
1. **环境变量 (.env / Process env)**（优先级最高）
2. **数据库持久化配置 (`system_configs` 表)**（Web UI 修改后生效）
3. **默认配置 (`src/config.py`)**（兜底默认值）

配置字段元数据统一收口在 `src/core/config_registry.py`，保证 Web UI 动态设置页与后端字段校验的一致性。

---

## 7. 常见运行指令与入口

### 7.1 CLI 常用命令 (`main.py`)
```bash
# 1. 执行指定股票分析
python main.py --stocks 600519,hk00700,AAPL

# 2. 仅执行大盘复盘
python main.py --market_review

# 3. 运行策略回测
python main.py --backtest --backtest-code 600519

# 4. 启动定时任务调度器
python main.py --schedule

# 5. 启动 Web/API 服务
python main.py --serve --host 0.0.0.0 --port 8000
# 或使用 uvicorn 直接启动 API 服务:
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

### 7.2 前端与桌面端开发 (`apps/`)
```bash
# Web 前端构建与测试
cd apps/dsa-web
npm ci
npm run lint
npm run build

# Electron 桌面端构建
cd ../dsa-desktop
npm install
npm run build
```

---

## 8. 代码质量与 CI/CD 门禁规范

根据仓库全局规范 (`AGENTS.md`)：

1. **AI 治理文件检查**：修改规则或 Skill 时必须通过 `python scripts/check_ai_assets.py`。
2. **后端单元测试与 Lint 门禁**：
   ```bash
   ./scripts/ci_gate.sh
   python -m pytest -m "not network"
   python -m py_compile <changed_python_files>
   ```
3. **变更收敛与稳定性优先**：
   - 杜绝仅靠静默捕获 (`try-except pass`) 或返回 Dummy 数据掩盖契约破坏。
   - 保持单一数据源失败不拖垮整条分析流水线。
   - 修改 API/Schema/配置时，必须同步检查后端的 FastAPI / Pydantic、Web 前端与 Desktop 客户端的兼容性。
4. **日志与版本 Tag**：
   - Commit title 需包含 `#patch`、`#minor` 或 `#major` 才会触发 GitHub Actions 自动 Tag 发版。
   - 更新文档与 CHANGELOG 需遵循扁平化 `[Unreleased]` 规范：`- [类型] 描述`（类型：`新功能`/`改进`/`修复`/`文档`/`测试`/`chore`）。

---

## 9. Context 调优速查与常用引用

在 AI 协作或对话中提出涉及具体模块的问题时，可参考以下文件锚点：

- **添加或扩展数据源** ➔ 参见 `data_provider/base.py` 与 `data_provider/tushare_fetcher.py`
- **调整 LLM 模型提供商 / Prompt** ➔ 参见 `src/llm/litellm_backend.py` 与 `src/services/analysis_context_builder.py`
- **扩展 Agent 策略与技能** ➔ 参见 `src/agent/orchestrator.py` 与 `src/agent/skills/`
- **修改 Web API 端点或认证** ➔ 参见 `api/v1/endpoints/` 与 `src/auth.py`
- **调整通知格式或新增推送渠道** ➔ 参见 `src/notification.py` 与 `src/notification_sender/`
- **修改系统配置字段与 UI 呈现** ➔ 参见 `src/core/config_registry.py` 与 `.env.example`

---
*本报告由系统深度扫描与架构分析自动生成，建议作为项目全局 Context 保存并持续维护。*
