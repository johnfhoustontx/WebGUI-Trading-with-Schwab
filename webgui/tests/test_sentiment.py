"""Pure-transform tests for the Sentiment page."""
import math

import bus_client
from pages import sentiment as S


def _snap(date, total, **comp):
    base = {"vix_complex": 5, "put_call": 5, "breadth": 5,
            "rotation": 5, "sector_perf": 5, "credit_pulse": 5}
    base.update(comp)
    return {
        "date": date,
        "composite": {"total_score": f"{total:.2f}", "bias": "Neutral",
                      "size_modifier": "1.00x", "aggregate_confidence": 0.8},
        "component_scores": base,
        "component_confidence": {k: 0.9 for k in base},
    }


def _snaps(*scores):
    """Consecutive daily snapshots carrying the given composite totals."""
    return [_snap(f"2026-08-{i + 1:02d}", s) for i, s in enumerate(scores)]


def test_gauge_score_scales_0_10_to_0_100():
    assert S.gauge_score("7.5") == 75.0
    assert S.gauge_score(0) == 0.0
    assert S.gauge_score("bad") == 0.0


def test_gauge_figure_is_no_longer_imported():
    """The four speedometers became two rings; nothing on this page draws a
    gauge any more, so the re-export is gone (see test_rings.py / the ring
    wiring tests below)."""
    assert not hasattr(S, "gauge_figure")


# ───────────────────────── Signals column (1x4 glowing tiles) ─────────────────
def test_signal_tile_defs_carry_the_full_reference_anatomy():
    defs = S.SIGNAL_TILE_DEFS
    assert [d["key"] for d in defs] == ["bias", "signal", "yesterday", "change"]
    assert [d["label"] for d in defs] == ["BIAS", "SIGNAL", "YESTERDAY", "CHANGE"]
    assert [d["descriptor"] for d in defs] == [
        "MARKET DIRECTION", "STRENGTH & MOMENTUM", "PREVIOUS CLOSE", "VS YESTERDAY"]
    # every tile carries its header + footer icon
    assert all(d["icon"] and d["foot_icon"] for d in defs)


def _tile_values(bias="Neutral", signal="Neutral", change="+0.10"):
    return {"modifier": "1.00x", "bias": bias, "signal": signal,
            "yesterday": "6.00", "change": change}


def test_word_tone_covers_the_signal_band_vocabularies():
    """BIAS and SIGNAL carry live_composite.signal_band's OWN words — the
    positioning set and the strength set — not the composite's bias field. A
    substring bull/bear test alone would paint 'Long' and 'Cautious' amber."""
    for word in ("Long", "Bullish", "Strong Bull"):
        assert S._word_tone(word) == "pos", word
    for word in ("Short", "Bearish", "Strong Bear"):
        assert S._word_tone(word) == "neg", word
    for word in ("Neutral", "Cautious"):
        assert S._word_tone(word) == "warn", word
    # cold cache / unknown wording
    assert S._word_tone("—") == "flat" and S._word_tone("") == "flat"
    assert S._word_tone(None) == "flat"
    assert S._word_tone("Wildly Bullish") == "pos"   # substring fallback
    assert S._word_tone("Sideways") == "warn"        # unknown -> neutral tone


def test_signal_tile_rows_negative_and_neutral_tones():
    rows = {r["key"]: r for r in
            S.signal_tile_rows(_tile_values(bias="Short", signal="Strong Bear",
                                            change="-0.30"), prev_total=5.5)}
    assert rows["bias"]["tone"] == "neg"
    assert rows["signal"]["tone"] == "neg"
    assert rows["yesterday"]["tone"] == "warn"  # mid band
    assert rows["change"]["tone"] == "neg"
    # an exactly-flat change is 'flat', not a band colour
    flat = {r["key"]: r for r in
            S.signal_tile_rows(_tile_values(change="+0.00"), 5.5)}
    assert flat["change"]["tone"] == "flat"


def test_signal_tile_rows_cold_cache_is_flat_not_invented():
    # No prior session -> YESTERDAY is flat rather than banded off a missing
    # number; em-dash bias/signal/change are unknown -> flat.
    rows = {r["key"]: r for r in
            S.signal_tile_rows(_tile_values(bias="—", signal="—", change="—"),
                               prev_total=None)}
    assert rows["bias"]["tone"] == "flat"
    assert rows["signal"]["tone"] == "flat"
    assert rows["yesterday"]["tone"] == "flat"
    assert rows["change"]["tone"] == "flat"


def test_render_paints_velocity_and_divergence_from_cache():
    """Smoke: a populated composite carrying velocity + divergence renders."""
    from nicegui import ui

    bus_client.reset()
    bus_client.bus().cache_set("cache:sentiment:composite", {
        "live": _snap("2026-08-14", 7.1),
        "composite_at": "2026-08-14T09:30:00",
        "proxy_up": True,
        "derived": {"weights": {"vix_complex": 0.3}, "size": "1.10x",
                    "bias": "Long", "signal": "Bullish",
                    "velocity": {"text": "3d ROC: +0.42",
                                 "flag": "REGIME BREAK: +2.30σ from 20d mean"},
                    "divergence": "breadth lagging"},
    })
    bus_client.bus().cache_set("cache:sentiment:history",
                               {"snaps": _snaps(5.0, 6.0), "spy": []})
    with ui.card():
        S.render()  # must not raise


def test_sc_text_class():
    assert S.sc_text_class(7) == S.TXT_G
    assert S.sc_text_class(3) == S.TXT_R
    assert S.sc_text_class(5) == S.TXT_Y


def test_trend_short_covers_both_vocabularies():
    for k in ("bullish", "lack_of_bullishness", "neutral",
              "lack_of_bearishness", "bearish"):
        assert k in S._TREND_SHORT
    for k in ("bull_trend", "pullback_in_bull", "range",
              "bear_rally", "bear_trend"):
        assert k in S._TREND_SHORT


def test_market_state_evidence_rows():
    ev = ["direction 75/100", "aggression -0.37"]
    assert S.market_state_evidence_rows({"evidence": ev}) == ev
    assert S.market_state_evidence_rows({}) == []
    assert S.market_state_evidence_rows(None) == []
    assert S.market_state_evidence_rows({"evidence": None}) == []


# ── Market Trend speedometer (needle = the directional 0-100 trend score) ─────
def test_trend_gauge_value_uses_score_directly():
    assert S.trend_gauge_value({"score": 84.0}) == 84.0
    assert S.trend_gauge_value({"smoothed_score": 62.5, "score": 70}) == 62.5  # prefers smoothed
    assert S.trend_gauge_value({"score": 0.0}) == 0.0                          # 0 is valid
    assert S.trend_gauge_value(None) == 50.0
    assert S.trend_gauge_value({}) == 50.0
    assert S.trend_gauge_value({"score": 150}) == 100.0                        # clamped


def test_trend_subscore_rows():
    rows = S.trend_subscore_rows({
        "sub_scores": {"price": 88, "breadth": 80, "sector": 82, "vix": 70},
        "sub_confidence": {"price": 1.0, "breadth": 0.9, "sector": 1.0, "vix": 1.0}})
    assert len(rows) == 4
    assert {"name": "Price / MTF", "score": "88.0", "weight": "45%", "conf": "1.00"} in rows


def test_trend_subscore_rows_skips_missing_and_handles_empty():
    assert S.trend_subscore_rows(None) == []
    assert S.trend_subscore_rows({}) == []
    rows = S.trend_subscore_rows({"sub_scores": {"price": 60, "sector": 55}})  # 30d shape
    assert [r["name"] for r in rows] == ["Price / MTF", "Sector"]
    assert rows[0]["conf"] == "0.00"   # missing sub_confidence -> 0.00


def test_composite_series_filters_zeros_and_blanks():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 0.0),
             _snap("2026-06-03", 7.0)]
    dates, scores = S.composite_series(snaps)
    assert scores == [6.0, 7.0]
    assert dates == ["2026-06-01", "2026-06-03"]


def test_page_imports_no_app_scoring():
    """Regression for the cross-app ``scoring`` collision: the page module must
    NOT carry any app ``scoring``/``live_composite``/trend_regime references —
    those now live only in the service. Importing the page (even with options'
    ``scoring`` already bound process-wide) must not need ``WEIGHTS``."""
    assert not hasattr(S, "scoring_composite")
    assert not hasattr(S, "scoring_sector")
    assert not hasattr(S, "signal_band")
    assert not hasattr(S, "trend_regime")
    assert not hasattr(S, "WEIGHTS")
    # Removed scoring-glue helpers are gone (computed in the service now).
    assert not hasattr(S, "velocity_line")
    assert not hasattr(S, "divergence_named")
    assert not hasattr(S, "commit_trend_regime")


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (service not running / cold start) — the Tier-3 graceful-empty path.

    The webgui suite has no NiceGUI User fixture; rendering inside a slot
    context (a card) is enough to exercise the widget wiring + initial paint.
    """
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("sentiment:composite") is None  # confirm empty
    with ui.card():
        S.render()  # must not raise


# ── render() ring wiring ─────────────────────────────────────────────────────
# The builders above are pure and covered on their own; these exercise the parts
# only render() can get wrong — which element each ring's payload lands on, that
# the two rings do not share a DOM id, and that the state words the deleted trend
# gauges used to caption survive on the Trend Detail popup.
def _render_card():
    """Render the page inside a slot context and hand back the container."""
    from nicegui import ui
    with ui.card() as card:
        S.render()
    return card


def _html_svgs(card):
    """Every ``ui.html`` on the page whose content is an SVG fragment.

    The two Day/Week/Month RINGS this used to select are gone — the console
    renders those horizons as meters. What is left is the confidence dial plus
    one sparkline per regime row."""
    from nicegui import ui
    return [e for e in card.descendants()
            if isinstance(e, ui.html) and "<svg" in (e.content or "")]


def _dial(card):
    """The regime confidence dial, by DOM id rather than by position."""
    found = [e for e in _html_svgs(card)
             if 'id="regime-dial-' in (e.content or "")]
    assert len(found) == 1, f"expected exactly one dial, found {len(found)}"
    return found[0]


def _label_texts(card):
    from nicegui import ui
    return [e.text for e in card.descendants() if isinstance(e, ui.label)]


def _trend_detail_texts(card):
    """Label texts inside the Trend Detail popup ONLY.

    Scoped deliberately. Asserting over the whole page cannot see this popup's
    em-dash placeholder: the four Signals tiles render "—" too, so a page-wide
    ``"—" in texts`` passes whether or not the popup was filled — verified by
    mutation, it let a gutted else-branch through. Located via the button's
    label rather than by position, so adding a popup elsewhere cannot capture
    the wrong subtree."""
    from nicegui import ui
    btns = [e for e in card.descendants()
            if isinstance(e, ui.button) and e.text == "Trend Detail"]
    assert len(btns) == 1, f"expected one Trend Detail button, found {len(btns)}"
    return [e.text for e in btns[0].descendants() if isinstance(e, ui.label)]


def _hero_values(card):
    """The console's two hero numbers, located by the hero TYPE SIZE rather than
    by position, so a layout nudge cannot redden these."""
    from nicegui import ui
    from pages import console_cards
    size = console_cards.HERO_VALUE.split("text-[")[1].split("]")[0]
    return [e.text for e in card.descendants()
            if isinstance(e, ui.label) and f"text-[{size}]" in e._classes]


def _dial_center(svg):
    """The dial's confidence reading, by its size constant."""
    from pages import console_dial
    chunk = [c for c in svg.split("<text ")
             if f'font-size="{console_dial.VALUE_SIZE}"' in c][0]
    return chunk.split(">", 1)[1].split("<", 1)[0]


def _seed_cache():
    """A composite + history payload rich enough to drive both rings and the
    Trend Detail popup. Distinct values per horizon so a ring wired to the wrong
    payload (or both rings wired to the same one) fails rather than coincides."""
    bus_client.bus().cache_set("cache:sentiment:composite", {
        "live": _snap("2026-08-14", 7.2),
        "derived": {
            "trend": {"smoothed_score": 64.0, "state": "bullish",
                      "label": "Rallying", "confidence": 0.8,
                      "description": "Broad participation, buyers in control"},
            "trend_7d": {"score": 58.0, "state": "neutral"},
            "trend_30d_ago": {"score": 41.0, "state": "bull_trend"},
        },
    })
    bus_client.bus().cache_set("cache:sentiment:history",
                               {"snaps": _snaps(*([3.0] * 5))})


def test_the_old_rings_are_gone_and_the_dial_has_a_unique_id():
    """The console replaced the two Day/Week/Month rings with meters, so a page
    still mounting them would be rendering every horizon twice. A shared DOM id
    remains the documented SVG collision failure, so the dial's is pinned too."""
    for seed in (False, True):
        bus_client.reset()
        if seed:
            _seed_cache()
        card = _render_card()
        assert not [e for e in _html_svgs(card) if 'id="ring-' in e.content], seed
        assert 'id="regime-dial-regime"' in _dial(card).content, seed


def test_console_svgs_mount_with_sanitizing_on():
    """The dial and sparklines keep ui.html's default client-side sanitizing.
    What makes that safe is a property of the SVG, not of this page — see
    ``test_console.test_dial_emits_nothing_the_sanitizer_would_strip``."""
    bus_client.reset()
    _seed_cache()
    svgs = _html_svgs(_render_card())
    assert svgs, "expected the dial and sparklines"
    for s in svgs:
        assert s._props["sanitize"] is True


def test_render_with_an_empty_cache_shows_the_waiting_state():
    """The cold-start read: no fabricated numbers anywhere — the dial dashes and
    the console says it is waiting rather than rendering an empty frame."""
    bus_client.reset()
    card = _render_card()
    assert _dial_center(_dial(card).content) == "—"
    assert any("Waiting for regime" in (t or "") for t in _label_texts(card))


def test_the_console_trend_card_carries_the_verdict_and_guidance():
    """The meters carry numbers only — the trend's LABEL and DESCRIPTION reach
    the reader solely through the verdict block, which sits in the same
    ``if trend:`` data path the gauge writes were cut out of."""
    bus_client.reset()
    _seed_cache()
    texts = _label_texts(_render_card())
    assert "RALLYING" in texts                                # verdict headline
    assert "Broad participation, buyers in control" in texts  # guidance


def test_trend_panel_clears_and_dashes_when_no_trend_is_published():
    """The ``else:`` branch. Replacing its whole body with ``pass`` was also
    green before this test: the labels start empty, so "still empty" only means
    something once something could have written them."""
    bus_client.reset()
    bus_client.bus().cache_set(          # composite present, derived.trend absent
        "cache:sentiment:composite", {"live": _snap("2026-08-14", 7.2)})
    card = _render_card()
    # Scoped to the popup: the Signals tiles also render "—", so a page-wide
    # check here is vacuous (it let a gutted else-branch survive mutation).
    assert _trend_detail_texts(card) == ["—"]
    assert "Rallying" not in _label_texts(card)      # badge cleared, page-wide


def _repaint(card):
    """Drive the page's version-poll callback — i.e. the REPAINT path, as opposed
    to a fresh build. Located by interval so it cannot grab a reflow timer."""
    from nicegui import ui
    timers = [e for e in card.descendants()
              if isinstance(e, ui.timer) and e.interval == 2.0]
    assert len(timers) == 1, f"expected one 2s poll timer, found {len(timers)}"
    timers[0].callback()


def test_a_repaint_that_loses_the_trend_drops_the_stale_verdict():
    """REPAINT-only semantics. A fresh page never wrote these, so "still absent"
    proves nothing there — this matters solely when a repaint follows a paint
    that HAD a trend, which is reachable: ``derived.trend`` comes from a
    module-level holder in sentiment_svc and goes absent on a restart or a
    defensive compute failure. The console gets this for free by rebuilding
    wholesale, where the old badge needed explicit ``.text = ""`` clears; this
    test is what would catch a future move back to in-place updates."""
    bus_client.reset()
    _seed_cache()
    card = _render_card()
    assert "RALLYING" in _label_texts(card)        # precondition: something to go stale
    assert "Broad participation, buyers in control" in _label_texts(card)

    # Republish WITHOUT derived.trend — the version bump is what drives the poll.
    bus_client.bus().cache_set("cache:sentiment:composite",
                               {"live": _snap("2026-08-14", 7.2), "derived": {}})
    _repaint(card)

    assert "RALLYING" not in _label_texts(card)
    assert "Broad participation, buyers in control" not in _label_texts(card)
    assert _trend_detail_texts(card) == ["—"]


def test_each_console_hero_reads_from_its_own_payload():
    """Distinct values per card, so a hero wired to the wrong payload (or both
    wired to the same one) fails rather than coincides."""
    bus_client.reset()
    _seed_cache()
    heroes = _hero_values(_render_card())
    assert "72" in heroes    # live composite 7.20 -> 0-100
    assert "64" in heroes    # derived.trend.smoothed_score


def test_trend_detail_names_the_state_of_all_three_horizons():
    """The deleted trend gauges captioned their faces with the state word. The
    Day word survives on the regime badge, but Week/Month have no badge — so
    losing this line loses those two readings outright."""
    bus_client.reset()
    _seed_cache()
    assert ["Day Bull · Week Neutral · Month BULL"] == [
        t for t in _trend_detail_texts(_render_card()) if t.startswith("Day ")]


def test_trend_detail_dashes_a_horizon_the_service_has_not_published():
    """``trend_7d`` is not published until sentiment_svc restarts — that horizon
    must read as absent, not borrow another horizon's word."""
    bus_client.reset()
    bus_client.bus().cache_set("cache:sentiment:composite", {
        "live": _snap("2026-08-14", 7.2),
        "derived": {"trend": {"score": 64.0, "state": "bullish"}}})
    assert ["Day Bull · Week — · Month —"] == [
        t for t in _trend_detail_texts(_render_card()) if t.startswith("Day ")]


def test_sentiment_avg_or_none_is_none_with_no_snaps():
    assert S.sentiment_avg_or_none([], 5) is None
    assert S.sentiment_avg_or_none(None) is None


def test_sentiment_avg_drops_non_finite_scores():
    """A non-finite total_score must be DROPPED, not averaged. NaN already
    fails composite_series' ``v > 0`` filter; inf survives it, and inf would
    reach the ring as a clamped 100.0 — a confident full arc built on garbage."""
    good = _snap("2026-08-01", 6.0)
    for bad in ("nan", "inf", "-inf"):
        snaps = [good, {"date": "2026-08-02", "composite": {"total_score": bad}}]
        v = S.sentiment_avg_or_none(snaps)
        assert v == 6.0 and math.isfinite(v), bad
    # A history of NOTHING BUT garbage is no reading at all, not a 100.0 arc.
    assert S.sentiment_avg_or_none(
        [{"date": "2026-08-01", "composite": {"total_score": "inf"}}]) is None


def test_sentiment_30d_avg_is_gone():
    """Deleted with the 30-Day-Avg speedometer it fed: it was a pure alias for
    ``sentiment_avg(snaps)`` with no remaining caller."""
    assert not hasattr(S, "sentiment_30d_avg")


# ── Day/Week/Month arc builders (the two concentric rings) ───────────────────
def test_sentiment_arcs_scales_composite_to_0_100():
    live = {"composite": {"total_score": 7.2}}
    arcs = S.sentiment_arcs(live, _snaps(5.0, 5.0))
    assert [a["caption"] for a in arcs] == ["DAY", "WEEK", "MONTH"]
    assert arcs[0]["value"] == 72.0
    assert arcs[1]["value"] == 50.0


def test_sentiment_arcs_falls_back_to_the_last_snap_when_live_is_absent():
    arcs = S.sentiment_arcs(None, _snaps(4.0, 6.0))
    assert arcs[0]["value"] == 60.0


def test_sentiment_arcs_week_and_month_are_none_with_no_history():
    arcs = S.sentiment_arcs({"composite": {"total_score": 7.0}}, [])
    assert arcs[1]["value"] is None and arcs[2]["value"] is None


def test_sentiment_arcs_day_is_none_with_neither_live_nor_history():
    """Cold start: no payload at all is no reading, not a real 0 (which the
    ring would draw as a genuine maximally-bearish arc)."""
    arcs = S.sentiment_arcs(None, [])
    assert [a["value"] for a in arcs] == [None, None, None]


def test_sentiment_arcs_week_uses_the_five_session_window():
    """Mutation guard: WEEK must not silently become MONTH."""
    snaps = _snaps(*([1.0] * 15 + [9.0] * 5))
    arcs = S.sentiment_arcs(None, snaps)
    assert arcs[1]["value"] == 90.0    # last 5
    assert arcs[2]["value"] == 30.0    # all 20


def test_sentiment_arcs_clamps_a_real_reading_but_drops_a_non_finite_one():
    """A genuine out-of-band composite clamps to the ring's maximum; a
    non-finite one is NOT a reading and must not paint a full arc."""
    arcs = S.sentiment_arcs({"composite": {"total_score": 99.0}},
                            [{"composite": {"total_score": "inf"}}])
    assert arcs[0]["value"] == 100.0        # clamped real value
    assert arcs[1]["value"] is None         # inf is no reading
    assert arcs[2]["value"] is None


def test_sentiment_arcs_day_drops_a_non_finite_score_too():
    """The Day arc needs the same guard as Week/Month, and for the same reason:
    ``gauge_score`` ends in ``min(100.0, x)`` and ``min(100.0, nan)`` is 100.0,
    so a poisoned score painted a confident FULL outer arc. The test above only
    ever fed Day a real 99.0, so this was unguarded as well as unfixed."""
    for bad in ("inf", "nan", float("inf"), float("nan")):
        arcs = S.sentiment_arcs({"composite": {"total_score": bad}}, [])
        assert arcs[0]["value"] is None, bad
    # -inf clamped to 0.0 rather than 100.0 — still a fabricated reading.
    assert S.sentiment_arcs({"composite": {"total_score": "-inf"}}, [])[0]["value"] is None
    # A real reading still gets through, so this is not a blanket None.
    assert S.sentiment_arcs({"composite": {"total_score": 7.2}}, [])[0]["value"] == 72.0


def test_sentiment_arcs_day_drops_an_unparseable_score_too():
    """The MIRROR of the non-finite case, and the same contradiction one input
    to the left. ``_safe_float``'s 0.0 default painted junk as a genuine
    maximally-BEARISH full arc, so 'n/a' gave DAY 0.0 beside WEEK/MONTH None off
    a single snapshot. Day now passes ``None`` as its default, matching
    ``_trend_arc_value``, which already did."""
    for bad in ("n/a", "", [], {}):
        arcs = S.sentiment_arcs({"composite": {"total_score": bad}}, [])
        assert arcs[0]["value"] is None, bad
    assert [a["value"] for a in
            S.sentiment_arcs(None, [{"composite": {"total_score": "n/a"}}])] \
        == [None, None, None]
    # A real 0.0 is still a READING, not missing data — the distinction the
    # whole None contract exists to preserve.
    assert S.sentiment_arcs({"composite": {"total_score": 0.0}}, [])[0]["value"] == 0.0


def test_sentiment_arcs_cannot_contradict_itself_across_horizons():
    """The sharpest form of the bug, needing no ``live`` payload: ONE poisoned
    snapshot is both the Day source and the whole history, so Day read 100.0
    beside Week/Month None — a single call disagreeing with itself."""
    arcs = S.sentiment_arcs(None, [{"composite": {"total_score": "inf"}}])
    assert [a["value"] for a in arcs] == [None, None, None]


def test_trend_arcs_reads_all_three_horizons():
    derived = {"trend": {"smoothed_score": 71.0},
               "trend_7d": {"score": 61.0},
               "trend_30d_ago": {"score": 52.0}}
    arcs = S.trend_arcs(derived)
    assert [a["caption"] for a in arcs] == ["DAY", "WEEK", "MONTH"]
    assert [a["value"] for a in arcs] == [71.0, 61.0, 52.0]


def test_trend_arcs_week_is_none_before_the_service_publishes_it():
    """trend_7d is absent until sentiment_svc is restarted -> track-only."""
    arcs = S.trend_arcs({"trend": {"smoothed_score": 71.0}})
    assert arcs[1]["value"] is None


def test_trend_arcs_handles_an_empty_derived_block():
    arcs = S.trend_arcs({})
    assert all(a["value"] is None for a in arcs)
    assert len(arcs) == 3
    assert S.trend_arcs(None) == arcs


def test_trend_arcs_scoreless_horizon_is_none_not_a_fabricated_50():
    """A published-but-scoreless horizon is exactly where trend_gauge_value
    falls back to its neutral 50.0 — the ring must say "no data" instead."""
    assert S.trend_gauge_value({"state": "neutral"}) == 50.0   # the fallback
    arcs = S.trend_arcs({"trend": {"state": "neutral"}, "trend_7d": {}})
    assert arcs[0]["value"] is None
    assert arcs[1]["value"] is None


def test_trend_arcs_unparseable_score_is_none_the_second_50_fallback():
    """trend_gauge_value has TWO fallbacks: the explicit ``v is None`` and the
    implicit ``_safe_float(v, 50.0)`` for a non-None non-numeric. Both must
    read as "no data" on the ring."""
    for junk in ("n/a", "", [], {}, None):
        assert S.trend_gauge_value({"score": junk}) == 50.0        # the fallback
        assert S.trend_arcs({"trend": {"score": junk}})[0]["value"] is None, junk


def test_trend_arcs_non_finite_score_is_none_not_a_clamped_100():
    """``_clamp(nan, 0, 100)`` is 100.0 — ``min(100.0, nan)`` returns 100.0
    because the comparison is False — so an unguarded NaN paints a full,
    maximally-bullish arc. JSON round-trips Infinity/NaN, so this is reachable."""
    assert S.trend_gauge_value({"score": float("nan")}) == 100.0   # the trap
    for junk in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
        assert S.trend_arcs({"trend": {"score": junk}})[0]["value"] is None, junk


def test_trend_arcs_keeps_a_real_zero_and_clamps():
    arcs = S.trend_arcs({"trend": {"score": 0.0}, "trend_7d": {"score": 150}})
    assert arcs[0]["value"] == 0.0        # a real 0 is a reading, not "no data"
    assert arcs[1]["value"] == 100.0      # clamped


def test_trend_arcs_zero_confidence_is_no_data_not_a_neutral_50():
    """The failure path that actually fires in production.

    The service does NOT omit a horizon it failed to compute — ``_neutral_trend``
    and ``_neutral_structural_trend`` both return a fully shaped dict carrying
    score 50.0 / confidence 0.0, and ``compute_7d_trend`` swallows its own
    exceptions to return exactly that. So a proxy blip replaces a good reading
    with a confident-looking 50, and every absent-key guard misses it."""
    published_neutral = {"score": 50.0, "state": "range", "confidence": 0.0}
    arcs = S.trend_arcs({"trend": published_neutral,
                         "trend_7d": published_neutral,
                         "trend_30d_ago": published_neutral})
    assert [a["value"] for a in arcs] == [None, None, None]


def test_trend_arcs_a_confident_neutral_reading_still_paints():
    """The converse, and the reason this guard keys on CONFIDENCE not on the
    score: a genuine 50 backed by evidence is a real reading and must survive.
    Blanking it would trade one fabrication for a different lie."""
    arcs = S.trend_arcs({"trend": {"score": 50.0, "confidence": 0.65}})
    assert arcs[0]["value"] == 50.0


def test_trend_arcs_a_missing_confidence_key_still_paints():
    """Absent confidence must not read as zero — older payloads and the page's
    own test fixtures omit it, and they carry real scores."""
    arcs = S.trend_arcs({"trend": {"smoothed_score": 71.0}})
    assert arcs[0]["value"] == 71.0


# ── Colorized intraday figures (Task 4) ──────────────────────────────────────
_PTS = [{"ts": 1000, "sentiment": 3.0, "trend": 20.0},
        {"ts": 1120, "sentiment": 6.0, "trend": 55.0},
        {"ts": 1240, "sentiment": 8.0, "trend": 85.0}]


def test_sentiment_intraday_figure_uses_sequential_index_slots():
    # Points map to sequential integer slots (a synthetic category axis packs the
    # trading days contiguously — no overnight dead space). Each point carries its
    # value in ``y`` and its CT date+time in ``name`` (for the tooltip).
    fig = S.build_sentiment_intraday_figure(_PTS)
    data = fig["series"][0]["data"]
    assert len(data) == 3
    assert data[0]["x"] == 0 and data[0]["y"] == 3.0 and "name" in data[0]
    assert [d["x"] for d in data] == [0, 1, 2]           # contiguous slots


def test_sentiment_intraday_figure_has_value_zones():
    fig = S.build_sentiment_intraday_figure(_PTS)
    zones = fig["series"][0]["zones"]
    # red <=4.5, yellow <=6.5, green above
    assert zones[0]["value"] == 4.5 and zones[1]["value"] == 6.5
    assert "color" in zones[-1]


def test_trend_intraday_figure_rescaled_to_0_10():
    # Trend is stored 0-100 but shown on a 0-10 scale (like sentiment): value ×0.1,
    # y-axis max 10, and zone boundaries at 3/7 (the 30/70 trend-state cuts /10).
    fig = S.build_trend_intraday_figure(_PTS)
    data = fig["series"][0]["data"]
    assert data[2]["y"] == 8.5                     # 85.0 → 8.5
    zones = fig["series"][0]["zones"]
    assert zones[0]["value"] == 3 and zones[1]["value"] == 7
    assert fig["yAxis"]["min"] == 0 and fig["yAxis"]["max"] == 10


def test_intraday_figures_break_line_across_overnight_gap():
    # Two RTH points on consecutive CT days (overnight gap > threshold): a NULL slot
    # is inserted (so the line breaks — each trading day is its own segment) but the
    # days stay CONTIGUOUS on the index axis (no dead space). Two days → two labeled
    # tick positions.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
    ts1 = int(datetime(2025, 1, 1, 14, 0, tzinfo=CT).timestamp())   # 14:00 CT day 1
    ts2 = int(datetime(2025, 1, 2, 8, 30, tzinfo=CT).timestamp())   # 08:30 CT day 2
    pts = [{"ts": ts1, "sentiment": 5.0, "trend": 50.0},
           {"ts": ts2, "sentiment": 6.0, "trend": 60.0}]
    for fig in (S.build_sentiment_intraday_figure(pts),
                S.build_trend_intraday_figure(pts)):
        data = fig["series"][0]["data"]
        # 2 real points + 1 null slot between them, all on contiguous integer slots.
        assert len(data) == 3
        assert any(d.get("y") is None for d in data)
        assert [d["x"] for d in data] == [0, 1, 2]
        assert len(fig["xAxis"]["tickPositions"]) == 2   # one labeled tick per day
        # No datetime axis / stock-only keys (those froze updates or dropped labels).
        assert "type" not in fig["xAxis"] and "breaks" not in fig["xAxis"]
        assert "time" not in fig

    # No break when points are within the same session (small gap) → no null slot.
    close = [{"ts": ts1, "sentiment": 5.0, "trend": 50.0},
             {"ts": ts1 + 120, "sentiment": 6.0, "trend": 60.0}]
    d = S.build_sentiment_intraday_figure(close)["series"][0]["data"]
    assert len(d) == 2 and all(pt.get("y") is not None for pt in d)


def test_intraday_figures_empty_points_are_valid():
    assert S.build_sentiment_intraday_figure([])["series"][0]["data"] == []
    assert S.build_trend_intraday_figure(None)["series"][0]["data"] == []


def test_intraday_figures_render_in_central_time():
    # The recorded ts are UTC epoch; the per-point tooltip name (and the day-boundary
    # tick labels) must format in Central Time, not UTC. A ts at 12:00 UTC is 06:00
    # (CST) / 07:00 (CDT) CT — so the name's time is NOT "12:00".
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ts = 1_735_732_800  # 2025-01-01 12:00:00 UTC
    ct = datetime.fromtimestamp(ts, ZoneInfo("America/Chicago"))
    fig = S.build_sentiment_intraday_figure([{"ts": ts, "sentiment": 5.0, "trend": 50.0}])
    name = fig["series"][0]["data"][0]["name"]
    assert f"{ct:%H:%M}" in name          # CT time-of-day
    assert "12:00" not in name            # NOT the UTC time-of-day


