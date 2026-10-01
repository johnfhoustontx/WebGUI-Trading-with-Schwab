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
hedging pushed it in the 5 and 15 minutes after each fire.

It REPLAYS the live rules -- ``hiro.detect_surge`` over every minute's prefix
with the clock set to that minute, ``hiro.flip_transitions`` for the reversals,
the handler's sigma rule (``handlers._hiro_sigma``) and its cooldowns -- and
re-implements none of them, so the report cannot drift from what the Flow screen
showed.

USAGE
-----
    .venv/bin/python tools/hiro_report.py                     # the latest closed session
    .venv/bin/python tools/hiro_report.py --date 2026-09-30   # re-run a past day
    .venv/bin/python tools/hiro_report.py --date 2026-10-03 --force

Reads ``options-scanner/gex_history.db`` read-only and nothing else (no proxy,
no Redis, no secrets), so a missed day can be re-run later with ``--date`` for as
long as ``[hiro].keep_sessions`` keeps its minutes. Skips with exit 0 on a
weekend or NYSE holiday unless ``--force``. Writes
``options-scanner/data/hiro_report/<date>/report.md`` (``--out`` replaces the
``hiro_report`` directory).

Scheduled daily by ``trading-<env>-hiro-report.timer`` at ``[slots.hiro_report]``.
"""
from __future__ import annotations

import argparse
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

# A forward return reads the first row at or after the horizon; one further than
# this past it means a collection gap, and the horizon would silently stretch.
_HORIZON_SLACK_SEC = 120

_UP = {"dealers_buying": 1, "to_buying": 1}
_DOWN = {"dealers_selling": -1, "to_selling": -1}

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


# --- replay ------------------------------------------------------------------

def replay_surges(rows, sigma, cfg, k):
    """Every surge the live rule would have fired at bar k, minute by minute,
    honouring the per-direction cooldown -- detect_surge over each prefix with
    now_ts = that minute (so freshness behaves as live).

    The cooldown is the handler's (``flow_alerts._on_cooldown``): a side stays
    quiet while fewer than ``cooldown_min`` minutes have passed since it fired.
    Each prefix is cut to the rows inside the window before it reaches
    ``detect_surge`` -- the rule reads only the newest row and the window ending
    there, so the answer is identical and the replay stays linear."""
    if _usable_sigma(sigma) is None or not rows:
        return []
    c = {**cfg, "k": float(k)}
    window = int(c["window_min"]) * 60
    cool = float(c["cooldown_min"]) * 60
    last = {}
    out = []
    lo = 0
    for i, r in enumerate(rows):
        end = r["ts"]
        while rows[lo]["ts"] <= end - window:
            lo += 1
        a = hiro.detect_surge("", rows[lo:i + 1], sigma, c, now_ts=end)
        if not a:
            continue
        if not flow_alerts._on_cooldown(last, a["side"], end, cool):
            last[a["side"]] = end
            out.append(a)
    return out


def replay_flips(rows, sigma, cfg, not_before_ts):
    """The reversals the live rule would have fired (hiro.flip_transitions with
    band = flip_band * sigma, honouring flip_cooldown_min).

    Live, every transition is seen on the minute it happens (well inside
    FLIP_MAX_AGE_SEC) and fires unless the symbol's one reversal cooldown is
    running. ``flip_transitions`` is causal, so replaying it over the whole day
    gives the same transitions each minute's prefix would."""
    s = _usable_sigma(sigma)
    if s is None or not rows:
        return []
    band = float(cfg["flip_band"]) * s
    cool = float(cfg["flip_cooldown_min"]) * 60
    spot_at = {r["ts"]: r.get("spot") for r in rows}
    cooldowns, out = {}, []
    for ts, state, cum in hiro.flip_transitions(rows, band, not_before_ts):
        if flow_alerts._on_cooldown(cooldowns, "flip", ts, cool):
            continue
        cooldowns["flip"] = ts
        out.append({"type": "hiro_flip",
                    "side": "to_buying" if state == "buying" else "to_selling",
                    "ts": ts, "spot": spot_at.get(ts), "cum": cum})
    return out


def forward_return(rows, fire, minutes):
    """Spot return `minutes` after a fire, signed so + means price moved the way
    the dealers' hedging pushed (dealers_buying / to_buying = up). None when the
    session ends before the horizon or a spot is unusable."""
    sign = _UP.get(fire.get("side")) or _DOWN.get(fire.get("side"))
    s0 = _finite(fire.get("spot"))
    if sign is None or s0 is None or s0 <= 0:
        return None
    target = fire["ts"] + int(minutes) * 60
    after = next((r for r in rows if r["ts"] >= target), None)
    if after is None or after["ts"] - target > _HORIZON_SLACK_SEC:
        return None
    s1 = _finite(after.get("spot"))
    if s1 is None or s1 <= 0:
        return None
    return sign * (s1 / s0 - 1.0)


# --- per symbol ------------------------------------------------------------------

def _sigma(rows, prior_sessions, cfg):
    """The handler's rule (``handlers._hiro_sigma``): a usable prior-session
    sigma, else today's own full windows. Returns ``(sigma, source)``."""
    p = hiro.prior_sigma(prior_sessions, cfg)
    if isinstance(p, float) and math.isfinite(p) and p > 0:
        return p, "prior"
    t = _usable_sigma(hiro.baseline_sigma([], rows, cfg))
    return (t, "today") if t is not None else (None, None)


def _unclassified(rows):
    cls = sum(v for v in (_finite(r.get("classified_vol")) for r in rows) if v is not None)
    unc = sum(v for v in (_finite(r.get("unclassified_vol")) for r in rows) if v is not None)
    total = cls + unc
    return unc / total if total > 0 else None


def _returns(rows, fires, horizons):
    out = {}
    for h in horizons:
        vals = (forward_return(rows, f, h) for f in fires)
        out[h] = [v for v in vals if v is not None]
    return out


def symbol_summary(rows, prior_sessions, cfg, k_grid, horizons, not_before_ts):
    """{fires_by_k, flips, unclassified (share of the day's volume), sigma,
    sigma_source ('prior' | 'today' | None), rows, fwd: {k_live: {h: [returns]}},
    flip_fwd: {h: [returns]}} -- fwd only for the LIVE k (cfg['k']).

    The live ``k`` and ``push_k`` are always in the grid. With no sigma the rules
    do not run, so every count is None (not measured), never 0."""
    k_live = float(cfg["k"])
    grid = sorted({float(k) for k in k_grid} | {k_live, float(cfg["push_k"])})
    horizons = [int(h) for h in horizons]
    sigma, source = _sigma(rows, prior_sessions, cfg)
    out = {"rows": len(rows), "sigma": sigma, "sigma_source": source,
           "unclassified": _unclassified(rows), "k_live": k_live,
           "push_k": float(cfg["push_k"]), "horizons": horizons}
    if sigma is None:
        out.update(fires_by_k={k: None for k in grid}, flips=None, surges=[],
                   events=[], fwd={k_live: {h: [] for h in horizons}},
                   flip_fwd={h: [] for h in horizons})
        return out
    fires = {k: replay_surges(rows, sigma, cfg, k) for k in grid}
    flips = replay_flips(rows, sigma, cfg, not_before_ts)
    events = [{"ts": f["ts"], "side": f["side"],
               "kind": "Surge" if f["type"] == "hiro_surge" else "Reversal",
               "size": (f"{f['mult']:.1f}×" if f["type"] == "hiro_surge"
                        else _money(f.get("cum"))),
               "fwd": {h: forward_return(rows, f, h) for h in horizons}}
              for f in sorted(fires[k_live] + flips, key=lambda f: f["ts"])]
    out.update(fires_by_k={k: len(v) for k, v in fires.items()},
               flips=len(flips), surges=fires[k_live], events=events,
               fwd={k_live: _returns(rows, fires[k_live], horizons)},
               flip_fwd=_returns(rows, flips, horizons))
    return out


# --- the report --------------------------------------------------------------------

def _pct(x, digits=0):
    return DASH if x is None else f"{x * 100:.{digits}f}%"


def _bp(x):
    return DASH if x is None else f"{x * 1e4:+.1f} bp"


def _money(x):
    if x is None:
        return DASH
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:.1f}{unit}"
    return f"${x:,.0f}"


def _stats(vals, measured):
    """(n, hit rate, mean) cells. Not measured -> all dashes; measured with no
    returns -> n is a real 0, the rest dashes."""
    if not measured:
        return DASH, DASH, DASH
    if not vals:
        return "0", DASH, DASH
    hit = sum(1 for v in vals if v > 0) / len(vals)
    return str(len(vals)), _pct(hit), _bp(sum(vals) / len(vals))


def _ct(ts):
    return _dt.datetime.fromtimestamp(ts, CT).strftime("%H:%M")


def _k_label(k, s):
    tags = [t for t, v in (("live", s["k_live"]), ("push", s["push_k"])) if k == v]
    return f"{k:.1f}" + (f" ({', '.join(tags)})" if tags else "")


def _symbol_section(sym, s):
    L = [f"## {sym}", ""]
    if s and "error" in s:
        L += [f"Skipped: {s['error']}", ""]
        return L
    if not s or not s.get("rows"):
        L += ["Nothing measured: no minutes stored for this day.", ""]
        return L
    measured = s["sigma"] is not None
    source = {"prior": "the prior sessions",
              # Live, a today-sourced size grows through the morning and the
              # rules wait for min_minutes; the replay uses the day's final
              # size throughout, so these counts are approximate.
              "today": "today's own minutes (not enough prior sessions; the "
                       "replay uses the day's final size, so counts are "
                       "approximate)"}
    L += [f"- Minutes measured: {s['rows']}",
          f"- Volume with no buy/sell label: {_pct(s['unclassified'], 1)}",
          f"- Normal 15-minute size: {_money(s['sigma'])}"
          + (f", from {source[s['sigma_source']]}" if measured else
             " — too little data, so the rules did not run"),
          ""]
    L += ["| Surge at (× normal) | Fires |", "|---|---|"]
    for k, n in s["fires_by_k"].items():
        L.append(f"| {_k_label(k, s)} | {DASH if n is None else n} |")
    L.append(f"| Reversals | {DASH if s['flips'] is None else s['flips']} |")
    L.append("")
    L += ["| Signal | Horizon | n | Hit rate | Mean return |", "|---|---|---|---|---|"]
    k_live = s["k_live"]
    for h in s["horizons"]:
        n, hit, mean = _stats(s["fwd"][k_live][h], measured)
        L.append(f"| Surge at {k_live:.1f} (live) | {h} min | {n} | {hit} | {mean} |")
    for h in s["horizons"]:
        n, hit, mean = _stats(s["flip_fwd"][h], measured)
        L.append(f"| Reversal | {h} min | {n} | {hit} | {mean} |")
    L.append("")
    if s["events"]:
        hs = s["horizons"]
        L += ["| Time (CT) | Signal | Direction | Size | "
              + " | ".join(f"{h} min" for h in hs) + " |",
              "|---|---|---|---|" + "---|" * len(hs)]
        for e in s["events"]:
            L.append(f"| {_ct(e['ts'])} | {e['kind']} | {e['side'].replace('_', ' ')} | "
                     f"{e['size']} | " + " | ".join(_bp(e['fwd'][h]) for h in hs) + " |")
        L.append("")
    return L


def build_report(day, per_symbol, cfg):
    """Markdown. Per symbol: rows measured, unlabelled share, sigma + source,
    fires at each k (mark the live k and push_k), reversals; for live-k surges
    and for reversals at each horizon: n, hit rate (share of returns > 0), mean
    return. A short plain-English header says it is a MODEL, what 'hit' means,
    and that a push stays off until several sessions show hits well above 50%.
    Symbols with no rows say so explicitly ('no minutes stored'), never a zero."""
    L = [f"# HIRO-model hedging-flow report — {day}", "",
         "This measures a **model** of dealer hedging flow, not SpotGamma's HIRO. "
         "Schwab publishes no trade tape, so each contract gets one buy/sell label "
         "a minute from where its last price sits against the bid and ask.", "",
         "A **hit** is a fire after which the price moved the way the modelled "
         "dealer hedging pushed it (up after dealers buying, down after dealers "
         "selling) over the horizon shown. A coin flip hits half the time. The "
         "phone push stays off until several sessions show hit rates well above "
         "50% at the live setting.", "",
         f"Live settings: Surge at {float(cfg['k']):.1f}× normal "
         f"(push at {float(cfg['push_k']):.1f}×), a {int(cfg['window_min'])}-minute "
         f"window, quiet {int(cfg['cooldown_min'])} minutes per direction; "
         f"reversal dead zone {float(cfg['flip_band']):.2f}× normal, none before "
         f"{cfg.get('flip_not_before', '09:00')} CT, quiet "
         f"{int(cfg['flip_cooldown_min'])} minutes.", ""]
    for sym, s in per_symbol.items():
        L += _symbol_section(sym, s)
    return "\n".join(L)


# --- CLI ---------------------------------------------------------------------------

def _float_list(s):
    return [float(x) for x in s.split(",") if x.strip()]


def _int_list(s):
    return [int(x) for x in s.split(",") if x.strip()]


def parse_args(argv):
    p = argparse.ArgumentParser(description="Daily HIRO-model validation report.")
    p.add_argument("--date", type=_dt.date.fromisoformat, default=None,
                   help="the session to report, YYYY-MM-DD (default: today after the "
                        "close, else the previous trading day)")
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
    """The session a run with no ``--date`` reports: today once the regular
    session has closed, else the previous trading day -- so a catch-up run at
    boot (the timer is Persistent) reports the session it missed rather than an
    empty morning. A non-trading day is returned as itself, for the gate."""
    d = now.date()
    if not is_trading_day(d):
        return d
    _open, close = _session_bounds("regular")
    return d if now.time() > close else prev_trading_day(d)


def _symbols(cfg):
    raw = cfg.get("symbols")
    if isinstance(raw, str):
        raw = [raw]
    return [s for s in raw if isinstance(s, str)] if isinstance(raw, (list, tuple)) else []


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else list(argv))
    day = args.date or default_day(_dt.datetime.now(CT))
    if not is_trading_day(day) and not args.force:
        print(f"Skipped: {day} is not a trading day. No report written "
              f"(--force to report anyway).")
        return 0
    cfg = load_cfg()
    k_grid = args.k or list(K_GRID)
    horizons = args.horizons or list(FORWARD_MIN)
    not_before = hiro.ct_ts(day, cfg.get("flip_not_before", "09:00"))
    try:
        conn = gh.connect(read_only=True)
    except Exception as e:                                  # noqa: BLE001
        print(f"Cannot open {gh.DB_PATH} read-only: {type(e).__name__}: {e}")
        return 1
    per = {}
    try:
        for sym in _symbols(cfg):
            try:
                rows = gh.load_hiro_day(conn, sym, day)
                if not rows:
                    per[sym] = None
                    continue
                prior = gh.load_hiro_prior_sessions(
                    conn, sym, int(cfg["baseline_sessions"]), before=day)
                per[sym] = symbol_summary(rows, prior, cfg, k_grid, horizons,
                                          not_before)
            except Exception as e:                          # noqa: BLE001
                per[sym] = {"rows": -1, "error": f"{type(e).__name__}: {e}"}
                print(f"  {sym}: skipped ({type(e).__name__}: {e})")
    finally:
        conn.close()
    base = pathlib.Path(args.out) if args.out else OPTIONS_SCANNER / "data" / "hiro_report"
    out_dir = base / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    path.write_text(build_report(day.isoformat(), per, cfg), encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
