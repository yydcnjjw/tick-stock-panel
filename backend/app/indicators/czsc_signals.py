"""Retired CZSC API. Historical strategy files are retained without executing them."""
from app.indicators.chan_signals import LEGACY_WARNING

SIGNALS = {f"signal_czsc_{kind}_{side}" for kind in ("first", "second", "third") for side in ("buy", "sell")}


def availability():
    return {"available": False, "reason": LEGACY_WARNING, "version": None}


def _load_runtime():
    raise ValueError(LEGACY_WARNING)


def compute(*args, **kwargs):
    raise ValueError(LEGACY_WARNING)
