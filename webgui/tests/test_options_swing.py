"""Tests for the Swing Scanner helpers (results reuse the scanner transforms)."""
from pages.options import swing


def test_render_callable():
    assert callable(swing.render)


def test_pct_to_fraction():
    # screen_spreads expects a fraction (0.10), not a percent (10) — regression guard.
    assert swing.pct_to_fraction(10) == 0.10
    assert swing.pct_to_fraction(0) == 0.0


def test_assign_ids_adds_unique_ids():
    out = swing.assign_ids([{"symbol": "MU"}, {"symbol": "MU"}], "MU")
    ids = [s["id"] for s in out]
    assert len(set(ids)) == 2
    assert all(i.startswith("MU") for i in ids)


def test_assign_ids_preserves_existing():
    assert swing.assign_ids([{"symbol": "MU", "id": "keep"}], "MU")[0]["id"] == "keep"
