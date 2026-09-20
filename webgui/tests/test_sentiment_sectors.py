"""Tests for the Sector & Industry screen (/sentiment/sectors).

Two halves. The row builders it reads through — ``sector_table_rows`` and
``industry_rows`` — still live in ``pages/sentiment.py`` (the composite page
owns the sector-perf transforms), and the pure DISPLAY language is pinned next
door in ``test_sector_heat.py``. What is left here is what only the page can get
wrong: the page-kit frame, where its three buttons land, and which element a
repaint is allowed to delete.
"""
import inspect

from nicegui import ui

from pages import sentiment as S


def _sector_data():
    return [
        {"kind": "sector", "sector": "Information Technology", "label": "Information Technology",
         "etf": "XLK", "name": "Software, semis", "sp_weight": 32.53},
        {"kind": "sector", "sector": "Utilities", "label": "Utilities",
         "etf": "XLU", "name": "Electric, gas", "sp_weight": 2.09},
        {"kind": "industry", "sector": "Information Technology", "label": "Semis",
         "etf": "SMH", "name": "Semiconductors", "sp_weight": 0.0},
    ]


def test_sector_table_rows_built_and_sorted():
    quotes = {"XLK": {"change_pct": 1.0}, "XLU": {"change_pct": 2.0}}
    trends = {"XLK": {"week_pct": 3.0, "month_pct": 5.0},
              "XLU": {"week_pct": -1.0, "month_pct": 0.5}}
    pcr = {"XLK": 0.80, "XLU": 1.20}
    quads = {"XLK": "Leading", "XLU": "Lagging"}
    rows = S.sector_table_rows(_sector_data(), quotes, trends, pcr, quads)
    assert [r["etf"] for r in rows] == ["XLU", "XLK"]   # only sectors, day% desc
    xlk = next(r for r in rows if r["etf"] == "XLK")
    assert xlk["sector"] == "Information Technology"
    assert xlk["day"] == 1.0 and xlk["week"] == 3.0 and xlk["month"] == 5.0
    assert xlk["pcr"] == 0.80 and xlk["rrg"] == "Leading"


def _full_snap(total, **comp):
    base = {"vix_complex": 4, "put_call": 8, "breadth": 7,
            "rotation": 7, "sector_perf": 8, "credit_pulse": 6}
    base.update(comp)
    return {
        "date": "2026-06-12",
        "composite": {"total_score": f"{total:.2f}", "bias": "Neutral",
                      "size_modifier": "1.00x", "aggregate_confidence": 0.8},
        "component_scores": base,
        "component_confidence": {k: 1.0 for k in base},
        "volatility": {"interpretation": "term backwardation"},
        # Real v4.3 shape: pc_equity ($CPCE) was retired and is always blank; the
        # cap-weighted sector P/C lives in interpretation + sector_pcr.
        "options": {"pc_equity": "",
                    "interpretation": "Cap-weighted sector P/C 0.77 (11/11) — call-dominated"},
        "sector_pcr": 0.766,
        "breadth": {"interpretation": "Advancing"},
        "rotation": {"interpretation": "Cyc rank 6.1 vs Def rank 5.8 (spread -0.4) — risk-off"},
    }


# v4.3 weights (credit_pulse out of composite) — mirrors the service-computed
# derived["weights"] dict now passed to the page transform.
_WEIGHTS = {"vix_complex": 0.20, "put_call": 0.20, "breadth": 0.20,
            "rotation": 0.15, "sector_perf": 0.25}


def test_component_table_rows_contrib():
    rows = S.component_table_rows(_full_snap(6.81), _WEIGHTS, rotation_value=None,
                                  sector_value="+0.70%")
    by = {r["name"]: r for r in rows}
    assert "Credit Pulse" not in by          # not in weights -> excluded
    vix = by["VIX Complex"]
    assert vix["score"] == 4 and vix["weight"] == "20%"
    assert abs(vix["contrib"] - 0.20 * 4 * 1.0) < 1e-9     # w*s*conf
    assert by["Sector Performance"]["value"] == "+0.70%"
    # Put/Call value reads the cap-weighted sector-P/C interp (NOT the dead
    # pc_equity field), so the value is consistent with the score.
    assert "0.77" in by["Put/Call (sectors)"]["value"]
    assert by["Put/Call (sectors)"]["value"] != "—"
    # Rotation value comes from the snapshot's OWN dual run (matches the score),
    # NOT the separate sectors-cache string passed as rotation_value.
    assert "Cyc rank" in by["Rotation"]["value"]
    rows2 = S.component_table_rows(_full_snap(6.81, sector_perf=7.6), _WEIGHTS,
                                   sector_value="+0.70%")
    assert next(r for r in rows2 if r["name"] == "Sector Performance")["score"] == 7.6


def test_put_call_value_falls_back_to_sector_pcr():
    # If the interp is blank but sector_pcr is present, show the ratio (never a
    # blank value next to a real score — the reported bug).
    snap = _full_snap(6.0)
    snap["options"] = {"pc_equity": ""}          # no interp
    rows = S.component_table_rows(snap, _WEIGHTS)
    v = next(r for r in rows if r["name"] == "Put/Call (sectors)")["value"]
    assert v != "—" and "0.77" in v


def test_rotation_value_prefers_snapshot_over_stale_sectors_cache():
    # The score comes from the snapshot's dual run; a stale sectors-cache string
    # ("no sector returns available") must NOT be shown next to that score.
    snap = _full_snap(6.0)
    rows = S.component_table_rows(snap, _WEIGHTS,
                                  rotation_value="no sector returns available")
    v = next(r for r in rows if r["name"] == "Rotation")["value"]
    assert v == "Cyc rank 6.1 vs Def rank 5.8 (spread -0.4) — risk-off"


def test_component_table_rows_cold_cache_empty():
    # No weights (cold cache) -> no rows produced (graceful-empty).
    assert S.component_table_rows(_full_snap(6.81), None) == []


def test_tiles_uses_service_band():
    # size/bias/signal now arrive from the service-computed derived band.
    t = S.tiles(_full_snap(6.81), prev_total=6.81,
                band=("1.00x", "Neutral", "Neutral"))
    assert t["modifier"] == "1.00x" and t["bias"] == "Neutral" and t["signal"] == "Neutral"
    assert t["yesterday"] == "6.81"
    assert t["change"] == "+0.00"


def test_tiles_cold_cache_placeholders():
    # No band (cold cache) -> size/bias/signal show '—'.
    t = S.tiles(_full_snap(6.81), None)
    assert t["modifier"] == "—" and t["bias"] == "—" and t["signal"] == "—"
    assert t["yesterday"] == "—"


def test_industry_rows_built():
    quotes = {"SMH": {"change_pct": 2.5}}
    trends = {"SMH": {"week_pct": 4.0, "month_pct": 9.0}}
    rows = S.industry_rows(_sector_data(), "Information Technology", quotes, trends)
    assert len(rows) == 1
    r = rows[0]
    assert r["etf"] == "SMH" and r["day"] == 2.5 and r["week"] == 4.0 and r["month"] == 9.0
    assert r["pcr"] is None and r["rrg"] is None
    assert r["label"] == "Semis"
    assert r.get("is_industry") is True


def test_industry_rows_missing_data_blank():
    rows = S.industry_rows(_sector_data(), "Information Technology", {}, {})
    assert rows[0]["day"] is None and rows[0]["week"] is None and rows[0]["month"] is None


def test_industry_rows_with_pcr_rrg():
    quotes = {"SMH": {"change_pct": 2.5}}
    trends = {"SMH": {"week_pct": 4.0, "month_pct": 9.0}}
    pcr = {"SMH": 0.92}
    quads = {"SMH": "Leading"}
    rows = S.industry_rows(_sector_data(), "Information Technology", quotes, trends, pcr, quads)
    assert rows[0]["pcr"] == 0.92 and rows[0]["rrg"] == "Leading"


def test_industry_rows_blank_when_no_pcr_rrg():
    rows = S.industry_rows(_sector_data(), "Information Technology", {}, {})
    assert rows[0]["pcr"] is None and rows[0]["rrg"] is None


def test_traffic_color_bands():
    assert S.traffic_color(7.0) == S.CLR_GREEN
    assert S.traffic_color(6.5) == S.CLR_GREEN
    assert S.traffic_color(3.0) == S.CLR_RED
    assert S.traffic_color(4.5) == S.CLR_RED
    assert S.traffic_color(5.5) == S.CLR_YELLOW
    assert S.traffic_color("bad") == S.CLR_YELLOW


# ── the Sector & Industry page on the page kit (Phase 3, Task 3) ─────────────
def _grid_payload(**rotation):
    """A ``sentiment:sectors`` payload shaped like ``handlers``' output."""
    return {"sector": {"sector_data": _sector_data(),
                       "quotes": {"XLK": {"change_pct": 1.0},
                                  "XLU": {"change_pct": -0.5}},
                       "trends": {"XLK": {"week_pct": 3.0, "month_pct": 5.0}},
                       "pcr": {"XLK": 1.80, "XLU": 0.42},
                       "quadrants": {}, "rotation": rotation},
            "industries": {}, "sector_at": "2026-08-17T20:00:00+00:00",
            "summary": {"wpct": 0.7, "score": 7.8}}


def _render_sectors(monkeypatch, payload=None):
    """Build the page against the auto-index client and return ONLY the
    elements THIS render built. ``payload=None`` is the cold cache.

    The auto-index client is shared by every test in the session, so a plain
    ``elements.values()`` would also hand back the spinner some other page's
    kit region left behind — and the scrim test below would then pass whatever
    this page did. That is not hypothetical: it is the measured Task 1 failure
    recorded on ``_rrg_render`` in ``test_sentiment_rotation.py``.
    """
    import bus_client
    from pages import sentiment_sectors
    monkeypatch.setattr(bus_client, "read", lambda _v: payload)
    monkeypatch.setattr(bus_client, "read_version",
                        lambda _v: 1 if payload else None)
    before = set(ui.context.client.elements)
    with ui.card():
        sentiment_sectors.render()
    return [e for i, e in ui.context.client.elements.items() if i not in before]


def _button(elements, text):
    return next(e for e in elements
                if isinstance(e, ui.button) and str(e.text) == text)


def _click(elements, text):
    """Press a button. NiceGUI wraps an ``on_click`` in a one-arg lambda taking
    the click args, so the stored handler is not the zero-arg one passed in."""
    button = _button(elements, text)
    next(listener.handler for listener in button._event_listeners.values()
         if listener.type == "click")(None)


def test_the_sectors_scrim_survives_the_build_repaint_that_used_to_delete_it(
        monkeypatch):
    """The bug this migration fixes. ``build_busy`` mounted the scrim INSIDE
    ``grid_box``, and ``_render_rows`` opens with ``grid_box.clear()`` — so the
    first ``_apply()`` on build deleted it, and every later Refresh raised a
    scrim that no longer existed. ``kit.region`` keeps the spinner on ``outer``
    and clears only ``content``, so it is still here after the build repaint."""
    els = _render_sectors(monkeypatch)
    assert any(isinstance(e, ui.spinner) for e in els), \
        "the region's spinner was deleted by the build-time repaint"


def test_the_sectors_scrim_survives_an_interaction_repaint_too(monkeypatch):
    """Worse here than on the RRG: ``_render_rows`` is ALSO the repaint for
    ``_toggle``, ``_sort_by``, ``_expand_all`` and ``_collapse_all``, so the
    scrim died on every interaction, not only on build. Pressing Collapse must
    leave it attached to the client."""
    els = _render_sectors(monkeypatch, _grid_payload(day_spread=0.1))
    alive = {e.id for e in els if isinstance(e, ui.spinner)}
    assert alive, "no spinner to survive: the build repaint already deleted it"
    _click(els, "Collapse")                 # → _collapse_all → _render_rows
    assert alive <= set(ui.context.client.elements), \
        "a grid repaint deleted the region's spinner"


def test_the_sectors_frame_is_the_kit_and_carries_no_surface_of_its_own():
    from pages import sentiment_sectors
    src = inspect.getsource(sentiment_sectors)
    assert "kit.page()" in src
    assert 'kit.header("Sector & Industry", view=VIEW, stale=False)' in src
    # The page-scoped ground, faces, greys and rules are gone. stale=False is
    # deliberate: the view publishes hourly (sentiment_svc SECTORS_MINUTE = 38)
    # and alerts.STALE_OVERRIDES has no entry for it, so the 600 s default
    # would paint the stamp amber for ~50 minutes of every hour.
    for token in ("SC_SANS", "SC_MONO", "SC_VOID_BG", "SC_TXT", "SC_DIM",
                  "SC_FAINT", "SC_EDGE", "SECTOR_FONT_HEAD_HTML"):
        assert token not in src, f"{token} is a page-scoped surface value"


def test_the_sectors_refresh_toast_is_gone_because_the_spinner_says_it():
    from pages import sentiment_sectors
    src = inspect.getsource(sentiment_sectors.render)
    assert "ui.notify" not in src and "Refreshing — the page updates" not in src


def test_refresh_is_a_header_action_while_the_grid_controls_are_a_control_bar(
        monkeypatch):
    """Three buttons, two destinations. Refresh is a PAGE action — it commands
    the sentiment service — and belongs in the header beside the Updated stamp.
    Expand all and Collapse are ungated page state that operates on the GRID,
    so they sit in a control bar under it. Before the migration all three
    shared one row in the page's own headline."""
    from pages import sentiment_sectors
    els = _render_sectors(monkeypatch)
    slots = {t: _button(els, t).parent_slot
             for t in ("Refresh", "Expand all", "Collapse")}
    assert slots["Expand all"] is slots["Collapse"], \
        "the two grid controls belong in one bar"
    assert slots["Refresh"] is not slots["Expand all"], \
        "Refresh is a page action: it goes in the header, not beside the grid"
    src = inspect.getsource(sentiment_sectors.render)
    assert "with head.actions:" in src and "kit.control_bar()" in src


def test_the_regime_word_wears_its_tone_rather_than_one_flat_colour(monkeypatch):
    """``_TONE_TXT`` was dead on the ADD path: ``_apply`` removed its values and
    then added ``SC_TXT`` / ``SC_FAINT``, so the map existed and the word never
    wore it — while ``config/theme.toml`` documents ``[sectors] up`` as the
    "risk-on regime word + dot". Wired, not deleted."""
    from pages.options.theme import SECTOR_TOKENS as T
    els = _render_sectors(monkeypatch,
                          _grid_payload(day_spread=1.5, day_cyc=2.0, day_def=0.5))
    word = next(e for e in els
                if str(getattr(e, "text", "")) == "Strong risk-on regime")
    assert T["SC_UP"] in word._classes, \
        "the regime word is painted one flat colour whatever the regime is"


def test_the_regime_tones_and_the_heat_tiles_are_untouched():
    """Charts keep their data colours: the half the kit may not reach.

    Passes before and after the migration by design — it is the guard on what
    must NOT change, not a red-then-green test."""
    from pages import sentiment_sectors as P
    from pages.options.theme import SECTOR_TOKENS as T
    for tone, txt, bg in (("up", "SC_UP", "SC_UP_BG"),
                          ("down", "SC_DN", "SC_DN_BG"),
                          ("warn", "SC_WARN", "SC_WARN_BG")):
        assert P._TONE_TXT[tone] == T[txt], f"{txt} is the regime word's colour"
        assert P._TONE_BG[tone] == T[bg], f"{bg} is the regime dot's colour"
    assert P._PCR_TXT["warn"] == T["SC_WARN"], "the put-heavy amber"
    # The oklch cell map is reached exactly twice — a sector row and an
    # industry row — and neither may grow a colour of its own.
    src = inspect.getsource(P.render)
    assert src.count("H.heat_classes(") == 2
