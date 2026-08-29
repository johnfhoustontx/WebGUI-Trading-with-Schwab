"""Is the live edge holding? — the monitor over the journal's labelled rows.

PURE: the caller passes rows; nothing here reads a database. Never raises.

**The live statistic is not the fit's statistic, and saying so is the point.**
The artifact's OOS IC is the mean of per-DATE cross-sectional Spearman
correlations — it asks "within one day's cross-section, did the ranking predict
the ordering?". Live readings are sparse: a handful of symbols on a typical day,
often one. A per-date IC mostly cannot be computed at all, and the pooled
correlation that CAN be computed answers a different question ("across all
readings ever, did a higher score go with a better outcome?").

Reporting the pooled number under the artifact's name would be an
apples-to-oranges comparison dressed as a decay finding. So both are computed,
the pooled one is always labelled as not-comparable, and ``decay`` is populated
ONLY from the by-date statistic — which usually means not at all, and that is
the honest state rather than a defect.

**Beta-awareness is not optional here.** Phase 4 measured this model at
cross-sectional IC +0.16 when the market rises and −0.11 when it falls: the
measured edge IS beta. A monitor scoring itself on the raw forward excess would
read healthy through any rising market and reproduce that illusion exactly. So
the same IC is computed on the beta-adjusted label, and the sample is split on
the market's own direction.

**Too little data is an answer.** Three readings is not a thin edge; it is no
measurement, and it must not render as one.
"""
import math
from services import _degrade

HORIZON_KEY = "fwd_20d"          # the model's own horizon
HORIZON_KEY_BA = "fwd_20d_ba"
MARKET_KEY = "mkt_fwd_20d"

# Below this a rank correlation is dominated by its own sampling noise. The
# model's whole measured edge is ~0.02, so a number computed from a dozen
# readings would be noise printed at two decimal places.
MIN_READINGS = 20
# A per-date cross-sectional IC needs a real cross-section. Matches
# `backtest._spearman`, which returns NaN below five names.
MIN_NAMES_PER_DATE = 5


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _spearman(pairs):
    """Rank correlation over ``[(x, y), …]``. None when it cannot be computed."""
    pts = [(a, b) for a, b in pairs
           if _num(a) is not None and _num(b) is not None]
    if len(pts) < 5:
        return None
    xs = _ranks([p[0] for p in pts])
    ys = _ranks([p[1] for p in pts])
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _ranks(vals):
    """Average ranks, so ties do not manufacture an ordering."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def _side(rows, want):
    picked = [r for r in rows
              if str(r.get("swing_verdict") or "").upper() == want]
    fwds = [_num(r.get(HORIZON_KEY)) for r in picked]
    fwds = [f for f in fwds if f is not None]
    return {
        "n": len(picked),
        "mean_fwd": (sum(fwds) / len(fwds)) if fwds else None,
        "hit_rate": (sum(1 for f in fwds if f > 0) / len(fwds)) if fwds else None,
        "ic": _spearman([(r.get("composite"), r.get(HORIZON_KEY)) for r in picked]),
    }


def symbol_history(rows, limit=5):
    """This name's recent reads and what followed — ROWS, not a statistic.

    Five reads can never support a correlation, so this deliberately returns no
    IC: it is a record, and the reader draws their own line through it. Every
    row says whether its outcome is known yet, because an unmatured read and a
    flat one are different facts and a blank cell conflates them."""
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        fwd = _num(r.get(HORIZON_KEY))
        out.append({
            "date": r.get("reading_date"),
            "percentile": r.get("percentile"),
            "verdict": r.get("swing_verdict"),
            "composite": _num(r.get("composite")),
            "result": fwd,
            "pending": fwd is None,
        })
    out.sort(key=lambda d: (d["date"] or ""), reverse=True)
    return out[:int(limit)] if limit else out


def compute(rows, artifact_oos_ic=None):
    """The monitor's reading over ``rows`` (labelled journal readings)."""
    out = {
        "status": "insufficient", "n_labelled": 0, "min_required": MIN_READINGS,
        "pooled_ic": None, "pooled_ic_beta_adj": None,
        "by_date_ic": None, "comparable_to_artifact": False,
        "ic_market_up": None, "ic_market_down": None,
        "artifact_oos_ic": artifact_oos_ic, "decay": None,
        "long": {"n": 0, "mean_fwd": None, "hit_rate": None, "ic": None},
        "short": {"n": 0, "mean_fwd": None, "hit_rate": None, "ic": None},
        "horizon_days": 20,
    }
    try:
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        labelled = [r for r in rows
                    if _num(r.get("composite")) is not None
                    and _num(r.get(HORIZON_KEY)) is not None]
        out["n_labelled"] = len(labelled)
        if len(labelled) < MIN_READINGS:
            return out

        out["status"] = "ok"
        out["pooled_ic"] = _spearman(
            [(r["composite"], r[HORIZON_KEY]) for r in labelled])
        out["pooled_ic_beta_adj"] = _spearman(
            [(r.get("composite"), r.get(HORIZON_KEY_BA)) for r in labelled])

        # The comparable statistic: per-date cross-sectional IC, averaged.
        by_date = {}
        for r in labelled:
            by_date.setdefault(r.get("reading_date"), []).append(r)
        day_ics = []
        for day_rows in by_date.values():
            if len(day_rows) < MIN_NAMES_PER_DATE:
                continue
            ic = _spearman([(r["composite"], r[HORIZON_KEY]) for r in day_rows])
            if ic is not None:
                day_ics.append(ic)
        if day_ics:
            out["by_date_ic"] = sum(day_ics) / len(day_ics)
            out["comparable_to_artifact"] = True
            if _num(artifact_oos_ic) is not None:
                out["decay"] = out["by_date_ic"] - float(artifact_oos_ic)

        up = [r for r in labelled if (_num(r.get(MARKET_KEY)) or 0) > 0]
        down = [r for r in labelled if (_num(r.get(MARKET_KEY)) or 0) < 0]
        out["ic_market_up"] = _spearman(
            [(r["composite"], r[HORIZON_KEY]) for r in up])
        out["ic_market_down"] = _spearman(
            [(r["composite"], r[HORIZON_KEY]) for r in down])

        out["long"] = _side(labelled, "BUY")
        out["short"] = _side(labelled, "SELL")
        return out
    except Exception:
        _degrade.degraded("trade.compute")
        return out
