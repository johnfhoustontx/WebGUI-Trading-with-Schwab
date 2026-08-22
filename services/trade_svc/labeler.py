"""Fill in what actually happened after each recommendation. PURE arithmetic.

The recommendation journal records what the model said; this says what the tape
did. Together they are the only way to answer "is the live edge holding?" — and
because the journal cannot be backfilled, that answer only exists going forward.

**Three labels per horizon, not one.** Phase 4 measured this model at
cross-sectional IC +0.16 when the market rises and −0.11 when it falls: its
measured edge IS beta. A monitor scoring itself on the RAW forward excess would
therefore report health right through any rising market and reproduce exactly the
illusion Phase 4 dismantled. So each horizon stores:

  ``fwd_Nd``     r_symbol − r_SPY          (comparable to the artifact's own IC)
  ``fwd_Nd_ba``  r_symbol − beta·r_SPY     (what is left once leverage is paid for)
  ``mkt_fwd_Nd`` r_SPY                     (so the monitor can split up from down)

**Horizons are TRADING bars.** The fit's 20-day horizon is 20 bars, so a
calendar-day label would be measuring a different horizon than the model was
validated on.

**Unknown stays NULL.** An unmatured horizon is not a flat outcome, and an
unmeasurable beta does not become 1.0 — that would quietly turn the honest
column into a copy of the raw one.

The I/O lives in ``tools/label_journal.py``; nothing here fetches or writes.
"""
import datetime as dt

import pandas as pd

HORIZONS = (5, 10, 20)
# A calendar cushion over the longest horizon: 20 trading days is ~28 calendar
# days, plus a few for holidays. Labeling early would freeze a partial answer,
# because `labeled_at` is what stops a row being revisited.
MATURITY_DAYS = 34


def _as_date(d):
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d)[:10])


def _start_index(close, reading_date):
    """Position of the first bar at or after ``reading_date``, or None.

    A reading is stamped with a calendar date, so a Saturday reading prices from
    the next session rather than being dropped.

    ⚠ A reading date BEFORE the series begins returns None rather than bar 0.
    The fetch is a fixed lookback, so a reading older than it would otherwise be
    priced from the wrong start bar and that wrong number stored as a fact —
    the worst outcome available here, since `labeled_at` then stops anyone
    revisiting it."""
    if len(close.index) == 0:
        return None
    when = pd.Timestamp(_as_date(reading_date))
    if when < close.index[0]:
        return None
    pos = close.index.searchsorted(when, side="left")
    return int(pos) if pos < len(close.index) else None


def forward_return(close, reading_date, horizon):
    """Total return over ``horizon`` TRADING bars from ``reading_date``.

    None when the horizon has not matured, or when the series does not reach
    back to the reading."""
    try:
        s = pd.Series(close).dropna()
        if s.empty:
            return None
        i = _start_index(s, reading_date)
        if i is None or i + horizon >= len(s):
            return None
        start, end = float(s.iloc[i]), float(s.iloc[i + horizon])
        if not start:
            return None
        return end / start - 1.0
    except Exception:
        return None


def labels_for(sym_close, spy_close, reading_date, beta=None, horizons=HORIZONS):
    """Every label this reading can support, keyed as ``rec_journal`` expects."""
    out = {"beta": float(beta) if beta is not None else None}
    for h in horizons:
        r_sym = forward_return(sym_close, reading_date, h)
        r_mkt = forward_return(spy_close, reading_date, h)
        out[f"fwd_{h}d"] = (r_sym - r_mkt) if (r_sym is not None
                                               and r_mkt is not None) else None
        out[f"mkt_fwd_{h}d"] = r_mkt
        out[f"fwd_{h}d_ba"] = (
            r_sym - float(beta) * r_mkt
            if (r_sym is not None and r_mkt is not None and beta is not None)
            else None)
    return out


def is_due(reading_date, today=None):
    """Has this reading's LONGEST horizon matured?"""
    today = today or dt.date.today()
    return (today - _as_date(reading_date)).days >= MATURITY_DAYS


def due_before(today=None):
    """ISO cutoff for ``rec_journal.unlabeled(before_date=…)``."""
    today = today or dt.date.today()
    return (today - dt.timedelta(days=MATURITY_DAYS)).isoformat()
