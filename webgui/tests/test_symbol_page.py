"""The Symbol Dossier page (``/symbol``) — its pure helpers and its wiring rules.

``render()`` is widgets and wiring; every decision it makes lives in a pure
helper in ``pages/symbol.py`` so it can be pinned here without a browser. The
one decision that costs money — whether to enqueue a ``dossier`` fetch — is
pinned twice: as a pure function, and at SOURCE level on the poll callback, so
a refactor cannot quietly start fetching on the 2-second timer.
"""
import ast
import inspect
import pathlib

import pytest

from pages import copy as _copy
from pages import symbol as sp
from pages import symbol_facts as sf

_SRC = pathlib.Path(sp.__file__).read_text(encoding="utf-8")
TODAY = "2026-09-18"


# ── the fetch decision ─────────────────────────────────────────────────────

@pytest.mark.parametrize("coverage", [sf.SCANNED, sf.COLLECTED, sf.UNKNOWN])
@pytest.mark.parametrize("have", [False, True])
def test_the_poll_never_enqueues_for_any_coverage(coverage, have):
    # Each fetch is 4-5 Schwab calls against the budget the 1-minute GEX poll
    # depends on. A clock must never be what spends them.
    assert sp.should_enqueue(coverage, "poll", have_dossier=have) is False


@pytest.mark.parametrize("trigger", ["navigate", "refresh"])
def test_a_scanned_symbol_never_enqueues(trigger):
    assert sp.should_enqueue(sf.SCANNED, trigger) is False


@pytest.mark.parametrize("coverage", [sf.COLLECTED, sf.UNKNOWN])
@pytest.mark.parametrize("trigger", ["navigate", "refresh"])
def test_collected_and_unknown_enqueue_on_navigate_and_refresh(coverage, trigger):
    assert sp.should_enqueue(coverage, trigger) is True


@pytest.mark.parametrize("coverage", [sf.COLLECTED, sf.UNKNOWN])
def test_navigation_reuses_a_dossier_still_in_the_cache(coverage):
    # The service keeps a dossier 15 minutes precisely so a repeat lookup inside
    # that window spends nothing — but only the page can decide not to ask.
    assert sp.should_enqueue(coverage, "navigate", have_dossier=True) is False


@pytest.mark.parametrize("coverage", [sf.COLLECTED, sf.UNKNOWN])
def test_refresh_is_the_explicit_re_fetch(coverage):
    assert sp.should_enqueue(coverage, "refresh", have_dossier=True) is True


@pytest.mark.parametrize("trigger", ["navigate", "refresh"])
def test_a_cold_options_feed_enqueues_nothing(trigger):
    # The service that would answer the fetch is the one that is not publishing.
    assert sp.should_enqueue(sf.UNKNOWN, trigger, feed_cold=True) is False


@pytest.mark.parametrize("trigger", ["", None, "timer", "NAVIGATE"])
def test_an_unrecognised_trigger_enqueues_nothing(trigger):
    assert sp.should_enqueue(sf.UNKNOWN, trigger) is False


def test_a_failed_fetch_is_not_worth_keeping_but_a_no_quote_is():
    # A fetch that could not reach Schwab may succeed a minute later; a typo
    # will not, and must not re-spend on every visit.
    assert sp.reusable_dossier({"error": None, "spot": 10.0}) is True
    assert sp.reusable_dossier({"error": "no_quote"}) is True
    assert sp.reusable_dossier({"error": "fetch_failed"}) is False
    assert sp.reusable_dossier(None) is False


@pytest.mark.parametrize("raw,expected", [
    ("mu", "MU"), (" nvda ", "NVDA"), ("$SPX", "$SPX"), ("BRK.B", "BRK.B"),
])
def test_the_fetch_command_carries_the_allow_listed_symbol(raw, expected):
    assert sp.fetch_command(raw) == {"type": "dossier",
                                     "args": {"symbol": expected}}


@pytest.mark.parametrize("raw", ["", None, "../../etc", "MU NVDA",
                                 "TOOLONGSYMBOL", "cache:options:gamma", ".."])
def test_a_rejected_symbol_builds_no_command_and_no_view(raw):
    # It is interpolated into a Redis KEY NAME. The service allow-lists it too;
    # the page must never be the reason a malformed one is enqueued.
    assert sp.fetch_command(raw) is None
    assert sp.dossier_view(raw) is None


def test_the_dossier_view_is_the_services_per_symbol_key():
    assert sp.dossier_view(" mu ") == "options:dossier:MU"


def _function_source(name):
    tree = ast.parse(_SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(_SRC, node)
    raise AssertionError(f"no function {name!r} in pages/symbol.py")


def test_the_poll_callback_cannot_enqueue_at_source_level():
    """The pure rule above is only half the guard: a refactor could call
    ``bus_client.request`` from the poll directly and never touch it."""
    poll = _function_source("_poll")
    assert "bus_client.request(" not in poll
    assert "_enqueue_fetch(" not in poll
    assert "should_enqueue(" not in poll


def test_every_enqueue_goes_through_the_one_guarded_helper():
    # Exactly one request site, inside _enqueue_fetch, which builds its command
    # with fetch_command (the allow-list) and asks should_enqueue first.
    assert _SRC.count("bus_client.request(") == 1
    helper = _function_source("_enqueue_fetch")
    assert "bus_client.request(" in helper
    assert "fetch_command(" in helper
    assert "should_enqueue(" in helper


def test_the_enqueue_helper_is_called_only_on_navigation_and_refresh():
    calls = [ln.strip() for ln in _SRC.splitlines()
             if "_enqueue_fetch(" in ln and "def _enqueue_fetch" not in ln]
    assert calls, "nothing enqueues — the fetch path is not wired"
    for ln in calls:
        assert '"navigate"' in ln or '"refresh"' in ln, ln


# ── the poll's view set ────────────────────────────────────────────────────

def test_the_poll_reads_the_eleven_shared_views_plus_this_symbols_dossier():
    views = sp.poll_views("MU")
    assert views[:len(sp.VIEWS)] == sp.VIEWS
    assert views[-1] == "options:dossier:MU"
    assert len(sp.VIEWS) == 11              # ten + gex_status (wall freshness)


def test_a_rejected_symbol_polls_no_dossier_view():
    assert sp.poll_views(None) == sp.VIEWS


def test_every_view_repaints_at_least_one_region():
    covered = set()
    for views in sp.REGION_VIEWS.values():
        covered |= set(views)
    assert set(sp.VIEWS) | {sp.DOSSIER} <= covered


def test_a_dossier_bump_repaints_the_fact_bands_and_nothing_else():
    regions = sp.regions_for({"options:dossier:MU"}, "MU")
    assert regions == {"header", "structure", "volatility", "context"}


def test_another_symbols_dossier_repaints_nothing():
    assert sp.regions_for({"options:dossier:NVDA"}, "MU") == set()


def test_a_flow_bump_repaints_only_the_flow_column():
    assert sp.regions_for({"options:flow_alerts"}, "MU") == {"flow"}


# ── the header chip ────────────────────────────────────────────────────────

def _dossier(error=None, fetched_at="2026-09-18T14:32:05"):
    return {"symbol": "XYZQ", "error": error, "fetched_at": fetched_at}


@pytest.mark.parametrize("error", [None, "no_quote", "fetch_failed"])
def test_a_scanned_symbol_always_reads_scanned(error):
    chip = sp.coverage_chip("MU", sf.SCANNED, _dossier(error))
    assert chip["label"] == "SCANNED"
    assert chip["message"] == ""


@pytest.mark.parametrize("coverage,error,label", [
    (sf.COLLECTED, None, "COLLECTED · FETCHED 14:32"),
    (sf.COLLECTED, "no_quote", "COLLECTED"),
    (sf.COLLECTED, "fetch_failed", "FETCH FAILED"),
    (sf.UNKNOWN, None, "FETCHED 14:32"),
    (sf.UNKNOWN, "no_quote", "NOT FOUND"),
    (sf.UNKNOWN, "fetch_failed", "FETCH FAILED"),
])
def test_every_coverage_and_error_combination(coverage, error, label):
    assert sp.coverage_chip("XYZQ", coverage, _dossier(error))["label"] == label


def test_no_quote_on_an_unknown_symbol_says_check_the_symbol():
    chip = sp.coverage_chip("XYZQ", sf.UNKNOWN, _dossier("no_quote"))
    assert chip["message"] == "No quote for XYZQ — check the symbol"


@pytest.mark.parametrize("coverage", [sf.COLLECTED, sf.UNKNOWN])
def test_a_failed_fetch_never_blames_the_symbol(coverage):
    # An outage is not a typo. Telling the user to check a ticker that is fine
    # is the dead-service / quiet-tape confusion pages/copy.py exists for.
    chip = sp.coverage_chip("MU", coverage, _dossier("fetch_failed"))
    assert "check the symbol" not in chip["message"].lower()
    assert "NOT FOUND" not in chip["label"]
    assert chip["message"]


def test_a_fetch_in_flight_says_so():
    chip = sp.coverage_chip("XYZQ", sf.UNKNOWN, None, pending=True)
    assert chip["label"] == "FETCHING"
    assert chip["message"] == "Fetching XYZQ…"


def test_a_collected_symbol_with_no_dossier_reads_collected():
    assert sp.coverage_chip("$VIX", sf.COLLECTED, None)["label"] == "COLLECTED"


def test_a_dossier_without_a_readable_time_still_says_fetched():
    chip = sp.coverage_chip("XYZQ", sf.UNKNOWN, _dossier(fetched_at="garbage"))
    assert chip["label"] == "FETCHED"


def test_a_rejected_ticker_is_not_found_without_a_fetch():
    chip = sp.coverage_chip(None, sf.UNKNOWN, None, raw="../etc")
    assert chip["label"] == "NOT FOUND"
    assert "not a ticker" in chip["message"]


def test_the_bare_route_asks_for_a_ticker_rather_than_failing():
    chip = sp.coverage_chip(None, sf.UNKNOWN, None, raw="")
    assert chip["label"] == ""
    assert "ticker" in chip["message"].lower()


def test_every_chip_tone_is_a_fixed_class():
    tones = set()
    for cov in (sf.SCANNED, sf.COLLECTED, sf.UNKNOWN):
        for err in (None, "no_quote", "fetch_failed"):
            tones.add(sp.coverage_chip("X", cov, _dossier(err))["tone"])
    tones.add(sp.coverage_chip("X", sf.UNKNOWN, None, pending=True)["tone"])
    assert tones <= set(sp.CHIP_TONES.values())


# ── the earnings line ──────────────────────────────────────────────────────

def test_not_listed_reads_not_covered_never_none_scheduled():
    # Conflating these is what makes an earnings gate fail open.
    line = sp.earnings_line(None, "not_listed", TODAY)
    assert "not covered" in line
    assert "none scheduled" not in line.lower()


def test_none_scheduled_has_its_own_wording():
    line = sp.earnings_line(None, "none_scheduled", TODAY)
    assert "none scheduled" in line.lower()
    assert "not covered" not in line


def test_an_upcoming_report_shows_its_date_and_distance():
    assert sp.earnings_line("2026-10-23", "upcoming", TODAY) == \
        "Earnings Oct 23 · in 35 days"


def test_a_cached_date_without_a_status_still_shows_the_date():
    # The scan funnel carries the date its gate read but never the status.
    assert sp.earnings_line("2026-09-19", None, TODAY) == \
        "Earnings Sep 19 · tomorrow"


def test_a_report_today_says_today():
    assert sp.earnings_line("2026-09-18", "upcoming", TODAY) == \
        "Earnings Sep 18 · today"


def test_no_date_and_no_status_claims_nothing():
    line = sp.earnings_line(None, None, TODAY)
    assert "none scheduled" not in line.lower()
    assert "not covered" not in line


# ── the structure and volatility bands ─────────────────────────────────────

def _facts(**kw):
    f = dict.fromkeys(sf.FACT_KEYS)
    f.update(kw)
    return f


def test_structure_reads_the_flip_side_and_the_bar():
    s = sp.structure_band(_facts(spot=105.0, flip=100.0, put_wall=90.0,
                                 call_wall=110.0, net_gex=2.4e9,
                                 gex_regime="above",
                                 dealer_regime="charm_grind"))
    assert s["side"] == "above"
    assert s["pos"] == {"put_wall": 0.0, "call_wall": 100.0,
                        "spot": 75.0, "flip": 50.0}
    assert s["regime_word"] == "LONG GAMMA · PINS"
    assert s["setup"] == "GRIND"
    assert s["net_gex_text"] == "+2.40B"


def test_an_all_zero_gex_grid_withholds_the_walls():
    # Index open interest reads 0 after hours; walls picked from a zero grid are
    # arbitrary, the Desk's own rule.
    s = sp.structure_band(_facts(spot=105.0, flip=100.0, put_wall=90.0,
                                 call_wall=110.0, net_gex=0.0))
    assert s["pos"] is None and s["walls_withheld"] is True


def test_a_fetched_symbol_gets_its_regime_word_from_its_own_flip():
    s = sp.structure_band(_facts(spot=95.0, flip=100.0))
    assert s["regime_word"] == "SHORT GAMMA · RUNS"


def test_volatility_band_words_are_the_scorers():
    v = sp.volatility_band(_facts(spot=100.0, iv_rank=62.0, current_iv=36.0,
                                  hv_current=30.0, atm_iv=36.5,
                                  iv_state="rising"))
    assert v["band"] == "high"
    assert v["iv_hv_text"] == "IV 36.0 vs HV 30.0 · high (1.20×)"
    assert v["em_day"] == pytest.approx(100 * 0.365 / 365 ** 0.5)


def test_volatility_without_an_hv_reading_names_no_band():
    v = sp.volatility_band(_facts(current_iv=36.0))
    assert v["band"] == "na"
    assert v["iv_hv_text"] == "IV vs HV —"


def test_expected_move_falls_back_to_the_fetched_iv():
    # An off-watchlist dossier has no matrix ATM IV; its current_iv is the same
    # percent basis (both are the scan's own definitions).
    v = sp.volatility_band(_facts(spot=100.0, current_iv=36.5))
    assert v["em_day"] == pytest.approx(100 * 0.365 / 365 ** 0.5)


# ── the three absences render differently ──────────────────────────────────

def test_signals_cold_feed_quiet_tape_and_stale_day_are_distinct():
    cold = sp.signal_band("MU", None, TODAY)
    quiet = sp.signal_band("MU", {"date": TODAY, "signals_0dte": []}, TODAY)
    stale = sp.signal_band("MU", {"date": "2026-09-17"}, TODAY)
    assert cold["message"] == _copy.WAITING_OPTIONS
    assert quiet["message"] == "No signals for MU today."
    assert stale["message"] and stale["message"] not in (
        cold["message"], quiet["message"])


def test_flow_cold_and_quiet_are_distinct():
    assert sp.flow_band("MU", None)["message"] == _copy.WAITING_OPTIONS
    assert sp.flow_band("MU", {"alerts": []})["message"] == \
        "No flow alerts for MU today."


def test_positions_cold_and_empty_are_distinct():
    assert sp.position_band("MU", {})["message"] == _copy.WAITING_OPTIONS
    books = {"options:paper_account": {"positions": []}}
    assert sp.position_band("MU", books)["message"] == "No open position in MU."


def test_a_position_row_carries_its_book_and_rescue_flag():
    books = {"options:paper_account": {"positions": [
        {"symbol": "MU", "strategy": "PCS", "short_strike": 180.0,
         "long_strike": 175.0, "expiration": "2026-09-25", "quantity": 2,
         "entry_credit": 1.1, "unrealized_pnl": -40.0,
         "rescue_state": "tested"}]},
        "options:captured": {"signals": [
            {"symbol": "MU", "strategy": "CCS", "short_strike": 200.0,
             "long_strike": 205.0, "expiration": "2026-09-25"}]}}
    rows = sp.position_band("MU", books)["rows"]
    by_book = {r["book"]: r for r in rows}
    assert by_book["account"]["flag"] == "AT RISK"
    assert by_book["account"]["strikes"] == "180.0/175.0"
    # The captured book is never inspected by the rescue overlay: a dash, never
    # a clean bill of health nobody issued.
    assert by_book["captured"]["flag"] == "—"


# ── the signal band's persistence marks (Task D8) ──────────────────────────

def _setups_entry():
    """A setups entry shaped exactly as ``options_svc.compute.merge_setups``
    writes it: stamped at the 09:15 scan, seen at three scans with one missed
    between them (last_seq jumped), best score each time."""
    return {"first_seen": "2026-09-18T09:15:00-05:00", "seen": 3,
            "scores": [61.0, 64.0, 66.5], "gaps": 1, "last_seq": 5}


def _day_env():
    return {"date": TODAY,
            "signals_0dte": [],
            "signals_swing": [
                {"id": "s1", "symbol": "MU", "type": "PCS",
                 "expiration": "2026-10-02", "dte": 14,
                 "short_strike": 170.0, "long_strike": 165.0,
                 "credit": 1.2, "composite_score": 66.5, "live": True,
                 "setup_key": "MU|PCS|2026-10-02|put"},
                {"id": "s2", "symbol": "NVDA", "type": "PCS",
                 "composite_score": 80.0, "live": True,
                 "setup_key": "NVDA|PCS|2026-10-02|put"}],
            "signals_directional": [],
            "setups": {"MU|PCS|2026-10-02|put": _setups_entry()}}


def test_the_band_reports_a_setup_that_outlived_its_rows():
    # The sentence the coarse key exists to produce.
    band = sp.signal_band("MU", _day_env(), TODAY)
    assert [d["detail"] for d in band["setups"]] == [
        "Live since 09:15 · 1 gap"]


def test_signal_rows_are_this_symbols_with_age_and_trend():
    band = sp.signal_band("MU", _day_env(), TODAY)
    assert [r["id"] for r in band["rows"]] == ["s1"]
    row = band["rows"][0]
    assert row["seen_since"] == "09:15 · 3x"
    assert row["score_trend"] == "new"          # three readings < the window
    assert row["spark"].startswith("<svg")
    assert row["stale"] is False


def test_a_row_with_no_setup_entry_dashes_rather_than_claims_new():
    env = _day_env()
    env["setups"] = {}
    row = sp.signal_band("MU", env, TODAY)["rows"][0]
    assert row["seen_since"] == "—"
    assert row["spark"] == ""
    assert sp.signal_band("MU", env, TODAY)["setups"] == []


def test_a_dropped_signal_is_marked_stale_but_keeps_its_age():
    env = _day_env()
    env["signals_swing"][0]["live"] = False
    env["signals_swing"][0]["stale_since"] = "2026-09-18T11:00:00"
    row = sp.signal_band("MU", env, TODAY)["rows"][0]
    assert row["stale"] is True
    assert row["seen_since"] == "09:15 · 3x"


# ── render: driven for real, with the bus faked ────────────────────────────
# A render that raises, or that enqueues on the wrong coverage, is invisible to
# the pure tests above. These build the page inside the test client, with
# ``bus_client`` answering from a dict and ``request`` recorded.

def _world():
    return {
        "options:matrix": {"rows": [
            {"symbol": "MU", "spot": 184.2, "day_pct": 1.8, "flip": 180.0,
             "put_wall": 170.0, "call_wall": 195.0, "net_gex": 2.4e9,
             "atm_iv": 48.5, "iv_state": "rising", "gex_regime": "above",
             "dealer_regime": "charm_grind"},
            {"symbol": "$VIX", "spot": 16.2, "flip": 15.0, "put_wall": 14.0,
             "call_wall": 20.0, "net_gex": 1.0e6}]},
        "options:scan_funnel": {"symbols": {"MU": {
            "price": 184.1, "iv_rank": 62.0, "current_iv": 48.0,
            "hv_current": 40.0, "earnings_date": "2026-12-17"}}},
        "options:scan_day": dict(_day_env(), date=sp.today_ct()),
        "options:flow_alerts": {"alerts": [
            {"id": "f1", "symbol": "MU", "type": "crossover", "side": "call",
             "text": "MU calls over puts", "ts": 1}]},
        "options:paper_account": {"positions": [
            {"symbol": "MU", "strategy": "PCS", "short_strike": 175.0,
             "long_strike": 170.0, "expiration": "2026-10-16",
             "quantity": 1, "unrealized_pnl": 22.0, "rescue_state": "ok"}]},
        "options:paper_trades": {"trades": []},
        "options:driver_paper_account": {"positions": []},
        "options:captured": {"signals": []},
        "sentiment:regime": {"label": "Rallying", "committed_label": "trending",
                             "confidence": 0.8, "direction": 1},
        "sentiment:bullbear": _bullbear_world(),
    }


def _bullbear_world():
    return {"levels": {"sector": [], "industry": [], "stock": [
        {"symbol": "MU", "sector": "Information Technology",
         "industry": "Semiconductors", "raw": {"trend": 0.12, "excess": 0.03},
         "rank": 4, "rank_prev": 9}]}}


@pytest.fixture
def world(monkeypatch):
    import bus_client
    data = _world()
    sent = []

    def _read_gated(view, memo):
        memo["state"] = (1, data.get(view)) if view in data else None
        return data.get(view), True

    monkeypatch.setattr(bus_client, "read", lambda v: data.get(v))
    monkeypatch.setattr(bus_client, "read_full",
                        lambda v: (data.get(v), 1 if v in data else None))
    monkeypatch.setattr(bus_client, "read_gated", _read_gated)
    monkeypatch.setattr(bus_client, "read_versions",
                        lambda vs: {v: (1 if v in data else None) for v in vs})
    monkeypatch.setattr(bus_client, "request",
                        lambda domain, cmd: sent.append((domain, cmd)) or "1-0")
    return data, sent


def _built(symbol):
    """Render, then run the page's own one-shot first read (it runs off the
    event loop in the app, so a bare render has painted nothing yet)."""
    import asyncio

    from nicegui import ui
    from nicegui.elements.timer import Timer
    before = set(ui.context.client.elements)
    sp.render(symbol)
    elements = [e for k, e in ui.context.client.elements.items()
                if k not in before]
    (seed,) = [e for e in elements
               if isinstance(e, Timer) and e.interval == sp.SEED_DELAY_SEC]
    asyncio.run(seed.callback())
    return [e for k, e in ui.context.client.elements.items() if k not in before]


def _render_texts(symbol):
    return [getattr(e, "text", "") or "" for e in _built(symbol)]


def test_the_build_itself_reads_nothing_on_the_event_loop(world, monkeypatch):
    """The first read (a multi-MB day union) runs on the page's one-shot
    timer through run.io_bound — never inside render(), which blocks the loop."""
    import bus_client
    reads = []
    for name in ("read", "read_full", "read_gated", "read_versions"):
        monkeypatch.setattr(bus_client, name,
                            lambda *a, n=name: reads.append(n) or (None, None))
    sp.render("MU")
    assert reads == []


def test_a_scanned_symbol_renders_every_band_and_fetches_nothing(world):
    _data, sent = world
    texts = _render_texts("mu")
    assert sent == []
    assert "SCANNED" in texts
    assert "MU" in texts
    assert any(t.startswith("Earnings Dec 17") for t in texts)
    assert any("Live since 09:15 · 1 gap" in t for t in texts)
    assert "No signals for MU today." not in texts
    assert any(t.startswith("IV 48.0 vs HV 40.0") for t in texts)


def test_an_unknown_symbol_fetches_exactly_once_on_navigation(world):
    _data, sent = world
    texts = _render_texts("XYZQ")
    assert sent == [("options", {"type": "dossier",
                                 "args": {"symbol": "XYZQ"}})]
    assert "FETCHING" in texts
    assert "No signals for XYZQ today." in texts
    assert "No open position in XYZQ." in texts


def test_a_collected_symbol_fetches_its_missing_facts(world):
    _data, sent = world
    _render_texts("$VIX")
    assert [c["args"]["symbol"] for _d, c in sent] == ["$VIX"]


def test_a_cached_dossier_is_reused_on_navigation(world):
    data, sent = world
    data["options:dossier:XYZQ"] = {"symbol": "XYZQ", "error": None,
                                    "fetched_at": "2026-09-18T14:32:00",
                                    "spot": 12.5, "flip": 12.0}
    texts = _render_texts("XYZQ")
    assert sent == []
    assert "FETCHED 14:32" in texts


def test_a_cached_no_quote_is_not_re_fetched_and_says_check_the_symbol(world):
    data, sent = world
    data["options:dossier:XYZQ"] = {"symbol": "XYZQ", "error": "no_quote"}
    texts = _render_texts("XYZQ")
    assert sent == []
    assert "No quote for XYZQ — check the symbol" in texts


def test_a_rejected_symbol_renders_not_found_and_enqueues_nothing(world):
    _data, sent = world
    texts = _render_texts("../../etc")
    assert sent == []
    assert "NOT FOUND" in texts


def test_a_cold_options_feed_enqueues_nothing(world):
    data, sent = world
    for view in list(data):
        if view.startswith("options:"):
            del data[view]
    texts = _render_texts("XYZQ")
    assert sent == []
    assert _copy.WAITING_OPTIONS in texts


# ── the live page: the poll, Refresh and the links, driven ─────────────────
# These reach the page's own callbacks through the elements it built — the poll
# timer's callback, the Refresh button's click handler, a band link's click —
# because a handler that behaves correctly and is wired wrongly (or a poll that
# reaches the enqueue through a helper) looks identical at source level.

def _render_page(symbol):
    return _built(symbol)


def _texts(elements):
    return [getattr(e, "text", "") or "" for e in elements]


def _poll_callback(elements):
    from nicegui.elements.timer import Timer
    timers = [e for e in elements
              if isinstance(e, Timer) and e.interval == sp.POLL_SEC]
    assert len(timers) == 1, timers
    return timers[0].callback


def _click_handlers(elements, text):
    out = []
    for e in elements:
        if (getattr(e, "text", "") or "") == text:
            out += [li.handler for li in e._event_listeners.values()
                    if li.type == "click"]
    assert out, f"no clickable {text!r}"
    return out


def _refresh(elements):
    """The Refresh coroutine itself: on_click dispatches through NiceGUI's
    event machinery, which a test cannot await, so the page exposes it."""
    (btn,) = [e for e in elements if hasattr(e, "_symbol_refresh")]
    return btn._symbol_refresh


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_a_dossier_whose_payload_lands_after_its_version_is_still_read(
        world, monkeypatch):
    """#1 — ``cache_set`` bumps ``{key}:ver`` one round-trip BEFORE it writes
    the payload, so a poll can probe v1 while the payload read still returns
    nothing. Storing the PROBED version then matched every later poll and the
    dossier (written once, never republished) was never read: FETCHING, then
    "try Refresh", and Refresh paid for a second fetch."""
    import bus_client
    data, sent = world
    elements = _render_page("XYZQ")
    assert len(sent) == 1
    poll = _poll_callback(elements)

    view = "options:dossier:XYZQ"
    real_full = bus_client.read_full
    landed = {"yes": False}
    monkeypatch.setattr(
        bus_client, "read_versions",
        lambda vs: {v: (1 if (v in data or v == view) else None) for v in vs})

    def _full(v):
        if v == view:
            if not landed["yes"]:
                return None, None               # :ver bumped, SET not yet run
            return ({"symbol": "XYZQ", "error": None,
                     "fetched_at": "2026-09-18T14:32:00", "spot": 12.5}, 1)
        return real_full(v)

    monkeypatch.setattr(bus_client, "read_full", _full)
    _run(poll())                                # probes v1, reads nothing
    landed["yes"] = True
    _run(poll())                                # the payload has now landed
    texts = _texts(elements)
    assert "FETCHED 14:32" in texts
    assert "FETCHING" not in texts
    assert len(sent) == 1                       # and nothing was re-fetched


def test_two_refresh_taps_send_exactly_one_fetch(world):
    """#2 — the handler awaits a read before it enqueues, so a second tap in
    that window used to enqueue a second 4-5-call fetch."""
    import asyncio
    data, sent = world
    data["options:dossier:XYZQ"] = {"symbol": "XYZQ", "error": None,
                                    "fetched_at": "2026-09-18T14:32:00"}
    elements = _render_page("XYZQ")
    assert sent == []                           # reused on navigation
    handler = _refresh(elements)

    async def _two_taps():
        await asyncio.gather(handler(), handler())

    _run(_two_taps())
    assert len(sent) == 1


def test_a_refresh_while_a_fetch_is_in_flight_sends_nothing(world):
    data, sent = world
    elements = _render_page("XYZQ")             # navigation fetch → pending
    assert len(sent) == 1
    handler = _refresh(elements)
    _run(handler())
    assert len(sent) == 1


def test_the_poll_never_enqueues_even_when_every_version_moves(
        world, monkeypatch):
    """#4 — behavioural, beside the source-level guard: a helper called from
    the poll would pass the source test and spend on every tick."""
    import bus_client
    data, sent = world
    data["options:dossier:XYZQ"] = {"symbol": "XYZQ", "error": "fetch_failed"}
    elements = _render_page("XYZQ")             # fetch_failed is retried...
    sent.clear()
    poll = _poll_callback(elements)
    for bump in (2, 3):
        monkeypatch.setattr(bus_client, "read_versions",
                            lambda vs, b=bump: {v: b for v in vs})
        monkeypatch.setattr(bus_client, "read_full",
                            lambda v, b=bump: (data.get(v), b))
        _run(poll())
    assert sent == []                           # ...but never by the poll


def test_the_poll_does_not_enqueue_a_poll_path_fetch_for_a_collected_symbol(
        world, monkeypatch):
    import bus_client
    data, sent = world
    elements = _render_page("$VIX")
    sent.clear()
    poll = _poll_callback(elements)
    monkeypatch.setattr(bus_client, "read_versions",
                        lambda vs: {v: 9 for v in vs})
    monkeypatch.setattr(bus_client, "read_full", lambda v: (data.get(v), 9))
    _run(poll())
    assert sent == []


def _timeout_timers(before):
    from nicegui import ui
    from nicegui.elements.timer import Timer
    return [e for k, e in ui.context.client.elements.items()
            if k not in before and isinstance(e, Timer)
            and e.interval == sp.LOAD_TIMEOUT_SEC]


def test_an_older_fetch_timeout_cannot_clear_a_newer_fetch(world):
    """Only the timeout of the CURRENT request may drop the overlay: a 30 s
    timer left over from an earlier fetch firing mid-way through a newer one
    would report "no data" while that newer fetch is still on its way."""
    from nicegui import ui
    data, sent = world
    before = set(ui.context.client.elements)
    elements = _render_page("XYZQ")             # request 1 (navigation)
    (first,) = _timeout_timers(before)
    first.callback()                            # request 1 times out
    assert "FETCHING" not in _texts(elements)
    handler = _refresh(elements)
    _run(handler())                             # request 2
    assert len(sent) == 2
    assert "FETCHING" in _texts(elements)
    first.callback()                            # request 1's timer, again
    assert "FETCHING" in _texts(elements)


def test_a_timed_out_fetch_says_it_is_still_queued_not_try_refresh():
    """options_svc runs ONE consumer on cmd:options, so a dossier queued behind
    a 26-40 s whole-chain scan outlasts the 30 s backstop. It is still coming —
    inviting Refresh then would queue a second paid fetch behind the first."""
    chip = sp.coverage_chip("XYZQ", sf.UNKNOWN, None, queued=True)
    assert "refresh" not in chip["message"].lower()
    assert "queued" in chip["message"]
    assert chip["label"] == "QUEUED"


def test_the_page_shows_the_queued_line_after_its_timeout(world):
    from nicegui import ui
    _data, sent = world
    before = set(ui.context.client.elements)
    elements = _render_page("XYZQ")
    (timer,) = _timeout_timers(before)
    timer.callback()
    texts = _texts(elements)
    assert "QUEUED" in texts
    assert not any("try Refresh" in t for t in texts)
    assert len(sent) == 1


# ── Refresh inside the service's dedup window ──────────────────────────────

def _ago_ct(seconds):
    """A naive-Central ``fetched_at`` ``seconds`` ago — the dossier's own
    stamp format (dossier._stamp: naive CT, seconds precision)."""
    import datetime as dt
    now = dt.datetime.now(sp._CT).replace(tzinfo=None)
    return (now - dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _ago_utc(seconds):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    return (now - dt.timedelta(seconds=seconds)).isoformat()


def test_a_dossier_written_moments_ago_is_current_by_its_envelope_time():
    # The service measures its dedup on the envelope's write time; the page
    # reads the same stamp when it has it.
    d = {"error": None, "fetched_at": _ago_ct(10)}
    assert sp.dossier_is_current(d, _ago_utc(10)) is True
    assert sp.dossier_is_current(d, _ago_utc(120)) is False


def test_without_an_envelope_time_the_fetch_stamp_is_read_as_central():
    """``fetched_at`` is NAIVE CENTRAL. Read as host-local or UTC it would be
    off by hours on any machine not on CT — the persistence plan's trap."""
    assert sp.dossier_is_current({"error": None,
                                  "fetched_at": _ago_ct(10)}) is True
    assert sp.dossier_is_current({"error": None,
                                  "fetched_at": _ago_ct(120)}) is False


def test_a_naive_utc_reading_of_the_stamp_would_be_hours_off():
    import datetime as dt
    stamp = _ago_ct(10)
    as_utc = dt.datetime.fromisoformat(stamp).replace(tzinfo=dt.timezone.utc)
    off = abs((dt.datetime.now(dt.timezone.utc) - as_utc).total_seconds())
    assert off > 3600          # the page must not read it this way...
    assert sp.dossier_is_current({"error": None, "fetched_at": stamp})


@pytest.mark.parametrize("dossier", [
    None, {}, {"error": "fetch_failed", "fetched_at": "x"},
    {"error": None, "fetched_at": "garbage"},
    {"error": None, "fetched_at": None}])
def test_nothing_unreadable_or_failed_is_current(dossier):
    assert sp.dossier_is_current(dossier) is False


def test_a_recent_failed_fetch_is_never_current():
    assert sp.dossier_is_current({"error": "fetch_failed",
                                  "fetched_at": _ago_ct(5)},
                                 _ago_utc(5)) is False


def test_a_future_stamp_is_not_current():
    assert sp.dossier_is_current({"error": None,
                                  "fetched_at": _ago_ct(-300)}) is False


def test_should_enqueue_refuses_a_refresh_of_a_current_dossier():
    assert sp.should_enqueue(sf.UNKNOWN, "refresh", current=True) is False
    assert sp.should_enqueue(sf.UNKNOWN, "refresh", current=False) is True


@pytest.mark.parametrize("coverage", [sf.UNKNOWN, sf.COLLECTED])
def test_the_chip_says_a_current_dossier_is_already_current(coverage):
    chip = sp.coverage_chip("XYZQ", coverage,
                            {"error": None,
                             "fetched_at": "2026-09-18T14:32:05"},
                            current=True)
    assert chip["message"] == "Fetched 14:32 — already current."


def _refresh_page(world, dossier):
    from nicegui import ui
    data, sent = world
    data["options:dossier:XYZQ"] = dossier
    before = set(ui.context.client.elements)
    elements = _render_page("XYZQ")
    # A fetch_failed dossier is retried on navigation; let that request time
    # out first, so the Refresh below is not refused for being in flight.
    for timer in _timeout_timers(before):
        timer.callback()
    sent.clear()
    _run(_refresh(elements)())
    return _texts(elements), sent


def test_refresh_ten_seconds_after_a_fetch_sends_nothing(world):
    texts, sent = _refresh_page(world, {
        "symbol": "XYZQ", "error": None, "fetched_at": _ago_ct(10),
        "spot": 12.5})
    assert sent == []
    assert any(t.endswith("— already current.") for t in texts)
    assert "QUEUED" not in texts and "FETCHING" not in texts


def test_refresh_two_minutes_after_a_fetch_sends_one(world):
    texts, sent = _refresh_page(world, {
        "symbol": "XYZQ", "error": None, "fetched_at": _ago_ct(120),
        "spot": 12.5})
    assert len(sent) == 1
    assert not any("already current" in t for t in texts)


def test_a_recent_failed_fetch_still_refreshes(world):
    _texts_, sent = _refresh_page(world, {
        "symbol": "XYZQ", "error": "fetch_failed",
        "fetched_at": _ago_ct(10)})
    assert len(sent) == 1


def test_refresh_uses_the_envelope_time_when_it_has_one(world, monkeypatch):
    """The stamp the service's dedup reads. fetched_at is taken BEFORE the
    4-5 Schwab calls, so it can read a few seconds older than the write."""
    import bus_client
    monkeypatch.setattr(bus_client, "read_meta",
                        lambda v: (1, _ago_utc(10)))
    _texts_, sent = _refresh_page(world, {
        "symbol": "XYZQ", "error": None, "fetched_at": _ago_ct(70),
        "spot": 12.5})
    assert sent == []


def test_a_stale_timeout_is_keyed_to_its_own_request():
    # pure: the token rule the page's timeout uses
    assert sp.timeout_applies(3, 3) is True
    assert sp.timeout_applies(2, 3) is False


def test_the_expected_move_link_carries_the_symbol(world, monkeypatch):
    from nicegui import ui
    from pages.options import handoff
    went = []
    monkeypatch.setattr(ui.navigate, "to",
                        lambda *a, **k: went.append(a[0]))
    elements = _render_page("mu")
    (handler,) = _click_handlers(elements, "→ Expected Move")
    handler(None)
    assert went == ["/options/expected-move"]
    assert handoff.take_pending_expected_move() == {"symbol": "MU"}


@pytest.mark.parametrize("coverage,allowed", [
    (sf.SCANNED, True), (sf.COLLECTED, True), (sf.UNKNOWN, False),
    (None, False), ("", False)])
def test_only_a_collected_symbol_may_link_to_dealer_positioning(coverage,
                                                                allowed):
    """The Gamma page points the shared sticky gamma slot at whatever it is
    handed, and the service then refreshes that symbol every GEX tick. For a
    symbol the collector does not poll there is no tick chain to reuse, so each
    refresh is a fresh Schwab chain fetch — one a minute, all session."""
    assert sp.gamma_link_allowed(coverage) is allowed


def _gamma_links(elements):
    return [e for e in elements
            if (getattr(e, "text", "") or "") == "→ Dealer Positioning"]


@pytest.mark.parametrize("dossier", [
    None,
    {"symbol": "XYZQ", "error": "no_quote"},
    {"symbol": "XYZQ", "error": "fetch_failed"},
    {"symbol": "XYZQ", "error": None, "fetched_at": "2026-09-18T14:32:00",
     "spot": 12.5, "flip": 12.0, "put_wall": 11.0, "call_wall": 14.0}])
def test_an_uncollected_symbol_draws_no_dealer_positioning_link(
        world, monkeypatch, dossier):
    from pages.options import handoff
    data, _sent = world
    if dossier is not None:
        data["options:dossier:XYZQ"] = dossier
    handed = []
    monkeypatch.setattr(handoff, "send_to_gamma", lambda s: handed.append(s))
    elements = _render_page("XYZQ")
    assert _gamma_links(elements) == []
    # and no other click on the page reaches it either
    for e in elements:
        for li in e._event_listeners.values():
            if li.type == "click":
                try:
                    li.handler(None)
                except TypeError:
                    pass
    assert handed == []


@pytest.mark.parametrize("symbol", ["MU", "$VIX"])
def test_a_scanned_or_collected_symbol_draws_the_link(world, symbol):
    assert len(_gamma_links(_render_page(symbol))) == 1


def test_the_dealer_positioning_link_carries_the_symbol(world, monkeypatch):
    from nicegui import ui
    from pages.options import handoff
    went = []
    monkeypatch.setattr(ui.navigate, "to",
                        lambda *a, **k: went.append(a[0]))
    elements = _render_page("mu")
    (handler,) = _click_handlers(elements, "→ Dealer Positioning")
    handler(None)
    assert went == ["/options/gamma"]
    assert handoff.take_pending_gamma() == "MU"


# ── #3: walls after the close ──────────────────────────────────────────────

_CACHED = {"put_wall": "cache", "call_wall": "cache"}
_FETCHED = {"put_wall": "fetch", "call_wall": "fetch"}


def _walls(**kw):
    return _facts(spot=105.0, flip=100.0, put_wall=90.0, call_wall=110.0, **kw)


def test_stale_gex_withholds_the_walls():
    f = _walls(net_gex=2.4e9)
    live = sp.structure_band(f, freshness="live", source=_CACHED)
    stale = sp.structure_band(f, freshness="stopped", source=_CACHED)
    assert live["pos"] is not None and live["put_wall"] == 90.0
    assert stale["pos"] is None and stale["put_wall"] is None
    assert stale["walls_withheld"] is True
    assert stale["withheld_reason"] == sp.WALLS_STALE


def test_fetched_walls_are_shown_while_the_collector_is_stopped():
    """19:00 CT, an off-watchlist name: the fetch read its chain seconds ago
    and the collector — which never drew these walls — is stopped. Withholding
    them "because the collector is not running" would be false twice."""
    s = sp.structure_band(_walls(), freshness="stopped", source=_FETCHED,
                          fetched_at="2026-09-18T19:00:05")
    assert s["pos"] is not None
    assert (s["put_wall"], s["call_wall"]) == (90.0, 110.0)
    assert s["walls_withheld"] is False
    assert s["walls_note"] == "walls fetched 19:00"


def test_fetched_walls_carry_no_note_when_the_fetch_time_is_unreadable():
    s = sp.structure_band(_walls(), freshness="live", source=_FETCHED,
                          fetched_at=None)
    assert s["walls_note"] == "walls fetched"


def test_cached_walls_carry_no_fetch_note():
    s = sp.structure_band(_walls(), freshness="live", source=_CACHED)
    assert s["walls_note"] == ""


def test_walls_of_unknown_source_are_gated_as_cached():
    # The conservative reading: with no provenance, the collector's rule holds.
    s = sp.structure_band(_walls(), freshness="stopped", source=None)
    assert s["pos"] is None and s["walls_withheld"] is True


def test_mixed_sources_gate_each_wall_on_its_own_and_draw_no_bar():
    """One wall from a stopped collector, one fetched just now. The fetched one
    is current and shown; the collector's is withheld. The BAR is not drawn:
    it would place spot between two walls read at different times, which is a
    geometry neither source ever saw."""
    src = {"put_wall": "cache", "call_wall": "fetch"}
    s = sp.structure_band(_walls(), freshness="stopped", source=src,
                          fetched_at="2026-09-18T14:32:00")
    assert s["pos"] is None
    assert s["put_wall"] is None and s["call_wall"] == 110.0
    assert s["walls_withheld"] is True
    assert s["withheld_reason"] == sp.WALLS_STALE
    assert s["walls_note"] == "walls fetched 14:32"


def test_mixed_sources_while_the_collector_is_live_draw_the_bar():
    src = {"put_wall": "cache", "call_wall": "fetch"}
    s = sp.structure_band(_walls(), freshness="live", source=src)
    assert s["pos"] is not None


def test_the_zero_grid_rule_still_gates_every_wall():
    for src in (_CACHED, _FETCHED):
        s = sp.structure_band(_walls(net_gex=0.0), freshness="live",
                              source=src)
        assert s["pos"] is None
        assert s["withheld_reason"] == sp.WALLS_ZERO_GRID


def test_a_fetched_zero_grid_says_why_it_has_no_walls():
    """Since fce4e7b the service publishes NO walls for an all-zero grid (index
    OI zeroed after hours) and carries net_gex = 0.0. With no wall value to
    gate, the band must still say why the walls are missing — an unexplained
    blank is what the Desk never shows."""
    f = _facts(spot=105.0, flip=100.0, net_gex=0.0)
    src = {"put_wall": None, "call_wall": None, "net_gex": "fetch"}
    s = sp.structure_band(f, freshness="stopped", source=src,
                          fetched_at="2026-09-18T19:00:05")
    assert s["pos"] is None
    assert s["walls_withheld"] is True
    assert s["withheld_reason"] == sp.WALLS_ZERO_GRID


def test_an_absent_net_gex_is_not_a_zero_grid():
    # Unknown is not zero: no net GEX and no walls claims nothing about a grid.
    f = _facts(spot=105.0, flip=100.0, net_gex=None)
    s = sp.structure_band(f, freshness="live",
                          source={"put_wall": None, "call_wall": None})
    assert s["walls_withheld"] is False
    assert s["withheld_reason"] == ""


def test_a_fetched_zero_net_gex_also_withholds_cached_walls():
    """Mixed source, deliberately conservative: the cache has walls but no net
    GEX, and the fetch fills net GEX with 0.0. The zero-grid rule reads the one
    net GEX the page has, so the cached walls are withheld too."""
    src = {"put_wall": "cache", "call_wall": "cache", "net_gex": "fetch"}
    s = sp.structure_band(_walls(net_gex=0.0), freshness="live", source=src)
    assert s["pos"] is None
    assert s["put_wall"] is None and s["call_wall"] is None
    assert s["withheld_reason"] == sp.WALLS_ZERO_GRID


def test_the_page_explains_a_fetched_zero_grid(world):
    data, _sent = world
    data["options:dossier:XSP"] = {
        "symbol": "XSP", "error": None, "fetched_at": "2026-09-18T19:00:05",
        "spot": 560.0, "flip": 555.0, "net_gex": 0.0,
        "put_wall": None, "call_wall": None}
    texts = _texts(_render_page("XSP"))
    assert sp.WALLS_ZERO_GRID in texts


def test_an_unknown_collector_age_is_not_called_stopped():
    """A cold gex_status means the age is UNKNOWN — the Desk says "Data age
    unknown" for it, never that the collector stopped."""
    s = sp.structure_band(_walls(), freshness="unknown", source=_CACHED)
    assert s["pos"] is None
    assert s["withheld_reason"] == sp.WALLS_AGE_UNKNOWN
    assert sp.WALLS_AGE_UNKNOWN != sp.WALLS_STALE
    assert "not running" not in sp.WALLS_AGE_UNKNOWN
    assert "age unknown" in sp.WALLS_AGE_UNKNOWN.lower()


def test_the_walls_follow_the_collectors_own_freshness():
    assert sp.gex_freshness({"age_seconds": 30}) == "live"
    assert sp.gex_freshness({"age_seconds": 3600}) == "stopped"
    assert sp.gex_freshness(None) == "unknown"     # never "live"
    assert sp.gex_freshness({}) == "unknown"


def test_the_page_shows_fetched_walls_after_the_close(world):
    data, sent = world
    data["options:gex_status"] = {"age_seconds": 7200}
    data["options:dossier:IREN"] = {
        "symbol": "IREN", "error": None, "fetched_at": "2026-09-18T19:00:05",
        "spot": 12.5, "flip": 12.0, "put_wall": 11.0, "call_wall": 14.0}
    texts = _texts(_render_page("IREN"))
    assert sent == []                          # a fresh dossier is reused
    assert "put wall 11.00" in texts and "call wall 14.00" in texts
    assert "walls fetched 19:00" in texts
    assert not any(t.startswith("Walls withheld") for t in texts)


def test_the_page_says_age_unknown_for_a_cold_gex_status(world):
    data, _sent = world
    data.pop("options:gex_status", None)
    texts = _texts(_render_page("mu"))
    assert sp.WALLS_AGE_UNKNOWN in texts
    assert sp.WALLS_STALE not in texts


def test_gex_status_is_polled_and_repaints_the_structure_band():
    assert "options:gex_status" in sp.VIEWS
    assert "structure" in sp.regions_for({"options:gex_status"}, "MU")


def test_the_page_draws_walls_only_while_the_collector_is_live(
        world, monkeypatch):
    data, _sent = world
    data["options:gex_status"] = {"age_seconds": 20}
    live = _texts(_render_page("mu"))
    assert "put wall 170.00" in live
    data["options:gex_status"] = {"age_seconds": 7200}
    after = _texts(_render_page("mu"))
    assert "put wall 170.00" not in after
    assert sp.WALLS_STALE in after


# ── a first read that fails ────────────────────────────────────────────────

def test_a_failed_first_read_recovers_on_the_poll_without_a_reload(
        world, monkeypatch):
    """Redis down at page build: the seed raises. The page must say the feed is
    waiting (not sit blank), and the poll must retry the first read until it
    succeeds — then populate, with no reload."""
    import bus_client
    data, _sent = world
    real_full = bus_client.read_full
    down = {"yes": True}

    def _full(v):
        if down["yes"]:
            raise ConnectionError("redis down")
        return real_full(v)

    monkeypatch.setattr(bus_client, "read_full", _full)
    from nicegui import ui
    from nicegui.elements.timer import Timer
    before = set(ui.context.client.elements)
    sp.render("mu")
    elements = [e for k, e in ui.context.client.elements.items()
                if k not in before]
    (seed,) = [e for e in elements
               if isinstance(e, Timer) and e.interval == sp.SEED_DELAY_SEC]
    poll = _poll_callback(elements)
    with pytest.raises(ConnectionError):
        _run(seed.callback())

    def _now():
        return [getattr(e, "text", "") or "" for k, e in
                ui.context.client.elements.items() if k not in before]

    assert _copy.WAITING_OPTIONS in _now()      # said, not blank
    _run(poll())                                # still down: retried, no crash
    assert "SCANNED" not in " ".join(_now())
    down["yes"] = False
    _run(poll())                                # the retry succeeds
    texts = _now()
    assert any(t.startswith("SCANNED") for t in texts)
    assert any(t.startswith("Earnings Dec 17") for t in texts)


def test_a_recovered_first_read_spends_the_navigation_fetch_exactly_once(
        world, monkeypatch):
    """The navigation's one look-up still happens when the first read lands on
    a poll's retry — and only once, however many polls follow."""
    import bus_client
    data, sent = world
    real_full = bus_client.read_full
    down = {"yes": True}
    monkeypatch.setattr(
        bus_client, "read_full",
        lambda v: (_ for _ in ()).throw(ConnectionError()) if down["yes"]
        else real_full(v))
    from nicegui import ui
    before = set(ui.context.client.elements)
    sp.render("XYZQ")
    elements = [e for k, e in ui.context.client.elements.items()
                if k not in before]
    poll = _poll_callback(elements)
    _run(poll())
    assert sent == []
    down["yes"] = False
    for _ in range(3):
        _run(poll())
    assert [c["args"]["symbol"] for _d, c in sent] == ["XYZQ"]


def test_the_poll_reads_no_versions_until_the_first_read_has_landed(
        world, monkeypatch):
    """Before the seed, the poll's only job is to retry it — it never probes
    versions against an empty baseline (which would read every view twice)."""
    import bus_client
    probes = []
    real = bus_client.read_versions
    monkeypatch.setattr(bus_client, "read_versions",
                        lambda vs: probes.append(1) or real(vs))
    monkeypatch.setattr(bus_client, "read_full",
                        lambda v: (_ for _ in ()).throw(ConnectionError()))
    from nicegui import ui
    before = set(ui.context.client.elements)
    sp.render("mu")
    elements = [e for k, e in ui.context.client.elements.items()
                if k not in before]
    _run(_poll_callback(elements)())
    assert probes == []


def test_a_retry_already_in_flight_is_not_started_twice(world, monkeypatch):
    import asyncio

    import bus_client
    calls = []
    real_full = bus_client.read_full

    def _full(v):
        calls.append(v)
        return real_full(v)

    monkeypatch.setattr(bus_client, "read_full", _full)
    from nicegui import ui
    before = set(ui.context.client.elements)
    sp.render("mu")
    elements = [e for k, e in ui.context.client.elements.items()
                if k not in before]
    poll = _poll_callback(elements)

    async def _both():
        await asyncio.gather(poll(), poll())

    _run(_both())
    # One first read's worth of views (the day union goes through read_gated).
    assert len(calls) == len(sp.poll_views("MU")) - 1


# ── minors ─────────────────────────────────────────────────────────────────

def test_a_cold_feed_never_suggests_refresh():
    chip = sp.coverage_chip("XYZQ", sf.UNKNOWN, None, feed_cold=True)
    assert "refresh" not in chip["message"].lower()
    assert chip["message"] == _copy.WAITING_OPTIONS


def test_a_cold_funnel_is_a_cold_feed_for_fetch_purposes(world):
    """A warm matrix with a cold funnel reads every watchlist name COLLECTED,
    and would pay to fetch what the scan is about to publish."""
    data, sent = world
    del data["options:scan_funnel"]
    _render_page("MU")
    assert sent == []


def test_the_scanned_chip_carries_the_scan_age():
    chip = sp.coverage_chip("MU", sf.SCANNED, None,
                            scanned_at="2026-09-18T09:30:00-05:00")
    assert chip["label"] == "SCANNED 09:30"


def test_a_scanned_chip_without_a_readable_stamp_says_only_scanned():
    assert sp.coverage_chip("MU", sf.SCANNED, None,
                            scanned_at=None)["label"] == "SCANNED"


def test_a_cold_sentiment_feed_is_said_once(world):
    data, _sent = world
    del data["sentiment:regime"]
    del data["sentiment:bullbear"]
    texts = _texts(_render_page("MU"))
    assert texts.count(_copy.WAITING_SENTIMENT) == 1


def test_the_page_uses_no_raw_tailwind_white():
    assert "text-white" not in _SRC


def test_importing_the_page_pulls_in_no_engine_service_or_main():
    """Transitive, in a fresh interpreter — the direct-import test above cannot
    see a module that arrives through a page it imports."""
    import subprocess
    import sys

    from test_live_main import _child_env   # the Central-clock child env
    webgui = pathlib.Path(sp.__file__).resolve().parents[1]
    code = (
        "import sys; sys.path[:0] = [%r, %r]\n"
        "import pages.symbol\n"
        "mods = set(sys.modules)\n"
        "bad = sorted(m for m in mods if m in ('main', 'sqlite3')"
        " or m.startswith(('services', 'scanner_engine', 'gex_', 'iv_analysis',"
        " 'options_calculator', 'strategy_scoring', 'scoring', 'schwab')))\n"
        "print(bad)\n"
        # redis is on the allow-list ONLY through shared.bus, which imports it
        "print('redis' not in mods or 'shared.bus' in mods)\n"
    ) % (str(webgui), str(webgui.parent))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(webgui), timeout=180,
                         env=_child_env())
    assert out.returncode == 0, out.stderr
    bad, redis_via_bus = out.stdout.strip().splitlines()[-2:]
    assert bad == "[]", bad
    assert redis_via_bus == "True"


# ── registration, placement and privacy ────────────────────────────────────

def test_the_route_is_registered_as_a_shell_page():
    import main  # noqa: F401  -- importing registers the @ui.page routes
    from nicegui import Client
    assert "/symbol" in set(Client.page_routes.values())


def test_the_dossier_sits_in_the_caption_less_leading_block():
    import main
    caption, entries = main.NAV_SECTIONS[0]
    assert caption is None
    assert entries == [main._sec_page("/desk"), main._sec_page("/symbol")]


def test_the_dossier_is_not_a_published_live_screen():
    # It enqueues commands, which bus_client.set_read_only(True) refuses. A
    # published dossier would render permanently empty for every visitor.
    import live_screens
    assert all(s.route != "/symbol" and s.private_route != "/symbol"
               for s in live_screens.SCREENS)


def test_the_page_parameter_goes_through_the_shared_allow_list():
    import main
    src = inspect.getsource(main.symbol_page)
    assert "render(symbol)" in src
    render_src = _function_source("render")
    assert "clean_symbol(" in render_src


def test_the_page_imports_no_engine_and_no_service():
    tree = ast.parse(_SRC)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    bad = {m for m in mods if m.startswith(("services", "sqlite3", "redis"))}
    assert not bad, bad


# ── Find trades: a hand-off to the Strategy Finder ─────────────────────────

_GOOD_FETCH = {"symbol": "XYZQ", "error": None,
               "fetched_at": "2026-09-18T14:32:00", "spot": 12.5}


@pytest.mark.parametrize("sym,coverage,dossier,allowed", [
    ("MU", sf.SCANNED, None, True),
    ("$VIX", sf.COLLECTED, None, True),
    ("XYZQ", sf.UNKNOWN, _GOOD_FETCH, True),
    # Nothing answered yet: the button would send the user to a scan of a
    # ticker nobody has confirmed exists.
    ("XYZQ", sf.UNKNOWN, None, False),
    ("XYZQ", sf.UNKNOWN, {"symbol": "XYZQ", "error": "no_quote"}, False),
    ("XYZQ", sf.UNKNOWN, {"symbol": "XYZQ", "error": "fetch_failed"}, False),
    (None, sf.UNKNOWN, None, False),
])
def test_find_trades_is_offered_only_for_a_quoted_symbol(sym, coverage,
                                                         dossier, allowed):
    assert sp.finder_allowed(sym, coverage, dossier) is allowed


def _finder_buttons(elements):
    return [e for e in elements
            if (getattr(e, "text", "") or "") == sp.FIND_TRADES_LABEL]


def test_find_trades_opens_the_finder_on_this_symbol(world, monkeypatch):
    from nicegui import ui
    from pages.options import handoff
    went = []
    monkeypatch.setattr(ui.navigate, "to",
                        lambda *a, **k: went.append(a[0]))
    elements = _render_page("mu")
    (btn,) = _finder_buttons(elements)
    assert btn.visible
    (handler,) = _click_handlers(elements, sp.FIND_TRADES_LABEL)
    handler(None)
    assert went == ["/options/swing"]
    # The Finder takes this one-shot stash, seeds its input and scans.
    assert handoff.take_pending_swing() == "MU"


@pytest.mark.parametrize("dossier", [
    None,
    {"symbol": "XYZQ", "error": "no_quote"},
    {"symbol": "XYZQ", "error": "fetch_failed"}])
def test_find_trades_is_hidden_and_inert_without_a_quote(world, monkeypatch,
                                                        dossier):
    from pages.options import handoff
    data, _sent = world
    if dossier is not None:
        data["options:dossier:XYZQ"] = dossier
    handed = []
    monkeypatch.setattr(handoff, "send_to_swing", lambda s: handed.append(s))
    elements = _render_page("XYZQ")
    (btn,) = _finder_buttons(elements)
    assert not btn.visible
    # a stale click (the page can change under a tap) still sends nothing
    for h in _click_handlers(elements, sp.FIND_TRADES_LABEL):
        h(None)
    assert handed == []


def test_find_trades_appears_once_a_look_up_returns_a_price(world):
    data, _sent = world
    data["options:dossier:XYZQ"] = dict(_GOOD_FETCH)
    (btn,) = _finder_buttons(_render_page("XYZQ"))
    assert btn.visible


def test_a_rejected_symbol_offers_no_find_trades(world):
    (btn,) = _finder_buttons(_render_page("../../etc"))
    assert not btn.visible
