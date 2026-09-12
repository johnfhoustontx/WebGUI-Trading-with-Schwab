"""The paper engine's SIXTH rung: a sector cap — gap assessment B4.

Design: docs/plans/2026-09-12-sector-cap-design.md.

The five rungs before it were per trade, per symbol, per expiry and per account,
so a book could hold four different semiconductors at the full symbol cap and
clear every one. Measured on the real books before building this:

* the **manual** book peaked at **$3,569 across 19 simultaneous Information
  Technology positions**, and at **$20,312 across 107** in Industrials (SPCX
  stacking, which the per-symbol cap now prevents on its own);
* the **driver** book peaked at **$21,531 across 15 IT positions** — 86% of a
  $25,000 account in one sector — and **$15,018 across 9 INDEX** positions;
* a $1,500 sector cap would have bound on **20 of 46** trading days in the manual
  book and **35 of 40** in the driver's.

``sector_of`` is INJECTED rather than reached for, so every test here states its
own map and none of them depends on what ``config/sectors.toml`` happens to say
today.
"""
import pytest

import paper_concentration as pc


def _pos(symbol, risk=250.0, expiration="2026-10-16"):
    return {"symbol": symbol, "max_loss_total": risk, "expiration": expiration,
            "status": "OPEN"}


_MAP = {"MU": "Information Technology", "INTC": "Information Technology",
        "AMAT": "Information Technology", "AMD": "Information Technology",
        "XOM": "Energy", "CVX": "Energy", "PG": "Consumer Staples",
        "SPY": "INDEX", "QQQ": "INDEX"}


def _sector_of(symbol):
    return _MAP.get((symbol or "").strip().upper())


def _limits(**over):
    """Loose everywhere except what a test is about, so one cap is under test at
    a time — otherwise a fixture breaches two and the assertion cannot say which."""
    base = {"max_positions_per_symbol": 99, "max_risk_per_symbol": 1e9,
            "max_positions_per_expiry": 99, "max_positions_per_sector": 99,
            "max_risk_per_sector": 1e9}
    base.update(over)
    return base


def _reject(positions, symbol, risk=250.0, expiration="2026-10-16", **over):
    return pc.concentration_reject(positions, symbol, expiration, risk,
                                   limits=_limits(**over), sector_of=_sector_of)


# ── the count cap ────────────────────────────────────────────────────────────

def test_a_fifth_position_in_one_sector_is_refused():
    book = [_pos("MU"), _pos("INTC"), _pos("AMAT"), _pos("AMD")]
    assert _reject(book, "MU", max_positions_per_sector=4) == pc.SECTOR_POSITION_CAP


def test_room_in_the_sector_is_allowed():
    book = [_pos("MU"), _pos("INTC")]
    assert _reject(book, "AMAT", max_positions_per_sector=4) is None


def test_positions_in_OTHER_sectors_do_not_count_toward_the_cap():
    """The whole point: this is a correlation cap, not a book-size cap - the
    deployment rung already does book size."""
    book = [_pos("XOM"), _pos("CVX"), _pos("PG"), _pos("SPY")]
    assert _reject(book, "MU", max_positions_per_sector=2) is None


def test_the_count_spans_SYMBOLS_within_the_sector():
    """Four different semiconductors at one position each breach nothing
    per-symbol, which is exactly the hole this rung closes."""
    book = [_pos("MU"), _pos("INTC"), _pos("AMAT")]
    assert _reject(book, "AMD", max_positions_per_sector=3) == pc.SECTOR_POSITION_CAP


# ── the risk cap ─────────────────────────────────────────────────────────────

def test_summed_sector_risk_over_the_cap_is_refused():
    book = [_pos("MU", 700.0), _pos("INTC", 700.0)]
    assert _reject(book, "AMAT", risk=250.0,
                   max_risk_per_sector=1500.0) == pc.SECTOR_RISK_CAP


def test_summed_sector_risk_exactly_at_the_cap_is_allowed():
    """``>`` not ``>=``, matching the symbol risk cap beside it: a trade that
    lands exactly on the ceiling has not breached it."""
    book = [_pos("MU", 700.0), _pos("INTC", 550.0)]
    assert _reject(book, "AMAT", risk=250.0, max_risk_per_sector=1500.0) is None


def test_the_candidates_own_risk_counts_toward_the_sector_cap():
    book = [_pos("MU", 700.0)]
    assert _reject(book, "INTC", risk=900.0,
                   max_risk_per_sector=1500.0) == pc.SECTOR_RISK_CAP


def test_a_non_finite_row_is_dropped_rather_than_poisoning_the_sector_sum():
    """The documented pins-the-bound trap: a NaN total makes every ``>`` False and
    switches the ceiling off. Summed through ``driver_policy.open_risk_dollars``
    for exactly this reason, like the symbol sum above it."""
    book = [_pos("MU", float("nan")), _pos("INTC", 1400.0)]
    assert _reject(book, "AMAT", risk=250.0,
                   max_risk_per_sector=1500.0) == pc.SECTOR_RISK_CAP


# ── indices are a bucket, not an exemption ───────────────────────────────────

def test_index_names_are_capped_TOGETHER():
    """Measured at 0.799 mean pairwise correlation - the second-tightest group in
    the universe. Nine index positions is one market bet; the driver held nine."""
    book = [_pos("SPY"), _pos("SPY"), _pos("QQQ")]
    assert _reject(book, "QQQ", max_positions_per_sector=3) == pc.SECTOR_POSITION_CAP


# ── an unmapped symbol: its own bucket, never pooled, never borrowed ─────────

def test_an_unmapped_symbol_does_not_join_another_sectors_bucket():
    book = [_pos("MU"), _pos("INTC"), _pos("AMAT"), _pos("AMD")]
    assert _reject(book, "ZZQQ", max_positions_per_sector=4) is None


def test_two_DIFFERENT_unmapped_symbols_are_not_capped_against_each_other():
    """Pooling every unknown into one bucket would cap XOM against PG on the
    strength of not knowing either."""
    book = [_pos("ZZQQ"), _pos("ZZQQ"), _pos("WWPP")]
    assert _reject(book, "WWPP", max_positions_per_sector=3) is None


def test_an_unmapped_symbol_IS_still_capped_against_ITSELF():
    """So the rung is never a way to escape a cap - just a narrower bucket."""
    book = [_pos("ZZQQ"), _pos("ZZQQ"), _pos("ZZQQ")]
    assert _reject(book, "ZZQQ",
                   max_positions_per_sector=3) == pc.SECTOR_POSITION_CAP


# ── absence and back-compatibility ───────────────────────────────────────────

def test_limits_without_the_sector_keys_keep_the_pre_B4_behaviour():
    """Opt-in by DATA, like the deployment cap: every existing caller that passes
    its own ``limits`` dict is untouched."""
    book = [_pos("MU"), _pos("INTC"), _pos("AMAT"), _pos("AMD"), _pos("MU")]
    got = pc.concentration_reject(
        book, "MU", "2026-10-16", 250.0,
        limits={"max_positions_per_symbol": 99, "max_risk_per_symbol": 1e9,
                "max_positions_per_expiry": 99},
        sector_of=_sector_of)
    assert got is None


@pytest.mark.parametrize("off", [0, None])
def test_a_cap_of_zero_or_absent_is_OFF(off):
    book = [_pos("MU"), _pos("INTC"), _pos("AMAT")]
    assert _reject(book, "AMD", max_positions_per_sector=off) is None
    assert _reject(book, "AMD", max_risk_per_sector=off) is None


def test_a_sector_lookup_that_raises_does_not_lose_the_entry_cycle():
    """The map is a config read. If it ever throws, the four rungs around it must
    still decide - refusing every trade because a TOML went missing would be a
    worse failure than not applying this one cap."""
    def _boom(symbol):
        raise RuntimeError("config exploded")

    got = pc.concentration_reject([_pos("MU")], "MU", "2026-10-16", 250.0,
                                  limits=_limits(max_positions_per_sector=1),
                                  sector_of=_boom)
    assert got is None


def test_the_default_sector_lookup_is_the_shared_map():
    """No injection -> the real map, so production needs no wiring at the call
    site (and cannot forget it)."""
    from shared import sectors
    book = [_pos("MU"), _pos("INTC"), _pos("AMAT")]
    got = pc.concentration_reject(book, "AMD", "2026-10-16", 250.0,
                                  limits=_limits(max_positions_per_sector=3))
    assert got == pc.SECTOR_POSITION_CAP
    assert sectors.sector_of("AMD") == "Information Technology"


# ── ordering: which reason wins when several bind ────────────────────────────

def test_the_symbol_cap_is_reported_ahead_of_the_sector_cap():
    """When both bind, "you already hold three MU" is the actionable sentence —
    the operator can pick a different symbol. "Tech is full" is the answer only
    once the symbol has room."""
    book = [_pos("MU"), _pos("MU"), _pos("MU"), _pos("INTC")]
    got = pc.concentration_reject(book, "MU", "2026-10-16", 250.0,
                                  limits=_limits(max_positions_per_symbol=3,
                                                 max_positions_per_sector=4),
                                  sector_of=_sector_of)
    assert got == pc.SYMBOL_POSITION_CAP


def test_the_deployment_cap_still_outranks_everything():
    book = [_pos("MU", 4000.0)]
    got = pc.concentration_reject(book, "MU", "2026-10-16", 1000.0,
                                  limits={**_limits(max_positions_per_sector=1),
                                          "max_deployed_risk_pct": 0.20},
                                  equity=10000.0, sector_of=_sector_of)
    assert got == pc.DEPLOYMENT_CAP


def test_the_sector_cap_is_reported_ahead_of_the_expiry_cap():
    """Sector is the newer and more specific statement about WHY this trade is
    unwanted; a shared expiry is the weaker coincidence of the two."""
    book = [_pos("MU"), _pos("INTC"), _pos("AMAT"), _pos("XOM")]
    got = pc.concentration_reject(book, "AMD", "2026-10-16", 250.0,
                                  limits=_limits(max_positions_per_sector=3,
                                                 max_positions_per_expiry=4),
                                  sector_of=_sector_of)
    assert got == pc.SECTOR_POSITION_CAP


def test_the_position_cap_is_reported_ahead_of_the_sector_risk_cap():
    """A count is the more legible log line — the same rule the symbol rungs
    already follow."""
    book = [_pos("MU", 900.0), _pos("INTC", 900.0)]
    got = pc.concentration_reject(book, "AMAT", "2026-10-16", 250.0,
                                  limits=_limits(max_positions_per_sector=2,
                                                 max_risk_per_sector=1500.0),
                                  sector_of=_sector_of)
    assert got == pc.SECTOR_POSITION_CAP


# ── the shipped policy ───────────────────────────────────────────────────────

def test_default_limits_carries_both_sector_caps():
    got = pc.default_limits()
    assert got["max_positions_per_sector"] > 0
    assert got["max_risk_per_sector"] > 0


def test_the_shipped_sector_caps_sit_between_the_symbol_and_book_rungs():
    """A rung that is looser than the book cap never binds, and one tighter than
    the symbol cap makes the symbol cap dead. Both would be a rung in name only.
    """
    import config_paper as cp
    lim = pc.default_limits()
    assert lim["max_risk_per_sector"] > cp.MAX_RISK_PER_SYMBOL
    # The book ceiling on the live account: 20% of ~$24,184 session-start equity.
    assert lim["max_risk_per_sector"] < 0.20 * 24184.0
    assert lim["max_positions_per_sector"] >= cp.MAX_POSITIONS_PER_SYMBOL


def test_the_shipped_caps_would_have_bound_on_the_measured_history():
    """Non-vacuity for the policy, not just the mechanism: the manual book's
    worst IT day was $3,569 across 19 positions, and the driver's $21,531 across
    15. A cap that would not have refused either is not a cap."""
    lim = pc.default_limits()
    assert lim["max_risk_per_sector"] < 3569.0
    assert lim["max_positions_per_sector"] < 19


# ── the journal line, which is the ONLY trace this refusal leaves ────────────

def test_the_journal_line_names_the_sector_that_filled_up(caplog):
    """A concentration breach is invisible in the UI by design, so this line is
    all an operator gets. "SECTOR_RISK_CAP" alone does not say WHICH sector."""
    import logging

    import paper_engine
    with caplog.at_level(logging.INFO, logger="paper_engine"):
        paper_engine._log_capped(set(), "MU", pc.SECTOR_POSITION_CAP)
    assert "Information Technology" in caplog.text
    assert "MU" in caplog.text


def test_an_unmapped_symbol_shows_as_a_question_mark_bucket(caplog):
    """The map's coverage gap surfaces here rather than in a second counter: a
    bucket printed as ``?ZZQQ`` is shared.sectors saying it has no sector."""
    import logging

    import paper_engine
    with caplog.at_level(logging.INFO, logger="paper_engine"):
        paper_engine._log_capped(set(), "ZZQQ_NOT_A_TICKER", pc.SECTOR_RISK_CAP)
    assert "?ZZQQ_NOT_A_TICKER" in caplog.text


def test_a_non_sector_reason_carries_no_bucket(caplog):
    """The other four rungs are not about sectors, and a bracket on their line
    would read as though they were."""
    import logging

    import paper_engine
    with caplog.at_level(logging.INFO, logger="paper_engine"):
        paper_engine._log_capped(set(), "MU", pc.SYMBOL_POSITION_CAP)
    assert "Information Technology" not in caplog.text
    assert "SYMBOL_POSITION_CAP" in caplog.text
