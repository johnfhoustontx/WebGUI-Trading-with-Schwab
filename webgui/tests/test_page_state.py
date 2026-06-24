"""Pure helpers for per-page UI-state persistence (Calculator + Simulator)."""
from pages.options import page_state as ps


def test_merge_restore_overlays_snapshot_on_defaults():
    defaults = {"symbol": "SPY", "strategy": "PCS", "dt": 5.0}
    # A present key wins; a missing key falls back to the default.
    assert ps.merge_restore({"symbol": "AAPL", "dt": 12.0}, defaults) == {
        "symbol": "AAPL", "strategy": "PCS", "dt": 12.0}


def test_merge_restore_none_or_empty_is_defaults():
    defaults = {"symbol": "SPY", "dt": 5.0}
    assert ps.merge_restore(None, defaults) == defaults
    assert ps.merge_restore({}, defaults) == defaults
    assert ps.merge_restore(None, defaults) is not defaults     # fresh copy


def test_merge_restore_ignores_stale_unknown_keys():
    # A snapshot from an older build with a removed field doesn't leak through.
    assert ps.merge_restore({"gone": 1, "symbol": "QQQ"}, {"symbol": "SPY"}) == {"symbol": "QQQ"}


def test_snapshot_whitelists_keys():
    vals = {"symbol": "SPY", "dt": 5.0, "_widget": object()}
    assert ps.snapshot(vals, ("symbol", "dt")) == {"symbol": "SPY", "dt": 5.0}
    # Missing keys are simply omitted (no KeyError).
    assert ps.snapshot({"symbol": "SPY"}, ("symbol", "dt")) == {"symbol": "SPY"}


def test_pick_seed_precedence():
    assert ps.pick_seed(handoff={"x": 1}, last={"y": 2}) == "handoff"
    assert ps.pick_seed(handoff=None, last={"y": 2}) == "restore"
    assert ps.pick_seed(handoff=None, last=None) == "default"
    assert ps.pick_seed(handoff={}, last={}) == "default"     # empty == absent
