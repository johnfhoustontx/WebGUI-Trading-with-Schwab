"""Every screen that waits on data must SAY it is waiting.

The requirement was "do this for every screen that has a wait to load data", and
the failure mode is silent: a page that enqueues a command and repaints later
looks finished the whole time it is working — the table simply keeps showing the
previous symbol's results, which is indistinguishable from a result.

So this is a coverage guard rather than a behaviour test. A page that enqueues a
service command must mount SOME wait indicator: the inline spinner
(``pages/busy.py``) for a panel refresh, or the full-screen overlay
(``pages/options/overlay.py``) where loading invalidates the whole page.

⚠ ``kit.region(...)`` counts as the inline one: it BUILDS a ``busy.build_busy``
spinner over the block a repaint replaces (that is the whole reason it exists),
so a page migrated to the kit shows the same wait under a different spelling.
Matching only the two original names would have failed every migrated page for
having no spinner while it had one - the guard has to know the kit's name too.

⚠ But the kit's name alone is NOT proof, where ``build_busy(`` was. A region is
a layout primitive too - the block a repaint replaces - so as the migration
proceeds nearly every page will contain one whether or not it waits on
anything, and a mount-check would decay into a check that the page was
migrated. The kit path therefore requires ``.busy.show(`` as well: mounting a
spinner and never showing it is precisely the silent failure at the top of this
docstring. See ``_shows_a_wait``.
"""
import pathlib
import re

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"

# Modules that enqueue a command on someone else's behalf, or are pure helpers /
# builders with no page of their own — nothing to put a spinner on.
_NOT_SCREENS = {
    "handoff.py",       # stashes + navigates; the DESTINATION page shows the wait
    "settings.py",      # writes prefs through; each control is its own instant ack
    "ticker.py",        # a background marquee, never user-initiated
    "busy.py",          # the helper itself
}

# Screens whose wait is deliberately NOT a spinner, with the reason. Keeping them
# named here (rather than silently excluded) is the point: each is a decision.
_EXEMPT = {
    # Confirm-gated one-shot actions whose result is a navigation or a dialog,
    # not a repaint of the page you are looking at.
    "terminate.py": "the page intentionally goes unresponsive after confirm",
    "manuals.py": "serves a static file in a new tab",
    "eod.py": "Generate writes files and links to them; no in-page repaint",
    # Already has its own spinner + re-entrancy guard on the sweep (predates this).
    "status.py": "the Refresh button owns a spinner of its own",
}


def _page_files():
    for p in sorted(PAGES.rglob("*.py")):
        if p.name.startswith("_") or p.name in _NOT_SCREENS:
            continue
        if "tests" in p.parts or "__pycache__" in p.parts:
            continue
        yield p


def _shows_a_wait(src):
    """Whether a page's source mounts a wait indicator AND uses it.

    The two original spellings are taken as proof on their own (that hole is
    older than the kit). The KIT REGION path needs both halves, because
    ``kit.region`` is a layout primitive as much as a spinner: as the migration
    proceeds nearly every page will hold one whether or not it waits on
    anything, so a mount-check would quietly stop discriminating.
    ``.busy.show(`` is the half that only a page which actually waits has a
    reason to write.

    ⚠ ``kit.set_busy(`` is the THIRD spelling and needs no second half: unlike
    a region, it is not a layout primitive and has no meaning except "hold this
    button and spin it". It is the right wait for a page whose command does not
    replace a block — the Rank Board's Rebuild re-scores the universe behind a
    board that stays readable meanwhile — and counting it is what let that
    page's exemption be DELETED rather than reworded.
    """
    if "build_busy(" in src or "build_loading_overlay(" in src:
        return True
    if "kit.set_busy(" in src:
        return True
    return "kit.region(" in src and ".busy.show(" in src


def test_the_kit_path_needs_the_spinner_to_be_SHOWN():
    """Non-vacuity, pinned rather than checked by hand: a page holding the
    region and never showing it has no wait."""
    assert _shows_a_wait("box = kit.region('…')\nbox.busy.show('Refreshing…')")
    assert not _shows_a_wait("box = kit.region('…')          # never shown")
    assert not _shows_a_wait("ui.column()")
    assert _shows_a_wait("_busy.build_busy(box, '…')")       # the older spelling
    assert _shows_a_wait("kit.set_busy(rebuild)")            # the button's own


def test_every_page_that_enqueues_a_command_shows_a_wait():
    missing = []
    for p in _page_files():
        src = p.read_text(encoding="utf-8")
        if "bus_client.request(" not in src:
            continue
        if p.name in _EXEMPT:
            continue
        if not _shows_a_wait(src):
            missing.append(p.relative_to(PAGES).as_posix())
    assert not missing, (
        "screens that enqueue a command but never show a wait: " + ", ".join(missing))


def test_exemptions_are_real_files_with_a_stated_reason():
    """An exemption list rots into a way of hiding regressions unless the entries
    have to keep existing and keep carrying a reason."""
    names = {p.name for p in PAGES.rglob("*.py")}
    for name, reason in _EXEMPT.items():
        assert name in names, f"stale exemption for a file that no longer exists: {name}"
        assert reason.strip(), name


def test_the_two_wait_styles_stay_distinguishable():
    """Two patterns coexist ON PURPOSE and the choice is per-screen, so neither
    may quietly become the other: inline where one panel refreshes, full-screen
    where loading a symbol invalidates every control on the page."""
    from pages import busy
    from pages.options import overlay

    calc = (PAGES / "options" / "calculator.py").read_text(encoding="utf-8")
    sim = (PAGES / "options" / "simulator.py").read_text(encoding="utf-8")
    for src, who in ((calc, "calculator"), (sim, "simulator")):
        assert "build_loading_overlay(" in src, f"{who} keeps the full-screen wait"
    gamma = (PAGES / "options" / "gamma.py").read_text(encoding="utf-8")
    assert "build_busy(" in gamma and "build_loading_overlay(" not in gamma
    # Same backstop for both, since they back the same fetches.
    assert busy.BUSY_TIMEOUT_SEC == overlay.LOAD_TIMEOUT_SEC
