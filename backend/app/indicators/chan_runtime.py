"""Pinned chan.py calculation adapter; data acquisition belongs to KlineRepository.

Confirmation is an as-of event contract based on the upstream retention boundary,
not a claim that an endpoint was knowable on its own date or a strict Chan point.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime

VERSION = "429d6ed3043e27c93a003ba2b10e70a05575e1f5"
CONFIG = {
    "bi_algo": "normal", "bi_strict": True, "bi_fx_check": "strict",
    "gap_as_kl": False, "bi_end_is_peak": True, "bi_allow_sub_peak": True,
    "seg_algo": "chan", "left_seg_method": "peak",
    "zs_combine": True, "zs_combine_mode": "zs", "one_bi_zs": False, "zs_algo": "normal",
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "divergence_rate": 1.0, "min_zs_cnt": 1, "bsp1_only_multibi_zs": True,
    "max_bs2_rate": 0.9999, "macd_algo": "peak", "bs1_peak": True,
    "bs_type": "1,1p,2,2s,3a,3b", "bsp2_follow_1": True, "bsp3_follow_1": True,
    "bsp3_peak": False, "bsp2s_follow_2": False, "max_bsp2s_lv": None,
    "strict_bsp3": False, "bsp3a_max_zs_cnt": 1,
    "macd_algo-seg": "slope", "bsp1_only_multibi_zs-seg": False,
    "trigger_step": True, "skip_step": 0, "kl_data_check": True,
    "max_kl_misalgin_cnt": 2, "max_kl_inconsistent_cnt": 5,
    "auto_skip_illegal_sub_lv": False, "print_warning": False, "print_err_time": False,
    "mean_metrics": [], "trend_metrics": [], "cal_demark": False,
    "cal_rsi": False, "cal_kdj": False, "boll_n": 20,
}
PROFILE_ID = "chan-v1-" + hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:16]
BSP_LABELS = {"1": "一类", "1p": "盘整背驰", "2": "二类", "2s": "类二", "3a": "三类A", "3b": "三类B"}


def label_time(value, timeframe):
    return value.isoformat(timespec="minutes") if isinstance(value, datetime) and timeframe.endswith("m") else value.strftime("%Y-%m-%d")


class ChanReplay:
    def __init__(self, symbol: str, timeframe: str, *, collect_structure=False):
        from app.vendor.chanpy.Chan import CChan
        from app.vendor.chanpy.ChanConfig import CChanConfig
        from app.vendor.chanpy.Common.CEnum import KL_TYPE

        periods = {"1m": KL_TYPE.K_1M, "5m": KL_TYPE.K_5M, "30m": KL_TYPE.K_30M,
                   "1d": KL_TYPE.K_DAY, "1w": KL_TYPE.K_WEEK}
        self.symbol, self.timeframe = symbol, timeframe
        self.frequency = periods[timeframe]
        self.native = CChan(symbol, lv_list=[self.frequency], config=CChanConfig(deepcopy(CONFIG)))
        self.level = self.native[0]
        self.collect_structure = collect_structure
        self.rows: list[dict] = []
        self.first_seen: dict[tuple, str] = {}
        self.confirmed: dict[tuple, dict] = {}
        self.signatures: dict[tuple, tuple] = {}
        self.events: list[dict] = []
        self.ready = False
        self.baseline = False
        self._ready_levels: set[str] = set()
        self._boundaries: dict[str, int] = {}
        self.fractal_events: list[dict] = []
        self._last_fractal_candle = -1

    def point_key(self, point, level):
        return level, point.bi.get_end_klu().idx, bool(point.is_buy)

    def point_signature(self, point):
        return (point.klu.idx, point.bi.get_end_klu().idx, point.bi.get_end_val(),
                tuple(sorted({t.value for t in point.type})))

    def update(self, row: dict) -> list[dict]:
        from app.vendor.chanpy.Common.CTime import CTime
        from app.vendor.chanpy.KLine.KLine_Unit import CKLine_Unit

        day = row["date"]
        stamp = label_time(day, self.timeframe)
        if self.rows and day <= self.rows[-1]["date"]:
            raise ValueError("chan.py 行情时间必须严格递增")
        hour, minute = (day.hour, day.minute) if isinstance(day, datetime) else (15, 0)
        unit = CKLine_Unit({"time_key": CTime(day.year, day.month, day.day, hour, minute, auto=False),
                           **{k: row[k] for k in ("open", "high", "low", "close")},
                           "volume": row["volume"] * 100, "turnover": row["amount"]})
        try:
            self.native.trigger_load({self.frequency: [unit]})
        except Exception as exc:
            # Upstream asserts and arithmetic errors must not become false signals.
            raise ValueError(f"chan.py 结构计算失败 ({self.symbol}, {stamp}): {exc}") from exc
        self.rows.append(row)
        self.fractal_events = self._new_fractals(stamp)
        added = []
        lists = [("bi", self.level.bs_point_lst)]
        if self.collect_structure:
            lists.append(("seg", self.level.seg_bs_point_lst))
        self.baseline = False
        for level, store in lists:
            if store.last_sure_pos < self._boundaries.get(level, -1):
                raise ValueError("chan.py 确认边界回退; 停止发布信号")
            self._boundaries[level] = store.last_sure_pos
            points = {self.point_key(p, level): p for p in store.get_latest_bsp(0)}
            for key, signature in self.signatures.items():
                if key[0] == level and (key not in points or self.point_signature(points[key]) != signature):
                    raise ValueError(f"chan.py 已确认买卖点发生变化 ({level}, {stamp}); 停止发布信号")
            first_ready = store.last_sure_pos >= 0 and level not in self._ready_levels
            if store.last_sure_pos >= 0:
                self._ready_levels.add(level)
            if level == "bi":
                self.ready = level in self._ready_levels
                self.baseline = first_ready
            for key, point in points.items():
                self.first_seen.setdefault(key, stamp)
                if key in self.confirmed or key[1] > store.last_sure_pos:
                    continue
                if point.klu.idx != point.bi.get_end_klu().idx:
                    raise ValueError("chan.py 买卖点端点不一致; 停止发布信号")
                signature = self.point_signature(point)
                endpoint = label_time(self.rows[key[1]]["date"], self.timeframe)
                event = {"event_id": f"{self.symbol}:{self.timeframe}:{self.rows[0]['date']}:{level}:{endpoint}:{int(point.is_buy)}",
                         "level": level, "endpoint_at": endpoint, "first_seen_at": self.first_seen[key],
                         "confirmed_at": stamp, "date": stamp, "price": row["close"],
                         "endpoint_price": point.bi.get_end_val(), "is_buy": bool(point.is_buy),
                         "types": list(signature[-1]), "baseline": first_ready}
                self.confirmed[key] = event
                self.signatures[key] = signature
                if not first_ready:
                    self.events.append(event)
                    if level == "bi":
                        added.append(event)
        return added

    def _new_fractals(self, stamp):
        from app.vendor.chanpy.Common.CEnum import FX_TYPE

        if len(self.level.lst) < 3:
            return []
        candle = self.level.lst[-2]
        if candle.idx <= self._last_fractal_candle:
            return []
        self._last_fractal_candle = candle.idx
        if candle.fx not in (FX_TYPE.TOP, FX_TYPE.BOTTOM):
            return []
        top = candle.fx == FX_TYPE.TOP
        unit = candle.get_peak_klu(top)
        endpoint = label_time(self.rows[unit.idx]["date"], self.timeframe)
        return [{"event_id": f"{self.symbol}:{self.timeframe}:fx:{endpoint}:{int(top)}",
                 "endpoint_at": endpoint, "confirmed_at": stamp,
                 "kind": "top" if top else "bottom", "price": unit.high if top else unit.low}]

    def points_snapshot(self):
        result = []
        for level, store in (("bi", self.level.bs_point_lst), ("seg", self.level.seg_bs_point_lst)):
            for point in store.get_latest_bsp(0):
                key = self.point_key(point, level)
                event = self.confirmed.get(key)
                endpoint = label_time(self.rows[key[1]]["date"], self.timeframe)
                result.append({"level": level, "date": endpoint, "price": point.bi.get_end_val(),
                               "is_buy": bool(point.is_buy), "types": sorted({t.value for t in point.type}),
                               "status": "confirmed" if event else "candidate",
                               "first_seen_at": self.first_seen.get(key),
                               "confirmed_at": event["confirmed_at"] if event else None})
        return result
