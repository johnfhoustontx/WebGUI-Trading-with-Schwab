"""The volatility gate — gap assessment B2.

Design: docs/plans/2026-09-12-volatility-gate-design.md.

The predicate is pure arithmetic over two readings and two bounds, so every rule
that matters is testable here without a chain, a proxy or a database. What it has
to get right is not the comparison — it is the three absence cases, each of which
is a documented bug class in this repo:

* a missing reading must SKIP the gate, never pin a bound (the NaN trap);
* a vega of exactly zero must not be read as long premium;
* a bound of 0 must mean OFF, because an IV rank of 0.0 is a real reading.
"""
import math

import pytest

from shared import vol_gate


# ── premium_side ─────────────────────────────────────────────────────────────

def test_negative_vega_is_short_premium():
    assert vol_gate.premium_side(-0.42) == vol_gate.SHORT_PREMIUM


def test_positive_vega_is_long_premium():
    assert vol_gate.premium_side(0.42) == vol_gate.LONG_PREMIUM


def test_zero_vega_is_neither_side():
    """A vega-neutral structure must not be handed the ceiling by a rounding sign."""
    assert vol_gate.premium_side(0.0) is None
    assert vol_gate.premium_side(-0.0) is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "0.4", True, [], {}])
def test_unreadable_vega_is_neither_side(bad):
    assert vol_gate.premium_side(bad) is None


# ── blocks: the floor ────────────────────────────────────────────────────────

def test_short_premium_below_the_floor_is_blocked():
    assert vol_gate.blocks(20.0, -0.5, floor=30) == vol_gate.IV_TOO_LOW


def test_short_premium_exactly_at_the_floor_passes():
    """The floor is a minimum, not an exclusive bound - mirrors MIN_IV_RANK's >=."""
    assert vol_gate.blocks(30.0, -0.5, floor=30) is None


def test_short_premium_above_the_floor_passes():
    assert vol_gate.blocks(85.0, -0.5, floor=30) is None


def test_the_floor_never_touches_long_premium():
    """Cheap volatility is when buying premium is RIGHT - the floor must not cut it."""
    assert vol_gate.blocks(0.1, +0.5, floor=45) is None


# ── blocks: the ceiling ──────────────────────────────────────────────────────

def test_long_premium_above_the_ceiling_is_blocked():
    assert vol_gate.blocks(80.0, +0.5, ceiling=65) == vol_gate.IV_TOO_HIGH


def test_long_premium_exactly_at_the_ceiling_passes():
    assert vol_gate.blocks(65.0, +0.5, ceiling=65) is None


def test_the_ceiling_never_touches_short_premium():
    assert vol_gate.blocks(100.0, -0.5, ceiling=65) is None


def test_both_bounds_can_be_active_at_once():
    assert vol_gate.blocks(10.0, -0.5, floor=45, ceiling=65) == vol_gate.IV_TOO_LOW
    assert vol_gate.blocks(90.0, +0.5, floor=45, ceiling=65) == vol_gate.IV_TOO_HIGH
    assert vol_gate.blocks(55.0, -0.5, floor=45, ceiling=65) is None
    assert vol_gate.blocks(55.0, +0.5, floor=45, ceiling=65) is None


# ── the three absence rules ──────────────────────────────────────────────────

def test_no_bound_at_all_blocks_nothing():
    assert vol_gate.blocks(0.0, -0.5) is None
    assert vol_gate.blocks(100.0, +0.5) is None


@pytest.mark.parametrize("off", [0, 0.0, None])
def test_a_bound_of_zero_means_off_not_a_floor_at_zero(off):
    """An IV rank of 0.0 is a REAL reading (IREN on the live income board), so a
    floor of 0 must not be the thing that refuses it."""
    assert vol_gate.blocks(0.0, -0.5, floor=off) is None
    assert vol_gate.blocks(0.0, +0.5, ceiling=off) is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_an_unreadable_iv_rank_skips_the_gate(bad):
    """``calc_iv_rank_percentile`` returns None by design when HV history is too
    short. A fraction of an unknown cannot be enforced, and a data outage must
    not read as a refusal."""
    assert vol_gate.blocks(bad, -0.5, floor=45) is None
    assert vol_gate.blocks(bad, +0.5, ceiling=65) is None


def test_an_unreadable_vega_skips_the_gate():
    assert vol_gate.blocks(0.0, None, floor=45) is None
    assert vol_gate.blocks(100.0, float("nan"), ceiling=65) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "45"])
def test_an_unreadable_BOUND_is_treated_as_absent(bad):
    """A NaN bound would make every comparison False - the pins-the-bound trap
    one layer up, so it is refused at the bound rather than trusted."""
    assert vol_gate.blocks(0.0, -0.5, floor=bad) is None
    assert vol_gate.blocks(100.0, +0.5, ceiling=bad) is None


# ── the signal-level convenience ─────────────────────────────────────────────

def test_signal_blocks_reads_the_candidates_own_fields():
    sig = {"iv_rank": 12.0, "net_vega": -0.31, "type": "PCS"}
    assert vol_gate.signal_blocks(sig, floor=30) == vol_gate.IV_TOO_LOW


def test_signal_blocks_falls_back_to_an_explicit_iv_rank():
    """The Market Scanner stamps iv_rank onto its rows only AFTER the filters run,
    so the caller must be able to supply the symbol's reading."""
    sig = {"net_vega": -0.31}
    assert vol_gate.signal_blocks(sig, floor=30, iv_rank=12.0) == vol_gate.IV_TOO_LOW
    assert vol_gate.signal_blocks(sig, floor=30, iv_rank=80.0) is None


def test_signal_blocks_prefers_the_explicit_reading_over_a_stale_row_value():
    sig = {"iv_rank": 99.0, "net_vega": -0.31}
    assert vol_gate.signal_blocks(sig, floor=30, iv_rank=12.0) == vol_gate.IV_TOO_LOW


def test_signal_blocks_on_a_row_with_neither_field_passes():
    assert vol_gate.signal_blocks({}, floor=45, ceiling=65) is None


# ── config wiring ────────────────────────────────────────────────────────────

def test_min_iv_rank_carries_an_INCOME_key():
    """B2: income_scan passes trade_type="INCOME", and a keyed lookup that has no
    such key returns 0 - a floor that reads like protection and is not one."""
    from shared import scanner_config
    scanner_config.reset_cache()
    assert "INCOME" in scanner_config.min_iv_rank()


def test_max_iv_rank_exists_and_ships_disabled():
    """The long-premium ceiling: mechanism shipped, gate off, because this app has
    no long-premium outcome data at all to set it from."""
    from shared import scanner_config
    scanner_config.reset_cache()
    ceil = scanner_config.max_iv_rank()
    assert set(ceil) == set(scanner_config.min_iv_rank())
    assert all(not v for v in ceil.values()), ceil


def test_the_two_accessors_cover_the_same_trade_types():
    """A floor keyed on a type the ceiling does not carry (or the reverse) is how
    one surface silently loses half the gate."""
    from shared import scanner_config
    scanner_config.reset_cache()
    assert set(scanner_config.min_iv_rank()) == set(scanner_config.max_iv_rank())


def test_a_toml_only_key_is_silently_dropped_so_DEFAULTS_must_carry_it(tmp_path,
                                                                      monkeypatch):
    """Pins the trap the design names: ``min_iv_rank`` is closed over
    ``DEFAULTS["iv_rank"]``, so adding a trade type to the TOML alone does
    nothing. The test is here so the next person adding a type learns it from a
    failure rather than from a scan that never gated.
    """
    from shared import scanner_config
    toml = tmp_path / "scanner.toml"
    toml.write_text('[iv_rank]\n"0-DTE" = 35\nSWING = 30\nINCOME = 30\nNOPE = 99\n',
                    encoding="utf-8")
    load, reset = __import__("shared.config_toml", fromlist=["toml_loader"]).toml_loader(
        toml, scanner_config.DEFAULTS, label="test")
    monkeypatch.setattr(scanner_config, "load", load)
    scanner_config.reset_cache()
    got = scanner_config.min_iv_rank()
    assert "NOPE" not in got
    assert got["INCOME"] == 30


def test_config_floors_and_the_shipped_toml_agree():
    """The TOML is the operator's surface; a value here that the defaults override
    would mean editing the file changes nothing."""
    import tomllib

    from repo_paths import SCANNER_TOML
    from shared import scanner_config
    scanner_config.reset_cache()
    raw = tomllib.loads(SCANNER_TOML.read_text(encoding="utf-8"))
    for key, val in (raw.get("iv_rank") or {}).items():
        assert scanner_config.min_iv_rank().get(key) == val, key
    for key, val in (raw.get("iv_rank_ceiling") or {}).items():
        assert scanner_config.max_iv_rank().get(key) == val, key


def test_no_bound_is_a_non_finite_number_in_the_shipped_config():
    from shared import scanner_config
    scanner_config.reset_cache()
    for name, table in (("min", scanner_config.min_iv_rank()),
                        ("max", scanner_config.max_iv_rank())):
        for k, v in table.items():
            assert isinstance(v, (int, float)) and not isinstance(v, bool), (name, k)
            assert math.isfinite(v), (name, k)
