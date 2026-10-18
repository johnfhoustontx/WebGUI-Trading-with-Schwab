"""The Opening Scalp's morning call (scalp_call): build, freeze, publish.

No network and no live store. A fake client answers the proxy's two calls (the
price-history ``/passthrough`` with no candles, so the seeded bars are what the
morning reads; ``get_option_chain`` from a hand-built 0DTE chain), every store
write goes to ``tmp_path``, and the bus is the fake one.

The seeded morning (2026-10-19, a Monday): each symbol's 08:30 bar rises 0.15%
and closes at 0.8 of its range, so the direction is UP and the one vote cast
(where the bar closed) agrees: alignment 1.0, bucket ``high``. $VIX's 08:30 bar
closes at 20, the modelled volatility when no chain is usable. The study's
``outcomes`` and ``verdicts`` rows are written by hand so each role's rate and
edge are known before the call is built.
"""
import ast
import datetime as dt
import json
import pathlib
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import handlers, scalp_call, scalp_chain, scalp_fetch, scalp_store
from services.options_svc.scalp_sim import ROLES
from shared import scalp_config
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command
from shared.contracts.options import ScalpView

CT = ZoneInfo("America/Chicago")
DAY = dt.date(2026, 10, 19)
PREV = dt.date(2026, 10, 16)
NOW = dt.datetime(2026, 10, 19, 8, 36, tzinfo=CT)
PRICES = {"$SPX": 5000.0, "SPY": 500.0, "QQQ": 400.0}
STEP = {"$SPX": 5, "SPY": 1, "QQQ": 1}
SVC = pathlib.Path(__file__).resolve().parents[1]


def ts(day, h, m=0):
    return int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT).timestamp())


def entry_spot(sym):
    return PRICES[sym] * 1.0015


def seed_morning(db, day=DAY, symbols=tuple(PRICES), flat=(), vix=True):
    rows = []
    for sym in symbols:
        p = PRICES[sym]
        bar = (p, p, p, p) if sym in flat else (p, p * 1.002, p * 0.9995, p * 1.0015)
        rows.append((sym, 5, ts(day, 8, 30), *bar, 1000.0))
    if vix:
        rows.append(("$VIX", 5, ts(day, 8, 30), 20.0, 20.0, 20.0, 20.0, 0.0))
    scalp_store.upsert_bars(rows, db_path=db)


def seed_study(db, symbol, rates, edges=(), bucket="high", start=0):
    """``rates``: role -> (wins, n) in ``bucket`` (and a matching base rate);
    ``edges``: the roles whose stored verdict has an edge in ``bucket``."""
    conn = scalp_store.connect(db)
    try:
        with conn:
            for role, (wins, n) in rates.items():
                for i in range(n):
                    day = (dt.date(2025, 1, 1) + dt.timedelta(days=start + i)).isoformat()
                    conn.execute(
                        "INSERT OR REPLACE INTO outcomes VALUES "
                        "(?, ?, 1.0, ?, 'x', ?, ?, 30, 1.0, 0.0, 0, 5)",
                        (day, symbol, role, bucket, "win" if i < wins else "loss"))
            for role in ROLES:
                conn.execute("INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?, ?)",
                             (symbol, role, bucket, json.dumps({"edge": role in edges})))
    finally:
        conn.close()


def _mids(spot, strike, step=1):
    """The test chain's (call mid, put mid) at ``strike``: intrinsic plus a time
    value that shrinks away from the money, so a spread's legs differ."""
    extrinsic = 3.0 - 0.1 * abs(spot - strike) / step
    return max(spot - strike, 0.0) + extrinsic, max(strike - spot, 0.0) + extrinsic


def make_chain(sym, day=DAY, iv=18.0, spot=None, drop=()):
    spot = entry_spot(sym) if spot is None else spot
    step = STEP[sym]
    centre = round(spot / step) * step
    strikes = [centre + step * k for k in range(-12, 13)]

    def side(put):
        out = {}
        for k in strikes:
            if k in drop:
                continue
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
    """The proxy's two calls: price history answers no candles; a chain answers
    from ``chains`` (an Exception instance is raised)."""

    def __init__(self, chains=None):
        self.chains = {s: make_chain(s) for s in PRICES} if chains is None else chains
        self.calls = []

    def _get(self, path, params=None):
        self.calls.append(("bars", path))
        return _Resp(200, {"candles": []})

    def get_option_chain(self, symbol, **kw):
        self.calls.append(("chain", symbol))
        got = self.chains.get(symbol)
        if isinstance(got, Exception):
            raise got
        return _Resp(200, got) if got is not None else _Resp(500, None)


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


def _seeded(db, **study):
    seed_morning(db)
    for sym in PRICES:
        seed_study(db, sym, study.get(sym, EVEN), edges=study.get(sym + ":edges", ()))


def _floats_are_plain(node):
    if isinstance(node, dict):
        return all(_floats_are_plain(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        return all(_floats_are_plain(v) for v in node)
    return not isinstance(node, float) or type(node) is float


# ── the call ─────────────────────────────────────────────────────────────────

def test_a_morning_call_is_built_from_the_store_and_the_live_chain(db):
    _seeded(db)
    payload = scalp_call.build(DAY, NOW, db_path=db, client=FakeClient())
    assert payload["state"] == "call" and payload["day"] == "2026-10-19"
    spy = payload["symbols"]["SPY"]
    assert spy["direction"] == 1 and spy["bucket"] == "high"
    assert spy["alignment"] == 1.0
    assert set(spy["roles"]) == set(ROLES)
    lw = spy["roles"]["long_with"]
    assert lw["structure"] == "long_call" and lw["strikes"] == [501.0]
    assert lw["p"] == 0.5 and lw["n"] == 30 and lw["p_base"] == 0.5
    # Live premium: the chain's mid at the role's strike.
    assert lw["entry_source"] == "chain"
    assert lw["entry_value"] == pytest.approx(_mids(entry_spot("SPY"), 501.0)[0])
    pcs = spy["roles"]["credit_with"]                    # put credit spread 500/499
    assert pcs["strikes"] == [500.0, 499.0]
    s = entry_spot("SPY")
    assert pcs["entry_value"] == pytest.approx(_mids(s, 500.0)[1] - _mids(s, 499.0)[1])
    # Points: a long call wins on a rise and loses on a fall.
    assert lw["win_points"] > 0 > lw["loss_points"]
    assert spy["chain"]["atm_iv"] == pytest.approx(0.18)
    assert spy["sigma_source"] == "chain"
    ScalpView(**payload)
    assert _floats_are_plain(payload)
    json.dumps(payload, allow_nan=False)


def test_the_call_is_frozen_in_the_store_as_built(db):
    _seeded(db)
    payload = scalp_call.build(DAY, NOW, db_path=db, client=FakeClient())
    saved = scalp_store.calls_on("2026-10-19", db_path=db)
    assert set(saved) == set(PRICES)
    assert saved["SPY"] == json.loads(json.dumps(payload["symbols"]["SPY"]))


def test_a_refresh_never_recomputes_the_probability_that_was_shown(db):
    # After 16:20 the rebuild adds today's outcome to the store: a later refresh
    # of the same day must show (and keep) the 08:36 probability.
    _seeded(db)
    first = scalp_call.build(DAY, NOW, db_path=db, client=FakeClient())
    seed_study(db, "SPY", {r: (30, 30) for r in ROLES}, edges=ROLES, start=100)
    later = dt.datetime(2026, 10, 19, 17, 0, tzinfo=CT)
    again = scalp_call.build(DAY, later, db_path=db, client=FakeClient())
    for role in ROLES:
        a, b = first["symbols"]["SPY"]["roles"][role], again["symbols"]["SPY"]["roles"][role]
        assert (a["p"], a["n"], a["edge"]) == (b["p"], b["n"], b["edge"])
    assert again["symbols"]["SPY"]["highlight"] is None
    assert scalp_store.calls_on("2026-10-19", db_path=db)["SPY"]["roles"]["long_with"]["p"] == 0.5


def test_no_edge_sets_note_and_no_highlight(db):
    _seeded(db, SPY={"long_with": (27, 30), "long_against": (3, 30),
                     "credit_with": (15, 30), "credit_against": (15, 30)})
    spy = scalp_call.build(DAY, NOW, db_path=db, client=FakeClient())["symbols"]["SPY"]
    assert spy["highlight"] is None
    assert spy["note"] == "no measured edge"
    assert spy["roles"]["long_with"]["edge"] is False


def test_the_highlight_is_the_edge_role_with_the_highest_rate(db):
    _seeded(db, **{"SPY": {"long_with": (27, 30), "long_against": (21, 30),
                           "credit_with": (18, 30), "credit_against": (15, 30)},
                   "SPY:edges": ("long_against", "credit_with")})
    spy = scalp_call.build(DAY, NOW, db_path=db, client=FakeClient())["symbols"]["SPY"]
    # long_with has the highest rate but no measured edge: it is never the call.
    assert spy["highlight"] == "long_against"
    assert spy["note"] is None


def test_missing_open_bar_symbol_has_note_others_called(db):
    seed_morning(db, symbols=("$SPX", "QQQ"), flat=("QQQ",))
    for sym in PRICES:
        seed_study(db, sym, EVEN)
    out = scalp_call.build(DAY, NOW, db_path=db, client=FakeClient())["symbols"]
    assert out["SPY"]["direction"] is None and out["SPY"]["roles"] == {}
    assert out["SPY"]["note"] == "no opening bar yet"
    assert "inputs" in out["SPY"]
    assert out["QQQ"]["direction"] is None and out["QQQ"]["roles"] == {}
    assert out["QQQ"]["note"] == "opened flat — no direction"
    assert out["$SPX"]["direction"] == 1 and set(out["$SPX"]["roles"]) == set(ROLES)


def test_chain_failure_still_publishes_with_model_points(db, bus):
    _seeded(db)
    chains = {s: make_chain(s) for s in PRICES}
    chains["SPY"] = RuntimeError("proxy down")
    scalp_call.run(bus, now=NOW, db_path=db, client=FakeClient(chains))
    spy = bus.cache_get(scalp_call.VIEW).payload["symbols"]["SPY"]
    assert spy["chain"] is None
    assert spy["sigma_source"] == "model"
    for role in ROLES:
        r = spy["roles"][role]
        assert r["entry_source"] == "model"
        assert r["win_points"] is not None and r["entry_value"] > 0
    # The others still read their own chain.
    qqq = bus.cache_get(scalp_call.VIEW).payload["symbols"]["QQQ"]
    assert qqq["chain"] is not None and qqq["roles"]["long_with"]["entry_source"] == "chain"


def test_a_strike_the_slice_lacks_is_priced_by_the_model(db):
    _seeded(db)
    chains = {s: make_chain(s) for s in PRICES}
    chains["SPY"] = make_chain("SPY", drop=(499.0,))
    spy = scalp_call.build(DAY, NOW, db_path=db, client=FakeClient(chains))["symbols"]["SPY"]
    assert spy["roles"]["credit_with"]["entry_source"] == "model"     # 500/499 put spread
    assert spy["roles"]["long_with"]["entry_source"] == "chain"


def test_no_volatility_anywhere_gives_no_strikes_and_a_note(db):
    seed_morning(db, vix=False)
    for sym in PRICES:
        seed_study(db, sym, EVEN)
    chains = {s: make_chain(s) for s in PRICES}
    chains["SPY"] = RuntimeError("proxy down")
    spy = scalp_call.build(DAY, NOW, db_path=db, client=FakeClient(chains))["symbols"]["SPY"]
    assert spy["points_note"] == scalp_call.NO_VOL
    assert spy["note"] == "no measured edge"
    for role in ROLES:
        r = spy["roles"][role]
        assert r["strikes"] is None and r["win_points"] is None and r["entry_value"] is None
        assert r["p"] == 0.5                       # the measured rate is still shown


def test_the_build_tops_up_bars_and_captures_the_chains(db, monkeypatch):
    _seeded(db)
    seen = []
    real = scalp_fetch.top_up
    monkeypatch.setattr(scalp_fetch, "top_up",
                        lambda **kw: seen.append(kw["today"]) or real(**kw))
    client = FakeClient()
    scalp_call.build(DAY, NOW, db_path=db, client=client)
    assert seen == [DAY]
    assert sorted(s for kind, s in client.calls if kind == "chain") == sorted(PRICES)
    assert scalp_store.chain_slice("2026-10-19", "SPY", db_path=db) is not None


# ── run: states and publishing ───────────────────────────────────────────────

def _no_fetch(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fetched")
    monkeypatch.setattr(scalp_fetch, "top_up", boom)
    monkeypatch.setattr(scalp_chain, "capture_all", boom)
    monkeypatch.setattr(scalp_call, "build", boom)


def test_waiting_before_0835(db, bus, monkeypatch):
    kept = {"direction": 1, "roles": {}, "note": "no measured edge"}
    scalp_store.save_call(PREV.isoformat(), "SPY", kept, db_path=db)
    _no_fetch(monkeypatch)
    client = FakeClient()
    scalp_call.run(bus, now=dt.datetime(2026, 10, 19, 8, 20, tzinfo=CT),
                   db_path=db, client=client)
    view = bus.cache_get(scalp_call.VIEW).payload
    assert view["state"] == "waiting"
    assert view["symbols"] == {"SPY": kept}
    assert view["call_day"] == "2026-10-16" and view["day"] == "2026-10-19"
    assert view["next_call"].startswith("2026-10-19T08:36")
    assert client.calls == []


def test_run_before_0835_does_not_fetch(db, bus, monkeypatch):
    _no_fetch(monkeypatch)
    client = FakeClient()
    for minute in (0, 34):
        scalp_call.run(bus, now=dt.datetime(2026, 10, 19, 8, minute, tzinfo=CT),
                       db_path=db, client=client)
    assert client.calls == []
    assert bus.cache_get(scalp_call.VIEW).payload["state"] == "waiting"


def test_closed_on_holiday(db, bus, monkeypatch):
    kept = {"direction": -1, "roles": {}}
    scalp_store.save_call("2026-11-25", "QQQ", kept, db_path=db)
    _no_fetch(monkeypatch)
    client = FakeClient()
    thanksgiving = dt.datetime(2026, 11, 26, 10, 0, tzinfo=CT)
    scalp_call.run(bus, now=thanksgiving, db_path=db, client=client)
    view = bus.cache_get(scalp_call.VIEW).payload
    assert view["state"] == "closed" and view["symbols"] == {"QQQ": kept}
    assert view["next_call"].startswith("2026-11-27T08:36")
    saturday = dt.datetime(2026, 10, 17, 9, 0, tzinfo=CT)
    scalp_call.run(bus, now=saturday, db_path=db, client=client)
    assert bus.cache_get(scalp_call.VIEW).payload["state"] == "closed"
    assert client.calls == []


def test_a_held_state_with_no_stored_call_keeps_the_current_view(db, bus, monkeypatch):
    bus.cache_set(scalp_call.VIEW, {"state": "call", "day": "2026-10-16",
                                    "symbols": {"SPY": {"direction": 1}}})
    _no_fetch(monkeypatch)
    scalp_call.run(bus, now=dt.datetime(2026, 10, 19, 7, 0, tzinfo=CT), db_path=db,
                   client=FakeClient())
    view = bus.cache_get(scalp_call.VIEW).payload
    assert view["state"] == "waiting" and view["symbols"] == {"SPY": {"direction": 1}}


def test_after_0835_the_call_is_built_and_published(db, bus):
    _seeded(db)
    scalp_call.run(bus, now=NOW, db_path=db, client=FakeClient())
    env = bus.cache_get(scalp_call.VIEW)
    assert env.payload["state"] == "call"
    assert set(env.payload["symbols"]) == set(PRICES)
    assert env.payload["context"]["report_url"] == scalp_config.report_url()
    ScalpView(**env.payload)


def test_an_unexpected_failure_publishes_error_with_the_last_call(db, bus, monkeypatch):
    kept = {"direction": 1, "roles": {}}
    scalp_store.save_call(PREV.isoformat(), "SPY", kept, db_path=db)

    def broken(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(scalp_call, "build", broken)
    scalp_call.run(bus, now=NOW, db_path=db, client=FakeClient())
    view = bus.cache_get(scalp_call.VIEW).payload
    assert view["state"] == "error" and view["symbols"] == {"SPY": kept}
    assert _degrade.counts().get("options.scalp_call") == 1


def test_invalid_payload_not_published(db, bus, monkeypatch):
    bad = {"day": "2026-10-19", "generated_at": NOW.isoformat(), "state": "call",
           "symbols": {"SPY": {"direction": 1, "roles": {"long_with": {"p": 1.5}}}},
           "context": {}, "scorecard": {}}
    monkeypatch.setattr(scalp_call, "build", lambda *a, **k: dict(bad))
    scalp_call.run(bus, now=NOW, db_path=db, client=FakeClient())
    assert bus.cache_get(scalp_call.VIEW) is None
    assert _degrade.counts().get("options.scalp_view") == 1


# ── the context block ────────────────────────────────────────────────────────

def _item(title, tickers):
    return {"title": title, "url": f"https://x/{title}", "source": "wire",
            "published_at": "2026-10-19T13:00:00Z", "tickers": tickers, "teaser": "t"}


def test_news_prefers_index_headlines_else_the_newest(bus):
    bus.cache_set("cache:news:feed", {"items": [
        _item("a", ["AAPL"]), _item("b", ["SPY"]), _item("c", ["NVDA"]),
        _item("d", ["QQQ", "MSFT"]), "junk"]})
    news = scalp_call.context(bus, DAY)["news"]
    assert [n["title"] for n in news] == ["b", "d"]
    assert set(news[0]) == {"title", "url", "source", "published_at", "tickers"}
    bus.cache_set("cache:news:feed", {"items": [_item(str(i), ["AAPL"]) for i in range(9)]})
    news = scalp_call.context(bus, DAY)["news"]
    assert [n["title"] for n in news] == ["0", "1", "2", "3", "4"]       # news.items = 5


def test_flow_is_todays_alerts_on_the_three_symbols_since_0830(bus):
    def alert(sym, h, m, day=DAY):
        return {"id": f"{sym}{h}{m}", "symbol": sym, "ts": ts(day, h, m), "type": "uoa",
                "side": "call", "text": f"{sym} alert"}
    bus.cache_set("cache:options:flow_alerts", {"date": "2026-10-19", "alerts": [
        alert("SPY", 8, 29), alert("SPY", 8, 31), alert("AAPL", 8, 40),
        alert("$SPX", 9, 0), alert("QQQ", 10, 0)]})
    flow = scalp_call.context(bus, DAY)["flow"]
    assert [(f["symbol"], f["ts"]) for f in flow] == [
        ("SPY", ts(DAY, 8, 31)), ("$SPX", ts(DAY, 9)), ("QQQ", ts(DAY, 10))]
    bus.cache_set("cache:options:flow_alerts", {"date": "2026-10-16",
                                                "alerts": [alert("SPY", 9, 0)]})
    assert scalp_call.context(bus, DAY)["flow"] == []


def test_a_missing_or_malformed_source_is_an_empty_list(bus):
    ctx = scalp_call.context(bus, DAY)
    assert ctx == {"report_url": scalp_config.report_url(), "news": [], "flow": []}
    bus.cache_set("cache:news:feed", {"items": "nope"})
    bus.cache_set("cache:options:flow_alerts", {"date": "2026-10-19", "alerts": [None, 3]})
    ctx = scalp_call.context(bus, DAY)
    assert ctx["news"] == [] and ctx["flow"] == []


# ── the refresh command and the module rules ────────────────────────────────

def test_refresh_is_replay_guarded_and_slow_lane(bus, monkeypatch):
    assert "scalp_refresh" in handlers._COMMANDS
    assert "scalp_refresh" in handlers._REPLAY_GUARDED
    assert "scalp_refresh" in handlers.SLOW_LANE
    assert "scalp_refresh" in handlers._STALE_WHY
    ran = []
    monkeypatch.setattr(scalp_call, "run", lambda b, **k: ran.append(b))
    fresh = dt.datetime.now(dt.timezone.utc).isoformat()
    handlers.handle_command(bus, Command(type="scalp_refresh", args={}, ts=fresh))
    assert ran == [bus]
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    handlers.handle_command(bus, Command(type="scalp_refresh", args={}, ts=stale))
    assert ran == [bus]                              # a stale click never re-fetches


def test_the_scheduler_slot_runs_the_call():
    import inspect
    from services.options_svc import scheduler
    src = inspect.getsource(scheduler.loop)
    assert "scalp_call.run" in src


def test_scalp_modules_do_not_import_compute():
    files = sorted(SVC.glob("scalp_*.py"))
    assert len(files) >= 8
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [mod] + [f"{mod}.{a.name}" for a in node.names]
            else:
                continue
            assert not any(n == "compute" or n.endswith(".compute") for n in names), \
                f"{path.name} imports compute"

