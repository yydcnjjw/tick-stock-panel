"""As-of, revocable pen-level evidence for the Lesson 24 experiment.

Native sure pens are an operational approximation, not recursively confirmed
sublevel trends. Revisions revoke evidence; no later endpoint is backfilled.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np


@dataclass(frozen=True)
class DownsideEvidence:
    center: int
    low: float
    high: float
    a_start: int
    a_end: int
    c_start: int
    c_end: int
    c_low: float
    a_area: float
    c_area: float
    observed_at: int

    def matches(self, signature):
        return (self.center, self.low, self.high) == signature


@dataclass(frozen=True)
class Rebound:
    start: int
    end: int
    high: float


@dataclass(frozen=True)
class Observation:
    divergences: tuple[DownsideEvidence, ...] = ()
    rebounds: tuple[Rebound, ...] = ()
    revoked: tuple[int, ...] = ()


@dataclass(frozen=True)
class Lesson24Features:
    # Keys use absolute ordinals so matrix warmup slicing cannot move evidence.
    observations: Mapping[tuple[int, int], Observation] = field(default_factory=dict)

    def readonly(self):
        return Lesson24Features(MappingProxyType(dict(self.observations)))

    def slice(self, days):
        if not len(days):
            return Lesson24Features().readonly()
        return Lesson24Features({k: v for k, v in self.observations.items()
                                 if days[0] <= k[0] <= days[-1]}).readonly()

    def validate(self, days, assets):
        allowed = set(map(int, days))
        for (day, asset), obs in self.observations.items():
            if day not in allowed or not 0 <= asset < assets:
                raise ValueError("第24课证据日期或标的越界")
            if any(e.observed_at != day or not e.a_start < e.a_end <= e.c_start < e.c_end < day
                   or not 0 < e.c_low < e.low < e.high or not 0 < e.c_area < e.a_area
                   for e in obs.divergences):
                raise ValueError("第24课证据时序或价格面积无效")
            if any(not r.start < r.end < day or not np.isfinite(r.high) or r.high <= 0
                   for r in obs.rebounds):
                raise ValueError("第24课反弹证据无效")

    @property
    def nbytes(self):
        return sum(128 + len(o.divergences) * 448 + len(o.rebounds) * 160 + len(o.revoked) * 32
                   for o in self.observations.values())


def negative_area(bi):
    """Sum every negative histogram bar in the whole A/C pen, including endpoints."""
    begin, end = bi.get_begin_klu().idx, bi.get_end_klu().idx
    return sum(max(0., -float(unit.macd.macd)) for candle in bi.klc_lst for unit in candle.lst
               if begin <= unit.idx <= end)


class Lesson24Replay:
    def __init__(self):
        self.evidence = {}
        self.rebounds = set()

    def update(self, replay):
        today = replay.rows[-1]["date"].toordinal()
        def ordinal(unit):
            return replay.rows[unit.idx]["date"].toordinal()
        current = {}
        for center in replay.level.zs_list:
            a, c = center.bi_in, center.bi_out
            if (center.is_one_bi_zs() or a is None or c is None
                    or not a.is_down() or not c.is_down() or not a.is_sure or not c.is_sure
                    or a.idx >= center.begin_bi.idx or c.idx != center.end_bi.idx + 1
                    or center.end_bi.idx < center.begin_bi.idx + 2
                    or not all(b.is_sure for b in replay.level.bi_list[center.begin_bi.idx:c.idx])
                    or c.get_end_val() >= min(center.low, a.get_end_val())
                    or ordinal(c.get_end_klu()) >= today):
                continue
            area_a, area_c = negative_area(a), negative_area(c)
            if not 0 < area_c < area_a:
                continue
            e = DownsideEvidence(ordinal(center.begin), float(center.low), float(center.high),
                                 ordinal(a.get_begin_klu()), ordinal(a.get_end_klu()),
                                 ordinal(c.get_begin_klu()), ordinal(c.get_end_klu()),
                                 float(c.get_end_val()), area_a, area_c, today)
            # Compare without observation time; a continuing proof is not a new buy.
            signature = tuple(vars(e).values())[:-1]
            current[e.c_end] = (signature, e)
        revoked = tuple(key for key, (sig, _) in self.evidence.items()
                        if key not in current or current[key][0] != sig)
        added = tuple(e for key, (sig, e) in current.items()
                      if key not in self.evidence or self.evidence[key][0] != sig)
        self.evidence = current
        rebounds = {Rebound(ordinal(b.get_begin_klu()), ordinal(b.get_end_klu()), float(b.get_end_val()))
                    for b in replay.level.bi_list if b.is_sure and b.is_up()
                    and ordinal(b.get_end_klu()) < today}
        # If a proof becomes available late, an already finished first rebound
        # must accompany it; otherwise the matcher might wait for a second one.
        relevant = {r for r in rebounds if any(r.start == e.c_end for e in added)}
        new_rebounds = tuple(sorted((rebounds - self.rebounds) | relevant, key=lambda r: (r.start, r.end)))
        self.rebounds = rebounds
        return Observation(added, new_rebounds, revoked)
