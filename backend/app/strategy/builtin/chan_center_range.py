"""Fill-aware daily range/fractal strategy, with an explicit PIT data gate."""
import numpy as np

from app.backtest.chan_center import (
    CENTER_RULE_SETS,
    EXPLORATORY_WARNING,
    FORMAL_BLOCK_REASON,
    LIVE_BLOCK_REASON,
    CenterPolicy,
    build_center_features,
    center_rule_details,
)
from app.backtest.matrix import make_signal_matrix

META = {
    "id": "chan_center_range",
    "name": "中枢震荡: 分型 + 123买卖点",
    "description": "日线宽中枢分型与123策略, 支持成交风控、中枢结构及第24课止损实验。各方案保留独立规则与结果口径。",
    "tags": ["chan.py", "日线", "中枢震荡"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "params": [{"id": "exploratory", "label": "探索性回测(历史资格未核验)",
                "type": "bool", "default": False},
               {"id": "rule_set", "label": "规则方案", "type": "select",
                "options": list(CENTER_RULE_SETS), "default": "完整新版",
                "option_details": center_rule_details()}],
    "scoring": {}, "order_by": "score", "descending": True, "limit": 100,
    "backtest_only": True,
    "backtest_block_reason": FORMAL_BLOCK_REASON,
    "backtest_exploratory_param": "exploratory",
    "backtest_exploratory_warning": EXPLORATORY_WARNING,
    "live_block_reason": LIVE_BLOCK_REASON,
}
EXECUTION_BACKEND = "matrix_native"
BASIC_FILTER = {"enabled": False}
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = None
RULES = """
沪深主板历史逐日动态筛选; 最近20个市场交易日内的最新中枢宽度至少10%, 初始3笔已完成。
下部20%底分型、上部20%顶分型在右侧K线收盘后触发; 实际成交后固定信号时的中枢上下沿。
新中枢或边界变化后转123; 只用切换后端点随后确认的1、2、3a/3b事件, 不证明同中枢归属。
123允许中枢上方买入; 不加仓; 清仓后重新筛选, 必须形成新分型才能重复入场。
收盘跌破固定下沿优先退出; 无持有期限。买单仅下一个市场交易日开盘有效, 卖出受阻保留。
算法为日线笔级辅助结构。历史资格数据未接入, 完整历史股票池收益回测阻断。
显式启用探索性回测后, 使用现有历史行情中的主板股票, 不按当前名称排除历史ST, 记录样本限制。
完整新版: 区间实际开盘高于下沿、位置不超过40%、下沿风险不超过5%; 上部20%及上方顶分型退出。
两阶段均按含双边成本的1%权益计划风险定仓, 每股最多50%, 100股整手, 余款留现金。
123阶段不套区间位置/5%门槛, 但成交价必须高于参考下沿。计划风险不保证实际最大损失。
未携带规则方案的旧请求沿用原版。原五方案不改变原生结构或宽度降序。
中枢结构版: 最新中枢上下沿均不低于上一个不同起点的软件中枢; 前中枢未知则不入场。
始终采用区间底分型入场, 不因中枢改边转123入场; 持仓遇上部及上方顶分型或新确认卖点退出。
按信号收盘的(ZG-C)/max(C-ZD,0.01*C)降序, 固定下沿止损; 实际成交须高于ZD, 不套40%/5%门槛。
结构版计划风险1%, 结构保守版0.5%。中枢位置不证明严格上涨趋势, 探索性历史结果不代表未来盈利。
第24课止损实验版: 同一软件中枢的入笔A、出笔C均为原生完成态下降笔, C低于A低点和ZD,
完整C绿柱面积小于A; 仅首次可观察日收盘已回到中枢且首次反弹尚未完成时产生买单。
成交仍须在(ZD,ZG]内。冻结C低点, 按到该点的含成本1%风险定仓, 不下移风险线。
有有效证据时允许收盘低于ZD但高于C低点; 首次反弹完成且高点<ZD退出, 等于ZD按未知恢复下沿风控。
同中枢新C证据须晚于旧C且低点不低于冻结风险线, 才能重新观察其首次反弹。
证据撤销、数据缺口恢复下沿风控; 收盘<=冻结C低点优先退出。原生is_sure可修订, 不称严格三卖。
"""


class CenterRangeStrategy:
    def required_fields(self):
        return frozenset({"open", "high", "low", "close", "volume", "amount"})

    def required_warmup_bars(self, params):
        return 1000

    def compute_signals(self, market, params):
        policy = CenterPolicy.for_rule_set(params.get("rule_set", "原版"))
        features = build_center_features(market, lesson24=policy.lesson24_stop)
        cells = features.values
        low, high = cells["low"], cells["high"]
        bottom = ((cells["bottom"] >= low)
                  & (cells["bottom"] <= low + (high - low) * .2))
        # Potential entries only; phase and real holdings are resolved by matcher.
        entry = cells["eligible"] & (bottom | (cells["buy_at"] > 0))
        if policy.lesson24_stop:
            entry = np.zeros(market.shape, dtype=bool)
            day_index = {int(day): t for t, day in enumerate(features.days)}
            for (day, asset), observation in features.lesson24.observations.items():
                if observation.divergences:
                    entry[day_index[day], asset] = cells["eligible"][day_index[day], asset]
        score = np.zeros(market.shape, dtype=np.float32)
        np.divide(high - low, low, out=score, where=low > 0)
        return make_signal_matrix(market.shape, entry=entry, score=score,
                                  center_features=features)


MATRIX_STRATEGY = CenterRangeStrategy()
