# 项目数据与分组契约

核对日期：2026-09-24。下列是当前源码契约；运行时确认服务地址及响应，升级后按链接复核。不要固化本机端口、分组 ID 或任何当前成员名单。

## 数据入口

| 用途 | 已有入口 | 限制 |
| --- | --- | --- |
| 数据状态 | `GET /api/data/status` | 检查实际截止、同步与覆盖状态；请求成功不证明全市场齐全 |
| 数据能力 | `GET /api/settings/capability-matrix` | 按实际 provider 能力读取；不能假设分钟数据一定存在 |
| 市场轻量快照 | `GET /api/screener/market-snapshot` | 返回 `as_of`、`rows`；仅含当日可用行情，不是完整证券主表或停牌／退市状态表 |
| 条件初筛 | `POST /api/screener/run` | 请求有 `conditions`、`pool`、`as_of`、`limit`；默认 `limit=30`，不能据默认结果宣称全量扫描 |
| 结构图 | `GET /api/kline/czsc?symbol=...&timeframe=30m&asset_type=stock` | 股票支持 1m/5m/30m/1d/1w，指数仅 1d/1w；不支持历史截止参数；`czsc-daily` 保留为日线别名 |
| 本地分钟线 | `GET /api/kline/minute-range?symbol=...&days=20` | `days` 为最近已落库交易日数量，上限20；返回 `sessions[].date/rows` 和 `source`，空值不代表结构不成立 |

实现：[K线 API](../../../../backend/app/api/kline.py)、[筛选 API](../../../../backend/app/api/screener.py)、[日线图表服务](../../../../backend/app/services/czsc_chart.py)。

批量研究优先使用现有 [KlineRepository](../../../../backend/app/tickflow/repository.py) 读取标准化数据，避免对所有股票逐只请求完整结构图：

- `get_instruments()` / `get_instruments_asset("stock")`：证券主表。先核对真实字段、日期和状态；缺少 ST、退市或停牌证据时不能由低成交量臆断。
- `get_daily_batch(symbols, start, end, columns)`：批量日线。可能走最新缓存；调用后仍按请求区间、实际截止及收盘边界检查返回记录。不得把缓存中的盘中尾行当成已发布收盘。
- `get_daily_published(symbol, end, limit, columns)`：单股已发布 enriched 日线；前后读取 `get_matrix_data_generation("stock", readonly=True)` 校验批次一致。发布中或版本变化时不拼接不同批次。
- `get_minute_range(symbols, start, end, asset_type="stock")`：批量本地分钟线；HTTP 的20日上限不是此仓库方法的上限，但实际本地历史仍可能不足。当前方法在读失败时也可能返回空表，要结合错误和覆盖检查处理。

不要绕过仓库直接散读 Parquet、从供应商原始字段拼第二条计算链，或为一次选股改 provider 配置。股票池资格与行情覆盖分别统计；代码前缀、名称、实时价格都不能代替缺失的全部资格信息。

## chan.py 与30分钟复核

当前源码采用 [chan.py 固定版本与确认契约](../../../../docs/chan-engine.md)。运行中的服务可能尚未升级，必须从响应的 `engine/version/profile_id/algorithm_config` 核对，不能因为 URL 仍叫 `czsc` 就认定计算引擎。若返回旧 CZSC 1.0.1，报告旧口径；不要擅自更换运行中的依赖。

图表返回 `status`（`ready/empty/unavailable`）、`cutoff`、输入范围、结构范围、数据版本及笔/线段/两层中枢。目标1000根输入不保证结构充分。数据已发布不等于供应商逐根最终定版；核对实际来源与价格口径。

新日线信号 ID 为 `signal_chan_bi_<类型>_<buy|sell>`，类型分别为 `1、1p、2、2s、3a、3b`；盘整背驰 1p 和类二 2s 独立，不自动混入 1/2。旧 `signal_czsc_*` 执行停用但历史保留。完整定义见 [chan_signals.py](../../../../backend/app/indicators/chan_signals.py)。

事件在原生买卖点进入保留边界的确认 K 线上产生，同一点只触发一次，首次就绪只建基线。记录 `endpoint_at/first_seen_at/confirmed_at`，不能把端点时间当确认时间。当前无事件不等于结构失效，候选可能改变；算法不保证覆盖原文所有分支，也不能作为唯一候选入口。笔中枢、线段中枢均不自动证明严格递归中枢。

30分钟可使用现有结构接口，但须核对响应 `minute_provider`；`czsc_minute` 独立路由及 `kline_czsc_minute/<provider>` 与普通分钟隔离。不要从普通分钟缓存偷偷补齐本来源。完整常规交易日的30分钟收盘时点为北京时间 `10:00、10:30、11:00、11:30、13:30、14:00、14:30、15:00`，午休不跨段聚合。先确认源分钟时间戳表示开始还是结束，处理集合竞价与重复记录，不能仅按行数分组。历史截止研究须通过仓库读取相同来源并裁剪已结束 K 线，再用对应周期适配器计算，不能把日线函数直接当分钟适配器。

核对有效交易日的覆盖、OHLC 聚合与同口径日线是否一致；来源价格口径不同需先建立可验证的转换，不能直接拼接。分钟成交量和金额单位以该数据入口为准，不套用日线的换算。无效缺口不补零或造 K；20个交易日也可能不足以覆盖待验证结构，标 `unknown`。本地不足时只使用当前已配置且确实可用的 provider 能力补充；补不到就报告边界，不静默缩短所需结构。

## 三个组的操作

目标名称准确为「一买」「二买」「三买」。每次执行先读取：

```text
GET /api/watchlist/groups   -> {"groups": [{"id", "name", ...}]}
GET /api/watchlist          -> {"symbols": [{"symbol", "group_ids", "note", "added_at", ...}]}
```

按名称解析 ID。缺失组使用 `POST /api/watchlist/groups`，JSON 为 `{"name":"一买"}`（其他名称同理），然后重新读取。若同名多组或接口返回与预期不符，停止有歧义的写入并报告，不选第一个猜测。既有「缠论」组与其他组均不迁移、不清空。

根据各类型判定计算差集：

```text
old[g]    = 更新前属于目标组 g 的股票
pass[g]   = 本次该类型复核通过的股票
fail[g]   = 本次明确不符合该类型或已失效的股票
add[g]    = pass[g] - old[g]
remove[g] = old[g] ∩ fail[g]
expected[g] = (old[g] ∪ add[g]) - remove[g]
```

`old[g] - pass[g]` 不是删除清单；其中可能包含数据缺失、未扫描或仅在本轮用户指定子股票池以外的成员。同一股票的三种类型分别判断；从一买移出时不自动从二买、三买移出。资格已明确不符合当前规则时可记录对应排除依据。

| 操作 | API | 保护语义 |
| --- | --- | --- |
| 已在自选，加入目标组 | `POST /api/watchlist/groups/{group_id}/members/{symbol}` | 只追加该组；保留其他组、备注、时间 |
| 尚不在自选，新增并入首个目标组 | `POST /api/watchlist`，JSON `{"symbol":"...","group_id":"..."}` | 新增后若还符合别组，用成员接口追加 |
| 从某目标组移出 | `DELETE /api/watchlist/groups/{group_id}/members/{symbol}` | 只移除此标签，保留股票及其他组 |

实际调用链：[watchlist API](../../../../backend/app/api/watchlist.py) → [watchlist service](../../../../backend/app/services/watchlist.py)；相邻验证见 [多组测试](../../../../backend/tests/test_watchlist_groups.py)。

**不得用这些操作代替增删差集：**

- `PUT /api/watchlist/{symbol}/group`：互斥设组，会移除其他组。
- 对已有自选调用 `POST /api/watchlist` 或 `/batch`：会覆盖备注、加入时间并调整排序。
- `DELETE /api/watchlist/{symbol}`、清空整个自选、清空或删除分组：超出按类型更新成员的范围。
- 直接改自选持久化文件：绕过应用读写和状态管理。

## 写入与核验顺序

1. 完成分析后生成三组增删清单，保存更新前的分组归属及已有股票的备注、时间。用户已要求执行选股时，展示清单后可继续更新，不新增常规确认步骤。
2. 整体取数失败、股票池来源失效或结果批次混乱时不写。部分完成时仅处理已经可靠判定的股票，未知旧成员保留；没有任何新增但所有旧成员均明确失效时，可以移除这些成员，报告有效空结果。
3. 写前重新读自选，核对目标组状态；若相对分析起点已有并发变动，停止该次成员写入并报告差异，不覆盖他人的新操作。当前 API 没有批量事务或条件写入，二次读取也不能保证彻底消除竞争窗口。
4. 先新增并核验，再执行有明确依据的移除。新股票创建成功后，跨组只使用成员接口，不重复新增自选；若它已被其他操作加入自选，也改用成员接口。网络超时先回读判断操作是否已成功：确认成功就不重试，确认尚未完成才最多补试一次，无法确认则停止并报告。持续失败就停止后续写入并报告部分完成，不无限重试。
5. 最后重新读取分组和自选，比较实际成员与 `expected[g]`，并确认原有其他组、备注、时间仍保留。没有事务保障时不能宣称“三组原子更新”；出现部分成功时列实际成功、失败、未执行项，不用清空重建或全量回滚掩盖错误。

只有回读核对一致后才报告对应组已更新。创建了空组不等于已完成选股，也不能将待核验保留项算作本轮通过项。
