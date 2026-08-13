"""Regression tests for the dashboard's tree → signal iid map.

This pins the invariant added 2026-05-28: every signal row inserted into
a signals Treeview is keyed by its `tree_iid` in `self._tree_signal_maps`,
so click handlers can resolve the selected row to its signal even after
the user clicks a column header to re-sort the tree.

Previously the dashboard mapped row → signal positionally via
`tree.index(sel[0]) -> current_signals_X[idx]`. Sorting the tree
re-ordered rows visually but left `current_signals_X` in insertion
order, silently misrouting click → detail. The iid-keyed map fixes that
by construction (iids travel with rows through `tree.move`).
"""
import os
import sys

import pytest

# Add project root so we can import dashboard
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ``dashboard.py`` (the legacy Tk desktop UI) was deliberately NOT copied into
# this monorepo -- the NiceGUI webgui replaced it -- so the tests below exercise
# code that does not exist here and cannot pass. They are kept rather than
# deleted because they still document the invariant for the source repo, where
# dashboard.py lives.
#
# The skip MUST be module-level. Each test called ``_tk_or_skip()`` first and
# imported ``dashboard`` only inside the test body, so the outcome depended on
# whether an earlier test had already built a Tk root: root fails -> skip, root
# succeeds -> ModuleNotFoundError -> FAIL. That order-dependent flip made the
# suite total swing 15<->17 and, on 2026-08-07, masked two genuine regressions
# behind an unchanged failure COUNT. Skipping at collection is deterministic.
pytest.importorskip("dashboard", reason="legacy Tk UI not present in this monorepo")


def _tk_or_skip():
    """Return a fresh Tk root, or skip the test if no display."""
    import tkinter as tk
    try:
        return tk.Tk()
    except tk.TclError:
        pytest.skip("No Tk display")


def _premium_sig(sid, sym="SPY", side="PCS", strike=500.0, score=70):
    return {
        "id": sid, "symbol": sym, "type": side, "trade_type": "0-DTE",
        "expiration": "2026-06-05", "dte": 0,
        "short_strike": strike, "long_strike": strike - 5, "width": 5.0,
        "credit": 0.50, "max_loss": 4.50,
        "rr_pct": 11.1, "pop_pct": 85.0,
        "composite_score": score, "grade": "B",
        "short_delta": -0.10, "net_theta": 0.05,
        "short_iv": 25.0,
        "mode": "PREMIUM",
    }


def _directional_sig(sid, sym="SPY", side="PCS", strike=495.0, score=55):
    s = _premium_sig(sid, sym=sym, side=side, strike=strike, score=score)
    s["mode"] = "DIRECTIONAL"
    return s


def _make_stub_app():
    """Build a minimal object that has just the attributes `_populate_tree`
    and `_on_signal_select` read on `self`. Lets us call the dashboard
    methods without instantiating the full UI.
    """
    class _Stub:
        pass
    stub = _Stub()
    stub._prev_signal_ids = set()
    stub._tree_signal_maps = {}
    return stub


def _build_signal_tree(root):
    """Construct a Treeview shaped like dashboard.py's signal trees."""
    from tkinter import ttk
    cols = ("score","grade","sym","type","strikes","exp","dte","credit",
            "maxloss","rr","pop","delta","theta","iv")
    tree = ttk.Treeview(root, columns=cols, show="headings", selectmode="browse")
    for c in cols:
        tree.heading(c, text=c)
    return tree


def test_populate_tree_keys_iid_map_by_iid():
    """Every signal row inserted by _populate_tree must appear in
    self._tree_signal_maps[id(tree)] keyed by its tree iid."""
    root = _tk_or_skip()
    try:
        from dashboard import OptionsScannerApp
        tree = _build_signal_tree(root)
        stub = _make_stub_app()

        sigs = [
            _premium_sig("A", strike=500.0, score=80),
            _premium_sig("B", strike=495.0, score=70),
            _directional_sig("C", strike=499.0, score=55),
            _directional_sig("D", strike=501.0, side="CCS", strike_=502.0)
            if False else _directional_sig("D", strike=501.0, side="CCS"),
        ]

        OptionsScannerApp._populate_tree(stub, tree, sigs)

        iid_map = stub._tree_signal_maps[id(tree)]
        # Four signal rows registered (separator is NOT in the map).
        assert len(iid_map) == 4
        # Each signal's iid resolves back to the same signal object.
        for iid, sig in iid_map.items():
            assert sig in sigs
        # Every signal in the input ended up exactly once in the map.
        assert sorted(s["id"] for s in iid_map.values()) == ["A", "B", "C", "D"]
    finally:
        root.destroy()


def test_separator_iid_is_not_in_map():
    """The "— Directional —" separator row gets inserted but is deliberately
    omitted from the iid map, so a click on it resolves to None and produces
    no detail panel."""
    root = _tk_or_skip()
    try:
        from dashboard import OptionsScannerApp
        tree = _build_signal_tree(root)
        stub = _make_stub_app()

        sigs = [
            _premium_sig("A"),
            _directional_sig("B"),
        ]
        OptionsScannerApp._populate_tree(stub, tree, sigs)

        # Total rows in tree = 2 signals + 1 separator = 3
        all_iids = tree.get_children()
        assert len(all_iids) == 3
        # Only 2 are in the iid map (the separator is not)
        iid_map = stub._tree_signal_maps[id(tree)]
        assert len(iid_map) == 2
        # The one missing iid is the separator
        unmapped = set(all_iids) - set(iid_map.keys())
        assert len(unmapped) == 1
        sep_iid = next(iter(unmapped))
        # Confirm the unmapped row is indeed the separator (has the tag)
        assert "directional_sep" in tree.item(sep_iid, "tags")
    finally:
        root.destroy()


def test_iid_lookup_survives_sort():
    """The load-bearing assertion: after the user sorts a column, the
    iid → signal mapping must still produce the right signal for any
    given tree row iid."""
    root = _tk_or_skip()
    try:
        from dashboard import OptionsScannerApp
        tree = _build_signal_tree(root)
        stub = _make_stub_app()

        sigs = [
            _premium_sig("A", strike=500.0, score=80),
            _premium_sig("B", strike=495.0, score=70),
            _premium_sig("C", strike=490.0, score=60),
        ]
        OptionsScannerApp._populate_tree(stub, tree, sigs)

        # Snapshot the iid → signal id before sort
        iid_map = stub._tree_signal_maps[id(tree)]
        before = {iid: sig["id"] for iid, sig in iid_map.items()}

        # Reorder rows via _sort_tree (descending by score).
        OptionsScannerApp._sort_tree(stub, tree, "score")

        # The iid map itself is unchanged (it's keyed by iid, not position).
        after = {iid: sig["id"] for iid, sig in iid_map.items()}
        assert before == after

        # Spot check: picking any row's iid still resolves to the same signal.
        for iid in tree.get_children():
            assert iid_map[iid]["id"] == before[iid]
    finally:
        root.destroy()


def test_empty_signals_clears_map():
    """A populate call with an empty signals list resets the per-tree map
    to empty so stale entries from a previous scan don't linger."""
    root = _tk_or_skip()
    try:
        from dashboard import OptionsScannerApp
        tree = _build_signal_tree(root)
        stub = _make_stub_app()

        # First populate with some signals
        OptionsScannerApp._populate_tree(stub, tree, [_premium_sig("A")])
        assert len(stub._tree_signal_maps[id(tree)]) == 1

        # Second populate with no signals: map should be empty
        OptionsScannerApp._populate_tree(stub, tree, [])
        assert stub._tree_signal_maps[id(tree)] == {}
    finally:
        root.destroy()
