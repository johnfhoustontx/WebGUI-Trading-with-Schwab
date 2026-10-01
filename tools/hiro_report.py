"""Daily validation report for the HIRO-model hedging-flow alerts.

WHY THIS EXISTS
---------------
``hiro_surge`` and ``hiro_flip`` (services/options_svc/hiro.py) model SpotGamma's
HIRO from a once-a-minute chain poll. They are a MODEL, never validated, so they
ship quiet: Flow screen only, no phone push, no Desk speech, not public, not
counted in Hotness. This report is what decides whether that ever changes
(docs/plans/2026-10-01-hiro-alert-design.md section 3): per symbol, how often the
Surge would fire at several multiples ``k``, how much of the day's volume could
not be labelled buy or sell, and whether price moved the way the modelled dealer
hedging pushed it in the 5 and 15 minutes after each fire -- measured against
that day's BASE RATE for the same direction, not against a coin flip, and pooled
over the stored sessions, because one day's handful of fires decides nothing.

It REPLAYS the live rules minute by minute -- ``hiro.detect_surge`` and
``hiro.detect_flip`` with the clock set to that minute, the handler's sigma rule
(``handlers._hiro_sigma``: the prior sessions, else today's minutes AS LIVE HAD
THEM at that minute) and its cooldowns -- and re-implements none of them, so the
report cannot drift from what the Flow screen showed.

USAGE
-----
    .venv/bin/python tools/hiro_report.py                     # the latest closed session
    .venv/bin/python tools/hiro_report.py --date 2026-09-30   # re-run a past day
    .venv/bin/python tools/hiro_report.py --date 2026-10-03 --force

Reads ``options-scanner/gex_history.db`` read-only and nothing else (no proxy,
no Redis, no secrets), so a missed day can be re-run later with ``--date`` for as
long as ``[hiro].keep_sessions`` keeps its minutes. Skips with exit 0 on a
weekend or NYSE holiday given with ``--date`` unless ``--force``. Writes
``options-scanner/data/hiro_report/<date>/report.md`` (``--out`` replaces the
``hiro_report`` directory). Exits 1 when nothing was measured: every symbol
failed, or a trading day has no stored minute for any symbol (a dead collector).

Scheduled daily by ``trading-<env>-hiro-report.timer`` at ``[slots.hiro_report]``.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import math
import pathlib
import sys
from zoneinfo import ZoneInfo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gex_history_db as gh  # noqa: E402

from services.options_svc import flow_alerts, hiro  # noqa: E402
from shared.market_calendar import (_session_bounds, is_trading_day,  # noqa: E402
                                    prev_trading_day)

CT = ZoneInfo("America/Chicago")

# The multiples of a symbol's normal 15-minute size to replay the Surge at, and
# the forward horizons (minutes) to score each fire over. Analysis parameters,
# not alert settings -- and module constants, not config/flow_alerts.toml, on
# purpose: Settings -> Configuration has no field kind for a list of numbers
# (``symbols`` would upper-case them into strings and fail the catalogue's
# round-trip test; a read-only section is reserved to news.toml). Override per
# run with --k / --horizons instead.
K_GRID = (2.0, 2.5, 3.0, 3.5, 4.0, 5.0)
FORWARD_MIN = (5, 15)

# A forward move reads the first row at or after the horizon; one further than
# this past it means a collection gap, and the horizon would silently stretch.
_HORIZON_SLACK_SEC = 120

# A hit rate is printed as a percentage only from this many decided fires; below
# it the bare "k of n" is the honest statement.
MIN_N_FOR_PCT = 10

_UP = {"dealers_buying": 1, "to_buying": 1}
_DOWN = {"dealers_selling": -1, "to_selling": -1}
SURGE_SIDES = ("dealers_buying", "dealers_selling")
FLIP_SIDES = ("to_buying", "to_selling")

DASH = "—"


def _finite(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _usable_sigma(sigma):
    s = _finite(sigma)
    return s if s is not None and s > 0 else None


def load_cfg() -> dict:
    """The live ``[hiro]`` table, merged over its defaults."""
    return dict(flow_alerts.load_thresholds()["hiro"])


# --- sigma, as live had it ----------------------------------------------------

def live_sigmas(rows, cfg):
    """``[sigma at minute i]`` from today's rows alone: exactly
    ``hiro.baseline_sigma([], rows[:i + 1], cfg)`` (None before ``min_minutes``),
    which is what the handler falls back to when the prior sessions give none.

    Built from hiro's own ``window_sum`` and ``rms`` over the same full windows
    ``full_window_sums`` selects, kept incrementally -- re-running
    ``baseline_sigma`` on every prefix is cubic. A test pins the equality on
    every prefix."""
    if not rows:
        return []
    window = int(cfg["window_min"]) * 60
    need = int(cfg["min_minutes"])
    first = rows[0]["ts"]
    sums, out, lo = [], [], 0
    for i, r in enumerate(rows):
        end = r["ts"]
        while rows[lo]["ts"] <= end - window:
            lo += 1
        if end - first >= window - 60:
            sums.append(hiro.window_sum(rows[lo:i + 1], end, window)["impact"])
        out.append(_usable_sigma(hiro.rms(sums)) if i + 1 >= need else None)
    return out


def _sigma_at(sigma, i):
    return sigma[i] if isinstance(sigma, (list, tuple)) else sigma


def _sigmas(rows, prior_sessions, cfg):
    """``(per-minute sigma or a constant, source, headline sigma)`` by the
    handler's rule: a usable prior-session sigma (constant all day), else
    today's minutes as live had them at each minute (``live_sigmas``)."""
    p = hiro.prior_sigma(prior_sessions, cfg)
    if isinstance(p, float) and math.isfinite(p) and p > 0:
        return p, "prior", p
    per = live_sigmas(rows, cfg)
    final = next((s for s in reversed(per) if s is not None), None)
    return (per, "today", final) if final is not None else (None, None, None)


# --- replay ------------------------------------------------------------------

def replay_surges(rows, sigma, cfg, k):
    """Every surge the live rule would have fired at bar k, minute by minute,
    honouring the per-direction cooldown -- detect_surge over each prefix with
    now_ts = that minute (so freshness behaves as live).

    ``sigma`` is a constant, or a list giving the sigma live had at each minute.
    The cooldown is the handler's (``flow_alerts._on_cooldown``). Each prefix is
    cut to the rows inside the window before it reaches ``detect_surge`` -- the
    rule reads only the newest row and the window ending there, so the answer is
    identical and the replay stays linear."""
    if not rows:
        return []
    if not isinstance(sigma, (list, tuple)) and _usable_sigma(sigma) is None:
        return []
    c = {**cfg, "k": float(k)}
    window = int(c["window_min"]) * 60
    cool = float(c["cooldown_min"]) * 60
    last, out, lo = {}, [], 0
    for i, r in enumerate(rows):
        end = r["ts"]
        while rows[lo]["ts"] <= end - window:
            lo += 1
        a = hiro.detect_surge("", rows[lo:i + 1], _sigma_at(sigma, i), c, now_ts=end)
        if a and not flow_alerts._on_cooldown(last, a["side"], end, cool):
            last[a["side"]] = end
            out.append(a)
    return out


def replay_flips(rows, sigma, cfg, not_before_ts):
    """The reversals the live rule would have fired: ``hiro.detect_flip`` on
    every minute's prefix with the clock at that minute and the seen marker
    carried forward, then the symbol's one ``flip_cooldown_min`` -- the
    handler's sequence (seen is set whether or not the cooldown lets it fire)."""
    if not rows or not_before_ts is None:
        return []
    if not isinstance(sigma, (list, tuple)) and _usable_sigma(sigma) is None:
        return []
    cool = float(cfg["flip_cooldown_min"]) * 60
    seen, cooldowns, out = None, {}, []
    for i, r in enumerate(rows):
        f = hiro.detect_flip("", rows[:i + 1], _sigma_at(sigma, i), cfg,
                             not_before_ts, seen, now_ts=r["ts"])
        if not f:
            continue
        seen = f["ts"]
        if not flow_alerts._on_cooldown(cooldowns, "flip", r["ts"], cool):
            cooldowns["flip"] = r["ts"]
            out.append(f)
    return out


# --- forward moves --------------------------------------------------------------

def _spot_move(rows, ts_list, ts, spot0, minutes):
    """Raw spot move ``minutes`` after ``ts`` from ``spot0``: the first row at or
    after the horizon, within the slack. None past the session end, across a
    gap, or on an unusable spot. The ONE rule both fires and the base rate use."""
    s0 = _finite(spot0)
    if s0 is None or s0 <= 0:
        return None
    target = ts + int(minutes) * 60
    j = bisect.bisect_left(ts_list, target)
    if j >= len(rows) or ts_list[j] - target > _HORIZON_SLACK_SEC:
        return None
    s1 = _finite(rows[j].get("spot"))
    if s1 is None or s1 <= 0:
        return None
    return s1 / s0 - 1.0


def forward_return(rows, fire, minutes):
    """Spot return `minutes` after a fire, signed so + means price moved the way
    the dealers' hedging pushed (dealers_buying / to_buying = up). None when the
    session ends before the horizon or a spot is unusable."""
    sign = _UP.get(fire.get("side")) or _DOWN.get(fire.get("side"))
    if sign is None:
        return None
    m = _spot_move(rows, [r["ts"] for r in rows], fire["ts"], fire.get("spot"), minutes)
    return None if m is None else sign * m


def base_counts(rows, horizons):
    """``{h: {"up", "down", "flat"}}`` over EVERY measured minute: how often the
    price simply rose or fell over each horizon that day, by ``_spot_move``."""
    ts_list = [r["ts"] for r in rows]
    out = {}
    for h in horizons:
        c = {"up": 0, "down": 0, "flat": 0}
        for r in rows:
            m = _spot_move(rows, ts_list, r["ts"], r.get("spot"), h)
            if m is None:
                continue
            c["up" if m > 0 else "down" if m < 0 else "flat"] += 1
        out[h] = c
    return out


def tally(fires, side, h):
    """``{hits, n, flat, mean}`` for the fires on one side at horizon ``h``.
    A tie (return exactly 0) is neither hit nor miss: it is left out of ``n``
    and counted in ``flat``. ``mean`` is over every return, ties included."""
    vals = [f["fwd"].get(h) for f in fires if f.get("side") == side]
    vals = [v for v in vals if v is not None]
    hits = sum(1 for v in vals if v > 0)
    misses = sum(1 for v in vals if v < 0)
    return {"hits": hits, "n": hits + misses, "flat": len(vals) - hits - misses,
            "mean": (sum(vals) / len(vals)) if vals else None}


# --- per symbol ------------------------------------------------------------------

def _unclassified(rows):
    cls = sum(v for v in (_finite(r.get("classified_vol")) for r in rows) if v is not None)
    unc = sum(v for v in (_finite(r.get("unclassified_vol")) for r in rows) if v is not None)
    total = cls + unc
    return unc / total if total > 0 else None


def event_row(fire, rows, horizons):
    """One line of the per-fire table: time, kind, direction, size, returns."""
    surge = fire["type"] == "hiro_surge"
    return {"ts": fire["ts"], "side": fire["side"],
            "kind": "Surge" if surge else "Reversal",
            "size": (f"{fire['mult']:.1f}× normal" if surge
                     else f"day net {_money(_finite(fire.get('cum')))}"),
            "fwd": {h: forward_return(rows, fire, h) for h in horizons}}


def _with_fwd(fires, rows, horizons):
    return [{**f, "fwd": {h: forward_return(rows, f, h) for h in horizons}}
            for f in fires]


def symbol_summary(rows, prior_sessions, cfg, k_grid, horizons, not_before_ts):
    """One symbol-day: {rows, sigma, sigma_source ('prior' | 'today' | None),
    unclassified (share of the day's volume), fires_by_k, flips, surges and
    reversals (live-k fires, each with its forward returns), base (the day's
    base rate counts), events (the per-fire table), fwd: {k_live: {h: [returns]}},
    flip_fwd: {h: [returns]}}.

    The live ``k`` and ``push_k`` are always in the grid. With no sigma the rules
    do not run, so every count is None (not measured), never 0. A None
    ``not_before_ts`` (a malformed ``flip_not_before``) skips the reversals, as
    the handler does."""
    k_live = float(cfg["k"])
    grid = sorted({float(k) for k in k_grid} | {k_live, float(cfg["push_k"])})
    horizons = [int(h) for h in horizons]
    sigma, source, headline = _sigmas(rows, prior_sessions, cfg)
    out = {"rows": len(rows), "sigma": headline, "sigma_source": source,
           "unclassified": _unclassified(rows), "k_live": k_live,
           "push_k": float(cfg["push_k"]), "horizons": horizons,
           "flips_skipped": not_before_ts is None,
           "flips_live": cfg.get("flip_enabled") is True,
           "base": base_counts(rows, horizons)}
    if sigma is None:
        out.update(fires_by_k={k: None for k in grid}, flips=None, surges=[],
                   reversals=[], events=[], fwd={k_live: {h: [] for h in horizons}},
                   flip_fwd={h: [] for h in horizons})
        return out
    fires = {k: replay_surges(rows, sigma, cfg, k) for k in grid}
    surges = _with_fwd(fires[k_live], rows, horizons)
    flips = _with_fwd(replay_flips(rows, sigma, cfg, not_before_ts), rows, horizons)
    out.update(
        fires_by_k={k: len(v) for k, v in fires.items()},
        flips=None if not_before_ts is None else len(flips),
        surges=surges, reversals=flips,
        events=[event_row(f, rows, horizons)
                for f in sorted(surges + flips, key=lambda f: f["ts"])],
        fwd={k_live: {h: [f["fwd"][h] for f in surges if f["fwd"][h] is not None]
                      for h in horizons}},
        flip_fwd={h: [f["fwd"][h] for f in flips if f["fwd"][h] is not None]
                  for h in horizons})
    return out


def _session_date(rows):
    return _dt.datetime.fromtimestamp(rows[0]["ts"], CT).date()


def session_summaries(today_rows, history, cfg, k_grid, horizons, not_before_for):
    """``[today, the session before, ...]`` -- up to ``keep_sessions`` in all --
    each summarised by ``symbol_summary`` with the prior sessions live had on
    THAT day (the ``baseline_sessions`` before it in ``history``, newest first).
    ``not_before_for(rows)`` gives a session's reversal start (or None)."""
    need = int(cfg["baseline_sessions"])
    keep = max(1, int(cfg.get("keep_sessions", 1)))
    out = [symbol_summary(today_rows, history[:need], cfg, k_grid, horizons,
                          not_before_for(today_rows))]
    for j, rows in enumerate(history[:keep - 1]):
        if not rows:
            continue
        out.append(symbol_summary(rows, history[j + 1:j + 1 + need], cfg,
                                  [float(cfg["k"])], horizons, not_before_for(rows)))
    return out


def pool(days):
    """Pool symbol-days: surges and reversals in SEPARATE lists, base-rate
    counts summed per horizon."""
    base = {}
    for d in days:
        for h, c in d["base"].items():
            acc = base.setdefault(h, {"up": 0, "down": 0, "flat": 0})
            for key in acc:
                acc[key] += c.get(key, 0)
    return {"sessions": len(days),
            "measured": sum(1 for d in days if d.get("sigma") is not None),
            "surges": [f for d in days for f in d["surges"]],
            "reversals": [f for d in days for f in d["reversals"]],
            "base": base}


# --- the report --------------------------------------------------------------------

def _pct(x, digits=0):
    return DASH if x is None else f"{x * 100:.{digits}f}%"


def _bp(x):
    return DASH if x is None else f"{x * 1e4:+.1f} bp"


def _money(x):
    if x is None:
        return DASH
    sign = "-" if x < 0 else ""
    a = abs(x)
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{sign}${a / div:.1f}{unit}"
    return f"{sign}${a:,.0f}"


def k_of_n(k, n):
    """'3 of 7'; with a percentage once n reaches MIN_N_FOR_PCT; a dash for 0."""
    if not n:
        return DASH
    s = f"{k} of {n}"
    return s + (f" ({k / n * 100:.0f}%)" if n >= MIN_N_FOR_PCT else "")


def _base_cell(base, side, h):
    c = (base or {}).get(h) or {}
    n = c.get("up", 0) + c.get("down", 0)
    k = c.get("up", 0) if side in _UP else c.get("down", 0)
    return k_of_n(k, n)


def _ct(ts):
    return _dt.datetime.fromtimestamp(ts, CT).strftime("%H:%M")


def _k_label(k, s):
    tags = [t for t, v in (("live", s["k_live"]), ("push", s["push_k"])) if k == v]
    return f"{k:.1f}" + (f" ({', '.join(tags)})" if tags else "")


def _rev_label(s):
    return "Reversals" + ("" if s.get("flips_live") else " (off live)")


_SIDE_WORDS = {"dealers_buying": "dealers buying", "dealers_selling": "dealers selling",
               "to_buying": "to buying", "to_selling": "to selling"}


def _outcome_table(L, signals, base, horizons, measured):
    """``signals`` = [(label, fires, sides, skipped)]; one row per signal, side
    and horizon, each beside the base rate for the SAME direction."""
    L += ["| Signal | Direction | Horizon | Hit rate | Flat | Same-direction base rate "
          "| Mean return |", "|---|---|---|---|---|---|---|"]
    for label, fires, sides, skipped in signals:
        for side in sides:
            for h in horizons:
                if not measured or skipped:
                    cells = (DASH, DASH, DASH)
                else:
                    t = tally(fires, side, h)
                    if t["n"] + t["flat"] == 0:
                        cells = ("no fires", DASH, DASH)
                    else:
                        cells = (k_of_n(t["hits"], t["n"]), str(t["flat"]),
                                 _bp(t["mean"]))
                L.append(f"| {label} | {_SIDE_WORDS[side]} | {h} min | {cells[0]} | "
                         f"{cells[1]} | {_base_cell(base, side, h)} | {cells[2]} |")
    L.append("")


def _symbol_section(sym, s, pooled=None, keep=None):
    L = [f"## {sym}", ""]
    if s and "error" in s:
        L += [f"Skipped: {s['error']}", ""]
        return L
    if not s or not s.get("rows"):
        L += ["Nothing measured: no minutes stored for this day.", ""]
        return L
    measured = s["sigma"] is not None
    if s["sigma_source"] == "prior":
        size = f"{_money(s['sigma'])}, from the prior sessions"
    elif s["sigma_source"] == "today":
        size = (f"{_money(s['sigma'])} at the close, from today's own minutes (not "
                f"enough prior sessions). The replay uses the size live had at "
                f"each minute, so nothing could fire before that size existed.")
    else:
        size = f"{DASH} — too little data, so the rules did not run"
    L += [f"- Minutes measured: {s['rows']}",
          f"- Volume with no buy/sell label: {_pct(s['unclassified'], 1)}",
          f"- Normal 15-minute size: {size}", ""]
    if s.get("flips_skipped"):
        L += ["Reversals skipped: `flip_not_before` is not a valid HH:MM time, so "
              "the live rule did not run them either.", ""]
    L += ["| Surge at (× normal) | Fires |", "|---|---|"]
    for k, n in s["fires_by_k"].items():
        L.append(f"| {_k_label(k, s)} | {DASH if n is None else n} |")
    L.append(f"| {_rev_label(s)} | {DASH if s['flips'] is None else s['flips']} |")
    L.append("")
    k_live = s["k_live"]
    rev = _rev_label(s)
    signals = [(f"Surge at {k_live:.1f} (live)", s["surges"], SURGE_SIDES, False),
               (rev, s["reversals"], FLIP_SIDES, s.get("flips_skipped"))]
    _outcome_table(L, signals, s["base"], s["horizons"], measured)
    if s["events"]:
        hs = s["horizons"]
        L += ["| Time (CT) | Signal | Direction | Size | "
              + " | ".join(f"{h} min" for h in hs) + " |",
              "|---|---|---|---|" + "---|" * len(hs)]
        for e in s["events"]:
            L.append(f"| {_ct(e['ts'])} | {e['kind']} | {_SIDE_WORDS[e['side']]} | "
                     f"{e['size']} | " + " | ".join(_bp(e['fwd'][h]) for h in hs) + " |")
        L.append("")
    if pooled:
        L += [f"### Last {pooled['sessions']} sessions"
              + (f" (up to {keep} kept)" if keep else ""), "",
              f"{pooled['measured']} of {pooled['sessions']} sessions had a normal "
              f"size to measure against. Each day used the size live had that day. "
              f"Surges and reversals are pooled SEPARATELY: they are correlated (a "
              f"reversal often follows a surge), and so are fires within one "
              f"session, so the counts below overstate how many independent tries "
              f"there were.", ""]
        signals = [(f"Surge at {k_live:.1f} (live)", pooled["surges"], SURGE_SIDES,
                    False),
                   (rev, pooled["reversals"], FLIP_SIDES, s.get("flips_skipped"))]
        _outcome_table(L, signals, pooled["base"], s["horizons"],
                       pooled["measured"] > 0)
    return L


def build_report(day, per_symbol, cfg, notes=(), pooled=None):
    """Markdown. Per symbol: rows measured, unlabelled share, sigma + source,
    fires at each k (mark the live k and push_k), reversals; for live-k surges
    and for reversals, per direction and horizon: hits as 'k of n', ties apart,
    the same-direction base rate and the mean return; then the per-fire table
    and the pooled 'Last N sessions'. The header says it is a MODEL and how to
    read a hit rate against the base rate. Symbols with no rows say so
    explicitly ('no minutes stored'), never a zero."""
    L = [f"# HIRO-model hedging-flow report — {day}", "",
         "This measures a **model** of dealer hedging flow, not SpotGamma's HIRO. "
         "Schwab publishes no trade tape, so each contract gets one buy/sell label "
         "a minute from where its last price sits against the bid and ask.", "",
         "A **hit** is a fire after which the price moved the way the modelled "
         "dealer hedging pushed it (up after dealers buying, down after dealers "
         "selling) over the horizon shown. A return of exactly zero is neither: it "
         "is left out of the hit rate and counted as **flat**. Hits read "
         f"'k of n', with a percentage once n reaches {MIN_N_FOR_PCT}.", "",
         "A Hit rate means something only above that day's **base rate** for the "
         "same direction: the share of every measured minute after which the "
         "price rose (beside a buying signal) or fell (beside a selling one) over "
         "the same horizon, ties left out the same way. On a day that trended up, "
         "most buying signals 'hit' by doing nothing. The phone push stays off "
         "until several sessions show hit rates well above their base rate at the "
         "live setting.", "",
         f"Live settings: Surge at {float(cfg['k']):.1f}× normal "
         f"(push at {float(cfg['push_k']):.1f}×), a {int(cfg['window_min'])}-minute "
         f"window, quiet {int(cfg['cooldown_min'])} minutes per direction; "
         f"reversal dead zone {float(cfg['flip_band']):.2f}× normal, none before "
         f"{cfg.get('flip_not_before', '09:00')} CT, quiet "
         f"{int(cfg['flip_cooldown_min'])} minutes"
         + ("" if cfg.get("flip_enabled") is True else
            " (reversals are OFF live; measured here anyway)") + ".", ""]
    for n in notes:
        L += [f"**{n}**", ""]
    keep = cfg.get("keep_sessions")
    for sym, s in per_symbol.items():
        L += _symbol_section(sym, s, (pooled or {}).get(sym), keep)
    return "\n".join(L)


# --- CLI ---------------------------------------------------------------------------

def _float_list(s):
    return [float(x) for x in s.split(",") if x.strip()]


def _int_list(s):
    return [int(x) for x in s.split(",") if x.strip()]


def parse_args(argv):
    p = argparse.ArgumentParser(description="Daily HIRO-model validation report.")
    p.add_argument("--date", type=_dt.date.fromisoformat, default=None,
                   help="the session to report, YYYY-MM-DD (default: the newest "
                        "closed session)")
    p.add_argument("--force", action="store_true",
                   help="report even on a weekend or market holiday")
    p.add_argument("--out", default=None,
                   help="directory to write <date>/report.md under "
                        "(default options-scanner/data/hiro_report)")
    p.add_argument("--k", type=_float_list, default=None,
                   help=f"comma-separated Surge multiples (default {K_GRID})")
    p.add_argument("--horizons", type=_int_list, default=None,
                   help=f"comma-separated forward minutes (default {FORWARD_MIN})")
    return p.parse_args(argv)


def default_day(now):
    """The session a run with no ``--date`` reports: the newest CLOSED one --
    today after the regular close, else the previous trading day. So a catch-up
    run at boot (the timer is Persistent) reports the session it missed rather
    than an empty morning, a weekend or a holiday."""
    d = now.date()
    if not is_trading_day(d):
        return prev_trading_day(d)
    _open, close = _session_bounds("regular")
    return d if now.time() > close else prev_trading_day(d)


def _symbols(cfg):
    raw = cfg.get("symbols")
    if isinstance(raw, str):
        raw = [raw]
    return [s for s in raw if isinstance(s, str)] if isinstance(raw, (list, tuple)) else []


def _not_before_fn(cfg):
    """``rows -> unix ts`` of ``flip_not_before`` on that session's date, or None
    when the value is malformed -- the handler's degrade: reversals skipped,
    surges still run."""
    hhmm = cfg.get("flip_not_before", "09:00")

    def not_before(rows):
        try:
            return hiro.ct_ts(_session_date(rows), hhmm)
        except Exception:                                   # noqa: BLE001
            return None
    return not_before


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else list(argv))
    day = args.date or default_day(_dt.datetime.now(CT))
    trading = is_trading_day(day)
    if not trading and not args.force:
        print(f"Skipped: {day} is not a trading day. No report written "
              f"(--force to report anyway).")
        return 0
    cfg = load_cfg()
    k_grid = args.k or list(K_GRID)
    horizons = args.horizons or list(FORWARD_MIN)
    not_before_for = _not_before_fn(cfg)
    need = int(cfg["baseline_sessions"])
    keep = max(1, int(cfg.get("keep_sessions", 1)))
    try:
        conn = gh.connect(read_only=True)
    except Exception as e:                                  # noqa: BLE001
        print(f"Cannot open {gh.DB_PATH} read-only: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1
    per, pooled = {}, {}
    try:
        for sym in _symbols(cfg):
            try:
                rows = gh.load_hiro_day(conn, sym, day)
                if not rows:
                    per[sym] = None
                    continue
                history = gh.load_hiro_prior_sessions(
                    conn, sym, keep - 1 + need, before=day)
                days = session_summaries(rows, history, cfg, k_grid, horizons,
                                         not_before_for)
                per[sym] = days[0]
                pooled[sym] = pool(days)
            except Exception as e:                          # noqa: BLE001
                per[sym] = {"rows": -1, "error": f"{type(e).__name__}: {e}"}
                print(f"  {sym}: skipped ({type(e).__name__}: {e})", file=sys.stderr)
    finally:
        conn.close()

    symbols = list(per)
    errored = [s for s in symbols if per[s] and "error" in per[s]]
    measured = [s for s in symbols if per[s] and "error" not in per[s]]
    notes, failed = [], False
    if not symbols:
        notes.append("No symbols are configured under [hiro].symbols; nothing measured.")
        failed = True
    elif len(errored) == len(symbols):
        notes.append("Every symbol failed; nothing was measured.")
        failed = True
    elif not measured and trading and errored:
        notes.append(f"No symbol could be measured: {len(errored)} failed and the "
                     f"rest had no minutes stored on a trading day.")
        failed = True
    elif not measured and trading:
        notes.append("No minutes stored for any symbol on a trading day: the HIRO "
                     "collector may not have run. Nothing was measured.")
        failed = True
    for n in notes:
        print(n, file=sys.stderr)

    base = pathlib.Path(args.out) if args.out else OPTIONS_SCANNER / "data" / "hiro_report"
    out_dir = base / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    path.write_text(build_report(day.isoformat(), per, cfg, notes, pooled),
                    encoding="utf-8")
    print(f"Wrote {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
