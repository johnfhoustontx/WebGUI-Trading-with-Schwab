"""Pure options-flow alert detection (crossover + unusual-activity) + config.

Operates on a symbol's day flow series (list of (ts, spot, call_vol, put_vol,
call_prem, put_prem) tuples from gex_history_db.load_flow_series) and a cooldown
map. No I/O, no push — the handler wires those. See the design doc."""
import logging
import tomllib

from repo_paths import FLOW_ALERTS_TOML

log = logging.getLogger(__name__)
_TOML_PATH = FLOW_ALERTS_TOML

_DEFAULTS = {
    "enabled": True,
    "crossover": {"band": 0.02, "cooldown_min": 30, "min_premium": 10000},
    "spike": {"k": 4.0, "window": 20, "floor": 500, "min_points": 5,
              "cooldown_min": 20, "min_baseline": 100},
}


def _merge(base, over):
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_TOML_CACHE = {"mtime": None, "cfg": None}


def reset_thresholds_cache():
    """Drop the mtime-cached thresholds (test helper)."""
    _TOML_CACHE.update(mtime=None, cfg=None)


def load_thresholds() -> dict:
    """flow_alerts.toml merged over the built-in defaults. Never raises.

    mtime-cached: this is read on every 1-min flow-alert tick, but the file
    rarely changes — re-parse only when its mtime moves (or it's missing)."""
    try:
        import os
        mtime = os.stat(_TOML_PATH).st_mtime
    except Exception:
        mtime = None
    if _TOML_CACHE["cfg"] is not None and _TOML_CACHE["mtime"] == mtime:
        return _TOML_CACHE["cfg"]
    try:
        with open(_TOML_PATH, "rb") as fh:
            cfg = _merge(_DEFAULTS, tomllib.load(fh))
    except Exception:
        log.debug("flow_alerts.toml load failed → defaults", exc_info=True)
        cfg = _merge(_DEFAULTS, {})
    _TOML_CACHE.update(mtime=mtime, cfg=cfg)
    return cfg


def _norm(series):
    """[(ts, spot, call_vol, put_vol, call_prem, put_prem), …] → list of dicts,
    dropping rows with a non-numeric ts."""
    out = []
    for r in series or []:
        if len(r) < 6 or not isinstance(r[0], (int, float)):
            continue
        out.append({"ts": r[0], "call_vol": r[2] or 0, "put_vol": r[3] or 0,
                    "call_prem": r[4], "put_prem": r[5]})
    return out


def detect_crossover(series, band, min_premium=10000):
    """Alert dict when net=call_prem-put_prem flips sign vs the prior snapshot and the
    new lead clears `band` × the larger side; else None. side: calls_over | puts_over.
    Skipped entirely when the larger side is below `min_premium` ($) — tiny-premium
    open-session noise."""
    return _crossover_rows(_norm(series), band, min_premium)


def _crossover_rows(norm_rows, band, min_premium=10000):
    """Crossover detection over ALREADY-normalized rows (see detect_crossover)."""
    rows = [r for r in norm_rows
            if isinstance(r["call_prem"], (int, float)) and isinstance(r["put_prem"], (int, float))]
    if len(rows) < 2:
        return None
    prev, cur = rows[-2], rows[-1]
    n0 = prev["call_prem"] - prev["put_prem"]
    n1 = cur["call_prem"] - cur["put_prem"]
    crossed = (n0 < 0 < n1) or (n1 < 0 < n0)
    larger = max(cur["call_prem"], cur["put_prem"], 1.0)
    if larger < min_premium:
        return None
    if not crossed or abs(n1) < band * larger:
        return None
    side = "calls_over" if n1 > 0 else "puts_over"
    return {"type": "crossover", "side": side, "ts": cur["ts"],
            "call_prem": cur["call_prem"], "put_prem": cur["put_prem"]}


def _increments(rows, field):
    out = []
    for i in range(1, len(rows)):
        d = (rows[i][field] or 0) - (rows[i - 1][field] or 0)
        out.append(d if d > 0 else 0.0)   # cumulative shouldn't drop; guard
    return out


def detect_spike(series, side, k, floor, window, min_points, min_baseline=100):
    """Alert dict when this-minute `side` volume increment ≥ k×trailing-avg AND ≥ floor
    (after a warm-up of min_points increments); else None. side: call | put.
    The trailing average is floored at `min_baseline` so a dead-quiet name (baseline 0)
    still has to clear k×min_baseline — the relative test ALWAYS applies."""
    return _spike_rows(_norm(series), side, k, floor, window, min_points, min_baseline)


def _spike_rows(rows, side, k, floor, window, min_points, min_baseline=100):
    """Spike detection over ALREADY-normalized rows (see detect_spike)."""
    field = "call_vol" if side == "call" else "put_vol"
    incs = _increments(rows, field)
    if len(incs) < min_points:
        return None
    latest = incs[-1]
    base_window = incs[-1 - window:-1] if window > 0 else incs[:-1]
    baseline = (sum(base_window) / len(base_window)) if base_window else 0.0
    if latest < floor:
        return None
    if latest < k * max(baseline, min_baseline):   # relative test ALWAYS applies
        return None
    return {"type": "spike", "side": side, "ts": rows[-1]["ts"],
            "increment": latest, "baseline": baseline,
            "mult": (latest / baseline) if baseline > 0 else None}


def _on_cooldown(cooldowns, key, now_ts, cooldown_sec):
    last = cooldowns.get(key)
    return isinstance(last, (int, float)) and (now_ts - last) < cooldown_sec


def detect_flow_alerts(symbol, series, cfg, cooldowns, now_ts):
    """Run both detectors for one symbol, honoring the cooldown map (mutated in place;
    the caller persists it). Returns a list of alert dicts (each with symbol + id)."""
    out = []
    xo = cfg.get("crossover", {})
    sp = cfg.get("spike", {})
    norm_rows = _norm(series)   # normalize ONCE, shared by all three passes

    a = _crossover_rows(norm_rows, band=xo.get("band", 0.02),
                        min_premium=xo.get("min_premium", 10000))
    if a:
        key = f"{symbol}|crossover"
        if not _on_cooldown(cooldowns, key, now_ts, xo.get("cooldown_min", 30) * 60):
            cooldowns[key] = now_ts
            out.append({**a, "symbol": symbol})

    for side in ("call", "put"):
        a = _spike_rows(norm_rows, side, k=sp.get("k", 4.0), floor=sp.get("floor", 500),
                        window=sp.get("window", 20), min_points=sp.get("min_points", 5),
                        min_baseline=sp.get("min_baseline", 100))
        if a:
            key = f"{symbol}|spike|{side}"
            if not _on_cooldown(cooldowns, key, now_ts, sp.get("cooldown_min", 20) * 60):
                cooldowns[key] = now_ts
                out.append({**a, "symbol": symbol})

    for a in out:
        a["id"] = f"{a['symbol']}|{a['type']}|{a.get('side')}|{int(a['ts'])}"
        a["text"] = alert_text(a)
    return out


def alert_text(a) -> str:
    """One-line human-readable alert (reused by push + popup). No buy/sell claim."""
    s = a["symbol"]
    if a["type"] == "crossover":
        if a["side"] == "calls_over":
            return (f"{s}: call premium overtook puts — "
                    f"${a['call_prem']:,.0f} vs ${a['put_prem']:,.0f} (bullish flip)")
        return (f"{s}: put premium overtook calls — "
                f"${a['put_prem']:,.0f} vs ${a['call_prem']:,.0f} (bearish flip)")
    mult = f"{a['mult']:.1f}× avg" if a.get("mult") else "burst"
    return (f"{s}: unusual {a['side']} activity — {int(a['increment']):,} contracts "
            f"this minute ({mult})")
