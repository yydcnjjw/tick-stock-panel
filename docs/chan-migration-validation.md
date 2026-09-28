# chan.py 迁移验证记录

日期：2026-09-24。计算版本 `429d6ed3043e27c93a003ba2b10e70a05575e1f5`，参数身份 `chan-v1-2a9f8b7ac4153d87`。先完成源码及隔离预览验证，随后按用户“重启”要求部署，见末尾记录。

## 自动验证

后端以下 21 个测试文件合计 **201 passed**：

```bash
cd backend
uv run --no-sync pytest \
  tests/test_chan_retirement.py tests/test_chan_runtime.py \
  tests/test_czsc_signals.py tests/test_czsc_chart.py tests/test_czsc_index.py \
  tests/test_czsc_periods.py tests/test_czsc_minute_source.py tests/test_czsc_lesson038.py \
  tests/test_czsc_replay_parallel.py tests/test_czsc_monitor.py \
  tests/backtest/test_czsc_preparation.py tests/backtest/test_czsc_worker_cleanup.py \
  tests/test_strategy_detail_signals.py tests/test_strategy_required_history.py \
  tests/test_strategy_code_save.py tests/test_screener_run_all_as_of.py \
  tests/test_screener_run_all_progressive.py tests/test_capability_matrix.py \
  tests/backtest/test_strategy_backtest_correctness.py \
  tests/test_strategy_monitor_events.py tests/test_strategy_run_all_parallel.py -q
```

随后加强了独立调用原生 `CChan` 的结构对照，图表文件再次 **14 passed**。旧 `test_czsc_*` 文件名保留，其中调用已改为新引擎，兼容入口的测试继续覆盖。

前端以下五个文件合计 **35 passed**，`pnpm build` 通过：

```bash
cd frontend
pnpm exec vitest run src/components/chartStructures.test.ts \
  src/components/screener/SignalPicker.test.tsx src/custom/czsc/CzscPage.test.tsx \
  src/pages/Indices.test.tsx src/lib/backtestTask.test.ts
pnpm build
```

新引擎、适配器、图表服务及对应新测试的 Ruff 检查通过。扩展到既有 API/策略文件的检查仍报告原有格式、注释标点和 lint 问题；与修改前文件对照，本次新增行未增加该批问题。未为迁移批量重排无关代码。`git diff --check` 通过。

`uv build --wheel --out-dir /tmp/tickflow-chan-wheel --offline` 通过；检查 wheel 中 46 个上游文件的 SHA256、MIT LICENSE 和清单均完整，包元数据不再依赖 CZSC。锁文件去除 CZSC 及其独占依赖，其余保留包版本未变化。

## 本地真实行情

隔离 GET-only FastAPI 预览挂载当前 K 线 API，使用现有 KlineRepository 只读已发布分区；不启动同步、监控或调度器，不实例化会执行迁移的生产 DataStore。前端为本次构建的 dist。每个响应核验 `engine=chan.py` 和上述参数身份。

| 标的 | 周期 | 实际截止 | 输入根数 | 已完成笔 | 线段（含未完成） | 确认事件记录 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 600276.SH | 1m | 2026-09-23 15:00 | 1000 | 36 | 5 | 0 |
| 600276.SH | 5m | 2026-09-23 15:00 | 1000 | 34 | 6 | 0 |
| 600276.SH | 30m | 2026-09-23 15:00 | 280 | 16 | 4 | 0 |
| 600276.SH | 1d | 2026-09-23 | 1000 | 54 | 10 | 2 |
| 600276.SH | 1w | 2026-09-18 | 1000 | 45 | 7 | 4 |
| 000300.SH | 1d | 2026-09-22 | 1000 | 57 | 10 | 11 |
| 000300.SH | 1w | 2026-09-18 | 1000 | 49 | 10 | 2 |

分钟来源为当前 `exchange_minute`；30m 仅 280 根，页面明确显示覆盖不足。指数日线实际截止落后股票一天，没有补造数据。指数 30m 返回 `unavailable / unsupported_timeframe`。事件按点的原生子类型展开计数；记录为 0 不代表原文定义下没有买卖点，也不证明覆盖充分。本次不做收益测算。

## 页面联调

使用本地 Chromium 实际渲染：

- 桌面暗色和 390px 移动端浅色，图表正常绘制，移动端页面无横向溢出。
- 日线切换 30m、指数周线；分钟不足提示出现，指数分钟按钮禁用。
- 线段和买卖点端点图层开关不新增行情请求。
- 事件列表分别显示端点、首见、确认时间；确认记录在确认 K 线上。
- 注入刷新 HTTP 503 后展示错误并保留已有图表；正常浏览过程无 pageerror。

UI 自动测试另覆盖加载、空数据、不可用、乱序响应、切股、切来源、分钟同步和新旧信号选择。真实预览为只读，没有点击数据同步或执行真实策略。

## 部署核验

2026-09-24 09:03（北京时间）按用户要求执行 `docker compose build app` 和 `docker compose up -d --no-build --no-deps app`。运行镜像为 `sha256:3b958fd129a18975784440db57d28b6fb61e9901e9efec524e333b57e2d0470e`；旧镜像保留为 `tickflow-stock-panel-app:rollback-d134ad620a50`。

- 切换前在新镜像 Python 3.11.16 中检查全部后端语法、chan.py 导入和原生 K 线加载，均通过。
- 切换后健康检查 `ok`、容器无重启；268 个后端源文件及 94 个前端产物哈希与工作区一致。
- 股票日线/30m、指数周线实际接口返回新引擎版本及参数身份；指数分钟明确拒绝。新信号组件可用，旧 CZSC 组件不可用。
- 四个原有 CZSC 策略均可查看并显示停用，策略源文件哈希保持不变。
- 实际服务的 Chromium 页面验证通过：图表、候选/确认记录、周期切换、图层不重取、指数分钟禁用；未出现 pageerror。
- 启动日志未见 ERROR/Traceback；原有完整性检查发现 9 月 23 日两处日线分区缺失并自动启动后台修复，另有免费行情源不支持实时行情的能力提示。重启核验时修复仍在运行，不将服务可用解释为全市场数据已修复完整。

## 运行时回测策略安装

2026-09-24 09:11（北京时间）按用户“回测使用的策略，也更新为 chan.py”要求，四份模板先经运行服务 `/api/strategies/code/validate` 验证，再通过 `/api/strategies/code/save` 以 `create/custom` 保存并热加载：

- `custom_chan_first_bs`：笔级 1 类买卖配对。
- `custom_chan_second_bs`：笔级 2 类买卖配对，不混入 2s。
- `custom_chan_third_bs`：笔级 3a/3b 同侧 OR。
- `custom_chan_lesson038`：笔级 1/2 同侧 OR，沪深主板，卖出优先。

策略详情与源文件回读一致，无加载错误；运行容器中的回测依赖解析全部通过，预热目标均为 500 根。原四份 CZSC 源文件哈希未变化，继续停用并保留历史。

实际浏览器 `/backtest` 的“自定义”分类能选择四个新策略，建仓/清仓均为 `open_t+1`，“运行回测”按钮可用，pageerror 为零。此次验证只确认安装、依赖与页面可用，没有启动收益回测或生成新的收益结论。
