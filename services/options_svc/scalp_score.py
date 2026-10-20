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


#: What an intraday call's confirmation can say (``scalp_confirm``'s words).
WORDS = ("Confirms", "Mixed", "Contradicts")
_INTRADAY_COLS = ("day", "slot", "symbol", "role", "model_result", "highlighted",
                  "avoided", "confirm_word", "p_base", "block")


def _days(rows) -> int:
    return len({r["day"] for r in rows})


def _intraday_line(rows) -> dict:
    """``{days, rows, wins, rate, p_base}`` over intraday grade rows: ``days`` is
    the number of distinct DAYS, ``rows`` the slot-roles counted, ``p_base`` the
    mean of their published base rates; the rates are ``None`` with no rows."""
    wins = sum(1 for r in rows if r["model_result"] == "win")
    return {"days": _days(rows), "rows": len(rows), "wins": wins,
            "rate": _rate(wins, len(rows)), "p_base": _mean(r["p_base"] for r in rows)}


def _word_line(rows) -> dict:
    wins = sum(1 for r in rows if r["model_result"] == "win")
    return {"days": _days(rows), "rows": len(rows), "wins": wins,
            "win_rate": _rate(wins, len(rows))}


def _block_card(rows) -> dict:
    """One symbol's one time block: the highlighted calls' hit rate beside the base
    rate, the cautions, and the long-with role's win rate by the call's word."""
    long_with = [r for r in rows if r["role"] == "long_with"]
    return {"days": _days(rows),
            "edge": _intraday_line([r for r in rows if r["highlighted"]]),
            "caution": _intraday_line([r for r in rows if r["avoided"]]),
            "by_word": {w: _word_line([r for r in long_with if r["confirm_word"] == w])
                        for w in WORDS}}


def _intraday(rows, window):
    """``{"days", "window", "symbols": {symbol: {"days", "blocks": {block: card}}}}``
    over the graded intraday record, or ``None`` when there is none. The look-back
    is the last ``window`` graded DAYS of each (symbol, block), the day being the
    unit (the rows of one day share one tape)."""
    symbols, seen = {}, set()
    for symbol in scalp_config.symbols():
        mine = [r for r in rows if r["symbol"] == symbol]
        blocks, kept = {}, set()
        first = {}
        for r in mine:
            first[r["block"]] = min(first.get(r["block"], r["slot"]), r["slot"])
        for block in sorted(first, key=first.get):
            in_block = [r for r in mine if r["block"] == block]
            days = set(sorted({r["day"] for r in in_block})[-window:])
            blocks[block] = _block_card([r for r in in_block if r["day"] in days])
            kept |= days
        if blocks:
            symbols[symbol] = {"days": len(kept), "blocks": blocks}
            seen |= kept
    if not symbols:
        return None
    return {"days": len(seen), "window": window, "symbols": symbols}


def scorecard(db_path=None) -> dict:
    """``{"days", "window", "symbols": {symbol: card}}`` over the graded record, or
    ``{}`` when nothing has been graded yet. When the 15-minute intraday calls have
    graded days it also carries ``intraday`` (see ``_intraday``); the 08:36
    sections are the same with or without it, and an intraday record alone is
    ``{"intraday": ...}``."""
    conn = scalp_store.connect(db_path)
    try:
        found = conn.execute(
            "SELECT day, symbol, role, model_result, chain_result, highlighted, "
            "avoided, p_base FROM grades WHERE model_result IN ('win', 'loss', 'timeout')"
        ).fetchall()
        intraday = conn.execute(
            "SELECT day, slot, symbol, role, model_result, highlighted, avoided, "
            "confirm_word, p_base, block FROM intraday_grades "
            "WHERE model_result IN ('win', 'loss', 'timeout')").fetchall()
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
    out = ({"days": len(seen), "window": window, "symbols": symbols} if symbols else {})
    section = _intraday([dict(zip(_INTRADAY_COLS, r)) for r in intraday], window)
    if section is not None:
        out["intraday"] = section
    return out


__all__ = ["WORDS", "scorecard"]
