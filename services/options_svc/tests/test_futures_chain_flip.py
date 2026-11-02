"""The /ES and /NQ gamma flip: the cumulative rule, writing it into the store, and
the ingest and the page using it. ``tmp_path`` stands in for ``gex_history.db``."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute  # noqa: F401  puts options-scanner on sys.path
from services.options_svc import futures_chain_feed as feed
from services.options_svc import futures_chain_flip as flip_rule
from services.options_svc import futures_chain_snapshot as chain_snapshot
from shared import futures_chain, futures_chain_config
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command

import gamma_tool as gt
import gex_history_db as gh

_CT = ZoneInfo("America/Chicago")
CUMULATIVE = {"rule": "cumulative", "band_pct": 3.0}
PER_STRIKE = {"rule": "per_strike", "band_pct": 3.0}


def grid(nets: dict) -> dict:
    return {float(k): {"call": 0.0, "put": 0.0, "net": float(v)} for k, v in nets.items()}


def es_morning(at_7635: float) -> dict:
    """/ES near the future as captured at 06:12 Central on 2026-11-02 (millions),
    with steady strikes either side. ``at_7635`` is the strike a hair from zero,
    whose sign decides whether the per-strike crossing at 7641 holds."""
    nets = {k: -100.0 for k in range(7600, 7635, 5)}
    nets.update({7635: at_7635, 7640: -13.7, 7645: 24.9, 7650: 104.5,
                 7655: -44.3, 7660: 1784.9})
    nets.update({k: 100.0 for k in range(7665, 7705, 5)})
    return grid(nets)


# ── the rule ─────────────────────────────────────────────────────────────────

def test_the_flip_is_where_the_running_total_changes_sign():
    # Running totals -5, -10, +10: the crossing is between 100 and 110.
    assert flip_rule.cumulative_flip(grid({90: -5, 100: -5, 110: 20}), 105.0, 10.0) == 105.0


def test_one_strike_wobbling_near_the_price_barely_moves_it():
    """What happened on the first live session: one strike's small net changed sign
    between captures. The per-strike flip appears and disappears; this one holds."""
    spot = 7647.75
    before, after = es_morning(-0.7), es_morning(3.0)
    assert gt.calc_flip_point(before, spot) == 7641.77
    assert gt.calc_flip_point(after, spot) is None
    one = flip_rule.cumulative_flip(before, spot, 3.0)
    two = flip_rule.cumulative_flip(after, spot, 3.0)
    assert one is not None and two is not None
    assert abs(one - two) < 1.0


def test_a_crossing_outside_the_band_is_not_a_flip():
    assert flip_rule.cumulative_flip(grid({90: -5, 100: -5, 110: 20}), 200.0, 3.0) is None


def test_the_band_is_read_in_percent():
    nets = grid({90: -5, 100: -5, 110: 20})
    assert flip_rule.cumulative_flip(nets, 120.0, 5.0) is None
    assert flip_rule.cumulative_flip(nets, 120.0, 10.0) == 105.0


def test_the_crossing_nearest_the_price_wins():
    nets = grid({90: -10, 100: 20, 110: -40, 120: 60})
    # Running totals -10, +10, -30, +30: crossings at 95.0, 102.5 and 115.0.
    assert flip_rule.cumulative_flip(nets, 112.0, 10.0) == 115.0
    assert flip_rule.cumulative_flip(nets, 101.0, 10.0) == 102.5


@pytest.mark.parametrize("spot", [None, 0, -1.0, float("nan"), float("inf"), "7600"])
def test_no_usable_price_is_no_flip(spot):
    assert flip_rule.cumulative_flip(grid({90: -5, 110: 20}), spot, 3.0) is None


def test_an_empty_grid_is_no_flip():
    assert flip_rule.cumulative_flip({}, 100.0, 3.0) is None
    assert flip_rule.cumulative_flip(None, 100.0, 3.0) is None


def test_a_strike_with_no_number_is_left_out_of_the_total():
    nets = grid({90: -5, 100: -5, 110: 20})
    nets[95.0] = {"net": float("nan")}
    nets[105.0] = {"net": None}
    assert flip_rule.cumulative_flip(nets, 105.0, 10.0) == 105.0


def test_the_shared_rule_is_kept_when_chosen():
    nets = es_morning(-44.3)
    assert flip_rule.flip_of(nets, 7647.75, PER_STRIKE) == gt.calc_flip_point(nets, 7647.75)


def test_the_shipped_rule_is_cumulative_and_a_bad_one_reads_as_it(monkeypatch):
    assert futures_chain_config.flip() == CUMULATIVE
    monkeypatch.setattr(futures_chain_config, "load",
                        lambda: {"flip": {"rule": "sideways", "band_pct": 99}})
    assert futures_chain_config.flip() == CUMULATIVE


# ── the store ────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "gex_history.db"
    monkeypatch.setattr(gh, "connect", lambda read_only=False: sqlite3.connect(str(path)))
    conn = sqlite3.connect(str(path))
    gh.init_schema(conn)
    yield conn
    conn.close()


def put(conn, symbol, view, ts, spot, flip, nets):
    gh.insert_snapshot(conn, symbol, view, {"ts": ts, "spot": spot, "flip": flip}, nets, 0)


def stored_flip(conn, symbol, view, ts):
    return conn.execute("SELECT flip FROM snapshots WHERE symbol = ? AND view = ? AND ts = ?",
                        (symbol, view, ts)).fetchone()[0]


def test_restamp_rewrites_the_gamma_flip_from_each_rows_own_grid(store):
    nets = es_morning(-44.3)
    put(store, "FOP.ES", "gex", 600, 7647.75, 7775.24, nets)
    put(store, "FOP.ES", "gex", 660, 7647.75, None, nets)
    changed = flip_rule.restamp(store, gh, "FOP.ES", gh.SessionSpan(0, 10_000), CUMULATIVE)
    expected = flip_rule.cumulative_flip(gh.load_date_with_grid(
        store, "FOP.ES", "gex", date=gh.SessionSpan(600, 601))[0][6], 7647.75, 3.0)
    assert changed == 2
    assert stored_flip(store, "FOP.ES", "gex", 600) == expected
    assert stored_flip(store, "FOP.ES", "gex", 660) == expected


def test_restamp_touches_only_that_symbols_gamma_rows_inside_the_span(store):
    nets = es_morning(-44.3)
    put(store, "FOP.ES", "gex", 600, 7647.75, 1.0, nets)
    put(store, "FOP.ES", "gex", 20_000, 7647.75, 2.0, nets)        # outside the span
    put(store, "FOP.ES", "charm", 600, 7647.75, 3.0, nets)        # another view
    put(store, "FOP.NQ", "gex", 600, 7647.75, 4.0, nets)          # another symbol
    flip_rule.restamp(store, gh, "FOP.ES", gh.SessionSpan(0, 10_000), CUMULATIVE)
    assert stored_flip(store, "FOP.ES", "gex", 20_000) == 2.0
    assert stored_flip(store, "FOP.ES", "charm", 600) == 3.0
    assert stored_flip(store, "FOP.NQ", "gex", 600) == 4.0


def test_restamp_counts_only_rows_it_changed(store):
    nets = es_morning(-44.3)
    put(store, "FOP.ES", "gex", 600, 7647.75, None, nets)
    assert flip_rule.restamp(store, gh, "FOP.ES", gh.SessionSpan(0, 10_000), CUMULATIVE) == 1
    assert flip_rule.restamp(store, gh, "FOP.ES", gh.SessionSpan(0, 10_000), CUMULATIVE) == 0


def test_the_stored_flip_is_read_at_its_minute(store):
    put(store, "FOP.ES", "gex", 600, 7647.75, 7656.71, es_morning(-44.3))
    assert flip_rule.stored(store, gh, "FOP.ES", 600) == 7656.71
    assert flip_rule.stored(store, gh, "FOP.ES", 660) is None


# ── the ingest ───────────────────────────────────────────────────────────────

WHEN = datetime(2026, 11, 2, 9, 0, tzinfo=_CT)


@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


def capture(when):
    expiry = futures_chain.session_expiry(when)
    text = (f"/ESZ26:XCME 1/50 {futures_chain.date_text(expiry)} ({expiry:%A}) "
            "/E1AX26:XCME 7600 CALL")
    contracts = [{"series": "E1AX26", "side": "C" if i % 2 == 0 else "P",
                  "strike": 7600 + 5 * (i // 2 - 2), "mark": 10.0, "bid": None,
                  "ask": None, "volume": 5, "open_int": 10, "iv": "15%", "delta": None}
                 for i in range(8)]
    return Command(type="snapshot", args={
        "instrument": "ES", "expiry": expiry.isoformat(), "captured_at": when.timestamp(),
        "descriptions": {"E1AX26": text}, "underlying_symbol": "/ESZ26:XCME",
        "underlying_price": 7600.0, "contracts": contracts})


def ingest(bus, monkeypatch, when=WHEN):
    monkeypatch.setattr(feed, "_now", lambda: when.timestamp() + 5)
    feed.handle(bus, capture(when))


@pytest.fixture
def restamps(monkeypatch):
    calls, real = [], flip_rule.restamp

    def _spy(conn, gex_history, symbol, span, settings=None):
        calls.append((symbol, tuple(span), settings["rule"]))
        return real(conn, gex_history, symbol, span, settings)
    monkeypatch.setattr(flip_rule, "restamp", _spy)
    return calls


def test_the_ingest_writes_the_feeds_flip_for_the_capture_minute(bus, store, restamps,
                                                                monkeypatch):
    ingest(bus, monkeypatch)
    minute = int(WHEN.timestamp()) // 60 * 60
    assert restamps == [("FOP.ES", (minute, minute + 1), "cumulative")]


def test_the_ingest_leaves_the_shared_rules_flip_when_chosen(bus, store, restamps,
                                                            monkeypatch):
    monkeypatch.setattr(futures_chain_config, "flip", lambda: PER_STRIKE)
    ingest(bus, monkeypatch)
    assert restamps == []


# ── the page ─────────────────────────────────────────────────────────────────

def test_the_page_shows_the_stored_flip_and_names_its_rule(bus, store, monkeypatch):
    ingest(bus, monkeypatch)
    minute = int(WHEN.timestamp()) // 60 * 60
    store.execute("UPDATE snapshots SET flip = 7601.5 WHERE view = 'gex' AND ts = ?", (minute,))
    store.commit()
    snap = chain_snapshot.snapshot(bus, "FOP.ES", now=WHEN)
    gex = snap["views"]["GEX"]
    assert gex["flip"] == 7601.5
    assert gex["summary"]["flip"] == 7601.5
    assert gex["summary"]["flip_rule"] == "cumulative"
    assert "flip_rule" not in snap["views"]["Charm"]["summary"]


def test_the_page_keeps_the_engines_flip_under_the_shared_rule(bus, store, monkeypatch):
    monkeypatch.setattr(futures_chain_config, "flip", lambda: PER_STRIKE)
    ingest(bus, monkeypatch)
    minute = int(WHEN.timestamp()) // 60 * 60
    store.execute("UPDATE snapshots SET flip = 7601.5 WHERE view = 'gex' AND ts = ?", (minute,))
    store.commit()
    gex = chain_snapshot.snapshot(bus, "FOP.ES", now=WHEN)["views"]["GEX"]
    assert gex["flip"] != 7601.5
    assert "flip_rule" not in gex["summary"]
