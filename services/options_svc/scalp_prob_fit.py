"""The fit of the Opening Scalp's weighted chance, and its weekly honesty check
(design 2026-10-22-scalp-weighted-probability, section 5).

``refit`` replays every labelled outcome the two studies stored, rebuilds each
call's factors from the stored bars and source history (``training_rows``), fits
one logistic model per call kind and trade (``fit``), scores each model
walk-forward by week against the usual rate (``walk_forward``) and stores the
eight models under one new version. Older versions are never deleted.

The rules, each one fixed before any weight was read:

* **The label** is the study's own outcome at the current ``iv_scale``: win 1,
  loss or timeout 0, a skip left out. The 08:36 call learns from the 5-minute
  outcomes (``outcomes.interval = 5``), the 15-minute calls from
  ``intraday_outcomes``. A row the studies would skip today (a short session, a
  slot an early close cuts, no direction) is left out even when a table built
  before a config change still holds it.
* **The day is the unit.** A row weighs ``1 / rows of its (day, symbol, trade)``,
  so 21 slots of one tape count as one day, not 21 independent ones.
* **A prior on every weight** (MAP logistic regression, Newton steps): each
  factor's weight is pulled toward its assumed value (``[prob.priors]``, negated
  for the trades against the move) with the wide or tight spread; each symbol's
  intercept toward 0 with the wide spread. A factor no row reads keeps its prior
  exactly.
* **The scaling is the fit's own.** It is ``fit_scaling`` over the rows fitted,
  stored with the weights so a live call is scaled the same way; a continuous
  factor no row reads is scaled by 1.
* **No week is scored on weights fitted with it.** ``walk_forward`` fits on the
  weeks before each scored week and only then predicts it. The usual rate it is
  compared with is the trade's weighted win share for that symbol (and, for the
  15-minute calls, that block) over every EARLIER day; a row with no earlier
  day in its group is left out of both Brier sums.
* **"Assumed" is measured, not counted.** Each weight's ``data_share`` is the
  share of its posterior precision the data supplied (``_data_share``). A thin
  source under the tight prior can be read on many days and still be mostly
  its assumption; the page marks it so (``scalp_prob``, ``[prob]
  assumed_share``).
* **What is stored is finite.** The store refuses NaN; a fit that does not end
  finite raises, and ``refit`` then stores nothing.

The row's ``win_points`` (the dealer read's room is measured in them) is the
study's own pricing of the long trade with the move: ``vol_at_entry x iv_scale``
through ``scalp_sim.strikes_for`` and ``level_points`` at the entry. The chain is
not stored historically, so the live call (which prices from the chain's
volatility when it has one) can read a slightly different room.

The general parts (the MAP logistic fit, the walk-forward split, the usual rate,
the Brier score and the calibration table) are ``shared/ml``'s since 2026-11-14;
``tests/test_scalp_prob_fit_golden.py`` holds the numbers to what they were
before the move.

Imports nothing from ``compute``.
"""
import datetime as _dt
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np

from shared import scalp_config
from shared.ml import baselines, logistic, metrics, walkforward
from shared.numeric import finite as _finite

from services import _degrade
from services.options_svc import (
    scalp_inputs, scalp_intraday_inputs, scalp_prob_features, scalp_sim, scalp_store,
    scalp_study, scalp_trend)
from services.options_svc.scalp_fetch import ct

log = logging.getLogger("options_svc.scalp_prob_fit")

#: The two call kinds, each with its own four models.
KINDS = ("morning", "intraday")
#: The slot the 08:36 call is recorded under (``intraday_readings`` and
#: ``prob_sources``).
MORNING_SLOT = "08:36"
_CT = ZoneInfo("America/Chicago")
#: One refit at a time: the scheduler and the command must not interleave two
#: fits under one version number.
_REFIT_LOCK = threading.Lock()


@dataclass
class Row:
    """One labelled outcome and the factors its call had. ``factors`` is shared,
    read-only, by the four trades of one call."""
    kind: str
    day: str
    slot: str
    symbol: str
    role: str
    win: int
    weight: float
    factors: dict = field(default_factory=dict)


@dataclass
class Model:
    """One kind's model of one trade: ``weights`` per factor and ``intercepts``
    per symbol in log-odds per scaled unit; ``scaling`` the divisor of each
    continuous factor; ``days[name]`` the distinct days that factor was read
    (not a gap); ``n_days`` the distinct days fitted; ``data_share[name]`` the
    share (0..1) of that weight's posterior precision that came from the data
    rather than its prior (``_data_share``). A factor missing from
    ``data_share`` is read as still its assumption."""
    role: str
    kind: str
    weights: dict = field(default_factory=dict)
    intercepts: dict = field(default_factory=dict)
    scaling: dict = field(default_factory=dict)
    days: dict = field(default_factory=dict)
    n_days: int = 0
    data_share: dict = field(default_factory=dict)


# ── the training rows ────────────────────────────────────────────────────────
def _labelled(kind, db_path) -> dict:
    """``{(day, slot, symbol): [(role, win), ...]}`` of the stored outcomes at the
    current ``iv_scale``; skips are left out."""
    scale = scalp_study._scale_key(scalp_config.iv_scale())
    conn = scalp_store.connect(db_path)
    try:
        if kind == "morning":
            got = conn.execute(
                "SELECT day, ?, symbol, role, result FROM outcomes WHERE interval = ? "
                "AND iv_scale = ?", (MORNING_SLOT, scalp_study.VERDICT_INTERVAL,
                                     scale)).fetchall()
        else:
            got = conn.execute(
                "SELECT day, slot, symbol, role, result FROM intraday_outcomes "
                "WHERE iv_scale = ?", (scale,)).fetchall()
    finally:
        conn.close()
    out = defaultdict(list)
    for day, slot, symbol, role, result in got:
        win = scalp_study._label(result)
        if win is not None:
            out[(day, slot, symbol)].append((role, win))
    return out


def _snapshot(recorded, loaded, key):
    """The call's sources: the live record first, else the loaded history, else
    ``None`` (every source factor a gap)."""
    body = recorded.get(key)
    snap = body.get("snapshot") if isinstance(body, dict) else None
    if isinstance(snap, dict):
        return snap
    snap = loaded.get(key)
    return snap if isinstance(snap, dict) else None


def _win_points(symbol, day, direction, spot, entry_ts, vol):
    """The long trade with the move's win level, in points from the entry spot
    (as the call prices it, at ``vol x iv_scale``); ``None`` when unpriceable."""
    spot, vol = _finite(spot), _finite(vol)
    if direction not in (1, -1) or spot is None or spot <= 0 or entry_ts is None \
            or vol is None or vol <= 0:
        return None
    structure = scalp_sim.structure_for("long_with", direction)
    strikes = scalp_sim.strikes_for(structure, spot, scalp_config.strike_step(symbol),
                                    scalp_config.width(symbol), scalp_config.otm_steps())
    win, _loss = scalp_sim.level_points(structure, spot, strikes, ct(entry_ts), day,
                                        vol * scalp_config.iv_scale())
    win = _finite(win)
    return abs(win) if win else None


def _call(kind, symbol, day, slot, db_path, cache):
    if kind == "morning":
        return scalp_inputs.morning(symbol, day, scalp_study.VERDICT_INTERVAL,
                                    db_path=db_path)
    return scalp_intraday_inputs.reading(symbol, day, slot, db_path=db_path, cache=cache)


def training_rows(kind, db_path=None) -> list:
    """One ``Row`` per labelled outcome of ``kind`` (``"morning"`` or
    ``"intraday"``) at the current ``iv_scale``, its factors rebuilt from the
    store as the call would have read them, weighted so each (day, symbol, trade)
    sums to one. Days and slots the studies skip are left out."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    labelled = _labelled(kind, db_path)
    recorded = scalp_store.readings_all(db_path=db_path)
    loaded = scalp_store.prob_sources_all(kind, db_path=db_path)
    stop_min = scalp_config.time_stop_min()
    by_day = defaultdict(list)
    for day, slot, symbol in labelled:
        by_day[day].append((slot, symbol))

    trend_cache = {}      # completed days' bars, kept across days (scalp_trend)
    rows = []
    for day_iso in sorted(by_day):
        day = _dt.date.fromisoformat(day_iso)
        if scalp_study._short(day, db_path):
            continue
        reading_cache = {}    # one per day, as the intraday study keeps it
        for slot, symbol in sorted(by_day[day_iso]):
            if kind == "intraday" and (
                    scalp_config.intraday_block(slot) is None
                    or scalp_config.intraday_slot_cut(day, slot, stop_min)):
                continue
            call = _call(kind, symbol, day, slot, db_path, reading_cache)
            d = call.direction
            if d not in (1, -1) or call.entry_spot is None or call.entry_ts is None:
                continue
            trend = scalp_trend.trend_factors(symbol, day, call.entry_ts,
                                              db_path=db_path, cache=trend_cache)
            key = (day_iso, slot, symbol)
            win_points = _win_points(symbol, day, d, call.entry_spot, call.entry_ts,
                                     call.vol_at_entry)
            factors = scalp_prob_features.factors(
                kind, symbol, slot, d, call.inputs, trend,
                _snapshot(recorded, loaded, key), win_points, call.entry_spot)
            for role, win in labelled[key]:
                rows.append(Row(kind=kind, day=day_iso, slot=slot, symbol=symbol,
                                role=role, win=win, weight=0.0, factors=factors))
    count = defaultdict(int)
    for r in rows:
        count[(r.day, r.symbol, r.role)] += 1
    for r in rows:
        r.weight = 1.0 / count[(r.day, r.symbol, r.role)]
    return rows


# ── the fit ──────────────────────────────────────────────────────────────────
def _usable(rows, role, kind) -> list:
    """The rows of one kind's trade with a label and a positive finite weight."""
    out = []
    for r in rows or ():
        w = _finite(r.weight)
        if r.role == role and r.kind == kind and r.win in (0, 1) and w is not None \
                and w > 0:
            out.append(r)
    return out


def fit(rows, role, kind, priors, prior_sd) -> Model:
    """The MAP logistic model of ``kind``'s ``role`` over ``rows`` (others are
    ignored). ``priors`` are written for the trades with the move and signed here
    by ``ROLE_SIGN[role]``; ``prior_sd`` is ``{"wide", "tight"}``. No usable row
    is an empty model (``n_days`` 0)."""
    used = _usable(rows, role, kind)
    if not used:
        return Model(role=role, kind=kind)
    sign = scalp_prob_features.ROLE_SIGN[role]
    names = sorted(set(priors) | {n for r in used for n in r.factors})
    scaling = scalp_prob_features.fit_scaling([r.factors for r in used])
    for n in names:
        if n not in scalp_prob_features.DISCRETE and n not in scaling:
            scaling[n] = 1.0
    symbols = sorted({r.symbol for r in used})
    k = len(symbols)
    col = {n: k + i for i, n in enumerate(names)}
    sym_col = {s: i for i, s in enumerate(symbols)}

    x = np.zeros((len(used), k + len(names)))
    read_on = defaultdict(set)
    for i, r in enumerate(used):
        x[i, sym_col[r.symbol]] = 1.0
        for n, v in scalp_prob_features.scaled(r.factors, scaling).items():
            x[i, col[n]] = v
        for n, raw in r.factors.items():
            if _finite(raw) is not None:
                read_on[n].add(r.day)
    y = np.array([float(r.win) for r in used])
    w = np.array([float(r.weight) for r in used])
    wide, tight = float(prior_sd["wide"]), float(prior_sd["tight"])
    mean = np.array([0.0] * k + [sign * float(priors.get(n, 0.0)) for n in names])
    sd = [wide] * k + [wide if scalp_prob_features.is_wide(n) else tight for n in names]
    prec = 1.0 / np.square(np.array(sd))

    theta = logistic.map_fit(x, y, w, mean, prec)
    share = logistic.data_share(x, w, theta, prec)
    return Model(
        role=role, kind=kind,
        weights={n: float(theta[col[n]]) for n in names},
        intercepts={s: float(theta[sym_col[s]]) for s in symbols},
        scaling={n: float(v) for n, v in scaling.items()},
        days={n: len(read_on.get(n, ())) for n in names},
        n_days=len({r.day for r in used}),
        data_share={n: float(share[col[n]]) for n in names})


def predict(model: Model, symbol, factors) -> tuple:
    """``(probability, pushes)``: each factor's push is its weight times its
    scaled value, in log-odds; a gap pushes 0. A symbol the model never saw has
    the intercept's prior, 0."""
    s = scalp_prob_features.scaled(factors, model.scaling)
    pushes = {n: float(wt * s.get(n, 0.0)) for n, wt in model.weights.items()}
    z = float(model.intercepts.get(symbol, 0.0)) + sum(pushes.values())
    return float(logistic.sigmoid(z)), pushes


# ── the walk-forward check ───────────────────────────────────────────────────
def _week(day_iso) -> tuple:
    return _dt.date.fromisoformat(day_iso).isocalendar()[:2]


def _usual_rates(rows, kind) -> list:
    """Per row, the weighted win share of the rows of its (symbol, trade[, block])
    on EARLIER days; ``None`` with no earlier day."""
    blocks = {}
    groups = []
    for r in rows:
        block = None
        if kind == "intraday":
            if r.slot not in blocks:
                blocks[r.slot] = scalp_config.intraday_block(r.slot)
            block = blocks[r.slot]
        groups.append((r.symbol, r.role, block))
    return baselines.usual_rates(groups, [r.day for r in rows], [r.win for r in rows],
                                 [r.weight for r in rows])


def walk_forward(rows, role, kind, priors, prior_sd) -> dict:
    """Score ``kind``'s ``role`` model on weeks it was not fitted on: for each ISO
    week after the first ``prob_min_train_weeks()``, fit on the earlier weeks and
    predict that week. Brier scores are weighted by the rows' weights, the model's
    and the usual rate's over the same rows. ``beats_usual`` is True only when the
    usual rate's Brier is at least ``prob_min_brier_margin()`` above the model's;
    with no scored week the Brier fields are ``None``."""
    used = _usable(rows, role, kind)
    usual = _usual_rates(used, kind)
    # ``fit`` is looked up at call time, so a test that wraps it sees every fit.
    scored, n_weeks = walkforward.walk_forward(
        len(used), lambda j: _week(used[j].day), scalp_config.prob_min_train_weeks(),
        lambda train: fit([used[j] for j in train], role, kind, priors, prior_sd),
        lambda model, j: predict(model, used[j].symbol, used[j].factors)[0],
        keep=lambda j: usual[j] is not None)
    p = [prob for _j, prob in scored]
    u = [usual[j] for j, _prob in scored]
    y = [used[j].win for j, _prob in scored]
    w = [used[j].weight for j, _prob in scored]
    brier = metrics.brier(p, y, w)
    if not scored or brier is None:
        return {"weeks": 0, "brier": None, "brier_usual": None, "beats_usual": False,
                "calibration": metrics.reliability([], [], [])}
    brier_usual = metrics.brier(u, y, w)
    return {"weeks": n_weeks, "brier": brier, "brier_usual": brier_usual,
            "beats_usual": bool(brier_usual - brier >= scalp_config.prob_min_brier_margin()),
            "calibration": metrics.reliability(p, y, w)}


# ── the refit ────────────────────────────────────────────────────────────────
def _ct_now(now) -> _dt.datetime:
    if not isinstance(now, _dt.datetime):
        return _dt.datetime.now(_CT)
    return now.replace(tzinfo=_CT) if now.tzinfo is None else now.astimezone(_CT)


def _body(rows, role, kind, priors, prior_sd) -> dict:
    """One stored model: the fit, its walk-forward result and the priors it
    started from (signed for the trade)."""
    role_rows = [r for r in rows if r.role == role]
    model = fit(role_rows, role, kind, priors, prior_sd)
    sign = scalp_prob_features.ROLE_SIGN[role]
    names = set(priors) | set(model.weights)
    return {"weights": model.weights, "intercepts": model.intercepts,
            "scaling": model.scaling, "days": model.days, "n_days": model.n_days,
            "data_share": model.data_share,
            "rows": len(_usable(role_rows, role, kind)),
            "priors": {n: sign * float(priors.get(n, 0.0)) for n in sorted(names)},
            "prior_sd": {k: float(v) for k, v in prior_sd.items()},
            "walk_forward": walk_forward(role_rows, role, kind, priors, prior_sd)}


def refit(db_path=None, now=None) -> dict:
    """Fit the eight models (two kinds x four trades) from the store and keep
    them under ``next_prob_version()``, ``fitted_at`` = ``now`` in Central ISO.
    Returns ``{"version", "rows": {kind: n}, "seconds"}``; on any failure nothing
    is stored, the failure is counted (``options.scalp_prob``) and ``version`` is
    ``None``. Older versions are never touched."""
    started = time.monotonic()
    counts = {}
    with _REFIT_LOCK:
        try:
            priors = scalp_config.prob_priors()
            prior_sd = scalp_config.prob_prior_sd()
            models = {}
            for kind in KINDS:
                t0 = time.monotonic()
                rows = training_rows(kind, db_path=db_path)
                counts[kind] = len(rows)
                t1 = time.monotonic()
                for role in scalp_sim.ROLES:
                    models[(kind, role)] = _body(rows, role, kind, priors, prior_sd)
                log.info("scalp prob refit %s: %d rows built in %.1fs, fitted in %.1fs",
                         kind, len(rows), t1 - t0, time.monotonic() - t1)
            version = scalp_store.next_prob_version(db_path=db_path)
            scalp_store.save_prob_models(version, _ct_now(now).isoformat(), models,
                                         db_path=db_path)
        except Exception:
            _degrade.degraded("options.scalp_prob", detail="refit")
            return {"version": None, "rows": counts,
                    "seconds": round(time.monotonic() - started, 2)}
    out = {"version": version, "rows": counts,
           "seconds": round(time.monotonic() - started, 2)}
    log.info("scalp prob refit stored: %s", out)
    return out


__all__ = ["Row", "Model", "KINDS", "training_rows", "fit", "predict", "walk_forward",
           "refit"]
