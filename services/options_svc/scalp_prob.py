"""The live chance of target of an Opening Scalp call
(design 2026-10-22-scalp-weighted-probability, sections 3 and 6).

``chance`` reads the NEWEST stored weights of the call's kind, builds the call's
factors exactly as the fit did (``scalp_prob_features.factors``, the trend readings
from ``scalp_trend``), and scores each trade with ``scalp_prob_fit.predict``: this
module never scores anything itself.

Rules:

* **No direction, no chance.** A flat tape (no ``direction``), a ``kind`` that is
  not ``"morning"`` or ``"intraday"``, no stored fit, or no model for the kind
  gives the no-chance answer: every trade ``None``, ``version`` ``None``. Never a
  made-up 0.5.
* **A model fitted on no rows says nothing.** Its trade is ``None``, not 0.5.
* **The room is priced as the fit priced it.** The factors' win points come from
  ``scalp_prob_fit._win_points`` (the long trade at ``vol x iv_scale``), never from
  the role's chain-priced points, so "room" means the same live as in training.
* **The pushes are ``long_with``'s.** One per factor in that model, the raw
  reading beside the push converted to percentage points of chance at the
  predicted probability (``p (1 - p) push 100``), the days it was read on (for
  display), and whether the factor's weight is still mostly the prior: its
  stored ``data_share`` (the share of its posterior precision the data gave) is
  under ``prob_assumed_share``. A stored model with no ``data_share`` (fitted
  before it was kept) reads every factor as assumed.
* **Never raises.** Any failure is counted (``options.scalp_prob``) and answered as
  no chance: the call it belongs to is published all the same.

Imports nothing from ``compute``.
"""
from datetime import date

from shared import scalp_config
from shared.numeric import finite as _finite

from services import _degrade
from services.options_svc import (
    scalp_prob_features, scalp_prob_fit, scalp_sim, scalp_store, scalp_trend)

#: The two call kinds the chance is fitted for.
KINDS = scalp_prob_fit.KINDS
#: The trade whose factors the page explains.
_PUSH_ROLE = "long_with"


def _none() -> dict:
    """The no-chance answer."""
    return {"version": None, "fitted_at": None,
            "by_role": {role: None for role in scalp_sim.ROLES},
            "beats_usual": {}, "pushes": []}


def _model(kind, role, body) -> scalp_prob_fit.Model:
    return scalp_prob_fit.Model(
        role=role, kind=kind, weights=dict(body.get("weights") or {}),
        intercepts=dict(body.get("intercepts") or {}),
        scaling=dict(body.get("scaling") or {}), days=dict(body.get("days") or {}),
        n_days=int(body.get("n_days") or 0),
        data_share=dict(body.get("data_share") or {}))


def _pushes(model, probability, pushes, factors) -> list:
    """``long_with``'s pushes in percentage points, largest first."""
    assumed_under = scalp_config.prob_assumed_share()
    out = []
    for name in model.weights:
        days = int(model.days.get(name) or 0)
        share = _finite(model.data_share.get(name))
        out.append({"factor": name, "reading": _finite(factors.get(name)),
                    "points": probability * (1.0 - probability)
                    * float(pushes.get(name, 0.0)) * 100.0,
                    "days": days,
                    # no stored share (an older model) is read as still assumed
                    "assumed": share is None or share < assumed_under})
    out.sort(key=lambda p: abs(p["points"]), reverse=True)
    return out


def chance(kind: str, symbol: str, entry: dict, snapshot: dict | None, day: date,
           db_path=None, models: dict | None = None,
           trend_cache: dict | None = None) -> dict:
    """The chance of target of the built call ``entry`` (``direction``, ``inputs``,
    ``entry_ts``, ``entry_spot``, ``slot``, ``roles``) of ``kind`` on ``symbol``,
    from ``snapshot`` (``scalp_confirm.snapshot``, or ``None``) and the newest
    stored weights (``models`` is their ``latest_prob_models`` answer when the
    caller already has it). See the module docstring for the answer's shape."""
    try:
        d = entry.get("direction") if isinstance(entry, dict) else None
        if kind not in KINDS or d not in (1, -1):
            return _none()
        stored = models if models is not None \
            else scalp_store.latest_prob_models(db_path=db_path)
        if not stored or not stored.get("models"):
            return _none()
        bodies = {}
        for role in scalp_sim.ROLES:
            body = stored["models"].get((kind, role))
            if body is not None:
                bodies[role] = body
        if not bodies:
            return _none()

        trend = scalp_trend.trend_factors(symbol, day, entry.get("entry_ts"),
                                          db_path=db_path, cache=trend_cache)
        factors = scalp_prob_features.factors(
            kind, symbol, entry.get("slot"), d, entry.get("inputs"), trend, snapshot,
            # the fit's own pricing, so "room" means here what it meant in training
            scalp_prob_fit._win_points(symbol, day, d, entry.get("entry_spot"),
                                       entry.get("entry_ts"), entry.get("vol_at_entry")),
            entry.get("entry_spot"))

        out = _none()
        out["version"] = stored.get("version")
        out["fitted_at"] = stored.get("fitted_at")
        for role, body in bodies.items():
            out["beats_usual"][role] = bool(
                (body.get("walk_forward") or {}).get("beats_usual"))
            model = _model(kind, role, body)
            if not model.weights and not model.intercepts:
                continue                    # fitted on no rows: no reading, not 0.5
            probability, pushes = scalp_prob_fit.predict(model, symbol, factors)
            out["by_role"][role] = probability
            if role == _PUSH_ROLE:
                out["pushes"] = _pushes(model, probability, pushes, factors)
        return out
    except Exception:
        _degrade.degraded("options.scalp_prob", detail="chance")
        return _none()


__all__ = ["chance"]
