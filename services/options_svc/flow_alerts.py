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
    "crossover": {"band": 0.02, "cooldown_min": 30},
    "spike": {"k": 4.0, "window": 20, "floor": 500, "min_points": 5,
              "cooldown_min": 20},
}


def _merge(base, over):
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_thresholds() -> dict:
    """flow_alerts.toml merged over the built-in defaults. Never raises."""
    try:
        with open(_TOML_PATH, "rb") as fh:
            return _merge(_DEFAULTS, tomllib.load(fh))
    except Exception:
        log.debug("flow_alerts.toml load failed → defaults", exc_info=True)
        return _merge(_DEFAULTS, {})


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


def detect_crossover(series, band):
    """Alert dict when net=call_prem-put_prem flips sign vs the prior snapshot and the
    new lead clears `band` × the larger side; else None. side: calls_over | puts_over."""
    rows = [r for r in _norm(series)
            if isinstance(r["call_prem"], (int, float)) and isinstance(r["put_prem"], (int, float))]
    if len(rows) < 2:
        return None
    prev, cur = rows[-2], rows[-1]
    n0 = prev["call_prem"] - prev["put_prem"]
    n1 = cur["call_prem"] - cur["put_prem"]
    crossed = (n0 < 0 < n1) or (n1 < 0 < n0)
    larger = max(cur["call_prem"], cur["put_prem"], 1.0)
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


def detect_spike(series, side, k, floor, window, min_points):
    """Alert dict when this-minute `side` volume increment ≥ k×trailing-avg AND ≥ floor
    (after a warm-up of min_points increments); else None. side: call | put."""
    field = "call_vol" if side == "call" else "put_vol"
    rows = _norm(series)
    incs = _increments(rows, field)
    if len(incs) < min_points:
        return None
    latest = incs[-1]
    base_window = incs[-1 - window:-1] if window > 0 else incs[:-1]
    baseline = (sum(base_window) / len(base_window)) if base_window else 0.0
    if latest < floor:
        return None
    if baseline > 0 and latest < k * baseline:
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

    a = detect_crossover(series, band=xo.get("band", 0.02))
    if a:
        key = f"{symbol}|crossover"
        if not _on_cooldown(cooldowns, key, now_ts, xo.get("cooldown_min", 30) * 60):
            cooldowns[key] = now_ts
            out.append({**a, "symbol": symbol})

    for side in ("call", "put"):
        a = detect_spike(series, side, k=sp.get("k", 4.0), floor=sp.get("floor", 500),
                         window=sp.get("window", 20), min_points=sp.get("min_points", 5))
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
