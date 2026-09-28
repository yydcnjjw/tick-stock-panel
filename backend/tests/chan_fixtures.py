"""Standard-library deterministic synthetic OHLC generator for structural tests."""
import math
import random
from datetime import datetime, timedelta


def synthetic_bars(seed, count):
    """Yield reproducible positive OHLC bars with nested oscillation + seeded noise.

    Artificial daily dates include non-trading days deliberately. This tests the
    structural state machine, not market calendar handling or profitability.
    Data generator uses only the Python standard library and no chan.py types.
    """
    rng = random.Random(seed)
    fast = 12 + seed % 5
    medium = 64 + (seed % 7) * 7
    slow = 311 + (seed % 11) * 13
    phases = [rng.random() * 2 * math.pi for _ in range(3)]
    start = datetime(2010, 1, 1)
    previous_close = 100.0
    drift = 0.0
    for i in range(count):
        drift += rng.uniform(-0.15, 0.15)
        close = (100.0 + drift
                 + (3.0 + 0.8 * math.sin(i / 179.0)) * math.sin(2 * math.pi * i / fast + phases[0])
                 + 7.0 * math.sin(2 * math.pi * i / medium + phases[1])
                 + 13.0 * math.sin(2 * math.pi * i / slow + phases[2])
                 + rng.uniform(-0.25, 0.25))
        open_price = previous_close
        high = max(open_price, close) + rng.uniform(0.05, 0.35)
        low = min(open_price, close) - rng.uniform(0.05, 0.35)
        volume = 1000 + rng.randrange(1000)
        yield {'dt': start + timedelta(days=i), 'open': open_price,
               'high': high, 'low': low, 'close': close, 'volume': volume}
        previous_close = close
