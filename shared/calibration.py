"""Realized-outcome statistics for recorded signals -- the pure math.

Hoisted out of ``tools/signal_calibration.py`` on 2026-08-25 because a SECOND
consumer arrived: ``options_svc`` publishes ``cache:options:calibration`` from
the same bucket arithmetic, and a copy would be the ``clamp``-times-nine trap in
a package this repo has already been bitten by. A service importing a ``tools/``
CLI would also invert the dependency direction.

Nothing here does I/O. ``tools/signal_calibration.py`` owns the SQL and the text
rendering; ``services/options_svc/calibration.py`` owns the cache payload.

Everything is measured in **R** -- realized P&L over the dollars that were at
risk -- so a 2-wide and a 10-wide spread are comparable, and so the formula's
``b`` and the reported EV share units.
"""
import math

CONTRACT_MULTIPLIER = 100.0


# ── the primitives ───────────────────────────────────────────────────────────

def _finite(v):
    """A real finite number, or None.

    Rejects bool (``float(True)`` is 1.0) and NaN. The NaN case is not
    hypothetical here: a NaN survives every ``>`` comparison as False, so it
    would be silently booked as a loss rather than excluded -- which is how the
    five NaN incidents documented in CLAUDE.md all began.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def r_multiple(realized_pnl, entry_max_loss):
    """Realized P&L as a multiple of the dollars risked, or None.

    ``entry_max_loss`` is stored PER SHARE and ``realized_pnl`` in dollars for
    one contract, so the risk is ``entry_max_loss * 100``. This is the only
    normalization that makes a $144 spread and a $1,000 spread comparable.
    """
    pnl, ml = _finite(realized_pnl), _finite(entry_max_loss)
    if pnl is None or ml is None or ml <= 0:
        return None
    return pnl / (ml * CONTRACT_MULTIPLIER)


def breakeven_win_rate(entry_credit, entry_max_loss):
    """The win rate this trade's own price demanded: p* = 1/(1+b).

    With b = credit/max_loss that reduces to ``max_loss / (credit + max_loss)``
    -- i.e. ``max_loss/width``, or ``1 - credit/width``. Below this, the trade
    loses money no matter how good it feels.
    """
    c, ml = _finite(entry_credit), _finite(entry_max_loss)
    if c is None or ml is None or ml <= 0 or (c + ml) <= 0:
        return None
    return ml / (c + ml)


def priced_win_rate(entry_short_delta, strategy=None):
    """The win rate the ENTRY DELTA implied: 1 - |delta|. None when unknowable.

    ⚠ An iron condor is refused. `signal_db` stores a single
    ``entry_short_delta``, and `scanner_engine` writes the SUM of the two shorts
    into it -- which for a symmetric condor is ~0, so ``1 - |d|`` would report a
    confident 100%. The two-sided probability needs both legs
    (``p_pop + c_pop - 100``) and that is not in this table, so the honest answer
    is that we do not know.
    """
    if (strategy or "").strip().upper() == "IC":
        return None
    d = _finite(entry_short_delta)
    if d is None:
        return None
    return 1.0 - abs(d)


def score_bin(score, width=5):
    """A continuous entry_score as a fixed-width bucket label, e.g. ``60-65``."""
    s = _finite(score)
    if s is None or width <= 0:
        return "?"
    lo = int(math.floor(s / width) * width)
    return f"{lo}-{lo + width}"


# ── the statistics ───────────────────────────────────────────────────────────

_EMPTY = {"n": 0, "wins": 0, "losses": 0, "scratches": 0, "realized_p": None,
          "avg_win_r": None, "avg_loss_r": None, "b": None, "ev_r": None,
          "ev_units": None, "breakeven_p": None, "priced_p": None,
          "priced_breakeven_p": None, "edge_pp": None, "t_stat": None,
          "t_day": None, "days": 0, "total_r": None}


def _t_stat(values):
    """t-stat of the mean against zero, or None when it is not defined."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    se = math.sqrt(var / n)
    return (mean / se) if se > 0 else None


def bucket_stats(rows):
    """Every number the report needs for one bucket. Never raises.

    ``ev_r`` is the arithmetic mean R -- the truth, computed without any model.
    ``ev_units`` is the same thing through ``p*b - (1-p)``, stated in units of
    the average loss. They are pinned to each other by test, because a report
    that shows both and lets them drift is worse than one that shows neither.

    ⚠ With a scratch (exactly $0) present the second term is the LOSS FRACTION,
    not ``1-p``. A scratch is neither a win nor a loss, so ``p + loss_frac < 1``
    and the textbook form would overstate the drag. When there are no scratches
    the two are identical.
    """
    rs, priced, priced_be = [], [], []
    by_day = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        rv = r_multiple(r.get("realized_pnl"), r.get("entry_max_loss"))
        if rv is None:
            continue                      # unusable row: excluded, not a loss
        rs.append(rv)
        day = r.get("first_seen_date")
        if day:                           # an undated row joins no day cluster
            by_day.setdefault(str(day), []).append(rv)
        p = priced_win_rate(r.get("entry_short_delta"), r.get("strategy"))
        if p is not None:
            priced.append(p)
        be = breakeven_win_rate(r.get("entry_credit"), r.get("entry_max_loss"))
        if be is not None:
            priced_be.append(be)

    n = len(rs)
    if not n:
        return dict(_EMPTY)

    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    mean_r = sum(rs) / n
    p_hat = len(wins) / n
    loss_frac = len(losses) / n
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None

    b = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss) else None
    # p*b - loss_frac, which IS p*b - (1-p) whenever nothing scratched.
    ev_units = (p_hat * b - loss_frac) if b is not None else None
    breakeven_p = (1.0 / (1.0 + b)) if b else None

    priced_p = (sum(priced) / len(priced)) if priced else None
    edge_pp = ((p_hat - priced_p) * 100.0) if priced_p is not None else None

    # Two t-stats on purpose. The naive one treats every signal as an
    # independent bet, which it plainly is not -- one scan emits a dozen at once
    # and they share a tape. t_day is computed over PER-DAY mean R, so a day that
    # fired twenty correlated signals counts once. Read t_day.
    t_stat = _t_stat(rs)
    t_day = _t_stat([sum(v) / len(v) for v in by_day.values()])

    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "scratches": n - len(wins) - len(losses),
        "days": len(by_day),
        "realized_p": p_hat,
        "avg_win_r": avg_win, "avg_loss_r": avg_loss, "b": b,
        "ev_r": mean_r, "ev_units": ev_units,
        "total_r": sum(rs),
        "breakeven_p": breakeven_p,
        "priced_p": priced_p,
        "priced_breakeven_p": (sum(priced_be) / len(priced_be)) if priced_be else None,
        "edge_pp": edge_pp,
        "t_stat": t_stat, "t_day": t_day,
    }


def calibrate(rows, key="entry_grade", min_n=1):
    """Group the joined rows and report each bucket. Sorted by bucket name.

    Name order is deliberate rather than "best first": the whole point is to read
    DOWN a monotone axis (grade, or a score bin) and see whether the numbers move
    with it. Sorting by EV would hide exactly the pattern being looked for.
    """
    groups = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if r_multiple(r.get("realized_pnl"), r.get("entry_max_loss")) is None:
            continue
        groups.setdefault(_bucket_of(r, key), []).append(r)

    out = []
    for name, rs in groups.items():
        s = bucket_stats(rs)
        if s["n"] < min_n:
            continue
        out.append({"bucket": name, **s})
    return sorted(out, key=lambda b: str(b["bucket"]))


def split_calibrate(rows, key="entry_score", split_by="scanner_type", min_n=1):
    """``[(split_value, buckets), ...]`` -- the ``calibrate`` breakdown computed
    SEPARATELY within each value of ``split_by``.

    0-DTE and swing are different games: a 0-DTE spread has hours of gamma risk
    and no room to recover, a 14-DTE one has both. Pooling them reports the mean
    of two populations that need not share a score gate at all.

    ⚠ ``min_n`` applies WITHIN a split, which is the whole point -- six trades
    that clear a floor pooled may be three and three once split, and neither side
    is then worth reporting. A split value left with no surviving bucket is
    dropped rather than printed empty.
    """
    groups = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        groups.setdefault(_bucket_of(r, split_by), []).append(r)

    out = []
    for name in sorted(groups, key=str):
        buckets = calibrate(groups[name], key, min_n)
        if buckets:
            out.append((name, buckets))
    return out


def _bucket_of(row, key):
    if key == "entry_score":
        return score_bin(row.get("entry_score"))
    v = row.get(key)
    return "?" if v is None or v == "" else str(v)


# ── the cross-tier bucket key ────────────────────────────────────────────────
#
# ⚠ The two tiers spell the family differently and nothing forced them to agree:
# `signals.db` stores `scanner_type` as '0DTE', while a live signal on the page
# carries `trade_type` '0-DTE' and a `scanner_type` of None. Keyed on the raw
# value the 0-DTE bucket would never match page-side -- silently, and for the
# family with the most recorded data. Both sides go through family_key().

_FAMILY_ALIASES = {"0DTE": "0DTE", "0-DTE": "0DTE", "SWING": "SWING"}


def family_key(value):
    """Canonical family token, or None when there is nothing to key on.

    An unrecognised family is passed through upper-cased rather than guessed --
    a new scanner type should show up as its own bucket, not be folded into an
    existing one.
    """
    if not isinstance(value, str):
        return None
    v = value.strip().upper()
    if not v:
        return None
    return _FAMILY_ALIASES.get(v, v)


def bucket_key(family, score, width=5):
    """``"0DTE|60-65"`` -- the key both tiers address a calibration bucket by."""
    fam = family_key(family)
    if fam is None:
        return None
    b = score_bin(score, width)
    return None if b == "?" else f"{fam}|{b}"
