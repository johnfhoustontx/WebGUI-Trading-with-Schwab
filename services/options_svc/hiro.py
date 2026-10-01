"""HIRO-style dealer hedging flow — measurement + detection. PURE (stdlib only).

A MODEL of SpotGamma's HIRO, not SpotGamma's number. Schwab publishes no
time-and-sales tape, so each contract gets ONE buy/sell label per minute, read
from where its latest trade price sits against this minute's bid/ask.
Design: docs/plans/2026-10-01-hiro-alert-design.md.

Hedge impact of a trade = side x delta x contracts x 100 x spot, with the
contract's SIGNED delta: a customer buying a call (+1 x +delta) makes the dealer
buy stock (positive); buying a put (+1 x -delta) makes the dealer sell (negative).
"""
import datetime as _dt
import math
from zoneinfo import ZoneInfo


def _finite(v):
    """A real finite float, or None (rejects bool, NaN, inf, non-numbers)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def classify_side(last, bid, ask) -> int:
    """+1 customer bought, -1 customer sold, 0 unclassified.

    At/through the ask = bought, at/through the bid = sold, otherwise the side of
    the midpoint. Exactly at the midpoint, or any unusable quote, is 0 — a reading
    with no label must never be guessed into one."""
    last, bid, ask = _finite(last), _finite(bid), _finite(ask)
    if last is None or bid is None or ask is None:
        return 0
    if last <= 0 or bid < 0 or ask <= 0 or ask <= bid:   # crossed or LOCKED quote
        return 0
    if last >= ask:
        return 1
    if last <= bid:
        return -1
    mid = (bid + ask) / 2.0
    # A print meant to be AT the mid can miss it by an ulp in binary (0.15 vs
    # (0.10 + 0.20) / 2), and must not be labelled by that rounding.
    if math.isclose(last, mid, rel_tol=1e-9, abs_tol=1e-9):
        return 0
    if last > mid:
        return 1
    if last < mid:
        return -1
    return 0


def _contracts(chain):
    """Yield ``(is_call, contract)`` for every contract dict in a Schwab chain,
    ``is_call`` from the map it came from. Total over malformed input."""
    for mapkey, is_call in (("callExpDateMap", True), ("putExpDateMap", False)):
        exp_map = chain.get(mapkey)
        if not isinstance(exp_map, dict):
            continue
        for strike_map in exp_map.values():
            if not isinstance(strike_map, dict):
                continue
            for contracts in strike_map.values():
                if not isinstance(contracts, list):
                    continue
                for c in contracts:
                    if isinstance(c, dict):
                        yield is_call, c


def measure_chain(chain, prev_vol):
    """One minute of hedge impact for one symbol.

    ``prev_vol`` is ``{contract symbol: totalVolume}`` from the previous poll.
    Returns ``(row, new_prev)``; ``row`` is ``{"spot", "impact",
    "classified_vol", "unclassified_vol"}`` or None when the chain has no usable
    spot (then ``new_prev`` is ``prev_vol`` unchanged, so the next good minute
    books the volume rather than losing it).

    A contract's FIRST reading only seeds the baseline: after a restart it must
    never book the whole day's volume into one minute.

    The stored baseline is a HIGH-WATER mark (``max(vol, before)``): volume
    never falls within a session, so a one-off glitch read of 0 books nothing
    and cannot re-book the day later. A legitimate daily reset is unaffected --
    the caller clears ``prev_vol`` when the session date changes.

    Volume that cannot be turned into impact -- no buy/sell label, an unusable
    delta, or a delta of the wrong sign for its right -- is counted as
    ``unclassified_vol``, so the window never looks better measured than it was.
    The caller's ``prev_vol`` is never mutated."""
    new_prev = dict(prev_vol or {})
    if not isinstance(chain, dict):
        return None, new_prev
    spot = _finite(chain.get("underlyingPrice"))
    if spot is None or spot <= 0:
        return None, new_prev
    impact = classified = unclassified = 0.0
    for is_call, c in _contracts(chain):
        osi = c.get("symbol")
        vol = _finite(c.get("totalVolume"))
        if not osi or vol is None:
            continue
        before = new_prev.get(osi)
        if before is None:
            new_prev[osi] = vol
            continue
        # High-water mark: cumulative volume never falls within a session, so a
        # glitch read (totalVolume = 0) must not reset the baseline and book the
        # contract's whole day into the next minute.
        new_prev[osi] = max(vol, before)
        dv = vol - before
        if dv <= 0:
            continue
        delta = _finite(c.get("delta"))
        if (delta is None or abs(delta) > 1      # NaN / Schwab's -999 sentinel
                or (is_call and delta < 0) or (not is_call and delta > 0)):
            unclassified += dv
            continue
        side = classify_side(c.get("last"), c.get("bid"), c.get("ask"))
        if side == 0:
            unclassified += dv
            continue
        classified += dv
        impact += side * delta * dv * 100.0 * spot
    return ({"spot": spot, "impact": impact, "classified_vol": classified,
             "unclassified_vol": unclassified}, new_prev)


# --- Surge ------------------------------------------------------------------
# Rows are the stored ``hiro_minutes`` rows, ASC by ts. A seeding minute is never
# stored, so every row is a real measurement. A missing minute (restart gap)
# simply contributes nothing to a wall-clock window.

def window_sum(rows, end_ts, window_sec):
    """Sum of the rows with ``end_ts - window_sec < ts <= end_ts``."""
    w = [r for r in rows if end_ts - window_sec < r["ts"] <= end_ts]
    impact = sum(r["impact"] for r in w)
    cls = sum(r["classified_vol"] for r in w)
    uncl = sum(r["unclassified_vol"] for r in w)
    total = cls + uncl
    return {"impact": impact, "n": len(w),
            "unclassified_share": (uncl / total) if total > 0 else None}


def full_window_sums(rows, window_sec):
    """The window sum ending at every row whose window lies wholly inside the
    session (the first ``window`` minutes give partial sums, which would shrink
    the baseline). "Inside" is measured from the first STORED row. Rows are ASC.
    O(n^2) -- every window rescans all rows -- fine for ~390 rows a session."""
    if not rows:
        return []
    first = rows[0]["ts"]
    return [window_sum(rows, r["ts"], window_sec)["impact"]
            for r in rows if r["ts"] - first >= window_sec - 60]


def rms(values):
    """Root-mean-square of the finite values, or None (none, or all zero).
    RMS, not a standard deviation: hedging flow's centre is zero, and a
    day-long drift is the signal, not noise to subtract."""
    vals = [v for v in (_finite(x) for x in values) if v is not None]
    if not vals:
        return None
    s = math.sqrt(sum(v * v for v in vals) / len(vals))
    return s if s > 0 else None


def baseline_sigma(prior_sessions, today_rows, cfg):
    """A symbol's normal 15-minute size. Prior sessions (newest first) when at
    least ``baseline_sessions`` exist; else today's full windows once there are
    ``min_minutes`` rows; else None (the rules do not run)."""
    window = int(cfg["window_min"]) * 60
    need = int(cfg["baseline_sessions"])
    if len(prior_sessions) >= need:
        sigma = rms([v for s in prior_sessions[:need] for v in full_window_sums(s, window)])
        if sigma is not None:
            return sigma
        # Prior sessions with no usable full window (a broken collection day,
        # or all-NaN rows) fall through to today rather than silencing the day.
    if len(today_rows) >= int(cfg["min_minutes"]):
        return rms(full_window_sums(today_rows, window))
    return None


# Rows stop at the 15:00 CT close, but detection keeps running after it, and a
# stalled collector freezes the newest row: without a clock check the same old
# window would re-fire every time its cooldown lapsed. Two poll intervals.
FRESH_ROW_SEC = 120


def detect_surge(symbol, today_rows, sigma, cfg, *, now_ts=None):
    """A ``hiro_surge`` alert dict for the window ending at the latest row, or
    None. No cooldown here — the handler owns that, as for every flow detector.

    With ``now_ts``, the newest row must be at most FRESH_ROW_SEC old against
    the clock. A non-finite window or sigma never fires: NaN fails every
    comparison, so unguarded it would pass the floor and the multiple both."""
    sigma = _finite(sigma)
    if not today_rows or sigma is None or sigma <= 0:
        return None
    last = today_rows[-1]
    if now_ts is not None and now_ts - last["ts"] > FRESH_ROW_SEC:
        return None
    w = window_sum(today_rows, last["ts"], int(cfg["window_min"]) * 60)
    share = w["unclassified_share"]
    if share is None or share > cfg["max_unclassified"]:
        return None
    imp = w["impact"]
    if not math.isfinite(imp) or abs(imp) < cfg["min_notional"]:
        return None
    mult = abs(imp) / sigma
    if mult < cfg["k"]:
        return None
    return {"type": "hiro_surge",
            "side": "dealers_buying" if imp > 0 else "dealers_selling",
            "symbol": symbol, "ts": last["ts"], "spot": last["spot"],
            "impact": imp, "mult": mult, "window_min": int(cfg["window_min"]),
            "unclassified_share": share}


# --- Flip (reversal) ---------------------------------------------------------

_CT = ZoneInfo("America/Chicago")
FLIP_MAX_AGE_SEC = 120     # a transition older than this is history, not news


def ct_ts(day, hhmm) -> int:
    """Unix seconds of ``hhmm`` Central on ``day`` ('YYYY-MM-DD' or a date)."""
    if isinstance(day, str):
        day = _dt.date.fromisoformat(day)
    h, m = (int(x) for x in str(hhmm).split(":"))
    return int(_dt.datetime(day.year, day.month, day.day, h, m, tzinfo=_CT).timestamp())


def _state(cum, prev, band):
    if prev == "buying":
        return "selling" if cum <= -band else "buying"
    if prev == "selling":
        return "buying" if cum >= band else "selling"
    if cum >= band:
        return "buying"
    if cum <= -band:
        return "selling"
    return None


def _row_impact(row):
    """A row's impact if it is a usable number, else None. The ONE skip rule for
    the day's running total, shared by the reversal rule and the screen."""
    return _finite(row.get("impact"))


def running_total(rows):
    """The day's net hedge impact so far, skipping non-finite minutes (one NaN
    added would make the total NaN for the rest of the day)."""
    return sum((v for v in (_row_impact(r) for r in rows) if v is not None), 0.0)


def flip_transitions(rows, band, not_before_ts):
    """``[(ts, new_state, cum)]`` for every change of the day's hedging direction.

    The running total counts from the session's FIRST row; only the evaluation
    waits for ``not_before_ts``. The first state reached is the baseline and is
    not a transition. Hysteresis: a state changes only when the total clears zero
    by ``band`` on the other side.

    A non-finite minute is skipped (``_row_impact``, the rule ``running_total``
    uses): added, it would make the total NaN for the rest of the day, every
    later comparison False, and the state frozen."""
    out, state, cum = [], None, 0.0
    for r in rows:
        imp = _row_impact(r)
        if imp is None:
            continue
        cum += imp
        if r["ts"] < not_before_ts:
            continue
        new = _state(cum, state, band)
        if new is None:
            continue
        if state is not None and new != state:
            out.append((r["ts"], new, cum))
        state = new
    return out


def detect_flip(symbol, rows, sigma, cfg, not_before_ts, seen_ts, *, now_ts=None):
    """A ``hiro_flip`` alert for the latest transition, or None.

    Stateless: replays today's rows every tick. Fires only for a transition
    newer than ``seen_ts`` AND at most FLIP_MAX_AGE_SEC old, so a restart cannot
    fire an old flip and an intraday-moving sigma cannot surface one. The age is
    measured against ``now_ts`` when given (the clock), else the newest row."""
    sigma = _finite(sigma)
    if not rows or sigma is None or sigma <= 0:
        return None
    t = flip_transitions(rows, float(cfg["flip_band"]) * sigma, not_before_ts)
    if not t:
        return None
    ts, state, cum = t[-1]
    if seen_ts is not None and ts <= seen_ts:
        return None
    ref = rows[-1]["ts"] if now_ts is None else now_ts
    if ref - ts > FLIP_MAX_AGE_SEC:
        return None
    spot = next((r["spot"] for r in rows if r["ts"] == ts), None)
    return {"type": "hiro_flip", "side": "to_buying" if state == "buying" else "to_selling",
            "symbol": symbol, "ts": ts, "spot": spot, "cum": cum}


def symbol_view(rows, sigma, cfg):
    """The small per-symbol summary published to cache:options:hiro.

    A non-finite figure is published as None, never as a number: a NaN in the
    view would read as a reading on screen. ``cum`` is ``running_total``, the
    same total the reversal rule uses, so it skips a bad minute rather than
    going None; ``window_impact`` stays None while a bad minute is in it."""
    last = rows[-1]
    w = window_sum(rows, last["ts"], int(cfg["window_min"]) * 60)
    window_impact = _finite(w["impact"])
    sigma = _finite(sigma)
    mult = (_finite(abs(window_impact) / sigma)
            if window_impact is not None and sigma is not None and sigma > 0 else None)
    return {"ts": last["ts"], "spot": last["spot"], "impact": _finite(last["impact"]),
            "cum": running_total(rows),
            "window_impact": window_impact, "sigma": sigma, "mult": mult,
            "unclassified_share": w["unclassified_share"]}
