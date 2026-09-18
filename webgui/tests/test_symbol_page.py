"""The Symbol Dossier page (``/symbol``) — its pure helpers and its wiring rules.

``render()`` is widgets and wiring; every decision it makes lives in a pure
helper in ``pages/symbol.py`` so it can be pinned here without a browser. The
one decision that costs money — whether to enqueue a ``dossier`` fetch — is
pinned twice: as a pure function, and at SOURCE level on the poll callback, so
a refactor cannot quietly start fetching on the 2-second timer.
"""
import ast
import datetime as dt
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

def test_the_poll_reads_the_ten_shared_views_plus_this_symbols_dossier():
    views = sp.poll_views("MU")
    assert views[:len(sp.VIEWS)] == sp.VIEWS
    assert views[-1] == "options:dossier:MU"
    assert len(sp.VIEWS) == 10


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


def _render_texts(symbol):
    from nicegui import ui
    before = set(ui.context.client.elements)
    sp.render(symbol)
    return [getattr(e, "text", "") or ""
            for k, e in ui.context.client.elements.items() if k not in before]


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


def test_the_ct_today_is_what_the_bands_are_dated_by():
    # The day envelope is stamped in CENTRAL time; a host-local date would
    # disagree around midnight for anyone not on CT.
    assert sp.today_ct() == dt.datetime.now(sp._CT).date().isoformat()
