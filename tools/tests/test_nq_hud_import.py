"""Import smoke test for tools/nq_hud.py.

WHY THIS EXISTS: the rest of tools/tests imports only nq_signal and
nq_signal_log, so nq_hud.py was never imported by the suite at all — a syntax
error in it passed 455 green tests and was caught only by ruff. This closes
that gap.

It also pins the lazy-import discipline the module depends on: nq_hud must be
importable with no customtkinter, no tkinter, no gamma_tool, no gex_history_db
and no redis pulled in at module scope, so the window can still open (and
degrade visibly) when an engine import or the bus is unavailable.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


def test_nq_hud_imports_cleanly():
    import tools.nq_hud as hud

    assert callable(hud.read_gamma)
    assert callable(hud.read_gamma_all)
    assert callable(hud.read_tape)
    assert callable(hud.build_pane)
    assert hud.ACTION_COLOR["LONG"] != hud.ACTION_COLOR["SHORT"]


def test_window_is_wide_enough_for_one_column_per_instrument():
    """The panes are packed side by side at a fixed PANE_WIDTH; a window sized
    for one would clip the second instrument entirely off the right edge — the
    same class of defect as the RISK panel that was cut off at "Entry"."""
    import tools.nq_hud as hud
    from tools.nq_instruments import INSTRUMENTS

    assert hud.WIN_WIDTH >= hud.PANE_WIDTH * len(INSTRUMENTS)


def test_nq_hud_pulls_in_no_heavy_dependencies_at_module_scope():
    heavy = {"customtkinter", "tkinter", "gamma_tool", "gex_history_db", "redis"}
    before = set(sys.modules)
    import tools.nq_hud  # noqa: F401
    leaked = {m.split(".")[0] for m in set(sys.modules) - before} & heavy
    assert not leaked, f"nq_hud imported {sorted(leaked)} at module scope"


def test_every_action_build_verdict_can_emit_has_a_colour():
    """A missing entry would paint the verdict in the fallback grey — legible,
    but it would silently lose the LONG/SHORT green-red distinction.
    """
    import tools.nq_hud as hud

    assert set(hud.ACTION_COLOR) == {"LONG", "SHORT", "WAIT", "STAND DOWN"}
