"""Price-only Chan charts, with gold session cutoffs independent of A shares."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import polars as pl

from app.data_providers.spot_gold import GoldBar, GoldHistory
from app.indicators.chan_runtime import CONFIG, PROFILE_ID, VERSION
from app.indicators.czsc_chart import CHART_INDICATORS, MA_PERIODS, structure_snapshot
from app.indicators.pipeline import compute_indicators
from app.market_time import CN_TZ
from app.vendor.chanpy.ChanConfig import CChanConfig
from app.vendor.chanpy.Common.CTime import CTime
from app.vendor.chanpy.KLine.KLine_List import CKLine_List
from app.vendor.chanpy.KLine.KLine_Unit import CKLine_Unit

PERIODS = {'1w': '周线', '1d': '日线', '4h': '4 小时', '1h': '1 小时'}


def _weekly(daily: tuple[GoldBar, ...]) -> tuple[GoldBar, ...]:
    groups = defaultdict(list)
    for row in daily:
        groups[row.time - timedelta(days=row.time.weekday())].append(row)
    return tuple(GoldBar(day, rows[0].open, max(r.high for r in rows),
                         min(r.low for r in rows), rows[-1].close)
                 for day, rows in groups.items())


def _closed(row: GoldBar, timeframe: str, now: datetime) -> bool:
    # Daily labels are trading dates, not midnight close stamps. Conservative
    # next-morning 08:00 Beijing avoids prematurely finalizing the overnight
    # session in both US DST regimes; weekly closes no earlier than Saturday 08:00.
    end = (row.time + timedelta(days=5, hours=8) if timeframe == '1w' else
           row.time + timedelta(days=1, hours=8) if timeframe == '1d' else row.time)
    return end <= now.astimezone(CN_TZ).replace(tzinfo=None)


def _panel(timeframe: str, bars: tuple[GoldBar, ...], now: datetime) -> dict:
    def label(t):
        return t.isoformat(timespec='minutes') if timeframe.endswith('h') else t.strftime('%Y-%m-%d')
    rows = [{'date': label(b.time), 'open': b.open, 'high': b.high, 'low': b.low,
             'close': b.close, 'closed': _closed(b, timeframe, now)} for b in bars]
    if rows:
        # Display indicators include the provisional tail. Chan structures below
        # still use closed bars only; all indicators depend on preceding prices.
        indicators = compute_indicators(pl.DataFrame({
            'symbol': ['XAUUSD'] * len(bars),
            'date': [b.time for b in bars], 'close': [b.close for b in bars],
        }), needed=set(CHART_INDICATORS))
        indicators = indicators.with_columns([
            pl.when(pl.col(f'ma{n}').is_not_null()).then(pl.col(f'ema{n}'))
            .otherwise(None).alias(f'ema{n}') for n in MA_PERIODS
        ])
        for row, values in zip(rows, indicators.select(CHART_INDICATORS).to_dicts(), strict=True):
            row.update(values)
    closed = [b for b in bars if _closed(b, timeframe, now)]
    level = CKLine_List(timeframe, CChanConfig(deepcopy(CONFIG)))
    previous = None
    for i, row in enumerate(closed):
        t = row.time
        unit = CKLine_Unit({'time_key': CTime(t.year, t.month, t.day, t.hour, t.minute, auto=False),
                           'open': row.open, 'high': row.high, 'low': row.low, 'close': row.close})
        unit.kl_type = timeframe
        unit.set_idx(i)
        unit.set_pre_klu(previous)
        level.add_single_klu(unit)
        previous = unit
    state = SimpleNamespace(level=level, rows=[{'date': b.time} for b in closed],
                            points_snapshot=lambda: [])
    structures = structure_snapshot(state, timeframe=timeframe)
    return {'timeframe': timeframe, 'label': PERIODS[timeframe], 'rows': rows,
            'status': 'ready' if closed else 'empty',
            'cutoff': label(closed[-1].time) if closed else None,
            'input_start': label(closed[0].time) if closed else None,
            'input_count': len(closed), 'source_count': len(rows),
            **structures}


def build_gold_charts(history: GoldHistory, *, now: datetime) -> dict:
    frames = {'1w': _weekly(history.frames['1d']), **history.frames}
    return {'symbol': 'XAUUSD', 'name': '伦敦金现', 'status': 'ready', 'reason': None,
            'provider': history.provider, 'source': history.source, 'source_url': history.source_url,
            'price_basis': '美元/金衡盎司 · 现货 CFD 原始报价', 'timezone': 'Asia/Shanghai',
            'refreshed_at': history.fetched_at.isoformat(), 'version': VERSION,
            'profile_id': PROFILE_ID, 'panels': [_panel(key, frames[key], now) for key in PERIODS],
            'notes': ['软件结构为辅助证据；K线周期不等于缠论递归级别，未确认严格买卖点。',
                      '小时线为北京时间结束标签；日线保守采用次日08:00定版，周线采用周六08:00定版。',
                      '未收盘K线仅展示，不参与结构计算；成交量缺失，不作补零。',
                      '均线与MACD复用应用价格指标：SMA/EMA 5/10/20/60满N根显示，MACD参数12/26/9；未收盘值可能变化。',
                      '周线由原生日线聚合；周/日历史始于2021-01-04，分钟历史范围由源决定。',
                      '供应商历史缺口未全部独立审计，不补造缺失K线；刷新时间不代表最新成交时间。']}
