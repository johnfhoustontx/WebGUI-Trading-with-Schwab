"""Guard: pages build buttons, dialogs, toasts and tables through pages/ui_kit.py.

The consistency standard (docs/plans/2026-09-19-app-ui-consistency-design.md)
holds only if a page cannot quietly build its own button, dialog, toast or
table, or load a web font of its own. ALLOWED is the ratchet: every page that
still does, by file and count. A migrated page DELETES its entry; a count may
only fall; and the list must match the code EXACTLY, so a page that drops a raw
call lowers its entry in the same commit - otherwise the list stops describing
the code. An entry may outlive the migration only with a written reason (a
control that is not an action button: a segmented picker, a leg-table toggle).
"""
import ast
import collections
import pathlib

GUARDED = ("button", "dialog", "notify", "table", "add_head_html")
PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"
KIT = "ui_kit.py"

ALLOWED = {
    "config_editor.py": {"button": 12, "dialog": 2, "notify": 10},
    "desk.py": {"add_head_html": 3, "button": 1},
    "driver.py": {"button": 8, "dialog": 2, "notify": 1, "table": 5},
    "eod.py": {"button": 3, "notify": 2},
    "manuals.py": {"button": 1},
    "market.py": {"add_head_html": 1, "button": 2},
    "options/calculator.py": {"add_head_html": 1, "button": 3, "dialog": 1, "notify": 3},
    # The panel's collapse toggle, and it stays: it carries a floating flag
    # badge and a tooltip retitled on every toggle, which kit.icon_button does
    # not model. Not an action button - the panel's ACTIONS are the page's, and
    # they go through the kit into handle.actions.
    "options/detail.py": {"button": 1},
    "options/entry_panel.py": {"button": 3},
    "options/expected_move.py": {"button": 1, "notify": 1},
    "options/gamma.py": {"button": 8, "notify": 7},
    "options/leg_editor.py": {"button": 10},
    "options/simulator.py": {"button": 1, "notify": 1},
    "options/strategy_menu.py": {"button": 2},
    "options/swing.py": {"button": 7, "table": 1},
    "portfolio.py": {"button": 1, "table": 3},
    "sentiment.py": {"add_head_html": 1, "button": 3, "notify": 1},
    "sentiment_bullbear.py": {"add_head_html": 1, "button": 1, "notify": 2},
    "sentiment_momentum.py": {"add_head_html": 1, "button": 3, "notify": 1, "table": 1},
    "sentiment_sectors.py": {"add_head_html": 1, "button": 3, "notify": 1},
    "settings.py": {"button": 7, "dialog": 1, "notify": 1},
    "status.py": {"button": 3, "notify": 4},
    "symbol.py": {"add_head_html": 1, "button": 2},
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
