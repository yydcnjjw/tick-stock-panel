"""公开交易所行情转为标准分钟数据, 不在图表或 API 内绑定来源。"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock

import httpx
import polars as pl

from app.market_time import CN_TZ, cn_today

logger = logging.getLogger(__name__)
_COLUMNS = ["datetime", "open", "high", "low", "close", "volume", "amount"]
_MAX_BARS = 70_000


@dataclass
class _Config:
    name: str = "exchange_minute"
    display_name: str = "交易所分钟行情"
    datasets: dict = field(default_factory=lambda: {"minute": None})
    path: None = None
    builtin: bool = True


def _wallclock(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is not None:
        return value.astimezone(CN_TZ).replace(tzinfo=None)
    return value


class ExchangeMinuteProvider:
    """沪深股票/ETF 1分钟原始价, 可选竞价行保留给统一聚合层。"""

    name = "exchange_minute"
    builtin = True

    def __init__(self) -> None:
        self.config = _Config()
        self._client = httpx.Client(timeout=20, headers={
            "Referer": "https://www.sse.com.cn/", "User-Agent": "Mozilla/5.0",
        })
        self._request_lock = Lock()
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def _fetch(self, market: str, code: str, count: int) -> dict:
        # 单源请求串行且最多每秒 4 次, 避免并发页面把公开行情源打满。
        with self._request_lock:
            time.sleep(max(0, 0.25 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            response = self._client.get(
                f"https://yunhq.sse.com.cn:32042/v1/{market}/mink/{code}",
                params={"period": 1, "begin": -(count + 1), "end": -1},
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != code or not isinstance(payload.get("kline"), list):
            raise ValueError("交易所分钟响应的代码或字段不符合要求")
        return payload

    @staticmethod
    def _normalize(rows: list, symbol: str) -> pl.DataFrame:
        if not rows:
            return pl.DataFrame()
        if any(not isinstance(row, list) or len(row) != len(_COLUMNS) for row in rows):
            raise ValueError("交易所分钟 K 字段数量不符合要求")
        df = pl.DataFrame(rows, schema=_COLUMNS, orient="row").with_columns(
            pl.col("datetime").cast(pl.String).str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S"),
            pl.col(*_COLUMNS[1:]).cast(pl.Float64),
            pl.lit(symbol).alias("symbol"),
        )
        valid = pl.all_horizontal([
            pl.col(c).is_not_null() & pl.col(c).is_finite() & (pl.col(c) > 0)
            for c in ("open", "high", "low", "close")
        ]) & (pl.col("low") <= pl.min_horizontal("open", "close")) & (
            pl.col("high") >= pl.max_horizontal("open", "close")
        )
        if df["datetime"].null_count() or df["datetime"].is_duplicated().any() or not df.select(valid.all()).item():
            raise ValueError("交易所分钟 K 时间或价格无效")
        # 上游成交量为股, 成交额为元; 不能将逐分钟价格人为复权或补齐。
        return df.with_columns(pl.col("volume") / 100).select("symbol", *_COLUMNS).sort("datetime")

    def get_minute(self, symbols, start_time, end_time, asset_type="stock", freq="1m", on_chunk_done=None):
        if asset_type not in {"stock", "etf"}:
            return pl.DataFrame()
        if freq != "1m":
            raise ValueError("交易所分钟插件仅提供 1m, 其他周期由本地聚合")
        start, end = _wallclock(start_time), _wallclock(end_time)
        if start and end and start > end:
            raise ValueError("分钟查询开始时间不能晚于结束时间")
        # 每个自然日最多 241 行含竞价, 按起点估算上界; 不承诺覆盖停牌或源缺失历史。
        count = min(_MAX_BARS, (max(0, (cn_today() - start.date()).days) + 2) * 241) if start else 20 * 241
        frames = []
        for index, symbol in enumerate(symbols):
            match = re.fullmatch(r"(\d{6})\.(SH|SZ)", symbol)
            if match:
                code, exchange = match.groups()
                payload = self._fetch("sh1" if exchange == "SH" else "sz1", code, count)
                df = self._normalize(payload["kline"], symbol)
                if not df.is_empty():
                    if start:
                        df = df.filter(pl.col("datetime") >= start)
                    if end:
                        df = df.filter(pl.col("datetime") <= end)
                    frames.append(df)
            else:
                logger.warning("交易所分钟源不覆盖标的 %s", symbol)
            if on_chunk_done:
                on_chunk_done(index + 1, len(symbols))
        return pl.concat(frames, how="vertical") if frames else pl.DataFrame()

    def test_dataset(self, dataset, symbols=None):
        if dataset != "minute":
            raise ValueError("交易所分钟插件仅支持 minute")
        df = self.get_minute(symbols or ["600276.SH"], None, None)
        preview = df.tail(3).with_columns(pl.col("datetime").cast(pl.String)) if not df.is_empty() else df
        return {"provider": self.name, "dataset": dataset, "rows": df.height,
                "columns": df.columns, "preview": preview.to_dicts()}
