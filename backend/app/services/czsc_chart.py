"""单股收盘 CZSC 图表服务,采用已发布日线和有界响应缓存。"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, time, timedelta
from threading import Lock

from app.enriched_generation import EnrichedGenerationUnavailableError
from app.indicators import czsc_signals as cs
from app.indicators.czsc_chart import build_chart
from app.market_time import CN_TZ, cn_now

ANALYSIS_BARS = 1000
_CACHE_LIMIT = 32
_cache: OrderedDict[tuple, dict] = OrderedDict()
_lock = Lock()


def get_chart(repo, symbol: str, *, now: datetime | None = None) -> dict:
    if repo.resolve_asset_type(symbol) != "stock":
        raise ValueError("CZSC 图表首版仅支持 A 股股票")
    name = repo.get_name_map([symbol]).get(symbol)
    if not name:
        raise ValueError("未找到股票,请通过股票搜索选择标的")
    stamp = now or cn_now()
    stamp = stamp.replace(tzinfo=CN_TZ) if stamp.tzinfo is None else stamp.astimezone(CN_TZ)
    end = stamp.date() - timedelta(days=stamp.time() < time(15))
    base = {"symbol": symbol, "name": name, "timeframe": "1d", "version": cs.VERSION,
            "analysis_bars": ANALYSIS_BARS, "min_bi_len": 6, "max_bi_num": 50,
            "source": "local_enriched", "price_basis": "沿用本地日线价格",
            "volume_unit": "手", "amount_unit": "元"}
    status = cs.availability()
    if not status["available"]:
        return {**base, "status": "unavailable", "reason": status["reason"]}
    generation = repo.get_matrix_data_generation("stock", readonly=True)
    key = (repo, symbol, end, generation, cs.VERSION, ANALYSIS_BARS, 6, 50, tuple(cs.SIGNALS))
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
    if cached is not None:
        if repo.get_matrix_data_generation("stock", readonly=True) != generation:
            raise EnrichedGenerationUnavailableError("日线数据更新中,请稍后刷新")
        return {**cached, "refreshed_at": stamp.isoformat()}
    frame = repo.get_daily_published(symbol, end, ANALYSIS_BARS, sorted(cs.INPUT_COLUMNS))
    if frame.is_empty():
        payload = {**base, "status": "empty", "reason": "本地暂无已发布的收盘日线,请先同步日线数据"}
    else:
        chart = build_chart(frame, now=stamp)
        starts = [item["start"] for item in chart["strokes"] + chart["unfinished"]]
        payload = {**base, **chart, "status": "ready", "reason": None,
                   "input_start": frame[0, "date"].isoformat(), "input_end": frame[-1, "date"].isoformat(),
                   "input_count": len(frame), "structure_start": min(starts) if starts else None,
                   "cutoff": chart["rows"][-1]["date"] if chart["rows"] else None}
    if repo.get_matrix_data_generation("stock", readonly=True) != generation:
        raise EnrichedGenerationUnavailableError("日线数据更新中,请稍后刷新")
    payload["generation"] = generation
    with _lock:
        _cache[key] = payload
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return {**payload, "refreshed_at": stamp.isoformat()}
