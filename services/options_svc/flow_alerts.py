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
    "uoa": {"k": 3.0, "vol_floor": 500, "premium_floor": 250000, "top_n": 3},
    # Dealer gamma-regime flip (spot crossing the gamma flip level). band_pct is a
    # hysteresis dead-zone (spot must clear the flip by this fraction to switch
    # regime — stops chatter when spot hovers at the flip). symbols = which names
    # to watch (empty → the whole flow universe); gamma flip is a clean read on
    # heavily-optioned index/ETF names, noise on illiquid ones.
    "gamma_flip": {"enabled": True, "band_pct": 0.0015, "cooldown_min": 60,
                   "symbols": ["$SPX", "SPY", "QQQ", "IWM"]},
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


def _mark(c):
    """Contract mark (mid) = mark field, else (bid+ask)/2. None if unusable."""
    v = c.get("mark")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    bid, ask = c.get("bid"), c.get("ask")
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and (bid + ask) > 0:
        return (bid + ask) / 2.0
    return None


def detect_uoa(symbol, chain, cfg):
    """Contract-level unusual options activity for one symbol from a live chain.

    Qualify a contract when volume/open-interest >= k AND volume >= vol_floor AND
    premium ($ = mark*vol*100) >= premium_floor; skip oi <= 0 (ratio undefined).
    Return the top_n qualifiers by premium (desc). Pure + defensive → []."""
    u = (cfg or {}).get("uoa", {})
    k = u.get("k", 3.0); vol_floor = u.get("vol_floor", 500)
    prem_floor = u.get("premium_floor", 250000); top_n = u.get("top_n", 3)
    out = []
    try:
        for side, mapkey in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            exp_map = (chain or {}).get(mapkey) or {}
            if not isinstance(exp_map, dict):
                continue
            for exp_key, strike_map in exp_map.items():
                expiry = str(exp_key).split(":")[0]
                try:
                    dte = int(str(exp_key).split(":")[1])
                except (IndexError, ValueError):
                    dte = None
                for strike_str, contracts in (strike_map or {}).items():
                    try:
                        strike = float(strike_str)
                    except (TypeError, ValueError):
                        continue
                    for c in (contracts or []):
                        try:
                            vol = c.get("totalVolume") or 0
                            oi = c.get("openInterest") or 0
                            mark = _mark(c)
                            if oi <= 0 or vol < vol_floor or mark is None:
                                continue
                            ratio = vol / oi
                            premium = mark * vol * 100
                            if ratio < k or premium < prem_floor:
                                continue
                            out.append({"type": "uoa", "side": side, "symbol": symbol,
                                        "strike": strike, "expiry": expiry, "dte": dte,
                                        "cost": mark, "volume": int(vol), "oi": int(oi),
                                        "vol_oi": ratio, "premium": premium})
                        except Exception:
                            continue
        out.sort(key=lambda a: a["premium"], reverse=True)
        return out[:top_n]
    except Exception:
        log.debug("detect_uoa failed for %s", symbol, exc_info=True)
        return []


def _human_money(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "$0"
    a = abs(v)
    if a >= 999_500:            # rounds to >= $1.00M → use M form
        return f"${v/1e6:.2f}M"
    if a >= 1e3:
        return f"${v/1e3:.0f}k"
    return f"${v:,.0f}"


def _exp_short(expiry, dte):
    if dte == 0:
        return "0DTE"
    try:
        _, m, d = str(expiry).split("-")
        return f"{int(m):02d}/{int(d):02d}"
    except Exception:
        return str(expiry)


def gamma_regime(spot, flip, prev=None, band_pct=0.0):
    """Dealer gamma regime for a symbol: 'positive' (spot above the flip → dealers
    long gamma, vol dampened), 'negative' (spot below → short gamma, vol amplified),
    or 'na' (missing data).

    Hysteresis: given a prior regime, spot must clear the flip by ``band_pct`` (a
    Schmitt trigger) to switch — so spot hovering at the flip holds the prior
    regime instead of chattering. With no prior regime the classification is a
    hard split at the flip (the day's baseline)."""
    if spot is None or flip is None or flip <= 0:
        return "na"
    if prev == "positive":
        return "negative" if spot <= flip * (1 - band_pct) else "positive"
    if prev == "negative":
        return "positive" if spot >= flip * (1 + band_pct) else "negative"
    return "positive" if spot >= flip else "negative"


def detect_gamma_flip(symbol, spot, flip, prev_regime, band_pct=0.0, ts=None):
    """Detect a gamma-regime transition for one symbol vs its prior regime.

    Returns ``(alert_or_None, new_regime)``. No alert on the baseline (no prior
    regime), on no change, or when the data is unclassifiable (regime 'na' keeps
    the prior). On a genuine transition the alert dict carries type/side/symbol/
    spot/flip/ts (side: 'to_positive' | 'to_negative'); the caller adds id + text.
    Pure — cooldown/state persistence is the handler's job."""
    new = gamma_regime(spot, flip, prev_regime, band_pct)
    if new == "na":
        return None, prev_regime
    if prev_regime in (None, "na") or new == prev_regime:
        return None, new
    side = "to_positive" if new == "positive" else "to_negative"
    return ({"type": "gamma_flip", "side": side, "symbol": symbol,
             "spot": spot, "flip": flip, "ts": ts}, new)


def _on_cooldown(cooldowns, key, now_ts, cooldown_sec):
    last = cooldowns.get(key)
    return isinstance(last, (int, float)) and (now_ts - last) < cooldown_sec


def detect_flow_alerts(symbol, series, cfg, cooldowns, now_ts):
    """Run both detectors for one symbol, honoring the cooldown map (mutated in place;
    the caller persists it). Returns a list of alert dicts (each with symbol + id)."""
    out = []
    xo = cfg.get("crossover", {})
    norm_rows = _norm(series)   # normalize ONCE

    a = _crossover_rows(norm_rows, band=xo.get("band", 0.02),
                        min_premium=xo.get("min_premium", 10000))
    if a:
        key = f"{symbol}|crossover"
        if not _on_cooldown(cooldowns, key, now_ts, xo.get("cooldown_min", 30) * 60):
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
        cp, pp = _human_money(a.get("call_prem")), _human_money(a.get("put_prem"))
        if a["side"] == "calls_over":
            return f"{s} — call premium overtook puts: {cp} calls vs {pp} puts (bullish flip)"
        return f"{s} — put premium overtook calls: {pp} puts vs {cp} calls (bearish flip)"
    if a["type"] == "uoa":
        cp = "C" if a["side"] == "call" else "P"
        return (f"{s} {_exp_short(a.get('expiry'), a.get('dte'))} {a['strike']:g}{cp} — "
                f"UNUSUAL: {a['volume']:,} vol vs {a['oi']:,} OI ({a['vol_oi']:.1f}×) · "
                f"${a['cost']:.2f} · {_human_money(a['premium'])} premium")
    if a["type"] == "gamma_flip":
        spot, flip = a.get("spot"), a.get("flip")
        if a["side"] == "to_negative":
            return (f"{s} — gamma flipped NEGATIVE: spot {spot:g} fell below the gamma flip "
                    f"{flip:g} (dealers short gamma → volatility amplified)")
        return (f"{s} — gamma flipped POSITIVE: spot {spot:g} rose above the gamma flip "
                f"{flip:g} (dealers long gamma → volatility dampened)")
    return f"{s}: flow alert"
