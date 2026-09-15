"""The Paper Ledger books ``max_loss_total`` byte-for-byte as it did before
``shared.book_caps.booked_risk`` became the one rounding rule.

The two expressions ``paper_trader`` used until 2026-09-15 are FROZEN below as
literals. Over a seeded grid of more than 20,000 (signal, quantity) pairs - raw
per-share credit spreads with 2-6 decimals, commission-adjusted normalized rows
built by the real ``strategy_scanner.adapt_credit_spread`` / ``adapt_iron_condor``,
and per-contract debit rows with fractional cents - the booked total must equal the
frozen expression EXACTLY: ``==``, the same type, and the same ``repr``. An approx
comparison would hide precisely the sub-cent drift this change exists to remove.

It also pins ``risk_basis``: ``booked_risk(risk_basis(signal), q)`` is the booked
total, which is the contract the service's stamp and the Paper dialog preview rely on.
"""
import datetime
import random

import paper_trader
import strategy_scanner
from shared import book_caps

_EXP = (datetime.date.today() + datetime.timedelta(days=21)).isoformat()
_SEED = 20260915


def _frozen_credit_total(max_loss_per, quantity):
    multiplier = 100
    return round(max_loss_per * quantity * multiplier, 2)


def _frozen_debit_total(max_loss, quantity):
    return round(max_loss * quantity, 2)


def _raw_credit(rng, typ):
    width = rng.choice((0.5, 1.0, 2.5, 5.0, 10.0, 25.0))
    decimals = rng.randint(2, 6)
    credit = round(rng.uniform(0.01, width * 0.6), decimals)
    max_loss = round(width - credit, decimals)
    short = round(rng.uniform(20, 900), 1)
    long_ = short - width if typ == "PCS" else short + width
    sig = {"symbol": rng.choice(("SPY", "ORCL", "MU", "KO", "ZZZQ")), "type": typ,
           "trade_type": "SWING", "expiration": _EXP, "dte": 21,
           "short_strike": short, "long_strike": long_, "width": width,
           "credit": credit, "max_loss": max_loss,
           "short_mark": round(credit + rng.uniform(0.05, 1.0), 4),
           "long_mark": round(rng.uniform(0.01, 1.0), 4),
           "short_delta": round(rng.uniform(-0.4, 0.4), 3)}
    return sig


def _raw_ic(rng):
    sig = _raw_credit(rng, "PCS")
    width = sig["width"]
    sig.update({"type": "IC", "call_short": sig["short_strike"] + 2 * width,
                "call_long": sig["short_strike"] + 3 * width,
                "call_short_mark": round(rng.uniform(0.05, 1.0), 4),
                "call_long_mark": round(rng.uniform(0.01, 0.5), 4)})
    return sig


def _debit(rng):
    typ = rng.choice(sorted(paper_trader.PAPER_DEBIT_TYPES))
    # per-contract dollars with fractional cents (3-5 decimals)
    max_loss = round(rng.uniform(5.0, 2500.0), rng.randint(3, 5))
    return {"symbol": "SPY", "type": typ, "trade_type": "SWING", "expiration": _EXP,
            "dte": 21, "net_debit": max_loss, "max_loss": max_loss,
            "max_profit": None, "unbounded": True,
            "legs": [{"kind": "call", "side": "long", "strike": 500.0}]}


def _grid():
    rng = random.Random(_SEED)
    signals = []
    for _ in range(300):
        signals.append(("raw", _raw_credit(rng, rng.choice(("PCS", "CCS")))))
    for _ in range(60):
        signals.append(("raw", _raw_ic(rng)))
    for _ in range(150):
        signals.append(("normalized",
                        strategy_scanner.adapt_credit_spread(
                            _raw_credit(rng, rng.choice(("PCS", "CCS"))))))
    for _ in range(40):
        signals.append(("normalized", strategy_scanner.adapt_iron_condor(_raw_ic(rng))))
    for _ in range(250):
        signals.append(("debit", _debit(rng)))
    return signals


def test_the_grid_is_big_and_covers_every_shape():
    signals = _grid()
    kinds = {k for k, _ in signals}
    assert kinds == {"raw", "normalized", "debit"}
    normalized = [s for k, s in signals if k == "normalized"]
    assert all(paper_trader._is_normalized_signal(s) for s in normalized)
    # The commission really was folded in, so the per-share figure is not just
    # max_loss / 100 - otherwise the "commission-adjusted" rows prove nothing new.
    assert any(paper_trader._credit_max_loss_per_share(s) != s["max_loss"] / 100
               for s in normalized)
    assert len(signals) * 100 >= 20_000


def test_booked_max_loss_total_is_byte_identical_to_the_frozen_expressions():
    checked = 0
    for kind, sig in _grid():
        for q in range(1, 101):
            trade = paper_trader.create_paper_trade(dict(sig), q)
            if kind == "debit":
                expected = _frozen_debit_total(sig.get("max_loss") or 0.0, q)
            else:
                expected = _frozen_credit_total(
                    paper_trader._credit_max_loss_per_share(sig), q)
            got = trade["max_loss_total"]
            assert got == expected and type(got) is type(expected) \
                and repr(got) == repr(expected), (kind, sig, q, got, expected)
            assert book_caps.booked_risk(paper_trader.risk_basis(sig), q) == got
            checked += 1
    assert checked >= 20_000


def test_risk_basis_names_the_figure_each_branch_books_from():
    rng = random.Random(_SEED + 1)
    raw = _raw_credit(rng, "PCS")
    assert paper_trader.risk_basis(raw) == {"per_share": raw["max_loss"]}
    norm = strategy_scanner.adapt_credit_spread(raw)
    assert paper_trader.risk_basis(norm) == {
        "per_share": paper_trader._credit_max_loss_per_share(norm)}
    debit = _debit(rng)
    assert paper_trader.risk_basis(debit) == {"per_contract": debit["max_loss"]}
    assert paper_trader.risk_basis({**debit, "max_loss": None}) == {"per_contract": 0.0}
