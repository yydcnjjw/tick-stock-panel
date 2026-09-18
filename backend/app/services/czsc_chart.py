"""单股多周期 CZSC 图表服务,只读本地行情和有界响应缓存。"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, time, timedelta
from threading import Lock

from app.enriched_generation import EnrichedGenerationUnavailableError
from app.indicators import czsc_signals as cs
from app.indicators.czsc_bars import FREQUENCIES, prepare_bars
from app.indicators.czsc_chart import build_chart
from app.market_time import CN_TZ, cn_now

ANALYSIS_BARS = 1000
_CACHE_LIMIT = 32
_cache: OrderedDict[tuple, dict] = OrderedDict()
_lock = Lock()


def get_chart(repo, symbol: str, *, now: datetime | None = None, timeframe: str = "1d") -> dict:
    if timeframe not in FREQUENCIES:
        raise ValueError("不支持的 CZSC 图表周期")
    if repo.resolve_asset_type(symbol) != "stock":
        raise ValueError("CZSC 图表首版仅支持 A 股股票")
    name = repo.get_name_map([symbol]).get(symbol)
    if not name:
        raise ValueError("未找到股票,请通过股票搜索选择标的")
    stamp = now or cn_now()
    stamp = stamp.replace(tzinfo=CN_TZ) if stamp.tzinfo is None else stamp.astimezone(CN_TZ)
    end = stamp.date() - timedelta(days=stamp.time() < time(15))
    minute = timeframe.endswith("m")
    step = int(timeframe[:-1]) if minute else 1
    minute_end = stamp.replace(tzinfo=None, second=0, microsecond=0, minute=(stamp.minute // step) * step)
    read_generation = (repo.get_minute_chart_generation if minute
                       else lambda: repo.get_matrix_data_generation("stock", readonly=True))
    base = {"symbol": symbol, "name": name, "timeframe": timeframe, "version": cs.VERSION,
            "analysis_bars": ANALYSIS_BARS, "min_bi_len": 6, "max_bi_num": 50,
            "source": "local_minute" if minute else "local_enriched",
            "price_basis": "沿用本地分钟价格" if minute else "沿用本地日线价格",
            "volume_unit": "手", "amount_unit": "元"}
    status = cs.availability()
    if not status["available"]:
        return {**base, "status": "unavailable", "reason": status["reason"]}
    generation = read_generation()
    key = (repo, symbol, timeframe, minute_end if minute else end, generation, cs.VERSION, ANALYSIS_BARS, 6, 50, tuple(cs.SIGNALS))
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
    if cached is not None:
        if read_generation() != generation:
            raise EnrichedGenerationUnavailableError("行情数据更新中,请稍后刷新")
        return {**cached, "refreshed_at": stamp.isoformat()}
    if minute:
        # Include auction rows and one extra session so truncation cannot invent a partial first bucket.
        read_limit = (ANALYSIS_BARS + 2) * step * 241 // 240 + 241
        source = repo.get_minute_published(symbol, minute_end, read_limit)
    else:
        read_limit = ANALYSIS_BARS * 5 + 5 if timeframe == "1w" else ANALYSIS_BARS
        source = repo.get_daily_published(symbol, end, read_limit, sorted(cs.INPUT_COLUMNS))
    frame = prepare_bars(source, timeframe, now=stamp).tail(ANALYSIS_BARS)
    base["source_count"] = len(source)
    if frame.is_empty():
        payload = {**base, "status": "empty", "reason": "本地暂无该周期已结束的行情,请前往数据管理同步对应日线或分钟数据"}
    else:
        chart = build_chart(frame, now=stamp, timeframe=timeframe)
        starts = [item["start"] for item in chart["strokes"] + chart["unfinished"]]
        payload = {**base, **chart, "status": "ready", "reason": None,
                   "input_start": frame[0, "date"].isoformat(), "input_end": frame[-1, "date"].isoformat(),
                   "input_count": len(frame), "structure_start": min(starts) if starts else None,
                   "cutoff": chart["rows"][-1]["date"] if chart["rows"] else None}
    if read_generation() != generation:
        raise EnrichedGenerationUnavailableError("行情数据更新中,请稍后刷新")
    payload["generation"] = generation
    with _lock:
        _cache[key] = payload
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return {**payload, "refreshed_at": stamp.isoformat()}
