"""The Opening Scalp study: stored bars -> an outcomes table -> walk-forward verdicts.

``rebuild`` replays every stored morning: for each symbol it measures the morning
(``scalp_inputs.morning``, 5-minute open bar), and when the morning is usable
simulates the four roles (``scalp_sim.simulate``) at every volatility multiplier
the study tries. It then judges each alignment bucket walk-forward
(``scalp_model``) and writes ``outcomes``, ``mornings`` and ``verdicts`` into
``scalp.db`` in ONE transaction: a reader sees the old study or the new one,
never half of each. The same bars give the same rows (no clock, no randomness;
the rows are rebuilt, never appended).

What the study refuses to count, and why (each lands in ``mornings`` as ``skip``):

* ``short_session`` - $SPX's regular bars end before 14:55 CT (or are missing):
  the settlement the model prices to is wrong that day;
* ``no_open_bar`` / ``no_direction`` - nothing to call;
* ``no_vol`` - no usable $VIX / $VXN at 08:30, so nothing to price with;
* ``data_hole`` - the trade's 5-minute path is short of a full ``time_stop_min``
  of bars. A hole must not read as a timeout.

A ``skip`` from ``simulate`` itself (the entry premium under ``min_premium``) IS
stored, with ``result = "skip"``, so the report can count it; it is never a label.
Labels: win 1, loss and timeout 0.

Verdicts come from 5-minute rows ONLY (design §5.2: one touch-rule bias across the
whole window). The 1-minute rows are the SAME trade (the 5-minute morning's
direction, spot, strikes, volatility and bucket) walked on 1-minute bars, at the
current ``iv_scale`` only, on days whose 1-minute open bar and path are complete:
they exist to measure the both-levels-in-one-bar bias, and nothing reads them
for a verdict.

A win or loss row's ``end_pnl_pct`` is the LEVEL's P&L (the exit is assumed to
fill at the level), not a realised fill; nothing here averages it.

Imports nothing from ``compute``.
"""
import json
import logging
import math
import time
from collections import Counter

from shared import market_calendar as _mc
from shared import scalp_config

from services.options_svc import scalp_inputs, scalp_model, scalp_sim, scalp_store
from services.options_svc.scalp_fetch import ct

log = logging.getLogger("options_svc.scalp_study")

#: The index whose regular bars say whether the session ran full length.
SESSION_SYMBOL = "$SPX"
#: The interval the verdict reads, and the one the bias check walks.
VERDICT_INTERVAL = 5
BIAS_INTERVAL = 1
_SESSION_SECONDS = 7 * 3600     # 08:30-15:30 CT covers any regular session


def _scale_key(scale) -> float:
    """The stored form of a volatility multiplier (``iv_scale`` column, and the
    JSON key as ``str``), so 1.0 from config and 1.0 from a query are equal."""
    return round(float(scale), 6)


def _scales():
    """Every multiplier the study runs: ``iv_scales()`` then ``iv_scale()`` if it
    is not already one of them."""
    out = []
    for s in list(scalp_config.iv_scales()) + [scalp_config.iv_scale()]:
        k = _scale_key(s)
        if k not in out:
            out.append(k)
    return out


def _stored_days(symbols, db_path) -> list:
    """Trading days on which any of ``symbols`` has a 5-minute bar, oldest first."""
    if not symbols:
        return []
    conn = scalp_store.connect(db_path)
    try:
        marks = ",".join("?" * len(symbols))
        hours = conn.execute(
            f"SELECT DISTINCT ts / 3600 FROM bars WHERE interval = ? "
            f"AND symbol IN ({marks})", (VERDICT_INTERVAL, *symbols)).fetchall()
    finally:
        conn.close()
    days = {ct(int(h) * 3600).date() for (h,) in hours}
    return sorted(d for d in days if _mc.is_trading_day(d))


def _short(day, db_path) -> bool:
    start = int(_mc.regular_open_on(day).timestamp())
    got = scalp_store.bars(SESSION_SYMBOL, VERDICT_INTERVAL, start,
                           start + _SESSION_SECONDS, db_path=db_path)
    return scalp_sim.short_session(got)


def _needed(interval, time_stop_min=None) -> int:
    """Bars a complete path holds: every bar starting in [entry, entry + stop)."""
    stop = scalp_config.time_stop_min() if time_stop_min is None else time_stop_min
    return math.ceil(stop / interval)


def _path(symbol, interval, entry_ts, db_path, time_stop_min=None):
    """The trade window's bars, or ``None`` when any of them is missing. The time
    stop is the configured one unless given (grading passes a call's frozen one)."""
    stop = scalp_config.time_stop_min() if time_stop_min is None else time_stop_min
    got = scalp_store.bars(symbol, interval, entry_ts, entry_ts + stop * 60,
                           db_path=db_path)
    return got if len(got) >= _needed(interval, stop) else None


def _has_one_minute_open(symbol, day, db_path) -> bool:
    start = int(_mc.regular_open_on(day).timestamp())
    got = scalp_store.bars(symbol, BIAS_INTERVAL, start, start + 300, db_path=db_path)
    return [b.ts for b in got] == [start + 60 * i for i in range(5)]


def _morning_json(m, day, symbol, skip, bucket, align, has_1min) -> str:
    body = {"day": day.isoformat(), "symbol": symbol, "skip": skip}
    if m is not None:
        body.update({
            "direction": m.direction, "entry_spot": m.entry_spot,
            "entry_ts": m.entry_ts,
            "open_bar": None if m.open_bar is None else list(m.open_bar),
            "inputs": m.inputs, "agree": m.agree,
            "vol_at_entry": m.vol_at_entry, "vol_source": m.vol_source,
            "alignment": align, "bucket": bucket, "has_1min": has_1min,
        })
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _outcome_row(day, symbol, scale, role, structure, bucket, out, interval):
    return (day.isoformat(), symbol, scale, role, structure, bucket, out.result,
            out.minutes, out.entry_value, out.end_pnl_pct, int(bool(out.both_in_bar)),
            interval)


def _simulate_morning(m, day, symbol, bucket, scales, current, path5, path1, rows):
    """Append the morning's outcome rows: every role at every scale on 5-minute
    bars, and every role at ``current`` on 1-minute bars when ``path1`` is given."""
    step, width = scalp_config.strike_step(symbol), scalp_config.width(symbol)
    otm = scalp_config.otm_steps()
    entry = ct(m.entry_ts)
    for role in scalp_sim.ROLES:
        structure = scalp_sim.structure_for(role, m.direction)
        strikes = scalp_sim.strikes_for(structure, m.entry_spot, step, width, otm)
        for scale in scales:
            out = scalp_sim.simulate(structure, m.entry_spot, strikes, entry, day,
                                     m.vol_at_entry * scale, path5, VERDICT_INTERVAL)
            rows.append(_outcome_row(day, symbol, scale, role, structure, bucket,
                                     out, VERDICT_INTERVAL))
        if path1 is not None:
            out = scalp_sim.simulate(structure, m.entry_spot, strikes, entry, day,
                                     m.vol_at_entry * current, path1, BIAS_INTERVAL)
            rows.append(_outcome_row(day, symbol, current, role, structure, bucket,
                                     out, BIAS_INTERVAL))


def _label(result):
    """win -> 1; loss and timeout -> 0; anything else (skip) is not a label."""
    return {"win": 1, "loss": 0, "timeout": 0}.get(result)


def _verdict_rows(outcome_rows, symbols, scales):
    """``(symbol, role, bucket, json)`` for every symbol x role x bucket, from the
    5-minute rows alone."""
    streams = {}
    for (day, sym, scale, role, _st, bucket, result, *_rest, interval) in outcome_rows:
        if interval != VERDICT_INTERVAL:
            continue
        win = _label(result)
        if win is None:
            continue
        streams.setdefault((sym, role, scale), []).append(
            {"day": day, "bucket": bucket, "win": win})
    min_n = scalp_config.min_samples()
    study_scales = [_scale_key(s) for s in scalp_config.iv_scales()]
    out = []
    for sym in symbols:
        for role in scalp_sim.ROLES:
            by_scale = {}
            for scale in scales:
                rows = sorted(streams.get((sym, role, scale), []),
                              key=lambda r: r["day"])
                by_scale[scale] = scalp_model.verdict(
                    scalp_model.walk_forward(rows), rows, min_n)
            across = scalp_model.edge_across_scales(
                {s: by_scale[s] for s in study_scales if s in by_scale})
            for bucket in scalp_model.BUCKETS:
                body = {"scales": {str(s): by_scale[s][bucket] for s in scales},
                        "edge": bool(across.get(bucket, False)),
                        "edge_scales": study_scales}
                out.append((sym, role, bucket,
                            json.dumps(body, sort_keys=True, separators=(",", ":"))))
    return out


def rebuild(db_path=None) -> dict:
    """Recompute the whole study from the stored bars and replace its three tables.

    Returns counts: ``days`` replayed, ``mornings`` recorded, ``outcomes`` written
    (``outcomes_1min`` of them on 1-minute bars), ``skipped`` by reason,
    ``verdicts`` written, and ``seconds`` taken."""
    started = time.monotonic()
    symbols = scalp_config.symbols()
    scales = _scales()
    current = _scale_key(scalp_config.iv_scale())
    days = _stored_days(symbols, db_path)

    morning_rows, outcome_rows = [], []
    skipped = Counter()
    for day in days:
        short = _short(day, db_path)
        for symbol in symbols:
            m, skip, bucket, align, path5, path1 = None, None, None, None, None, None
            if short:
                skip = "short_session"
            else:
                m = scalp_inputs.morning(symbol, day, VERDICT_INTERVAL, db_path=db_path)
                align = scalp_inputs.alignment(m)
                bucket = scalp_model.bucket_of(align, scalp_config.buckets())
                if m.open_bar is None:
                    skip = "no_open_bar"
                elif m.direction is None:
                    skip = "no_direction"
                elif m.vol_at_entry is None:
                    skip = "no_vol"
                else:
                    path5 = _path(symbol, VERDICT_INTERVAL, m.entry_ts, db_path)
                    if path5 is None:
                        skip = "data_hole"
            if skip is None:
                if _has_one_minute_open(symbol, day, db_path):
                    path1 = _path(symbol, BIAS_INTERVAL, m.entry_ts, db_path)
                _simulate_morning(m, day, symbol, bucket, scales, current, path5,
                                  path1, outcome_rows)
            else:
                skipped[skip] += 1
            morning_rows.append((day.isoformat(), symbol, _morning_json(
                m, day, symbol, skip, bucket, align, path1 is not None)))

    verdict_rows = _verdict_rows(outcome_rows, symbols, scales)

    conn = scalp_store.connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM outcomes")
            conn.execute("DELETE FROM mornings")
            conn.execute("DELETE FROM verdicts")
            conn.executemany(
                "INSERT INTO outcomes (day, symbol, iv_scale, role, structure, bucket, "
                "result, minutes, entry_value, end_pnl_pct, both_in_bar, interval) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", outcome_rows)
            conn.executemany("INSERT INTO mornings (day, symbol, json) VALUES (?, ?, ?)",
                             morning_rows)
            conn.executemany(
                "INSERT INTO verdicts (symbol, role, bucket, json) VALUES (?, ?, ?, ?)",
                verdict_rows)
    finally:
        conn.close()

    counts = {
        "days": len(days),
        "mornings": len(morning_rows),
        "outcomes": len(outcome_rows),
        "outcomes_1min": sum(1 for r in outcome_rows if r[-1] == BIAS_INTERVAL),
        "skipped": dict(skipped),
        "verdicts": len(verdict_rows),
        "seconds": round(time.monotonic() - started, 2),
    }
    log.info("scalp study rebuilt: %s", counts)
    return counts


def current_rates(symbol, bucket, db_path=None) -> dict:
    """Per role, the win rate of ``bucket`` over EVERY stored day at the current
    ``iv_scale`` (5-minute rows): ``{p, lo, hi, n, p_base, n_base, edge}``.

    ``p``/``lo``/``hi`` are ``None`` when the bucket has no labelled morning (and
    always for ``bucket=None``, an unknown alignment); ``p_base`` is ``None`` when
    the role has none at all. ``edge`` is the stored verdict's (every study
    scale). ``min_samples`` gates only that verdict (``scalp_model.verdict``):
    ``p`` is shown beside its ``n`` and interval whatever ``n`` is."""
    scale = _scale_key(scalp_config.iv_scale())
    conn = scalp_store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT role, bucket, result FROM outcomes WHERE symbol = ? AND "
            "iv_scale = ? AND interval = ?", (symbol, scale, VERDICT_INTERVAL)).fetchall()
        edges = {}
        if bucket is not None:
            for role, body in conn.execute(
                    "SELECT role, json FROM verdicts WHERE symbol = ? AND bucket = ?",
                    (symbol, bucket)):
                edges[role] = bool(json.loads(body).get("edge"))
    finally:
        conn.close()
    out = {}
    for role in scalp_sim.ROLES:
        n = w = n_base = w_base = 0
        for r_role, r_bucket, result in rows:
            win = _label(result)
            if r_role != role or win is None:
                continue
            n_base += 1
            w_base += win
            if bucket is not None and r_bucket == bucket:
                n += 1
                w += win
        lo, hi = scalp_model.wilson(w, n) if n else (None, None)
        out[role] = {"p": w / n if n else None, "lo": lo, "hi": hi, "n": n,
                     "p_base": w_base / n_base if n_base else None, "n_base": n_base,
                     "edge": edges.get(role, False)}
    return out


__all__ = ["rebuild", "current_rates", "SESSION_SYMBOL"]
