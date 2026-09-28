"""chan.py 一买/一卖确认信号的日线回测模板。"""

import polars as pl

META = {
    "id": "custom_chan_first_bs",
    "name": "chan.py 一买/一卖确认",
    "description": "笔级原生 1 类确认信号配对: 一买进入保留区后首次确认入场, 一卖进入保留区后首次确认出场; 两侧均使用次交易日开盘成交。",
    "tags": ["chan.py", "日线", "确认信号"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [],
    "scoring": {},
    "limit": 100,
}

EXECUTION_BACKEND = "polars_expr"
BASIC_FILTER = {"enabled": False}
ENTRY_SIGNALS = ["signal_chan_bi_1_buy"]
EXIT_SIGNALS = ["signal_chan_bi_1_sell"]
STOP_LOSS = None
MAX_HOLD_DAYS = None

RULES = """
1. 全部股票作为候选, 通过买卖触发器决定入场和出场。
2. 仅处理已收盘日线; 首次可计算只建基线, 同一买卖点不重复触发。
3. 建仓和清仓均选择次交易日开盘; 不另加止损或持有天数退出。
4. 算法辅助判定不等于严格缠论买卖点确认; 不支持盘中监控。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
