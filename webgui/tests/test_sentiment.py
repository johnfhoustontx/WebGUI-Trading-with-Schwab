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


def test_bias_color_buckets():
    assert S.bias_color("Bullish") == S.CLR_GREEN
    assert S.bias_color("Bearish") == S.CLR_RED
    assert S.bias_color("Neutral") == S.CLR_YELLOW


# ── Local Tailwind color-class maps (Phase 5) — driven by config [charts] ──
# These test the band→color MAPPING, so they reference the module's own
# config-derived class constants (S.TXT_G, S.BG_R, …) rather than literal hexes,
# and stay correct when config/theme.toml is re-themed (e.g. Deep Slate).
def test_local_class_constants_mirror_palette():
    # The CLR_* constants mirror config/theme.toml [charts]; the class strings
    # wrap them as text-[]/bg-[] utilities.
    assert S.TXT_G == f"text-[{S.CLR_GREEN}]" and S.TXT_R == f"text-[{S.CLR_RED}]"
    assert S.TXT_Y == f"text-[{S.CLR_YELLOW}]" and S.TXT_FLAT == f"text-[{S.CLR_FLAT}]"
    assert S.TXT_CY == f"text-[{S.CLR_CYAN}]"
    assert S.BG_G == f"bg-[{S.CLR_GREEN}]" and S.BG_R == f"bg-[{S.CLR_RED}]" and S.BG_Y == f"bg-[{S.CLR_YELLOW}]"
    # remove-sets cover every class an in-place element can apply
    assert set(S.SENT_TEXT_CLASSES.split()) == {S.TXT_G, S.TXT_R, S.TXT_Y, S.TXT_FLAT, S.TXT_CY}
    assert set(S.TRAFFIC_BG_CLASSES.split()) == {S.BG_G, S.BG_R, S.BG_Y}


def test_traffic_bg_class_maps_bands():
    assert S.traffic_bg_class(7) == S.BG_G
    assert S.traffic_bg_class(4) == S.BG_R
    assert S.traffic_bg_class(5.5) == S.BG_Y


def test_bias_text_class_buckets():
    assert S.bias_text_class("Bullish") == S.TXT_G
    assert S.bias_text_class("Bearish") == S.TXT_R
    assert S.bias_text_class("Neutral") == S.TXT_Y


def test_bias_text_class_agrees_with_the_signals_tile_on_the_same_word():
    """The headline under the Sentiment ring and the BIAS tile render the SAME
    word from the same payload. bias_color only substring-matches bull/bear, so
    'Long' read amber in the headline while the tile beside it read green.
    Pin the agreement, not just the buckets."""
    for word in ("Long", "Short", "Cautious", "Neutral", "Strong Bull",
                 "Strong Bear", "Bullish", "Bearish"):
        assert S.bias_text_class(word) == S._TONE_TXT[S._word_tone(word)], word
    # and the specific regression: positioning words are no longer amber
    assert S.bias_text_class("Long") == S.TXT_G
    assert S.bias_text_class("Short") == S.TXT_R
    # an absent bias reads flat rather than a fabricated amber "neutral"
    assert S.bias_text_class("") == S.TXT_FLAT
    assert S.bias_text_class(None) == S.TXT_FLAT


def test_pct_text_class():
    assert S.pct_text_class(0.5) == S.TXT_G
    assert S.pct_text_class(-0.5) == S.TXT_R
    assert S.pct_text_class(0.0) == S.TXT_FLAT
    assert S.pct_text_class(None) == S.TXT_FLAT


def test_pcr_text_class_and_rrg_text_class():
    assert S.pcr_text_class(0.9) == S.TXT_G
    assert S.pcr_text_class(1.1) == S.TXT_R
    assert S.pcr_text_class(1.0) == S.TXT_FLAT
    assert S.rrg_text_class("Improving") == S.TXT_CY
    assert S.rrg_text_class("Lagging") == S.TXT_R
    assert S.rrg_text_class("Leading") == S.TXT_G
    assert S.rrg_text_class("Weakening") == S.TXT_Y
    assert S.rrg_text_class("???") == S.TXT_FLAT


# ───────────────────────── Signals column (1x4 glowing tiles) ─────────────────
def test_signal_tile_defs_carry_the_full_reference_anatomy():
    defs = S.SIGNAL_TILE_DEFS
    assert [d["key"] for d in defs] == ["bias", "signal", "yesterday", "change"]
    assert [d["label"] for d in defs] == ["BIAS", "SIGNAL", "YESTERDAY", "CHANGE"]
    assert [d["descriptor"] for d in defs] == [
        "MARKET DIRECTION", "STRENGTH & MOMENTUM", "PREVIOUS CLOSE", "VS YESTERDAY"]
    # every tile carries its header + footer icon
    assert all(d["icon"] and d["foot_icon"] for d in defs)


def test_tone_classes_cover_the_finite_set_and_carry_the_glow():
    assert set(S.TONE_CLASSES) == {"pos", "neg", "warn", "flat"}
    pos = S.TONE_CLASSES["pos"]
    # the neon glow on the value + the colour-tinted shell/rule/dot
    assert f"[text-shadow:0_0_12px_{S.CLR_GREEN}]" in pos["text"]
    assert f"text-[{S.CLR_GREEN}]" in pos["text"]
    assert "bg-gradient-to-b" in pos["tile"] and "to-[#0a0f14]" in pos["tile"]
    assert f"border-[{S.CLR_GREEN}]/40" in pos["tile"]
    # box-shadow arbitraries must use the rgba() form, and hold no spaces
    assert "shadow-[0_0_18px_-6px_rgba(" in pos["tile"]
    assert " " not in pos["tile"].split("shadow-[")[1].split("]")[0]
    assert pos["dot"] == f"bg-[{S.CLR_GREEN}]"


def test_tone_remove_sets_cover_every_class_each_element_can_apply():
    # If a remove-set misses a class, that class stacks across the page's
    # version-poll repaint (the documented failure mode for reactive recolors).
    for attr, remove_set in (("text", S.TONE_TEXT_CLASSES),
                             ("tile", S.TONE_TILE_CLASSES),
                             ("rule", S.TONE_RULE_CLASSES),
                             ("dot", S.TONE_DOT_CLASSES)):
        present = set(remove_set.split())
        for tone in S.TONE_CLASSES.values():
            assert set(tone[attr].split()) <= present, attr


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


def test_signal_tile_rows_tone_mapping():
    rows = {r["key"]: r for r in
            S.signal_tile_rows(_tile_values(bias="Long", signal="Bullish",
                                            change="+0.42"), prev_total=4.0)}
    assert rows["bias"]["tone"] == "pos"        # 'Long'
    assert rows["signal"]["tone"] == "pos"      # 'Bullish'
    assert rows["yesterday"]["tone"] == "neg"   # prior 4.0 -> red band
    assert rows["change"]["tone"] == "pos"      # positive change
    # values + static chrome ride along
    assert rows["bias"]["value"] == "Long"
    assert rows["change"]["descriptor"] == "VS YESTERDAY"
    assert all(r["tone"] in S.TONE_CLASSES for r in rows.values())


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


def test_velocity_lines_recovers_the_published_fields():
    derived = {"velocity": {"text": "3d ROC: +0.42 | 5d ROC: -0.18 | 20d Z: +1.10",
                            "flag": "REGIME BREAK: +2.30σ from 20d mean"},
               "divergence": "VIX complex diverging from breadth"}
    out = S.velocity_lines(derived)
    assert out["text"].startswith("3d ROC: +0.42")
    assert out["flag"].startswith("REGIME BREAK")
    assert out["divergence"] == "VIX complex diverging from breadth"


def test_velocity_lines_defensive_on_missing_or_malformed():
    for bad in (None, {}, {"velocity": None, "divergence": None},
                {"velocity": "not-a-dict"}):
        out = S.velocity_lines(bad)
        assert out == {"text": "", "flag": "", "divergence": ""}
    # a quiet tape: text present, no regime break, no divergence
    out = S.velocity_lines({"velocity": {"text": "3d ROC: —", "flag": ""},
                            "divergence": ""})
    assert out["text"] == "3d ROC: —" and out["flag"] == "" and out["divergence"] == ""


def test_tone_swap_does_not_accumulate_classes_across_repaints():
    """Two successive tone applications must leave ONE tone's classes on each
    element — the remove/add idiom, exercised through real NiceGUI elements."""
    from nicegui import ui

    with ui.card():
        lbl = ui.label("—").classes(S.TONE_CLASSES["flat"]["text"])
        card = ui.card().classes(S.TONE_CLASSES["flat"]["tile"])
        dot = ui.element("div").classes(S.TONE_CLASSES["flat"]["dot"])
    for tone in ("pos", "neg", "pos"):
        t = S.TONE_CLASSES[tone]
        lbl.classes(remove=S.TONE_TEXT_CLASSES, add=t["text"])
        card.classes(remove=S.TONE_TILE_CLASSES, add=t["tile"])
        dot.classes(remove=S.TONE_DOT_CLASSES, add=t["dot"])
    # Compare within the tone vocabulary only (ui.card carries 'nicegui-card').
    for el, attr, remove_set in ((lbl, "text", S.TONE_TEXT_CLASSES),
                                 (card, "tile", S.TONE_TILE_CLASSES),
                                 (dot, "dot", S.TONE_DOT_CLASSES)):
        vocab = set(remove_set.split())
        assert set(el._classes) & vocab == set(S.TONE_CLASSES["pos"][attr].split())
    # and specifically: no other tone's colour survives
    assert f"text-[{S.CLR_RED}]" not in lbl._classes


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


def test_trend_text_class():
    # old-vocab (still used by the 30-day structural gauge)
    assert S.trend_text_class("bull_trend") == S.TXT_G
    assert S.trend_text_class("pullback_in_bull") == S.TXT_G
    assert S.trend_text_class("bear_trend") == S.TXT_R
    assert S.trend_text_class("bear_rally") == S.TXT_R
    assert S.trend_text_class("range") == S.TXT_Y
    # new five-state vocab (direction x aggression) used by the Today gauge
    assert S.trend_text_class("bullish") == S.TXT_G
    assert S.trend_text_class("lack_of_bearishness") == S.TXT_G
    assert S.trend_text_class("bearish") == S.TXT_R
    assert S.trend_text_class("lack_of_bullishness") == S.TXT_Y
    assert S.trend_text_class("neutral") == S.TXT_Y
    assert S.trend_text_class("mystery") == S.TXT_Y  # unknown -> amber default


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


def test_rotation_text_class():
    assert S.rotation_text_class(S.CLR_GREEN) == S.TXT_G
    assert S.rotation_text_class(S.CLR_RED) == S.TXT_R
    assert S.rotation_text_class(S.CLR_YELLOW) == S.TXT_Y
    assert S.rotation_text_class(S.CLR_FLAT) == S.TXT_FLAT


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


# ── Windowed composite average (the ring's Week arc) ─────────────────────────
def test_sentiment_avg_windows_to_the_last_n():
    snaps = _snaps(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
    assert S.sentiment_avg(snaps, 5) == 5.0     # mean of 3..7


def test_sentiment_avg_with_no_window_uses_every_snap():
    assert S.sentiment_avg(_snaps(2.0, 4.0)) == 3.0


def test_sentiment_avg_window_larger_than_history_uses_all():
    assert S.sentiment_avg(_snaps(2.0, 4.0), 5) == 3.0


def test_sentiment_avg_is_zero_with_no_snaps():
    assert S.sentiment_avg([], 5) == 0.0
    assert S.sentiment_avg(None) == 0.0


def test_sentiment_avg_or_none_is_none_with_no_snaps():
    assert S.sentiment_avg_or_none([], 5) is None
    assert S.sentiment_avg_or_none(None) is None


def test_sentiment_avg_week_window_is_five_sessions():
    """WEEK_SNAPS pins the Week arc's window; a 20-day history must NOT average
    everything (the mutation this guards: WEEK_SNAPS = None)."""
    assert S.WEEK_SNAPS == 5
    snaps = _snaps(*([1.0] * 15 + [9.0] * 5))
    assert S.sentiment_avg(snaps, S.WEEK_SNAPS) == 9.0   # not mean(all) == 3.0


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


# --- Market Regime panel (blended structural regime) --------------------------
def _regime(**over):
    r = {"ts": "2026-07-23T10:05:00-05:00", "label": "Trending",
         "committed_label": "trending", "confidence": 0.62, "unclear": False,
         "memberships": {"mean_reversion": 0.28, "trending": 0.52, "breakout": 0.08,
                         "choppy": 0.09, "crisis": 0.03},
         "transition": {"from": "mean_reversion", "to": "trending", "progress": 0.6},
         "evidence": ["ADX 32 rising", "VWAP held 95%"]}
    r.update(over)
    return r


def _regime_points(n=3, base_ts=1_753_280_700):
    return [{"ts": base_ts + i * 300, "confidence": 0.6, "label": "trending",
             "memberships": {"mean_reversion": 0.3, "trending": 0.5, "breakout": 0.05,
                             "choppy": 0.1, "crisis": 0.05}} for i in range(n)]


def test_regime_headline_parts():
    label, conf, cls = S.regime_headline_parts(_regime())
    assert label == "Trending" and "62" in conf and cls
    # NiceGUI .classes(remove=...) splits a STRING; a list raises at render
    # (caught live, not by the builder tests) — and every class it may ADD
    # must be in the remove-set or repaints stack conflicting colors.
    assert isinstance(S.REGIME_TEXT_CLASSES, str)
    removable = set(S.REGIME_TEXT_CLASSES.split())
    for key in list(S.REGIME_ORDER) + ["", "bogus"]:
        for unclear in (False, True):
            _l, _c, c = S.regime_headline_parts(
                _regime(committed_label=key, unclear=unclear))
            assert c in removable


def test_regime_headline_unclear_and_missing():
    assert S.regime_headline_parts(_regime(label="Unclear", unclear=True))[0] == "Unclear"
    # No payload at all -> a waiting placeholder, never a crash.
    for empty in ({}, None):
        label, conf, cls = S.regime_headline_parts(empty)
        assert label and conf == "" and cls


def test_regime_transition_text():
    assert S.regime_transition_text(_regime()) == "Balanced → Trending · 60%"
    # Stable (no transition) / missing -> empty string, so the row hides.
    assert S.regime_transition_text(_regime(transition=None)) == ""
    assert S.regime_transition_text({}) == ""
    assert S.regime_transition_text(None) == ""


def test_regime_evidence_rows():
    assert S.regime_evidence_rows(_regime()) == ["ADX 32 rising", "VWAP held 95%"]
    assert S.regime_evidence_rows({}) == []


def test_regime_transition_text_carries_the_direction():
    r = _regime(direction=1, direction_strong=True)
    assert S.regime_transition_text(r) == "Balanced → Rallying · 60%"
    r = _regime(direction=-1, direction_strong=False)
    assert S.regime_transition_text(r) == "Balanced → Softening · 60%"


def test_regime_row_labels_never_take_the_direction():
    """A row label that renames itself intra-session cannot be tracked across
    repaints. Direction belongs on the headline, not in the panel."""
    svg = S.regime_mix_svg(_regime_points())
    for base in ("Balanced", "Trending", "Breakout", "Whipsaw", "Stressed"):
        assert base in svg
    for adorned in ("Rallying", "Firming", "Retreating", "Softening", "Breakdown"):
        assert adorned not in svg


def test_regime_headline_color_follows_the_direction():
    """Trending's band colour is fixed, but the HEADLINE must not paint a
    down-trend green: green up, red down, neutral grey when no direction is
    claimed."""
    removable = set(S.REGIME_TEXT_CLASSES.split())
    up = S.regime_headline_parts(
        _regime(committed_label="trending", direction=1, direction_strong=True))
    down = S.regime_headline_parts(
        _regime(committed_label="trending", direction=-1, direction_strong=True))
    flat = S.regime_headline_parts(_regime(committed_label="trending", direction=0))
    assert up[0] == "Rallying" and down[0] == "Retreating" and flat[0] == "Trending"
    assert up[2] != down[2]
    assert {up[2], down[2], flat[2]} <= removable


def test_regime_headline_direction_junk_is_neutral():
    for bad in ("up", 2, None, True):
        label, _c, cls = S.regime_headline_parts(
            _regime(committed_label="trending", direction=bad))
        assert label == "Trending"
        assert cls in set(S.REGIME_TEXT_CLASSES.split())


def test_regime_headline_prefers_a_locally_derived_label():
    """The payload's own `label` is the service's word; the page re-derives it
    from (committed_label, direction) so a stale/absent label can't outlive a
    rename, and falls back to the payload when the key is unknown."""
    r = _regime(committed_label="choppy", label="Choppy")
    assert S.regime_headline_parts(r)[0] == "Whipsaw"
