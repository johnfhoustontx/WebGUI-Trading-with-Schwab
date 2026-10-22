"""The intraday scalp call (scalp_intraday_call): build, freeze, publish, refresh.

No network and no live store. A fake client answers the proxy's two calls (the
price-history ``/passthrough`` with no candles, so the seeded bars are what each
reading sees; ``get_option_chain`` from a hand-built 0DTE chain), every store
write goes to ``tmp_path``, and the bus is the fake one.

The seeded day (Monday 2026-10-19): $SPX, SPY and QQQ each rise 0.1% a bar
from 08:30, so every slot's last 15 minutes are UP; $VIX sits flat at 20, the
modelled volatility when no chain is usable. The 09:06 slot enters on the 09:05
boundary (the bars starting 08:30 .. 09:00), the 09:21 slot on 09:20. The
intraday study's ``intraday_outcomes`` and ``intraday_verdicts`` rows are written
by hand so each role's rate and edge are known before the call is built.
"""
import ast
import datetime as dt
import json
import pathlib
import threading
import time
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import (handlers, scalp_call, scalp_chain, scalp_fetch,
                                  scalp_intraday_call as sic, scalp_intraday_inputs,
                                  scalp_model, scalp_record, scalp_store, scalp_study)
from services.options_svc.scalp_sim import ROLES
from shared import scalp_config
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command
from shared.contracts.options import ScalpView

CT = ZoneInfo("America/Chicago")
DAY = dt.date(2026, 10, 19)
ISO = DAY.isoformat()
SLOT = "09:06"
AT = dt.datetime(2026, 10, 19, 9, 6, 20, tzinfo=CT)
PRICES = {"$SPX": 5000.0, "SPY": 500.0, "QQQ": 400.0}
STEP = {"$SPX": 5, "SPY": 1, "QQQ": 1}
SVC = pathlib.Path(__file__).resolve().parents[1]
N_BARS = 10          # 08:30 .. 09:15


def ts(h, m=0, day=DAY):
    return int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT).timestamp())


def up_path(base, n=N_BARS):
    return [base * (1 + 0.001 * i) for i in range(n + 1)]


def seed_bars(db, symbols=tuple(PRICES), flat=(), vix=True, n=N_BARS):
    """Five-minute bars from 08:30, each opening at one price and closing at the
    next: up 0.1% a bar, or dead flat for a symbol in ``flat``."""
    rows = []
    for sym in symbols:
        prices = [PRICES[sym]] * (n + 1) if sym in flat else up_path(PRICES[sym], n)
        for i in range(n):
            o, c = prices[i], prices[i + 1]
            minute = 30 + 5 * i
            rows.append((sym, 5, ts(8 + minute // 60, minute % 60), o, max(o, c),
                         min(o, c), c, 1000.0))
    if vix:
        for i in range(n):
            minute = 30 + 5 * i
            rows.append(("$VIX", 5, ts(8 + minute // 60, minute % 60),
                         20.0, 20.0, 20.0, 20.0, 0.0))
    scalp_store.upsert_bars(rows, db_path=db)


def spot_at(sym, slot=SLOT):
    """The reading's ``entry_spot``: the close of the bar before the entry."""
    k = {"09:06": 7, "09:21": 10}[slot]
    return up_path(PRICES[sym])[k]


def bucket_of(db, sym, slot=SLOT):
    r = scalp_intraday_inputs.reading(sym, DAY, slot, db_path=db)
    return scalp_model.bucket_of(scalp_intraday_inputs.alignment(r), scalp_config.buckets())


def seed_study(db, symbol, rates, edges=(), bucket="high", block="morning", start=0):
    """``rates``: role -> (wins, n days) in ``bucket`` of ``block``; ``edges``:
    the roles whose stored verdict has an edge there."""
    scale = scalp_study._scale_key(scalp_config.iv_scale())
    conn = scalp_store.connect(db)
    try:
        with conn:
            for role, (wins, n) in rates.items():
                for i in range(n):
                    day = (dt.date(2025, 1, 1) + dt.timedelta(days=start + i)).isoformat()
                    conn.execute(
                        "INSERT OR REPLACE INTO intraday_outcomes VALUES "
                        "(?, '09:06', ?, ?, ?, 'x', ?, ?, ?, 30, 1.0, 0)",
                        (day, symbol, scale, role, block, bucket,
                         "win" if i < wins else "loss"))
            for role in ROLES:
                conn.execute("INSERT OR REPLACE INTO intraday_verdicts VALUES (?, ?, ?, ?, ?)",
                             (symbol, block, role, bucket,
                              json.dumps({"edge": role in edges})))
    finally:
        conn.close()


def _mids(spot, strike, step=1):
    extrinsic = 3.0 - 0.1 * abs(spot - strike) / step
    return max(spot - strike, 0.0) + extrinsic, max(strike - spot, 0.0) + extrinsic


def make_chain(sym, day=DAY, iv=18.0, spot=None):
    spot = spot_at(sym) if spot is None else spot
    step = STEP[sym]
    centre = round(spot / step) * step
    strikes = [centre + step * k for k in range(-12, 13)]

    def side(put):
        out = {}
        for k in strikes:
            mid = _mids(spot, k, step)[1 if put else 0]
            out[f"{float(k)}"] = [{
                "strikePrice": float(k), "putCall": "PUT" if put else "CALL",
                "bid": mid - 0.05, "ask": mid + 0.05, "mark": mid,
                "volatility": iv, "symbol": f"{sym.strip('$')}W {k}"}]
        return {f"{day.isoformat()}:0": out}

    return {"symbol": sym, "underlyingPrice": spot,
            "callExpDateMap": side(False), "putExpDateMap": side(True)}


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    """Price history answers no candles; a chain answers from ``chains``."""

    def __init__(self, chains=None):
        self.chains = {s: make_chain(s) for s in PRICES} if chains is None else chains
        self.calls = []

    def _get(self, path, params=None, **kw):
        self.calls.append(("bars", path, params, kw))
        return _Resp(200, {"candles": []})

    def get_option_chain(self, symbol, **kw):
        self.calls.append(("chain", symbol, None, kw))
        got = self.chains.get(symbol)
        if isinstance(got, Exception):
            raise got
        return _Resp(200, got) if got is not None else _Resp(500, None)


def chains_of(client):
    return [c[1] for c in client.calls if c[0] == "chain"]


def bars_of(client):
    return [c for c in client.calls if c[0] == "bars"]


EVEN = {r: (15, 30) for r in ROLES}


@pytest.fixture
def db(tmp_path):
    return tmp_path / "scalp.db"


@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


@pytest.fixture(autouse=True)
def _fresh_degrades():
    _degrade.reset()
    yield
    _degrade.reset()


def _seeded(db, study=None, edges=None):
    seed_bars(db)
    for sym in PRICES:
        for i, bucket in enumerate(scalp_model.BUCKETS):
            # Each bucket on its own days: a row is keyed by (day, slot, ...).
            seed_study(db, sym, (study or {}).get(sym, EVEN), bucket=bucket,
                       edges=(edges or {}).get(sym, ()), start=1000 * (i + 1))


def _floats_are_plain(node):
    if isinstance(node, dict):
        return all(_floats_are_plain(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        return all(_floats_are_plain(v) for v in node)
    return not isinstance(node, float) or type(node) is float


def _intraday(bus):
    env = bus.cache_get(sic.VIEW)
    return None if env is None else env.payload.get("intraday")


def _roundtrip(x):
    return json.loads(json.dumps(x))


# ── the call ─────────────────────────────────────────────────────────────────

def test_slot_call_published_and_frozen(db, bus, monkeypatch):
    _seeded(db)
    client = FakeClient()
    built = AT + dt.timedelta(seconds=95)          # the wall clock when it was built
    monkeypatch.setattr(sic, "_wall_now", lambda: built)
    assert sic.run_slot(bus, SLOT, now=AT, db_path=db, client=client) is True
    view = bus.cache_get(sic.VIEW).payload
    ScalpView(**view)
    assert _floats_are_plain(view)
    section = view["intraday"]
    assert section["slot"] == SLOT and section["day"] == ISO
    assert set(section["latest"]) == set(PRICES)
    spy = section["latest"]["SPY"]
    assert spy["slot"] == SLOT and spy["block"] == "morning"
    assert spy["direction"] == 1 and spy["bucket"] == bucket_of(db, "SPY")
    assert spy["entry_ts"] == scalp_config.intraday_entry_ts(DAY, SLOT)
    assert spy["entry_spot"] == pytest.approx(spot_at("SPY"))
    assert spy["inputs"] and spy["rates_basis"] == "all stored days"
    assert set(spy["roles"]) == set(ROLES)
    lw = spy["roles"]["long_with"]
    assert lw["structure"] == "long_call" and lw["p"] == 0.5 and lw["n_days"] == 30
    assert lw["p_base"] == 0.5 and lw["n_base_days"] == 90
    assert lw["entry_source"] == "chain" and lw["strikes"]
    assert lw["win_points"] > 0 > lw["loss_points"]
    assert isinstance(lw["wall_context"], list)
    assert spy["sigma_source"] == "chain" and spy["sigma"] == pytest.approx(0.18)
    assert spy["note"] == "no measured edge" and spy["highlight"] is None
    # Changed by the final review's M2: made_at is the wall clock at build, not
    # the scheduler's tick (which only judges the window).
    assert spy["made_at"] == built.isoformat()
    assert spy["rules"] == scalp_call._call_rules()
    assert set(spy["confirmation"]) >= {"flow", "netprem", "dealer", "sentiment",
                                        "score", "word"}
    assert "dealer_line" in spy
    # Frozen exactly as published, with its own chain slice (not the morning's).
    saved = scalp_store.intraday_calls_on(ISO, db_path=db)
    assert set(saved) == {(SLOT, s) for s in PRICES}
    for sym in PRICES:
        assert saved[(SLOT, sym)] == _roundtrip(section["latest"][sym])
        assert scalp_store.intraday_slice(ISO, SLOT, sym, db_path=db) is not None
        assert scalp_store.chain_slice(ISO, sym, db_path=db) is None
    assert sorted(chains_of(client)) == sorted(PRICES)
    assert len(bars_of(client)) == len(scalp_config.bar_symbols())    # one each, today
    for kind, _sym, params, kw in client.calls:
        assert "priority" not in kw and "priority" not in (params or {})


def test_refresh_reuses_a_frozen_slot_whole(db, bus):
    _seeded(db)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    first = _intraday(bus)["latest"]
    slices = {s: scalp_store.intraday_slice(ISO, SLOT, s, db_path=db) for s in PRICES}
    # The study moves and the market moves: a refresh inside the grace must show
    # (and keep) exactly the published call, and fetch nothing.
    seed_study(db, "SPY", {r: (30, 30) for r in ROLES}, edges=ROLES,
               bucket=bucket_of(db, "SPY"), start=500)
    client = FakeClient({s: make_chain(s, iv=30.0, spot=spot_at(s) * 1.01) for s in PRICES})
    later = AT + dt.timedelta(minutes=3)
    assert sic.run_slot(bus, SLOT, now=later, db_path=db, client=client) is True
    assert _intraday(bus)["latest"] == first
    assert client.calls == []
    for sym in PRICES:
        assert scalp_store.intraday_slice(ISO, SLOT, sym, db_path=db) == slices[sym]
    # A later page refresh (no slot in its grace) republishes what exists.
    client = FakeClient()
    ten = dt.datetime(2026, 10, 19, 10, 0, tzinfo=CT)
    assert sic.run_slot(bus, None, now=ten, db_path=db, client=client) is True
    assert _intraday(bus)["latest"] == first and _intraday(bus)["slot"] == SLOT
    assert client.calls == []
    assert scalp_store.intraday_calls_on(ISO, db_path=db)[(SLOT, "SPY")] == first["SPY"]


def test_timeline_lists_every_published_slot(db, bus):
    _seeded(db)
    sic.run_slot(bus, "09:06", now=AT, db_path=db, client=FakeClient())
    later = dt.datetime(2026, 10, 19, 9, 21, 10, tzinfo=CT)
    sic.run_slot(bus, "09:21", now=later, db_path=db, client=FakeClient())
    section = _intraday(bus)
    assert section["slot"] == "09:21"
    assert section["latest"]["SPY"]["slot"] == "09:21"
    assert [row["slot"] for row in section["timeline"]] == ["09:06", "09:21"]
    for row in section["timeline"]:
        assert set(row["symbols"]) == set(PRICES)
        cell = row["symbols"]["SPY"]
        assert cell["direction"] == 1 and cell["highlight"] is None
        assert {"word", "edge_p", "edge_p_base"} <= set(cell)
    assert section["latest"]["SPY"]["entry_spot"] == pytest.approx(spot_at("SPY", "09:21"))
    saved = scalp_store.intraday_calls_on(ISO, db_path=db)
    assert {k[0] for k in saved} == {"09:06", "09:21"}
    ScalpView(**bus.cache_get(sic.VIEW).payload)


def test_a_highlight_is_carried_into_the_timeline(db, bus):
    seed_bars(db)
    bucket = bucket_of(db, "SPY")
    for sym in PRICES:
        seed_study(db, sym, EVEN, bucket=bucket)
    other = next(b for b in scalp_model.BUCKETS if b != bucket)
    seed_study(db, "SPY", {"long_with": (24, 30)}, edges=("long_with",), bucket=bucket,
               start=100)
    seed_study(db, "SPY", {"long_with": (6, 30)}, bucket=other, start=200)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    section = _intraday(bus)
    spy = section["latest"]["SPY"]
    assert spy["highlight"] == "long_with" and spy["note"] is None
    cell = section["timeline"][0]["symbols"]["SPY"]
    assert cell["highlight"] == "long_with"
    assert cell["edge_p"] == spy["roles"]["long_with"]["p"]
    assert cell["edge_p_base"] == spy["roles"]["long_with"]["p_base"]
    assert cell["edge_p"] > cell["edge_p_base"]
    # the timeline carries the day count behind the highlighted rate (days, not rows)
    assert cell["n_days"] == spy["roles"]["long_with"]["n_days"] > 0
    plain = section["timeline"][0]["symbols"]["QQQ"]
    assert plain["highlight"] is None and plain["n_days"] is None
    ScalpView(**bus.cache_get(sic.VIEW).payload)


def test_no_new_call_after_the_slot_window(db, bus, monkeypatch):
    _seeded(db)
    client = FakeClient()
    # The 09:06 slot enters at 09:05; with the 60-minute time stop its trade is
    # over at 10:05. A build after that reads the inputs and makes no call.
    late = dt.datetime(2026, 10, 19, 10, 6, tzinfo=CT)
    out = sic.build_slot(DAY, SLOT, late, db_path=db, client=client)
    for sym in PRICES:
        e = out[sym]
        assert e["note"] == sic.MISSED and e["roles"] == {}
        assert e["direction"] == 1 and e["inputs"] and e["highlight"] is None
        assert "made_at" not in e and "rules" not in e
    assert client.calls == []
    # Even when the grace reaches that far, run_slot freezes nothing late.
    monkeypatch.setattr(scalp_config, "intraday_grace_min", lambda: 120)
    assert sic.run_slot(bus, SLOT, now=late, db_path=db, client=client) is True
    assert {e["note"] for e in _intraday(bus)["latest"].values()} == {sic.MISSED}
    assert scalp_store.intraday_calls_on(ISO, db_path=db) == {}
    assert client.calls == []


def test_flat_last_15_minutes_has_no_direction_note(db, bus):
    seed_bars(db, symbols=("$SPX", "QQQ"), flat=("QQQ",))
    for sym in PRICES:
        seed_study(db, sym, EVEN, bucket="high")
    client = FakeClient()
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=client)
    latest = _intraday(bus)["latest"]
    qqq, spy = latest["QQQ"], latest["SPY"]
    assert qqq["direction"] is None and qqq["roles"] == {}
    assert qqq["note"] == sic.FLAT and qqq["inputs"]
    assert spy["direction"] is None and spy["roles"] == {} and spy["note"] == sic.NO_BARS
    assert latest["$SPX"]["direction"] == 1 and set(latest["$SPX"]["roles"]) == set(ROLES)
    # Only a symbol with a direction is worth a chain.
    assert chains_of(client) == ["$SPX"]
    saved = scalp_store.intraday_calls_on(ISO, db_path=db)
    # A flat tape is a settled answer for the slot; a missing bar is not.
    assert set(saved) == {(SLOT, "$SPX"), (SLOT, "QQQ")}
    row = _intraday(bus)["timeline"][0]["symbols"]
    assert row["QQQ"]["direction"] is None and row["QQQ"]["note"] == sic.FLAT


def test_0836_symbols_untouched_by_an_intraday_publish(db, bus):
    _seeded(db)
    scalp_call.run(bus, now=dt.datetime(2026, 10, 19, 8, 36, tzinfo=CT), db_path=db,
                   client=FakeClient())
    before = bus.cache_get(sic.VIEW).payload
    morning_slices = {s: scalp_store.chain_slice(ISO, s, db_path=db) for s in PRICES}
    morning_calls = scalp_store.calls_on(ISO, db_path=db)
    assert before["symbols"] and "intraday" not in before
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    after = bus.cache_get(sic.VIEW).payload
    assert after["intraday"]["latest"]
    assert json.dumps(after["symbols"], sort_keys=True) == \
        json.dumps(before["symbols"], sort_keys=True)
    assert {k: v for k, v in after.items() if k != "intraday"} == before
    # The 08:36 call's own slices and frozen calls are not touched either.
    for sym in PRICES:
        assert scalp_store.chain_slice(ISO, sym, db_path=db) == morning_slices[sym]
    assert scalp_store.calls_on(ISO, db_path=db) == morning_calls


class _SlowClient(FakeClient):
    """The first chain request blocks until released."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def get_option_chain(self, symbol, **kw):
        if not self.entered.is_set():
            self.entered.set()
            self.release.wait(5)
        return super().get_option_chain(symbol, **kw)


def test_concurrent_0836_and_slot_runs_fetch_once_each(db, bus):
    # The 08:36 call (refreshed at 09:06) holds the lock in its chain fetch while
    # the 09:06 slot fires: the slot waits, then builds and fetches once.
    _seeded(db)
    client = _SlowClient()
    morning = threading.Thread(target=scalp_call.run, args=(bus,),
                               kwargs={"now": AT, "db_path": db, "client": client})
    slot = threading.Thread(target=sic.run_slot, args=(bus, SLOT),
                            kwargs={"now": AT, "db_path": db, "client": client})
    morning.start()
    assert client.entered.wait(5)
    seen = len(client.calls)
    slot.start()
    time.sleep(0.3)             # time enough for an unlocked slot run to fetch
    assert len(client.calls) == seen
    client.release.set()
    morning.join(10)
    slot.join(10)
    assert not morning.is_alive() and not slot.is_alive()
    assert sorted(chains_of(client)) == sorted(list(PRICES) * 2)      # once each
    assert len(bars_of(client)) == \
        len(scalp_config.bar_symbols()) * (len(scalp_fetch.INTERVALS) + 1)
    view = bus.cache_get(sic.VIEW).payload
    morning_saved = scalp_store.calls_on(ISO, db_path=db)
    intraday_saved = scalp_store.intraday_calls_on(ISO, db_path=db)
    for sym in PRICES:
        assert morning_saved[sym] == _roundtrip(view["symbols"][sym])
        assert intraday_saved[(SLOT, sym)] == _roundtrip(view["intraday"]["latest"][sym])


def test_two_slot_runs_at_once_build_once(db, bus, monkeypatch):
    # The scheduler's slot and a page refresh inside its grace, together.
    _seeded(db)
    client = _SlowClient()
    built = AT + dt.timedelta(seconds=7)
    monkeypatch.setattr(sic, "_wall_now", lambda: built)
    first = threading.Thread(target=sic.run_slot, args=(bus, SLOT),
                             kwargs={"now": AT, "db_path": db, "client": client})
    second = threading.Thread(target=sic.run_slot, args=(bus, SLOT),
                              kwargs={"now": AT + dt.timedelta(seconds=40),
                                      "db_path": db, "client": client})
    first.start()
    assert client.entered.wait(5)
    second.start()
    time.sleep(0.3)
    client.release.set()
    first.join(10)
    second.join(10)
    assert sorted(chains_of(client)) == sorted(PRICES)
    assert len(bars_of(client)) == len(scalp_config.bar_symbols())
    latest = _intraday(bus)["latest"]
    saved = scalp_store.intraday_calls_on(ISO, db_path=db)
    for sym in PRICES:
        assert saved[(SLOT, sym)] == _roundtrip(latest[sym])
        # built once: by the first run (M2: made_at is the build's wall clock)
        assert latest[sym]["made_at"] == built.isoformat()


def test_invalid_payload_not_saved(db, bus, monkeypatch):
    _seeded(db)
    real = sic._symbol_call

    def bad(*a, **k):
        entry = real(*a, **k)
        if entry.get("roles"):
            entry["roles"]["long_with"]["p"] = 1.5
        return entry
    monkeypatch.setattr(sic, "_symbol_call", bad)
    recorded = []
    monkeypatch.setattr(scalp_record, "record",
                        lambda *a, **k: recorded.append(k.get("directions")) or 0)
    assert sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient()) is False
    assert bus.cache_get(sic.VIEW) is None
    assert scalp_store.intraday_calls_on(ISO, db_path=db) == {}
    for sym in PRICES:
        assert scalp_store.intraday_slice(ISO, SLOT, sym, db_path=db) is None
    assert _degrade.counts().get("options.scalp_intraday") == 1
    # The slot's raw inputs are still recorded, with no call's direction.
    assert recorded == [{}]


def test_a_call_the_bus_refused_is_not_frozen(db, bus, monkeypatch):
    _seeded(db)

    def refused(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(bus, "cache_set", refused)
    assert sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient()) is False
    assert scalp_store.intraday_calls_on(ISO, db_path=db) == {}
    assert _degrade.counts().get("options.scalp_intraday") == 1


def test_record_called_after_publish_with_directions(db, bus, monkeypatch):
    _seeded(db)
    seen = {}

    def record(b, day, slot, now=None, db_path=None, directions=None, win_points=None,
               entry_ts=None, src=None, spots=None):
        seen.update(day=day, slot=slot, directions=directions, win_points=win_points,
                    entry_ts=entry_ts, db_path=db_path, spots=spots,
                    published=_intraday(b) is not None,
                    saved=bool(scalp_store.intraday_calls_on(ISO, db_path=db_path)))
        raise RuntimeError("a record failure")
    monkeypatch.setattr(scalp_record, "record", record)
    assert sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient()) is True
    assert seen["published"] and seen["saved"]
    assert seen["day"] == DAY and seen["slot"] == SLOT and seen["db_path"] == db
    assert seen["directions"] == {s: 1 for s in PRICES}
    latest = _intraday(bus)["latest"]
    for sym in PRICES:
        lw = latest[sym]["roles"]["long_with"]["win_points"]
        assert seen["win_points"][sym] == pytest.approx(abs(lw)) and lw > 0
        # the dealer read's room is measured from the call's own entry price
        assert seen["spots"][sym] == latest[sym]["entry_spot"]
    assert seen["entry_ts"] == scalp_config.intraday_entry_ts(DAY, SLOT)
    # The failure is counted and touches nothing already published or saved.
    assert _degrade.counts().get("options.scalp_intraday") == 1
    assert len(scalp_store.intraday_calls_on(ISO, db_path=db)) == 3


def test_the_record_carries_a_down_calls_points_as_a_magnitude(db, bus, monkeypatch):
    # A down move's long-with role is a long put: its win points are negative,
    # and the dealer read and the record take the distance, not the sign.
    rows = []
    for sym in PRICES:
        prices = [PRICES[sym] * (1 - 0.001 * i) for i in range(N_BARS + 1)]
        for i in range(N_BARS):
            o, c = prices[i], prices[i + 1]
            minute = 30 + 5 * i
            rows.append((sym, 5, ts(8 + minute // 60, minute % 60), o, max(o, c),
                         min(o, c), c, 1000.0))
        rows += [("$VIX", 5, ts(8 + (30 + 5 * i) // 60, (30 + 5 * i) % 60),
                  20.0, 20.0, 20.0, 20.0, 0.0) for i in range(N_BARS)]
    scalp_store.upsert_bars(rows, db_path=db)
    seen = {}
    monkeypatch.setattr(scalp_record, "record",
                        lambda *a, **k: seen.update(k) or 0)
    chains = {s: make_chain(s, spot=PRICES[s] * (1 - 0.007)) for s in PRICES}
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient(chains))
    spy = _intraday(bus)["latest"]["SPY"]
    assert spy["direction"] == -1
    lw = spy["roles"]["long_with"]
    assert lw["structure"] == "long_put" and lw["win_points"] < 0
    assert seen["directions"]["SPY"] == -1
    assert seen["win_points"]["SPY"] == pytest.approx(-lw["win_points"])
    assert spy["confirmation"]["dealer"]["raw"].get("state") == "absent"


def test_a_refreshed_slot_upgrades_its_directionless_record(db, bus, monkeypatch):
    # The scheduled run could not publish (the bus refused): its rows were
    # recorded with no direction. The refresh inside the window publishes the
    # call, and the rows then carry each call's direction.
    _seeded(db)
    real_set = bus.cache_set

    def refused(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(bus, "cache_set", refused)
    assert sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient()) is False
    rows = scalp_store.readings_on(ISO, db_path=db)
    assert {rows[(SLOT, s)]["direction"] for s in PRICES} == {None}
    monkeypatch.setattr(bus, "cache_set", real_set)
    later = AT + dt.timedelta(minutes=2)
    assert sic.run_slot(bus, SLOT, now=later, db_path=db, client=FakeClient()) is True
    rows = scalp_store.readings_on(ISO, db_path=db)
    latest = _intraday(bus)["latest"]
    for sym in PRICES:
        assert rows[(SLOT, sym)]["direction"] == latest[sym]["direction"] == 1
        assert rows[(SLOT, sym)]["recorded_at"] == later.isoformat()
    # Once it has a direction, a later refresh never rewrites it.
    first = scalp_store.readings_on(ISO, db_path=db)
    sic.run_slot(bus, SLOT, now=later + dt.timedelta(minutes=1), db_path=db,
                 client=FakeClient())
    assert scalp_store.readings_on(ISO, db_path=db) == first


def test_a_call_names_the_inputs_whose_bars_failed(db, bus):
    _seeded(db)

    class Partial(FakeClient):
        def _get(self, path, params=None, **kw):
            sym = dict(kv.split("=", 1) for kv in params["params"].split(","))["symbol"]
            if sym in ("XLV", "/NQ"):
                self.calls.append(("bars", path, params, kw))
                return _Resp(500, None)
            return super()._get(path, params, **kw)

    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=Partial())
    latest = _intraday(bus)["latest"]
    assert latest["SPY"]["inputs_failed"] == ["XLV"]          # SPY reads /ES, not /NQ
    assert latest["QQQ"]["inputs_failed"] == ["/NQ"]          # QQQ reads XLK, not XLV
    assert latest["$SPX"]["inputs_failed"] == ["XLV"]
    for sym in PRICES:
        assert "fewer inputs" in latest[sym]["inputs_note"]
        assert all(n in latest[sym]["inputs_note"] for n in latest[sym]["inputs_failed"])
    # With every fetch fine, an entry says it lacked nothing.
    reset_fake_bus()
    clean = Bus(fake=True)
    sic.run_slot(clean, "09:21", now=dt.datetime(2026, 10, 19, 9, 21, tzinfo=CT),
                 db_path=db, client=FakeClient())
    spy = _intraday(clean)["latest"]["SPY"]
    assert spy["inputs_failed"] == [] and spy["inputs_note"] is None
    ScalpView(**clean.cache_get(sic.VIEW).payload)


def test_the_record_is_read_from_the_sources_the_call_showed(db, bus, monkeypatch):
    # A view that moves between the build and the record must not change the
    # recorded components: the row is what the page showed.
    _seeded(db)
    _views(bus, "bullish", 1.05)
    real_save = sic._save

    def save_then_move(*a, **k):
        real_save(*a, **k)
        bus.cache_set("cache:sentiment:composite",
                      {"composite_at": AT.isoformat(),
                       "derived": {"trend": {"state": "bearish", "score": 1.0}}})
    monkeypatch.setattr(sic, "_save", save_then_move)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    shown = _intraday(bus)["latest"]["SPY"]["confirmation"]
    row = scalp_store.readings_on(ISO, db_path=db)[(SLOT, "SPY")]
    assert shown["sentiment"]["raw"]["state"] == "bullish"
    assert row["components"]["sentiment"] == _roundtrip(shown["sentiment"])
    assert row["components"]["score"] == pytest.approx(shown["score"])
    assert row["components"]["word"] == shown["word"]
    assert row["snapshot"]["sentiment"]["state"] == "bullish"


def test_the_dealer_room_is_measured_from_the_calls_entry_spot(db, bus):
    # The matrix is read a grace after the entry bar: its spot (1% higher here)
    # is not the price the call was made at. The room is measured from the call's
    # own entry_spot, in what is shown and what is recorded.
    _seeded(db)
    stamp = AT.isoformat()
    bus.cache_set("cache:options:matrix", {"ts": stamp, "rows": [
        {"symbol": s.lstrip("$"), "spot": spot_at(s) * 1.01, "call_wall": 1.02 * spot_at(s),
         "put_wall": 0.98 * spot_at(s), "flip": 0.99 * spot_at(s), "net_gex": 1e9}
        for s in PRICES]})
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    spy = _intraday(bus)["latest"]["SPY"]
    shown = spy["confirmation"]["dealer"]["raw"]
    assert shown["room_spot_source"] == "entry"
    assert shown["room_spot"] == pytest.approx(spy["entry_spot"])
    assert shown["spot"] == pytest.approx(spot_at("SPY") * 1.01)
    assert shown["room"] == pytest.approx(0.02 * spot_at("SPY") + spot_at("SPY")
                                          - spy["entry_spot"])
    row = scalp_store.readings_on(ISO, db_path=db)[(SLOT, "SPY")]
    assert row["components"]["dealer"]["raw"]["room_spot"] == pytest.approx(spy["entry_spot"])
    assert row["components"]["dealer"]["raw"]["room_spot_source"] == "entry"
    # every source's age is in the row
    assert set(row["snapshot"]["sources"]) == {"matrix", "netprem", "flow", "sentiment",
                                               "flow_sides"}
    assert all("age_min" in v for v in row["snapshot"]["sources"].values())


def _half_day(monkeypatch, close="10:00"):
    monkeypatch.setattr(scalp_config, "early_close_days", lambda: {DAY})
    monkeypatch.setattr(scalp_config, "early_close_time", lambda: close)


def test_no_call_whose_trade_passes_an_early_close(db, bus, monkeypatch):
    # A half day closing at 10:00: the 09:06 slot enters at 09:05 and its 60-minute
    # trade would end at 10:05, after the close. No call; nothing fetched or frozen.
    _seeded(db)
    _half_day(monkeypatch)
    client = FakeClient()
    assert sic.run_slot(bus, SLOT, now=AT, db_path=db, client=client) is True
    section = _intraday(bus)
    for sym in PRICES:
        e = section["latest"][sym]
        assert e["note"] == sic.EARLY_CLOSE == "the market closes early today"
        assert e["roles"] == {} and e["highlight"] is None
        assert "made_at" not in e and "rules" not in e
    assert client.calls == []
    assert scalp_store.intraday_calls_on(ISO, db_path=db) == {}
    # the next call is not today's next slot (every later one is cut too) but
    # the next session's first
    assert section["next_slot"] == dt.datetime(2026, 10, 20, 8, 51, tzinfo=CT).isoformat()
    ScalpView(**bus.cache_get(sic.VIEW).payload)


def test_an_early_close_leaves_the_slots_before_it_alone(db, bus, monkeypatch):
    # The same half day closing at 10:10: 09:06's trade ends 10:05, inside it.
    _seeded(db)
    _half_day(monkeypatch, close="10:10")
    assert sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient()) is True
    section = _intraday(bus)
    assert set(section["latest"]["SPY"]["roles"]) == set(ROLES)
    # 09:21 enters 09:20 and would end 10:20: skipped by next_slot
    assert section["next_slot"] == dt.datetime(2026, 10, 20, 8, 51, tzinfo=CT).isoformat()
    _half_day(monkeypatch, close="12:00")
    assert sic._next_slot(DAY, AT) == dt.datetime(2026, 10, 19, 9, 21, tzinfo=CT).isoformat()


# ── the confirmation is shown, never in the probability ─────────────────────

def _views(bus, state, call_wall):
    stamp = AT.isoformat()
    bus.cache_set("cache:sentiment:composite",
                  {"composite_at": stamp, "derived": {"trend": {"state": state, "score": 5.0}}})
    bus.cache_set("cache:options:matrix", {"ts": stamp, "rows": [
        {"symbol": s.lstrip("$"), "spot": spot_at(s), "call_wall": call_wall * spot_at(s),
         "put_wall": 0.98 * spot_at(s), "flip": 0.99 * spot_at(s), "net_gex": 1e9}
        for s in PRICES]})


def test_confirmation_never_changes_the_probability(tmp_path):
    out = {}
    for name, state, wall in (("for", "bullish", 1.05), ("against", "bearish", 1.0005)):
        db = tmp_path / f"{name}.db"
        _seeded(db)
        reset_fake_bus()
        b = Bus(fake=True)
        _views(b, state, wall)
        sic.run_slot(b, SLOT, now=AT, db_path=db, client=FakeClient())
        out[name] = _intraday(b)["latest"]["SPY"]
    yes, no = out["for"], out["against"]
    assert yes["confirmation"]["sentiment"]["verdict"] == 1
    assert no["confirmation"]["sentiment"]["verdict"] == -1
    assert yes["confirmation"]["score"] > no["confirmation"]["score"]
    assert yes["confirmation"]["word"] != no["confirmation"]["word"]
    assert yes["dealer_line"] and no["dealer_line"] and yes["dealer_line"] != no["dealer_line"]
    for key in ("direction", "bucket", "highlight", "avoid", "note", "sigma"):
        assert yes[key] == no[key]
    for role in ROLES:
        for key in ("p", "lo", "hi", "p_base", "n_days", "edge", "strikes", "entry_value",
                    "win_points", "loss_points"):
            assert yes["roles"][role][key] == no["roles"][role][key]
    # The wall inside the target is named beside the role it is in the way of.
    assert any("call wall" in t for t in no["roles"]["long_with"]["wall_context"])
    assert not any("Target" in t for t in yes["roles"]["long_with"]["wall_context"])


def test_a_highlight_and_an_avoid_never_move_with_the_confirmation(tmp_path):
    # A real edge on each side: long_with beats its base rate (the highlight),
    # long_against reliably loses (avoid). Opposite confirmation inputs change
    # the reading only - never the highlight, the avoid list or the note.
    out = {}
    for name, state, wall in (("for", "bullish", 1.05), ("against", "bearish", 1.0005)):
        db = tmp_path / f"{name}.db"
        seed_bars(db)
        bucket = bucket_of(db, "SPY")
        other = next(b for b in scalp_model.BUCKETS if b != bucket)
        for sym in PRICES:
            seed_study(db, sym, EVEN, bucket=bucket)
        seed_study(db, "SPY", {"long_with": (24, 30), "long_against": (3, 30)},
                   edges=("long_with", "long_against"), bucket=bucket, start=100)
        seed_study(db, "SPY", {"long_with": (6, 30), "long_against": (27, 30)},
                   bucket=other, start=200)
        reset_fake_bus()
        b = Bus(fake=True)
        _views(b, state, wall)
        sic.run_slot(b, SLOT, now=AT, db_path=db, client=FakeClient())
        out[name] = _intraday(b)
    yes, no = out["for"]["latest"]["SPY"], out["against"]["latest"]["SPY"]
    assert yes["highlight"] == "long_with" and yes["avoid"] == ["long_against"]
    assert yes["confirmation"]["word"] == "Confirms"
    assert no["confirmation"]["word"] == "Contradicts"
    for key in ("highlight", "avoid", "note"):
        assert yes[key] == no[key]
    for role in ROLES:
        for key in ("p", "lo", "hi", "p_base", "edge"):
            assert yes["roles"][role][key] == no["roles"][role][key]
    cells = [o["timeline"][0]["symbols"]["SPY"] for o in (out["for"], out["against"])]
    assert cells[0]["highlight"] == cells[1]["highlight"] == "long_with"
    assert cells[0]["edge_p"] == cells[1]["edge_p"]
    assert cells[0]["word"] != cells[1]["word"]


# ── off-slot runs, the refresh and the schedule ──────────────────────────────

def test_refresh_never_makes_an_off_slot_call(db, bus):
    _seeded(db)
    client = FakeClient()
    off = dt.datetime(2026, 10, 19, 9, 12, tzinfo=CT)       # past 09:06 + 5 grace
    assert sic.run_slot(bus, SLOT, now=off, db_path=db, client=client) is False
    assert sic.run_slot(bus, None, now=off, db_path=db, client=client) is False
    assert sic.run_slot(bus, "09:21", now=off, db_path=db, client=client) is False
    assert bus.cache_get(sic.VIEW) is None
    assert client.calls == []
    assert scalp_store.intraday_calls_on(ISO, db_path=db) == {}
    assert sic.current_slot(dt.datetime(2026, 10, 19, 9, 8, tzinfo=CT)) == "09:06"
    assert sic.current_slot(off) is None
    assert sic.current_slot(dt.datetime(2026, 10, 19, 8, 50, tzinfo=CT)) is None
    assert sic.current_slot(dt.datetime(2026, 10, 19, 13, 52, tzinfo=CT)) == "13:51"


def test_a_holiday_or_weekend_does_nothing(db, bus):
    _seeded(db)
    client = FakeClient()
    for when in (dt.datetime(2026, 10, 17, 9, 6, tzinfo=CT),        # Saturday
                 dt.datetime(2026, 11, 26, 9, 6, tzinfo=CT)):       # Thanksgiving
        assert sic.current_slot(when) is None
        assert sic.run_slot(bus, SLOT, now=when, db_path=db, client=client) is False
    assert bus.cache_get(sic.VIEW) is None and client.calls == []


def test_the_refresh_command_also_fills_the_current_slot(bus, monkeypatch):
    order = []
    monkeypatch.setattr(scalp_call, "run", lambda b, **k: order.append(("0836", b)))
    monkeypatch.setattr(sic, "current_slot", lambda now=None: "09:06")
    monkeypatch.setattr(sic, "run_slot", lambda b, slot, **k: order.append((slot, b)))
    fresh = dt.datetime.now(dt.timezone.utc).isoformat()
    handlers.handle_command(bus, Command(type="scalp_refresh", args={}, ts=fresh))
    assert order == [("0836", bus), ("09:06", bus)]
    order.clear()
    monkeypatch.setattr(sic, "current_slot", lambda now=None: None)
    handlers.handle_command(bus, Command(type="scalp_refresh", args={}, ts=fresh))
    # Off a slot it republishes what exists: run_slot with no slot builds nothing.
    assert order == [("0836", bus), (None, bus)]


def test_the_scheduler_branch_runs_the_slot():
    import inspect
    from services.options_svc import scheduler
    src = inspect.getsource(scheduler.loop)
    head, seg = src.split("async def _scalp_intraday_branch", 1)
    seg = seg.split("if intraday_slot:", 1)[0]
    # The tick's own time is bound when the branch is made, and passed on.
    assert seg.startswith("(slot=intraday_slot, at=now)")
    assert "run_in_executor(None, scalp_intraday_call.run_scheduled, bus, slot, at)" in seg
    assert "scalp_record.record" not in seg
    assert "except Exception" in seg


def test_a_scheduled_run_outside_its_window_counts_a_degrade(db, bus):
    _seeded(db)
    client = FakeClient()
    off = dt.datetime(2026, 10, 19, 9, 12, tzinfo=CT)       # past 09:06 + 5 grace
    assert sic.run_scheduled(bus, SLOT, off, db_path=db, client=client) is False
    assert _degrade.counts().get("options.scalp_intraday") == 1
    assert client.calls == [] and bus.cache_get(sic.VIEW) is None
    # A refresh off a window is normal and counts nothing.
    _degrade.reset()
    assert sic.run_slot(bus, SLOT, now=off, db_path=db, client=client) is False
    assert _degrade.counts().get("options.scalp_intraday") is None
    # Inside its window the scheduled entry builds and counts nothing.
    assert sic.run_scheduled(bus, SLOT, AT, db_path=db, client=client) is True
    assert _degrade.counts().get("options.scalp_intraday") is None


def test_run_scheduled_is_run_slot_at_the_tick(monkeypatch):
    seen = []
    monkeypatch.setattr(sic, "run_slot", lambda b, slot, **k: seen.append((b, slot, k)))
    sic.run_scheduled("bus", "09:06", AT)
    assert seen == [("bus", "09:06", {"now": AT, "db_path": None, "client": None,
                                      "scheduled": True})]


def test_modules_do_not_import_compute():
    for name in ("scalp_intraday_call.py", "scalp_chain.py", "scalp_fetch.py"):
        tree = ast.parse((SVC / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [mod] + [f"{mod}.{a.name}" for a in node.names]
            else:
                continue
            assert not any(n == "compute" or n.endswith(".compute") for n in names), \
                f"{name} imports compute"


def test_the_morning_chain_capture_is_still_the_only_writer_of_chain_slices():
    src = (SVC / "scalp_intraday_call.py").read_text(encoding="utf-8")
    assert "scalp_chain.capture" not in src and "save_chain_slice" not in src
    assert "scalp_chain.fetch_slice" in src


# ── the fitted chance of target ──────────────────────────────────────────────

def seed_models(db, kind="intraday", version=1):
    """A hand-made fit: one factor's weight, an intercept per symbol, no refit."""
    body = {"weights": {"price:gap_pct": 0.8}, "intercepts": {s: 0.1 for s in PRICES},
            "scaling": {"price:gap_pct": 1.0}, "days": {"price:gap_pct": 100},
            "n_days": 100, "rows": 400, "priors": {}, "prior_sd": {},
            "walk_forward": {"beats_usual": False}}
    scalp_store.save_prob_models(version, "2026-10-21T20:00:00-05:00",
                                 {(kind, r): body for r in ROLES}, db_path=db)


def test_intraday_call_carries_a_chance(db, bus):
    _seeded(db)
    seed_models(db, version=5)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    view = bus.cache_get(sic.VIEW).payload
    ScalpView(**view)
    latest = view["intraday"]["latest"]
    for sym in PRICES:
        entry = latest[sym]
        assert entry["chance"]["version"] == 5
        for role in ROLES:
            assert 0.0 < entry["roles"][role]["chance"] < 1.0
            assert entry["roles"][role]["chance"] == entry["chance"]["by_role"][role]
    # frozen with the call
    saved = scalp_store.intraday_calls_on(ISO, db_path=db)[(SLOT, "SPY")]
    assert saved["chance"] == _roundtrip(latest["SPY"]["chance"])


def test_the_morning_models_are_not_the_intraday_calls(db, bus):
    _seeded(db)
    seed_models(db, kind="morning")
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    chance = _intraday(bus)["latest"]["SPY"]["chance"]
    assert set(chance["by_role"].values()) == {None}


def test_the_timeline_cell_carries_the_named_trades_chance(db, bus):
    seed_bars(db)
    bucket = bucket_of(db, "SPY")
    for sym in PRICES:
        seed_study(db, sym, EVEN, bucket=bucket)
    other = next(b for b in scalp_model.BUCKETS if b != bucket)
    seed_study(db, "SPY", {"long_with": (24, 30)}, edges=("long_with",), bucket=bucket,
               start=100)
    seed_study(db, "SPY", {"long_with": (6, 30)}, bucket=other, start=200)
    seed_models(db)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    section = _intraday(bus)
    spy = section["latest"]["SPY"]
    assert spy["highlight"] == "long_with"
    cells = section["timeline"][0]["symbols"]
    assert cells["SPY"]["chance"] == spy["chance"]["by_role"]["long_with"]
    # no highlight: the long-with-the-move trade is the one named
    assert cells["QQQ"]["highlight"] is None
    assert cells["QQQ"]["chance"] == section["latest"]["QQQ"]["chance"]["by_role"]["long_with"]
    ScalpView(**bus.cache_get(sic.VIEW).payload)


def test_a_cell_names_the_highlighted_role_else_long_with():
    assert sic._cell({"direction": 1})["chance"] is None
    assert sic._cell({"chance": None})["chance"] is None
    by_role = {"ccs": 0.3, "long_with": 0.6}
    assert sic._cell({"highlight": "ccs", "roles": {"ccs": {}},
                      "chance": {"by_role": by_role}})["chance"] == 0.3
    assert sic._cell({"chance": {"by_role": by_role}})["chance"] == 0.6


def test_an_intraday_entry_with_no_roles_has_a_none_chance(db, bus):
    seed_bars(db, flat=("QQQ",))
    for sym in PRICES:
        seed_study(db, sym, EVEN, bucket="high")
    seed_models(db)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    latest = _intraday(bus)["latest"]
    assert latest["QQQ"]["roles"] == {} and latest["QQQ"]["chance"] is None
    assert latest["SPY"]["chance"]["version"] == 1


def test_switched_off_there_is_no_chance_key(db, bus, monkeypatch):
    _seeded(db)
    seed_models(db)
    monkeypatch.setattr(scalp_config, "prob_show", lambda: False)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    spy = _intraday(bus)["latest"]["SPY"]
    assert "chance" not in spy
    assert all("chance" not in role for role in spy["roles"].values())
    assert _intraday(bus)["timeline"][0]["symbols"]["SPY"]["chance"] is None


def test_a_refit_does_not_change_a_frozen_slot(db, bus):
    _seeded(db)
    seed_models(db, version=1)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    first = _intraday(bus)["latest"]["SPY"]["chance"]
    seed_models(db, version=2)
    sic.run_slot(bus, SLOT, now=AT + dt.timedelta(minutes=3), db_path=db,
                 client=FakeClient())
    assert _intraday(bus)["latest"]["SPY"]["chance"] == first
    assert first["version"] == 1


def test_the_chance_reads_the_snapshot_of_the_sources_the_call_used(db, bus, monkeypatch):
    _seeded(db)
    seed_models(db)
    seen = {}
    real = scalp_call.scalp_prob.chance

    def chance(kind, symbol, entry, snapshot, day, **kw):
        seen[symbol] = (kind, snapshot, kw.get("models"), kw.get("trend_cache"))
        return real(kind, symbol, entry, snapshot, day, **kw)
    monkeypatch.setattr(scalp_call.scalp_prob, "chance", chance)
    sic.run_slot(bus, SLOT, now=AT, db_path=db, client=FakeClient())
    assert set(seen) == set(PRICES)
    assert {v[0] for v in seen.values()} == {"intraday"}
    assert all(isinstance(v[1], dict) and "matrix" in v[1] for v in seen.values())
    # one load of the weights and one trend cache shared by the three symbols
    assert len({id(v[2]) for v in seen.values()}) == 1
    assert len({id(v[3]) for v in seen.values()}) == 1
