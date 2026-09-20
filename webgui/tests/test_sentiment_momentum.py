"""Momentum page — pure builders (banner, quadrant scatter, ribbon, leaderboard)."""
import pytest

from pages import sentiment_momentum as sm


def _row(symbol, score=1.0, pct=90.0, rank=1, accel=0.5, **extra):
    row = {"symbol": symbol, "label": extra.pop("label", symbol),
           "score": score, "percentile": pct, "rank": rank,
           "rank_prev": extra.pop("rank_prev", None),
           "participation": extra.pop("participation", 0.6),
           "components": {"trend": 1.2, "rs": 0.8, "accel": accel, "path": 0.3},
           "raw": {"trend": 0.42, "excess": 0.11, "slope": 0.01,
                   "accel": 0.05, "path": 0.7}}
    row.update(extra)
    return row


def _payload(**over):
    payload = {
        "schema": 1, "session_date": "2026-07-28",
        "computed_at": "2026-07-28T16:22:04-05:00",
        "regime": {"state": "favorable", "label": "Favorable",
                   "description": "Momentum's home turf.",
                   "lookback": "63/126", "crash_risk": False,
                   "dispersion_pct": 0.62,
                   "reasons": ["SPY above its 200 DMA", "VIX term in contango"]},
        "levels": {
            "sector": [_row("XLK", label="Information Technology")],
            "industry": [_row("SMH", label="Semiconductors", sector="Tech"),
                         _row("XBI", label="Biotech", score=-0.5, pct=10.0, rank=2)],
            "stock": [_row("NVDA", sector="Tech", industry="Semiconductors",
                           participation=None, alignment=[True, True, True]),
                      _row("INTC", score=-0.9, pct=5.0, rank=2, sector="Tech",
                           industry="Semiconductors", participation=None,
                           alignment=[True, False, False])],
        },
        "excluded": [{"symbol": "TPIC", "reason": "liquidity"},
                     {"symbol": "OLD", "reason": "no_quote"}],
    }
    payload.update(over)
    return payload


# --- regime banner ----------------------------------------------------------


def test_favorable_leaderboard_is_not_muted():
    assert sm.leaderboard_muted(_payload()["regime"]) is False


# --- quadrant scatter -------------------------------------------------------


def test_quadrant_names_the_corner_by_score_and_acceleration():
    assert sm.quadrant_for(1.0, 1.0) == "Leading"
    assert sm.quadrant_for(1.0, -1.0) == "Weakening"
    assert sm.quadrant_for(-1.0, 1.0) == "Improving"
    assert sm.quadrant_for(-1.0, -1.0) == "Lagging"


def test_quadrant_of_a_missing_score_is_unknown():
    assert sm.quadrant_for(None, 1.0) == ""


# --- rank ribbon ------------------------------------------------------------


# --- leaderboard ------------------------------------------------------------

def test_leaderboard_shows_top_and_bottom_with_component_columns():
    rows = [_row(f"S{i}", score=float(-i), rank=i + 1) for i in range(40)]

    top, bottom = sm.leaderboard_rows(rows, n=15)

    assert len(top) == 15 and len(bottom) == 15
    assert top[0]["symbol"] == "S0"
    assert bottom[-1]["symbol"] == "S39"
    assert "trend" in top[0] and "accel" in top[0]


def test_leaderboard_does_not_repeat_rows_in_a_short_list():
    rows = [_row("A", rank=1), _row("B", rank=2)]

    top, bottom = sm.leaderboard_rows(rows, n=15)

    assert {r["symbol"] for r in top}.isdisjoint({r["symbol"] for r in bottom})


def test_leaderboard_renders_the_alignment_blocks():
    row = sm.leaderboard_rows(_payload()["levels"]["stock"], n=5)[0][0]

    assert row["alignment"] == "▮▮▮"


def test_partial_alignment_shows_hollow_blocks():
    rows = [_row("X", alignment=[True, False, False])]

    assert sm.leaderboard_rows(rows, n=5)[0][0]["alignment"] == "▮▯▯"


def test_leaderboard_formats_missing_numbers_as_a_dash():
    rows = [_row("X", score=None, pct=None, participation=None)]

    row = sm.leaderboard_rows(rows, n=5)[0][0]

    assert row["score"] == "—"
    assert row["participation"] == "—"


def test_rank_delta_shows_movement_since_the_previous_session():
    assert sm.rank_delta(_row("A", rank=1, rank_prev=5)) == "▲4"
    assert sm.rank_delta(_row("A", rank=5, rank_prev=1)) == "▼4"
    assert sm.rank_delta(_row("A", rank=3, rank_prev=3)) == "–"
    assert sm.rank_delta(_row("A", rank=3, rank_prev=None)) == ""


# --- excluded footer --------------------------------------------------------

def test_footer_counts_the_excluded_symbols():
    assert "2" in sm.excluded_text(_payload()["excluded"])


def test_footer_hover_lists_symbols_with_their_reason():
    tip = sm.excluded_tooltip(_payload()["excluded"])

    assert "TPIC" in tip and "liquidity" in tip
    assert "OLD" in tip and "no_quote" in tip


def test_footer_is_quiet_when_nothing_was_dropped():
    assert sm.excluded_text([]) == ""


# --- level toggle -----------------------------------------------------------

def test_level_options_cover_industry_and_stock():
    assert set(sm.LEVEL_OPTIONS) == {"industry", "stock"}


def test_rows_for_level_reads_the_payload():
    payload = _payload()

    assert sm.rows_for(payload, "industry")[0]["symbol"] == "SMH"
    assert sm.rows_for(payload, "stock")[0]["symbol"] == "NVDA"


def test_rows_for_a_missing_payload_is_empty():
    assert sm.rows_for({}, "industry") == []
    assert sm.rows_for(None, "stock") == []


def test_rank_history_is_read_per_level():
    payload = _payload(rank_history={"industry": {"SMH": [("2026-07-28", 1)]},
                                     "stock": {}})

    assert sm.rank_history_for(payload, "industry") == {"SMH": [("2026-07-28", 1)]}
    assert sm.rank_history_for(payload, "stock") == {}


def test_rank_history_of_a_missing_payload_is_empty():
    assert sm.rank_history_for({}, "industry") == {}
    assert sm.rank_history_for(None, "stock") == {}


# --- columns adapt to the level ---------------------------------------------

def _fields(level):
    return [c["field"] for c in sm.leaderboard_columns(level)]


def test_industry_view_drops_the_stock_only_alignment_column():
    # Alignment is a stock-level flag; a permanently blank column reads as broken.
    assert "alignment" not in _fields("industry")
    assert "participation" in _fields("industry")


def test_stock_view_drops_the_undefined_participation_column():
    # Participation is undefined at stock level — it would be all em-dashes.
    assert "participation" not in _fields("stock")
    assert "alignment" in _fields("stock")


def test_both_levels_keep_the_component_columns():
    for level in ("industry", "stock"):
        assert {"trend", "rs", "accel", "path", "score"} <= set(_fields(level))


def test_unknown_level_falls_back_to_the_full_column_set():
    assert _fields("nonsense") == _fields("industry")


# --- level is addressable ---------------------------------------------------

def test_normalise_level_accepts_the_known_levels():
    assert sm.normalise_level("stock") == "stock"
    assert sm.normalise_level("industry") == "industry"


def test_normalise_level_rejects_junk():
    assert sm.normalise_level("../etc/passwd") == "industry"
    assert sm.normalise_level(None) == "industry"
    assert sm.normalise_level("") == "industry"


def test_section_heading_names_the_level():
    assert "Industries" in sm.section_heading("Leaders", "industry")
    assert "Stocks" in sm.section_heading("Leaders", "stock")


# --- zero lines + quadrant labels -------------------------------------------


# --- ribbon readability ------------------------------------------------------


# ── the Momentum page on the page kit (Phase 3, Task 5) ─────────────────────
# The last of the four rotation-family screens, so this is also where the shared
# warm-neutral ladder retires (see tests/test_rotation_view.py and
# tests/test_theme.py for that half).
import ast
import inspect

from nicegui import ui

from pages import momentum_view as V
from pages import ui_kit as kit
from pages.options import theme

# The faint end of the app's text ladder — the colour ``kit.EYEBROW`` wears, and
# what replaces the page-scoped ``NT["ghost"]`` / ``NT["axis"]`` rungs.
FAINT = f"text-[{theme.THEME['palette']['icon']}]"


def _momentum_render(level="industry"):
    """Render the Momentum page in a slot context and return ONLY the elements
    this render built.

    The auto-index client is shared by every test in the module, so a plain
    ``elements.values()`` would also hand back a spinner some OTHER render left
    behind — and the scrim test below would then pass whatever this page did
    (rule 10 of the plan, measured on Task 1)."""
    before = set(ui.context.client.elements)
    with ui.card():
        sm.render(level)
    return [e for i, e in ui.context.client.elements.items() if i not in before]


def _render_tree():
    return ast.parse(inspect.getsource(sm.render))


def _parents(tree):
    up = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            up[child] = node
    return up


def _calls(tree, attr):
    """Every ``<something>.<attr>(...)`` call in ``tree``."""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def _inside_may_enqueue(node, up):
    """True when ``node`` sits under an ``if _may_enqueue:`` branch."""
    cur = up.get(node)
    while cur is not None:
        if (isinstance(cur, ast.If) and isinstance(cur.test, ast.Name)
                and cur.test.id == "_may_enqueue"):
            return True
        cur = up.get(cur)
    return False


def _by_text(elements, text):
    return next(e for e in elements if str(getattr(e, "text", "")) == text)


def _button(elements, text):
    return next(e for e in elements
                if isinstance(e, ui.button) and str(e.text) == text)


def test_the_momentum_scrim_survives_the_repaint_that_used_to_delete_it():
    """The bug this migration fixes, the fourth screen of the family over.

    ``build_busy`` mounted the scrim INSIDE ``quad_box``, and
    ``_paint_quadrants`` opens with ``quad_box.clear()`` — so the build-time
    ``_apply()`` deleted it, and every later Refresh raised a scrim that no
    longer existed. ``kit.region`` keeps the spinner on ``outer`` and clears
    only ``content``."""
    els = _momentum_render()
    spinners = [e for e in els if isinstance(e, ui.spinner)]
    assert len(spinners) == 1, \
        f"expected the region's one spinner, found {len(spinners)}"


def test_the_momentum_frame_is_the_kit_and_carries_no_surface_of_its_own():
    """``stale=False`` is deliberate: ``[slots.momentum] at = "16:20"``
    recomputes this view once a night, so ``alerts.stale_after`` would call it
    stale every single day. A view that is not due to publish now has an age
    that says nothing."""
    src = inspect.getsource(sm)
    assert "kit.page()" in src
    assert 'kit.header("Momentum", view=VIEW, stale=False)' in src
    assert "kit.region(" in src
    # The page-scoped ground, the two faces and the warm-neutral ladder are
    # gone. Read off the NAMES the module binds rather than the source text, so
    # an unrelated identifier that merely contains "NT" cannot decide this.
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {a.asname or a.name for n in ast.walk(tree)
              if isinstance(n, ast.ImportFrom) for a in n.names}
    for token in ("NT", "NB", "NE", "_T", "_MONO", "ROTATION_TOKENS",
                  "ROTATION_FONT_HEAD_HTML"):
        assert token not in names, f"{token} is a page-scoped surface value"
    for key in ("RT_SANS", "RT_MONO", "RT_VOID_BG", "RT_PANEL_BG"):
        assert key not in src, f"{key} is a page-scoped surface value"
    # The four dead hook classes go with the frame: no CSS rule in the repo
    # defines any of them, so they styled nothing and only read as if they did.
    for hook in ("momentum-table", "momentum-board", "momentum-level",
                 "momentum-more"):
        assert hook not in src, f"{hook} is a hook nothing styles"


def test_the_recompute_toast_is_gone_because_the_spinner_says_it():
    """The "Recomputing" toast only repeated what the region's spinner already
    says, and the standard keeps a toast for the OUTCOME of an action."""
    src = inspect.getsource(sm)
    assert "ui.notify" not in src
    assert "Recomputing — this one takes a moment" not in src


def test_the_level_picker_is_a_labelled_kit_field_and_stays_public():
    """The level picker is pure page state — it commands nothing — so it is the
    control bar's, not the header's, and it is drawn on the public live origin
    where Refresh is not."""
    tree = _render_tree()
    up = _parents(tree)
    picks = _calls(tree, "select_field")
    assert len(picks) == 1, "the level picker is one kit.select_field"
    assert not _inside_may_enqueue(picks[0], up), \
        "the level picker is page state and must stay on the public origin"
    assert "kit.control_bar()" in inspect.getsource(sm.render)
    # ...and Refresh is the opposite: gated, because a refresh is eleven sector
    # chains per click against the owner's Schwab budget.
    refresh = [c for c in _calls(tree, "button")
               if c.args and getattr(c.args[0], "value", None) == "Refresh"]
    assert len(refresh) == 1 and _inside_may_enqueue(refresh[0], up), \
        "Refresh must stay inside the may_enqueue gate"


def test_refresh_is_a_header_action_rather_than_a_row_in_the_page_body():
    """The one page action this screen has — it commands the sentiment service
    — so it belongs beside the Updated stamp. Read off the DOM: ``with
    head.actions:`` is in the source whichever row the button lands in."""
    els = _momentum_render()
    actions = _button(els, "Refresh").parent_slot.parent
    title = _by_text(els, "Momentum")
    assert actions.parent_slot.parent is title.parent_slot.parent, \
        "Refresh is not in the kit header's action row"


def test_the_leaderboard_is_a_kit_table_so_its_columns_sort(monkeypatch):
    """⚠ A behaviour change, not a port: ``kit.table_columns`` defaults every
    data column ``sortable``, so the leaderboard sorts for the first time. The
    per-column ``align`` values survive because the kit only overrides the ones
    named ``numeric``, and this page passes none."""
    import bus_client
    monkeypatch.setattr(bus_client, "read", lambda _v: _payload())
    els = _momentum_render()
    tables = [e for e in els if isinstance(e, ui.table)]
    assert tables, "the leaderboard is not a table"
    for t in tables:
        assert t._props["row-key"] == "symbol"
        cols = {c["name"]: c for c in t._props["columns"]}
        assert all(c.get("sortable") for c in cols.values()), \
            "a leaderboard column that cannot be sorted"
        # The existing alignment is the kit's `c.get("align", "left")` branch.
        assert cols["rank"]["align"] == "right"
        assert cols["label"]["align"] == "left"
        assert "momentum-table" not in t._classes


def test_the_top_ranked_control_is_a_quiet_kit_button():
    """Link-like, and it only resets the page's own selection — ``quiet`` is
    the kit kind for exactly that."""
    src = inspect.getsource(sm.render)
    assert 'kit.button("Top ranked", kind="quiet"' in src


def test_the_name_chip_is_the_one_raw_button_the_page_keeps():
    """``_name_chip`` stays a raw ``ui.button`` WITH A WRITTEN REASON, and the
    guard's ALLOWED entry records it.

    It is one call site producing on the order of the whole level's universe per
    repaint, and it is a selectable name chip — 10.5px, ring-on-select,
    ``max-w-full`` — not a page action. Putting it through ``kit.button`` would
    drop a full-size action button into a quadrant panel hundreds of times."""
    tree = ast.parse(inspect.getsource(sm))
    raw = [n for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and isinstance(n.func.value, ast.Name) and n.func.value.id == "ui"
           and n.func.attr == "button"]
    assert len(raw) == 1, f"expected the one name chip, found {len(raw)}"
    chip = inspect.getsource(sm.render).split("def _name_chip")[1].split("    def ")[0]
    # Restyled with app tokens: the app face (no page-scoped mono), and the
    # app's selection accent for the ring. The chip's BACKGROUND and TEXT hue
    # stay the quadrant's — those are data.
    assert "_SEL_RING" in chip
    assert "cls['chip']" in chip and "cls['chip_txt']" in chip


def test_the_selected_ring_is_the_app_selection_accent():
    """The ring says "this one" and nothing about the datum, so it is the app's
    selection accent — the colour the kit's own selected table row wears
    (``theme.build_surface_css``'s ``.kit-row-selected``)."""
    assert sm._SEL_RING == f"ring-[{theme.THEME['palette']['focus']}]"


def test_the_momentum_quadrant_and_regime_colours_are_untouched():
    """Charts keep their data colours: the half the kit may not reach. Passes
    before and after the migration by design — it is the guard on what must NOT
    change, not a red-then-green test."""
    src = inspect.getsource(sm.render)
    for expr in ("V.QUAD_CLASSES[", "V.REGIME_CLASSES[", "V.ALIGN_CLASSES",
                 "V.ALIGN_ON", "V.ALIGN_OFF", "V.POS_TXT", "V.NEG_TXT",
                 "V.POS_BAR", "V.NEG_BAR", "V.HILITE_TXT", "V.LEVEL_FILL",
                 "V.LEVEL_TRACK", "V.LEVEL_GROOVE"):
        assert expr in src, f"{expr} is a data colour and must survive"
    # The page's own align / dispersion / limits hues are data too: green 158
    # for "all three agree", olive 80 for the dispersion reading and the
    # caveats. Named here so a later pass cannot quietly grey them out.
    for name in ("_ALIGN_PANEL", "_ALIGN_EDGE", "_ALIGN_TITLE", "_ALIGN_BODY",
                 "_DISP_TXT", "_DISP_FILL", "_DISP_MARK", "_LIMIT_TAG"):
        assert getattr(sm, name), f"{name} is a data colour and must survive"


def test_render_still_takes_the_level_argument():
    """Load-bearing and must not change: ``live_screens.py`` pins
    ``kwargs={"level": "industry"}`` for the published screen, and
    ``test_gallery_routes.py`` drives ``?level=``. Passes before and after."""
    sig = inspect.signature(sm.render)
    assert list(sig.parameters) == ["level"]
    assert sig.parameters["level"].default == "industry"
