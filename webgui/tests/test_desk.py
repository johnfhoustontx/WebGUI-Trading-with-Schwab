"""Pure display logic for the Desk (/desk).

Every builder here takes plain dicts and returns plain dicts, so the whole
screen's arithmetic is testable without a browser — the same shape
``pages/market.py`` proved out.
"""
import ast
import datetime
import inspect
import pathlib
import re

import pytest
import voice
from pages import desk as d


@pytest.fixture(autouse=True)
def _no_synthesis_over_the_network(monkeypatch):
    """NO test may reach the edge-tts endpoint.

    render() warms the flow-clip cache on its first build, and prewarm
    is a daemon thread that synthesizes over the network and writes mp3s under
    webgui/data/voice — a live call and an on-disk side effect from a smoke
    test that only wanted to read some labels back. ensure is stubbed for
    the same reason. The _PREWARMED latch is reset per test so the order
    tests run in cannot decide which one exercises the prewarm path.
    """
    monkeypatch.setattr(voice, "prewarm", lambda *a, **k: None)
    monkeypatch.setattr(voice, "ensure", lambda *a, **k: None)
    monkeypatch.setitem(d._PREWARMED, "done", False)


# The Tailwind-first guard for this module now lives with every other page's, in
# ``test_no_inline_style.py``'s ``PHASE_8_FILES``. One guard, not two — a second
# copy is a second thing to forget.


# ── structure_positions ──────────────────────────────────────────────────────
def test_structure_positions_places_spot_between_the_walls():
    p = d.structure_positions(spot=100.0, flip=99.0, put_wall=95.0, call_wall=105.0)
    assert 0.0 <= p["put_wall"] < p["spot"] < p["call_wall"] <= 100.0
    assert p["spot"] == 50.0


def test_structure_positions_is_none_without_both_walls():
    assert d.structure_positions(100.0, 99.0, None, 105.0) is None
    assert d.structure_positions(100.0, 99.0, 95.0, None) is None
    assert d.structure_positions(None, 99.0, 95.0, 105.0) is None


def test_structure_positions_clamps_spot_outside_the_walls():
    p = d.structure_positions(spot=110.0, flip=99.0, put_wall=95.0, call_wall=105.0)
    assert p["spot"] == 100.0


def test_structure_positions_survives_a_degenerate_span():
    assert d.structure_positions(100.0, 100.0, 100.0, 100.0) is None


def test_structure_positions_omits_flip_when_absent_but_keeps_the_bar():
    p = d.structure_positions(100.0, None, 95.0, 105.0)
    assert p is not None and p["flip"] is None


def test_structure_positions_refuses_non_finite_inputs_rather_than_pinning_a_wall():
    """A NaN must NOT render as a position — the documented app-wide trap.

    ``min(100.0, max(0.0, nan))`` is **0.0** (every comparison against NaN is
    False, so ``max`` keeps its running value), which would draw an absent spot
    sitting exactly ON the put wall — the single most alarming thing the bar can
    say. ``+inf`` clamps the other way, onto the call wall. Both are "no
    reading" dressed as an extreme one, so the bar is withheld instead.
    """
    nan, inf = float("nan"), float("inf")
    assert d.structure_positions(nan, 99.0, 95.0, 105.0) is None
    assert d.structure_positions(inf, 99.0, 95.0, 105.0) is None
    assert d.structure_positions(100.0, 99.0, nan, 105.0) is None
    assert d.structure_positions(100.0, 99.0, 95.0, nan) is None
    # A non-finite FLIP only costs the flip tick; the bar itself still stands.
    p = d.structure_positions(100.0, nan, 95.0, 105.0)
    assert p is not None and p["flip"] is None


def test_structure_positions_never_raises_on_junk():
    assert d.structure_positions("x", None, "y", "z") is None
    assert d.structure_positions({}, [], object(), 105.0) is None


# ── dealer_rows ──────────────────────────────────────────────────────────────
def _mrow(sym, **over):
    """One ``cache:options:matrix`` row, in the shape options_svc publishes."""
    row = {"symbol": sym, "spot": 6712.81, "day_pct": 0.31,
           "trend_state": "up", "trend_dir": 0.42,
           "call_accel": "hot", "put_accel": "flat",
           "pc_ratio": 0.87, "net_prem_m": 12.4,
           "call_prem": 3e6, "put_prem": 8e5,
           "flip": 6680.0, "gex_regime": "above",
           "n_signals": 3, "n_alerts": 2, "signal": "buy",
           "signal_strength": 2, "hotness": 94, "eth_eligible": False,
           "call_wall": 6800.0, "put_wall": 6600.0, "net_gex": 1.42e9,
           "atm_iv": 13.4, "iv_state": "stable",
           "dealer_regime": "delta_wall_pin"}
    row.update(over)
    return row


def test_dealer_rows_selects_and_orders_the_desk_symbols():
    """Asserted against ``DESK_SYMBOLS`` itself, never a written-out list.

    The panel's subtitle is built from that tuple and the rows are emitted in
    its order, so a reorder there has to reach this expectation — and a copy of
    the order written down here is exactly how the two stop agreeing."""
    view = {"rows": [_mrow(s) for s in reversed(d.DESK_SYMBOLS)] + [_mrow("AAPL")]}
    assert [r["symbol"] for r in d.dealer_rows(view, stale=False)] == \
        list(d.DESK_SYMBOLS)


def test_desk_symbols_pair_each_index_with_its_tracking_etf():
    """$SPX/SPY and $NDX/QQQ sit adjacent, index first.

    The two rows a reader compares are an index and the ETF that tracks it, and
    a comparison that needs the reader to skip a row is one they will not make.
    """
    assert d.DESK_SYMBOLS == ("$SPX", "SPY", "$NDX", "QQQ")


def test_dealer_rows_regime_word_comes_only_from_gex_regime():
    above = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime="above", net_gex=-5.0)]},
                          stale=False)[0]
    assert above["regime_word"] == "LONG GAMMA · PINS"
    below = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime="below")]}, stale=False)[0]
    assert below["regime_word"] == "SHORT GAMMA · RUNS"


def test_dealer_rows_regime_word_is_a_dash_when_the_side_is_unknown():
    for regime in ("na", None, "", "sideways"):
        row = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime=regime)]},
                            stale=False)[0]
        assert row["regime_word"] == "—"


def test_dealer_rows_net_gex_is_carried_but_never_names_the_regime():
    """The magnitude is displayed; the WORD is ``gex_regime``'s alone.

    They can legitimately disagree, and printing two conflicting regime claims
    in one row is the sectors-vs-rotation bug all over again."""
    row = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime="below", net_gex=9.9e9)]},
                        stale=False)[0]
    assert row["net_gex"] == 9.9e9
    assert row["regime_word"] == "SHORT GAMMA · RUNS"


def test_dealer_rows_withhold_walls_when_stale():
    row = d.dealer_rows({"rows": [_mrow("$SPX")]}, stale=True)[0]
    assert row["call_wall"] is None and row["put_wall"] is None
    assert row["structure"] is None
    assert row["stale"] is True
    # The spot / flip read still stands — only the WALLS are untrustworthy.
    assert row["spot"] == 6712.81 and row["flip"] == 6680.0


def test_dealer_rows_withhold_walls_on_the_all_zero_grid_signature():
    """Index option OI reads 0 after hours → an all-zero GEX grid → ARBITRARY
    walls. A withheld wall is honest; a confident wrong one is not."""
    row = d.dealer_rows({"rows": [_mrow("$SPX", net_gex=0.0)]}, stale=False)[0]
    assert row["call_wall"] is None and row["put_wall"] is None
    assert row["structure"] is None


def test_dealer_rows_keeps_walls_when_net_gex_is_merely_absent():
    """Absent ≠ zero. A symbol that simply doesn't publish net GEX has not
    exhibited the all-zero-grid signature, so its walls are still usable."""
    row = d.dealer_rows({"rows": [_mrow("$SPX", net_gex=None)]}, stale=False)[0]
    assert row["call_wall"] == 6800.0 and row["put_wall"] == 6600.0
    assert row["structure"] is not None


def test_dealer_rows_flip_side_and_distance():
    above = d.dealer_rows({"rows": [_mrow("$SPX", spot=6700.0, flip=6600.0)]},
                          stale=False)[0]
    assert above["flip_side"] == "above"
    # A percent-of-flip magnitude, so $SPX and SPY compare at a glance.
    assert abs(above["flip_distance"] - 100.0 / 6600.0 * 100.0) < 1e-3
    below = d.dealer_rows({"rows": [_mrow("$SPX", spot=6500.0, flip=6600.0)]},
                          stale=False)[0]
    assert below["flip_side"] == "below"
    assert below["flip_distance"] > 0        # a magnitude; the SIDE carries sign


def test_dealer_rows_flip_side_is_none_without_a_usable_flip():
    row = d.dealer_rows({"rows": [_mrow("$SPX", flip=None)]}, stale=False)[0]
    assert row["flip_side"] is None and row["flip_distance"] is None


def test_dealer_rows_never_pins_a_flip_side_on_a_nan():
    row = d.dealer_rows({"rows": [_mrow("$SPX", flip=float("nan"))]}, stale=False)[0]
    assert row["flip"] is None and row["flip_side"] is None
    assert row["flip_distance"] is None


def test_dealer_rows_is_empty_for_a_missing_view():
    assert d.dealer_rows(None, stale=False) == []
    assert d.dealer_rows({}, stale=False) == []
    assert d.dealer_rows({"rows": "nonsense"}, stale=False) == []


def test_dealer_rows_tolerates_a_row_missing_every_new_key():
    rows = d.dealer_rows({"rows": [{"symbol": "$SPX"}]}, stale=False)
    assert rows[0]["symbol"] == "$SPX" and rows[0]["structure"] is None
    assert rows[0]["regime_word"] == "—"
    assert rows[0]["call_wall"] is None and rows[0]["net_gex"] is None


def test_dealer_rows_keeps_the_first_row_per_symbol_and_skips_junk():
    view = {"rows": ["junk", None, _mrow("SPY", spot=1.0), _mrow("SPY", spot=2.0)]}
    rows = d.dealer_rows(view, stale=False)
    assert [r["symbol"] for r in rows] == ["SPY"]
    assert rows[0]["spot"] == 1.0


# ── opportunity_rows ─────────────────────────────────────────────────────────
def test_opportunity_rows_takes_the_top_n_by_hotness_descending():
    """Asserted against ``BOARD_ROWS_N``, never a bare literal: the panel's row
    cap, its "HOTTEST N" subtitle and this expectation all read the same
    constant, and three independent numbers are how they stop agreeing."""
    n = d.BOARD_ROWS_N
    view = {"rows": [_mrow(f"S{i}", hotness=i) for i in range(n + 5)]}
    rows = d.opportunity_rows(view)
    assert [r["hotness"] for r in rows] == list(range(n + 4, 4, -1))
    assert [r["symbol"] for r in rows] == [f"S{i}" for i in range(n + 4, 4, -1)]
    assert len(rows) == n


def test_opportunity_rows_sorts_rows_without_hotness_to_the_bottom():
    view = {"rows": [_mrow("NOHOT", hotness=None), _mrow("HOT", hotness=1)]}
    assert [r["symbol"] for r in d.opportunity_rows(view)] == ["HOT", "NOHOT"]


def test_opportunity_rows_carries_the_decision_fields():
    row = d.opportunity_rows({"rows": [_mrow("AMD", atm_iv=41.2, iv_state="spiking",
                                             signal="sell", signal_strength=1,
                                             pc_ratio=1.44, net_prem_m=-8.1)]})[0]
    assert row["atm_iv"] == 41.2 and row["iv_state"] == "spiking"
    assert row["signal"] == "sell" and row["signal_strength"] == 1
    assert row["pc_ratio"] == 1.44 and row["net_prem_m"] == -8.1


def test_opportunity_rows_maps_the_dealer_setup_word():
    cases = {"gamma_cascade": "CASCADE", "vanna_squeeze": "VOL CRUSH",
             "delta_wall_pin": "PIN", "charm_grind": "GRIND",
             "neutral": "", "na": "", None: "", "nonsense": ""}
    for regime, word in cases.items():
        row = d.opportunity_rows({"rows": [_mrow("AMD", dealer_regime=regime)]})[0]
        assert row["setup"] == word, regime


def test_opportunity_rows_rationale_is_composed_from_real_state():
    row = d.opportunity_rows({"rows": [_mrow(
        "AMD", dealer_regime="neutral", gex_regime="below",
        trend_state="flat", call_accel="hot", put_accel="steady")]})[0]
    assert row["rationale"] == "below flip · call flow hot"


def test_opportunity_rows_rationale_is_empty_when_nothing_is_known():
    row = d.opportunity_rows({"rows": [{"symbol": "AMD", "hotness": 5}]})[0]
    assert row["rationale"] == ""
    assert row["setup"] == "" and row["atm_iv"] is None


def test_opportunity_rows_never_claims_an_iv_edge():
    """No ``rv``/``edge`` column, ever.

    Realized volatility does not exist anywhere in this app — nothing collects
    it, nothing publishes it — so an IV−RV "edge" cannot be computed. A column
    with nothing behind it would be indistinguishable from one that works.
    """
    rows = d.opportunity_rows({"rows": [_mrow("AMD")]})
    assert "rv" not in rows[0] and "edge" not in rows[0]
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    assert '"rv"' not in src and '"edge"' not in src


def test_opportunity_rows_is_empty_for_a_missing_view():
    assert d.opportunity_rows(None) == []
    assert d.opportunity_rows({}) == []
    assert d.opportunity_rows({"rows": ["junk", None]}) == []


# ── flow_rows ────────────────────────────────────────────────────────────────
def _alert(i, **over):
    a = {"id": f"a{i}", "ts": 1_700_000_000 + i, "symbol": "SPY",
         "type": "uoa", "side": "call", "strike": 500.0, "volume": 4000,
         "oi": 900, "vol_oi": 4.4, "premium": 2.1e6, "expiry": "2026-08-21",
         "dte": 3, "text": f"alert {i}"}
    a.update(over)
    return a


def test_flow_rows_takes_the_newest_n_newest_first():
    """Asserted against ``FLOW_ROWS_N``, never a bare literal: the panel's row
    cap and this expectation have to move together, and two independent numbers
    are exactly how they stop doing so."""
    n = d.FLOW_ROWS_N
    view = {"alerts": [_alert(i) for i in range(n + 4)]}  # service is oldest-first
    rows = d.flow_rows(view)
    assert [r["id"] for r in rows] == [f"a{i}" for i in range(n + 3, 3, -1)]
    assert len(rows) == n


def test_flow_rows_caps_at_the_row_count_the_panel_advertises():
    """The panel subtitle reads "NEWEST {FLOW_ROWS_N}", so a cap that did not
    match it would make the card lie about itself."""
    view = {"alerts": [_alert(i) for i in range(d.FLOW_ROWS_N * 3)]}
    assert len(d.flow_rows(view)) == d.FLOW_ROWS_N


def test_flow_rows_delegates_to_the_flow_pages_own_builder():
    """The Desk composes; it never re-formats. If these two ever diverge, the
    Desk's feed is contradicting the page it links to."""
    from pages.options import flow
    view = {"alerts": [_alert(i) for i in range(3)]}
    assert d.flow_rows(view) == flow.alert_rows(view)[:d.FLOW_ROWS_N]


def test_flow_kind_text_joins_the_kind_and_the_side_it_fired_on():
    assert d.flow_kind_text({"kind": "Unusual activity", "side": "Call"}) == \
        "Unusual activity · Call"


def test_flow_kind_text_drops_the_separator_when_a_half_is_missing():
    """A dangling ' · ' reads as a cell that failed to render."""
    assert d.flow_kind_text({"kind": "Crossover", "side": ""}) == "Crossover"
    assert d.flow_kind_text({"kind": "", "side": "Put"}) == "Put"
    assert d.flow_kind_text({}) == "—"
    assert d.flow_kind_text(None) == "—"


def test_flow_kind_text_never_claims_who_initiated():
    """Call/Put names the side of the book that moved. Schwab publishes no
    time-and-sales tape to this app, so nobody here knows who bought it."""
    blob = d.flow_kind_text({"kind": "Big delta", "side": "Call"}).lower()
    assert "buy" not in blob and "sell" not in blob


def test_flow_rows_never_claims_a_buy_or_sell_side():
    """Schwab gives this app no time-and-sales tape, so nothing here knows who
    initiated. ``flow_alerts.alert_text``'s own docstring says: no buy/sell
    claim — and the Desk must not add one by paraphrase."""
    rows = d.flow_rows({"alerts": [{"type": "uoa", "side": "call", "symbol": "SPY",
                                    "ts": 1, "id": "a", "text": "x"}]})
    blob = " ".join(str(v) for v in rows[0].values()).lower()
    assert "buy" not in blob and "sell" not in blob


def test_flow_rows_is_empty_for_a_missing_view():
    assert d.flow_rows(None) == []
    assert d.flow_rows({}) == []
    assert d.flow_rows({"alerts": "nonsense"}) == []


# ── position_rows / positions_summary ────────────────────────────────────────
def _pos(pid, **over):
    p = {"position_id": pid, "symbol": "SPY", "strategy": "put_credit_spread",
         "short_strike": 600.0, "long_strike": 595.0, "call_short": None,
         "call_long": None, "width": 5.0, "expiration": "2026-09-19",
         "quantity": 2, "entry_credit": 1.35, "current_value": 0.80,
         "unrealized_pnl": 110.0, "status": "OPEN", "rescue_state": "ok",
         "heat": 12}
    p.update(over)
    return p


def _sig(sid, **over):
    """A captured signal — the shape ``cache:options:captured`` really publishes.

    ⚠ Note what is NOT here, because these absences are the whole point of the
    third book: no ``quantity`` (advisory signals were never sized), no
    ``rescue_state`` and no ``heat`` (the manage cycle's rescue overlay only
    tags paper ACCOUNT positions), and the id key is ``signal_id``."""
    s = {"signal_id": sid, "scanner_type": "SWING", "symbol": "UAL",
         "strategy": "CCS", "short_strike": 130.0, "long_strike": 135.0,
         "call_short": None, "call_long": None, "width": 5.0,
         "expiration": "2026-08-28", "dte_at_entry": 10, "entry_credit": 1.59,
         "current_value": 0.5, "unrealized_pnl": 109.0, "current_score": 65,
         "recommendation": "HOLD", "status": "OPEN", "mode": "PREMIUM"}
    s.update(over)
    return s


def test_position_rows_merges_both_accounts_with_a_source_chip():
    rows = d.position_rows({"positions": [_pos("p1")]},
                           {"positions": [_pos("c1")]})
    assert [r["source"] for r in rows] == ["PAPER", "CLAUDE"]
    assert [r["position_id"] for r in rows] == ["p1", "c1"]


# ── the captured book ────────────────────────────────────────────────────────
def test_position_rows_merges_the_captured_book_as_a_third_source():
    rows = d.position_rows({"positions": [_pos("p1")]},
                           {"positions": [_pos("c1")]},
                           {"signals": [_sig("s1")]})
    assert sorted(r["source"] for r in rows) == ["CAPTURED", "CLAUDE", "PAPER"]
    cap = [r for r in rows if r["source"] == "CAPTURED"][0]
    assert cap["position_id"] == "s1"          # signal_id, not position_id


def test_position_rows_reads_the_captured_payloads_own_list_key():
    """``cache:options:captured`` publishes ``signals``, NOT ``positions``. A
    shared "positions" lookup would find nothing and the book would vanish from
    the panel with no error anywhere."""
    assert d.position_rows(None, None, {"signals": [_sig("s1")]}) != []
    assert d.position_rows(None, None, {"positions": [_sig("s1")]}) == []


def test_captured_rows_have_no_quantity_rather_than_a_default_of_one():
    """A captured signal was never sized. Printing 1 would state a position
    size this app does not have — and would look exactly like a real
    one-contract position, including inside any total built off the column."""
    row = d.position_rows(None, None, {"signals": [_sig("s1")]})[0]
    assert row["quantity"] is None
    # Even if a stray quantity turns up in the payload, an unsized book must not
    # start reporting one.
    stray = d.position_rows(None, None, {"signals": [_sig("s", quantity=4)]})[0]
    assert stray["quantity"] is None


def test_captured_rows_flag_is_an_em_dash_not_an_assertion_of_health():
    """The rescue overlay only tags the paper account, so a captured signal has
    no ``rescue_state``. Falling through to the "OK" default would print a clean
    bill of health nobody issued."""
    row = d.position_rows(None, None, {"signals": [_sig("s1")]})[0]
    assert row["flag"] == d.UNTAGGED_FLAG == "—"
    assert row["flag"] != d._DEFAULT_FLAG
    assert row["rescue_state"] is None and row["heat"] is None
    # And it is not merely a lookup miss: the same missing state inside a TAGGED
    # book still means healthy.
    paper = d.position_rows({"positions": [_pos("p", rescue_state=None)]},
                            None)[0]
    assert paper["flag"] == "OK"


def test_position_flag_needs_to_be_told_which_kind_of_missing_it_is():
    assert d.position_flag(None) == "OK"
    assert d.position_flag(None, rescue_tagged=False) == d.UNTAGGED_FLAG
    # An untagged book reports nothing even if a state somehow rides along.
    assert d.position_flag("critical", rescue_tagged=False) == d.UNTAGGED_FLAG


def test_captured_rows_carry_the_three_money_fields():
    row = d.position_rows(None, None, {"signals": [_sig("s1")]})[0]
    assert row["entry_credit"] == 1.59
    assert row["current_value"] == 0.5
    assert row["unrealized_pnl"] == 109.0
    assert row["strikes"] == "130.0/135.0"


def test_captured_rows_use_the_live_dte_not_the_entry_day_snapshot():
    """``dte_at_entry`` is the countdown as it stood the day the signal was
    found. Printing it would give every captured row a stale, too-large number
    while the paper rows beside it counted down."""
    from pages.options import paper
    row = d.position_rows(None, None,
                          {"signals": [_sig("s1", expiration="2026-09-19",
                                            dte_at_entry=99)]})[0]
    assert row["dte"] == paper._dte_from_expiration("2026-09-19")


def test_captured_rows_exclude_closed_signals():
    view = {"signals": [_sig("open"), _sig("shut", status="CLOSED"),
                        _sig("gone", status="EXPIRED")]}
    assert [r["position_id"] for r in d.position_rows(None, None, view)] == \
        ["open"]


def test_position_rows_still_works_without_a_captured_view():
    """The third argument is optional, so nothing that reads the two paper books
    alone had to change."""
    assert d.position_rows({"positions": [_pos("p")]}, None)[0]["source"] == \
        "PAPER"
    assert d.position_rows(None, None, None) == []
    assert d.position_rows(None, None, {"signals": "nonsense"}) == []


def test_every_book_has_a_chip_and_a_page_to_open():
    """A book with no route would strand its rows on the Desk, and a book
    sharing another's chip would make a merged row unreadable."""
    sources = [b["source"] for b in d.BOOKS]
    assert sources == ["PAPER", "CLAUDE", "CAPTURED"]
    assert set(d.POSITION_ROUTES) == set(sources)
    assert len({d.source_chip_class(s) for s in sources}) == 3
    # An unknown source must not borrow a real book's chip — otherwise a
    # malformed row would render as one of the three.
    assert d.source_chip_class("nonsense") not in \
        {d.source_chip_class(s) for s in sources}


def test_strikes_text_falls_back_to_the_call_side_for_an_iron_condor():
    assert d.strikes_text({"short_strike": 600.0, "long_strike": 595.0}) == \
        "600.0/595.0"
    assert d.strikes_text({"short_strike": None, "long_strike": None,
                           "call_short": 620.0, "call_long": 625.0}) == \
        "620.0/625.0"
    assert d.strikes_text({}) == "—"


# ── the cap, and what must survive it ────────────────────────────────────────
def test_position_rows_sorts_the_at_risk_states_above_everything_else():
    """The cap is only safe because of this order. A cap that hid a RESCUE row
    while showing a healthy one would be a real defect."""
    rows = d.position_rows({"positions": [
        _pos("calm", rescue_state="ok", expiration="2026-08-20"),
        _pos("watch", rescue_state="watch", expiration="2026-08-21"),
        _pos("tested", rescue_state="tested", expiration="2026-12-31"),
        _pos("rescue", rescue_state="critical", expiration="2026-12-31"),
    ]}, None)
    assert [r["position_id"] for r in rows][:2] == ["rescue", "tested"]
    # WATCH is a heads-up, not trouble — it does not jump the queue.
    assert [r["position_id"] for r in rows][2:] == ["calm", "watch"]


def test_position_rows_puts_held_trades_above_advisory_signals():
    """Measured live before this key existed: 30 captured signals at 2 DTE
    against 3 paper positions at 9 DTE meant every visible row was a captured
    signal, and a panel titled POSITIONS showed no positions at all. Money at
    risk outranks a suggestion nobody acted on."""
    rows = d.position_rows(
        {"positions": [_pos("held", expiration="2027-01-15")]}, None,
        {"signals": [_sig(f"s{i}", expiration="2026-08-20") for i in range(5)]})
    assert rows[0]["position_id"] == "held"
    assert all(r["source"] == "CAPTURED" for r in rows[1:])


def test_urgency_outranks_the_held_tier():
    """The held tier sits BELOW at-risk, never above it: a tested captured
    signal would still lead a calm paper position. (Today that ordering is
    unreachable from real data — see the test below — so this pins it on the two
    books that DO carry states, where an inverted key order would show up as a
    calm row leading a tested one.)"""
    rows = d.position_rows(
        {"positions": [_pos("calm", rescue_state="ok",
                            expiration="2026-08-20")]},
        {"positions": [_pos("tested", rescue_state="tested",
                            expiration="2027-01-15")]})
    assert [r["position_id"] for r in rows] == ["tested", "calm"]


def test_an_untagged_book_can_never_enter_the_urgency_tier():
    """Belt and braces on ``UNTAGGED_FLAG``: even if a rescue state somehow
    rode along in a captured payload, the book is not one the manage cycle
    inspects, so the state is dropped rather than acted on. A page that sorted
    on a state it refuses to display would be arguing with itself."""
    rows = d.position_rows(
        {"positions": [_pos("calm", rescue_state="ok",
                            expiration="2026-08-20")]}, None,
        {"signals": [_sig("stray", rescue_state="critical",
                          expiration="2026-08-19")]})
    assert rows[0]["position_id"] == "calm"     # held, calm tier
    assert rows[1]["rescue_state"] is None      # still untagged
    assert rows[1]["flag"] == d.UNTAGGED_FLAG


def test_position_rows_breaks_ties_on_the_nearest_expiry():
    """Nearest expiry, not largest P&L: size is not urgency. A $500 loser at 45
    DTE has weeks to mean-revert; a spread expiring tomorrow has to be decided
    today."""
    rows = d.position_rows({"positions": [
        _pos("far", expiration="2027-01-15", unrealized_pnl=-500.0),
        _pos("near", expiration="2026-08-20", unrealized_pnl=-5.0),
        _pos("mid", expiration="2026-10-16", unrealized_pnl=-50.0),
    ]}, None)
    assert [r["position_id"] for r in rows] == ["near", "mid", "far"]


def test_position_rows_sorts_an_unreadable_expiry_last_not_first():
    """An expiration that will not parse is not evidence of urgency."""
    rows = d.position_rows({"positions": [
        _pos("junk", expiration="soon"),
        _pos("dated", expiration="2027-01-15"),
    ]}, None)
    assert [r["position_id"] for r in rows] == ["dated", "junk"]


def test_the_cap_never_hides_a_trade_in_trouble():
    """The whole-book test: many calm captured signals against one critical
    paper position, and the critical one still lands inside the visible slice."""
    captured = {"signals": [_sig(f"s{i}", expiration="2026-08-20")
                            for i in range(30)]}
    paper = {"positions": [_pos("hot", rescue_state="critical",
                                expiration="2027-06-18")]}
    rows = d.position_rows(paper, None, captured)
    assert len(rows) == 31
    visible = rows[:d.POSITION_ROWS_N]
    assert visible[0]["position_id"] == "hot"


def test_positions_summary_totals_the_whole_book_not_the_visible_slice():
    """⚠ The load-bearing invariant of the cap. Unrealized P&L and the at-risk
    count are BOOK-level facts; computing them off the drawn rows would
    understate both — and the at-risk count is the one number on this panel
    somebody acts on."""
    # More at-risk positions than the panel can draw, so the cap has to swallow
    # some of them however the list is ordered — which is exactly the day the
    # header must not report zero.
    n_risk = d.POSITION_ROWS_N + 4
    paper = {"positions": [
        _pos(f"r{i}", rescue_state="tested", expiration="2026-09-18",
             unrealized_pnl=-100.0) for i in range(n_risk)]}
    captured = {"signals": [_sig(f"s{i}", expiration="2026-08-20",
                                 unrealized_pnl=10.0) for i in range(30)]}
    rows = d.position_rows(paper, None, captured)
    total = n_risk + 30
    assert len(rows) == total > d.POSITION_ROWS_N

    full = d.positions_summary(rows)
    assert full["open"] == total
    assert abs(full["unrealized"] - (30 * 10.0 - n_risk * 100.0)) < 1e-9
    assert full["at_risk"] == n_risk

    # What the panel would report if it summarised only what it draws — the
    # exact bug this test exists to keep out. Every number is understated.
    sliced = d.positions_summary(rows[:d.POSITION_ROWS_N])
    assert sliced["open"] == d.POSITION_ROWS_N < full["open"]
    assert sliced["at_risk"] < full["at_risk"]
    assert sliced["unrealized"] != full["unrealized"]


def test_position_rows_excludes_closed_positions():
    view = {"positions": [_pos("open"), _pos("shut", status="CLOSED"),
                          _pos("gone", status="EXPIRED")]}
    assert [r["position_id"] for r in d.position_rows(view, None)] == ["open"]


def test_position_rows_maps_the_rescue_state_to_a_flag():
    flags = {"ok": "OK", "watch": "WATCH", "tested": "AT RISK",
             "critical": "RESCUE"}
    for state, word in flags.items():
        row = d.position_rows({"positions": [_pos("p", rescue_state=state)]},
                              None)[0]
        assert row["flag"] == word, state
    # An unknown / missing state falls back to the healthy word rather than
    # inventing an alarm.
    assert d.position_rows({"positions": [_pos("p", rescue_state=None)]},
                           None)[0]["flag"] == "OK"


def test_position_rows_dte_uses_the_paper_pages_own_helper():
    """Same helper, same answer — the Desk must not carry a second calendar."""
    from pages.options import paper
    row = d.position_rows({"positions": [_pos("p", expiration="2026-09-19")]},
                          None)[0]
    assert row["dte"] == paper._dte_from_expiration("2026-09-19")


def test_position_rows_dte_is_none_for_an_unparseable_expiration():
    row = d.position_rows({"positions": [_pos("p", expiration="soon")]}, None)[0]
    assert row["dte"] is None


def test_position_rows_is_empty_for_missing_views():
    assert d.position_rows(None, None) == []
    assert d.position_rows({}, {}) == []
    assert d.position_rows({"positions": "nonsense"}, None) == []


def test_positions_summary_counts_open_unrealized_and_at_risk():
    rows = d.position_rows({"positions": [
        _pos("a", unrealized_pnl=110.0, rescue_state="ok"),
        _pos("b", unrealized_pnl=-40.0, rescue_state="watch"),
        _pos("c", unrealized_pnl=25.5, rescue_state="tested"),
        _pos("d", unrealized_pnl=-5.5, rescue_state="critical"),
    ]}, None)
    s = d.positions_summary(rows)
    assert s["open"] == 4
    assert abs(s["unrealized"] - 90.0) < 1e-9
    # WATCH is a heads-up, not a position in trouble; counting it would inflate
    # the one number on this card that is supposed to prompt action.
    assert s["at_risk"] == 2


def test_positions_summary_of_nothing_is_zeroed_not_none():
    assert d.positions_summary([])["open"] == 0
    assert d.positions_summary([])["unrealized"] == 0.0
    assert d.positions_summary(None)["at_risk"] == 0


def test_positions_summary_skips_a_non_finite_pnl_rather_than_poisoning_the_total():
    rows = d.position_rows({"positions": [
        _pos("a", unrealized_pnl=50.0),
        _pos("b", unrealized_pnl=float("nan")),
    ]}, None)
    assert d.positions_summary(rows)["unrealized"] == 50.0


# ── freshness_facts ──────────────────────────────────────────────────────────
def _status(**over):
    st = {"status_label": "Collecting", "status_color": "#22c55e",
          "last_scan": "9:31 AM", "next_scan": "9:32 AM",
          "age_seconds": 41, "session": "Regular"}
    st.update(over)
    return st


def test_freshness_facts_with_no_probe_is_unknown_not_live():
    """The drawer's status card rule: no probe data reads 'unknown', never a
    confident 'live'. This is also what gates ``dealer_rows(stale=…)``, so a
    wrong guess here promotes off-hours walls to trustworthy."""
    f = d.freshness_facts(None)
    assert f["stale"] is True and "unknown" in f["label"].lower()


def test_freshness_facts_is_unknown_for_a_view_with_no_age():
    for view in ({}, _status(age_seconds=None), _status(age_seconds="soon"),
                 _status(age_seconds=float("nan")), "nonsense"):
        f = d.freshness_facts(view)
        assert f["stale"] is True and "unknown" in f["label"].lower()


def test_freshness_facts_is_live_during_collection():
    f = d.freshness_facts(_status(age_seconds=41))
    assert f["stale"] is False and "live" in f["label"].lower()
    assert f["age_seconds"] == 41


def test_freshness_facts_is_stale_on_a_large_age():
    f = d.freshness_facts(_status(age_seconds=3600))
    assert f["stale"] is True and "stale" in f["label"].lower()


def test_freshness_facts_threshold_is_exactly_at_the_boundary():
    assert d.freshness_facts(_status(age_seconds=d.STALE_AFTER_SEC))["stale"] is False
    assert d.freshness_facts(
        _status(age_seconds=d.STALE_AFTER_SEC + 1))["stale"] is True


def test_freshness_facts_carries_the_collector_strip_fields_through():
    f = d.freshness_facts(_status())
    assert f["last_scan"] == "9:31 AM" and f["next_scan"] == "9:32 AM"
    assert f["session"] == "Regular"
    assert f["status_label"] == "Collecting"


# ── regime_display (+ the drift guard) ───────────────────────────────────────
# Payload shapes sentiment_svc actually publishes, plus the degenerate ones a
# cold start can produce. Every one of them goes through BOTH functions below.
_REGIME_SAMPLES = [
    # A normal committed sample: the service pre-renders `label`.
    {"label": "Rallying", "committed_label": "trending", "unclear": False,
     "direction": 1, "direction_strong": True, "confidence": 0.71},
    # `_unclear_shell` — no evidence at all.
    {"label": "Unclear", "committed_label": "", "unclear": True,
     "direction": 0, "direction_strong": False, "confidence": 0.0},
    # Weak evidence, but hysteresis is still holding a regime.
    {"label": "Balanced", "committed_label": "mean_reversion", "unclear": True,
     "direction": 0, "confidence": 0.11},
    # A payload with no pre-rendered label — the committed key must carry it.
    {"committed_label": "choppy", "unclear": False, "confidence": 0.4},
    # Nothing usable at all.
    {}, {"label": "", "committed_label": ""}, {"committed_label": "not_a_regime"},
    None,
]


def test_desk_regime_word_matches_console_regime_for_the_same_payload():
    """If these two ever disagree, the Desk contradicts the page it links to.

    This is the whole reason the derivation was extracted rather than copied:
    the app already ships one screen-pair printing opposite regime verdicts, and
    a copy is exactly how that happened."""
    from pages import console_regime
    for sample in _REGIME_SAMPLES:
        assert d.regime_display(sample)["word"] == console_regime.regime_name(sample)


def test_desk_regime_word_is_unclear_when_the_sample_is_unclear():
    # The shape the service publishes for an unclear read...
    assert d.regime_display(
        {"label": "Unclear", "committed_label": "", "unclear": True})["word"] == "Unclear"
    # ...and the same conclusion reached through the fallback, from a payload
    # carrying only the flag. `unclear` alone does NOT override a held commit —
    # see ``console_regime.regime_name``; the hysteresis commit exists precisely
    # so a weak sample does not blank a regime that is still in force.
    assert d.regime_display({"unclear": True})["word"] == "Unclear"


def test_desk_regime_word_falls_back_to_the_committed_key():
    assert d.regime_display({"committed_label": "crisis"})["word"] == "Stressed"
    assert d.regime_display({"committed_label": "choppy"})["word"] == "Whipsaw"


def test_desk_regime_display_carries_the_supporting_reads():
    r = d.regime_display({"label": "Rallying", "committed_label": "trending",
                          "confidence": 0.71, "direction": 1,
                          "direction_strong": True})
    assert r["word"] == "Rallying"
    assert r["confidence"] == 0.71
    assert r["direction"] == 1 and r["direction_strong"] is True
    assert r["unclear"] is False


def test_desk_regime_display_withholds_a_non_finite_confidence():
    """A NaN confidence must read as absent, not as a maximal one — the same
    trap that made an all-NaN price read score 92.5 at confidence 1.0."""
    assert d.regime_display({"confidence": float("nan")})["confidence"] is None
    assert d.regime_display({"confidence": None})["confidence"] is None


def test_desk_regime_display_survives_a_missing_view():
    assert d.regime_display(None)["word"] == "Unclear"
    assert d.regime_display("nonsense")["word"] == "Unclear"


# ── formatters ───────────────────────────────────────────────────────────────
# The render layer's own pure surface. It is small, but every one of these is a
# place a "no reading" can be dressed up as a reading, which is the failure mode
# this whole page is built to avoid.
def test_formatters_render_an_em_dash_for_every_kind_of_no_reading():
    """None / NaN / inf / junk must ALL read as absent — never as 0.

    A zero is a claim. "$0.00 unrealized" and "0.00% day" are readings a trader
    would act on, and neither is what an unpublished field means."""
    for junk in (None, float("nan"), float("inf"), float("-inf"), "x", {}, [],
                 True):
        for fn in (d.fmt_price, d.fmt_signed_pct, d.fmt_gex, d.fmt_money,
                   d.fmt_net_prem, d.fmt_iv, d.fmt_ratio, d.fmt_hotness):
            assert fn(junk) == "—", (fn.__name__, junk)


def test_fmt_price_and_pct_shapes():
    assert d.fmt_price(6712.81) == "6,712.81"
    assert d.fmt_signed_pct(0.31) == "+0.31%"
    assert d.fmt_signed_pct(-1.2) == "-1.20%"
    # A genuine zero IS a reading and prints as one, signed.
    assert d.fmt_signed_pct(0.0) == "+0.00%"


def test_fmt_gex_scales_and_always_carries_a_sign():
    assert d.fmt_gex(1.42e9) == "+1.42B"
    assert d.fmt_gex(-5.4e8) == "-540M"
    assert d.fmt_gex(-2_000) == "-2K"
    assert d.fmt_gex(7) == "+7"


def test_fmt_money_puts_the_minus_outside_the_dollar_sign():
    assert d.fmt_money(110.0) == "$110.00"
    assert d.fmt_money(-40.0) == "-$40.00"


def test_fmt_net_prem_scales_exactly_once():
    """``net_prem_m`` arrives ALREADY in millions. Scaling it again here is the
    classic way this column starts printing a plausible thousand-fold error."""
    assert d.fmt_net_prem(12.4) == "+12.4M"
    assert d.fmt_net_prem(-8.1) == "-8.1M"


def test_flip_text_drops_the_side_word_when_the_side_is_unknown():
    row = d.dealer_rows({"rows": [_mrow("$SPX", spot=6700.0, flip=6600.0)]},
                        stale=False)[0]
    assert d.flip_text(row).startswith("6,600.00 · 1.5")
    assert d.flip_text(row).endswith("% above")
    # No flip at all -> an em-dash, not a bare "above".
    noflip = d.dealer_rows({"rows": [_mrow("$SPX", flip=None)]}, stale=False)[0]
    assert d.flip_text(noflip) == "—"
    # A level with no usable side prints the level alone — never a default side,
    # which would be a claim about dealer hedging nothing supports.
    assert d.flip_text({"flip": 100.0, "flip_side": None,
                        "flip_distance": None}) == "100.00"


def test_strategy_and_dte_text():
    assert d.strategy_label("put_credit_spread") == "PUT CREDIT SPREAD"
    assert d.strategy_label(None) == "—"
    assert d.dte_text(0) == "0DTE"
    assert d.dte_text(32) == "32d"
    assert d.dte_text(None) == "—"


def test_summary_line_reads_the_three_numbers_the_header_promises():
    line = d.summary_line({"open": 4, "unrealized": 90.0, "at_risk": 2})
    assert line == "OPEN 4 · UNREALIZED $90.00 · AT RISK 2"
    # An empty book still reads as a book, not as a broken one.
    assert d.summary_line(d.positions_summary([])) == \
        "OPEN 0 · UNREALIZED $0.00 · AT RISK 0"


def test_summary_line_says_how_many_rows_it_is_hiding():
    """A positions panel that silently truncates the book is dangerous — the
    reader cannot tell "three open trades" from "three of thirty-six"."""
    line = d.summary_line({"open": 36, "unrealized": -258.0, "at_risk": 1},
                          shown=8)
    assert line == "OPEN 36 · SHOWING 8 · UNREALIZED -$258.00 · AT RISK 1"


def test_summary_line_omits_the_shown_clause_when_nothing_is_hidden():
    """A permanent 'SHOWING 3' on a three-row book is noise, and noise is what
    trains the eye to skip the clause on the day it means something."""
    s = {"open": 3, "unrealized": 10.0, "at_risk": 0}
    assert "SHOWING" not in d.summary_line(s, shown=3)
    assert "SHOWING" not in d.summary_line(s, shown=None)
    # Defensive: a caller that over-reports cannot produce "SHOWING 9 of 3".
    assert "SHOWING" not in d.summary_line(s, shown=9)


def test_expiry_text_pairs_the_date_with_its_countdown():
    assert d.expiry_text({"expiration": "2026-08-28", "dte": 10}) == \
        "2026-08-28 · 10d"
    assert d.expiry_text({"expiration": "2026-08-28", "dte": 0}) == \
        "2026-08-28 · 0DTE"


def test_expiry_text_never_leaves_a_dangling_separator():
    assert d.expiry_text({"expiration": "2026-08-28", "dte": None}) == \
        "2026-08-28"
    assert d.expiry_text({"expiration": "", "dte": 4}) == "4d"
    assert d.expiry_text({}) == "—"
    assert d.expiry_text(None) == "—"


# ── class maps ───────────────────────────────────────────────────────────────
def test_every_class_map_covers_its_whole_finite_domain_distinctly():
    """The styling standard's rule: a data-driven colour maps from a KNOWN
    finite set to static classes. If two states share a class the reader cannot
    tell them apart, which for the position flags is the difference between a
    healthy trade and one that needs rescuing."""
    regimes = [d.regime_word(k) for k in ("above", "below")]
    assert len(set(d.regime_chip_class(w) for w in regimes)) == 2
    flags = list(d.POSITION_FLAGS.values())
    assert len(set(d.flag_chip_class(f) for f in flags)) == len(set(flags))
    assert d.source_chip_class(d.PAPER_SOURCE) != \
        d.source_chip_class(d.CLAUDE_SOURCE)
    ivs = ("spiking", "collapsing", "stable", "na")
    assert len(set(d.iv_state_class(s) for s in ivs)) == len(ivs)


def test_unknown_states_fall_back_to_a_neutral_class_rather_than_a_verdict():
    """An unrecognised state must not borrow either verdict's colour."""
    assert d.regime_chip_class("—") == d.CHIP_MUTED
    assert d.regime_chip_class("nonsense") == d.CHIP_MUTED
    assert d.flag_chip_class("nonsense") == d.CHIP_MUTED
    assert d.iv_state_class("nonsense") == d.iv_state_class("na")
    assert d.flip_side_class(None) == d.flip_side_class("nonsense")


def test_signed_class_treats_a_missing_number_as_missing_not_as_flat():
    assert d.signed_class(1.0) != d.signed_class(-1.0)
    assert d.signed_class(None) == d.signed_class(0.0)     # both are "no move"
    assert d.signed_class(float("nan")) == d.signed_class(None)
    assert d.signed_class(1.0) != d.signed_class(None)


def test_no_class_map_emits_an_inline_style_or_a_var_arbitrary():
    """Two separate traps in one assertion. ``.style()`` is banned outright, and
    the bundled Tailwind JIT does NOT generate an arbitrary class containing
    ``var(...)`` — such a class silently produces no rule at all, so the colour
    just never appears and nothing fails."""
    every = ([d.CHIP_POS, d.CHIP_NEG, d.CHIP_NEG_STRONG, d.CHIP_WARN,
              d.CHIP_ACCENT, d.CHIP_MUTED]
             + [d.flag_chip_class(f) for f in d.POSITION_FLAGS.values()]
             + [d.iv_state_class(s) for s in ("spiking", "stable", "na")])
    for cls in every:
        assert "var(" not in cls
        assert "style" not in cls


# ── the Bull / Bear sector strip ─────────────────────────────────────────────
def _brow(symbol, label, trend, excess, **over):
    """One ``levels.sector`` row, in the shape ``compute.merge_live`` writes."""
    return {"symbol": symbol, "label": label,
            "raw": {"trend": trend, "excess": excess}, **over}


def test_desk_reads_the_bullbear_view():
    """Polled, wired, and wired to that view ALONE. A view missing from
    ``VIEWS`` is never probed; a region missing from ``_REGION_VIEWS`` never
    repaints when it moves; and a region carrying a SECOND dependency rebuilds
    eleven chips on every 2 s header bump, on a page that stays open all day for
    scores that change once a night."""
    assert "sentiment:bullbear" in d.VIEWS
    assert d._REGION_VIEWS["bullbear"] == ("sentiment:bullbear",)


def test_desk_reads_the_bullbear_axes_through_the_maps_own_accessor():
    """``bullbear.row_axes`` is public precisely so two page modules do not each
    hand-roll ``(row.get("raw") or {}).get("trend")`` and drift from ``_raw``'s
    policy — which is the only thing deciding whether a null ``raw`` degrades
    and a non-dict row raises. A hand-rolled pair is output-equivalent TODAY,
    which is exactly why no behaviour test can hold this and a source one must.
    """
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    assert "_bb.row_axes(" in src
    # ``raw`` is the block ``_raw`` owns, and reaching for it by name is the one
    # way this gets hand-rolled. (A bare "trend" would false-positive: the
    # composite payload has a ``derived.trend`` of its own, read by the pill.)
    assert '"raw"' not in src and "'raw'" not in src


def test_desk_bullbear_strip_shows_every_sector():
    chips = d.bullbear_chips({"levels": {"sector": [
        _brow("XLV", "Health Care", 1.0, 0.1, participation=0.75, day_pct=0.4),
        _brow("XLU", "Utilities", -1.0, -0.1, participation=0.16,
              day_pct=-0.2)]}})
    assert [c["label"] for c in chips] == ["Health Care", "Utilities"]
    assert [c["symbol"] for c in chips] == ["XLV", "XLU"]
    assert [c["quadrant"] for c in chips] == ["rising_leading", "falling_lagging"]
    assert [c["thin"] for c in chips] == [False, True]      # 0.16 participation


def test_desk_bullbear_strip_is_empty_when_the_view_is_cold():
    """Four cold shapes. The third is the one that raises rather than degrades:
    ``render()`` seeds every view at build, and a service caught mid-restart
    publishes a payload of the wrong SHAPE, not an absent one."""
    assert d.bullbear_chips(None) == []
    assert d.bullbear_chips({}) == []
    assert d.bullbear_chips("nonsense") == []
    assert d.bullbear_chips({"levels": "nonsense"}) == []
    assert d.bullbear_chips({"levels": {"sector": []}}) == []


def test_desk_bullbear_strip_orders_sectors_exactly_as_the_map_does():
    """Two screens ordering the same rows differently is a defect neither shows
    — the documented /sentiment/sectors-vs-/sentiment/rotation failure one level
    down. Asserted against ``bullbear.by_strength`` itself rather than against a
    hand-written order that happens to agree today."""
    from pages import bullbear as B
    rows = [_brow("A", "Alpha", 0.1, 0.2), _brow("B", "Beta", 0.9, -0.3),
            _brow("C", "Gamma", None, 0.5), _brow("D", "Delta", 0.5, 0.0)]
    chips = d.bullbear_chips({"levels": {"sector": rows}})
    assert [c["label"] for c in chips] == [r["label"] for r in B.by_strength(rows)]
    assert [c["label"] for c in chips][0] == "Beta"   # …and it really re-sorts


def test_desk_bullbear_breadth_keeps_no_track_apart_from_an_empty_one():
    """``breadth_width`` answers None for "there is no reading" and 0 for
    "nothing confirms" — two different drawings. A truthiness check at the call
    site collapses exactly the distinction the function exists to draw."""
    rows = [_brow("A", "Absent", 1.0, 1.0),
            _brow("B", "Zero", 1.0, 1.0, participation=0.0),
            _brow("C", "Full", 1.0, 1.0, participation=1.0)]
    chips = d.bullbear_chips({"levels": {"sector": rows}})
    assert [c["breadth"] for c in chips] == [None, 0, 100]
    assert [c["thin"] for c in chips] == [False, True, False]


def test_desk_bullbear_day_move_separates_absent_from_returned_not_from_flat():
    """``compute.merge_live`` (services/sentiment_svc/compute.py) leaves
    ``day_pct`` None only for a symbol the proxy OMITTED; one it returned with
    no usable percent field yields 0.0, because
    ``SchwabProxyClient._extract_change_pct`` (schwab-proxy/proxy_client.py)
    falls through to a literal 0.0. So the dash means "not returned", and
    "0.00%" is not proof of a flat tape."""
    rows = [_brow("A", "Absent", 1.0, 1.0),
            _brow("B", "Flat", 1.0, 1.0, day_pct=0.0),
            _brow("C", "Up", 1.0, 1.0, day_pct=0.4)]
    chips = d.bullbear_chips({"levels": {"sector": rows}})
    assert [c["day_text"] for c in chips] == ["—", "0.00%", "+0.40%"]


def test_desk_bullbear_strip_degrades_a_row_it_cannot_score():
    """Three broken shapes, because a mutant misses at the level nobody was
    thinking about: a null row, a row with neither ``raw`` nor a label, and a
    NaN axis — the app's documented trap, since every comparison against NaN is
    False and an unguarded one falls straight through to the falling branch."""
    rows = [None, {"symbol": "B"}, _brow("N", "Nan", float("nan"), 0.5)]
    chips = d.bullbear_chips({"levels": {"sector": rows}})
    assert [c["label"] for c in chips] == ["B", "Nan"]   # label falls back
    assert [c["quadrant"] for c in chips] == ["unknown", "unknown"]


# ── the poll contract ────────────────────────────────────────────────────────
def test_every_region_only_depends_on_views_the_page_actually_polls():
    """A region wired to a view outside ``VIEWS`` would never repaint: the poll
    would not be watching the counter that moves it."""
    assert set(d.VIEWS) == set(d.VIEWS)          # no duplicates in the tuple
    assert len(d.VIEWS) == len(set(d.VIEWS))
    for region, deps in d._REGION_VIEWS.items():
        assert set(deps) <= set(d.VIEWS), region


def test_every_polled_view_feeds_at_least_one_region():
    """The mirror of the test above: a view nothing reads is a Redis read on
    every tick, all day, for a number that never reaches the screen."""
    used = set()
    for deps in d._REGION_VIEWS.values():
        used.update(deps)
    assert used == set(d.VIEWS)


def test_the_dealer_panel_repaints_when_freshness_moves_not_only_the_matrix():
    """``gex_status`` GATES the walls (see ``_walls_trustworthy``). A dealer
    panel wired to the matrix alone would keep showing walls after the collector
    died, because the matrix version does not move when the feed stops."""
    assert "options:gex_status" in d._REGION_VIEWS["dealer"]


def test_the_top_strip_carries_no_index_quote_view():
    """Deliberate: the Dealer Positioning panel shows $SPX/SPY/QQQ with more
    context, and the two would come from different cache keys with independent
    version counters — a 2-second window could genuinely show two different
    prices for one symbol on one screen. $VIX used to ride ``options:header``
    (excluded from the matrix universe, so never a dealer row); since it was
    replaced by BIAS/SIGNAL the strip carries no quote at all."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    # ``prices`` is the header payload's quote map. Reading that key is the one
    # way this rule gets broken, and it is a one-line change away at all times.
    assert '"prices"' not in src and "'prices'" not in src


def test_the_page_mounts_no_highcharts():
    """Deliberate: nothing on this page is a time series, and this app's chart
    element collapses when it mounts hidden, has no ResizeObserver, and loses
    in-place updates the moment the stock module loads."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    assert "ui.highchart" not in src


# ── the compact Sentiment / Trend cards ──────────────────────────────────────
def test_sentiment_pill_prefers_live_over_the_newest_snapshot():
    """The console's own headline rule. A pill naming a different session than
    the Day meter beside it would be the Desk contradicting itself."""
    live = {"composite": {"bias": "Cautious", "total_score": 4.45}}
    snaps = [{"composite": {"bias": "Long", "total_score": 7.1}}]
    assert d.sentiment_pill_text(live, snaps) == "CAUTIOUS 4.45"
    assert d.sentiment_pill_text(None, snaps) == "LONG 7.10"


def test_sentiment_pill_drops_the_number_rather_than_printing_a_zero():
    """/sentiment's own formatter defaults a missing total to 0.0 and prints
    "CAUTIOUS 0.00" — a maximally bearish score nobody measured. This page never
    prints a reading it did not read."""
    assert d.sentiment_pill_text({"composite": {"bias": "Cautious"}}, []) == \
        "CAUTIOUS"
    assert d.sentiment_pill_text(
        {"composite": {"bias": "Cautious", "total_score": float("nan")}}, []
    ) == "CAUTIOUS"


def test_sentiment_pill_is_empty_without_a_bias():
    assert d.sentiment_pill_text(None, []) == ""
    assert d.sentiment_pill_text(None, None) == ""
    assert d.sentiment_pill_text("nonsense", "nonsense") == ""
    assert d.sentiment_pill_text({"composite": {"total_score": 5.0}}, []) == ""


def test_trend_pill_uses_the_sentiment_pages_own_state_vocabulary():
    """Imported, not restated — the five words are /sentiment's, and a sixth
    copy here is exactly the drift this page exists to avoid."""
    from pages import sentiment as S
    for state, word in S._TREND_SHORT.items():
        assert d.trend_pill_text({"trend": {"state": state}}) == word.upper()


def test_trend_pill_is_empty_for_an_unknown_or_absent_state():
    """The five words are readings; there is no sixth meaning 'no reading'."""
    assert d.trend_pill_text({"trend": {"state": "wat"}}) == ""
    assert d.trend_pill_text({}) == ""
    assert d.trend_pill_text(None) == ""
    assert d.trend_pill_text({"trend": "nonsense"}) == ""


# ── BIAS / SIGNAL, the strip tiles that replaced VIX ─────────────────────────


def test_signal_band_facts_are_the_consoles_own_two_tiles_in_its_order():
    """Both the label and the descriptor are lifted from the console's
    ``SIGNAL_TILE_DEFS``, so a wording change there reaches this strip instead
    of leaving two screens describing one number differently."""
    from pages import sentiment as S
    facts = d.signal_band_facts({"bias": "Long", "signal": "Bullish"})
    assert [f["key"] for f in facts] == ["bias", "signal"]
    defs = {x["key"]: x for x in S.SIGNAL_TILE_DEFS}
    for f in facts:
        assert f["label"] == defs[f["key"]]["label"]
        assert f["descriptor"] == defs[f["key"]]["descriptor"]


def test_signal_band_facts_read_the_two_words_off_derived():
    """``live_composite.signal_band`` writes them there; nothing is recomputed
    here, which is why this page needs no composite total at all."""
    facts = d.signal_band_facts({"bias": "Cautious", "signal": "Bearish"})
    assert [f["value"] for f in facts] == ["Cautious", "Bearish"]


def test_signal_band_facts_colour_each_tile_from_its_OWN_word():
    """The two carry DIFFERENT vocabularies — positioning (Long/Neutral/
    Cautious/Short) and strength (Strong Bull…Strong Bear) — so one shared tone
    would eventually paint a colour that contradicts the word beside it. At
    total 3.88 the band is ('Cautious', 'Bearish'): amber and red, not one
    colour twice."""
    facts = d.signal_band_facts({"bias": "Cautious", "signal": "Bearish"})
    assert facts[0]["cls"] == d.CON_WARN
    assert facts[1]["cls"] == d.CON_NEG
    assert facts[0]["cls"] != facts[1]["cls"]


def test_signal_band_facts_tone_every_word_signal_band_can_emit():
    """The producer's five bands, end to end — a word the desk cannot tone
    would render at the cold-cache grey while saying something definite."""
    import sys, pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]
                           / "sentiment-dashboard"))
    from live_composite import signal_band
    expected = {"Long": d.CON_POS, "Neutral": d.CON_WARN,
                "Cautious": d.CON_WARN, "Short": d.CON_NEG,
                "Strong Bull": d.CON_POS, "Bullish": d.CON_POS,
                "Bearish": d.CON_NEG, "Strong Bear": d.CON_NEG}
    for total in (9.5, 7.0, 5.0, 3.0, 1.0):
        _size, bias, signal = signal_band(total)
        facts = d.signal_band_facts({"bias": bias, "signal": signal})
        assert facts[0]["cls"] == expected[bias], bias
        assert facts[1]["cls"] == expected[signal], signal


def test_signal_band_facts_print_a_dash_for_a_cold_cache_never_neutral():
    """'Neutral' is a reading. A composite that has not published is not one,
    and the strip must not turn the absence into the middle band."""
    for cold in (None, {}, "nonsense", {"bias": None, "signal": ""}):
        facts = d.signal_band_facts(cold)
        assert [f["value"] for f in facts] == [d._DASH, d._DASH]
        assert all(f["cls"] == d.CON_TXT_MUTED for f in facts)


def test_signal_band_facts_survive_a_word_the_producer_has_not_shipped_yet():
    """The vocabulary is service-side and could grow. An unknown word still
    renders — toned by the same substring read the console falls back to."""
    facts = d.signal_band_facts({"bias": "Very Long", "signal": "Wat"})
    assert facts[0]["value"] == "Very Long" and facts[0]["cls"] == d.CON_POS
    assert facts[1]["value"] == "Wat" and facts[1]["cls"] == d.CON_WARN


def test_the_desk_band_words_match_the_console_tiles_for_one_payload():
    """The two screens read the same two fields off the same ``derived``, so
    for any payload their BIAS and SIGNAL text must be identical."""
    from pages import sentiment as S
    derived = {"size": "0.85x", "bias": "Cautious", "signal": "Bearish"}
    rows = S.signal_tile_rows(
        S.tiles({"composite": {"total_score": 3.88}}, None,
                (derived["size"], derived["bias"], derived["signal"])), None)
    console = {r["key"]: r["value"] for r in rows}
    for f in d.signal_band_facts(derived):
        assert f["value"] == console[f["key"]]


def test_the_desk_no_longer_polls_the_options_header_view():
    """VIX was its only reader. A view left in ``VIEWS`` with nothing reading
    it is a Redis probe every 2 s for the life of the session, plus a repaint
    trigger for a strip that cannot change because of it."""
    assert "options:header" not in d.VIEWS
    assert "options:header" not in d._REGION_VIEWS["strip"]
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    # The two payload fields the tile read. The WORD still appears in comments
    # explaining what the strip used to carry, which is worth keeping.
    assert '"vix"' not in src and "vix_regime" not in src


def test_the_strip_repaints_its_band_when_the_composite_moves():
    """BIAS and SIGNAL ride ``sentiment:composite``. Without it the two words
    would freeze at whatever the page built with."""
    assert "sentiment:composite" in d._REGION_VIEWS["strip"]


def test_the_compact_cards_reuse_the_consoles_own_hero_and_delta():
    """Size is the ONLY thing the Desk's cards change. Every number and every
    colour still comes from ``console_cards``/``console``, so the two renderings
    cannot say different things about one payload."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    assert "_CC.hero_parts" in src
    assert "_CC.delta_parts" in src
    assert "_K.meter_row" in src
    # …and it must not have grown a private copy of the band arithmetic.
    assert "score_band" not in src


# ── countdown_facts ──────────────────────────────────────────────────────────
def _ct(y, m, day, hh, mm, ss=0):
    from zoneinfo import ZoneInfo
    return datetime.datetime(y, m, day, hh, mm, ss,
                             tzinfo=ZoneInfo("America/Chicago"))


def test_countdown_counts_to_the_open_before_the_bell():
    # Wednesday 2026-08-19, regular session 08:30-15:00 CT.
    assert d.countdown_facts(_ct(2026, 8, 19, 7, 0)) == {
        "label": "TO OPEN", "text": "1:30:00", "state": "to_open"}


def test_countdown_counts_to_the_close_during_the_session():
    assert d.countdown_facts(_ct(2026, 8, 19, 12, 0, 30)) == {
        "label": "TO CLOSE", "text": "2:59:30", "state": "to_close"}


def test_countdown_counts_to_tomorrows_open_after_the_close():
    assert d.countdown_facts(_ct(2026, 8, 19, 16, 0)) == {
        "label": "TO OPEN", "text": "16:30:00", "state": "to_open"}


def test_countdown_rolls_a_weekend_to_mondays_open():
    """Hours are UNBOUNDED for exactly this case — 44 hours, not 20 with the
    day silently dropped."""
    assert d.countdown_facts(_ct(2026, 8, 22, 12, 0)) == {
        "label": "TO OPEN", "text": "44:30:00", "state": "to_open"}


def test_countdown_rolls_a_holiday_to_the_next_trading_day():
    """Labor Day 2026 is Monday 7 Sep: the countdown must name Tuesday's open,
    not a bell that never rings. The holiday comes from the shared NYSE
    calendar — this page carries no holiday list of its own."""
    from shared import market_calendar as mc
    assert mc.is_trading_day(datetime.date(2026, 9, 7)) is False
    assert d.countdown_facts(_ct(2026, 9, 7, 8, 0)) == {
        "label": "TO OPEN", "text": "24:30:00", "state": "to_open"}


def test_countdown_reads_a_naive_datetime_as_central():
    """The app's trading clock, and ``market_calendar``'s own rule for a naive
    input — so the two cannot disagree about which session a moment is in."""
    naive = datetime.datetime(2026, 8, 19, 12, 0)
    assert d.countdown_facts(naive)["text"] == "3:00:00"


def test_countdown_takes_every_session_bound_from_the_shared_calendar():
    """No time literal and no holiday list on this page: move the configured
    regular session and the countdown moves with it."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    assert "_cal.mins_to_close" in src
    assert "_cal.next_regular_open" in src
    for literal in ("08:30", "15:00", "16:00"):
        assert literal not in src


# ── render() smoke ───────────────────────────────────────────────────────────
# ``render()`` is otherwise unexercised: /desk has no route yet, so no shell
# smoke test reaches it. These build the page against the auto-index client and
# read the text back out — enough to catch a bad name, a stale handle, or (the
# one that matters) a cold service rendering as a confident zero.
def _rendered_texts():
    """Every label text ``render()`` just added, in build order."""
    from nicegui import ui
    from pages import desk

    before = set(ui.context.client.elements)
    desk.render()
    return [getattr(e, "text", None)
            for key, e in ui.context.client.elements.items()
            if key not in before]


def _rendered_classes():
    """Every class string ``render()`` just mounted. Some readings are drawn
    rather than written — a groove that is absent, empty or full says three
    different things and carries no text at all."""
    from nicegui import ui
    from pages import desk

    before = set(ui.context.client.elements)
    desk.render()
    return [" ".join(e._classes)
            for key, e in ui.context.client.elements.items() if key not in before]


def _click_handlers():
    """Every click handler ``render()`` just wired, in build order.

    Text alone cannot see a click-through, and a chip that draws but navigates
    nowhere is exactly the regression a smoke test should catch."""
    from nicegui import ui
    from pages import desk

    before = set(ui.context.client.elements)
    desk.render()
    return [listener.handler
            for key, el in ui.context.client.elements.items() if key not in before
            for listener in el._event_listeners.values()
            if listener.type == "click"]


def _seed_bus(monkeypatch, data):
    import bus_client
    monkeypatch.setattr(bus_client, "read_full",
                        lambda v: (data.get(v), 1 if v in data else None))
    monkeypatch.setattr(bus_client, "read", lambda v: data.get(v))


def _full_payloads():
    return {
        "sentiment:regime": {"label": "Rallying", "committed_label": "trending",
                             "confidence": 0.71, "direction": 1},
        "sentiment:composite": {
            "live": None,
            "derived": {"size": "0.85x", "bias": "Cautious",
                        "signal": "Bearish"},
        },
        "sentiment:history": {"snaps": []},
        "options:gex_status": _status(),
        "options:matrix": {"rows": [_mrow("$SPX")]},
        "options:flow_alerts": {"alerts": [_alert(1)]},
        "options:paper_account": {"positions": [_pos("p1",
                                                     rescue_state="tested")]},
        "options:driver_paper_account": {"positions": []},
    }


def test_render_gives_every_panel_its_own_placeholder_when_nothing_is_published():
    """One dead service must not blank the page — and a blank box is worse than
    a placeholder, because it looks like a rendering bug rather than a cold
    feed. Four panels, four placeholders."""
    from pages import desk
    texts = _rendered_texts()
    assert texts.count(desk.WAITING_OPTIONS) == 4


def test_render_with_nothing_published_prints_no_reading_it_did_not_read():
    """The failure this whole page is built to avoid. With every view cold there
    must be no live-feed claim, no zeroed position count, and no $0.00 P&L —
    each of which is a statement a trader could act on."""
    texts = [t for t in _rendered_texts() if t]
    assert not any(t.startswith("Live") for t in texts)
    assert not any(t.startswith("OPEN ") for t in texts)
    assert not any("$0.00" in t for t in texts)
    # The freshness read says so in as many words, rather than staying silent.
    assert any("unknown" in t.lower() for t in texts)


def test_render_paints_all_four_panels_from_a_full_payload_set(monkeypatch):
    _seed_bus(monkeypatch, _full_payloads())
    from pages import desk
    texts = [t for t in _rendered_texts() if t]
    assert desk.WAITING_OPTIONS not in texts
    assert "$SPX" in texts                       # a dealer row AND a board row
    assert "LONG GAMMA · PINS" in texts          # the dealer regime chip
    # The flow kind, now carrying the side it fired on in the same cell — the
    # rows are one line each, so the side is a qualifier rather than a column.
    assert "Unusual activity · Call" in texts
    assert "AT RISK" in texts                    # the position flag
    assert any(t.startswith("OPEN 1 ·") for t in texts)
    assert "Rallying" in texts                   # the regime word in the strip
    # The two band tiles that replaced VIX, each with the console's descriptor.
    assert "Cautious" in texts and "Bearish" in texts
    assert "MARKET DIRECTION" in texts and "STRENGTH & MOMENTUM" in texts


def test_render_never_puts_a_buy_or_sell_word_on_the_flow_feed(monkeypatch):
    """Schwab publishes no time-and-sales tape to this app, so nobody here knows
    who initiated. The alert rows say Call/Put and stop there; the Desk must not
    add a side by paraphrase, in a column header or anywhere else."""
    _seed_bus(monkeypatch, {"options:flow_alerts": {"alerts": [_alert(1)]}})
    blob = " ".join(t for t in _rendered_texts() if t).lower()
    assert "bought" not in blob and "sold" not in blob


def test_render_mounts_both_score_cards_with_the_consoles_own_anatomy(monkeypatch):
    """Head, hero and the three meters — the console card minus its footer. The
    two SCALE 0—100 metas are what prove there are two cards, not one."""
    payloads = _full_payloads()
    payloads["sentiment:composite"] = {
        "live": {"composite": {"bias": "Cautious", "total_score": 4.45}},
        "derived": {"trend": {"state": "lack_of_bearishness", "score": 39.0,
                              "confidence": 0.8}},
    }
    _seed_bus(monkeypatch, payloads)
    texts = [t for t in _rendered_texts() if t]
    assert "MARKET SENTIMENT" in texts and "MARKET TREND" in texts
    assert texts.count("SCALE 0—100") == 2
    assert texts.count("DAY READ") == 2
    assert "CAUTIOUS 4.45" in texts               # the sentiment hero pill
    assert "RESILIENT" in texts                   # the trend hero pill
    # Three meters per card, each captioned by ``console.meter_row``.
    for caption in ("DAY", "WEEK", "MONTH"):
        assert texts.count(caption) == 2


def test_render_drops_the_console_card_footers(monkeypatch):
    """The Desk is a glance surface and the whole card click-throughs to
    /sentiment, where the confidence meter, the verdict block and these two
    links all live at full size."""
    _seed_bus(monkeypatch, _full_payloads())
    texts = [t for t in _rendered_texts() if t]
    assert "MODEL CONFIDENCE" not in texts
    assert "COMPONENTS →" not in texts
    assert "TREND DETAIL →" not in texts


def test_render_survives_junk_in_every_view(monkeypatch):
    """Every payload here is the wrong SHAPE, not merely empty. A page that
    aggregates nine services will meet this eventually — a half-written cache
    key, an older writer, a service mid-restart."""
    from pages import desk
    _seed_bus(monkeypatch, {v: "nonsense" for v in desk.VIEWS})
    texts = [t for t in _rendered_texts() if t]
    # It degrades to the empty state rather than raising — but note it does NOT
    # degrade to the *waiting* state, because a malformed payload is not an
    # absent one and the page cannot tell the difference from here.
    assert "No open positions." in texts


# ── the Bull / Bear sector strip, mounted ────────────────────────────────────
def _bullbear_payload():
    return {
        "session_date": "2026-08-19",
        "quoted_at": "2026-08-20T09:31:02-05:00",
        # The verdict block ``compute.bullbear_view`` copies out of the nightly
        # cascade. Present in every real payload, printed by nothing.
        "regime": {"state": "suppressed", "label": "Suppressed",
                   "description": "Momentum-crash risk — the biggest losers "
                                  "rip hardest here."},
        "levels": {"sector": [
            _brow("XLK", "Technology", 0.42, 0.11, participation=0.8,
                  day_pct=1.2),
            _brow("XLU", "Utilities", -0.2, 0.05, participation=0.2,
                  day_pct=-0.3)]},
    }


def test_render_mounts_a_chip_per_sector_over_the_maps_own_count_sentence(
        monkeypatch):
    """The sentence is ``sentiment_bullbear.headline_line`` — the map's own,
    pluralisation included — so the two screens cannot report different counts
    off one payload."""
    _seed_bus(monkeypatch, {"sentiment:bullbear": _bullbear_payload()})
    texts = [t for t in _rendered_texts() if t]
    assert "Technology" in texts and "Utilities" in texts
    assert "Rising · Leading" in texts and "Falling · Leading" in texts
    assert "1 of 2 sectors rising and leading" in texts
    assert "+1.20%" in texts and "-0.30%" in texts


def test_render_never_prints_the_bullbear_regime_verdict(monkeypatch):
    """The payload carries ``regime`` and this strip must not read it.
    /sentiment/sectors and /sentiment/rotation already print OPPOSITE
    risk-on/risk-off headlines off quantities that are not commensurable
    (CLAUDE.md, 2026-08-17); the map answers that by counting rows and stopping,
    and a strip that pointed at it under a verdict would reopen it."""
    _seed_bus(monkeypatch, {"sentiment:bullbear": _bullbear_payload()})
    blob = " ".join(t for t in _rendered_texts() if t).lower()
    assert "suppressed" not in blob and "momentum-crash" not in blob
    assert "risk-on" not in blob and "risk-off" not in blob


def test_render_gives_the_bullbear_strip_its_own_cold_message():
    """A cold sentiment service is a different outage from a cold options one,
    so it must not borrow that placeholder — and it must not count to zero:
    ``B.headline`` returns "" on an empty payload precisely because "0 of 0
    sectors rising and leading" states a maximally bearish tape nobody read."""
    texts = [t for t in _rendered_texts() if t]
    assert d.WAITING_BULLBEAR in texts
    assert d.WAITING_BULLBEAR != d.WAITING_OPTIONS
    assert not any("rising and leading" in t for t in texts)


def test_render_wires_every_bullbear_chip_through_to_the_map(monkeypatch):
    """A chip is a pointer — the strip carries eleven sectors, the map under it
    carries the industries and stocks inside each. A chip that draws but does
    not navigate is the one failure a text-only smoke test cannot see. The two
    /sentiment clicks are the score cards, and they are what proves this counts
    only the chips."""
    from nicegui import ui
    routes = []
    monkeypatch.setattr(ui.navigate, "to", lambda r, *a, **k: routes.append(r))
    _seed_bus(monkeypatch, {"sentiment:bullbear": _bullbear_payload()})
    for handler in _click_handlers():
        handler(None)
    assert routes.count("/sentiment/bullbear") == 2
    assert routes.count("/sentiment") == 2


def test_render_draws_no_breadth_groove_where_there_is_no_reading(monkeypatch):
    """Three sectors, three different drawings — and none of them is text, so
    this is the one assertion that can see the distinction ``breadth_width``
    exists to draw. A sector whose members were all unusable gets NO groove; one
    where nothing confirms gets an empty groove; a broad one gets a filled bar.
    Collapsing the first two is the documented trap."""
    _seed_bus(monkeypatch, {"sentiment:bullbear": {"levels": {"sector": [
        _brow("A", "NoRead", 1.0, 1.0),
        _brow("B", "Empty", 1.0, 1.0, participation=0.0),
        _brow("C", "Broad", 1.0, 1.0, participation=0.8)]}}})
    classes = _rendered_classes()
    grooves = [c for c in classes if "rounded-full" in c and "overflow-hidden" in c]
    assert len(grooves) == 2                                   # not three
    assert [c for c in classes if "w-[0%]" in c]                # empty ≠ absent
    assert [c for c in classes if "w-[80%]" in c]


def test_desk_panel_grid_is_two_columns_at_every_width():
    """The 2x2 is fixed, not responsive (2026-08-20, by request).

    It was `grid-cols-1 min-[2300px]:grid-cols-2`. A responsive class here is
    the regression: the layout would silently rearrange itself at the width
    this page is actually read at, which is what the request removed.
    """
    import ast
    import inspect

    from pages import desk

    # Read the STRING LITERALS, not the raw source: the comment above the grid
    # quotes the old `grid-cols-1 min-[2300px]:grid-cols-2` value, and a
    # substring check over the source matches that and fails on a correct file.
    tree = ast.parse(inspect.getsource(desk.render).lstrip())
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    grids = [v for v in literals if "grid-cols" in v]
    assert "grid grid-cols-2 gap-5 w-full items-stretch" in grids
    for value in grids:
        assert "grid-cols-1" not in value, f"panel grid went responsive: {value}"
        assert "min-[" not in value, f"a width breakpoint came back: {value}"


def test_desk_panels_do_not_scroll_sideways():
    """`overflow-x-auto` is the tempting fix for the fixed 2x2 at narrow widths
    and is deliberately refused — see the note above `_GAP`: a dashboard you
    scroll sideways to read defeats the page's purpose. Pinned because the next
    person to hit a clipped row will reach for it."""
    import inspect

    from pages import desk
    assert "overflow-x-auto" not in inspect.getsource(desk._panel)


# ── the 1920px width budget ──────────────────────────────────────────────────
# A CSS grid never shrinks a track below its ``minmax()`` floor, so a panel
# whose floors oversubscribe its share of the window CLIPS its rows instead of
# reflowing — and `overflow-x-auto` is refused (see the test above). Three of
# the four grids shipped over budget until the type ladder and the floors were
# unwound together to the reference design's own scale; these are the guards
# that make the next widened track fail HERE rather than on screen.
def _floors(grid):
    """The pixel floor of every track in a grid class string, in order."""
    inner = grid.split("grid-cols-[", 1)[1].split("]", 1)[0]
    out = []
    for track in inner.split("_"):
        m = (re.fullmatch(r"minmax\((\d+)px,[\d.]+fr\)", track)
             or re.fullmatch(r"(\d+)px", track))
        assert m, f"unparsed track {track!r}"
        out.append(int(m.group(1)))
    return out


def _panel_width_needed(grid):
    """What one panel must be given before this grid stops clipping."""
    t = _floors(grid)
    return sum(t) + (len(t) - 1) * d.COL_GAP_PX + d.PANEL_PAD_PX


def _px(classes):
    """The `text-[Npx]` size out of a Tailwind class string."""
    return int(re.search(r"text-\[(\d+)px\]", classes).group(1))


def _head_calls():
    """Every ``_grid_head(GRID, (labels...))`` in ``render``, resolved."""
    tree = ast.parse(inspect.getsource(d.render).lstrip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_grid_head":
            yield (getattr(d, node.args[0].id),
                   [e.value for e in node.args[1].elts])


def test_every_panel_grid_fits_one_panel_at_the_1920px_window():
    """The four panels are a fixed 2x2, so each gets half the window less the
    chrome and the gutter. Over that, the row overflows its card."""
    for name in ("DEALER_GRID", "BOARD_GRID", "FLOW_GRID", "POS_GRID"):
        need = _panel_width_needed(getattr(d, name))
        assert need <= d.PANEL_BUDGET_PX, (
            f"{name} needs {need}px of the {d.PANEL_BUDGET_PX}px a panel gets")


def test_the_panel_budget_subtracts_the_scrollbar_and_is_860px():
    """Derived, so a chrome or gutter change moves it — and pinned at its value,
    because 860 is what the grid comment's arithmetic is written against.

    The scrollbar term is the half of this that is easy to drop: the page is
    taller than any window it is read in, so it is always there, and leaving it
    out reads 868px where the panel really gets 860."""
    assert d.PANEL_BUDGET_PX == 860 == (
        d.DESK_WINDOW_PX - d.DESK_SCROLLBAR_PX - d.DESK_CHROME_PX
        - d.PANEL_GUTTER_PX) // 2


def test_the_minimum_supported_window_above_the_grid_is_the_real_one():
    """That arithmetic is load-bearing documentation — it is what the next
    person sizes a track against — and nothing fails when it goes stale.
    Positions is the widest panel, so its floors ARE the minimum."""
    src = inspect.getsource(d.render)
    need = _panel_width_needed(d.POS_GRID)
    assert f"= {need}px minimum for one panel" in src
    window = need * 2 + d.PANEL_GUTTER_PX + d.DESK_CHROME_PX + 15   # + scrollbar
    assert f"{window}px of innerWidth" in src


def test_every_column_label_fits_the_track_it_stands_over():
    """Three floors here are LABEL-bound rather than value-bound: a label on
    .2em tracking does not shrink with the data under it, and a clipped label
    turns a column of numbers into an unlabelled column of numbers. JetBrains
    Mono advances 0.6em, and CSS adds the .2em after every character."""
    per_char = _px(d._HEAD) * 0.8
    for grid, labels in _head_calls():
        floors = _floors(grid)
        assert len(labels) == len(floors), (labels, floors)
        for label, floor in zip(labels, floors):
            assert len(label) * per_char <= floor, (label, floor)


def test_the_panel_type_ladder_stays_a_ladder():
    """Value over qualifier is what makes a ten-column row scannable. Scaling
    the ladder is allowed — it was scaled 0.8x to fit 1920 — but flattening it
    is not, so this pins the ORDER and the label's readability step, never the
    sizes themselves."""
    price, value, sub = _px(d._V_SPOT), _px(d._VALUE), _px(d._SUB)
    assert price > value > sub
    assert _px(d._HEAD) >= sub          # the documented +1 step, never below it


def test_each_panel_paints_its_head_and_its_rows_on_one_track_string():
    """The identity of the two grid strings is the only thing keeping a label
    over its column; if they drift, every number on the panel starts reading as
    the wrong quantity. Row painters interpolate the grid into an f-string, so
    the two uses are collected separately and compared."""
    tree = ast.parse(inspect.getsource(d.render).lstrip())
    heads = {n.args[0].id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_grid_head"}
    rows = {v.value.id for j in ast.walk(tree) if isinstance(j, ast.JoinedStr)
            for v in j.values
            if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
            and v.value.id.endswith("_GRID")}
    assert heads == rows and len(heads) == 4


# ── arrival detection ────────────────────────────────────────────────────────
def test_new_ids_reports_only_rows_not_seen_before():
    rows = [{"id": "c"}, {"id": "b"}, {"id": "a"}]
    assert d.new_ids(rows, {"a", "b"}) == ["c"]


def test_new_ids_preserves_row_order_so_the_newest_is_first():
    # ``flow.alert_rows`` is newest-first, and the newest new row is the one
    # that gets spoken. Order is load-bearing, not incidental.
    rows = [{"id": "c"}, {"id": "b"}, {"id": "a"}]
    assert d.new_ids(rows, set()) == ["c", "b", "a"]


def test_new_ids_counts_a_duplicated_id_once():
    rows = [{"id": "a"}, {"id": "a"}]
    assert d.new_ids(rows, set()) == ["a"]


def test_new_ids_skips_rows_with_no_id():
    assert d.new_ids([{"id": None}, {}, {"id": "a"}], set()) == ["a"]


def test_new_ids_reads_the_key_the_caller_names():
    # Positions key on position_id, flow on id. One function, not two.
    rows = [{"position_id": "p1"}]
    assert d.new_ids(rows, set(), key="position_id") == ["p1"]


def test_new_ids_of_nothing_is_empty():
    assert d.new_ids(None, set()) == []
    assert d.new_ids([], {"a"}) == []


def test_id_set_is_what_seen_is_replaced_with_each_paint():
    # REPLACED, not unioned — see ``id_set``'s docstring. A row with no id is
    # left out, matching ``new_ids``, so the two can never disagree about which
    # rows exist.
    rows = [{"id": "a"}, {"id": None}, {}, {"id": "b"}]
    assert d.id_set(rows) == {"a", "b"}
    assert d.id_set(None) == set()
    assert d.id_set([{"position_id": "p1"}], key="position_id") == {"p1"}


# ── flag changes ─────────────────────────────────────────────────────────────
def test_flag_changes_reports_a_moved_flag():
    rows = [{"position_id": "p1", "flag": "AT RISK"}]
    assert d.flag_changes(rows, {"p1": "OK"}) == ["p1"]


def test_flag_changes_ignores_an_unchanged_flag():
    rows = [{"position_id": "p1", "flag": "OK"}]
    assert d.flag_changes(rows, {"p1": "OK"}) == []


def test_a_first_sighting_is_not_a_flag_change():
    # It is an ARRIVAL, and new_ids already glows it. Counting it here too
    # would give a brand-new row two overlapping glows.
    rows = [{"position_id": "p1", "flag": "OK"}]
    assert d.flag_changes(rows, {}) == []


def test_flag_map_keys_positions_by_id():
    rows = [{"position_id": "p1", "flag": "OK"}, {"position_id": None,
                                                  "flag": "RESCUE"}]
    assert d.flag_map(rows) == {"p1": "OK"}


# ── the neon glow ────────────────────────────────────────────────────────────
def test_glow_step_starts_at_zero():
    assert d.glow_step(started=100.0, now=100.0) == 0


def test_glow_step_advances_one_class_per_second():
    assert d.glow_step(started=100.0, now=103.4) == 3
    assert d.glow_step(started=100.0, now=109.9) == 9


def test_glow_step_never_exceeds_the_last_class():
    # A rounding slip that returned 10 would emit desk-neon-10, a class with no
    # rule behind it — the animation would restart instead of finishing.
    assert d.glow_step(started=100.0, now=109.999999) == d.GLOW_STEPS - 1


def test_glow_step_is_none_once_expired():
    assert d.glow_step(started=100.0, now=110.0) is None
    assert d.glow_step(started=100.0, now=999.0) is None


def test_glow_step_is_none_for_a_row_that_never_glowed():
    assert d.glow_step(started=None, now=100.0) is None


def test_glow_classes_name_a_hue_and_a_resume_point():
    cls = d.glow_classes(("new", 100.0), now=103.0)
    assert "desk-neon" in cls and "desk-neon-new" in cls and "desk-neon-3" in cls


def test_glow_classes_are_empty_once_expired():
    assert d.glow_classes(("new", 100.0), now=120.0) == ""
    assert d.glow_classes(None, now=120.0) == ""


def test_every_glow_step_class_has_a_rule_behind_it():
    # The resume trick is silent when it breaks: a missing rule just restarts
    # the animation, which looks like a glow that never expires.
    css = d.DESK_NEON_CSS.replace(" ", "")
    for i in range(d.GLOW_STEPS):
        assert f".desk-neon-{i}{{" in css


def test_both_glow_hues_have_a_rule():
    assert ".desk-neon-new" in d.DESK_NEON_CSS
    assert ".desk-neon-flag" in d.DESK_NEON_CSS


def test_the_animation_runs_for_the_advertised_ten_seconds():
    assert d.GLOW_SEC == 10.0
    assert "animation-name: deskNeon;" in d.DESK_NEON_CSS
    assert f"animation-duration: {d.GLOW_SEC:g}s;" in d.DESK_NEON_CSS


def _base_neon_rule():
    """The body of the ``.desk-neon`` rule — the base the step rules refine."""
    css = d.DESK_NEON_CSS
    start = css.index(".desk-neon {")
    return css[start:css.index("}", start)]


def test_the_base_rule_declares_no_delay_for_a_step_rule_to_out_order():
    """THE trick, and the one thing about it a reader would not guess.

    ``.desk-neon`` and ``.desk-neon-3`` are both a single class, so specificity
    cannot break the tie between them — whichever declares ``animation-delay``
    LAST in source order wins. The base rule therefore must not declare one at
    all, and in particular must not use the ``animation:`` SHORTHAND, which
    resets every ``animation-*`` longhand it does not name (``animation-delay``
    back to 0s included) whether the author meant it or not.

    Written as a shorthand it happens to work only because the step rules are
    concatenated last in the f-string — an invariant nothing but string order
    holds up, whose failure mode is a glow that never expires and so is
    indistinguishable from the feature not having been built. Longhands make it
    structural: the step rule wins wherever it sits in the file.
    """
    base = _base_neon_rule()
    assert "animation:" not in base           # the resetting shorthand
    assert "animation-delay" not in base
    for i in range(d.GLOW_STEPS):
        assert f".desk-neon-{i} {{ animation-delay:" in d.DESK_NEON_CSS


def test_the_glow_does_not_fill_forwards_over_the_rows_hover():
    """``animation-fill-mode: forwards`` would kill the row's hover cue.

    Animation declarations outrank normal author declarations (CSS Cascade
    §6.6.2), so an element still applying the 100% keyframe —
    ``background-color: transparent`` — beats the row's ``hover:bg-…`` for as
    long as the class stays on it. That is until the next rebuild: seconds
    during market hours, but the rest of the session for an alert arriving at
    15:59, on a row that is ``cursor-pointer`` and click-navigates. The cue
    dies on exactly the row the user was just told to look at.

    ``forwards`` buys nothing here: the 100% keyframe (transparent, no shadow)
    IS the row's author default, so dropping it gives an identical end state
    with no snap. It looks like an obvious improvement, hence this test.
    """
    assert "forwards" not in d.DESK_NEON_CSS


def test_the_zero_step_delay_is_not_written_as_negative_zero():
    # ``-0s`` is valid and behaves identically; it just reads as a generator
    # artifact in a stylesheet a human will open.
    assert ".desk-neon-0 { animation-delay: 0s; }" in d.DESK_NEON_CSS
    assert "-0s" not in d.DESK_NEON_CSS


def test_the_glow_map_drops_only_expired_entries():
    glow = {"a": ("new", 100.0), "b": ("flag", 108.0)}
    assert d.prune_glows(glow, now=111.0) == {"b": ("flag", 108.0)}
    assert glow == {"b": ("flag", 108.0)}       # mutates the caller's map


def test_glow_step_survives_a_nonsense_timestamp():
    # Page state is a plain dict; a wedged entry must go dark, never raise on
    # the paint path.
    assert d.glow_step(started="soon", now=100.0) is None


def test_glow_step_goes_dark_on_a_nan_instead_of_raising():
    """A NaN is the one bad value that survives the ``float()`` coercion.

    ``float('nan')`` raises nothing, so the try/except cannot catch it, and
    EVERY comparison against a NaN is False — so the obvious range guard
    (``if elapsed < 0 or elapsed >= span``) is False on both halves and waves it
    through to ``int(nan)``, which raises ValueError. That raise lands on the
    paint path, inside ``prune_glows``, which runs this over every entry in the
    map: one wedged timestamp takes down a whole panel repaint, not one row.
    """
    nan = float("nan")
    assert d.glow_step(started=0.0, now=nan) is None
    assert d.glow_step(started=nan, now=5.0) is None
    assert d.glow_step(started=0.0, now=5.0, span=nan) is None


def test_the_glow_map_survives_a_nan_timestamp():
    # The reason the line above matters: prune runs mid-paint over every entry.
    glow = {"a": ("new", float("nan")), "b": ("flag", 108.0)}
    assert d.prune_glows(glow, now=111.0) == {"b": ("flag", 108.0)}


def test_glow_classes_refuses_a_hue_with_no_rule_behind_it():
    """An unknown kind must paint nothing, not a half-glow.

    ``desk-neon-<typo>`` leaves ``--neon`` unset, and a ``box-shadow`` naming an
    undefined custom property is invalid at computed-value time — BOTH shadow
    declarations drop. The row would flash a background and never glow, which
    looks like a rendering quirk rather than a wiring bug. GLOW_NEW/GLOW_FLAG
    exist precisely so there is a finite set to check against.
    """
    assert d.glow_classes(("nwe", 100.0), now=103.0) == ""
    assert d.glow_classes((None, 100.0), now=103.0) == ""
    for kind in (d.GLOW_NEW, d.GLOW_FLAG):
        assert d.glow_classes((kind, 100.0), now=103.0) != ""


def test_the_step_clamp_cannot_produce_a_negative_class():
    # ``min(steps - 1, max(0, …))`` undoes its own floor when ``steps`` is 0 and
    # emits ``desk-neon--1``. Unreachable today — nothing passes ``steps`` — but
    # the fix is free (apply the floor last) and the comment beside it claims
    # the clamp is airtight, so it should be.
    assert d.glow_step(started=100.0, now=100.0, steps=0) >= 0


# ── arrival detection: what glows, and what gets said ────────────────────────
# These are the feature's whole decision layer, and they are module-level
# functions taking their state explicitly precisely so this block can exist —
# a closure inside ``render()`` is reachable only from a browser.
def _arr_flow(rid, symbol="SPY", kind="Crossover", side="Calls over"):
    return {"id": rid, "symbol": symbol, "kind": kind, "side": side}


def _arr_pos(pid, symbol="SPY", flag="OK", strategy="put_credit_spread"):
    return {"position_id": pid, "symbol": symbol, "flag": flag,
            "strategy": strategy}


def test_arrival_state_starts_silent_and_empty():
    """``first`` being True IS the silent-first-paint mechanism.

    Built by a shared helper rather than a dict literal in ``render`` so this
    test exercises the state the page actually starts from — a hand-rolled copy
    here could not catch the flag being dropped there.
    """
    s = d.arrival_state()
    assert s["first"] is True
    assert s["glow"] == {} and s["speak"] == []
    assert s["seen_flow"] == set() and s["seen_pos"] == set()
    assert s["pos_flags"] == {}


def test_the_first_paint_announces_nothing_and_lights_nothing():
    """Navigating to the Desk must not squawk the whole backlog at you."""
    s = d.arrival_state()
    said = d.fold_flow_arrivals(s, [_arr_flow("a"), _arr_flow("b")], now=100.0)
    assert said is None
    assert s["glow"] == {}
    # ...but the backlog IS recorded, so the next arrival is the only new one.
    assert s["seen_flow"] == {"a", "b"}


def test_the_first_paint_is_dark_for_positions_too():
    s = d.arrival_state()
    assert d.fold_position_arrivals(s, [_arr_pos("p1")], now=100.0) is None
    assert s["glow"] == {}
    assert s["seen_pos"] == {"p1"} and s["pos_flags"] == {"p1": "OK"}


def test_the_second_paint_glows_and_speaks_the_new_alert():
    s = d.arrival_state()
    d.fold_flow_arrivals(s, [_arr_flow("a")], now=100.0)
    s["first"] = False
    said = d.fold_flow_arrivals(s, [_arr_flow("b"), _arr_flow("a")], now=200.0)
    assert said == "S P Y. Crossover alert, calls over."
    assert s["glow"] == {"b": (d.GLOW_NEW, 200.0)}


def test_a_burst_is_one_sentence_naming_the_newest_and_counting_the_rest():
    """The feed is newest-first, so ``[0]`` is the one to say out loud."""
    s = d.arrival_state()
    s["first"] = False
    rows = [_arr_flow("c", "QQQ"), _arr_flow("b", "AMD"), _arr_flow("a", "SPY")]
    said = d.fold_flow_arrivals(s, rows, now=100.0)
    assert said.startswith("Q Q Q.")
    assert said.endswith("Plus 2 more.")
    assert set(s["glow"]) == {"a", "b", "c"}


def test_an_unchanged_feed_says_nothing_on_the_next_paint():
    s = d.arrival_state()
    s["first"] = False
    d.fold_flow_arrivals(s, [_arr_flow("a")], now=100.0)
    assert d.fold_flow_arrivals(s, [_arr_flow("a")], now=101.0) is None


def test_an_alert_with_no_ticker_glows_but_is_never_announced():
    """``flow_phrase({})`` is "Flow alert." — a squawk that refuses to say what.

    Worse than silence: it makes the reader look for something the sentence
    declines to name. The row still lights, because the panel prints whatever
    the alert does carry.
    """
    s = d.arrival_state()
    s["first"] = False
    assert d.fold_flow_arrivals(s, [_arr_flow("a", symbol="")], now=100.0) is None
    assert s["glow"] == {"a": (d.GLOW_NEW, 100.0)}
    # A symbol of pure punctuation spells to nothing, so it counts as absent —
    # ``spell`` is the test, not a truthiness check on the raw field.
    s2 = d.arrival_state()
    s2["first"] = False
    assert d.fold_flow_arrivals(s2, [_arr_flow("z", symbol="$$")], now=1.0) is None


def test_a_new_position_glows_and_speaks():
    s = d.arrival_state()
    s["first"] = False
    said = d.fold_position_arrivals(s, [_arr_pos("p1")], now=100.0)
    assert said == "S P Y. New position, put credit spread."
    assert s["glow"] == {"p1": (d.GLOW_NEW, 100.0)}


# ── the spoken contract, END TO END ──────────────────────────────────────────
# ⚠ The unit tests in test_voice.py hand ``flow_phrase``/``position_phrase`` a
# hand-written row, so they cannot see the field NAMES drift — a builder reading
# ``expiry`` while ``position_rows`` publishes ``expiration`` would leave that
# whole file green and every live phrase silently short. These two start from a
# raw service payload and end at the sentence.
def test_a_new_flow_alert_speaks_its_contract_from_the_raw_payload():
    raw = {"type": "uoa", "side": "call", "symbol": "QQQ", "strike": 737.0,
           "expiry": "2026-08-09", "dte": 0, "volume": 12400, "oi": 1100,
           "vol_oi": 11.27, "premium": 2132800.0, "ts": 1754750100,
           "id": "QQQ|uoa|call|737|2026-08-09"}
    s = d.arrival_state()
    s["first"] = False
    said = d.fold_flow_arrivals(s, d.flow_rows({"alerts": [raw]}), now=1.0)
    assert said == "Q Q Q. Unusual activity, 0-D T E 7 37 Call."


def test_a_new_position_speaks_its_contract_from_the_raw_payload():
    """The expiration is deliberately far out so ``dte`` cannot reach 0 and turn
    the date into "0-D T E" — ``position_rows`` computes it against today."""
    raw = _pos("p1", expiration="2027-09-17", entry_credit=1.35)
    s = d.arrival_state()
    s["first"] = False
    said = d.fold_position_arrivals(
        s, d.position_rows({"positions": [raw]}, None), now=1.0)
    assert said == ("S P Y. New position, put credit spread. "
                    "6 hundred, 5 95, 9 - 17, entry 1 dollar 35 credit.")


def test_a_flag_change_glows_amber_but_says_nothing():
    """A position ALREADY in the book changing state is not an arrival."""
    s = d.arrival_state()
    d.fold_position_arrivals(s, [_arr_pos("p1", flag="OK")], now=100.0)
    s["first"] = False
    said = d.fold_position_arrivals(s, [_arr_pos("p1", flag="AT RISK")], now=200.0)
    assert said is None
    assert s["glow"] == {"p1": (d.GLOW_FLAG, 200.0)}


def test_a_brand_new_position_never_takes_the_flag_glow_as_well():
    # ``flag_changes`` already declines a first sighting; the ``setdefault`` is
    # the second half of that, so an arrival keeps its cyan.
    s = d.arrival_state()
    s["first"] = False
    d.fold_position_arrivals(s, [_arr_pos("p1", flag="AT RISK")], now=100.0)
    assert s["glow"]["p1"][0] == d.GLOW_NEW


def test_the_folds_survive_a_malformed_row():
    """Rows arrive off a cache read, so a non-dict must not take the paint down."""
    s = d.arrival_state()
    s["first"] = False
    assert d.fold_flow_arrivals(s, ["junk", None, _arr_flow("a")], now=1.0)
    s2 = d.arrival_state()
    s2["first"] = False
    assert d.fold_position_arrivals(s2, ["junk", _arr_pos("p1")], now=1.0)


# ── the speak gate ───────────────────────────────────────────────────────────
_WEEKDAY = datetime.datetime(2026, 8, 19, 10, 0, tzinfo=d._CT)   # a Wednesday
_SUNDAY = datetime.datetime(2026, 8, 23, 10, 0, tzinfo=d._CT)


def test_the_enable_switch_silences_the_desk():
    assert d.should_speak({"voice_enabled": False}, _WEEKDAY) is False
    assert d.should_speak({}, _WEEKDAY) is False
    assert d.should_speak({"voice_enabled": True}, _WEEKDAY) is True


def test_the_market_hours_gate_is_honoured_on_the_speak_path():
    """The Settings card promises "Uses the existing market-hours gate above."

    It is the SAME ``alerts.in_market_hours`` the scanner chime goes through —
    one setting, not a second voice-only copy of the idea.
    """
    on = {"voice_enabled": True, "alert_market_hours_only": True}
    assert d.should_speak(on, _WEEKDAY) is True
    assert d.should_speak(on, _SUNDAY) is False
    # Gate off: a Sunday backtest session still speaks.
    off = {"voice_enabled": True, "alert_market_hours_only": False}
    assert d.should_speak(off, _SUNDAY) is True


def test_speak_volume_clamps_a_hand_edited_settings_file():
    # settings.json is hand-editable and never validated on read.
    assert d.speak_volume({"voice_volume": 1.7}) == 1.0
    assert d.speak_volume({"voice_volume": -3}) == 0.0
    assert d.speak_volume({"voice_volume": 0.5}) == 0.5


def test_speak_volume_falls_back_rather_than_raising_inside_a_timer():
    """A bare ``float("loud")`` raises on the poll path — one tab, no audio, and
    a traceback a user never sees. And a NaN must not pin the MAXIMUM: the
    documented ``min(1.0, nan) == 1.0`` trap would answer a missing reading with
    full volume."""
    for junk in ("loud", None, object(), float("nan"), True, {}):
        v = d.speak_volume({"voice_volume": junk})
        assert 0.0 <= v <= 1.0
    assert d.speak_volume({}) == d.DEFAULT_VOICE_VOLUME
    assert d.speak_volume(None) == d.DEFAULT_VOICE_VOLUME


# ── the browser side ─────────────────────────────────────────────────────────
def test_the_desk_speaks_through_its_own_audio_element():
    """Not ``alert-audio``: a scanner chime must not cut an announcement off."""
    assert "desk-voice" in d.DESK_VOICE_JS
    assert "alert-audio" not in d.DESK_VOICE_JS


def test_a_dead_clip_cannot_wedge_the_queue_for_the_life_of_the_tab():
    """``onended`` alone stalls forever on a 404 — nothing ever ends."""
    assert "el.onerror = done" in d.DESK_VOICE_JS
    assert "el.onended = done" in d.DESK_VOICE_JS


def test_a_blocked_autoplay_reports_itself_instead_of_failing_silently():
    """``play()`` just rejects; nothing appears in any log. Without this the
    feature looks broken on every fresh tab."""
    assert f"emitEvent('{d.VOICE_BLOCKED_EVENT}'" in d.DESK_VOICE_JS
    assert ".catch(" in d.DESK_VOICE_JS


def test_the_blocked_event_name_is_the_constant_and_not_a_third_copy():
    """Renaming ``VOICE_BLOCKED_EVENT`` must not leave ``ui.on`` subscribed to a
    name nothing emits — the unlock button silently dead, every test green. The
    placeholder must also be fully substituted, or the JS emits a literal
    ``__VOICE_BLOCKED_EVENT__`` that no handler is listening for."""
    assert "__VOICE_BLOCKED_EVENT__" not in d.DESK_VOICE_JS
    assert d.DESK_VOICE_JS.count(f"'{d.VOICE_BLOCKED_EVENT}'") == 1


def test_only_a_real_autoplay_block_is_reported_as_one():
    """A 404 clip rejects ``play()`` too — with **NotSupportedError**, per the
    HTML "dedicated media source failure steps", which ALSO fire ``error``.
    Treating every rejection as a block truncated the queue, desynced ``busy``,
    and showed the user an unlock button that fixes nothing."""
    assert "err.name === 'NotAllowedError'" in d.DESK_VOICE_JS
    # ...and everything else retires the attempt like any playback failure.
    blocked = d.DESK_VOICE_JS.index("NotAllowedError")
    assert "done();" in d.DESK_VOICE_JS[blocked:]


def test_one_clip_cannot_advance_the_queue_twice():
    """A 404 delivers BOTH the ``error`` event and the ``play()`` rejection for
    the same attempt. Without the token, ``done`` would run twice: two clips
    consumed for one played, and ``busy`` no longer describing reality."""
    js = d.DESK_VOICE_JS
    assert "const attempt = ++v.token;" in js
    assert "if (attempt !== v.token) return;" in js


def test_a_blocked_queue_is_dropped_rather_than_replayed_later():
    # Holding a backlog would announce a stale burst the moment audio unlocks.
    assert "v.q.length = 0" in d.DESK_VOICE_JS


def test_the_known_gaps_in_the_play_queue_stay_written_down():
    """Two accepted limitations, both recorded so they are not re-discovered as
    bugs: a mid-burst volume change only takes effect on the NEXT burst, and a
    media element that stalls without ``ended`` or ``error`` wedges ``busy``."""
    src = inspect.getsource(d)
    head = src[:src.index("DESK_VOICE_JS = ")]
    assert "KNOWN GAP" in head
    assert "volume change is IGNORED" in d.DESK_VOICE_JS


def test_the_glow_needs_no_repaint_timer_of_its_own():
    """The browser animates the LIVE element for free; the class only matters at
    REBUILD time, which is what ``glow_step`` computes. A 1 s timer on the
    landing page would be 86,400 no-op repaints a day."""
    assert inspect.getsource(d.render).count("ui.timer(") == 2   # clock + poll


def test_the_paint_uses_one_clock_for_detection_pruning_and_drawing():
    """Two ``time.monotonic()`` calls in one paint can prune a glow and then be
    asked to draw it."""
    src = inspect.getsource(d.render)
    paint = src[src.index("    def _paint(payloads):"):]
    # ⚠ NO trailing newline in the anchor. ``"\n    @guard\n"`` does not match
    # ``@guard_async``, which is what actually follows ``_paint`` — so the slice
    # silently widened to cover ``_speak_pending`` too, and a ``time.monotonic()``
    # added THERE would fail this test with a message naming the wrong function.
    # As a prefix, ``"\n    @guard"`` matches both decorators.
    paint = paint[:paint.index("\n    @guard")]
    assert "async def _speak_pending" not in paint     # the slice really stops
    assert paint.count("time.monotonic()") == 1


# ── the prewarm ──────────────────────────────────────────────────────────────
def test_prewarm_symbols_reads_the_watchlist_off_the_matrix():
    view = {"rows": [{"symbol": "SPY"}, {"symbol": "AMD"}, {"symbol": "SPY"}]}
    assert d.prewarm_symbols(view) == ["SPY", "AMD"]


def test_prewarm_symbols_drops_anything_that_is_not_a_symbol():
    # The payload is a cache read. A blank would warm the ticker-less sentence
    # that nothing is allowed to speak in the first place.
    view = {"rows": [{"symbol": ""}, {"symbol": None}, "junk", {},
                     {"symbol": "  "}, {"symbol": "SPY"}]}
    assert d.prewarm_symbols(view) == ["SPY"]
    assert d.prewarm_symbols(None) == []
    assert d.prewarm_symbols({"rows": None}) == []


def test_the_prewarm_is_capped_at_the_head_of_the_hotness_ranking():
    """UNCAPPED this is the whole watchlist — ~30 symbols × the 8
    ``voice.FLOW_CAUSES`` = ~240 SERIAL synthesis calls at a measured 0.9-2.4 s
    each: 3.6 to 9.6 MINUTES of continuous network on first Desk open, repeated
    whenever the voice changes (it is part of the clip cache key).

    Truncating is only defensible because ``options_svc`` sorts the matrix rows
    by HOTNESS descending, server-side — so the kept head is the set most likely
    to fire a flow alert. The order is therefore load-bearing, not incidental,
    and the cap keeps the FIRST N.
    """
    view = {"rows": [{"symbol": f"S{i}"} for i in range(30)]}
    got = d.prewarm_symbols(view)
    assert len(got) == d.PREWARM_SYMBOLS_MAX == 8
    assert got == [f"S{i}" for i in range(8)]        # the head, not a sample
    # 8 symbols x the 4 CONTRACT-LESS causes: about 30-80 s, not nine minutes.
    # (It was 64 phrases until the contract form retired the four warmable-in-
    # principle uoa/big_delta pairs — see voice.FLOW_CAUSES.)
    assert len(voice.prewarm_texts(got)) == 32


def test_the_prewarm_cap_counts_usable_symbols_not_rows():
    """A run of junk rows in front must not eat the budget — the cap is on what
    actually gets warmed."""
    rows = [{"symbol": ""}, "junk", {}, {"symbol": None}]
    rows += [{"symbol": f"S{i}"} for i in range(12)]
    assert d.prewarm_symbols({"rows": rows}) == [f"S{i}" for i in range(8)]


def test_the_prewarm_is_skipped_while_spoken_alerts_are_off(monkeypatch):
    """...and the latch stays OPEN, so switching them on later still warms."""
    calls = []
    monkeypatch.setattr(voice, "prewarm", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(d.app_settings, "load", lambda: {"voice_enabled": False})
    d._prewarm_clips({"options:matrix": {"rows": [{"symbol": "SPY"}]}})
    assert calls == []
    assert d._PREWARMED["done"] is False


def test_the_prewarm_runs_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(voice, "prewarm", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(d.app_settings, "load",
                        lambda: {"voice_enabled": True, "voice_name": "v"})
    seed = {"options:matrix": {"rows": [{"symbol": "SPY"}]}}
    d._prewarm_clips(seed)
    d._prewarm_clips(seed)
    assert calls == [(["SPY"], "v")]


def test_a_broken_prewarm_cannot_break_the_page_build(monkeypatch):
    """It runs during ``render()``. A cold cache must cost the prewarm, not the
    Desk."""
    def _boom(*_a, **_k):
        raise OSError("no cache directory")
    monkeypatch.setattr(voice, "prewarm", _boom)
    monkeypatch.setattr(d.app_settings, "load",
                        lambda: {"voice_enabled": True, "voice_name": "v"})
    d._prewarm_clips({"options:matrix": {"rows": [{"symbol": "SPY"}]}})


def test_a_failed_prewarm_leaves_the_latch_open_for_the_next_build(monkeypatch):
    """The latch means "this process has warmed the cache". A run that RAISED
    warmed nothing, so setting it before the call would trade the whole feature
    for one transient — permanently, for the life of the process, on the
    strength of a single unreadable payload. Either behaviour is defensible;
    this pins the one the ``_PREWARMED`` comment claims."""
    calls = []

    def _boom(*_a, **_k):
        raise OSError("no cache directory")

    monkeypatch.setattr(voice, "prewarm", _boom)
    monkeypatch.setattr(d.app_settings, "load",
                        lambda: {"voice_enabled": True, "voice_name": "v"})
    seed = {"options:matrix": {"rows": [{"symbol": "SPY"}]}}
    d._prewarm_clips(seed)
    assert d._PREWARMED["done"] is False
    # ...and the next build gets its chance.
    monkeypatch.setattr(voice, "prewarm", lambda *a, **k: calls.append(a))
    d._prewarm_clips(seed)
    assert calls == [(["SPY"], "v")]
    assert d._PREWARMED["done"] is True


# ── speak_phrases: the gate WIRING, not just the gate ────────────────────────
# ⚠ WHY THESE EXIST. ``should_speak`` and ``speak_volume`` were thoroughly unit
# tested while every test of the code that CALLS them was a source scrape
# (``inspect.getsource``). Deleting ``if not should_speak(...): return`` from the
# speak path left the whole suite green — so the market-hours gate the Settings
# card promises was, in practice, unguarded. A source scrape cannot see a line
# that is not there. These call the real function and assert on what came out.
def _spoke(phrases, settings, urls=None, now=_WEEKDAY):
    """Run the speak path with a fake synthesizer; return (js, texts_synthesized)."""
    import asyncio
    seen = []
    supply = list(urls if urls is not None else
                  [f"/voice/{i}.mp3" for i in range(len(phrases))])

    async def _synth(text):
        seen.append(text)
        return supply.pop(0) if supply else None

    js = asyncio.run(d.speak_phrases(phrases, settings, _synth, now=now))
    return js, seen


_SPEAK_ON = {"voice_enabled": True, "voice_volume": 0.5}


def test_the_speak_path_actually_speaks_when_the_gates_allow_it():
    js, seen = _spoke(["S P Y. Crossover alert."], _SPEAK_ON)
    assert seen == ["S P Y. Crossover alert."]
    assert js == 'window.__deskSpeak(["/voice/0.mp3"], 0.5)'


def test_the_market_hours_gate_really_silences_the_speak_path():
    """Not just ``should_speak`` in isolation — the CALL. This is the test whose
    absence let the gate be deleted with the suite green."""
    on = dict(_SPEAK_ON, alert_market_hours_only=True)
    js, seen = _spoke(["S P Y. Crossover alert."], on, now=_SUNDAY)
    assert js is None
    assert seen == []           # and nothing was synthesized, either


def test_the_enable_switch_really_silences_the_speak_path():
    js, seen = _spoke(["S P Y. Crossover alert."], {"voice_enabled": False})
    assert js is None and seen == []


def test_the_volume_that_reaches_the_browser_is_the_CLAMPED_one():
    """``speak_volume`` clamping in isolation proves nothing if the raw settings
    value is what gets interpolated into the JS."""
    js, _ = _spoke(["a"], {"voice_enabled": True, "voice_volume": 9.9})
    assert js.endswith(", 1.0)")
    js, _ = _spoke(["a"], {"voice_enabled": True, "voice_volume": "loud"})
    assert js.endswith(f", {d.DEFAULT_VOICE_VOLUME})")


def test_a_phrase_that_would_not_synthesize_is_skipped_not_spoken_as_a_gap():
    """``ensure`` returns None on failure. The row has already glowed, so a dead
    endpoint costs the sentence and nothing else — but it must not put a null
    into the URL list the browser is handed."""
    js, seen = _spoke(["first", "second"], _SPEAK_ON,
                      urls=[None, "/voice/b.mp3"])
    assert seen == ["first", "second"]
    assert js == 'window.__deskSpeak(["/voice/b.mp3"], 0.5)'
    # ...and if NOTHING synthesized there is no call at all.
    js, _ = _spoke(["first"], _SPEAK_ON, urls=[None])
    assert js is None


def test_an_empty_queue_never_reaches_the_browser_or_the_synthesizer():
    """The common case, 43,200 times a day: a poll that painted nothing to say.
    It must not load settings' worth of work, and must never emit JS."""
    js, seen = _spoke([], _SPEAK_ON)
    assert js is None and seen == []


def test_the_urls_are_json_encoded_not_string_joined():
    js, _ = _spoke(["a", "b"], _SPEAK_ON,
                   urls=['/voice/x".mp3', "/voice/y.mp3"])
    import json
    assert json.loads(js[js.index("(") + 1:js.rindex(",")]) == \
        ['/voice/x".mp3', "/voice/y.mp3"]


def test_the_live_synthesis_budget_is_short_enough_to_keep_the_poll_alive():
    """``_poll`` AWAITS the speak step, and NiceGUI's timer awaits its callback
    before sleeping — so a hung endpoint at ``voice``'s 20 s background budget
    froze the landing page for 40 s (a paint can queue two phrases). The live
    path must pass its own, much shorter, budget."""
    assert voice.LIVE_SYNTH_TIMEOUT_SEC <= 3.0
    assert voice.LIVE_SYNTH_TIMEOUT_SEC > 2.4    # the slowest measured synthesis
    src = inspect.getsource(d.render)
    assert "timeout=_voice.LIVE_SYNTH_TIMEOUT_SEC" in src
    # 2 phrases x the budget, and no worse.
    assert 2 * voice.LIVE_SYNTH_TIMEOUT_SEC <= 6.0


# ── render() wiring for the voice ────────────────────────────────────────────
def _rendered_elements():
    """Every element ``render()`` just added, in build order."""
    from nicegui import ui
    from pages import desk

    before = set(ui.context.client.elements)
    desk.render()
    return [e for key, e in ui.context.client.elements.items()
            if key not in before]


def test_render_mounts_the_desks_own_audio_element():
    """Its own, not ``main.py``'s shared ``alert-audio``: a scanner chime fires
    from the app-wide watcher on every page, this one included, and sharing one
    element would let it cut an announcement off mid-sentence."""
    html = [getattr(e, "content", "") or "" for e in _rendered_elements()]
    assert any('id="desk-voice"' in h for h in html)


def test_render_hides_the_unlock_prompt_until_the_browser_complains():
    """A tab that was never going to need it must never show it."""
    btn = [e for e in _rendered_elements()
           if getattr(e, "text", None) == "ENABLE SPOKEN ALERTS"]
    assert len(btn) == 1
    assert "hidden" in btn[0]._classes          # NiceGUI's display:none class


def test_render_subscribes_to_the_blocked_autoplay_event():
    src = inspect.getsource(d.render)
    assert "ui.on(VOICE_BLOCKED_EVENT" in src
    assert "unlock_btn.on_click" in src


def test_the_poll_speaks_only_after_the_paint():
    """The row must already be lit when the sentence starts, and synthesis can
    take a second."""
    src = inspect.getsource(d.render)
    poll = src[src.index("    async def _poll():"):]
    assert poll.index("_paint(payloads)") < poll.index("await _speak_pending()")


def test_synthesis_never_runs_on_the_event_loop():
    """``voice.ensure`` blocks a measured 0.9-2.4 s on a cache miss, and the loop
    is shared by every page in the app."""
    src = inspect.getsource(d.render)
    for line in src.splitlines():
        if "_voice.ensure" in line:
            assert "run.io_bound" in line
    assert src.count("_voice.ensure") == 2      # the poll, and the unlock button
