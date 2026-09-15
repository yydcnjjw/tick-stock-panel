"""CZSC 一买/一卖辅助信号的日线回测模板。"""

import polars as pl

META = {
    "id": "custom_czsc_first_bs",
    "name": "CZSC 一买/一卖辅助",
    "description": "V221126 原生辅助信号配对: 一买首次成立入场, 一卖首次成立出场; 两侧均使用次交易日开盘成交。",
    "tags": ["CZSC", "日线", "辅助信号"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [],
    "scoring": {},
    "limit": 100,
}

EXECUTION_BACKEND = "polars_expr"
BASIC_FILTER = {"enabled": False}
ENTRY_SIGNALS = ["signal_czsc_first_buy"]
EXIT_SIGNALS = ["signal_czsc_first_sell"]
STOP_LOSS = None
MAX_HOLD_DAYS = None

RULES = """
1. 全部股票作为候选, 通过买卖触发器决定入场和出场。
2. 仅处理已收盘日线; 首次可计算只建基线, 持续成立不重复触发。
3. 建仓和清仓均选择次交易日开盘; 不另加止损或持有天数退出。
4. 算法辅助判定不等于严格缠论买卖点确认; 不支持盘中监控。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
