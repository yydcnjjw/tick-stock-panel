"""One bounded chart snapshot, atomically replaced after all four charts succeed."""
from __future__ import annotations

import logging
from concurrent.futures import Future
from threading import Lock

from app.indicators.spot_gold_chart import build_gold_charts
from app.market_time import cn_now

logger = logging.getLogger(__name__)


class SpotGoldService:
    def __init__(self, repository, *, clock=cn_now):
        self.repository = repository
        self.clock = clock
        self._lock = Lock()
        self._flight: Future | None = None
        self._snapshot: dict | None = None
        self._loaded_at = None

    def get_chart(self, *, refresh=False) -> dict:
        now = self.clock()
        with self._lock:
            if not refresh and self._snapshot is not None and self._loaded_at is not None and (now - self._loaded_at).total_seconds() < 60:
                return self._snapshot
            flight = self._flight
            owner = flight is None
            if owner:
                flight = self._flight = Future()
            previous = self._snapshot
        # Neither network I/O, Chan calculation nor waiting holds the state lock.
        if not owner:
            return flight.result(timeout=60)
        try:
            history = self.repository.get_snapshot()
            result = build_gold_charts(history, now=history.fetched_at)
            with self._lock:
                self._snapshot = result
                self._loaded_at = self.clock()
            flight.set_result(result)
            return result
        except Exception as exc:
            logger.warning('spot-gold snapshot refresh failed: %s', type(exc).__name__)
            if previous is not None:
                stale = {**previous, 'status': 'stale',
                         'reason': '刷新失败，当前显示上次成功数据；请稍后重试。'}
                with self._lock:
                    self._snapshot = stale
                    self._loaded_at = self.clock()
                flight.set_result(stale)
                return stale
            flight.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._flight = None
