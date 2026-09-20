"""The Signal Desk shell on the page kit (Phase 5, Task 1).

``trade_shell.page`` is the frame for FOUR screens — Overview, Evidence, Rank
Board and Trade Plan — so every assertion in this file lands on all four at
once. That is also why the migration ships alone.

The wait, the Symbol field and the two report buttons all live in the shell
rather than on a screen, so this is where their behaviour is pinned.
"""
import inspect
import pathlib

import pytest
from nicegui import ui

from pages import trade_shell as sh

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"

# The four screens and the nav word each one must call itself. The words are
# cross-checked against main.TRADE_CHILDREN below rather than trusted here.
SCREENS = (("trade_overview", "Overview"), ("trade_evidence", "Evidence"),
           ("trade_board", "Rank Board"), ("trade_plan_screen", "Trade Plan"))


def _src(name):
    return (PAGES / f"{name}.py").read_text(encoding="utf-8")


def _render(module_name):
    """Render one Signal Desk screen and return ONLY the elements IT built.

    ``ui.context.client.elements`` is the auto-index client every test in this
    module shares, so a plain ``elements.values()`` also hands back the spinner
    another page's kit region left behind — and a scrim test then passes
    whatever the page under test did (the Phase 3 Task 1 measurement). Diffing
    the ids around the render is what makes these assertions about this page.
    """
    import importlib
    module = importlib.import_module(f"pages.{module_name}")
    before = set(ui.context.client.elements)
    with ui.card():
        module.render()
    return [e for i, e in ui.context.client.elements.items() if i not in before]


def _descends_from(child, ancestor):
    node = child
    while node is not None:
        if node is ancestor:
            return True
        slot = getattr(node, "parent_slot", None)
        node = slot.parent if slot is not None else None
    return False


# ── the frame ───────────────────────────────────────────────────────────────
def test_the_shell_frame_is_the_kit_and_carries_no_terminal_surface():
    """One frame, four screens: the page column, the header line and the
    Updated stamp all come from the kit, and the Signal Desk's own ground and
    face are gone from it."""
    src = inspect.getsource(sh.page)
    assert "kit.page()" in src
    assert 'kit.header(title, view=view, stale=False)' in src
    assert "add_head_html" not in src
    # Task 5 deleted those three from the module, so ``"T.PAGE" not in src``
    # can no longer fail whatever this function does. The attribute check can
    # — the positive form ``test_theme.py:541`` uses for ``[rotation]``.
    from pages import terminal_theme as T
    for name in ("PAGE", "SHELL", "FONT_HTML"):
        assert not hasattr(T, name), \
            f"terminal_theme.{name} is a page-scoped surface value"


def test_the_stamp_defaults_to_the_analysis_and_a_screen_may_name_its_own():
    """Three of the four screens show the symbol analysis, so that is the
    default. The Rank Board shows a universe-wide board published under its own
    key, and a stamp naming the analysis there would time the command bar
    rather than the thing on screen."""
    assert inspect.signature(sh.page).parameters["view"].default == sh.VIEW


def test_the_shell_hands_the_header_to_the_screen():
    """A screen's own page ACTION belongs in the one actions row. Without this
    the Rank Board's Rebuild had nowhere to go but a container its repaint
    clears — which deleted the button and the backstop timer ``kit.set_busy``
    mounts in its parent slot."""
    assert '"head": head' in inspect.getsource(sh.page)


def test_the_signal_desk_fonts_stop_loading():
    """Manrope and JetBrains Mono were injected per page build by the shell.
    The app loads IBM Plex once, app-wide; a second and third web font on four
    screens is exactly what the guard's ``add_head_html`` entry was about, and
    a font link is the one thing its docstring allows no reason for."""
    from pages import terminal_theme as T
    assert not hasattr(T, "FONT_HTML"), \
        "the Signal Desk font link must be gone, not merely unused"
    for name in ("trade_shell", "terminal_theme"):
        src = _src(name)
        assert "fonts.googleapis.com" not in src, name
        assert "add_head_html" not in src, name


def test_the_shell_takes_the_screen_s_own_title():
    """Overview and Evidence had NO title at all — the shell drew only the
    family name — so a reader met a symbol box and a wall of panels."""
    assert "title" in inspect.signature(sh.page).parameters
    with pytest.raises(TypeError):
        sh.page(lambda _s, _r: None)      # a screen must name itself


@pytest.mark.parametrize("module_name,title", SCREENS)
def test_each_screen_passes_the_nav_s_word(module_name, title):
    assert f'"{title}"' in _src(module_name), \
        f"{module_name} must pass {title!r} to trade_shell.page"


def test_the_titles_are_the_nav_s_own_words_not_a_second_vocabulary():
    """The breadcrumb, the rail and the header must agree. ``trade_board``
    called itself "Rank board" where the nav says "Rank Board"."""
    import main
    nav = {route: label for route, label, _icon in main.TRADE_CHILDREN}
    assert set(nav.values()) == {t for _m, t in SCREENS}


@pytest.mark.parametrize("module_name,title", SCREENS)
def test_the_header_names_the_screen_on_every_one_of_the_four(module_name, title):
    els = _render(module_name)
    labels = [e.text for e in els if isinstance(e, ui.label)]
    assert title in labels, f"{module_name} draws no header title"


def test_no_screen_draws_a_SECOND_title_under_the_header():
    """Rank Board and Trade Plan each drew their own headline, because the
    shell had none to give them. The header supplies it now, and a doubled
    title is the one thing a shared frame makes easy to ship."""
    for name in ("trade_board", "trade_plan_screen"):
        assert "SCREEN_TITLE" not in _src(name), name


# ── the wait ────────────────────────────────────────────────────────────────
def test_the_wait_covers_the_RESULTS_not_the_whole_screen():
    """``build_busy(shell, …)`` scrimmed the page column, so an analyze greyed
    out the Symbol box the reader had just typed into — a full-screen overlay
    on a page the design does not list among the three that get one. The kit
    region scopes it to the panels being replaced."""
    els = _render("trade_overview")
    spinners = [e for e in els if isinstance(e, ui.spinner)]
    assert len(spinners) == 1, "the shell mounts exactly one wait"
    scrim = spinners[0].parent_slot.parent
    outer = scrim.parent_slot.parent
    inputs = [e for e in els if isinstance(e, ui.input)]
    assert inputs, "the Symbol field is built"
    for inp in inputs:
        assert not _descends_from(inp, outer), \
            "the wait must not cover the Symbol field"


def test_the_analyze_backstop_outlasts_a_real_analysis():
    """MEASURED: a COIN analysis took 96s end to end on 2026-08-31.

    busy.BUSY_TIMEOUT_SEC is 30s -- sized for the Simulator's ~19s fetch -- so
    the spinner vanished at t=30 and left an empty panel for another 66
    seconds. The operator reported it as "did not return anything", which is
    exactly what it looked like. The 300s fix was written on ``pages/trade.py``
    — a page NOTHING routes — while all four reachable screens ran the 30s
    default; it lives here now, where the wait actually is."""
    from pages import busy
    assert sh.ANALYZE_TIMEOUT_SEC > 96, \
        "the backstop must outlast a measured analysis"
    assert sh.ANALYZE_TIMEOUT_SEC > busy.BUSY_TIMEOUT_SEC, \
        "the shared default is too short for this page and must be overridden"
    assert f"timeout=ANALYZE_TIMEOUT_SEC" in inspect.getsource(sh.page), \
        "the region must actually use it"


def test_analyzing_label_counts_up():
    assert sh.analyzing_label("COIN", 0) == "Analyzing COIN… 0s"
    assert sh.analyzing_label("COIN", 41.6) == "Analyzing COIN… 41s"


def test_analyzing_label_says_so_once_it_runs_long_rather_than_going_quiet():
    """Past the typical duration the wait needs a different word, or the reader
    is left deciding for themselves whether it has died. It must NOT claim
    failure -- the analysis genuinely does take this long sometimes."""
    txt = sh.analyzing_label("COIN", sh.TYPICAL_ANALYZE_SEC + 20)
    assert "COIN" in txt and "s" in txt
    assert txt != sh.analyzing_label("COIN", 5)
    assert "fail" not in txt.lower() and "error" not in txt.lower()


def test_analyzing_label_survives_a_missing_symbol_or_clock():
    for sym, sec in (("", 10), (None, 10), ("COIN", None), ("COIN", -1)):
        out = sh.analyzing_label(sym, sec)
        assert isinstance(out, str) and out


def test_the_wait_counts_UP_rather_than_holding_one_sentence():
    """busy.py's own docstring: "A static message for ninety seconds is
    indistinguishable from a hang -- which is exactly how a 96s trade analysis
    read to its operator." The counter existed on the unrouted page only."""
    assert "elapsed_label" in inspect.getsource(sh.page)
    els = _render("trade_overview")
    spin = [e for e in els if isinstance(e, ui.spinner)][0]
    # The scrim's label is the sibling the counter writes into.
    texts = [c.text for c in spin.parent_slot.children if isinstance(c, ui.label)]
    assert any("Analyz" in t for t in texts)


# ── the Symbol field ────────────────────────────────────────────────────────
def test_the_symbol_box_is_the_app_s_one_symbol_field():
    """Five Symbol behaviours across the app, and this was the fourth: it
    listened on ``blur``, which NiceGUI attaches to the q-input ROOT, where
    ``blur`` does not bubble — so that listener never fired. The kit's field
    uses ``focusout``, which does."""
    src = inspect.getsource(sh._command_bar)
    assert "kit.symbol_field(" in src
    assert '"blur"' not in src and "'blur'" not in src, \
        "blur does not bubble to the q-input root; focusout does"
    els = _render("trade_overview")
    inputs = [e for e in els if isinstance(e, ui.input)]
    assert any(hasattr(i, "_symbol_load_last") for i in inputs), \
        "the field is bound through pages.options.inputs.bind_symbol_load"
    assert any("uppercase" in i.classes for i in inputs)


def test_the_app_s_symbol_dedup_carries_the_signal_desk_s_own_rule():
    """``should_commit`` is the Signal Desk's rule — Enter always requests,
    Tab/blur only a CHANGE, an empty box is never a request. The kit's field is
    ``bind_symbol_load(enter_always=True)``, which is the same rule; this pins
    that they agree, so routing the shell through the shared helper did not
    quietly relax it."""
    from pages import trade_terminal as tt
    from pages.options import inputs
    for draft, committed in (("MU", "MU"), ("NVDA", "MU"), ("", "MU"),
                             ("   ", "MU"), ("MU", ""), (" mu ", "MU")):
        cur = (draft or "").strip().upper()
        assert inputs.should_load(cur, (committed or "").strip().upper()) is \
            tt.should_commit(draft, committed, explicit=False), (draft, committed)


def test_an_uncommitted_edit_still_says_it_is_not_what_is_on_screen():
    """The draft/committed distinction the indigo border carried: while the
    typed symbol differs from the analyzed one, the panels below are not about
    it. Two static classes swapped, never a computed one."""
    els = _render("trade_overview")
    inp = [e for e in els if isinstance(e, ui.input)][0]
    assert sh.DRAFT_RING not in " ".join(inp.classes)
    inp.value = "TSLA"                       # a draft nobody committed
    assert sh.DRAFT_RING in " ".join(inp.classes)
    inp.value = "AAPL"                       # back to the committed symbol
    assert sh.DRAFT_RING not in " ".join(inp.classes)


def test_an_empty_box_reverts_rather_than_clearing_the_screen():
    """"I cleared the box" is not "analyze nothing" — the committed symbol
    comes back, so the field never disagrees with the panels by being blank."""
    src = inspect.getsource(sh._command_bar)
    assert "focusout" in src, "the revert rides the same event the kit binds"
    assert 'state["draft"]' in src


# ── the two report buttons ──────────────────────────────────────────────────
def test_the_report_buttons_go_through_the_kit_and_sit_in_the_header():
    from pages.options import theme as _t
    src = inspect.getsource(sh._command_bar)
    assert "head.actions" in src and "kit.button(" in src
    els = _render("trade_overview")
    buttons = {e.text: e for e in els if isinstance(e, ui.button)}
    for label, _cmd, _view, _route in sh._REPORTS:
        assert label in buttons, label
        assert _t.BTN in " ".join(buttons[label].classes), \
            f"{label} is not the app's secondary button"


def test_a_report_click_holds_its_own_button_and_the_watcher_releases_it(monkeypatch):
    """The orphan status label goes. "Running Deep Dive for MU…" was a line
    beside the buttons that only ``_watch_reports`` ever cleared; the kit's
    rule is that a button which starts work shows its OWN spinner and stays
    disabled, which also stops a second click buying a second report."""
    from pages import ui_kit as kit
    sent = []
    monkeypatch.setattr(sh.bus_client, "request",
                        lambda *a, **k: sent.append(a))
    monkeypatch.setattr(sh.bus_client, "read_version", lambda _v: 7)
    with ui.card():
        btn = kit.button("Deep Dive")
    state = {"draft": "MU", "deepdive_btn": btn}

    sh._report(state, "deepdive", "trade:deepdive")()
    assert sent, "the command is still enqueued"
    assert not btn.enabled and "loading" in btn.props

    opened = []
    monkeypatch.setattr(ui.navigate, "to", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(sh.bus_client, "read_version", lambda _v: 8)
    sh._watch_reports(state)()
    assert opened, "the report still opens in a new tab"
    assert btn.enabled and "loading" not in btn.props


def test_the_orphan_report_status_label_is_gone():
    assert "report_status" not in _src("trade_shell")


def test_the_report_backstop_outlasts_a_report():
    """A Deep Dive takes tens of seconds and the AI Query is a paid Claude
    call. ``kit.set_busy``'s 30s default would hand the button back — and the
    second click its own tooltip warns about — while the first report is still
    being written."""
    src = inspect.getsource(sh._report)
    assert "set_busy(" in src and "ANALYZE_TIMEOUT_SEC" in src


def test_the_reports_keep_their_four_tuple_shape():
    """Unpacked in three places here and read by test_trade_help."""
    for row in sh._REPORTS:
        assert len(row) == 4
