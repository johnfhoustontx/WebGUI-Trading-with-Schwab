"""Frozen outputs of the Opening Scalp's chance-of-target fit.

Written on 2026-11-14 against the fit as it stood before its general parts moved
to ``shared/ml`` (design 2026-11-14-ml-foundation). The digest covers every field
of every model and of the walk-forward result, at full float precision, so a
change in the last bit of any number fails here. It must never be regenerated to
make a change pass: a moved digest means the model's numbers moved.
"""
import datetime as dt
import hashlib
import json
import math
import random

from services.options_svc import scalp_prob_fit
from services.options_svc.scalp_prob_fit import Row
from shared import market_calendar as mc
from shared import scalp_config

ROLES = ("long_with", "long_against", "credit_with", "credit_against")
FROZEN_DIGEST = "579d93b3687b394b27ec10b0ff865fa9e88812c4e952d979c631677382865738"


def _days(n, start=dt.date(2026, 2, 2)):
    out, d = [], start
    while len(out) < n:
        if mc.is_trading_day(d):
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _rows():
    """Two symbols over 90 days: slope_5 planted at 1.2, netprem read on every
    third day at 0.8, a midday block flag, and per-role labels."""
    rng = random.Random(20261114)
    rows = []
    for i, day in enumerate(_days(90)):
        for symbol in ("SPY", "QQQ"):
            x = rng.uniform(-1.0, 1.0)
            n = rng.uniform(-1.0, 1.0) if i % 3 == 0 else None
            mid = 1.0 if rng.random() < 0.4 else 0.0
            factors = {"slope_5": x, "netprem": n, "block:midday": mid,
                       "block:afternoon": 0.0}
            for role in ROLES:
                sign = 1.0 if role.endswith("_with") else -1.0
                z = sign * (1.2 * x + (0.8 * n if n is not None else 0.0)) - 0.2 * mid
                win = 1 if rng.random() < 1.0 / (1.0 + math.exp(-z)) else 0
                rows.append(Row(kind="morning", day=day, slot="08:36", symbol=symbol,
                                role=role, win=win, weight=1.0, factors=factors))
    return rows


def _canonical():
    rows = _rows()
    priors, sd = scalp_config.prob_priors(), scalp_config.prob_prior_sd()
    out = {}
    for role in ROLES:
        m = scalp_prob_fit.fit(rows, role, "morning", priors, sd)
        out[role] = {"weights": m.weights, "intercepts": m.intercepts,
                     "scaling": m.scaling, "days": m.days, "n_days": m.n_days,
                     "data_share": m.data_share}
    out["walk_forward"] = scalp_prob_fit.walk_forward(rows, "long_with", "morning",
                                                      priors, sd)
    p, pushes = scalp_prob_fit.predict(
        scalp_prob_fit.fit(rows, "long_with", "morning", priors, sd), "SPY",
        {"slope_5": 0.3, "netprem": -0.5, "block:midday": 1.0})
    out["predict"] = {"p": p, "pushes": pushes}
    return out


def _digest(obj):
    def enc(v):
        if isinstance(v, float):
            return repr(v)
        if isinstance(v, dict):
            return {str(k): enc(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [enc(x) for x in v]
        return v
    text = json.dumps(enc(obj), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def test_fit_and_walk_forward_match_frozen_values():
    got = _canonical()
    assert got["walk_forward"]["weeks"] > 0          # the check really scored weeks
    assert got["long_with"]["n_days"] == 90
    # Three values in the open, so a failure says roughly where it moved.
    assert got["walk_forward"]["weeks"] == 11
    assert got["walk_forward"]["brier"] == 0.2256348751973896
    assert got["long_with"]["weights"]["slope_5"] == 1.0686659253922843
    assert _digest(got) == FROZEN_DIGEST
