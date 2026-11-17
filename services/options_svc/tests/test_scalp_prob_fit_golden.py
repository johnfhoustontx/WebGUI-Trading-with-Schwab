"""Frozen outputs of the Opening Scalp's chance-of-target fit.

Written on 2026-11-14 against the fit as it stood before its general parts moved
to ``shared/ml`` (design 2026-11-14-ml-foundation). The digest covers every field
of every model and of the walk-forward result, at full float precision. Those
values are in ``fixtures/scalp_prob_fit_golden.json``, which must hash to the
digest. The fit is compared with them number by number, floats within a relative
1e-9: Linux computes a last bit or two differently from Windows, where they were
frozen (changed 2026-11-17; the bit-exact digest had failed every CI run since).
Neither the digest nor the fixture may ever be regenerated to make a change pass:
a moved number means the model's numbers moved.
"""
import datetime as dt
import hashlib
import json
import math
import pathlib
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


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "scalp_prob_fit_golden.json"

# How far a float may sit from its frozen value. Linux CI computes the same fit
# one or two units in the last place away from Windows, where the values were
# frozen: slope_5 came out 1.0686659253922846 against 1.0686659253922843, a
# relative 3e-16. A change to the model moves a number by far more than 1e-9;
# the gap between platforms never comes near it.
REL_TOL = 1e-9
ABS_TOL = 1e-12


def _frozen():
    with FIXTURE.open(encoding="utf-8") as f:
        return json.load(f)


def _close(got, want, path="$"):
    """Every key, every type and every non-float exactly; floats within
    ``REL_TOL``. Returns the paths that differ, so a failure says where."""
    if isinstance(want, dict):
        if not isinstance(got, dict) or set(got) != set(want):
            return [f"{path}: keys {sorted(got) if isinstance(got, dict) else got!r}"
                    f" != {sorted(want)}"]
        return [p for k in sorted(want) for p in _close(got[k], want[k], f"{path}.{k}")]
    if isinstance(want, list):
        if not isinstance(got, (list, tuple)) or len(got) != len(want):
            return [f"{path}: {got!r} != {want!r}"]
        return [p for i, (g, w) in enumerate(zip(got, want))
                for p in _close(g, w, f"{path}[{i}]")]
    if isinstance(want, float) and isinstance(got, float):
        ok = math.isclose(got, want, rel_tol=REL_TOL, abs_tol=ABS_TOL)
        return [] if ok else [f"{path}: {got!r} != {want!r}"]
    if type(got) is not type(want) or got != want:
        return [f"{path}: {got!r} != {want!r}"]
    return []


def test_the_fixture_is_the_frozen_values():
    """The fixture hashes to the digest frozen on 2026-11-14, so it holds those
    values to the last bit. A regenerated fixture fails here."""
    assert _digest(_frozen()) == FROZEN_DIGEST


def test_fit_and_walk_forward_match_frozen_values():
    got = _canonical()
    assert got["walk_forward"]["weeks"] > 0          # the check really scored weeks
    assert got["long_with"]["n_days"] == 90
    # Three values in the open, so a failure says roughly where it moved.
    assert got["walk_forward"]["weeks"] == 11
    assert math.isclose(got["walk_forward"]["brier"], 0.2256348751973896,
                        rel_tol=REL_TOL)
    assert math.isclose(got["long_with"]["weights"]["slope_5"], 1.0686659253922843,
                        rel_tol=REL_TOL)
    diffs = _close(json.loads(json.dumps(got)), _frozen())
    assert not diffs, "moved from the frozen values:\n" + "\n".join(diffs[:20])


def test_the_comparison_catches_a_moved_number():
    """A partner for the tolerance: a number moved by a real amount, a missing
    key and a changed integer are all caught."""
    want = _frozen()
    moved = json.loads(json.dumps(want))
    moved["long_with"]["weights"]["slope_5"] *= 1 + 1e-6
    assert _close(moved, want)
    gone = json.loads(json.dumps(want))
    del gone["walk_forward"]["brier"]
    assert _close(gone, want)
    days = json.loads(json.dumps(want))
    days["long_with"]["n_days"] += 1
    assert _close(days, want)
    assert _close(json.loads(json.dumps(want)), want) == []
