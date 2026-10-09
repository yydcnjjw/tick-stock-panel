"""Daily, as-of center observations and a fill-aware range/BSP state machine.

Only the portfolio matcher owns positions. Observations never simulate fills.
Dates in this payload are ordinals, so slicing a matrix cannot move an endpoint.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np

from app.backtest.chan_center_context import CenterContext, build_center_context
from app.backtest.chan_center_lesson24 import (
    DownsideEvidence,
    Lesson24Features,
    Lesson24Replay,
    Observation,
)
from app.indicators.chan_runtime import ChanReplay
from app.indicators.czsc_bars import bar_close_time
from app.market_time import cn_now

FORMAL_BLOCK_REASON = (
    "中枢震荡正式收益回测尚未开放: 当前历史行情未接入逐日上市、退市及 ST 资格数据, "
    "无法证明沪深主板历史股票池完整。不得用当前股票名单或观察分组替代; "
    "可显式启用探索性回测, 结果仅适用于现有行情样本, 不生成完整历史股票池的收益结论。"
)
EXPLORATORY_WARNING = (
    "探索性回测: 使用现有历史行情中的沪深主板股票逐日筛选, 默认不按当前名称排除历史 ST。"
    "历史股票池完整性、ST 与退市资格未核验; 涨跌停沿用现有引擎的历史规则和名称估计。"
    "收益仅描述该行情样本及成交假设, 可能存在样本选择和成交偏差。"
)
LIVE_BLOCK_REASON = "中枢震荡依赖实际成交后的持仓状态, 仅支持回测, 不支持普通选股或监控"
BSP_TYPES = ("1", "2", "3a", "3b")
CENTER_RULE_SETS = ("原版", "仅成交门槛", "仅扩大退出", "仅风险定仓", "完整新版", "中枢结构实验版", "中枢结构实验保守版", "第24课止损实验版")
CENTER_DTYPE = np.dtype([
    ("valid", "?"), ("center", "i4"), ("low", "f8"), ("high", "f8"),
    ("eligible", "?"), ("bottom", "f8"), ("top", "f8"),
    ("bottom_at", "i4"), ("top_at", "i4"),
    ("buy_at", "i4"), ("sell_at", "i4"), ("buy_code", "i2"), ("sell_code", "i2"),
])


@dataclass(frozen=True)
class CenterFeatures:
    values: np.ndarray
    days: np.ndarray
    context: CenterContext | None = None
    lesson24: Lesson24Features | None = None

    def validate(self, shape):
        if self.values.shape != shape or self.values.dtype != CENTER_DTYPE:
            raise ValueError("中枢观察矩阵维度或类型错误")
        if self.days.shape != (shape[0],) or np.any(np.diff(self.days) <= 0):
            raise ValueError("中枢观察日期必须严格递增")
        if self.context is not None:
            self.context.validate(shape)
        if self.lesson24 is not None:
            self.lesson24.validate(self.days, shape[1])

    def readonly(self):
        self.values.setflags(write=False)
        self.days.setflags(write=False)
        if self.context is not None:
            self.context.readonly()
        return CenterFeatures(self.values, self.days, self.context,
                              self.lesson24.readonly() if self.lesson24 is not None else None)

    def slice(self, start, stop):
        return CenterFeatures(self.values[start:stop], self.days[start:stop],
                              self.context.slice(start, stop) if self.context else None,
                              self.lesson24.slice(self.days[start:stop]) if self.lesson24 is not None else None)

    @property
    def nbytes(self):
        return (self.values.nbytes + self.days.nbytes + (self.context.nbytes if self.context else 0)
                + (self.lesson24.nbytes if self.lesson24 is not None else 0))


def build_center_features(market, *, now=None, lesson24=False) -> CenterFeatures:
    """Continuous chronological replay from the supplied warmup origin.

    Missing market-day rows reset structure, never bridge a missing right K-line.
    The latest center is chosen before qualification, without falling back to an
    older, wider center. Current instrument names are deliberately not PIT data.
    """
    cutoff = (now or cn_now()).replace(tzinfo=None)
    dates = [date.fromisoformat(label[:10]) for label in market.timestamp_labels]
    days = np.array([day.toordinal() for day in dates], dtype=np.int32)
    values = np.zeros(market.shape, dtype=CENTER_DTYPE)
    values["bottom"] = np.nan
    values["top"] = np.nan
    date_ids = {day: i for i, day in enumerate(dates)}
    amount = market.field("amount")
    observations = {}
    for a, symbol in enumerate(market.symbols):
        if not ((symbol.startswith("60") and symbol.endswith(".SH"))
                or (symbol.startswith("00") and symbol.endswith(".SZ"))):
            continue
        replay = None
        lesson_replay = None
        for t, day in enumerate(dates):
            numeric = [float(arr[t, a]) for arr in
                       (market.open, market.high, market.low, market.close, market.volume, amount)]
            op, high, low, close, volume, turnover = numeric
            if (bar_close_time(day, "1d") > cutoff
                    or not all(np.isfinite(v) and v > 0 for v in numeric)
                    or not low <= min(op, close) <= max(op, close) <= high):
                replay = None
                continue
            if replay is None:
                replay = ChanReplay(symbol, "1d")
                lesson_replay = Lesson24Replay() if lesson24 else None
            events = replay.update(dict(symbol=symbol, date=day, open=op, high=high,
                                        low=low, close=close, volume=volume, amount=turnover))
            cell = values[t, a]
            cell["valid"] = True
            if lesson_replay is not None:
                observation = lesson_replay.update(replay)
                if observation.divergences or observation.rebounds or observation.revoked:
                    observations[(int(days[t]), a)] = observation
            for event in replay.fractal_events:
                kind = event["kind"]
                cell[kind] = event["price"]
                cell[f"{kind}_at"] = date.fromisoformat(event["endpoint_at"]).toordinal()
            for event in events:
                types = [kind for kind in BSP_TYPES if kind in event["types"]]
                if not types:
                    continue
                side = "buy" if event["is_buy"] else "sell"
                endpoint = date.fromisoformat(event["endpoint_at"]).toordinal()
                if endpoint >= cell[f"{side}_at"]:
                    cell[f"{side}_at"] = endpoint
                    cell[f"{side}_code"] = BSP_TYPES.index(types[0]) + 1
            centers = [z for z in replay.level.zs_list if not z.is_one_bi_zs()]
            if not centers:
                continue
            center = max(centers, key=lambda z: (z.end.idx, z.begin.idx))
            cell["center"] = replay.rows[center.begin.idx]["date"].toordinal()
            cell["low"], cell["high"] = center.low, center.high
            first = center.begin_bi.idx
            formed = (center.end_bi.idx >= first + 2
                      and all(replay.level.bi_list[i].is_sure for i in range(first, first + 3)))
            end_id = date_ids[replay.rows[center.end.idx]["date"]]
            cell["eligible"] = (formed and 0 <= t - end_id < 20 and center.low > 0
                                and (center.high - center.low) / center.low >= 0.10)
    return CenterFeatures(values, days, build_center_context(market),
                          Lesson24Features(observations) if lesson24 else None).readonly()


@dataclass(frozen=True)
class CenterOrder:
    signal_id: str
    low: float = 0.0
    high: float = 0.0
    reason: str = "signal"
    atr: float = 0.0
    rank_score: float | None = None
    risk_low: float = 0.0
    evidence: DownsideEvidence | None = None
    exit_evidence: dict | None = None


@dataclass(frozen=True)
class CenterPolicy:
    """Fixed, reviewed execution experiments; they do not change Chan observations."""

    rule_set: str = "原版"
    entry_gate: bool = False
    extended_exit: bool = False
    risk_sizing: bool = False
    max_entry_position: float = .40
    max_entry_distance: float = .05
    risk_fraction: float = .01
    position_cap: float = .50
    entry_trend: str = "none"
    breadth_min: float = 0.0
    volume_min: float = 0.0
    ranking: str = "width"
    target_position: float | None = None
    max_hold_bars: int = 0
    trail_activate: float = 0.0
    trail_drawdown: float = 0.0
    close_loss_cap: float = 0.0
    cooldown_bars: int = 0
    risk_floor_pct: float = 0.0
    atr_risk_multiple: float = 0.0
    entry_structure: str = "legacy"
    exit_structure: str = "legacy"
    center_relation: str = "any"
    confirmation_bars: int = 20
    lesson24_stop: bool = False

    def __post_init__(self):
        if self.entry_structure not in ("legacy", "range", "bsp", "bsp1", "bsp2", "bsp3", "confirmed_pullback", "lesson24"):
            raise ValueError("未知中枢入场结构条件")
        if self.exit_structure not in ("legacy", "top", "bsp", "combined"):
            raise ValueError("未知中枢退出结构条件")
        if self.center_relation not in ("any", "non_lower", "above"):
            raise ValueError("未知前后中枢位置条件")
        if not isinstance(self.confirmation_bars, int) or self.confirmation_bars <= 0:
            raise ValueError("确认事件观察窗口必须为正整数")
        if self.entry_trend not in ("none", "ma20_rising", "above_ma60", "positive_momentum", "breakout_bar"):
            raise ValueError("未知中枢入场趋势条件")
        if self.ranking not in ("width", "reward_risk", "momentum", "low_volatility"):
            raise ValueError("未知中枢候选排序")
        for name in ("risk_fraction", "position_cap", "max_entry_position", "max_entry_distance"):
            value = getattr(self, name)
            if not np.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"中枢比例参数越界: {name}")
        for name in ("breadth_min", "trail_activate", "trail_drawdown", "close_loss_cap", "risk_floor_pct"):
            value = getattr(self, name)
            if not np.isfinite(value) or not 0 <= value < 1:
                raise ValueError(f"中枢比例参数越界: {name}")
        if bool(self.trail_activate) != bool(self.trail_drawdown):
            raise ValueError("移动退出需要同时设置启动和回撤比例")
        for name in ("volume_min", "atr_risk_multiple", "max_hold_bars", "cooldown_bars"):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"中枢参数越界: {name}")
        if self.target_position is not None and not 0 < self.target_position <= 1:
            raise ValueError("中枢退出位置越界")
        if self.lesson24_stop != (self.entry_structure == "lesson24") or (self.lesson24_stop and not self.risk_sizing):
            raise ValueError("第24课止损须配合证据入场和风险定仓")

    @property
    def needs_context(self):
        return (self.entry_trend != "none" or self.breadth_min > 0 or self.volume_min > 0
                or self.atr_risk_multiple > 0 or self.ranking in ("momentum", "low_volatility"))

    def context_allows(self, context, t, a, close):
        if not self.needs_context:
            return True
        if context is None:
            raise ValueError("中枢实验缺少历史上下文")
        trend = {
            "none": True,
            "ma20_rising": close >= context.ma20[t, a] >= context.ma20_prior5[t, a],
            "above_ma60": close >= context.ma60[t, a],
            "positive_momentum": context.momentum20[t, a] > 0,
            "breakout_bar": close > context.prior_high[t, a],
        }[self.entry_trend]
        return (trend and (not self.breadth_min or context.breadth60[t] >= self.breadth_min)
                and (not self.volume_min or context.volume_ratio[t, a] >= self.volume_min)
                and (not self.atr_risk_multiple or np.isfinite(context.atr14[t, a])))

    @classmethod
    def for_rule_set(cls, name: str) -> CenterPolicy:
        if name not in CENTER_RULE_SETS:
            raise ValueError(f"未知中枢震荡规则方案: {name}")
        if name == "第24课止损实验版":
            return cls(rule_set=name, extended_exit=True, risk_sizing=True,
                       entry_structure="lesson24", exit_structure="combined", lesson24_stop=True)
        if name in ("中枢结构实验版", "中枢结构实验保守版"):
            return cls(rule_set=name, extended_exit=True, risk_sizing=True,
                       entry_structure="range", exit_structure="combined", center_relation="non_lower",
                       ranking="reward_risk", risk_fraction=.005 if name == "中枢结构实验保守版" else .01)
        return cls(name, name in ("仅成交门槛", "完整新版"),
                   name in ("仅扩大退出", "完整新版"),
                   name in ("仅风险定仓", "完整新版"))

    def snapshot(self) -> dict:
        expanded = (self.needs_context or self.target_position is not None or self.max_hold_bars
                    or self.trail_activate or self.close_loss_cap or self.cooldown_bars or self.risk_floor_pct
                    or self.entry_structure != "legacy" or self.exit_structure != "legacy"
                    or self.center_relation != "any")
        return {"version": "center-execution-4" if self.lesson24_stop else "center-execution-3" if expanded else "center-execution-2", **asdict(self),
                "risk_basis": ("entry_to_frozen_c_low_including_both_costs" if self.lesson24_stop else
                               "max_frozen_lower_and_volatility_floor_including_both_costs"
                               if self.risk_floor_pct or self.atr_risk_multiple
                               else "entry_to_frozen_lower_including_both_costs"),
                "equity_basis": "post_exit_cash_plus_previous_closes"}

    def reject_entry(self, order: CenterOrder, price: float) -> str:
        if not (self.entry_gate or self.risk_sizing):
            return ""
        if price <= order.low:
            return "buy_center_lower"
        if self.lesson24_stop:
            if order.evidence is None or not 0 < order.risk_low < order.low:
                return "buy_center_risk"
            if price > order.high:
                return "buy_center_position"
        if self.entry_gate and order.signal_id == "center_bottom_buy":
            # Input prices are float32; tolerate only their rounding at inclusive bounds.
            position = (price - order.low) / (order.high - order.low)
            if position > self.max_entry_position + 1e-7:
                return "buy_center_position"
            if (price - order.low) / price > self.max_entry_distance + 1e-7:
                return "buy_center_distance"
        return ""


def center_rule_details():
    descriptions = {
        "原版": "下部20%底分型入场, 上部20%以内顶分型退出; 等权或评分加权。",
        "仅成交门槛": "开盘位置≤40%、到下沿跌幅≤5%; 退出与定仓沿用原版。",
        "仅扩大退出": "上部20%及上方顶分型退出; 入场与定仓沿用原版。",
        "仅风险定仓": "每笔计划风险1%、单只最多50%; 入场与退出沿用原版。",
        "完整新版": "区间成交位置≤40%、下沿距离≤5%; 上部及上方顶分型退出; 每笔计划风险1%。",
        "中枢结构实验版": "中枢上下沿均不低于前中枢, 下部底分型入场, 按区间剩余空间/下沿距离排序; 顶分型或确认卖点退出, 每笔计划风险1%。笔级辅助实验, 历史对照未通过, 仅供研究。",
        "中枢结构实验保守版": "与中枢结构实验版相同的入场、退出和排序, 每笔计划风险0.5%。保留固定下沿止损, 不套40%/5%成交门槛。笔级辅助实验, 历史对照未通过, 仅供研究。",
        "第24课止损实验版": "A/C完整绿柱面积减弱且C创新低, 笔完成事件首次可观察时回到中枢才入场。冻结C低点风控、计划风险1%; 破下沿观察首次反弹, 证据撤销则恢复下沿止损。笔级辅助实验, 未作收益验证。",
    }
    return {name: {"description": descriptions[name],
                   "risk_sizing": (policy := CenterPolicy.for_rule_set(name)).risk_sizing,
                   "risk_reference": "c_low" if policy.lesson24_stop else "center_lower",
                   "risk_fraction": policy.risk_fraction, "position_cap": policy.position_cap}
            for name in CENTER_RULE_SETS}


@dataclass
class CenterState:
    signature: tuple | None = None
    phase_since: int = 0
    frozen: tuple[float, float] | None = None
    last_exit: int = 0
    observed_center: tuple | None = None
    previous_center: tuple | None = None
    buy_confirmation: tuple[int, int] | None = None
    risk_low: float = 0.0
    downside: DownsideEvidence | None = None
    lesson_reference: tuple | None = None
    evidence_after: int = 0


@dataclass
class CenterBook:
    features: CenterFeatures
    states: dict[int, CenterState] = field(default_factory=dict)
    policy: CenterPolicy = field(default_factory=CenterPolicy)

    def __post_init__(self):
        if self.policy.needs_context and self.features.context is None:
            raise ValueError("中枢实验缺少历史上下文")
        if self.policy.lesson24_stop and self.features.lesson24 is None:
            raise ValueError("第24课实验缺少逐日结构证据; 请重新生成信号矩阵")

    def on_close(self, t: int, closes, positions):
        buys, sells = {}, {}
        day = int(self.features.days[t])
        empty_observation = Observation()
        for a, cell in enumerate(self.features.values[t]):
            state = self.states.setdefault(a, CenterState())
            held = a in positions
            close = float(closes[a])
            observation = (self.features.lesson24.observations.get((day, a), empty_observation)
                           if self.policy.lesson24_stop else empty_observation)
            # Risk uses the filled trade's reference even if new structure is unknown.
            stop = (held and state.frozen is not None and np.isfinite(close)
                    and close > 0 and close < state.frozen[0])
            lesson_sell = self._lesson24_exit(state, cell, observation, close, day) if held and self.policy.lesson24_stop else None
            if lesson_sell is not None:
                sells[a] = lesson_sell
            elif stop and not (self.policy.lesson24_stop and state.downside is not None):
                sells[a] = CenterOrder("center_lower_stop", reason="center_stop")
            elif held and np.isfinite(close) and close > 0:
                pos = positions[a]
                entry = pos.get("entry_price", 0)
                peak = pos.get("max_high", 0)
                if self.policy.close_loss_cap and close < entry * (1 - self.policy.close_loss_cap):
                    sells[a] = CenterOrder("center_loss_cap_sell")
                elif (self.policy.trail_activate and self.policy.trail_drawdown
                      and peak >= entry * (1 + self.policy.trail_activate)
                      and close < peak * (1 - self.policy.trail_drawdown)):
                    sells[a] = CenterOrder("center_trailing_sell")
                elif self.policy.max_hold_bars and pos.get("hold_days", 0) + 1 >= self.policy.max_hold_bars:
                    sells[a] = CenterOrder("center_time_sell")
            if not cell["valid"] or not cell["center"]:
                # An unknown structure cannot carry a prior confirmation across a data gap.
                state.observed_center = state.previous_center = state.buy_confirmation = None
                continue
            signature = (int(cell["center"]), float(cell["low"]), float(cell["high"]))
            if state.observed_center is not None and signature[0] != state.observed_center[0]:
                state.previous_center = state.observed_center
            state.observed_center = signature
            if cell["sell_at"]:
                state.buy_confirmation = None
            elif cell["buy_at"] > state.last_exit:
                state.buy_confirmation = (t, int(cell["buy_at"]))
            if state.signature is None:
                if not cell["eligible"]:
                    continue
                state.signature = signature
            elif signature != state.signature:
                state.signature = signature
                if not state.phase_since:
                    state.phase_since = day
            low, high = state.frozen if held and state.frozen else signature[1:]
            width = high - low
            if width <= 0:
                continue
            if state.phase_since:
                sell = cell["sell_at"] > state.phase_since
                buy = cell["buy_at"] > state.phase_since and close >= cell["low"]
                buy_id = self._bsp_id(cell, "buy")
                sell_id = self._bsp_id(cell, "sell")
            else:
                sell = (high - width * .2 <= cell["top"]
                        and (self.policy.extended_exit or cell["top"] <= high))
                buy = (low <= cell["bottom"] <= low + width * .2
                       and low <= close <= high
                       and cell["bottom_at"] > state.last_exit)
                buy_id, sell_id = "center_bottom_buy", "center_top_sell"
                if (self.policy.target_position is not None
                        and close >= low + width * self.policy.target_position):
                    sell, sell_id = True, "center_target_sell"
            if self.policy.entry_structure != "legacy":
                mode = self.policy.entry_structure
                evidence = None
                if mode == "lesson24":
                    evidence = next((e for e in reversed(observation.divergences)
                                     if e.matches(signature) and e.observed_at == day
                                     and state.last_exit < e.c_end < day and e.c_area < e.a_area
                                     and not any(r.start == e.c_end for r in observation.rebounds)), None)
                    buy = evidence is not None and low < close <= high
                    buy_id = "center_l24_divergence_buy"
                elif mode in ("range", "confirmed_pullback"):
                    buy = (low <= cell["bottom"] <= low + width * .2
                           and low <= close <= high and cell["bottom_at"] > state.last_exit)
                    buy_id = "center_bottom_buy"
                    if mode == "confirmed_pullback":
                        confirmation = state.buy_confirmation
                        buy = (buy and confirmation is not None
                               and 0 < t - confirmation[0] <= self.policy.confirmation_bars
                               and cell["bottom_at"] > confirmation[1])
                else:
                    codes = {"bsp": (1, 2, 3, 4), "bsp1": (1,), "bsp2": (2,), "bsp3": (3, 4)}[mode]
                    buy = (cell["buy_code"] in codes and cell["buy_at"] > state.last_exit
                           and close > cell["low"])
                    buy_id = self._bsp_id(cell, "buy")
            if self.policy.exit_structure != "legacy":
                top = high - width * .2 <= cell["top"]
                bsp = cell["sell_at"] > 0
                sell = ((self.policy.exit_structure in ("top", "combined") and top)
                        or (self.policy.exit_structure in ("bsp", "combined") and bsp))
                sell_id = self._bsp_id(cell, "sell") if bsp and self.policy.exit_structure != "top" else "center_top_sell"
            if self.policy.center_relation != "any":
                previous = state.previous_center
                relation = (previous is not None and (
                    signature[1] > previous[2] if self.policy.center_relation == "above"
                    else signature[1] >= previous[1] and signature[2] >= previous[2]))
                buy = buy and relation
            if held and sell and a not in sells:
                sells[a] = CenterOrder(sell_id)
            # Same-close sell/stop has priority even when no position was filled.
            if not held and not sell and not stop and buy and cell["eligible"]:
                if (self.policy.cooldown_bars and state.last_exit
                        and t - np.searchsorted(self.features.days, state.last_exit) < self.policy.cooldown_bars):
                    continue
                context = self.features.context
                if not self.policy.context_allows(context, t, a, close):
                    continue
                score = None
                if self.policy.ranking == "reward_risk":
                    score = (float(cell["high"]) - close) / max(close - float(cell["low"]), close * .01)
                elif self.policy.ranking == "momentum":
                    score = float(context.momentum20[t, a])
                elif self.policy.ranking == "low_volatility":
                    score = -float(context.atr14[t, a]) / close
                if score is not None and not np.isfinite(score):
                    continue
                atr = float(context.atr14[t, a]) if context is not None else 0.0
                buys[a] = CenterOrder(buy_id, float(cell["low"]), float(cell["high"]),
                                       atr=atr, rank_score=score,
                                       risk_low=evidence.c_low if self.policy.lesson24_stop else 0.0,
                                       evidence=evidence if self.policy.lesson24_stop else None)
        return buys, sells

    @staticmethod
    def _lesson24_exit(state, cell, observation, close, day):
        if not cell["valid"] or (state.downside and state.downside.c_end in observation.revoked):
            state.downside = None
        if not np.isfinite(close) or close <= 0:
            return None
        if state.risk_low > 0 and close <= state.risk_low:
            return CenterOrder("center_l24_risk_stop", reason="center_stop")
        if cell["valid"]:
            for e in observation.divergences:
                if (e.matches(state.lesson_reference) and e.observed_at == day
                        and state.evidence_after < e.c_end < day and e.c_low >= state.risk_low):
                    state.downside = e
                    state.evidence_after = e.c_end
        if state.downside is not None:
            rebound = next((r for r in observation.rebounds if r.start == state.downside.c_end), None)
            if rebound is not None:
                departure = state.downside
                state.downside = None
                if rebound.high < state.frozen[0]:
                    return CenterOrder("center_l24_failed_rebound", exit_evidence={
                        "first_rebound": asdict(rebound), "departure": asdict(departure)})
        return None

    @staticmethod
    def _bsp_id(cell, side):
        code = int(cell[f"{side}_code"])
        return f"center_bsp_{BSP_TYPES[code - 1]}_{side}" if 1 <= code <= 4 else ""

    def on_fill(self, asset: int, order: CenterOrder):
        self.states[asset].frozen = (order.low, order.high)
        self.states[asset].risk_low = order.risk_low
        self.states[asset].downside = order.evidence
        if order.evidence is not None:
            self.states[asset].lesson_reference = (order.evidence.center, order.low, order.high)
            self.states[asset].evidence_after = order.evidence.c_end

    def on_exit(self, asset: int, day: int):
        state = self.states[asset]
        state.frozen = None
        state.last_exit = int(day)
        state.phase_since = 0
        state.signature = None
        state.buy_confirmation = None
        state.risk_low = 0.0
        state.downside = None
        state.lesson_reference = None
        state.evidence_after = 0


def validate_center_execution(config):
    if (config.entry_fill != "open_t+1" or config.exit_fill != "open_t+1"
            or config.minute_fill or config.max_hold_days is not None
            or any(getattr(config, name) is not None for name in (
                "stop_loss_pct", "take_profit_pct", "trailing_stop_pct",
                "trailing_take_profit_activate_pct", "trailing_take_profit_drawdown_pct"))):
        raise ValueError("中枢震荡须使用双侧次交易日开盘和方案内止损, 不支持分钟成交或额外止盈止损及持有期限")
