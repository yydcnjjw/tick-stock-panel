"""股票及指数 chan.py 图表服务,只读本地行情和有界响应缓存。"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, time, timedelta
from threading import Lock

from app.enriched_generation import EnrichedGenerationUnavailableError
from app.indicators import chan_signals as cs
from app.indicators.chan_runtime import CONFIG, PROFILE_ID
from app.indicators.czsc_bars import FREQUENCIES, prepare_bars
from app.indicators.czsc_chart import build_chart
from app.market_time import CN_TZ, cn_now
from app.services import kline_sync, preferences

ANALYSIS_BARS = 1000
_CACHE_LIMIT = 32
_cache: OrderedDict[tuple, dict] = OrderedDict()
_lock = Lock()


def minute_route() -> dict:
    from app.data_providers.capabilities import build_capability_matrix
    from app.tickflow.policy import base_tier_name

    matrix = build_capability_matrix(
        {"czsc_minute_data_provider": preferences.get_czsc_minute_data_provider()},
        tickflow_tier=base_tier_name(),
    )
    return next(cap for cap in matrix["capabilities"] if cap["id"] == "czsc_minute")


def sync_minute(repo, symbol: str, *, provider: str, days: int) -> int:
    """复用分钟标准化及原子分区写入, 只更新所选源的 chan.py 数据。"""
    root = repo.minute_chart_root(provider)
    now = cn_now()
    written = 0

    def persist(frame):
        nonlocal written
        with repo._write_lock:
            written += kline_sync._write_minute_partition(frame, root)

    kline_sync.sync_minute_batch(
        [symbol], start_time=now - timedelta(days=int(days * 7 / 5) + 5), end_time=now,
        on_segment=persist, provider_name=provider,
        segment_trading_days=preferences.get_minute_sync_segment_days(),
    )
    return written


def get_chart(repo, symbol: str, *, now: datetime | None = None, timeframe: str = "1d",
              asset_type: str | None = None) -> dict:
    if timeframe not in FREQUENCIES:
        raise ValueError("不支持的 chan.py 图表周期")
    resolved_asset = repo.resolve_asset_type(symbol)
    if resolved_asset not in {"stock", "index"}:
        raise ValueError("chan.py 图表仅支持 A 股股票和指数目录中的指数")
    if asset_type is not None and asset_type != resolved_asset:
        raise ValueError("标的与所选资产类型不匹配,请通过股票或指数搜索选择标的")
    asset_type = resolved_asset
    name = repo.get_name_map([symbol]).get(symbol)
    if not name:
        raise ValueError("未找到标的,请通过股票或指数搜索选择标的;目录缺失时请前往数据管理同步")
    stamp = now or cn_now()
    stamp = stamp.replace(tzinfo=CN_TZ) if stamp.tzinfo is None else stamp.astimezone(CN_TZ)
    end = stamp.date() - timedelta(days=stamp.time() < time(15))
    minute = timeframe.endswith("m")
    is_index = asset_type == "index"
    route = minute_route() if minute and not is_index else None
    provider = route["effective"] if route else None
    step = int(timeframe[:-1]) if minute else 1
    minute_end = stamp.replace(tzinfo=None, second=0, microsecond=0, minute=(stamp.minute // step) * step)
    read_generation = (repo.get_index_chart_generation if is_index else
                       (lambda: repo.get_minute_chart_generation(provider=provider)) if minute else
                       lambda: repo.get_matrix_data_generation("stock", readonly=True))
    base = {"symbol": symbol, "name": name, "asset_type": asset_type,
            "timeframe": timeframe, "version": cs.VERSION,
            "supported_timeframes": ["1d", "1w"] if is_index else list(FREQUENCIES),
            "analysis_bars": ANALYSIS_BARS, "engine": "chan.py", "profile_id": PROFILE_ID,
            "algorithm_config": CONFIG, "confirmation": "endpoint_at_or_before_last_sure_pos",
            "source": "local_index_enriched" if is_index else "local_minute" if minute else "local_enriched",
            "price_basis": "沿用本地指数点位(不复权)" if is_index else "沿用本地分钟价格" if minute else "沿用本地日线价格",
            "volume_unit": "手", "amount_unit": "元"}
    if is_index and minute:
        return {**base, "status": "unavailable", "unavailable_reason": "unsupported_timeframe",
                "reason": "指数 chan.py 图表仅支持日线和周线,请选择对应周期"}
    if route:
        base.update(minute_provider=provider, minute_provider_display=route["effective_display"],
                    minute_sync_available=route["usable"])
    status = cs.availability()
    if not status["available"]:
        return {**base, "status": "unavailable", "reason": status["reason"]}
    generation = read_generation()
    key = (repo, asset_type, symbol, name, timeframe, provider, route["usable"] if route else None,
           minute_end if minute else end, generation, cs.VERSION, PROFILE_ID, ANALYSIS_BARS, tuple(cs.SIGNALS))
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
        source = repo.get_minute_published(symbol, minute_end, read_limit, provider=provider)
    else:
        read_limit = ANALYSIS_BARS * 5 + 5 if timeframe == "1w" else ANALYSIS_BARS
        source = repo.get_daily_published(symbol, end, read_limit, sorted(cs.INPUT_COLUMNS), asset_type=asset_type)
    frame = prepare_bars(source, timeframe, now=stamp).tail(ANALYSIS_BARS)
    base["source_count"] = len(source)
    if frame.is_empty():
        reason = ("当前 chan.py 分钟源尚无本地已结束行情,请同步分钟数据" if minute else
                  "本地暂无该周期已结束的行情,请前往数据管理同步日线数据")
        payload = {**base, "status": "empty", "reason": reason}
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
