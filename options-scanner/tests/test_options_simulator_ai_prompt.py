from datetime import date, datetime
import numpy as np
import pandas as pd
import pytest

from options_simulator.engine import ChainSnapshot, ContractRow
from options_simulator.ai_prompt import build_simulator_prompt


def _snap(history=None, symbol="TSLA"):
    return ChainSnapshot(
        spot=580.0,
        as_of=datetime(2026, 5, 23, 14, 30),
        r=0.04,
        symbol=symbol,
        contracts=[],
        price_history=history if history is not None else pd.Series(dtype=float),
    )


CONTRACT = ContractRow(strike=580.0, kind="call", bid=2.10, ask=2.20, mid=2.15,
                        iv=0.18, expiry=date(2026, 5, 23))


def test_prompt_contains_contract_identity_line():
    out = build_simulator_prompt(_snap(), CONTRACT, mode_state={"mode": "whatif"})
    assert "580" in out
    assert "call" in out.lower()
    assert "2026-05-23" in out


def test_prompt_includes_underlying_symbol():
    """The AI should know the ticker, not have to guess from price."""
    out = build_simulator_prompt(_snap(symbol="TSLA"), CONTRACT, mode_state={"mode": "whatif"})
    assert "TSLA" in out


def test_prompt_demands_why_reasoning():
    """Every section should push for explanation, not just facts."""
    out = build_simulator_prompt(_snap(), CONTRACT, mode_state={"mode": "whatif"})
    # The question block asks WHY in multiple places
    assert out.lower().count("why") >= 5


def test_prompt_requests_plain_english():
    out = build_simulator_prompt(_snap(), CONTRACT, mode_state={"mode": "whatif"})
    assert "plain english" in out.lower() or "plain prose" in out.lower()


def test_prompt_contains_current_greeks_section():
    out = build_simulator_prompt(_snap(), CONTRACT, mode_state={"mode": "whatif"})
    assert "Delta" in out or "delta" in out
    assert "Gamma" in out or "gamma" in out
    assert "Theta" in out or "theta" in out


def test_prompt_replay_mode_includes_history_summary():
    hist = pd.Series(np.linspace(575, 585, 100),
                     index=pd.date_range("2026-05-22 09:30", periods=100, freq="1min"))
    out = build_simulator_prompt(_snap(hist), CONTRACT, mode_state={"mode": "replay"})
    assert "open" in out.lower() and "high" in out.lower() and "low" in out.lower()


def test_prompt_whatif_mode_includes_S_sweep_table():
    out = build_simulator_prompt(_snap(), CONTRACT,
                                  mode_state={"mode": "whatif", "s_pct_range": 0.20})
    assert "What-if" in out or "what-if" in out.lower()
    assert "-20%" in out or "-20 %" in out


def test_prompt_ivshock_mode_includes_sigma_multipliers():
    out = build_simulator_prompt(_snap(), CONTRACT,
                                  mode_state={"mode": "ivshock"})
    assert "0.5" in out and "3.0" in out


def test_prompt_ends_with_question_block():
    out = build_simulator_prompt(_snap(), CONTRACT, mode_state={"mode": "whatif"})
    assert "what could go wrong" in out.lower()
    assert "breakeven" in out.lower()


def test_prompt_naked_short_call_flags_unlimited_risk():
    from options_simulator.engine import Position
    pos = Position.single(CONTRACT, direction="sell", symbol="TSLA")
    out = build_simulator_prompt(_snap(symbol="TSLA"), pos, mode_state={"mode": "whatif"})
    assert "naked" in out.lower()
    assert "unlimited" in out.lower()


def test_prompt_vertical_spread_describes_legs_and_net():
    from options_simulator.engine import Position
    long_leg = ContractRow(strike=580.0, kind="call", bid=2.10, ask=2.20, mid=2.15,
                           iv=0.18, expiry=date(2026, 5, 23))
    short_leg = ContractRow(strike=585.0, kind="call", bid=1.10, ask=1.20, mid=1.15,
                            iv=0.17, expiry=date(2026, 5, 23))
    pos = Position.vertical(long_leg, short_leg, symbol="TSLA")
    out = build_simulator_prompt(_snap(symbol="TSLA"), pos, mode_state={"mode": "whatif"})
    assert "LONG" in out and "SHORT" in out
    assert "580" in out and "585" in out
    # Net debit = 2.15 - 1.15 = 1.00; should appear in the prompt
    assert "1.00" in out
    # Greeks are signed (net)
    assert "Current net Greeks" in out


def test_prompt_legacy_contract_arg_still_works():
    """Old callers passing a ContractRow get auto-wrapped as a long position."""
    out = build_simulator_prompt(_snap(symbol="TSLA"), CONTRACT, mode_state={"mode": "whatif"})
    assert "Long" in out
