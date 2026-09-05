"""``handlers.publish_income`` — the Tier-2 half of the 30-45 DTE income window.

``compute.income_scan`` answers for ONE symbol (``swing_scan``'s per-symbol shape:
``signals`` / ``view`` / ``filtered_out``). This publisher runs it across the
autoscan's watchlist and merges every symbol's rows into ONE jointly-ranked
``candidates`` list under ``cache:options:income`` — hence the deliberate naming
split the ``IncomeScan`` contract documents.

Every test monkeypatches ``handlers.compute.income_scan`` so nothing touches a
live proxy. ``Bus(fake=True)`` shares one ``FakeServer`` per test (as prod shares
one Redis), so a second Bus here sees the first one's writes.
"""
import datetime as _dt

import pytest

from services import _degrade
from services.options_svc import handlers
from shared.bus import Bus
from shared.contracts.envelope import Command
from shared.contracts.options import IncomeScan


def _row(symbol, kind="PCS", score=50.0):
    """One income candidate, distinguishable by symbol AND by score.

    Distinct rows matter: a merge test over identical rows passes trivially even
    if the publisher drops every symbol but one.
    """
    return {"type": kind, "symbol": symbol, "composite_score": score,
            "short_strike": 100.0, "credit": 1.2}


def _one_row_each(sym, **kw):
    return {"signals": [_row(sym)], "view": {}, "filtered_out": 0}


# ── the view is written, and the version moves ───────────────────────────────

def test_publish_income_writes_the_view_and_bumps_the_version(monkeypatch):
    bus = Bus(fake=True)
    before = bus.cache_version(handlers.CACHE_INCOME)
    monkeypatch.setattr(handlers.compute, "income_scan", _one_row_each)

    handlers.publish_income(bus, symbols=["AAPL", "MSFT"])

    env = bus.cache_get(handlers.CACHE_INCOME)
    assert env.payload["scanned_symbols"] == 2
    assert len(env.payload["candidates"]) == 2
    # The page repaints off ``{key}:ver``, so a publish nothing can see is not a
    # publish. ``before`` is None on a cold key.
    assert bus.cache_version(handlers.CACHE_INCOME) != before


def test_publish_income_publishes_the_event_the_page_subscribes_to(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "income_scan", _one_row_each)
    sub = bus.subscribe(handlers.EVENT_INCOME)

    handlers.publish_income(bus, symbols=["AAPL"])

    assert sub.get_message(timeout=1.0) is not None


# ── one bad symbol must not lose the pass, and must leave a trace ────────────

def test_publish_income_survives_one_symbol_failing(monkeypatch):
    """A swallowed failure with no trace is this repo's costliest bug class."""
    def _scan(sym, **kw):
        if sym == "BAD":
            raise RuntimeError("no chain")
        return {"signals": [_row(sym)], "view": {}, "filtered_out": 0}

    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "income_scan", _scan)

    handlers.publish_income(bus, symbols=["AAPL", "BAD"])

    payload = bus.cache_get(handlers.CACHE_INCOME).payload
    assert len(payload["candidates"]) == 1
    assert payload["errors"], "a swallowed symbol failure must leave a trace"
    assert "BAD" in " ".join(str(e) for e in payload["errors"])


def test_a_failed_symbol_is_counted_for_health(monkeypatch):
    """The errors list tells the PAGE; ``_degrade`` tells ``/health``. One
    degrade is noise, twenty-three in a pass is a proxy outage — and only the
    counter can say which."""
    def _scan(sym, **kw):
        raise RuntimeError("no chain")

    _degrade.reset()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "income_scan", _scan)

    handlers.publish_income(bus, symbols=["AAPL"])

    assert _degrade.counts().get("options.publish_income") == 1


def test_scanned_symbols_counts_what_was_ATTEMPTED(monkeypatch):
    """Coverage, not success. Reporting 1-of-1 after 22 symbols failed would
    read as a thin tape rather than a broken pass."""
    def _scan(sym, **kw):
        if sym == "AAPL":
            return {"signals": [_row(sym)], "view": {}, "filtered_out": 0}
        raise RuntimeError("no chain")

    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "income_scan", _scan)

    handlers.publish_income(bus, symbols=["AAPL", "BAD", "WORSE"])

    assert bus.cache_get(handlers.CACHE_INCOME).payload["scanned_symbols"] == 3


# ── the merge is real, not a repeated single symbol ──────────────────────────

def test_the_merge_carries_DISTINCT_rows_from_DISTINCT_symbols(monkeypatch):
    """Vacuity guard. "Two symbols → two candidates" passes even when the
    publisher scans one symbol twice, so assert the rows are actually the two
    symbols' own."""
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": [_row(sym, kind=f"K-{sym}")],
                                           "view": {}, "filtered_out": 0})
    bus = Bus(fake=True)

    handlers.publish_income(bus, symbols=["AAPL", "MSFT"])

    rows = bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]
    assert {(r["symbol"], r["type"]) for r in rows} == {("AAPL", "K-AAPL"),
                                                       ("MSFT", "K-MSFT")}


def test_the_merged_list_is_ranked_ACROSS_symbols_not_concatenated(monkeypatch):
    """One symbol's second-best must outrank another's best when it scores
    higher. A per-symbol concatenation gives 80, 40, 60 — the interleave is what
    proves the list was re-ranked as one."""
    per_symbol = {
        "AAPL": [_row("AAPL", score=80.0), _row("AAPL", score=40.0)],
        "MSFT": [_row("MSFT", score=60.0)],
    }
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": list(per_symbol[sym]),
                                           "view": {}, "filtered_out": 0})
    bus = Bus(fake=True)

    handlers.publish_income(bus, symbols=["AAPL", "MSFT"])

    rows = bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]
    assert [r["composite_score"] for r in rows] == [80.0, 60.0, 40.0]


def test_a_row_with_no_score_sorts_last_rather_than_crashing(monkeypatch):
    """``score_all`` neutralizes an unscorable signal to 0.0, but the key can be
    absent entirely on a shape drift — and a merge that raises loses the whole
    pass for one bad row."""
    per_symbol = {"AAPL": [{"type": "PCS", "symbol": "AAPL"}],
                  "MSFT": [_row("MSFT", score=10.0)]}
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": list(per_symbol[sym]),
                                           "view": {}, "filtered_out": 0})
    bus = Bus(fake=True)

    handlers.publish_income(bus, symbols=["AAPL", "MSFT"])

    rows = bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]
    assert [r["symbol"] for r in rows] == ["MSFT", "AAPL"]


# ── the contract, and what must NOT ride along ───────────────────────────────

def test_the_payload_validates_as_IncomeScan(monkeypatch):
    monkeypatch.setattr(handlers.compute, "income_scan", _one_row_each)
    bus = Bus(fake=True)

    handlers.publish_income(bus, symbols=["AAPL"])

    payload = bus.cache_get(handlers.CACHE_INCOME).payload
    snap = IncomeScan(**payload)          # raises on gross drift
    assert snap.scanned_symbols == 1
    assert snap.ts


def test_the_chain_never_rides_along(monkeypatch):
    """``cache:options:calc_chain`` reached 8.77 MB — 53% of all prod Redis
    string bytes — by caching a raw chain beside what the page reads. A 30-45
    DTE chain is WIDER than the 0-DTE one that caused it."""
    fat = {"signals": [_row("AAPL")], "filtered_out": 0,
           "view": {"bias": "neutral"},
           # ``income_scan`` does not return these today; the guard is against a
           # future one that does, since a publisher that forwards its input
           # wholesale would happily carry them.
           "chain": {"callExpDateMap": {"2026-10-17:42": {"100.0": [{}]}}}}
    monkeypatch.setattr(handlers.compute, "income_scan", lambda sym, **kw: dict(fat))
    bus = Bus(fake=True)

    handlers.publish_income(bus, symbols=["AAPL"])

    blob = repr(bus.cache_get(handlers.CACHE_INCOME).payload)
    for chain_shaped in ("callExpDateMap", "putExpDateMap"):
        assert chain_shaped not in blob


# ── the universe ─────────────────────────────────────────────────────────────

def test_the_default_universe_is_the_autoscan_watchlist(monkeypatch):
    """Not a list invented here: ``run_full_scan`` scans ``get_scan_symbols()``,
    and an income board over a different universe than the scanner's is a
    silently different product."""
    seen = []
    monkeypatch.setattr(handlers, "_income_symbols", lambda: ["AAPL", "MSFT", "NVDA"])
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: (seen.append(sym) or
                                           {"signals": [], "view": {}, "filtered_out": 0}))
    bus = Bus(fake=True)

    handlers.publish_income(bus)

    assert sorted(seen) == ["AAPL", "MSFT", "NVDA"]


def test_income_symbols_reads_the_watchlist_accessor(monkeypatch):
    """``_income_symbols`` must go through options-scanner's cached watchlist
    accessor — the SAME one ``scanner_engine.run_full_scan`` calls."""
    import watchlist

    monkeypatch.setattr(watchlist, "get_scan_symbols", lambda: ["AAPL", "MSFT"])
    assert handlers._income_symbols() == ["AAPL", "MSFT"]


def test_a_watchlist_failure_degrades_to_an_empty_universe(monkeypatch):
    import watchlist

    def _boom():
        raise OSError("workbook gone")

    _degrade.reset()
    monkeypatch.setattr(watchlist, "get_scan_symbols", _boom)
    assert handlers._income_symbols() == []
    assert _degrade.counts().get("options.income_symbols") == 1


# ── the market-state tilt ────────────────────────────────────────────────────

def test_the_committed_market_state_is_threaded_to_every_scan(monkeypatch):
    """``income_scan(market_state=…)`` is the family-ranking tilt the Swing page
    already applies. Unthreaded it would be a dead parameter — this publisher is
    its only caller — and the two boards would rank the same spread differently."""
    seen = []
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, market_state=None: (
                            seen.append(market_state) or
                            {"signals": [], "view": {}, "filtered_out": 0}))
    bus = Bus(fake=True)
    bus.cache_set("cache:sentiment:composite",
                  {"derived": {"trend": {"state": "Bullish"}}})

    handlers.publish_income(bus, symbols=["AAPL", "MSFT"])

    assert seen == ["Bullish", "Bullish"]


def test_a_cold_sentiment_service_means_no_tilt_not_a_crash(monkeypatch):
    seen = []
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, market_state=None: (
                            seen.append(market_state) or
                            {"signals": [], "view": {}, "filtered_out": 0}))
    bus = Bus(fake=True)          # nothing published under cache:sentiment:composite

    handlers.publish_income(bus, symbols=["AAPL"])

    assert seen == [None]


# ── the fan-out ──────────────────────────────────────────────────────────────

def test_the_symbols_are_fanned_out_concurrently(monkeypatch):
    """~23 symbols x one /chains round-trip. The house pattern for an I/O-bound
    proxy loop is ``parallel_map``, pool <= 8 (the proxy still spaces dispatch
    ~0.2 s apart); a serial rewrite would make this pass a minutes-long block."""
    calls = {}
    real = handlers.parallel_map

    def _spy(fn, items, workers=6):
        calls["workers"] = workers
        calls["n"] = len(list(items))
        return real(fn, items, workers=workers)

    monkeypatch.setattr(handlers, "parallel_map", _spy)
    monkeypatch.setattr(handlers.compute, "income_scan", _one_row_each)
    bus = Bus(fake=True)

    handlers.publish_income(bus, symbols=["AAPL", "MSFT", "NVDA"])

    assert calls["n"] == 3
    assert 1 <= calls["workers"] <= 8


# ── the command path ─────────────────────────────────────────────────────────

def test_the_income_scan_command_reaches_the_publisher(monkeypatch):
    ran = []
    monkeypatch.setattr(handlers, "publish_income", lambda bus: ran.append(bus))
    bus = Bus(fake=True)

    handlers.handle_command(bus, Command(type="income_scan"))

    assert ran == [bus]


def test_a_REPLAYED_income_scan_is_refused(monkeypatch):
    """Consumer groups are created at id 0, so a fresh group re-delivers the
    whole backlog — the documented incident where a first launch "burned a day's
    API budget in one go". This pass is ~23 chain fetches whose entire design
    premise is call-count minimisation, so it joins the replay-guarded set."""
    ran = []
    monkeypatch.setattr(handlers, "publish_income", lambda bus: ran.append(bus))
    stale = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(seconds=handlers.STALE_OPEN_MAX_AGE_SEC + 60))
    bus = Bus(fake=True)

    handlers.handle_command(bus, Command(type="income_scan", ts=stale.isoformat()))

    assert ran == [], "a replayed income_scan must not re-spend the Schwab budget"


def test_a_fresh_income_scan_command_still_runs(monkeypatch):
    """The age gate must not swallow the user's own Refresh click."""
    ran = []
    monkeypatch.setattr(handlers, "publish_income", lambda bus: ran.append(bus))
    fresh = _dt.datetime.now(_dt.timezone.utc).isoformat()
    bus = Bus(fake=True)

    handlers.handle_command(bus, Command(type="income_scan", ts=fresh))

    assert len(ran) == 1


@pytest.mark.parametrize("kind", ["income_scan"])
def test_the_command_is_in_the_replay_guarded_set(kind):
    assert kind in handlers._REPLAY_GUARDED

