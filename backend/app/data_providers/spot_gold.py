"""spot_gold v1: XAU/USD raw OHLC, no A-share units or calendar semantics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class GoldBar:
    # Naive Beijing wall clock. Intraday labels denote interval ends;
    # daily midnight denotes the supplier's trading date, NOT its close time.
    time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class GoldHistory:
    provider: str
    source: str
    source_url: str
    fetched_at: datetime
    frames: dict[str, tuple[GoldBar, ...]]


class SpotGoldProvider(Protocol):
    def get_spot_gold(self, *, now: datetime | None = None) -> GoldHistory:
        """Return native 1d/4h/1h snapshots; errors must not become empty data."""
