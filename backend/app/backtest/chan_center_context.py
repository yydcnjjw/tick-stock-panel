"""Past-only market context for daily center experiments, separate from Chan structure."""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CenterContext:
    atr14: np.ndarray
    ma20: np.ndarray
    ma60: np.ndarray
    ma20_prior5: np.ndarray
    momentum20: np.ndarray
    prior_high: np.ndarray
    volume_ratio: np.ndarray
    breadth60: np.ndarray

    def validate(self, shape):
        for name, array in vars(self).items():
            expected = (shape[0],) if name == "breadth60" else shape
            if array.shape != expected:
                raise ValueError(f"中枢上下文维度错误: {name}")

    def readonly(self):
        for array in vars(self).values():
            array.setflags(write=False)
        return self

    def slice(self, start, stop):
        return CenterContext(**{name: array[start:stop] for name, array in vars(self).items()})

    @property
    def nbytes(self):
        return sum(array.nbytes for array in vars(self).values())


def build_center_context(market) -> CenterContext:
    # Deferred import: matrix types also carry CenterFeatures.
    from app.backtest.matrix import rolling_mean

    def lag(array, n=1):
        out = np.full(array.shape, np.nan, dtype=np.float32)
        if n < len(array):
            out[n:] = array[:-n]
        return out

    valid = (np.isfinite(market.close) & np.isfinite(market.open)
             & np.isfinite(market.high) & np.isfinite(market.low)
             & np.isfinite(market.volume) & (market.volume > 0)
             & (market.low > 0) & (market.low <= np.minimum(market.open, market.close))
             & (np.maximum(market.open, market.close) <= market.high))
    close = np.where(valid, market.close, np.nan).astype(np.float32)
    high = np.where(valid, market.high, np.nan).astype(np.float32)
    low = np.where(valid, market.low, np.nan).astype(np.float32)
    volume = np.where(valid, market.volume, np.nan).astype(np.float32)
    previous = lag(close)
    true_range = np.maximum(high - low, np.maximum(abs(high - previous), abs(low - previous)))
    atr = rolling_mean(true_range, 14)
    ma20, ma60 = rolling_mean(close, 20), rolling_mean(close, 60)
    momentum = np.full(close.shape, np.nan, dtype=np.float32)
    continuous = np.isfinite(rolling_mean(close, 21))
    np.divide(close, lag(close, 20), out=momentum, where=continuous)
    momentum -= 1
    volume_ratio = np.full(close.shape, np.nan, dtype=np.float32)
    volume_mean = lag(rolling_mean(volume, 20))
    np.divide(volume, volume_mean, out=volume_ratio, where=np.isfinite(volume_mean) & continuous)
    mainboard = np.array([(s.startswith("60") and s.endswith(".SH"))
                         or (s.startswith("00") and s.endswith(".SZ")) for s in market.symbols])
    known = np.isfinite(ma60) & mainboard[None, :]
    count = known.sum(axis=1)
    breadth = np.full(len(close), np.nan, dtype=np.float32)
    np.divide(((close >= ma60) & known).sum(axis=1), count, out=breadth, where=count >= 500)
    return CenterContext(atr, ma20, ma60, lag(ma20, 5), momentum, lag(high),
                         volume_ratio, breadth).readonly()
