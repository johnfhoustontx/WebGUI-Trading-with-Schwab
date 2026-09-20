"""Guard: pages build buttons, dialogs, toasts and tables through pages/ui_kit.py.

The consistency standard (docs/plans/2026-09-19-app-ui-consistency-design.md)
holds only if a page cannot quietly build its own button, dialog, toast or
table, or load a web font of its own. ALLOWED is the ratchet: every page that
still does, by file and count. A migrated page DELETES its entry; a count may
only fall; and the list must match the code EXACTLY, so a page that drops a raw
call lowers its entry in the same commit - otherwise the list stops describing
the code. An entry may outlive the migration only with a written reason (a
control that is not an action button: a segmented picker, a leg-table toggle).

⚠ SEVEN entries are PERMANENT rather than pending, and each carries its reason
above it: ``options/detail.py`` (Phase 1), ``desk.py`` / ``market.py`` /
``sentiment_momentum.py`` (Phases 3 & 4), and the three shared Options widgets
``options/entry_panel.py`` / ``options/leg_editor.py`` /
``options/strategy_menu.py`` (Phase 2). Every other entry is a page no phase has
migrated yet and is expected to fall to zero and be deleted. The nine screens
Phases 3 & 4 covered left no entry at all except those three.
"""
import ast
import collections
import pathlib

GUARDED = ("button", "dialog", "notify", "table", "add_head_html")
PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"
KIT = "ui_kit.py"

ALLOWED = {
    "config_editor.py": {"button": 12, "dialog": 2, "notify": 10},
    # The audio player and its queue, and it stays: ``DESK_VOICE_JS`` is a
    # <script>, not a font - the same shape as options/detail.py's toggle. The
    # two FONT links (the console's display face and the page's own JetBrains
    # Mono) went with the kit migration, and the unlock button went through
    # kit.notice + kit.button with them.
    "desk.py": {"add_head_html": 1},
    "driver.py": {"button": 8, "dialog": 2, "notify": 1, "table": 5},
    "eod.py": {"button": 3, "notify": 2},
    "manuals.py": {"button": 1},
    # The Macro Board's skin toggle, and it stays: a SEGMENTED PICKER, mutually
    # exclusive by construction and applied instantly with no Go. kit.button's
    # four kinds have no selected state, so expressing selection through the kit
    # would mean a page-side class swap over button_classes(...) - the exact
    # drift the kit exists to stop. The page has no other control: it commands
    # nothing and polls on a timer.
    "market.py": {"button": 2},
    "options/calculator.py": {"add_head_html": 1, "button": 3, "dialog": 1, "notify": 3},
    # The panel's collapse toggle, and it stays: it carries a floating flag
    # badge and a tooltip retitled on every toggle, which kit.icon_button does
    # not model. Not an action button - the panel's ACTIONS are the page's, and
    # they go through the kit into handle.actions.
    "options/detail.py": {"button": 1},
    # The expiry pill, and it stays: a SEGMENTED PICKER built once per listed
    # expiration on every repaint, whose selected state is a class SWAP over
    # pill_on / pill_off. kit.button's four kinds have no selected state, so
    # routing it through them would mean a page-side swap over
    # button_classes(...) - the drift the kit exists to stop. The panel's two
    # ACTIONS (Load, Columns) go through the kit.
    "options/entry_panel.py": {"button": 1},
    "options/expected_move.py": {"button": 1, "notify": 1},
    "options/gamma.py": {"button": 8, "notify": 7},
    # The leg row's four one-click controls, and they stay: the SELL/BUY side
    # toggle, the two ‹ › strike steppers and the cycling CALL/PUT/STOCK picker.
    # None is an action - each is a segmented or stepping control inside a
    # ~40px table track, and all four carry a per-leg reading (long-cyan /
    # short-green) the kit's kinds cannot express. The six ACTIONS - both
    # layouts' remove and Add leg, Reset to template and the typed-price reset -
    # go through the kit.
    "options/leg_editor.py": {"button": 4},
    "options/simulator.py": {"button": 1, "notify": 1},
    # The cascading Strategy trigger, boxed and plain, and it stays: a VALUE
    # PICKER standing in for ui.select. The kit has no such field, and its
    # button kinds carry neither a current-value label nor a menu anchor; the
    # two spellings exist because ``boxed`` drops the Quasar outline, which
    # forces a transparent background page CSS cannot beat.
    "options/strategy_menu.py": {"button": 2},
    "options/swing.py": {"button": 7, "table": 1},
    "portfolio.py": {"button": 1, "table": 3},
    # The per-member name chip, and it stays: ONE call site producing on the
    # order of the whole level's universe per repaint, and a selectable name
    # chip - 10.5px, ring-on-select, max-w-full - not a page action. Through
    # kit.button it would drop a full-size action button into a quadrant panel
    # hundreds of times. The page's ACTIONS (Refresh, Top ranked) go through
    # the kit.
    "sentiment_momentum.py": {"button": 1},
    "settings.py": {"button": 7, "dialog": 1, "notify": 1},
    "status.py": {"button": 3, "notify": 4},
    "terminate.py": {"button": 3, "dialog": 1, "notify": 1},
    "trade.py": {"button": 3, "table": 2},
    "trade_board.py": {"button": 2},
    "trade_plan_screen.py": {"button": 2, "notify": 1},
    "trade_shell.py": {"add_head_html": 1, "button": 1},
}


def raw_calls():
    """``{page file: {ui.<guarded>: count}}`` for every page but the kit."""
    out = {}
    for f in sorted(PAGES.rglob("*.py")):
        rel = f.relative_to(PAGES).as_posix()
        if rel == KIT:
            continue
        counts = collections.Counter()
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "ui"
                    and n.func.attr in GUARDED):
                counts[n.func.attr] += 1
        if counts:
            out[rel] = dict(sorted(counts.items()))
    return out


def test_no_new_page_builds_its_own_controls():
    now = raw_calls()
    new = {f: now[f] for f in sorted(set(now) - set(ALLOWED))}
    assert not new, f"build these through pages/ui_kit.py instead: {new}"


def test_allowed_matches_the_code_exactly():
    now = raw_calls()
    drift = {f: {"allowed": ALLOWED[f], "actual": now.get(f, {})}
             for f in ALLOWED if now.get(f, {}) != ALLOWED[f]}
    assert not drift, (
        f"ALLOWED no longer describes the pages: {drift}. A count that FELL: lower "
        "the entry (delete it at zero). A count that ROSE: use pages/ui_kit.py.")


def test_the_kit_really_builds_what_it_guards():
    """Non-vacuity: the guard exempts ui_kit.py because the kit is where these
    calls live. If the kit stopped making them, the exemption would hide a gap."""
    src = (PAGES / KIT).read_text(encoding="utf-8")
    for attr in ("button", "dialog", "notify", "table"):
        assert f"ui.{attr}(" in src, f"ui_kit no longer builds ui.{attr}"
