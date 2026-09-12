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
                        lambda sym, market_state=None, **kw: (
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
                        lambda sym, market_state=None, **kw: (
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

# ── the scheduler branch ─────────────────────────────────────────────────────

def test_the_loop_latches_the_income_slot_at_DISPATCH():
    """``launch_branches`` starts branches as keyed background tasks with a
    still-running skip, so a slow scan can only ever delay ITSELF — but only
    once the slot is marked. Latching inside the branch instead re-fires it on
    the very next tick, which for this pass means re-spending 23 chain calls."""
    import inspect

    from services.options_svc import scheduler

    src = inspect.getsource(scheduler.loop)
    assert "income_ran = set()" in src
    assert "income_slot_due(now, income_ran)" in src
    assert src.index("income_ran.add(") < src.index('branches.append(("income"')


def _drive_one_tick(monkeypatch, **overrides):
    """Run exactly ONE iteration of ``scheduler.loop``; return what it launched.

    Behavioural, not a source grep. The two tests below used to assert only that
    a log STRING appeared in ``inspect.getsource(scheduler.loop)`` — which is
    satisfied by a ``log.exception(...)`` immediately followed by ``raise``.
    Adding exactly that after BOTH of the income guards was measured to pass all
    32 tests in this file, while the gate killed the tick and the branch took its
    task down: precisely the two failures those docstrings name.

    Hermetic in the same way ``test_app``'s loop driver is, and for the same
    reason (see its docstring — a leaked branch runs REAL gamma/manage work on a
    thread that outlives the test). Every ``handlers.*`` name the scheduler can
    submit is stubbed, and the list is DERIVED from the module source rather
    than hand-maintained, so a new branch cannot quietly start running for real.

    ``overrides`` are handler stubs applied AFTER the wholesale no-op pass, so a
    caller's own stub is not silently overwritten by it.

    Returns ``(keys, tasks)`` — the branch keys launched this tick, and
    ``key -> asyncio.Task`` so a caller can inspect how a branch finished.
    """
    import asyncio
    import pathlib
    import re
    from datetime import datetime

    from services.options_svc import compute, scheduler

    src = pathlib.Path(scheduler.__file__).read_text(encoding="utf-8")
    for name in sorted(set(re.findall(r"handlers\.([a-z_]+)", src))):
        monkeypatch.setattr(handlers, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(compute, "reconcile_paper_buying_power", lambda *a, **k: {})
    for name, fn in overrides.items():
        monkeypatch.setattr(handlers, name, fn, raising=False)

    # A fixed in-window weekday. 09:00 CT is deliberate: it is a tick on which
    # the income slot is due AND branches are due both BEFORE it (rescan,
    # analyze) and AFTER it (market_snapshot), which is what makes "the branches
    # around it still ran" an assertion rather than a hope.
    monkeypatch.setattr(scheduler, "_market_now",
                        lambda: datetime(2026, 6, 15, 9, 0, tzinfo=scheduler._CT))

    keys, tasks = [], {}
    _real_launch = scheduler.launch_branches

    def _record(running, branches, create_task):
        launched = _real_launch(running, branches, create_task)
        keys.extend(launched)
        tasks.update({k: running[k] for k in launched})
        return launched

    monkeypatch.setattr(scheduler, "launch_branches", _record)

    async def _boom(*a, **k):          # break out of the infinite loop
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", _boom)

    lp = asyncio.new_event_loop()
    try:
        try:
            lp.run_until_complete(scheduler.loop(Bus(fake=True)))
        except asyncio.CancelledError:
            pass                        # the sleep stub — the tick COMPLETED
        pending = [t for t in asyncio.all_tasks(lp) if not t.done()]
        if pending:
            # Drain the background branches so a task's outcome is inspectable.
            lp.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    finally:
        # Wait for the executor threads BEFORE closing — close() only calls
        # shutdown(wait=False), which returns while they are still running.
        lp.run_until_complete(lp.shutdown_default_executor())
        lp.close()
    return keys, tasks


def test_the_income_branch_runs_OFF_the_event_loop_and_is_guarded(monkeypatch):
    """~23 chain fetches on the event loop would stall every other branch on the
    tick; an unguarded branch would take the loop down with it.

    ``launch_branches``'s docstring states the contract this pins — "each branch
    carries its own try/except, so a task never raises" — so the assertion is on
    how the TASK finished, not on the text of the guard.
    """
    import asyncio
    import threading

    ran_on = []

    def _boom(bus):
        ran_on.append(threading.get_ident())
        raise RuntimeError("the ~23 chain fetches failed")

    keys, tasks = _drive_one_tick(monkeypatch, publish_income=_boom)

    assert "income" in keys, "the branch must be due on this tick"
    assert ran_on, "publish_income never ran"
    assert ran_on[0] != threading.get_ident(), (
        "publish_income ran on the event-loop thread; ~23 chain fetches there "
        "stall every other branch on the tick")

    task = tasks["income"]
    assert task.done()
    assert task.exception() is None, (
        "the branch re-raised — launch_branches' contract is that a task never "
        "does, and an unretrieved exception takes the loop's health with it")
    # The tick itself survived: every branch after income still launched.
    assert "market_snapshot" in keys


def test_the_income_gate_cannot_skip_the_branches_around_it(monkeypatch):
    """A raising gate must degrade to a falsy slot, not propagate.

    Driven from the GATE rather than the source: ``income_slot_due`` reads a
    module-level slot table and a ``ran`` set, so a malformed
    ``config/sessions.toml`` is a real way for it to raise mid-tick.
    """
    def _boom(now, ran):
        raise ValueError("malformed [slots.income]")

    from services.options_svc import scheduler
    monkeypatch.setattr(scheduler, "income_slot_due", _boom)

    # Reaching here at all is half the assertion: a propagating gate escapes
    # _drive_one_tick as ValueError instead of the sleep stub's CancelledError.
    keys, _tasks = _drive_one_tick(monkeypatch)

    assert "income" not in keys, "a raising gate must degrade to a falsy slot"
    # The branches on BOTH sides of the gate still ran.
    assert "rescan" in keys
    assert "analyze" in keys
    assert "market_snapshot" in keys


# ── B2: the volatility floor's count reaches the published view ──────────────

def test_publish_income_sums_the_volatility_drops_across_the_watchlist(monkeypatch):
    """The producer side of the page's "N too cheap to sell" line.

    ⚠ Driven from the PUBLISHER, not from ``status_text``: a consumer-side
    assertion proves nothing until a test shows the producer actually emits the
    shape the consumer tests — the exact lesson behind the `signal_band` incident
    (a correct guard over a payload the service never wrote).
    """
    def _scan(sym, **kw):
        return {"signals": [_row(sym)], "view": {}, "filtered_out": 0,
                "vol_filtered": {"AAPL": 3, "MSFT": 2, "NVDA": 0}[sym]}

    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "income_scan", _scan)

    handlers.publish_income(bus, symbols=["AAPL", "MSFT", "NVDA"])

    assert bus.cache_get(handlers.CACHE_INCOME).payload["vol_filtered"] == 5


def test_publish_income_reports_zero_when_the_floor_dropped_nothing(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "income_scan", _one_row_each)

    handlers.publish_income(bus, symbols=["AAPL"])

    assert bus.cache_get(handlers.CACHE_INCOME).payload["vol_filtered"] == 0


def test_a_failed_symbol_contributes_no_volatility_count(monkeypatch):
    """A symbol that raised told us nothing about volatility - counting it would
    read as a refusal where there was an outage. It is already in ``errors``."""
    def _scan(sym, **kw):
        if sym == "BAD":
            raise RuntimeError("chain fetch failed")
        return {"signals": [], "view": {}, "filtered_out": 0, "vol_filtered": 4}

    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "income_scan", _scan)

    handlers.publish_income(bus, symbols=["GOOD", "BAD"])

    payload = bus.cache_get(handlers.CACHE_INCOME).payload
    assert payload["vol_filtered"] == 4
    assert len(payload["errors"]) == 1
