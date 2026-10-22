"""Bounded, read-only Sina XAU snapshot adapter. Never evaluate JSONP."""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx

from app.data_providers.spot_gold import GoldBar, GoldHistory
from app.market_time import CN_TZ, cn_now

SOURCE_URL = 'https://finance.sina.com.cn/futures/quotes/XAU.shtml'
DAILY_URL = ('https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_data=/'
             'GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU')
MINUTE_URL = 'https://gu.sina.cn/ft/api/jsonp.php/var%20_data=/GlobalService.getMink'
JSONP = re.compile(r'\s*(?:/\*.*?\*/\s*)?var\s+_data\s*=\s*\((.*)\);\s*', re.S)
HISTORY_START = datetime(2021, 1, 4)
MAX_RESPONSE_BYTES = 2_000_000


def parse_rows(text: str, timeframe: str, now: datetime) -> tuple[GoldBar, ...]:
    match = JSONP.fullmatch(text)
    if not match:
        raise ValueError('黄金数据响应格式无效')
    raw = json.loads(match[1])
    if not isinstance(raw, list) or not raw or len(raw) > 10000:
        raise ValueError('黄金历史为空或响应超出范围')
    daily = timeframe == '1d'
    wall_now = now.astimezone(CN_TZ).replace(tzinfo=None)
    latest_label = (wall_now.replace(hour=0, minute=0, second=0, microsecond=0) if daily
                    else wall_now + timedelta(hours=4 if timeframe == '4h' else 1))
    rows = []
    for item in raw:
        try:
            stamp = datetime.strptime(item['date' if daily else 'd'], '%Y-%m-%d' if daily else '%Y-%m-%d %H:%M:%S')
            # Verified old history contains invalid OHLC. Keep a contiguous window,
            # never silently skip or repair an invalid row inside that window.
            if daily and stamp < HISTORY_START:
                continue
            fields = ('open', 'high', 'low', 'close') if daily else ('o', 'h', 'l', 'c')
            o, h, low, c = (float(item[k]) for k in fields)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('黄金 K 线字段无效') from exc
        if not all(math.isfinite(v) and v > 0 for v in (o, h, low, c)) or not low <= min(o, c) <= max(o, c) <= h:
            raise ValueError(f'黄金 K 线价格无效：{stamp.isoformat()}')
        if stamp > latest_label or (rows and stamp <= rows[-1].time):
            raise ValueError('黄金 K 线时间超前、重复或乱序')
        if not daily and (stamp.minute or stamp.second or (timeframe == '4h' and stamp.hour % 4)):
            raise ValueError('黄金 K 线周期标签无效')
        rows.append(GoldBar(stamp, o, h, low, c))
    if not rows:
        raise ValueError('黄金历史窗口内没有可用 K 线')
    return tuple(rows)


class SinaGoldProvider:
    name = 'sina_gold'
    builtin = True

    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.config = SimpleNamespace(datasets={'spot_gold': {}}, display_name='新浪伦敦金')
        self._transport = transport

    def close(self) -> None:
        """Clients are scoped to a snapshot; there is no persistent connection."""

    def get_spot_gold(self, *, now: datetime | None = None) -> GoldHistory:
        now = now or cn_now()
        urls = {'1d': DAILY_URL, '4h': MINUTE_URL + '?symbol=XAU&type=240',
                '1h': MINUTE_URL + '?symbol=XAU&type=60'}
        with httpx.Client(timeout=15, transport=self._transport,
                          headers={'Referer': SOURCE_URL, 'User-Agent': 'Mozilla/5.0'}) as client:
            def fetch(item):
                timeframe, url = item
                with client.stream('GET', url) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ValueError('黄金数据响应过大')
                        chunks.append(chunk)
                return timeframe, parse_rows(b''.join(chunks).decode('utf-8'), timeframe, now)

            with ThreadPoolExecutor(max_workers=3) as pool:
                frames = dict(pool.map(fetch, urls.items()))
        return GoldHistory(self.name, '新浪伦敦金（现货黄金）CFD', SOURCE_URL, now, frames)

    def test_dataset(self, dataset: str, symbols=None) -> dict:
        if dataset != 'spot_gold':
            return {'provider': self.name, 'dataset': dataset, 'error': '不支持该数据集'}
        snapshot = self.get_spot_gold()
        return {'provider': self.name, 'dataset': dataset,
                'rows': sum(len(rows) for rows in snapshot.frames.values()),
                'columns': ['time', 'open', 'high', 'low', 'close'],
                'preview': [{'timeframe': key, 'latest': rows[-1].time.isoformat(),
                             'close': rows[-1].close} for key, rows in snapshot.frames.items()]}
