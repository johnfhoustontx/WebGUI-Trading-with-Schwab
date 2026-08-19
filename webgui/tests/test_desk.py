"""Pure display logic for the Desk (/desk).

Every builder here takes plain dicts and returns plain dicts, so the whole
screen's arithmetic is testable without a browser — the same shape
``pages/market.py`` proved out.
"""
import pathlib

from pages import desk as d


# ── Tailwind-first guard (house standard; the shared guard file is not ours to
# edit right now, so the Desk carries its own copy of the same assertion) ─────
def test_desk_module_has_no_inline_style():
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "desk.py"
           ).read_text(encoding="utf-8")
    assert ".style(" not in src, "desk.py still uses .style()"
    assert ":style=" not in src, "desk.py still uses a Vue :style= slot binding"


# ── structure_positions ──────────────────────────────────────────────────────
def test_structure_positions_places_spot_between_the_walls():
    p = d.structure_positions(spot=100.0, flip=99.0, put_wall=95.0, call_wall=105.0)
    assert 0.0 <= p["put_wall"] < p["spot"] < p["call_wall"] <= 100.0
    assert p["spot"] == 50.0


def test_structure_positions_is_none_without_both_walls():
    assert d.structure_positions(100.0, 99.0, None, 105.0) is None
    assert d.structure_positions(100.0, 99.0, 95.0, None) is None
    assert d.structure_positions(None, 99.0, 95.0, 105.0) is None


def test_structure_positions_clamps_spot_outside_the_walls():
    p = d.structure_positions(spot=110.0, flip=99.0, put_wall=95.0, call_wall=105.0)
    assert p["spot"] == 100.0


def test_structure_positions_survives_a_degenerate_span():
    assert d.structure_positions(100.0, 100.0, 100.0, 100.0) is None


def test_structure_positions_omits_flip_when_absent_but_keeps_the_bar():
    p = d.structure_positions(100.0, None, 95.0, 105.0)
    assert p is not None and p["flip"] is None


def test_structure_positions_refuses_non_finite_inputs_rather_than_pinning_a_wall():
    """A NaN must NOT render as a position — the documented app-wide trap.

    ``min(100.0, max(0.0, nan))`` is **0.0** (every comparison against NaN is
    False, so ``max`` keeps its running value), which would draw an absent spot
    sitting exactly ON the put wall — the single most alarming thing the bar can
    say. ``+inf`` clamps the other way, onto the call wall. Both are "no
    reading" dressed as an extreme one, so the bar is withheld instead.
    """
    nan, inf = float("nan"), float("inf")
    assert d.structure_positions(nan, 99.0, 95.0, 105.0) is None
    assert d.structure_positions(inf, 99.0, 95.0, 105.0) is None
    assert d.structure_positions(100.0, 99.0, nan, 105.0) is None
    assert d.structure_positions(100.0, 99.0, 95.0, nan) is None
    # A non-finite FLIP only costs the flip tick; the bar itself still stands.
    p = d.structure_positions(100.0, nan, 95.0, 105.0)
    assert p is not None and p["flip"] is None


def test_structure_positions_never_raises_on_junk():
    assert d.structure_positions("x", None, "y", "z") is None
    assert d.structure_positions({}, [], object(), 105.0) is None
