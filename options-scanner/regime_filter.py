"""
regime_filter.py - Sentiment/trend regime filter for spread side selection.

Reads the SentimentDashboard bridge JSON and combines the composite sentiment
score (1-10, high=bullish) with the trend_regime structural state to decide
which sides (PCS / CCS) are allowed in the current regime.

Both votes must agree to block a side. If they disagree, both sides stay open
and the divergence is logged — single-source blocks are suppressed. This
prevents whipsaw from a single noisy sentiment reading while still catching
sustained trends like the 2026-04/05 CCS bleed (235 trades, 23% wins,
-$30,494) that motivated the original filter.
"""

import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import BRIDGE_PATH

log = logging.getLogger("scanner.regime")

# Sole source: the shared sentiment bridge (canonical location: shared/).

# Composite score thresholds (1-10, normal/non-contrarian: high = bullish).
BULLISH_SCORE = 6.5   # >= this: sentiment votes bullish (block CCS if trend agrees)
BEARISH_SCORE = 3.5   # <= this: sentiment votes bearish (block PCS if trend agrees)

# Trend regime confidence below which the trend vote becomes a soft lean
# (insufficient on its own to block — still requires sentiment agreement,
# which is already the rule, so this mostly affects logging/exposed fields).
MIN_TREND_CONFIDENCE = 0.6
MIN_AGGREGATE_CONFIDENCE = 0.5

# Max age of bridge file before it's considered stale (hours).
MAX_BRIDGE_AGE_HOURS = 36

# trend_regime.state -> side vote ("bull" blocks CCS, "bear" blocks PCS,
# None means no trend vote). "lean_*" states only contribute if sentiment
# also agrees (the AND rule below), and can't satisfy the double-hard block
# under low confidence / divergence.
#
# Keys MUST match the market-state classifier's 5 states exactly
# (sentiment-dashboard/scoring/market_state.py STATE_LABELS), a direction ×
# aggression vocabulary. Confirmed direction states (bullish/bearish) are hard
# votes; the two lack_of_* states are soft leans (lack_of_bearishness = a
# resilient tape with undefended puts -> lean bullish / favor PCS;
# lack_of_bullishness = buyer exhaustion at highs -> lean bearish / favor CCS);
# neutral casts no vote.
_TREND_STATE_VOTE = {
    "bullish":             "bull",       # motivated buying -> block CCS
    "lack_of_bullishness": "lean_bear",  # exhaustion at highs -> soft-favor CCS
    "neutral":             None,         # both sides allowed
    "lack_of_bearishness": "lean_bull",  # resilient, puts undefended -> soft-favor PCS
    "bearish":             "bear",       # urgent selling -> block PCS
}


def _load_bridge(path=BRIDGE_PATH):
    """Read the bridge file. Returns None if missing or unreadable."""
    try:
        if not path.exists():
            log.info(f"Sentiment bridge not found at {path} — regime filter inactive")
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"Failed to read sentiment bridge: {e}")
        return None


def _is_stale(bridge):
    """Return True if the bridge's generated_at is older than MAX_BRIDGE_AGE_HOURS."""
    gen = bridge.get("generated_at")
    if not gen:
        return False
    try:
        ts = datetime.fromisoformat(gen.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        if age_h > MAX_BRIDGE_AGE_HOURS:
            log.warning(f"Sentiment bridge is {age_h:.1f}h old — treating as inactive")
            return True
    except (ValueError, TypeError):
        pass
    return False


def _sentiment_vote(score, bias):
    """Return 'bull' / 'bear' / None based on composite score + bias."""
    if (score is not None and score >= BULLISH_SCORE) or bias == "long":
        return "bull"
    if (score is not None and score <= BEARISH_SCORE) or bias == "short":
        return "bear"
    return None


def _trend_vote(trend_regime):
    """Return ('bull'|'bear'|'lean_bull'|'lean_bear'|None, confidence)."""
    if not trend_regime:
        return None, None
    state = (trend_regime.get("state") or "").lower()
    conf = trend_regime.get("confidence")
    return _TREND_STATE_VOTE.get(state), conf


def evaluate_regime(bridge=None, technicals_by_symbol=None, path=BRIDGE_PATH):
    """Evaluate the current regime and return side-allowance flags.

    Args:
        bridge: pre-loaded bridge dict (for tests). If None, loaded from `path`.
        technicals_by_symbol: optional {symbol: {"trend": "BULLISH"/...}} map.
            When provided, per-symbol decisions can re-enable a side that the
            global regime blocked, if the symbol's own technical trend disagrees.
        path: bridge file path override.

    Returns:
        dict with global gates, per-symbol overlay, and exposed bridge fields
        (trend_state, trend_confidence, aggregate_confidence, divergence_flag).
    """
    out = {
        "allow_ccs": True, "allow_pcs": True,
        "per_symbol": {},
        "composite_score": None, "bias": "unknown",
        "trend_state": None, "trend_confidence": None,
        "aggregate_confidence": None, "divergence_flag": None,
        "reason": "filter inactive (no bridge data)",
        "active": False,
    }

    if bridge is None:
        bridge = _load_bridge(path=path)
    if bridge is None or _is_stale(bridge):
        return out

    score = bridge.get("composite_score")
    bias = (bridge.get("bias") or "neutral").lower()
    trend_regime = bridge.get("trend_regime") or {}
    agg_conf = bridge.get("aggregate_confidence")
    divergence = bridge.get("divergence_flag")

    out["composite_score"] = score
    out["bias"] = bias
    out["trend_state"] = trend_regime.get("state")
    out["trend_confidence"] = trend_regime.get("confidence")
    out["aggregate_confidence"] = agg_conf
    out["divergence_flag"] = divergence
    out["active"] = True

    sent = _sentiment_vote(score, bias)
    trend, trend_conf = _trend_vote(trend_regime)

    # Treat low-confidence trend states as soft leans (downgrade hard votes).
    if trend in ("bull", "bear") and trend_conf is not None and trend_conf < MIN_TREND_CONFIDENCE:
        trend = "lean_bull" if trend == "bull" else "lean_bear"

    # Soft leans count as agreement direction but cannot block on their own —
    # which is already enforced by the AND rule below. Normalize for compare.
    def _dir(v):
        if v in ("bull", "lean_bull"):
            return "bull"
        if v in ("bear", "lean_bear"):
            return "bear"
        return None

    sent_dir = _dir(sent)
    trend_dir = _dir(trend)

    # AND-of-agreement: only block when both votes point the same way AND at
    # least one of them is a "hard" vote (not just a lean).
    hard_vote = (sent in ("bull", "bear")) or (trend in ("bull", "bear"))

    if sent_dir and trend_dir and sent_dir == trend_dir and hard_vote:
        # If aggregate confidence is too low or the bridge flagged a
        # divergence, require strict double-hard agreement to block.
        low_conf = (agg_conf is not None and agg_conf < MIN_AGGREGATE_CONFIDENCE)
        flagged = bool(divergence)
        if (low_conf or flagged) and not (sent in ("bull", "bear") and trend in ("bull", "bear")):
            out["reason"] = (
                f"agreement {sent_dir} but low confidence "
                f"(agg={agg_conf}, divergence={divergence}) — both sides allowed"
            )
        else:
            if sent_dir == "bull":
                out["allow_ccs"] = False
                out["reason"] = (
                    f"bullish regime (score={score}, trend={out['trend_state']}) — CCS blocked"
                )
            else:
                out["allow_pcs"] = False
                out["reason"] = (
                    f"bearish regime (score={score}, trend={out['trend_state']}) — PCS blocked"
                )
    elif sent_dir and trend_dir and sent_dir != trend_dir:
        out["reason"] = (
            f"divergence: sentiment={sent_dir} (score={score}) vs "
            f"trend={trend_dir} ({out['trend_state']}) — both sides allowed"
        )
        log.info(out["reason"])
    else:
        out["reason"] = (
            f"neutral/insufficient agreement (score={score}, "
            f"trend={out['trend_state']}) — both sides allowed"
        )

    # Per-symbol overlay: only RE-ENABLE a side when symbol trend disagrees
    # with the global block. Never tighten further at neutral scores — trust
    # the SentimentDashboard's classification (see test_neutral_score_with_bullish_trend_does_not_tighten).
    if technicals_by_symbol:
        for sym, tech in technicals_by_symbol.items():
            trend_t = (tech or {}).get("trend", "NEUTRAL")
            allow_ccs = out["allow_ccs"]
            allow_pcs = out["allow_pcs"]
            reason_parts = []

            if not allow_ccs and trend_t in ("BEARISH", "WEAKENING"):
                allow_ccs = True
                reason_parts.append(f"global bullish but {sym} trend={trend_t} — CCS re-enabled")

            if not allow_pcs and trend_t in ("BULLISH", "RECOVERING"):
                allow_pcs = True
                reason_parts.append(f"global bearish but {sym} trend={trend_t} — PCS re-enabled")

            out["per_symbol"][sym] = {
                "allow_ccs": allow_ccs,
                "allow_pcs": allow_pcs,
                "trend": trend_t,
                "reason": "; ".join(reason_parts) if reason_parts else "follows global",
            }

    return out


def filter_signals(signals, regime, symbol=None):
    """Drop signals whose side is blocked by the regime.

    IC signals pass through unchanged — both legs are required for the structure
    and the IC's risk is symmetric, unlike a naked CCS in a bull tape.
    """
    if not regime or not regime.get("active"):
        return signals

    kept = []
    blocked = {"PCS": 0, "CCS": 0}
    for s in signals:
        t = s.get("type")
        if t == "IC":
            kept.append(s)
            continue
        if s.get("mode") == "DIRECTIONAL":
            # Directional signals are explicitly regime-aligned and should not
            # be filtered out by the regime gate that produced them.
            kept.append(s)
            continue
        sym = symbol or s.get("symbol")
        per = regime.get("per_symbol", {}).get(sym)
        if per is not None:
            allow_ccs = per["allow_ccs"]
            allow_pcs = per["allow_pcs"]
        else:
            allow_ccs = regime["allow_ccs"]
            allow_pcs = regime["allow_pcs"]

        if t == "CCS" and not allow_ccs:
            blocked["CCS"] += 1
            continue
        if t == "PCS" and not allow_pcs:
            blocked["PCS"] += 1
            continue
        kept.append(s)

    if blocked["CCS"] or blocked["PCS"]:
        log.info(f"Regime filter dropped: PCS={blocked['PCS']} CCS={blocked['CCS']} "
                 f"(reason: {regime.get('reason')})")
    return kept
