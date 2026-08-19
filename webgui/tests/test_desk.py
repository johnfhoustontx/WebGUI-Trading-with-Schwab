"""Pure display logic for the Desk (/desk).

Every builder here takes plain dicts and returns plain dicts, so the whole
screen's arithmetic is testable without a browser — the same shape
``pages/market.py`` proved out.
"""
import pathlib

from pages import desk as d


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
    view = {"rows": [_mrow("QQQ"), _mrow("AAPL"), _mrow("$SPX")]}
    assert [r["symbol"] for r in d.dealer_rows(view, stale=False)] == ["$SPX", "QQQ"]


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
def test_opportunity_rows_takes_the_top_five_by_hotness_descending():
    view = {"rows": [_mrow(f"S{i}", hotness=i) for i in range(10)]}
    rows = d.opportunity_rows(view)
    assert [r["hotness"] for r in rows] == [9, 8, 7, 6, 5]
    assert [r["symbol"] for r in rows] == ["S9", "S8", "S7", "S6", "S5"]


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


def test_flow_rows_takes_the_newest_five_newest_first():
    view = {"alerts": [_alert(i) for i in range(9)]}   # service appends oldest-first
    rows = d.flow_rows(view)
    assert [r["id"] for r in rows] == ["a8", "a7", "a6", "a5", "a4"]


def test_flow_rows_delegates_to_the_flow_pages_own_builder():
    """The Desk composes; it never re-formats. If these two ever diverge, the
    Desk's feed is contradicting the page it links to."""
    from pages.options import flow
    view = {"alerts": [_alert(i) for i in range(3)]}
    assert d.flow_rows(view) == flow.alert_rows(view)[:5]


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


def test_position_rows_merges_both_accounts_with_a_source_chip():
    rows = d.position_rows({"positions": [_pos("p1")]},
                           {"positions": [_pos("c1")]})
    assert [r["source"] for r in rows] == ["PAPER", "CLAUDE"]
    assert [r["position_id"] for r in rows] == ["p1", "c1"]


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
    prices for one symbol on one screen. $VIX rides ``options:header`` because
    it is excluded from the matrix universe and so can never be a dealer row."""
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


def test_the_two_rings_use_distinct_uids():
    """``ring_svg`` namespaces the SVG root DOM id with ``uid``, and these two
    rings share a page — identical uids would make them collide."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    assert src.count('uid="desk-sent"') == 2      # the seed + the repaint
    assert src.count('uid="desk-trend"') == 2


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


def _rendered_html():
    """The raw content of every ``ui.html`` fragment ``render()`` just added."""
    from nicegui import ui
    from pages import desk

    before = set(ui.context.client.elements)
    desk.render()
    return [getattr(e, "content", "")
            for key, e in ui.context.client.elements.items()
            if key not in before and hasattr(e, "content")]


def _seed_bus(monkeypatch, data):
    import bus_client
    monkeypatch.setattr(bus_client, "read_full",
                        lambda v: (data.get(v), 1 if v in data else None))
    monkeypatch.setattr(bus_client, "read", lambda v: data.get(v))


def _full_payloads():
    return {
        "options:header": {"vix": 14.2, "vix_regime": {"label": "Normal"}},
        "sentiment:regime": {"label": "Rallying", "committed_label": "trending",
                             "confidence": 0.71, "direction": 1},
        "sentiment:composite": {"live": None, "derived": {}},
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
    assert "Unusual activity" in texts           # the flow kind
    assert "AT RISK" in texts                    # the position flag
    assert any(t.startswith("OPEN 1 ·") for t in texts)
    assert "Rallying" in texts                   # the regime word in the strip
    assert "14.20" in texts and "Normal" in texts    # VIX and its band


def test_render_never_puts_a_buy_or_sell_word_on_the_flow_feed(monkeypatch):
    """Schwab publishes no time-and-sales tape to this app, so nobody here knows
    who initiated. The alert rows say Call/Put and stop there; the Desk must not
    add a side by paraphrase, in a column header or anywhere else."""
    _seed_bus(monkeypatch, {"options:flow_alerts": {"alerts": [_alert(1)]}})
    blob = " ".join(t for t in _rendered_texts() if t).lower()
    assert "bought" not in blob and "sold" not in blob


def test_render_mounts_two_rings_with_distinct_dom_ids(monkeypatch):
    """``ring_svg`` namespaces the SVG root id with ``uid``, and these two rings
    share a page — identical uids would make them collide."""
    _seed_bus(monkeypatch, _full_payloads())
    html = " ".join(_rendered_html())
    assert 'id="ring-desk-sent"' in html
    assert 'id="ring-desk-trend"' in html


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
