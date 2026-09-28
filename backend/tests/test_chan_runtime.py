"""The replacement must preserve as-of events rather than backfill final BSPs."""
import math
import random
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.indicators.chan_runtime import CONFIG, PROFILE_ID, ChanReplay
from tests.chan_fixtures import synthetic_bars


def history(n=1200, seed=2019):
    rng = random.Random(seed)
    price = 20.0
    result = []
    for i in range(n):
        price *= math.exp(rng.gauss(0, .035))
        result.append(dict(symbol="600000.SH", date=date(2018, 1, 1) + timedelta(days=i),
                           open=price, close=price, high=price * 1.015, low=price * .985,
                           volume=1000.0, amount=price * 100000))
    return result


def replay(rows, **kwargs):
    state = ChanReplay("600000.SH", "1d", **kwargs)
    events = []
    for row in rows:
        events.extend(state.update(row))
    return state, events


def test_fixed_profile_does_not_disable_divergence_or_fetch_data():
    assert CONFIG["divergence_rate"] == 1.0
    assert CONFIG["trigger_step"] is True
    assert CONFIG["one_bi_zs"] is False
    assert CONFIG["bs_type"] == "1,1p,2,2s,3a,3b"
    assert PROFILE_ID


def test_events_are_observed_after_endpoints_and_append_only():
    rows = history()
    _, short = replay(rows[:800])
    state, events = replay(rows, collect_structure=True)
    assert events, "fixture must actually exercise confirmed events"
    assert [e for e in events if e["confirmed_at"] <= rows[799]["date"].isoformat()] == short
    assert all(e["endpoint_at"] <= e["first_seen_at"] <= e["confirmed_at"] for e in events)
    assert any(e["endpoint_at"] < e["confirmed_at"] for e in events)
    assert len({e["event_id"] for e in events}) == len(events)
    assert state.ready
    assert state.level.bs_point_lst.last_sure_pos >= 0


def test_flat_history_is_unknown_not_a_false_signal():
    rows = history(30)
    for row in rows:
        row.update(open=10, high=11, low=9, close=10)
    state, events = replay(rows)
    assert not state.ready
    assert events == []


def test_native_dependency_is_namespaced():
    import sys
    replay(history(10))
    assert "Chan" not in sys.modules
    assert "Bi" not in sys.modules


def test_duplicate_native_time_fails():
    state = ChanReplay("600000.SH", "1d")
    row = history(1)[0]
    state.update(row)
    with pytest.raises(ValueError):
        state.update(row)


def test_native_trade_units_are_shares_and_yuan():
    row = history(1)[0]
    row.update(volume=123.0, amount=123456.0)
    state, _ = replay([row])
    unit = state.level.lst[0].lst[0]
    assert unit.trade_info.metric["volume"] == 12300.0
    assert unit.trade_info.metric["turnover"] == 123456.0


def test_all_native_families_on_both_layers_remain_immutable_at_every_prefix():
    families = {"bi": set(), "seg": set()}
    for seed in (0, 5, 7):
        state = ChanReplay("600000.SH", "1d", collect_structure=True)
        frozen = []
        for bar in synthetic_bars(seed, 3000):
            row = {**bar, "date": bar["dt"].date(), "amount": bar["volume"] * 100 * bar["close"]}
            state.update(row)  # also verifies disappearance, price/type mutation and boundary regression
            assert state.events[:len(frozen)] == frozen
            frozen = deepcopy(state.events)
        assert not any(e["baseline"] for e in state.events)
        assert len({e["event_id"] for e in state.events}) == len(state.events)
        for event in state.events:
            assert event["endpoint_at"] <= event["first_seen_at"] <= event["confirmed_at"]
            families[event["level"]].update(event["types"])
    assert families == {level: {"1", "1p", "2", "2s", "3a", "3b"} for level in families}


@pytest.mark.parametrize("failure", ["missing", "type", "boundary"])
def test_upstream_confirmation_regressions_fail_closed(monkeypatch, failure):
    rows = history()
    state, _ = replay(rows, collect_structure=True)
    assert state.signatures
    monkeypatch.setattr(state.native, "trigger_load", lambda _: None)
    key = next(k for k in state.signatures if k[0] == "bi")
    store = state.level.bs_point_lst
    points = list(store.get_latest_bsp(0))
    if failure == "missing":
        monkeypatch.setattr(store, "get_latest_bsp", lambda _: [p for p in points if state.point_key(p, "bi") != key])
    elif failure == "type":
        point = next(p for p in points if state.point_key(p, "bi") == key)
        point.type = []
    else:
        store.last_sure_pos = -1
    with pytest.raises(ValueError, match="停止发布信号"):
        state.update({**rows[-1], "date": rows[-1]["date"] + timedelta(days=1)})


def test_vendor_manifest_pins_every_included_upstream_file():
    import hashlib
    import json
    from pathlib import Path

    from app.indicators.chan_runtime import VERSION
    from app.vendor import chanpy

    root = Path(chanpy.__file__).parent
    manifest = json.loads((root / "UPSTREAM.json").read_text())
    assert manifest["commit"] == VERSION
    assert "MIT License" in (root / "LICENSE").read_text()
    for path, hashes in manifest["files"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == hashes["vendored_sha256"]
