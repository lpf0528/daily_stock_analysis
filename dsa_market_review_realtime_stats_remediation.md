# DSA 市场复盘实时市场统计阻断：修复任务书

## 1. 任务目标

消除 DSA 在生成 A 股市场复盘时，因实时市场统计数据源不可达而长期卡在 `processing` 的问题。失败必须在可配置的时间预算内形成可审计终态；不得把缺失、缓存或过期数据伪装成当前交易日实时数据。

本任务只改 `daily_stock_analysis`。`cross_project_pipeline` 在本次改造验收通过前，仍必须把 DSA 视为未就绪，且不得批量提交候选股。

## 2. 已复现实证与根因

2026-08-12 对候选股 `605117` 提交了一次 API 探针：

```json
{
  "stock_code": "605117",
  "stock_name": "德业股份",
  "original_query": "605117",
  "selection_source": "import",
  "report_type": "detailed",
  "async_mode": true,
  "analysis_phase": "postmarket",
  "notify": false,
  "report_language": "zh"
}
```

API 已接受任务，且该股 42 条日 K 数据获取成功；随后 DSA 自动生成“每日大盘上下文”，任务进度停在 16%，没有生成报告。`notify=false` 只禁止通知，**不会**关闭每日大盘上下文。

已观测到 EastMoney `push2` 连接被远端关闭（`RemoteDisconnected`；stock 项目同一时段也出现 `curl (56)`）。DSA 的问题不是 K 线不可用，而是依赖实时全市场行情的统计与板块排行不可控地串行回退。

相关实现位置如下：

| 位置 | 当前行为 | 问题 |
| --- | --- | --- |
| `src/market_analyzer.py::MarketAnalyzer.get_market_overview` | 依次拉取指数、市场涨跌统计、行业排行、概念排行 | 整体没有截止时间，也没有数据质量状态。 |
| `src/market_analyzer.py::_get_market_statistics` | 调用 `DataFetcherManager.get_market_stats(purpose="market_review:cn")` | 空结果默认留为数值 `0`，容易与真实零值混淆。 |
| `data_provider/base.py::DataFetcherManager.get_market_stats` | TickFlow 后，按 `_fetchers` 串行调用每个 provider | 没有全局预算和按 provider 的时间预算；一个阻塞调用会占住分析线程。 |
| `data_provider/efinance_fetcher.py::EfinanceFetcher.get_market_stats` | `ef.stock.get_realtime_quotes()`，实际依赖东财 | 有 `EFINANCE_CALL_TIMEOUT`（默认 30 秒），但超时线程无法强杀；连续回退仍可能堆积。 |
| `data_provider/akshare_fetcher.py::AkshareFetcher.get_market_stats` | 先 `ak.stock_zh_a_spot_em()`（东财），后 `ak.stock_zh_a_spot()`（新浪） | 两次调用没有同级硬超时；前者与 efinance 不是独立上游。 |
| `src/services/daily_market_context.py` | 市场上下文生成异常后允许个股继续 | 现在“异常”发生得太晚；卡住时根本没有终态。 |

## 3. 不可违反的行为约束

1. 不得把历史缓存、空字典或默认 `0` 当作当日实时市场统计。
2. 一个市场复盘请求的实时数据阶段必须在总预算内结束；推荐默认总预算 25 秒，单个 provider 5 秒。
3. 已确认 EastMoney 链路失败时，同一次复盘不得由 efinance、AkShare-EM、行业/概念排行重复访问同一上游。
4. 严格的日常编排模式下：市场复盘未取得合格数据和报告时，必须终止为 `failed`，不能提交候选个股，也不能推送。
5. 独立单股分析可选择“降级继续”，但报告和 API 必须明确标记 `market_context_status=unavailable` 或 `partial`；不能称“已完成大盘复盘”。
6. 不得在日志、报告、接口响应或测试夹具中写入 Token、Cookie、Webhook 等敏感值。

## 4. 推荐设计

### 4.1 引入明确的运行策略与配置

在 `src/config.py`、`src/core/config_registry.py`、`.env.example` 和系统设置 API 中增加以下配置（变量名可微调，但语义不能缺失）：

| 配置 | 推荐默认值 | 作用 |
| --- | --- | --- |
| `MARKET_REVIEW_REALTIME_MODE` | `bounded` | `strict`：数据不合格即失败；`bounded`：限时拉取并按调用方策略处理；`disabled`：不取实时广度，只允许明确标注的非实时复盘。 |
| `MARKET_REVIEW_TOTAL_TIMEOUT_SECONDS` | `25` | 指数、广度、行业、概念合计的实时数据总预算。 |
| `MARKET_REVIEW_PROVIDER_TIMEOUT_SECONDS` | `5` | 单 provider / 单上游请求的最大等待时间。 |
| `MARKET_REVIEW_STATS_PROVIDERS` | `tickflow,tushare,sina` | 市场广度的允许顺序；不要把 `efinance` 和 `akshare_em` 作为默认独立回退。 |
| `MARKET_REVIEW_STRICT_FOR_ORCHESTRATOR` | `true` | 编排入口要求完整、当日、可审计的市场复盘。 |
| `MARKET_REVIEW_ALLOW_STALE_CACHE_SECONDS` | `0` | 默认禁止将旧缓存用于当日严格复盘；若未来允许，仅限非严格模式，并必须返回真实 `as_of` 与 `stale=true`。 |

配置校验要求：时间值必须为正数且总预算不得小于单 provider 超时；provider 名称必须在白名单中。配置读取失败时采用安全的严格/限时默认值，不能退化为无限等待。

### 4.2 建立带质量信息的结果契约

不要让 `get_market_stats()` 仅返回裸 `dict`。新增内部 Pydantic/dataclass，例如 `MarketDataResult`，至少包含：

```python
status: Literal["fresh", "partial", "unavailable", "stale"]
source: str | None
upstream: str | None
as_of: datetime | None
duration_ms: int
warnings: list[str]
attempts: list[ProviderAttempt]
data: dict[str, int | float] | None
```

`ProviderAttempt` 至少记录 provider、upstream、开始/结束时间、耗时、`success|empty|timeout|skipped|failed`、错误类别（不写原始敏感请求）。

把该结果传播到 `MarketOverview` 与市场复盘结构化 payload。数值字段改为可空值，或另加 `market_stats_available: bool`；关键是 UI/报告能区分“统计为 0”与“未取得统计”。行业、概念、指数也需各自有可用性和 `as_of`。

### 4.3 实现总预算、单调用超时与上游熔断

在 `DataFetcherManager` 增加市场复盘专用执行器（不要直接复用 `_run_with_timeout` 的 fundamental 命名和 semaphore，除非重构为通用、隔离且有测试的实现）：

1. 在 `get_market_overview()` 创建一个 deadline（`monotonic() + total_timeout`），每个子步骤都接收剩余预算。
2. 在 `get_market_stats()` 中只运行配置允许的 provider；每次调用的 timeout 为 `min(provider_timeout, remaining_budget)`。
3. provider 调用必须能返回到调用线程。对库本身可设置网络 timeout 的，应优先设置连接/读取 timeout；线程只可作为最后一道等待上限。线程超时后不得无限创建新线程：要有有界 semaphore、熔断与指标。
4. 给每个 provider 声明实际 `upstream`，例如 `eastmoney`、`sina`、`tickflow`、`tushare`。在一次复盘内，EastMoney 出现 `RemoteDisconnected`、curl 56、连接/读取超时或明确限流后，将 `eastmoney` 标为打开熔断，后续所有同上游调用直接 `skipped`。
5. 行业排行和概念排行沿用同一个 deadline 与熔断状态，不能重新从 25 秒开始计算。
6. 预算耗尽后返回 `unavailable` 或 `partial`，并写入诊断信息；绝不继续阻塞任务线程。

注意：`efinance` 的 `_ef_call_with_timeout()` 注释已明确说明其后台线程可能存活。改造必须限制这类孤儿线程数量，并优先为可控 HTTP 客户端配置实际网络超时。不能仅把 30 秒改小而不处理 AkShare 与重复上游问题。

### 4.4 明确数据源策略

先做真实连通性烟测，再决定默认 provider 顺序；不允许仅根据代码路径宣布“可用”。

建议优先级：

1. 已部署且经烟测可用的 TickFlow；
2. 有合法配置且当前接口权限足够的 Tushare；
3. 已验证的新浪路径；
4. EastMoney 仅作为可选项，且需要独立健康状态为健康。

`efinance` 和 `ak.stock_zh_a_spot_em()` 都依赖 EastMoney，不能互相视为真正的容灾备份。对行业/概念排行也要建立同样的“真实上游”映射。若没有合格替代源，严格模式应快速失败并给出“需要配置/恢复何种数据源”的操作提示，而不是以空市场数据生成结论。

### 4.5 调用方终态策略

定义 `MarketReviewDataUnavailableError`（包含结构化诊断摘要），由 API/队列明确处理：

| 场景 | 推荐处理 |
| --- | --- |
| `/api/v1/analysis/market-review`，或编排器的市场复盘步骤 | 严格模式；在预算内转为任务 `failed`，错误码 `market_review_realtime_data_unavailable`，不生成“完成”报告。 |
| 跨项目日常流程 | 读取上述失败终态，阻断所有个股提交和最终汇总；保存 DSA 诊断产物。 |
| 单股 API `POST /api/v1/analysis/analyze` | 新增请求级 `market_context_policy`：`required`（失败）、`optional`（个股继续但上下文标为不可用）、`disabled`（仅显式授权的调试/离线场景）。默认依旧使用系统配置，不能因为 `notify=false` 自动变为 disabled。 |

单股 optional 模式产生的报告，开头必须显示：市场环境未取得实时统计、缺少哪些字段、来源/时间和该结论的限制。结构化任务结果必须返回 `market_context_status`，供控制台识别。

### 4.6 取消能力（并行可靠性补强）

`TaskStatus` 已有 `CANCEL_REQUESTED` 和 `CANCELLED`，但分析 API 没有公开取消入口。新增：

```text
POST /api/v1/analysis/tasks/{task_id}/cancel
```

返回 202（已请求取消）或 409（已终态）。在市场复盘的子步骤开始前、每次 provider 回退前后检查取消标志；收到取消后停止后续步骤、终态置为 `cancelled`、不产生完成报告且不发送通知。不要试图强杀 Python 正在执行的线程。

## 5. 建议实施顺序

### 阶段 A：基线与可观测性

1. 为上述实际故障写回归测试：模拟 efinance EastMoney 连接关闭、AkShare-EM 异常、Sina 阻塞。
2. 在日志中统一加入 `trace_id`、`component=market_review`、`stage`、`provider`、`upstream`、`elapsed_ms`、`remaining_ms`、`outcome`。
3. 给市场复盘 payload 增加 `data_quality`、`market_context_status`、`source_attempts`、`as_of`、`warnings`。

### 阶段 B：限时 provider 执行与熔断

1. 实现独立的、有界市场数据调用器和共享 deadline。
2. 改造 `get_market_stats`，使 provider 顺序可配置、调用限时、按 upstream 熔断。
3. 改造行业/概念排行使用相同运行上下文，确保 EastMoney 失败只记录一次后续跳过。
4. 改造 `MarketOverview`，避免默认零值掩盖不可用状态。

### 阶段 C：API 与编排语义

1. 实现严格/optional/disabled 三种市场上下文策略。
2. 让市场复盘 API 在时间预算内给出终态和机器可读错误码。
3. 增加取消 API 与队列协作检查点。
4. 更新 API schema、前端任务面板/运行流显示和 `.env.example`。

### 阶段 D：真实环境验收

1. 在不发送通知的前提下，执行一次 `POST /api/v1/analysis/market-review`。
2. 若实时源仍不可用，验证其在 25 秒内失败，且错误明确说明数据源与下一步操作。
3. 配置并验证一个真实可用的非 EastMoney provider 后，再运行一次市场复盘；验收“任务完成 + 报告落盘 + payload 为 fresh + as_of 为目标交易日”。
4. 最后以 `notify=false` 提交 605117 单股探针，验收终态、报告落盘、无通知证据；只在市场复盘为合格 fresh 时允许跨项目编排继续。

## 6. 必须通过的测试

1. EastMoney `RemoteDisconnected`：严格市场复盘任务在总预算（建议 25 秒）内 `failed`，错误码正确，任务不再停留 `processing`。
2. efinance 超时 + AkShare-EM 超时：EastMoney 熔断后不重复尝试同一 upstream；后台受控线程数不超过配置上限。
3. Sina 成功：返回 `fresh` 的统计与真实 `as_of`，报告显示正确涨跌家数和成交额。
4. 空数据：返回 `unavailable`，数值字段不被伪装为零。
5. 过期缓存：严格模式失败；optional 模式只能以 `stale`/`partial` 返回并标注真实日期。
6. 单股 optional：市场上下文不可用时任务可以完成，但结果与 Markdown 明确标注限制；`notify=false` 无通知调用。
7. 单股 required 与跨项目编排：市场复盘失败后，不提交任何候选个股、不生成总汇、不推送。
8. 取消：`POST .../cancel` 后任务在下一个安全检查点转为 `cancelled`，不产生完成报告。
9. 回归：现有 TickFlow fallback、市场复盘锁、API 合同、任务队列和前端任务面板测试仍通过。

## 7. 交付物与验收证据

执行 Agent 必须交付：

1. 修改清单（文件、配置、接口和兼容性说明）；
2. 新增/更新的单元测试和测试命令输出；
3. 一份脱敏的真实运行日志，包含每个 provider 的耗时、upstream、熔断/跳过原因与终态；
4. 一次真实市场复盘任务的 ID、状态、报告路径、结构化 payload 摘要；
5. 若数据源仍失败：在预算内失败的证明，以及用户需要配置或恢复的具体 provider；
6. 明确声明没有发送通知、没有执行交易，也没有把缓存写为当日实时数据。

## 8. 非目标

- 不修改 stock 的抓取逻辑，不通过 stock 数据库伪造 DSA 实时统计；
- 不把 EastMoney 绕过措施视为数据正确性证明；
- 不开启工作日自动调度、批量候选分析或飞书推送；
- 不增加自动交易、下单、仓位控制。
