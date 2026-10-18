"""The Opening Scalp's scorecard: the graded record the page shows under the calls.

``scorecard`` reads the ``grades`` table (``scalp_grade`` writes it: one row per
PUBLISHED role, once the day's bars are in) and answers, per symbol and over the
last ``scorecard_days`` graded days, three honest questions:

* ``edge`` - on the days the call HIGHLIGHTED a role, how often did it win, beside
  the average base rate that role was published with. The study almost never
  finds an edge, so most symbols have no highlighted day: then ``days`` is 0 and
  the rate and base rate are ``None`` (the page says there were none);
* ``caution`` - on the days a role was in ``avoid`` (measured to lose more often
  than usual), how often that role won, beside its published base rate. This is
  the check on the one signal the study did find;
* ``roles`` - every role over all graded days: win / loss / timeout counts and the
  win rate by the model's result, and the win rate by the chain-implied result
  where there is one.

A win is the model's (``model_result``); the chain result is shown beside it and
never replaces it. A day is "graded" for a symbol when at least one of its roles
has a modelled win, loss or timeout (a ``skip`` and an absent result are not
results). Every number is a plain int or float; a rate with nothing under it is
``None``, and all four roles are always present. Pure reading: opens the store,
writes nothing. Imports nothing from ``compute`` and nothing from ``scalp_call``
(which calls this).
"""
from shared import scalp_config

from services.options_svc import scalp_sim, scalp_store

_RESULTS = ("win", "loss", "timeout")


def _rate(wins, n):
    return wins / n if n else None


def _mean(values):
    got = [v for v in values if v is not None]
    return sum(got) / len(got) if got else None


def _line(rows) -> dict:
    """``{days, wins, rate, p_base}`` over grade rows: ``days`` is the number of
    role-days counted, ``p_base`` the mean of their published base rates."""
    wins = sum(1 for r in rows if r["model_result"] == "win")
    return {"days": len(rows), "wins": wins, "rate": _rate(wins, len(rows)),
            "p_base": _mean(r["p_base"] for r in rows)}


def _role_line(rows) -> dict:
    n = len(rows)
    win = sum(1 for r in rows if r["model_result"] == "win")
    loss = sum(1 for r in rows if r["model_result"] == "loss")
    chain = [r for r in rows if r["chain_result"] in _RESULTS]
    chain_win = sum(1 for r in chain if r["chain_result"] == "win")
    return {"n": n, "win": win, "loss": loss, "timeout": n - win - loss,
            "win_rate": _rate(win, n), "chain_n": len(chain),
            "chain_win_rate": _rate(chain_win, len(chain))}


def _symbol_card(rows, window) -> dict:
    days = sorted({r["day"] for r in rows})[-window:]
    keep = set(days)
    mine = [r for r in rows if r["day"] in keep]
    return {"days": len(days),
            "edge": _line([r for r in mine if r["highlighted"]]),
            "caution": _line([r for r in mine if r["avoided"]]),
            "roles": {role: _role_line([r for r in mine if r["role"] == role])
                      for role in scalp_sim.ROLES}}


def scorecard(db_path=None) -> dict:
    """``{"days", "window", "symbols": {symbol: card}}`` over the graded record, or
    ``{}`` when nothing has been graded yet."""
    conn = scalp_store.connect(db_path)
    try:
        found = conn.execute(
            "SELECT day, symbol, role, model_result, chain_result, highlighted, "
            "avoided, p_base FROM grades WHERE model_result IN ('win', 'loss', 'timeout')"
        ).fetchall()
    finally:
        conn.close()
    cols = ("day", "symbol", "role", "model_result", "chain_result", "highlighted",
            "avoided", "p_base")
    rows = [dict(zip(cols, r)) for r in found]
    window = scalp_config.scorecard_days()
    symbols, seen = {}, set()
    for symbol in scalp_config.symbols():
        mine = [r for r in rows if r["symbol"] == symbol]
        if not mine:
            continue
        card = _symbol_card(mine, window)
        symbols[symbol] = card
        seen.update(sorted({r["day"] for r in mine})[-window:])
    if not symbols:
        return {}
    return {"days": len(seen), "window": window, "symbols": symbols}


__all__ = ["scorecard"]
