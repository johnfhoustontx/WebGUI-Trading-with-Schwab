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

THE HEDGING FLIP (``gamma_flip``, added 2026-10-20) is the third Flow alert of
the same family: price crossing the dealer gamma flip level. It is replayed the
same way (``flow_alerts.detect_gamma_flip`` minute by minute with the handler's
cooldown, ``replay_gamma_flips``) from the GEX snapshots' spot and flip, and
scored on the SAME HIRO minute prices and base rate, so its row sits beside the
surges' and reversals' and reads the same way. The snapshots keep 5 sessions
and the HIRO minutes 20, so each run also saves the day's flips in
``<date>/gamma_flip.json``; later runs pool from those once the snapshots are
gone.

USAGE
-----
    .venv/bin/python tools/hiro_report.py                     # the latest closed session
    .venv/bin/python tools/hiro_report.py --date 2026-09-30   # re-run a past day
    .venv/bin/python tools/hiro_report.py --date 2026-10-03 --force

Reads ``options-scanner/gex_history.db`` read-only and its own earlier output,
and nothing else (no proxy, no Redis, no secrets), so a missed day can be re-run
later with ``--date`` for as long as ``[hiro].keep_sessions`` keeps its minutes.
Skips with exit 0 on a weekend or NYSE holiday given with ``--date`` unless
``--force``. Writes ``options-scanner/data/hiro_report/<date>/report.md``, plus
``gamma_flip.json`` in the folder of every session whose flips it could replay
(``--out`` replaces the ``hiro_report`` directory). Exits 1 when nothing was measured: every symbol
failed, or a trading day has no stored minute for any symbol (a dead collector).

Scheduled daily by ``trading-<env>-hiro-report.timer`` at ``[slots.hiro_report]``.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import json
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

_UP = {"dealers_buying": 1, "to_buying": 1, "to_positive": 1}
_DOWN = {"dealers_selling": -1, "to_selling": -1, "to_negative": -1}
SURGE_SIDES = ("dealers_buying", "dealers_selling")
FLIP_SIDES = ("to_buying", "to_selling")
# The Hedging flip (``gamma_flip``): price crossing the dealer gamma flip level.
# to_positive is a cross UP through it, to_negative a cross DOWN, so it is
# scored as continuation -- a hit is price carrying on the way it crossed.
GFLIP_SIDES = ("to_positive", "to_negative")

# Each run saves the day's Hedging flip fires here, beside report.md. The
# per-minute spot and flip they are replayed from live in ``snapshots``, which
# keeps 5 sessions (purge_keep_sessions); the HIRO minutes keep 20. Without the
# saved fires the flip's pool could never grow past 5 sessions.
GFLIP_FILE = "gamma_flip.json"

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
    """The live ``[hiro]`` table, merged over its defaults, with its numbers
    coerced by ``hiro.clean_cfg`` -- the helper the live handler uses, so a bad
    hand-edited value replays exactly as it runs live. A non-dict table (a
    ``hiro = 5`` in the TOML) falls back to the built-in defaults."""
    h = flow_alerts.load_thresholds().get("hiro")
    return hiro.clean_cfg(h, flow_alerts._DEFAULTS["hiro"])


def load_gflip_cfg() -> dict:
    """The live ``[gamma_flip]`` table through ``flow_alerts.section``, the
    accessor the live handler (``handlers._run_gamma_flip``) reads it with."""
    return flow_alerts.section(flow_alerts.load_thresholds(), "gamma_flip")


def gflip_symbols(gcfg) -> list | None:
    """The symbols the live Hedging flip watches, or None for 'every symbol'
    (an empty list live means the whole flow universe)."""
    raw = (gcfg or {}).get("symbols")
    if isinstance(raw, str):
        raw = [raw]
    syms = [s for s in raw if isinstance(s, str)] if isinstance(raw, (list, tuple)) else []
    return syms or None


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


def replay_gamma_flips(points, gcfg):
    """The Hedging flips the live rule would have fired over one session's
    ``[(ts, spot, flip)]``, ascending -- ``handlers._run_gamma_flip``'s sequence
    minute by minute: ``flow_alerts.detect_gamma_flip`` against the regime
    carried from the minute before (none at the session's first minute, as the
    live state is date-scoped), and on a fire the symbol's cooldown. A fire the
    cooldown suppresses keeps the PRIOR regime, as live does, so it can fire
    once the cooldown ends; every other minute takes the new regime."""
    band = _finite(gcfg.get("band_pct"))
    band = 0.0015 if band is None else band
    cd = _finite(gcfg.get("cooldown_min"))
    cool = (60.0 if cd is None else cd) * 60
    prev, cooldowns, out = None, {}, []
    for ts, spot, flip in points:
        alert, new = flow_alerts.detect_gamma_flip("", spot, flip, prev, band, ts)
        if alert:
            if flow_alerts._on_cooldown(cooldowns, "gamma_flip", ts, cool):
                continue
            cooldowns["gamma_flip"] = ts
            out.append({"type": "gamma_flip", "side": alert["side"], "ts": ts,
                        "spot": spot, "flip": flip})
        prev = new
    return out


# A Hedging flip is timed off the GEX snapshot; it is scored off the HIRO minute
# nearest it, so its forward move and the day's base rate come from ONE price
# series. A flip with no HIRO minute this close is not scored.
_ANCHOR_SEC = 60


def anchor_gamma_flips(fires, rows):
    """Each Hedging flip re-timed onto the nearest HIRO minute (within
    ``_ANCHOR_SEC``), taking that minute's ts and spot -- the price series the
    surges, reversals and base rate are all measured on. ``flip`` is kept for
    the per-fire table. A flip with no minute close enough is dropped."""
    ts_list = [r["ts"] for r in rows]
    out = []
    for f in fires:
        ts = _finite(f.get("ts"))
        if ts is None or not ts_list:
            continue
        j = bisect.bisect_left(ts_list, ts)
        near = [i for i in (j - 1, j) if 0 <= i < len(rows)]
        i = min(near, key=lambda i: abs(ts_list[i] - ts))
        if abs(ts_list[i] - ts) > _ANCHOR_SEC:
            continue
        out.append({"type": "gamma_flip", "side": f["side"], "ts": ts_list[i],
                    "spot": rows[i].get("spot"), "flip": f.get("flip")})
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
    if fire["type"] == "hiro_surge":
        kind, size = "Surge", f"{fire['mult']:.1f}× normal"
    elif fire["type"] == "gamma_flip":
        flip = _finite(fire.get("flip"))
        kind, size = "Hedging flip", (DASH if flip is None else f"flip at {flip:,.2f}")
    else:
        kind, size = "Reversal", f"day net {_money(_finite(fire.get('cum')))}"
    return {"ts": fire["ts"], "side": fire["side"], "kind": kind, "size": size,
            "fwd": {h: forward_return(rows, fire, h) for h in horizons}}


def _with_fwd(fires, rows, horizons):
    return [{**f, "fwd": {h: forward_return(rows, f, h) for h in horizons}}
            for f in fires]


def symbol_summary(rows, prior_sessions, cfg, k_grid, horizons, not_before_ts,
                   gflip=None):
    """One symbol-day: {rows, sigma, sigma_source ('prior' | 'today' | None),
    unclassified (share of the day's volume), fires_by_k, flips, surges and
    reversals (live-k fires, each with its forward returns), base (the day's
    base rate counts), events (the per-fire table), fwd: {k_live: {h: [returns]}},
    flip_fwd: {h: [returns]}, gflips, gflip_source}.

    The live ``k`` and ``push_k`` are always in the grid. With no sigma the rules
    do not run, so every count is None (not measured), never 0. A None
    ``not_before_ts`` (a malformed ``flip_not_before``) skips the reversals, as
    the handler does.

    ``gflip`` is ``(fires, source)`` for the Hedging flip: the session's raw
    fires and where they came from: 'stored' (replayed from the GEX snapshots),
    'saved' (an earlier run's ``GFLIP_FILE``), or with no fires 'unwatched' (not
    a symbol the live alert watches), 'gone' (snapshots purged and nothing
    saved) or 'error'. None leaves it unmeasured and unmentioned. It needs no
    sigma, so it is measured on a day the HIRO rules could not run. ``gflips``
    is None whenever it was not measured."""
    k_live = float(cfg["k"])
    grid = sorted({float(k) for k in k_grid} | {k_live, float(cfg["push_k"])})
    horizons = [int(h) for h in horizons]
    sigma, source, headline = _sigmas(rows, prior_sessions, cfg)
    g_raw, g_source = gflip if gflip else (None, None)
    gflips = (None if g_raw is None
              else _with_fwd(anchor_gamma_flips(g_raw, rows), rows, horizons))
    out = {"rows": len(rows), "sigma": headline, "sigma_source": source,
           "unclassified": _unclassified(rows), "k_live": k_live,
           "push_k": float(cfg["push_k"]), "horizons": horizons,
           "flips_skipped": not_before_ts is None,
           "flips_live": cfg.get("flip_enabled") is True,
           "base": base_counts(rows, horizons),
           "gflips": gflips, "gflip_source": g_source}
    if sigma is None:
        out.update(fires_by_k={k: None for k in grid}, flips=None, surges=[],
                   reversals=[], fwd={k_live: {h: [] for h in horizons}},
                   flip_fwd={h: [] for h in horizons},
                   events=[event_row(f, rows, horizons) for f in (gflips or [])])
        return out
    fires = {k: replay_surges(rows, sigma, cfg, k) for k in grid}
    surges = _with_fwd(fires[k_live], rows, horizons)
    flips = _with_fwd(replay_flips(rows, sigma, cfg, not_before_ts), rows, horizons)
    out.update(
        fires_by_k={k: len(v) for k, v in fires.items()},
        flips=None if not_before_ts is None else len(flips),
        surges=surges, reversals=flips,
        events=[event_row(f, rows, horizons)
                for f in sorted(surges + flips + (gflips or []), key=lambda f: f["ts"])],
        fwd={k_live: {h: [f["fwd"][h] for f in surges if f["fwd"][h] is not None]
                      for h in horizons}},
        flip_fwd={h: [f["fwd"][h] for f in flips if f["fwd"][h] is not None]
                  for h in horizons})
    return out


def _session_date(rows):
    return _dt.datetime.fromtimestamp(rows[0]["ts"], CT).date()


def session_summaries(today_rows, history, cfg, k_grid, horizons, not_before_for,
                      gflip_for=None):
    """``[today, the session before, ...]`` -- up to ``keep_sessions`` in all --
    each summarised by ``symbol_summary`` with the prior sessions live had on
    THAT day (the ``baseline_sessions`` before it in ``history``, newest first).
    ``not_before_for(rows)`` gives a session's reversal start (or None);
    ``gflip_for(rows)`` its Hedging flip ``(fires, source)`` (see
    ``symbol_summary``), or None to leave the flip unmeasured.

    Each summary carries ``excluded``. A prior day whose own window of older
    sessions is incomplete is a FAITHFUL replay only while the history is young
    (live, too, had no prior sessions then and used today's minutes). Once the
    stored history is purge-limited (``keep_sessions - 1`` older sessions or
    more), that day's older sessions existed live and were purged since, so its
    replay would use a size live never used: it is summarised but excluded."""
    need = int(cfg["baseline_sessions"])
    keep = max(1, int(cfg.get("keep_sessions", 1)))
    purge_limited = len(history) >= keep - 1

    def gflip(rows):
        return gflip_for(rows) if gflip_for else None

    out = [{**symbol_summary(today_rows, history[:need], cfg, k_grid, horizons,
                             not_before_for(today_rows), gflip(today_rows)),
            "excluded": False}]
    for j, rows in enumerate(history[:keep - 1]):
        if not rows:
            continue
        older = history[j + 1:j + 1 + need]
        s = symbol_summary(rows, older, cfg, [float(cfg["k"])], horizons,
                           not_before_for(rows), gflip(rows))
        s["excluded"] = purge_limited and len(older) < need
        out.append(s)
    return out


def pool(days):
    """Pool the symbol-days not ``excluded``: surges and reversals in SEPARATE
    lists, base-rate counts summed per horizon over the days where the rules
    ran (a day with no sigma had no fires to compare), sigma sources counted."""
    kept = [d for d in days if not d.get("excluded")]
    base = {}
    for d in kept:
        if d.get("sigma") is None:
            continue
        for h, c in d["base"].items():
            acc = base.setdefault(h, {"up": 0, "down": 0, "flat": 0})
            for key in acc:
                acc[key] += c.get(key, 0)
    sources = {"prior": 0, "today": 0, None: 0}
    for d in kept:
        src = d.get("sigma_source")
        sources[src if src in sources else None] += 1
    # The Hedging flip needs no sigma, so ``excluded`` (a sigma rule) does not
    # apply: it pools over EVERY session it was measured on, with its own base
    # rate summed over exactly those sessions.
    g_days = [d for d in days if d.get("gflips") is not None]
    g_base = {}
    for d in g_days:
        for h, c in d["base"].items():
            acc = g_base.setdefault(h, {"up": 0, "down": 0, "flat": 0})
            for key in acc:
                acc[key] += c.get(key, 0)
    g_src = {"stored": 0, "saved": 0}
    for d in g_days:
        if d.get("gflip_source") in g_src:
            g_src[d["gflip_source"]] += 1
    return {"sessions": len(kept), "excluded": len(days) - len(kept),
            "measured": sum(1 for d in kept if d.get("sigma") is not None),
            "sources": sources,
            "surges": [f for d in kept for f in d["surges"]],
            "reversals": [f for d in kept for f in d["reversals"]],
            "base": base,
            "gflips": [f for d in g_days for f in d["gflips"]],
            "gflip_base": g_base, "gflip_sessions": len(g_days),
            "gflip_sources": g_src,
            "gflip_missing": sum(1 for d in days if d.get("gflip_source") == "gone")}


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


def _base_kn(base, side, h):
    c = (base or {}).get(h) or {}
    n = c.get("up", 0) + c.get("down", 0)
    return (c.get("up", 0) if side in _UP else c.get("down", 0)), n


def _base_cell(base, side, h):
    return k_of_n(*_base_kn(base, side, h))


# Below this many decided tries the luck figure says nothing worth printing.
MIN_N_FOR_LUCK = 5


def chance_by_luck(k, n, p):
    """P(at least ``k`` hits in ``n`` tries) when each try hits with
    probability ``p`` (the same-direction base rate): the exact one-sided
    binomial tail. None below MIN_N_FOR_LUCK tries or without a usable ``p``."""
    p = _finite(p)
    if p is None or not 0 <= p <= 1 or n < MIN_N_FOR_LUCK or not 0 <= k <= n:
        return None
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def _luck(x):
    if x is None:
        return DASH
    return "<0.1%" if x < 0.001 else f"{x * 100:.1f}%"


def _ct(ts):
    return _dt.datetime.fromtimestamp(ts, CT).strftime("%H:%M")


def _k_label(k, s):
    tags = [t for t, v in (("live", s["k_live"]), ("push", s["push_k"])) if k == v]
    return f"{k:.1f}" + (f" ({', '.join(tags)})" if tags else "")


def _rev_label(s):
    return "Reversals" + ("" if s.get("flips_live") else " (off live)")


def _gflip_label(_s):
    return "Hedging flips"


def _gflip_note(s):
    """Why a symbol-day's Hedging flip row reads a dash, or None."""
    if s.get("gflip_source") == "unwatched":
        return ("Hedging flip: not measured. This symbol is not in the live "
                "alert's `[gamma_flip].symbols`, so it never fires here live.")
    if s.get("gflip_source") == "gone":
        return ("Hedging flip: not measured. Its per-minute spot and flip are "
                "gone (the GEX snapshots keep 5 sessions) and no earlier run "
                f"saved this day's fires in `{GFLIP_FILE}`.")
    if s.get("gflip_source") == "error":
        return ("Hedging flip: not measured. Its stored spot and flip could not "
                "be read (the reason is on the run's error output).")
    return None


_SIDE_WORDS = {"dealers_buying": "dealers buying", "dealers_selling": "dealers selling",
               "to_buying": "to buying", "to_selling": "to selling",
               "to_positive": "crossed up", "to_negative": "crossed down"}


def _outcome_table(L, signals, base, horizons, measured):
    """``signals`` = [(label, fires, sides, skipped)], or with a fifth element
    ``{"base": ..., "measured": ...}`` for a signal measured on its own set of
    days (the Hedging flip); one row per signal, side and horizon, each beside
    the base rate for the SAME direction."""
    L += ["| Signal | Direction | Horizon | Hit rate | Flat | Same-direction base rate "
          "| Chance by luck | Mean move (signed) |", "|---|---|---|---|---|---|---|---|"]
    for sig in signals:
        label, fires, sides, skipped = sig[:4]
        own = sig[4] if len(sig) > 4 else {}
        sig_base = own.get("base", base)
        sig_measured = own.get("measured", measured)
        for side in sides:
            for h in horizons:
                bk, bn = _base_kn(sig_base, side, h)
                if not sig_measured or skipped:
                    cells = (DASH, DASH, DASH, DASH)
                else:
                    t = tally(fires, side, h)
                    if t["n"] + t["flat"] == 0:
                        cells = ("no fires", DASH, DASH, DASH)
                    else:
                        luck = chance_by_luck(t["hits"], t["n"], bk / bn if bn else None)
                        cells = (k_of_n(t["hits"], t["n"]), str(t["flat"]),
                                 _luck(luck), _bp(t["mean"]))
                L.append(f"| {label} | {_SIDE_WORDS[side]} | {h} min | {cells[0]} | "
                         f"{cells[1]} | {k_of_n(bk, bn)} | {cells[2]} | {cells[3]} |")
    L.append("")


def _symbol_section(sym, s, pooled=None, keep=None, need=None):
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
    gflip_shown = s.get("gflip_source") not in (None, "unwatched")
    if gflip_shown:
        L.append(f"| {_gflip_label(s)} | "
                 f"{DASH if s.get('gflips') is None else len(s['gflips'])} |")
    L.append("")
    note = _gflip_note(s)
    if note:
        L += [note, ""]
    k_live = s["k_live"]
    rev = _rev_label(s)
    signals = [(f"Surge at {k_live:.1f} (live)", s["surges"], SURGE_SIDES, False),
               (rev, s["reversals"], FLIP_SIDES, s.get("flips_skipped"))]
    if gflip_shown:
        signals.append((_gflip_label(s), s.get("gflips") or [], GFLIP_SIDES, False,
                        {"measured": s.get("gflips") is not None}))
    _outcome_table(L, signals, s["base"], s["horizons"], measured)
    if s["events"]:
        hs = s["horizons"]
        L += ["| Time (CT) | Signal | Direction | Size | "
              + " | ".join(f"Move {h} min" for h in hs) + " |",
              "|---|---|---|---|" + "---|" * len(hs)]
        for e in s["events"]:
            L.append(f"| {_ct(e['ts'])} | {e['kind']} | {_SIDE_WORDS[e['side']]} | "
                     f"{e['size']} | " + " | ".join(_bp(e['fwd'][h]) for h in hs) + " |")
        L.append("")
    if pooled and pooled["sessions"] > 1:
        src = pooled["sources"]
        left_out = ""
        if pooled["excluded"]:
            left_out = (f" {pooled['excluded']} older session(s) left out: fewer "
                        f"than {need} sessions before them are still stored "
                        f"(retention keeps {keep}), so the normal size live used "
                        f"that day cannot be rebuilt.")
        L += [f"### Last {pooled['sessions']} sessions"
              + (f" (up to {keep} kept)" if keep else ""), "",
              f"Pooled {pooled['sessions']} sessions.{left_out} Normal size from "
              f"the prior sessions on {src['prior']}, from that day's own minutes "
              f"(as live had them each minute) on {src['today']}, and none, so the "
              f"rules did not run, on {src[None]}. The base rate counts only days "
              f"where the rules ran. Surges and reversals are pooled SEPARATELY: "
              f"they are correlated (a reversal often follows a surge), and so are "
              f"fires within one session, so the counts below overstate how many "
              f"independent tries there were.", ""]
        signals = [(f"Surge at {k_live:.1f} (live)", pooled["surges"], SURGE_SIDES,
                    False),
                   (rev, pooled["reversals"], FLIP_SIDES, s.get("flips_skipped"))]
        if gflip_shown:
            g = pooled.get("gflip_sessions", 0)
            src = pooled.get("gflip_sources") or {}
            missing = pooled.get("gflip_missing", 0)
            L += [f"Hedging flips pooled over {g} session(s): {src.get('stored', 0)} "
                  f"replayed from the stored GEX snapshots, {src.get('saved', 0)} "
                  f"from fires an earlier run saved"
                  + (f", and {missing} not measured (snapshots purged before any "
                     f"run saved them)" if missing else "")
                  + ". Their base rate is summed over those same sessions.", ""]
            signals.append((_gflip_label(s), pooled.get("gflips") or [], GFLIP_SIDES,
                            False, {"base": pooled.get("gflip_base") or {},
                                    "measured": g > 0}))
        _outcome_table(L, signals, pooled["base"], s["horizons"],
                       pooled["measured"] > 0)
    return L


def build_report(day, per_symbol, cfg, notes=(), pooled=None, gcfg=None):
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
         "Moves are **signed**: + means the price moved the way the signal "
         "pointed (up after a buying signal, down after a selling one), so a "
         "positive mean is good for the signal whichever way it pointed.", "",
         "**Chance by luck** is how likely at least that many hits would be if "
         "each fire were a coin weighted to the day's base rate: small means "
         "unlikely to be luck. Fires within one session are correlated, so treat "
         f"it as optimistic. Shown from {MIN_N_FOR_LUCK} decided fires.", "",
         f"Live settings: Surge at {float(cfg['k']):.1f}× normal "
         f"(push at {float(cfg['push_k']):.1f}×), a {int(cfg['window_min'])}-minute "
         f"window, quiet {int(cfg['cooldown_min'])} minutes per direction; "
         f"reversal dead zone {float(cfg['flip_band']):.2f}× normal, none before "
         f"{cfg.get('flip_not_before', '09:00')} CT, quiet "
         f"{int(cfg['flip_cooldown_min'])} minutes"
         + ("" if cfg.get("flip_enabled") is True else
            " (reversals are OFF live; measured here anyway)") + ".", ""]
    if gcfg is not None:
        band = _finite(gcfg.get("band_pct"))
        cd = _finite(gcfg.get("cooldown_min"))
        L += ["**Hedging flips** fire when price crosses the dealer gamma flip "
              "level: up through it (dealers' hedging turns to damping moves) or "
              "down through it (to amplifying them). They are scored the same way "
              "as the two HIRO signals, as **continuation**: a hit is price "
              "carrying on the way it crossed. That is not the flip's own claim, "
              "which is about how BIG moves get either side of the level, so a hit "
              "rate near the base rate here does not by itself say the flip level "
              "is meaningless. Each flip is timed on the GEX snapshot and scored "
              "on the nearest HIRO minute, so its moves and base rate come from "
              "the same prices as the surges and reversals.", "",
              "Hedging flip settings: "
              + (f"dead zone {band * 100:.2f}% of the flip level" if band is not None
                 else "dead zone —")
              + (f", quiet {cd:.0f} minutes" if cd is not None else "")
              + ("" if gcfg.get("enabled", True) else
                 " (the alert is OFF live; measured here anyway)") + ".", ""]
    for n in notes:
        L += [f"**{n}**", ""]
    keep = cfg.get("keep_sessions")
    need = cfg.get("baseline_sessions")
    for sym, s in per_symbol.items():
        L += _symbol_section(sym, s, (pooled or {}).get(sym), keep, need)
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


def read_saved_gflips(base, day, sym):
    """The Hedging flip fires an earlier run saved for ``sym`` on ``day``, or
    None when no run saved that symbol-day. A malformed entry is dropped, never
    guessed at; a malformed file reads as not saved."""
    try:
        data = json.loads((base / day.isoformat() / GFLIP_FILE).read_text(encoding="utf-8"))
        fires = data["symbols"][sym]
    except Exception:                                       # noqa: BLE001
        return None
    if not isinstance(fires, list):
        return None
    return [{"type": "gamma_flip", "side": f["side"], "ts": _finite(f.get("ts")),
             "spot": _finite(f.get("spot")), "flip": _finite(f.get("flip"))}
            for f in fires
            if isinstance(f, dict) and f.get("side") in GFLIP_SIDES
            and _finite(f.get("ts")) is not None]


def write_saved_gflips(base, day, by_symbol, gcfg):
    """Save ``{symbol: [fires]}`` for ``day`` into ``GFLIP_FILE``, merged over
    the symbols already saved there, through a temporary file so a reader never
    sees half of one. Written whenever the fires were replayed from the stored
    snapshots, so the file holds the replay as of the newest run that could
    still see them."""
    out_dir = base / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / GFLIP_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        symbols = data["symbols"] if isinstance(data.get("symbols"), dict) else {}
    except Exception:                                       # noqa: BLE001
        symbols = {}
    for sym, fires in by_symbol.items():
        symbols[sym] = [{"ts": f["ts"], "side": f["side"], "spot": f.get("spot"),
                         "flip": f.get("flip")} for f in fires]
    payload = {"date": day.isoformat(),
               "settings": {"band_pct": gcfg.get("band_pct"),
                            "cooldown_min": gcfg.get("cooldown_min")},
               "symbols": symbols}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)


def gflip_source(conn, sym, gcfg, base, fresh):
    """``rows -> (fires, source)`` for one symbol's Hedging flip on the session
    ``rows`` belong to: replayed from the stored GEX snapshots while they last
    (and recorded in ``fresh`` to be saved), else an earlier run's saved fires,
    else ``(None, 'gone')``. A symbol the live alert does not watch is
    ``(None, 'unwatched')``; a read that fails is ``(None, 'error')``, never a
    failure of the HIRO half of the report."""
    watched = gflip_symbols(gcfg)

    def source(rows):
        if watched is not None and sym not in watched:
            return None, "unwatched"
        day = _session_date(rows)
        try:
            points = gh.load_spot_flip_day(conn, sym, day)
            if points:
                fires = replay_gamma_flips(points, gcfg)
                fresh.setdefault(day, {})[sym] = fires
                return fires, "stored"
        except Exception as e:                              # noqa: BLE001
            print(f"  {sym} {day}: Hedging flip not measured "
                  f"({type(e).__name__}: {e})", file=sys.stderr)
            return None, "error"
        saved = read_saved_gflips(base, day, sym)
        return (saved, "saved") if saved is not None else (None, "gone")
    return source


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else list(argv))
    day = args.date or default_day(_dt.datetime.now(CT))
    trading = is_trading_day(day)
    if not trading and not args.force:
        print(f"Skipped: {day} is not a trading day. No report written "
              f"(--force to report anyway).")
        return 0
    cfg = load_cfg()
    gcfg = load_gflip_cfg()
    k_grid = args.k or list(K_GRID)
    horizons = args.horizons or list(FORWARD_MIN)
    not_before_for = _not_before_fn(cfg)
    need = int(cfg["baseline_sessions"])
    keep = max(1, int(cfg.get("keep_sessions", 1)))
    base = pathlib.Path(args.out) if args.out else OPTIONS_SCANNER / "data" / "hiro_report"
    try:
        conn = gh.connect(read_only=True)
    except Exception as e:                                  # noqa: BLE001
        print(f"Cannot open {gh.DB_PATH} read-only: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1
    per, pooled, fresh = {}, {}, {}
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
                                         not_before_for,
                                         gflip_source(conn, sym, gcfg, base, fresh))
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
    if errored and measured:
        print(f"{len(errored)} of {len(symbols)} symbols failed "
              f"({', '.join(errored)}); the rest were reported.", file=sys.stderr)

    out_dir = base / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    path.write_text(build_report(day.isoformat(), per, cfg, notes, pooled, gcfg),
                    encoding="utf-8")
    print(f"Wrote {path}")
    for d, by_symbol in sorted(fresh.items()):
        try:
            write_saved_gflips(base, d, by_symbol, gcfg)
        except Exception as e:                              # noqa: BLE001
            print(f"Could not save the Hedging flips for {d}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
