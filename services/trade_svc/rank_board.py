"""The rank board — today's whole cross-section, ranked, with its gates showing.

The single-symbol card answers "what about THIS name?". The board answers "of
everything the model can see, what is best and worst right now?" — the question
that turns a research tool into a shortlist.

Four rules, each because the alternative is quietly wrong:

**One code path.** Every row is scored by `swing_model.score_symbol`, the same
function the card calls, with the same cross-sectional basis and the same regime
key. A second scoring path would drift from the card, and the two are read side
by side.

**Deciles come from TODAY's cross-section**, not the artifact's calibration
bands. The bands say where a score sat against five years; the board answers
what is best today. A universe that is uniformly mid-band still has a best and a
worst name.

**Gate-disqualified rows are MARKED, not dropped.** A dropped row is invisible;
a marked one is a finding — "the top-ranked name reports earnings in two days"
is exactly what you opened the board to learn.

**An empty or unusual short pool must say WHY.** The model predicts excess
return vs SPY, so in an uptrend a bottom-decile name is predicted to LAG, not to
fall. Left unlabelled that invites a directional short the tape has refused.

⚠ **The board ranks by a composite that is largely a volatility bet** (Phase 4:
cross-sectional IC +0.16 when the market rises, −0.11 when it falls). So the top
decile skews to the highest-beta names. `risk_share` travels with the payload
because on a RANKED board that is the single most important thing to know.

Pure: the caller injects the snapshot, artifact, clearance and gate context.
Never raises.
"""
import math

MIN_UNIVERSE = 10       # below this there is no bottom decile to speak of
POOL_FRACTION = 0.10
EARNINGS_GATE_DAYS = 20  # the model's own horizon

# The board evaluates a SUBSET of the card's gates — everything derivable from
# the daily snapshot plus two cheap lookups. Naming them is load-bearing: an
# ungated row must not read as "cleared everything the card checks".
GATES_EVALUATED = (
    "earnings inside the 20-day horizon (both sides)",
    "price below its 200-EMA (longs)",
    "short-squeeze risk (shorts)",
)


def flat_basis(snapshot):
    """``{factor: [values]}`` — the cross-section the scorer z-scores against.

    Mirrors ``compute.flat_basis``; duplicated here so the board module is
    importable and testable without pulling in the orchestrator."""
    by_symbol = (snapshot or {}).get("by_symbol") or {}
    if not by_symbol:
        return (snapshot or {}).get("factors") or {}
    basis = {}
    for row in by_symbol.values():
        for f, v in (row or {}).items():
            basis.setdefault(f, []).append(v)
    return basis


def _decile(rank_asc, n):
    """1..10 with **10 the highest** composite. Ascending 0-based rank in."""
    if n <= 0:
        return None
    return max(1, min(10, math.ceil((rank_asc + 1) / n * 10)))


def _gates(symbol, factors, ctx):
    """``(gates, gated_long, gated_short)`` for one row."""
    gates, glong, gshort = [], False, False
    days = (ctx.get("earnings_days") or {}).get(symbol)
    if isinstance(days, (int, float)) and 0 <= days <= EARNINGS_GATE_DAYS:
        gates.append(f"earnings in {int(days)} days")
        glong = gshort = True
    below = (factors or {}).get("below_200ema")
    if isinstance(below, (int, float)) and below < 0:
        gates.append("below its 200-EMA")
        glong = True
    squeeze = (ctx.get("squeeze") or {}).get(symbol)
    if squeeze:
        gates.append(f"squeeze risk ({squeeze})" if isinstance(squeeze, str)
                     else "squeeze risk")
        gshort = True
    return gates, glong, gshort


def build(snapshot, artifact, regime=None, clearance=None, gate_ctx=None):
    """Today's ranked cross-section. Never raises."""
    from services.trade_svc import swing_model as _swing

    clearance = clearance or {}
    ctx = gate_ctx or {}
    by_symbol = (snapshot or {}).get("by_symbol") or {}
    short_state = ((clearance.get("short") or {}).get("state")) or "cleared"

    board = {
        "rows": [], "long_pool": [], "short_pool": [],
        "n": 0, "thin_cross_section": True, "status": "ok",
        "market_filter": {
            "long": clearance.get("long") or {"state": "cleared", "reasons": []},
            "short": clearance.get("short") or {"state": "cleared", "reasons": []},
        },
        "short_expression": "relative" if short_state != "cleared" else "directional",
        "gates_evaluated": list(GATES_EVALUATED),
        "regime_key": None, "risk_share": None, "model_version": None,
        "horizon_days": (artifact or {}).get("horizon", 20),
    }
    if not artifact:
        board["status"] = "no_artifact"
        return board
    if not by_symbol:
        # ⚠ Two very different empties. `get_universe_snapshot` deliberately
        # tolerates a payload from older code carrying only the FLAT
        # `{factor: [values]}` basis — scoring works against it, and its
        # docstring says so. RANKING cannot: the flat basis has values but no
        # symbol names. Found on the first live build, where it rendered as a
        # board of zero rows, which is indistinguishable from "the market
        # offered nothing today".
        board["status"] = ("legacy_snapshot"
                           if (snapshot or {}).get("factors") else "no_snapshot")
        return board

    basis = flat_basis(snapshot)
    scored = []
    for sym, factors in by_symbol.items():
        s = _swing.score_symbol(factors, basis, artifact, regime=regime)
        if not s:
            continue          # the scorer declined — omit rather than invent a 0
        gates, glong, gshort = _gates(sym, factors, ctx)
        scored.append({
            "symbol": sym,
            "composite": s.get("score"),
            "percentile": s.get("percentile"),
            "band": None,
            "verdict": s.get("verdict"),
            "expected_fwd": s.get("expected_fwd"),
            "hit_rate": s.get("hit_rate"),
            "gates": gates,
            "gated_long": glong,
            "gated_short": gshort,
            "disqualified": bool(gates),
            "_raw": s,
        })
    if not scored:
        board["status"] = "unscoreable"
        return board

    first = scored[0]["_raw"]
    board["regime_key"] = first.get("regime_key")
    board["risk_share"] = first.get("risk_share")
    board["model_version"] = first.get("model_version")

    calib = ((artifact.get("regimes") or {}).get(board["regime_key"] or "all")
             or {}).get("calibration") or []
    scored.sort(key=lambda r: (r["composite"] is None, r["composite"]))
    n = len(scored)
    for i, row in enumerate(scored):
        row["decile"] = _decile(i, n)
        row["band"] = _band_index(row["composite"], calib)
        row["pool"] = ""
        row.pop("_raw", None)

    if n >= MIN_UNIVERSE:
        board["thin_cross_section"] = False
        k = max(1, int(round(n * POOL_FRACTION)))
        for row in scored[-k:]:
            if row["decile"] == 10:
                row["pool"] = "long"
        for row in scored[:k]:
            if row["decile"] == 1:
                row["pool"] = "short"

    scored.reverse()          # best first — the board reads top-down
    board["rows"] = scored
    board["n"] = n
    board["long_pool"] = [r["symbol"] for r in scored if r["pool"] == "long"]
    board["short_pool"] = [r["symbol"] for r in scored if r["pool"] == "short"]
    return board


def _band_index(comp, calib):
    if comp is None or not calib:
        return None
    for b in calib:
        if comp <= b.get("score_hi", 0):
            return b.get("band")
    return calib[-1].get("band")
