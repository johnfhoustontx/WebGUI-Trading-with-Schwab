"""Regression test for the captured-signals drift display crash.

Pre-fix, `_reload_captured_signals_from_db` formatted `score_drift` with
`f"{drift:+d}"`, which assumes int. But the writer pipeline persists
float values into the INTEGER-affinity column:

  signals.entry_score is declared INTEGER but the recorder writes the
  composite_score float as-is (SQLite doesn't enforce affinity unless
  STRICT mode). signal_recommender.build_mark then computes
  drift = cur_score - entry_score, which is int - float = float, and
  persists that into signal_marks.score_drift (also declared INTEGER,
  also stored as REAL).

When the dashboard reads a legacy mark with a float drift, the format
string raises `ValueError: Unknown format code 'd' for object of type
'float'` and the Captured Signals reload crashes.

Fix: switch the format string from ":+d" to ":+.0f" so it tolerates
both int and float values transparently. Display is identical for ints
(e.g. 4 → "+4") and rounded for floats (e.g. 4.4 → "+4").

This regression test stubs `signal_db.get_open_signals_with_latest_mark`
to return one row with a legacy float drift, runs the reload method,
and asserts it completes without raising. It also pins the displayed
drift to confirm the rounding behaviour.
"""
import os
import sys

import pytest

# Add project root so dashboard import resolves
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tk_or_skip():
    """Return a fresh Tk root, or skip the test if no display."""
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError:
        pytest.skip("No Tk display")


def _captured_tree(root):
    """Build a Treeview matching the captured-signals column shape in
    dashboard.py:_build_captured_signals_tab. Columns must match the
    values= tuple `_reload_captured_signals_from_db` constructs."""
    from tkinter import ttk
    cols = ("sid", "sym", "scanner", "strat", "mode", "strikes", "exp", "dte",
            "credit", "risk", "pnl", "entry_score", "cscore", "drift", "grade",
            "rec", "status", "seen")
    tree = ttk.Treeview(root, columns=cols, show="headings")
    for c in cols:
        tree.heading(c, text=c)
    return tree


def _legacy_row_with_float_drift():
    """Sample row matching what get_open_signals_with_latest_mark returns
    for a legacy signal whose marks were written before any int-coercion
    fix. The float score_drift here is what triggers the original bug."""
    return {
        "signal_id": "abc12345",
        "symbol": "SPY", "scanner_type": "0DTE", "strategy": "PCS",
        "mode": "PREMIUM",
        "short_strike": 500.0, "long_strike": 495.0, "expiration": "2026-06-01",
        "dte_at_entry": 0, "entry_credit": 0.50, "entry_max_loss": 4.50,
        "unrealized_pnl": None,
        "entry_score": 67.7,                # float (legacy)
        "current_score": 65,                # int
        "score_drift": -2.6999999999999,    # float (legacy)
        "first_seen_ts": "2026-05-28T10:00:00",
        "entry_grade": "B",
        "recommendation": "HOLD",
        "status": "OPEN",
    }


def test_reload_handles_float_drift_without_crash(monkeypatch):
    """The captured-signals reload must not raise ValueError when a mark
    row carries a float score_drift (legacy data path)."""
    root = _tk_or_skip()
    try:
        from dashboard import OptionsScannerApp
        import signal_db

        tree = _captured_tree(root)

        # Stub the App with just the attributes _reload_captured_signals_from_db
        # reads on `self`.
        class _Stub:
            pass
        stub = _Stub()
        stub.tree_captured = tree
        stub._auto_close_expired_captured = lambda: None

        monkeypatch.setattr(
            signal_db, "get_open_signals_with_latest_mark",
            lambda *a, **kw: [_legacy_row_with_float_drift()],
        )

        # Must not raise (pre-fix this crashed with ValueError on f"{drift:+d}").
        OptionsScannerApp._reload_captured_signals_from_db(stub)

        children = tree.get_children()
        assert len(children) == 1
    finally:
        root.destroy()


def test_float_drift_renders_with_sign_and_rounding(monkeypatch):
    """Float drift -2.6999... must render as "-3" (rounded toward nearest
    integer, with a sign). Int drift continues to render with sign and
    no decimals."""
    root = _tk_or_skip()
    try:
        from dashboard import OptionsScannerApp
        import signal_db

        tree = _captured_tree(root)

        class _Stub:
            pass
        stub = _Stub()
        stub.tree_captured = tree
        stub._auto_close_expired_captured = lambda: None

        rows = [
            # Legacy float drift, just past -2.5 -> rounds to -3
            {**_legacy_row_with_float_drift(),
             "signal_id": "float001", "score_drift": -2.6999},
            # Int drift -- post-fix writes
            {**_legacy_row_with_float_drift(),
             "signal_id": "int00001", "score_drift": 4},
            # Float zero (exact) -- known edge: f"{0:+.0f}" -> "+0"
            {**_legacy_row_with_float_drift(),
             "signal_id": "zero0001", "score_drift": 0.0},
        ]
        monkeypatch.setattr(
            signal_db, "get_open_signals_with_latest_mark",
            lambda *a, **kw: rows,
        )

        OptionsScannerApp._reload_captured_signals_from_db(stub)

        # Drift column is index 13 in the values tuple (0:sid_short, 1:sym,
        # 2:scanner, 3:strat, 4:mode, 5:strikes, 6:exp, 7:dte, 8:credit,
        # 9:risk, 10:pnl, 11:entry_score, 12:cscore, 13:drift).
        children = tree.get_children()
        drift_displays = [tree.item(c, "values")[13] for c in children]
        # Float -2.6999 -> "-3", int 4 -> "+4", float 0.0 -> "+0"
        assert drift_displays == ["-3", "+4", "+0"]
    finally:
        root.destroy()
