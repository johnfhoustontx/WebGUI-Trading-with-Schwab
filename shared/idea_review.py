"""The weekly trade-idea review's numbers (pure).

Takes the Paper Ledger's trade rows (``cache:options:paper_trades`` "trades")
and computes every figure the weekly review shows: a rating per closed trade,
grouped tables for the week and for all time, the open trades, and the ideas
booked more than once. Nothing here reads config, the clock, a store or the
bus: ``cfg`` and ``today`` are arguments, so the verdict is reproducible.

Imports the standard library and ``shared.market_calendar`` and nothing else.

Rows and rules
--------------
* Only rows with ``source == "trade_idea"`` count; ``facts`` filters itself.
* Closed means ``status != "OPEN"``. A closed trade belongs to the week its
  ``exit_time`` falls in (Central date); an open trade is listed under
  ``open`` whatever week it was posted in and is never graded.
* Timestamps are ISO. One with a UTC offset is converted to Central before a
  date or an hour is taken; a naive one is already Central.
* ``mae_pnl`` is per contract and ``max_loss_per`` per share, so
  ``mae_pct = mae_pnl / (max_loss_per * 100) * 100`` (``None`` when the max
  loss is 0 or missing). ``webgui/pages/eod.py`` keeps its own copy of this
  one-liner on purpose: Tier 1 may not import this module.
* ``win_rate`` is a percentage (0-100) of closed trades with a P&L above zero.
  A closed row with no ``realized_pnl`` counts as 0.

The verification contract (Task 10 relies on it)
------------------------------------------------
``fact_ids(f)`` is the set of ids Claude may cite: ``week.total``,
``all_time.total`` and ``<scope>.<dim>.<value>`` for every group row, with
``scope`` in ``week`` / ``all_time`` and ``dim`` in ``grade``, ``strategy``,
``trade_type``, ``score_band``, ``post_hour``, ``symbol``. Ids are unique and
stable for the same input.

``fact_numbers(f)`` is the set of strings those numbers print as. The report
prints money and percentages with TWO decimals (``125.00``, ``-37.50``,
``62.50``) and counts as integers (``4``). Every money or percentage figure is
in the set both signed and absolute (``-37.50`` and ``37.50``: Claude may
write "lost 37.50"), and a negative zero never appears. A leading ``$`` or a
thousands comma is NOT in the strings: the verifier strips ``$`` and ``,``
from Claude's text before matching.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from shared.market_calendar import is_trading_day, next_trading_day

_CT = ZoneInfo("America/Chicago")

DIMENSIONS = ("grade", "strategy", "trade_type", "score_band", "post_hour",
              "symbol")
SCORE_BANDS = ("<60", "60-69", "70-79", "80+", "no score")

RATINGS = ("clean win", "win after drawdown", "stopped", "expired win",
           "expired loss", "closed by hand")


# ---- small readers --------------------------------------------------------

def _real(v):
    """A finite number as a float, else None (a bool is not a number)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _moment(v) -> datetime | None:
    """ISO text to an aware Central datetime; a naive stamp is Central."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        dt = datetime.fromisoformat(v.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_CT)
    return dt.astimezone(_CT)


def _posted(t: dict) -> datetime | None:
    return _moment(t.get("idea_posted")) or _moment(t.get("entry_time"))


def mae_pct(t: dict) -> float | None:
    """Worst drawdown as a percentage of the max loss (<= 0), or None."""
    mae = _real(t.get("mae_pnl"))
    per = _real(t.get("max_loss_per"))
    if mae is None or per is None or per <= 0:
        return None
    return mae / (per * 100.0) * 100.0


def _held_h(t: dict) -> float | None:
    start, end = _posted(t), _moment(t.get("exit_time"))
    if start is None or end is None:
        return None
    # Subtract in UTC: two Central datetimes share a tzinfo, and Python then
    # subtracts wall-clock time, which is an hour out across a DST change.
    secs = (end.astimezone(timezone.utc)
            - start.astimezone(timezone.utc)).total_seconds()
    return secs / 3600.0 if secs >= 0 else None


def _pnl(t: dict) -> float:
    return _real(t.get("realized_pnl")) or 0.0


# ---- ratings and the calendar --------------------------------------------

def rate(t: dict, clean_mae_pct: float) -> str:
    """One rating for a closed trade.

    A TARGET whose drawdown stayed shallower than ``clean_mae_pct`` of the max
    loss is a clean win, a deeper one a win after drawdown. When the percentage
    cannot be taken (no max loss), a TARGET whose worst point is exactly 0.0 -
    marked and never below zero - is still a clean win, because no drawdown
    happened; one with no worst point recorded (``mae_pnl`` missing), or one
    that fell by an amount that cannot be judged, is not called clean. EXPIRED
    is judged by the sign of ``realized_pnl`` (zero is a loss); MANUAL, and
    anything else, is closed by hand.
    """
    reason = t.get("exit_reason")
    if reason == "TARGET":
        m = mae_pct(t)
        if m is None:
            return ("clean win" if _real(t.get("mae_pnl")) == 0.0
                    else "win after drawdown")
        return "clean win" if m > -float(clean_mae_pct) else "win after drawdown"
    if reason == "STOP":
        return "stopped"
    if reason == "EXPIRED" or (reason is None and t.get("status") == "EXPIRED"):
        return "expired win" if _pnl(t) > 0 else "expired loss"
    return "closed by hand"


def week_bounds(d: date) -> tuple[date, date]:
    """Monday and Sunday of ``d``'s ISO week."""
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def is_review_day(d: date) -> bool:
    """The last trading day of its ISO week (a Friday holiday runs Thursday)."""
    return (is_trading_day(d)
            and week_bounds(next_trading_day(d))[0] != week_bounds(d)[0])


# ---- grouping -------------------------------------------------------------

def _score_band(score) -> str:
    s = _real(score)
    if s is None:
        return "no score"
    if s < 60:
        return "<60"
    if s < 70:
        return "60-69"
    if s < 80:
        return "70-79"
    return "80+"


def _post_hour(t: dict) -> str:
    p = _posted(t)
    return str(p.hour) if p is not None else "unknown"


def _text(v, fallback: str) -> str:
    s = str(v).strip() if v is not None else ""
    return s or fallback


_KEYERS = {
    "grade": lambda t: _text(t.get("idea_grade"), "ungraded"),
    "strategy": lambda t: _text(t.get("strategy"), "unknown"),
    "trade_type": lambda t: _text(t.get("trade_type"), "unknown"),
    "score_band": lambda t: _score_band(t.get("idea_score")),
    "post_hour": _post_hour,
    "symbol": lambda t: _text(t.get("symbol"), "unknown").upper(),
}


def _mean(values: list) -> float | None:
    return sum(values) / len(values) if values else None


def _fact(fid: str, rows: list[dict], min_n: int) -> dict:
    n = len(rows)
    pnls = [_pnl(t) for t in rows]
    reasons = Counter(t.get("exit_reason") for t in rows)
    expired = reasons["EXPIRED"] + sum(
        1 for t in rows
        if t.get("exit_reason") is None and t.get("status") == "EXPIRED")
    total = sum(pnls)
    return {
        "id": fid,
        "n": n,
        "targets": reasons["TARGET"],
        "stops": reasons["STOP"],
        "expired": expired,
        "manual": n - reasons["TARGET"] - reasons["STOP"] - expired,
        "win_rate": (100.0 * sum(1 for p in pnls if p > 0) / n) if n else None,
        "avg_pnl": total / n if n else None,
        "total_pnl": total,
        "avg_mae_pct": _mean([m for m in map(mae_pct, rows) if m is not None]),
        "avg_held_h": _mean([h for h in map(_held_h, rows) if h is not None]),
        "thin": n < min_n,
    }


def _order(dim: str, value: str):
    if dim == "score_band":
        return SCORE_BANDS.index(value)
    if dim == "post_hour":
        return (0, int(value)) if value.isdigit() else (1, 0)
    return 0


def _scope(scope: str, rows: list[dict], min_n: int) -> dict:
    out = {"total": _fact(f"{scope}.total", rows, min_n)}
    for dim in DIMENSIONS:
        groups: dict[str, list[dict]] = {}
        for t in rows:
            groups.setdefault(_KEYERS[dim](t), []).append(t)
        facts = [_fact(f"{scope}.{dim}.{v}", g, min_n)
                 for v, g in groups.items()]
        if dim in ("score_band", "post_hour"):
            facts.sort(key=lambda f: _order(dim, f["id"].split(".", 2)[2]))
        else:
            facts.sort(key=lambda f: (-f["n"], f["id"]))
        out[dim] = facts
    return out


def _structure(t: dict) -> str:
    """The structure's display name: the idea card's label stored at booking
    (``idea_label``), else the structure key."""
    return _text(t.get("idea_label"), _text(t.get("strategy"), "unknown"))


def _closed_line(t: dict, clean: float) -> dict:
    return {
        "idea_id": t.get("idea_id"),
        "symbol": _text(t.get("symbol"), "unknown").upper(),
        "strategy": _text(t.get("strategy"), "unknown"),
        "structure": _structure(t),
        "grade": _text(t.get("idea_grade"), "ungraded"),
        "score": t.get("idea_score"),
        "exit_reason": t.get("exit_reason"),
        "rating": rate(t, clean),
        "entry": _real(t.get("entry_credit_total")),
        "exit": _real(t.get("exit_debit_total")),
        "realized_pnl": _pnl(t),
        "mae_pct": mae_pct(t),
        "held_h": _held_h(t),
    }


def _open_line(t: dict, today: date) -> dict:
    p = _posted(t)
    return {
        "idea_id": t.get("idea_id"),
        "symbol": _text(t.get("symbol"), "unknown").upper(),
        "strategy": _text(t.get("strategy"), "unknown"),
        "structure": _structure(t),
        "grade": _text(t.get("idea_grade"), "ungraded"),
        "unrealized_pnl": _real(t.get("unrealized_pnl")),
        "mae_pnl": _real(t.get("mae_pnl")),
        "mae_pct": mae_pct(t),
        "days_open": max((today - p.date()).days, 0) if p is not None else None,
    }


def facts(trades: list[dict], today: date, cfg: dict) -> dict:
    """Every number of the weekly review, from the Ledger's trade rows.

    ``cfg`` carries ``clean_mae_pct`` and ``min_n`` (``paper_limits.
    idea_trades()``). Returns ``{"week": scope, "all_time": scope, "open":
    [...], "closed_week": [...], "repeats": [...], "structure_names": {...}}``,
    a scope being ``{"total": fact, "<dim>": [fact, ...]}`` for each dimension
    in ``DIMENSIONS``. ``structure_names`` maps each structure key the rows hold
    (the ``strategy`` group's value) to its display name (``_structure``).
    """
    clean = float(cfg["clean_mae_pct"])
    min_n = int(cfg["min_n"])
    rows = [t for t in trades or [] if t.get("source") == "trade_idea"]
    closed = [t for t in rows if t.get("status") != "OPEN"]
    start, end = week_bounds(today)

    def in_week(t: dict) -> bool:
        x = _moment(t.get("exit_time"))
        return x is not None and start <= x.date() <= end

    closed_week = [t for t in closed if in_week(t)]

    booked = Counter()
    for t in rows:
        p = _posted(t)
        if p is not None and start <= p.date() <= end and t.get("idea_id"):
            booked[t["idea_id"]] += 1

    return {
        "week": _scope("week", closed_week, min_n),
        "all_time": _scope("all_time", closed, min_n),
        "open": [_open_line(t, today) for t in rows
                 if t.get("status") == "OPEN"],
        "closed_week": [_closed_line(t, clean) for t in closed_week],
        "repeats": [{"idea_id": i, "count": c}
                    for i, c in sorted(booked.items()) if c > 1],
        "structure_names": _structure_names(rows),
    }


def _structure_names(rows: list[dict]) -> dict[str, str]:
    """``{structure key: display name}``; a key whose rows carry a label takes
    the first one, a key with none keeps its own text."""
    out: dict[str, str] = {}
    for t in rows:
        key = _KEYERS["strategy"](t)
        if out.get(key, key) == key:
            out[key] = _structure(t)
    return out


# ---- what Task 10 verifies against ---------------------------------------

def _tables(f: dict):
    for scope in ("week", "all_time"):
        s = f.get(scope) or {}
        if s.get("total"):
            yield s["total"]
        for dim in DIMENSIONS:
            yield from s.get(dim) or ()


def fact_ids(f: dict) -> set[str]:
    """Every id Claude may cite."""
    return {x["id"] for x in _tables(f)}


def _two(v: float) -> str:
    s = f"{v:.2f}"
    return "0.00" if float(s) == 0 else s


def _add(out: set, v) -> None:
    if isinstance(v, bool) or v is None:
        return
    if isinstance(v, int):
        out.add(str(v))
        return
    x = _real(v)
    if x is None:
        return
    out.add(_two(x))
    out.add(_two(abs(x)))
    if x == int(x):
        out.add(str(abs(int(x))))


_FACT_KEYS = ("n", "targets", "stops", "expired", "manual", "win_rate",
              "avg_pnl", "total_pnl", "avg_mae_pct", "avg_held_h")
_CLOSED_KEYS = ("score", "entry", "exit", "realized_pnl", "mae_pct",
                "held_h")
_OPEN_KEYS = ("unrealized_pnl", "mae_pnl", "mae_pct", "days_open")


def numbers_of(fact: dict) -> set[str]:
    """The numbers of ONE group fact (a scope's total or a group row), printed
    as :func:`fact_numbers` prints them. A suggestion that cites a fact may state
    these and no others: a group fact carries its own figures, not its rows."""
    out: set[str] = set()
    for k in _FACT_KEYS:
        _add(out, fact.get(k))
    return out


def fact_numbers(f: dict) -> set[str]:
    """Every number of ``f`` as the report prints it (see the module docstring).

    Money and percentages with two decimals, signed and absolute; counts as
    integers. A float that happens to be whole also gets its bare integer form
    ("75" for a score of 75.0), because a score prints that way.
    """
    out: set[str] = set()
    for x in _tables(f):
        out |= numbers_of(x)
    for x in f.get("closed_week") or ():
        for k in _CLOSED_KEYS:
            _add(out, x.get(k))
    for x in f.get("open") or ():
        for k in _OPEN_KEYS:
            _add(out, x.get(k))
    for x in f.get("repeats") or ():
        _add(out, x.get("count"))
    return out
