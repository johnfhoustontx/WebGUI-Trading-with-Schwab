"""The page-to-shell seam, and the reason it is a module of its own.

``pages/options/gamma.py`` used to do ``import main``. In a SECOND NiceGUI
process that executes main.py's module body, and that body registers every
``@_page`` route -- so a public live process importing it would serve
``/terminate`` (Stop All Services) and ``/settings`` to the internet, silently,
while looking entirely correct.

These tests pin the seam as a leaf module both entrypoints can provide.
"""
import ast
import pathlib

import shell

_PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def _imports_main(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "main" for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module == "main":
            return True
    return False


def test_no_page_imports_main():
    """THE GUARD THAT KEEPS /terminate OFF THE PUBLIC ORIGIN.

    A page that imports ``main`` cannot be rendered by any entrypoint other than
    main.py without dragging the whole route table along. Every page reaches the
    shell through ``shell.py`` instead."""
    offenders = [p.relative_to(_PAGES).as_posix()
                 for p in _PAGES.rglob("*.py") if _imports_main(p)]
    assert offenders == [], (
        f"these pages import main and so cannot be served by the live "
        f"process: {offenders}")


def test_the_seam_exposes_what_the_pages_actually_call():
    import shell
    for name in ("subtab_slot", "bind_breadcrumb_leaf",
                 "set_breadcrumb_leaf", "_view_name", "play_alert",
                 "publish", "unpublish", "is_public", "may_enqueue"):
        assert hasattr(shell, name), f"shell.py is missing {name}"


def test_main_still_exposes_the_seam_for_backwards_compatibility():
    """main.py re-exports the seam so anything still reaching for
    ``main.set_breadcrumb_leaf`` keeps working."""
    import main
    import shell
    assert main.subtab_slot is shell.subtab_slot
    assert main.set_breadcrumb_leaf is shell.set_breadcrumb_leaf
    assert main.play_alert is shell.play_alert


def test_the_layout_mutates_the_seam_state_it_no_longer_owns():
    """``_layout`` fills ``_SUBTAB_SLOT``/``_breadcrumb_leaf`` in place.

    The re-export binds the SAME dict objects, so a page calling
    ``shell.subtab_slot()`` sees what main's ``_layout`` just wrote. Rebinding
    either name in main (``_SUBTAB_SLOT = {...}``) would break that silently --
    no test would fail, the subtab row would just stop mounting."""
    import main
    import shell
    assert main._SUBTAB_SLOT is shell._SUBTAB_SLOT
    assert main._breadcrumb_leaf is shell._breadcrumb_leaf


# --- the page-level CSS both entrypoints inject -----------------------------

def test_the_shared_page_css_lives_in_the_shell():
    """``main._TABLE_CSS`` styled a widget the PAGE mounts, from a module the
    public entrypoint may not import — so /opportunity and /flow published
    without sticky headers, and /net-premium's group picker without its pill
    shape. Same seam, same reason as ``play_alert``."""
    import shell
    assert ".q-table thead tr th" in shell.TABLE_CSS
    assert ".q-table__middle { max-height: 65vh; }" in shell.TABLE_CSS
    assert ".compact-subtabs .q-tab" in shell.SUBTAB_CSS


def test_main_re_exports_the_table_css_under_its_old_name():
    """``main._TABLE_CSS`` has been reachable since 2026-06; the move keeps it,
    bound to the very same string."""
    import main
    import shell
    assert main._TABLE_CSS is shell.TABLE_CSS
    assert main.SUBTAB_CSS is shell.SUBTAB_CSS


def test_the_private_layout_still_injects_both_shared_blocks():
    """main.py must behave IDENTICALLY. Read off ``_layout``'s own source, so a
    constant that is merely imported and never injected fails."""
    import ast
    import inspect
    import main
    tree = ast.parse(inspect.getsource(main._layout).lstrip())
    injected = [n.args[0].id
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_css" and n.args
                and isinstance(n.args[0], ast.Name)]
    assert "TABLE_CSS" in injected, "the private app lost its sticky table headers"
    assert "SUBTAB_CSS" in injected, "the private app lost its subtab row styling"
    assert "_NAV_CSS" in injected, "the nav chrome must stay with main"


def _rules_only(css: str) -> str:
    """``css`` with its ``/* ... */`` comments removed.

    ``_NAV_CSS`` keeps a comment SAYING where the subtab rules went, which is
    the note a reader wants and is not a rule. The tests below are about what
    the browser is served."""
    import re
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_the_moved_rules_are_gone_from_the_nav_block():
    """Non-vacuity for the test above: a copy left behind in ``_NAV_CSS`` would
    make every assertion here pass while the duplication silently drifted."""
    import main
    nav = _rules_only(main._NAV_CSS)
    assert ".q-table" not in nav
    assert ".compact-subtabs" not in nav


def test_the_nav_only_css_did_not_follow_the_pages_out():
    """What was deliberately LEFT, and why: the rail, the top tab strip, the
    page-help tooltips, the market-status pill, the brand lockup and the header
    padding all style chrome the public process never mounts. Shipping rules for
    elements that do not exist is how a "shared" module becomes main.py again."""
    import main
    import shell
    shared = shell.TABLE_CSS + shell.SUBTAB_CSS
    nav = _rules_only(main._NAV_CSS)
    for selector in (".nav-drawer", ".compact-tabs", ".flush-panels",
                     ".q-tooltip.help-tip", ".mkt-pill", ".brand-mark",
                     ".q-header"):
        assert selector in nav, f"{selector} vanished from _NAV_CSS"
        assert selector not in shared, f"{selector} is nav chrome; it must stay in main"


def test_the_shell_stays_a_leaf_module():
    """``shell.py`` is imported by every page AND by the public entrypoint, so
    what it imports is what they all pay. The CSS moved as plain strings; if it
    ever needs ``theme`` or a page module, that is a decision to take
    deliberately, not to discover."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "shell.py"
    roots = set()
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        if isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots == {"nicegui", "pages"}, f"shell.py grew imports: {sorted(roots)}"


# --- the screenshot session's chrome suppression -----------------------------
def test_the_capture_css_needs_the_exact_cookie_value():
    """Presence is not the contract. A stray or empty ns_capture must not
    suppress a real visitor's disconnect warning -- that banner is the only
    thing telling the owner their trading UI stopped updating."""
    assert shell.capture_chrome_css({shell.CAPTURE_COOKIE: "1"})
    for cookies in (None, {}, {shell.CAPTURE_COOKIE: ""},
                    {shell.CAPTURE_COOKIE: "0"},
                    {shell.CAPTURE_COOKIE: "true"},
                    {"ns_session": "1"}):
        assert shell.capture_chrome_css(cookies) is None, cookies


def test_the_capture_css_hides_the_reconnect_banner_and_nothing_else():
    """⚠ The blast radius IS the point. This CSS is injected into the real
    trading app, so a rule that reached further would hide live content from a
    screenshot and nobody would know which tile was lying."""
    css = shell.capture_chrome_css({shell.CAPTURE_COOKIE: "1"})
    selectors = [ln.split("{")[0].strip()
                 for ln in css.strip().splitlines() if "{" in ln]
    assert selectors == ["#popup.nicegui-error-popup"], selectors


def test_the_public_origin_never_suppresses_its_own_chrome():
    """⚠ On live.neuralstrike.co a VISITOR can set any cookie they like, so a
    cookie-gated suppression there would let anyone hide their own disconnect
    warning on a screen whose whole value is being live. The capture only ever
    drives the private app on loopback, so live_main must never import this."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "live_main.py").read_text(encoding="utf-8")
    assert "capture_chrome_css" not in src
    assert shell.CAPTURE_COOKIE not in src
