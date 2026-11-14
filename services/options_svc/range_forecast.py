"""The range forecast, scored out of sample (ML roadmap Phase 2, approach A).

For each configured symbol, at each forecast time of each complete session, the
log of the high-to-low range still to come over each horizon, forecast as a
10th, 50th and 90th percentile by three models fitted the same way (ridge on the
log target plus the fit's own residual percentiles):

* the realised-volatility yardstick: recent realised volatility and time only;
* the implied move: the volatility index scaled to the horizon, and the time
  left (so it, too, knows ranges are wider near the open and the close);
* the forecast: every input.

Each session is forecast by fits on earlier sessions only. Every input comes
from bars that closed at or before the forecast time. Nothing here is shown
anywhere but Model Health, and nothing reads it to decide anything.

``evaluate`` returns raw losses, day-clustered differences, coverage and the
inputs; ``model_health`` turns those into verdicts and drift, so this module
imports nothing from it. Imports nothing from ``compute``.
Design: docs/plans/2026-11-14-range-forecast-design.md
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from shared import market_calendar as mc
from shared import model_health_config as cfg
from shared.ml import linear, metrics

from services.options_svc import scalp_store

BAR_MIN = 5
BAR_SEC = BAR_MIN * 60
QUANTILES = (0.1, 0.5, 0.9)
#: The inputs of each model, by name. The order is the column order.
YARDSTICK = ("rv_30", "rv_day", "rv_prev", "rv_5", "mins_left", "horizon")
IMPLIED = ("implied", "mins_left")
FORECAST = ("rv_30", "rv_day", "rv_prev", "rv_5", "gap", "iv", "iv_change",
            "volume", "mins_left", "horizon")
MODELS = {"forecast": FORECAST, "yardstick": YARDSTICK, "implied": IMPLIED}
#: The inputs whose drift is read. Only those that should look alike in any
#: market: the volatility LEVELS (realised and the index) cluster for weeks, so
#: a recent week always differs from the history and the session shuffle, which
#: treats sessions as interchangeable, flags them every time (measured on prod's
#: 2026-11-13 backup: every symbol "shifted"). A level that moved is the market
#: the forecast reads, not a broken input.
DRIFT_INPUTS = ("gap", "iv_change", "volume")


@dataclass
class Session:
    day: str
    bars: list          # the 5-minute bars of the regular session, in order
    close_min: int      # minutes after midnight Central of the close


@dataclass
class Row:
    day: str
    t_min: int
    horizon: int        # 0 = the rest of the session
    y: float
    inputs: dict


# ── reading bars ────────────────────────────────────────────────────────────
def _minutes(dt) -> int:
    return dt.hour * 60 + dt.minute


def sessions(symbol, db_path=None) -> dict:
    """``{iso day: Session}`` of the symbol's COMPLETE regular sessions: every
    5-minute bar from the open to the close present. Any gap leaves the day out
    whole (which also leaves out an early close)."""
    by_day = defaultdict(list)
    for bar in scalp_store.bars(symbol, BAR_MIN, 0, 2 ** 40, db_path=db_path):
        by_day[_dt.datetime.fromtimestamp(bar.ts, mc.CT).date()].append(bar)
    out = {}
    for day in sorted(by_day):
        if not mc.is_trading_day(day):
            continue
        open_dt, close_dt = mc.regular_open_on(day), mc.regular_close_on(day)
        start, end = open_dt.timestamp(), close_dt.timestamp()
        bars = [b for b in by_day[day] if start <= b.ts < end]
        want = int((end - start) // BAR_SEC)
        if len(bars) != want or any(b.ts != start + i * BAR_SEC for i, b in enumerate(bars)):
            continue
        out[day.isoformat()] = Session(day.isoformat(), bars, _minutes(close_dt))
    return out


# ── inputs and targets ──────────────────────────────────────────────────────
def _returns(bars) -> list:
    """Each bar's log return: the first against its own open, the rest against
    the previous close."""
    out = []
    prev = None
    for b in bars:
        base = b.open if prev is None else prev
        out.append(math.log(b.close / base) if base > 0 and b.close > 0 else 0.0)
        prev = b.close
    return out


def _log(v, floor) -> float:
    return math.log(max(v, floor))


def rows(symbol_s, vix_s, volume_s, horizons=None, *, first=None, last=None,
         step=None, floor=None, volume_sessions=None) -> list:
    """Every row of one symbol. ``symbol_s`` / ``vix_s`` / ``volume_s`` are
    :func:`sessions` maps of the symbol, its volatility index and the symbol
    whose volume is read. A day needs all three, five earlier sessions of the
    symbol and five earlier of the volume symbol; a row whose horizon runs past
    the close is left out."""
    horizons = cfg.range_horizons() if horizons is None else horizons
    first = cfg.range_first() if first is None else first
    last = cfg.range_last() if last is None else last
    step = cfg.range_step_min() if step is None else step
    floor = cfg.range_floor() if floor is None else floor
    n_vol = cfg.range_volume_sessions() if volume_sessions is None else volume_sessions

    days = sorted(symbol_s)
    rv_whole = {d: sum(r * r for r in _returns(symbol_s[d].bars)) for d in days}
    vol_days = sorted(volume_s)
    cumvol = {d: np.cumsum([b.volume or 0.0 for b in volume_s[d].bars]) for d in vol_days}
    out = []
    for i, day in enumerate(days):
        if i < 5 or day not in vix_s or day not in volume_s:
            continue
        prior_vol = [d for d in vol_days if d < day][-n_vol:]
        if len(prior_vol) < 5:
            continue
        s = symbol_s[day]
        bars, vbars = s.bars, vix_s[day].bars
        open_min = s.close_min - len(bars) * BAR_MIN
        rets = _returns(bars)
        prev = symbol_s[days[i - 1]]
        rv_prev = rv_whole[days[i - 1]]
        rv_5 = sum(rv_whole[d] for d in days[i - 5:i]) / 5.0
        prev_close = prev.bars[-1].close
        gap = math.log(1.0 + abs(bars[0].open / prev_close - 1.0)) if prev_close > 0 else 0.0
        t = first
        while t <= last:
            k = (t - open_min) // BAR_MIN          # bars closed by t
            if k < 6 or k >= len(bars):
                t += step
                continue
            price = bars[k - 1].close
            iv_now, iv_open = vbars[k - 1].close, vbars[0].open
            usual = [cumvol[d][k - 1] for d in prior_vol]
            usual_mean = sum(usual) / len(usual)
            mins_left = s.close_min - t
            inputs_base = {
                "rv_30": _log(sum(r * r for r in rets[k - 6:k]), floor),
                "rv_day": _log(sum(r * r for r in rets[:k]), floor),
                "rv_prev": _log(rv_prev, floor),
                "rv_5": _log(rv_5, floor),
                "gap": gap,
                "iv": _log(iv_now, floor),
                "iv_change": math.log(iv_now / iv_open) if iv_now > 0 and iv_open > 0 else 0.0,
                "volume": _log(cumvol[day][k - 1] / usual_mean, floor) if usual_mean > 0 else 0.0,
                "mins_left": math.log(mins_left),
            }
            for h in horizons:
                span = mins_left if h == 0 else h
                if span <= 0 or t + span > s.close_min:
                    continue
                ahead = bars[k:k + span // BAR_MIN]
                if not ahead or price <= 0:
                    continue
                rng = max(b.high for b in ahead) - min(b.low for b in ahead)
                inputs = dict(inputs_base)
                inputs["horizon"] = math.log(span)
                # The implied move over the span, as a log: annualised
                # volatility times the square root of the span's share of a year.
                inputs["implied"] = inputs["iv"] + 0.5 * math.log(span)
                out.append(Row(day, t, h, _log(rng / price, floor), inputs))
            t += step
    return out


# ── fitting and scoring ─────────────────────────────────────────────────────
def _weights(days) -> np.ndarray:
    count = defaultdict(int)
    for d in days:
        count[d] += 1
    return np.array([1.0 / count[d] for d in days])


def _fit(x, y, w, lam):
    model = linear.ridge(x, y, w, lam)
    resid = y - linear.predict(model, x)
    return model, [metrics.weighted_quantile(resid, w, q) for q in QUANTILES]


def walk_forward(rows_, *, min_train_days=None, lam=None) -> dict:
    """``{model: [[q10, q50, q90] per scored row]}`` plus ``"rows"``, the rows
    scored. Each day is predicted by fits on earlier days only."""
    min_train = cfg.range_min_train_days() if min_train_days is None else min_train_days
    lam = cfg.range_ridge() if lam is None else lam
    days = sorted({r.day for r in rows_})
    by_day = defaultdict(list)
    for r in rows_:
        by_day[r.day].append(r)
    cols = {m: np.array([[r.inputs[c] for c in names] for r in rows_])
            for m, names in MODELS.items()}
    y_all = np.array([r.y for r in rows_])
    day_all = [r.day for r in rows_]
    index = defaultdict(list)
    for i, d in enumerate(day_all):
        index[d].append(i)
    out = {m: [] for m in MODELS}
    scored = []
    train_idx = []
    for j, day in enumerate(days):
        if j >= min_train and train_idx:
            test = index[day]
            w = _weights([day_all[i] for i in train_idx])
            for m in MODELS:
                model, resid_q = _fit(cols[m][train_idx], y_all[train_idx], w, lam)
                mean = linear.predict(model, cols[m][test])
                out[m].extend([[float(mu + q) for q in resid_q] for mu in mean])
            scored.extend(rows_[i] for i in test)
        train_idx.extend(index[day])
    out["rows"] = scored
    return out


def _row_loss(pred, y) -> float:
    return sum(max(q * (y - p), (q - 1.0) * (y - p)) for q, p in zip(QUANTILES, pred)) / len(QUANTILES)


def score(rows_, *, n_boot=None, **kw) -> dict:
    """Losses, day-clustered differences against each baseline, and coverage of
    the forecast's 10th to 90th band, over the out-of-sample rows."""
    wf = walk_forward(rows_, **kw)
    scored = wf["rows"]
    days = [r.day for r in scored]
    y = [r.y for r in scored]
    w = list(_weights(days)) if scored else []
    loss = {m: [_row_loss(p, yi) for p, yi in zip(wf[m], y)] for m in MODELS}
    total = sum(w)

    def mean(xs):
        return sum(a * b for a, b in zip(xs, w)) / total if total > 0 else None

    def diff(base):
        if not scored:
            return None
        return metrics.day_bootstrap_diff(loss["forecast"], loss[base], days, w,
                                          n_boot=n_boot or cfg.bootstrap_draws())

    inside = [1.0 if p[0] <= yi <= p[2] else 0.0 for p, yi in zip(wf["forecast"], y)]
    return {"days": len(set(days)), "rows": len(scored),
            "loss": mean(loss["forecast"]), "loss_yardstick": mean(loss["yardstick"]),
            "loss_implied": mean(loss["implied"]),
            "diff_yardstick": diff("yardstick"), "diff_implied": diff("implied"),
            "coverage": mean(inside), "last_day": max(days) if days else None}


def evaluate(db_path=None) -> list:
    """One entry per (symbol, horizon) in config order, each with
    :func:`score`'s fields plus ``symbol``, ``horizon`` and ``inputs`` (the
    scored days' inputs for the drift check, shared across a symbol's horizons)."""
    out = []
    cache = {}

    def load(sym):
        if sym not in cache:
            cache[sym] = sessions(sym, db_path)
        return cache[sym]

    volume_from = cfg.range_volume_from()
    for symbol, vix in cfg.range_symbols().items():
        all_rows = rows(load(symbol), load(vix), load(volume_from.get(symbol, symbol)))
        first_h = None
        inputs = []
        for h in cfg.range_horizons():
            mine = [r for r in all_rows if r.horizon == h]
            entry = {"symbol": symbol, "horizon": h, "vol_index": vix, **score(mine)}
            if first_h is None:
                first_h = h
                inputs = [(r.day, {k: r.inputs[k] for k in DRIFT_INPUTS}) for r in mine]
            entry["inputs"] = inputs
            out.append(entry)
    return out


__all__ = ["Session", "Row", "sessions", "rows", "walk_forward", "score", "evaluate",
           "QUANTILES", "MODELS", "DRIFT_INPUTS"]
