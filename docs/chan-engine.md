# chan.py 结构与确认事件

本项目用 [Vespa314/chan.py](https://github.com/Vespa314/chan.py/tree/429d6ed3043e27c93a003ba2b10e70a05575e1f5) 替换 CZSC 计算引擎。算法确认属于固定版本的工程规则，不等同于《教你炒股票》的严格递归买卖点判定。

## 来源和固定参数

- 上游 commit：`429d6ed3043e27c93a003ba2b10e70a05575e1f5`，MIT 许可证。
- 计算核心放在 `backend/app/vendor/chanpy`，保留 LICENSE；`UPSTREAM.json` 记录每个源文件及命名空间转换后的 SHA256。只修改内部导入路径，未修改算法；不包含上游网络数据源或画图库。
- 引擎内置，无需安装 CZSC。`czsc` extra 暂保留为空，兼容旧部署命令。使用正常的 `uv sync --frozen` 安装。
- 完整生效参数见 [chan_runtime.py](../backend/app/indicators/chan_runtime.py) 的 `CONFIG`。响应携带 `engine`、完整 `version`、`profile_id`、`algorithm_config`；缓存也包含这些计算身份。
- 固定原生笔规则 `normal/strict`、分型检查 `strict`、线段 `chan`、中枢 `normal`，关闭单笔中枢，MACD 为 12/26/9。保留原生笔级 `peak`、段级 `slope` 力度指标。`divergence_rate` 从上游默认无穷大改为 **1.0**，比较允许 `out_metric <= in_metric`，相等也通过，不能写成“严格衰减”。

## 确认时间与交易时间

每根有效且已收盘的 K 线按时间顺序送入原生引擎。买卖点候选可以延伸、改变类型或消失；图中的末端结构和候选仅代表当前快照。

项目确认规则：本次更新后，买卖点仍存在，且其原始 K 线端点索引不大于该层 `BSPointList.last_sure_pos`。这个边界来自最近已确认线段最后一笔的**起点**，不是最近已确认笔的终点。笔层、段层分别使用自己的边界。买卖点本身没有独立的 `is_sure` 字段，不能拿笔的 `is_sure` 直接冒充点确认。

每点保留三个时间：

| 字段 | 含义 |
| --- | --- |
| `endpoint_at` | 结构端点所在 K 线 |
| `first_seen_at` | 本次输入范围内逐根回放首次见到该点的 K 线 |
| `confirmed_at` | 首次满足本项目确认规则的 K 线 |

事件归属于 `confirmed_at`，不回填端点。点身份为标的、周期、连续有效输入段、笔/段层、端点和买卖方向；同一点只发布一次，多个原生类型可同日分别触发。两个不同点在相邻日期确认时都可触发，不做旧 CZSC 的布尔 False→True 门控。

每层第一次获得可计算确认边界时只建立基线，不把当时保留区中的旧点当新事件。历史不足输出 `null / insufficient_structure`；基线为 `null / baseline`；有效且无新事件才是 `false`。坏 OHLC、缺值等会中断连续计算并重新预热，不能跨缺口拼结构。确认边界回退，或已确认点消失、价格/端点/类型变化，立即报错停止发布，不降级成无信号。

此保证限定于**相同输入起点、相同已发布数据和相同参数**的逐根回放。图表窗口目标 1000 根，策略预热目标 500 根，不保证都形成可计算结构；改变起点或修订历史可能改变结果。图表与回测不同预热范围的事件不能直接混用。历史不足不静默缩短用户指定区间或股票池。

策略首版只用股票**日线笔级**确认事件。入场、出场均须选择次交易日开盘，保留原有 T+1、费用、滑点、停牌和不可成交约束。事件不保证成交；不支持盘中监控、跨周期联合触发或指数/ETF 交易。

## 新信号和模板

完整 ID 为 `signal_chan_bi_<类型>_<buy|sell>`，共 12 个，独立于旧 CZSC ID。

| 原生类型 | 含义 | 默认模板 |
| --- | --- | --- |
| `1` | 一类 | 一买/一卖配对 |
| `1p` | 盘整背驰 | 单独可选，不并入 1 |
| `2` | 二类 | 二买/二卖配对 |
| `2s` | 类二 | 单独可选，不并入 2 |
| `3a`、`3b` | 两种三类 | 三买/三卖中同侧 OR 组合 |

模板通过现有“新建自定义策略”代码编辑器保存；新 ID 不覆盖旧源码或旧历史。仓库不会在启动时自动安装模板。

- [一类配对](examples/chan-strategies/custom_chan_first_bs.py)
- [二类配对](examples/chan-strategies/custom_chan_second_bs.py)
- [三类配对](examples/chan-strategies/custom_chan_third_bs.py)
- [第38课日线近似](examples/chan-strategies/custom_chan_lesson038.py)：沪深主板，1/2 类同侧 OR，卖出优先，保留原先账户约束；仍不是完整同级别走势分解。

旧 `signal_czsc_*` / `czsc_*` 声明保留历史标签，但执行停用。策略列表和详情返回 `execution_available=false` 及原因；原始 `REQUIRED_FEATURES/ENTRY_SIGNALS/EXIT_SIGNALS` 与有效覆盖配置都检查，清空 UI 选择不能重启旧策略。默认批量扫描跳过已停用策略，显式执行会报告原因。原配置、源码、历史回测和历史缓存不重写、不迁移成新算法结果。

## 图表与兼容入口

- 股票：1m、5m、30m、1d、1w；指数：仅 1d、1w，分钟明确返回 `unsupported_timeframe`。
- 图层：分型、笔、线段、笔中枢、线段中枢、买卖点端点（含候选）、未完成结构、确认事件。笔和段的原生完成状态与点的项目确认状态分别表达。段层事件只用于解释，不加入策略选择器。
- 页面 `/czsc`、API `/api/kline/czsc`、日线兼容接口 `/api/kline/czsc-daily` 保留。新增 `segments`、`segment_centers`、`bsp_points`；`signals` 为确认事件并携带三个时间与唯一 ID。
- `czsc_minute` 能力 ID、`czsc_minute_data_provider` 偏好、`kline_czsc_minute/<provider>` 分区及结果字段 `czsc_coverage` 暂留为兼容名字。新 coverage 的 `engine` 为 `chan.py`；旧历史未提供 engine 时仍显示 CZSC，不能混标。
- 复用 KlineRepository 和现有发布批次校验。行情输入仍是本地已发布数据，图表刷新不抓远端；分钟按已配置来源隔离，不能借普通分钟源隐式补齐。
- 输入/图表成交量为手，原生计算边界乘 100 转为股；金额保持元。收盘时间按北京时间；午休分段聚合，09:30 竞价并入首根。周线保守等到周五 15:00。指数保持指数点位，不复权。

## 验证

定向测试包含原生与适配结构对照、固定参数和供应链哈希、单位映射、逐前缀事件稳定性、相邻新点触发、已确认点异常 fail closed、坏数据/未收盘 K 线、基线、并行/取消、成交时间、旧策略停用、发布批次与缓存、股票/指数/分钟路由及前端切换。

三种固定合成样本各 3000 根覆盖笔、段两层全部六类点，逐根检查已发布事件不变；这是有限样本的回归证据，不是所有行情下不重绘的数学证明。实际验收记录见 [迁移验证](chan-migration-validation.md)。
