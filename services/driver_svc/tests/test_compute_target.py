"""Tests for the cumulative MTD banking target helpers in ``driver_svc.compute``.

``effective_target`` carries the $500/day deficit/excess forward month-to-date, clamped
to [floor, cap]; ``mtd_realized_before_today`` sums the driver book's realized P&L this
month excluding today; ``_mtd_trading_days`` counts weekdays−holidays MTD. All pure.
"""
import datetime as dt

from services.driver_svc import compute


# ── effective_target ─────────────────────────────────────────────────────────
def test_effective_target_on_pace_is_base():
    # day 1, nothing banked yet -> base 500
    assert compute.effective_target(500, 1, 0, cap=1000, floor=250) == 500


def test_effective_target_behind_ratchets_to_cap():
    # day 5, only $1000 banked of the $2500 pace -> need 1500 today, capped 1000
    assert compute.effective_target(500, 5, 1000, cap=1000, floor=250) == 1000


def test_effective_target_ahead_eases_to_floor():
    # day 5, $3000 banked (ahead of $2500 pace) -> raw negative -> floored 250
    assert compute.effective_target(500, 5, 3000, cap=1000, floor=250) == 250


def test_effective_target_mid_range():
    # day 3, $700 banked of $1500 pace -> need 800 today (within band)
    assert compute.effective_target(500, 3, 700, cap=1000, floor=250) == 800


def test_effective_target_defensive_on_junk():
    assert compute.effective_target(500, None, None, cap=1000, floor=250) == 500
    assert compute.effective_target(500, "x", 0, cap=1000, floor=250) == 500
