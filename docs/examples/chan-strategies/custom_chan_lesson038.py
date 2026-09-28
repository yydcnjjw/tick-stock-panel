"""第38课操作思路的日线辅助近似, 不实现严格同级别走势类型分解。"""

import polars as pl

META = {
    "id": "custom_chan_lesson038",
    "name": "第38课 · chan.py 日线确认买卖",
    "description": "沪深主板: 笔级原生 1/2 类买点确认后入场, 1/2 类卖点确认后退出; 卖出优先, 两侧均次交易日开盘成交。第38课辅助近似, 不含完整同级别分解。",
    "tags": ["chan.py", "第38课", "日线", "确认信号", "沪深主板"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [],
    "scoring": {},
    "limit": 100,
}

EXECUTION_BACKEND = "polars_expr"
BASIC_FILTER = {"enabled": False}
ENTRY_SIGNALS = ["signal_chan_bi_1_buy", "signal_chan_bi_2_buy"]
EXIT_SIGNALS = ["signal_chan_bi_1_sell", "signal_chan_bi_2_sell"]
REQUIRED_FEATURES = {"signal_chan_bi_1_sell", "signal_chan_bi_2_sell"}
STOP_LOSS = None
MAX_HOLD_DAYS = None

RULES = """
1. 仅沪深主板股票(含ST), 不额外限制价格、市值、成交额或上市天数。
2. 日线笔级原生 1 类或 2 类买点任一进入保留区后首次确认时触发买入。
3. 日线笔级原生 1 类或 2 类卖点任一进入保留区后首次确认时触发卖出;
   同日买卖冲突不新买入, 卖出信号仍独立生效。
4. 回测选择仓位模拟(position): 持仓不加仓, 卖出后空仓等待新买信号;
   仅用已收盘日线, 买卖均次交易日开盘, 保留T+1、费用及不可成交约束。
5. 不附加固定止损、止盈或最大持有天数; 回测结束平仓属于end, 不是卖点。
6. 同一买卖点只在确认日触发一次, 不可计算不生成该信号, 首次就绪只建基线。
7. 本策略不逐段比较走势高点或盘整背驰, 不等同于原文完整算法;
   不支持盘中监控, 策略扫描本身不感知账户持仓。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    main_board = pl.col("symbol").str.contains(r"^(60[0-9]{4}\.SH|00[0-9]{4}\.SZ)$")
    # 候选过滤只约束入场; 卖出仍由 EXIT_SIGNALS 独立计算。
    sell_event = (
        pl.col("signal_chan_bi_1_sell").fill_null(False)
        | pl.col("signal_chan_bi_2_sell").fill_null(False)
    )
    return main_board & ~sell_event
