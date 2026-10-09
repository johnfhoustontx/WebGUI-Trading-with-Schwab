"""How much of the Flow ribbon, the Net Prem line and the crossover alert is
re-marking rather than trading.

The collector stores each symbol's call and put premium as
``Σ mark × totalVolume × 100`` (``flow_skew.index_call_put_premium``): the day's
volume at the CURRENT mark. The Flow view draws those two lines as a ribbon, Net
Prem draws their difference, and the ``crossover`` flow alert fires when the
difference changes sign. A traded total cannot fall, so wherever a line falls
the move is the mark, not a trade. This measures, for stored sessions:

* each ribbon line: how often it falls, and falls against rises;
* the net line: the share of its minute-to-minute movement that is CERTAINLY
  re-marking (the part that comes from a side falling), and how closely it
  simply follows the underlying's price;
* the alert: every crossover the real detector would have fired, and how many
  would not have fired had neither side fallen in that minute.

With ``--traded`` it also sets the stored figure beside the ``tprem`` view
(``services/options_svc/traded_premium.py``), where one has been collected: from
the view's first row, the stored line's change against what actually traded.

Read-only. Usage, from the repo root::

    .venv/bin/python tools/measure_flow_remark.py SPX SPY NVDA --date 2026-10-07
    .venv/bin/python tools/measure_flow_remark.py --all --date 2026-10-07
    .venv/bin/python tools/measure_flow_remark.py SPX --traded
    .venv/bin/python tools/measure_flow_remark.py SPX --date 2026-09-24 --db /path/to/gex_history.db

A symbol may be typed without its ``$``. ``--db`` reads a COPY of the database
(a backup) immutably, which is only safe for a file nothing is writing.

⚠ The bounds are one-sided. A fall proves re-marking; a rise may hide some
(marks rising on old volume look like new premium). So the "certain" shares here
are floors, and only the ``tprem`` comparison measures the whole of it.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gex_history_db as gh  # noqa: E402

from services.options_svc import flow_alerts  # noqa: E402
from shared.market_calendar import _session_bounds, window_bounds  # noqa: E402
from shared.numeric import finite  # noqa: E402
from tools import measure_prem_remark as remark  # noqa: E402

TRADED_VIEW = "tprem"
# The flow-series row: (ts, spot, call_vol, put_vol, call_prem, put_prem).
TS, SPOT, CALL_VOL, PUT_VOL, CALL, PUT = range(6)


def usable(rows):
    """The rows the ribbon draws: a timestamp and BOTH premiums, in time order."""
    out = [r for r in rows or ()
           if len(r) > PUT and finite(r[TS]) is not None
           and finite(r[CALL]) is not None and finite(r[PUT]) is not None]
    return sorted(out, key=lambda r: r[TS])


def line_stats(values):
    """One ribbon line: how often it falls, and falls against rises."""
    diffs = [b - a for a, b in zip(values, values[1:])]
    risen = sum(d for d in diffs if d > 0)
    fallen = -sum(d for d in diffs if d < 0)
    return {"steps": len(diffs),
            "fell_pct": _pct(sum(1 for d in diffs if d < 0), len(diffs)),
            "risen": risen, "fallen": fallen,
            "fallen_pct_of_risen": _pct(fallen, risen),
            "peak": max(values) if values else None,
            "last": values[-1] if values else None}


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def _corr(xs, ys):
    """Pearson's r, or None with too little to say."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy), 2)


def net_stats(rows):
    """The net line (call minus put) over a session.

    ``certain_remark_pct``: of the line's total minute-to-minute movement, the
    share that comes from a side FALLING. Each step is ``Δcall − Δput``; the
    part ``min(Δcall, 0) − min(Δput, 0)`` exists only because a figure that
    cannot fall fell, so it is re-marking beyond argument. A floor, not the
    whole of it.

    ``with_price_pct`` / ``price_corr``: how the net's steps line up with the
    underlying's. A call's mark rises and a put's falls when price rises, so a
    net of re-marked premium follows price with no trade at all.
    ``volume_price_corr`` is the same for net CONTRACTS (call volume minus put
    volume, which cannot be re-marked), for contrast."""
    path = certain = 0.0
    d_net, d_spot, d_vol = [], [], []
    for a, b in zip(rows, rows[1:]):
        dc, dp = b[CALL] - a[CALL], b[PUT] - a[PUT]
        path += abs(dc - dp)
        certain += abs(min(dc, 0.0) - min(dp, 0.0))
        s0, s1 = finite(a[SPOT]), finite(b[SPOT])
        if s0 is None or s1 is None:
            continue
        d_net.append(dc - dp)
        d_spot.append(s1 - s0)
        volumes = [finite(v) for v in (a[CALL_VOL], a[PUT_VOL], b[CALL_VOL], b[PUT_VOL])]
        d_vol.append(None if None in volumes
                     else (volumes[2] - volumes[0]) - (volumes[3] - volumes[1]))
    moved = [(n, s) for n, s in zip(d_net, d_spot) if n and s]
    with_volume = [(v, s) for v, s in zip(d_vol, d_spot) if v is not None]
    return {"steps": max(len(rows) - 1, 0),
            "certain_remark_pct": _pct(certain, path),
            "with_price_pct": _pct(sum(1 for n, s in moved if (n > 0) == (s > 0)),
                                   len(moved)),
            "price_corr": _corr(d_net, d_spot),
            "volume_price_corr": _corr([v for v, _s in with_volume],
                                       [s for _v, s in with_volume])}


def _alert_row(row, call=None, put=None):
    return {"ts": row[TS], "call_vol": 0, "put_vol": 0,
            "call_prem": row[CALL] if call is None else call,
            "put_prem": row[PUT] if put is None else put}


def replay(rows, *, band, min_premium, cooldown_sec):
    """Every crossover the REAL detector would have fired over ``rows``, with
    its cooldown, and what each owes to a fall.

    The detector (``flow_alerts._crossover_rows``) compares the last two rows,
    so it is run on each consecutive pair. ``needs_fall``: the same pair with
    each side's fall removed (a side that fell is held at its previous value)
    does NOT fire. Such an alert reported a flip that no trade made.

    The opposite, a fall HIDING a crossover that trading made, cannot be read
    off the stored lines: removing falls only raises both sides, so it never
    creates a crossing the stored pair lacks. Only the traded view can show it
    (``against_traded``)."""
    alerts, last = [], None
    for a, b in zip(rows, rows[1:]):
        prev = _alert_row(a)
        stored = flow_alerts._crossover_rows([prev, _alert_row(b)], band, min_premium)
        floored = flow_alerts._crossover_rows(
            [prev, _alert_row(b, call=max(a[CALL], b[CALL]), put=max(a[PUT], b[PUT]))],
            band, min_premium)
        if stored is None:
            continue
        if last is not None and b[TS] - last < cooldown_sec:
            continue
        last = b[TS]
        alerts.append({"ts": b[TS], "side": stored["side"],
                       "needs_fall": floored is None})
    return {"alerts": alerts}


def crossover_settings():
    """The live detector's own settings (``config/flow_alerts.toml``)."""
    xo = flow_alerts.section(flow_alerts.load_thresholds(), "crossover")
    defaults = flow_alerts._DEFAULTS["crossover"]

    def value(key):
        got = finite(xo.get(key))
        return defaults[key] if got is None else got

    return {"band": value("band"), "min_premium": value("min_premium"),
            "cooldown_sec": value("cooldown_min") * 60}


def against_traded(rows, traded, settings=None):
    """The stored lines set beside what traded, from the traded view's first
    row. ``traded`` is ``[(ts, call, put), …]``, a running total from zero.

    With ``settings`` the alert is replayed on both over the shared minutes:
    ``stored_alerts`` and ``traded_alerts``. The two are comparable only when
    the traded view was collecting from the open, because a crossover is about
    LEVELS and the traded total starts at zero wherever it starts.

    The stored figure is day-cumulative, so only its CHANGE since that first
    row is comparable. ``remark`` is that change less what traded: the whole
    re-marking of the day's volume over the stretch, falls and rises alike.
    None when the two share fewer than two timestamps."""
    by_ts = {r[TS]: r for r in rows}
    common = [t for t in traded if t[0] in by_ts]
    if len(common) < 2:
        return None
    first, last = by_ts[common[0][0]], by_ts[common[-1][0]]
    base = common[0]
    out = {"from_ts": first[TS], "to_ts": last[TS], "rows": len(common)}
    for side, idx, col in (("call", CALL, 1), ("put", PUT, 2)):
        stored = last[idx] - first[idx]
        booked = common[-1][col] - base[col]
        out[side] = {"stored_change": stored, "traded": booked,
                     "remark": stored - booked}
    out["net"] = {k: out["call"][k] - out["put"][k]
                  for k in ("stored_change", "traded", "remark")}
    # Minute by minute: does the stored net step the way the traded net did?
    steps = agree = 0
    stored_steps, traded_steps, spot_steps = [], [], []
    for a, b in zip(common, common[1:]):
        ra, rb = by_ts[a[0]], by_ts[b[0]]
        d_stored = (rb[CALL] - ra[CALL]) - (rb[PUT] - ra[PUT])
        d_traded = (b[1] - a[1]) - (b[2] - a[2])
        if d_stored and d_traded:
            steps += 1
            agree += (d_stored > 0) == (d_traded > 0)
        s0, s1 = finite(ra[SPOT]), finite(rb[SPOT])
        if s0 is not None and s1 is not None:
            stored_steps.append(d_stored)
            traded_steps.append(d_traded)
            spot_steps.append(s1 - s0)
    out["net_steps_agree_pct"] = _pct(agree, steps)
    out["stored_price_corr"] = _corr(stored_steps, spot_steps)
    out["traded_price_corr"] = _corr(traded_steps, spot_steps)
    if settings:
        out["stored_alerts"] = replay([by_ts[t[0]] for t in common], **settings)["alerts"]
        out["traded_alerts"] = replay(
            [(t[0], None, 0, 0, t[1], t[2]) for t in common], **settings)["alerts"]
    return out


# ── printing ─────────────────────────────────────────────────────────────────
def _money(v):
    return "n/a" if v is None else f"${v / 1e6:,.1f}M"


def _clock(ts):
    return _dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def report(symbol, date, rows, settings, alert_rows=None):
    """``rows`` are the regular session's; ``alert_rows`` the rows the detector
    sees (default: the same)."""
    if len(rows) < 2:
        return [f"{symbol} {date}: fewer than two flow rows in the regular session"]
    lines = [f"{symbol} {date}: {len(rows)} rows"]
    for name, idx in (("calls", CALL), ("puts", PUT)):
        s = line_stats([r[idx] for r in rows])
        lines.append(f"  {name:5} fell in {s['fell_pct']:.1f}% of minutes; fell "
                     f"{_money(s['fallen'])} against {_money(s['risen'])} risen "
                     f"({s['fallen_pct_of_risen']:.1f}%); peak {_money(s['peak'])}, "
                     f"last {_money(s['last'])}")
    n = net_stats(rows)
    lines.append(f"  net   at least {n['certain_remark_pct']:.1f}% of its movement is "
                 f"re-marking; steps with price {n['with_price_pct']:.1f}% of the time "
                 f"(r = {n['price_corr']}); net contracts r = {n['volume_price_corr']}")
    r = replay(rows if alert_rows is None else alert_rows, **settings)
    needs = [a for a in r["alerts"] if a["needs_fall"]]
    lines.append(f"  alert {len(r['alerts'])} crossover alert(s), {len(needs)} needing a "
                 f"fall to fire")
    lines += [f"          {_clock(a['ts'])} {a['side']}"
              f"{'  <- would not have fired without a fall' if a['needs_fall'] else ''}"
              for a in r["alerts"]]
    return lines


def report_traded(symbol, cmp):
    if cmp is None:
        return [f"  traded: no {TRADED_VIEW} rows to compare for {symbol}"]
    lines = [f"  traded, {_clock(cmp['from_ts'])} to {_clock(cmp['to_ts'])} "
             f"({cmp['rows']} rows):"]
    for side in ("call", "put", "net"):
        s = cmp[side]
        lines.append(f"    {side:4} stored line moved {_money(s['stored_change'])}; "
                     f"traded {_money(s['traded'])}; re-marking {_money(s['remark'])}")
    lines.append(f"    net steps agree in direction {cmp['net_steps_agree_pct']:.1f}% of "
                 f"minutes; with price: stored r = {cmp['stored_price_corr']}, "
                 f"traded r = {cmp['traded_price_corr']}")
    if "traded_alerts" in cmp:
        def times(alerts):
            return ", ".join(f"{_clock(a['ts'])} {a['side']}" for a in alerts) or "none"
        lines.append(f"    alert on the stored lines: {times(cmp['stored_alerts'])}")
        lines.append(f"    alert on the traded lines: {times(cmp['traded_alerts'])}"
                     f"  (comparable only if the view began at the open; it began "
                     f"{_clock(cmp['from_ts'])})")
    return lines


def summary(date, per_symbol):
    """The alert across every symbol measured: ``{symbol: replay result}``."""
    alerts = [(sym, a) for sym, r in per_symbol.items() for a in r["alerts"]]
    needs = [x for x in alerts if x[1]["needs_fall"]]
    return [f"ALL {date}: {len(per_symbol)} symbols, {len(alerts)} crossover alert(s); "
            f"{len(needs)} ({_pct(len(needs), len(alerts)):.1f}%) would not have fired "
            f"without a fall"]


# ── reading ──────────────────────────────────────────────────────────────────
def _between(rows, bounds):
    start, end = bounds
    return [r for r in rows
            if start <= _dt.datetime.fromtimestamp(r[TS]).time() <= end]


def _regular(rows):
    """What the page draws: the regular session."""
    return _between(rows, _session_bounds("regular"))


def _alert_window(rows):
    """When the live detector runs: the collection window (08:00 to 15:20 CT as
    shipped), which is wider than the regular session."""
    return _between(rows, window_bounds("collection"))


def symbols_on(conn, day):
    start, end = gh._local_unix_range(day)
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM snapshots "
        "WHERE view = 'gex' AND ts >= ? AND ts < ? ORDER BY symbol", (start, end))]


def traded_rows(conn, symbol, day):
    start, end = gh._local_unix_range(day)
    return [r for r in conn.execute(
        "SELECT ts, call_prem, put_prem FROM snapshots "
        "WHERE symbol = ? AND view = ? AND ts >= ? AND ts < ? ORDER BY ts",
        (symbol, TRADED_VIEW, start, end))
        if finite(r[1]) is not None and finite(r[2]) is not None]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--all", action="store_true",
                    help="every symbol collected that day: the alert summary only")
    ap.add_argument("--date", help="session date, YYYY-MM-DD (default: today)")
    ap.add_argument("--db", help="read this COPY of gex_history.db, immutably")
    ap.add_argument("--traded", action="store_true",
                    help=f"also compare with the {TRADED_VIEW} view")
    args = ap.parse_args(argv)
    if not args.symbols and not args.all:
        ap.error("name at least one symbol, or pass --all")
    day = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    settings = crossover_settings()
    try:
        conn = remark._connect(args.db)
    except sqlite3.Error as e:
        print(f"Cannot open the history database read-only: {e}", file=sys.stderr)
        return 2
    try:
        def load(symbol):
            return usable(gh.load_flow_series(conn, symbol, day))

        for typed in args.symbols:
            name, every = remark.load_symbol(load, typed)
            rows = _regular(every)
            print("\n".join(report(name, day.isoformat(), rows, settings,
                                   _alert_window(every))))
            if args.traded:
                print("\n".join(report_traded(
                    name, against_traded(rows, traded_rows(conn, name, day),
                                         settings))))
        if args.all:
            per_symbol = {}
            for symbol in symbols_on(conn, day):
                rows = _alert_window(load(symbol))
                if len(rows) >= 2:
                    per_symbol[symbol] = replay(rows, **settings)
            print("\n".join(summary(day.isoformat(), per_symbol)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
