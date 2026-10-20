"""The intraday study: stored bars -> intraday outcomes -> walk-forward verdicts.

The 08:36 study (``scalp_study``) pointed at the 15-minute slots (design
2026-10-20-scalp-intraday-calls, section 4). ``rebuild`` replays every stored
trading day: at every slot (08:51 ... 13:51 CT) it takes each symbol's reading
(``scalp_intraday_inputs.reading``) and, when the reading has a direction and a
usable volatility, simulates the four 0DTE roles (``scalp_sim.simulate``) from
that slot's entry along the stored 5-minute bars, at every volatility multiplier
the study tries. Each (symbol, time block, role, alignment bucket) is then judged
walk-forward against the plain base rate, and ``intraday_outcomes`` and
``intraday_verdicts`` are replaced in ONE transaction. The same bars give the same
rows (no clock, no randomness; rebuilt, never appended). The 08:36 tables, the
recorded readings and calls, and the bars are never touched.

The measurement's rules, each one fixed before any outcome was read:

* the entry is the reading's own ``entry_ts`` (the slot floored to the 5-minute
  boundary, the close of the last complete bar) - never a wall-clock time; the
  reading sees no bar at or after it, and the trade's path starts at it;
* a (day, slot, symbol) has no rows when the day is a configured early close
  (``[intraday] early_close_days``) and the slot's trade would pass it (no call
  is made there either), when the day is a short session ($SPX's
  regular bars end early: the 0DTE settlement the pricer assumes is wrong), when
  the reading has no direction, when there is no usable vol at the entry, or when
  the path holds fewer than ``time_stop_min / 5`` bars in
  ``[entry, entry + time_stop_min)`` (a hole must not read as a timeout). Each is
  counted in ``rebuild``'s ``skipped``;
* a ``skip`` from ``simulate`` itself (the entry premium under ``min_premium`` -
  common late in the day) IS stored, with ``result = "skip"``, and never labelled.
  Labels: win 1, loss and timeout 0;
* 5-minute bars only: there are no 1-minute rows in this study;
* **the day is the unit.** Rows of one day share one tape over overlapping hours,
  so they are not independent. One walk-forward stream per (symbol, block, role,
  scale), its rows sorted by (day, slot), keyed for the walk-forward by the DAY:
  ``scalp_model.walk_forward`` folds a day's rows in only when the day is over, so
  no row ever sees another row of its own day, whatever the slot. A cell's ``n``
  is its DISTINCT days (``scalp_model.verdict_by_day``), ``min_samples`` gates on
  days, and ``current_rates``' interval is Wilson with ``n`` = days around the row
  mean (``scalp_model.wilson_days``);
* an edge only if it holds at every multiplier in ``iv_scales()``; the current
  ``iv_scale()`` is simulated too (it is the rate the page shows) but does not vote;
* and only if, at each of those multipliers, the bucket's Brier sits at least
  ``[intraday] min_brier_margin`` below the base's (declared before the study was
  read): on pure-noise bars a bucket holding most of a cell's rows nearly IS the
  base rate, and "strictly lower" alone passed such near-ties. The 08:36 study's
  rule (``scalp_model.verdict``) has no margin and is unchanged.

Imports nothing from ``compute``.
"""
import json
import logging
import time
from collections import Counter

from shared import market_calendar as _mc
from shared import scalp_config

from services.options_svc import (
    scalp_intraday_inputs, scalp_model, scalp_sim, scalp_store, scalp_study)
from services.options_svc.scalp_fetch import ct

log = logging.getLogger("options_svc.scalp_intraday_study")

#: The only bar size the intraday study reads.
INTERVAL = scalp_study.VERDICT_INTERVAL
_SESSION_SECONDS = 7 * 3600     # 08:30-15:30 CT covers any regular session
#: The order of the columns ``rebuild`` writes and ``scored_streams`` reads.
COLUMNS = ("day", "slot", "symbol", "iv_scale", "role", "structure", "block",
           "bucket", "result", "minutes", "entry_value", "both_in_bar")


def _blocks(slots) -> list:
    """The time blocks the slots fall in, in slot order (each once)."""
    return list(dict.fromkeys(b for b in (scalp_config.intraday_block(s) for s in slots)
                              if b is not None))


def _day_bars(symbols, day, db_path) -> dict:
    """``{symbol: [Bar]}``: each symbol's 5-minute bars of the regular session,
    read once per day; every path is sliced from them."""
    start = int(_mc.regular_open_on(day).timestamp())
    return {sym: scalp_store.bars(sym, INTERVAL, start, start + _SESSION_SECONDS,
                                  db_path=db_path)
            for sym in symbols}


def _path(day_bars, entry_ts, stop_min):
    """The bars starting in ``[entry_ts, entry_ts + stop)``, or ``None`` when the
    window is short of a full time stop's worth."""
    end = entry_ts + stop_min * 60
    got = [b for b in day_bars if entry_ts <= b.ts < end]
    return got if len(got) >= scalp_study._needed(INTERVAL, stop_min) else None


def _rules() -> dict:
    """The trade's four exit rules, read ONCE per rebuild: every row of one
    study is priced under the same rules (a config save mid-rebuild cannot split
    it), and ~135,000 simulations do not each re-stat the config file."""
    return {"target_pct": scalp_config.target_pct(), "stop_pct": scalp_config.stop_pct(),
            "time_stop_min": scalp_config.time_stop_min(),
            "min_premium": scalp_config.min_premium()}


def _simulate_slot(r, block, bucket, scales, path, rules, rows):
    """Append the slot's outcome rows: every role at every scale."""
    day, symbol = r.day, r.symbol
    step, width = scalp_config.strike_step(symbol), scalp_config.width(symbol)
    otm = scalp_config.otm_steps()
    entry = ct(r.entry_ts)
    for role in scalp_sim.ROLES:
        structure = scalp_sim.structure_for(role, r.direction)
        strikes = scalp_sim.strikes_for(structure, r.entry_spot, step, width, otm)
        for scale in scales:
            out = scalp_sim.simulate(structure, r.entry_spot, strikes, entry, day,
                                     r.vol_at_entry * scale, path, INTERVAL, **rules)
            rows.append((day.isoformat(), r.slot, symbol, scale, role, structure,
                         block, bucket, out.result, out.minutes, out.entry_value,
                         int(bool(out.both_in_bar))))


def scored_streams(outcome_rows) -> dict:
    """``{(symbol, block, role, iv_scale): (stream, scored)}`` from outcome rows in
    ``COLUMNS`` order (as ``rebuild`` writes them and the table returns them).

    ``stream`` is the labelled rows, sorted by (day, slot), as ``{day, slot,
    bucket, win}`` with ``day`` the ISO date - the walk-forward key, so rows of
    one day never see each other; ``scored`` is ``scalp_model.walk_forward``'s
    answer for them, in the same order. Skips are left out."""
    streams = {}
    for (day, slot, sym, scale, role, _st, block, bucket, result,
         *_rest) in outcome_rows:
        win = scalp_study._label(result)
        if win is None:
            continue
        streams.setdefault((sym, block, role, scale), []).append(
            {"day": day, "slot": slot, "bucket": bucket, "win": win})
    out = {}
    for key, rows in streams.items():
        rows.sort(key=lambda r: (r["day"], r["slot"]))
        out[key] = (rows, scalp_model.walk_forward(rows))
    return out


def _verdict_rows(outcome_rows, symbols, blocks, scales):
    """``(symbol, block, role, bucket, json)`` for every symbol x block x role x
    bucket."""
    streams = scored_streams(outcome_rows)
    min_n = scalp_config.min_samples()
    margin = scalp_config.min_brier_margin()
    study_scales = [scalp_study._scale_key(s) for s in scalp_config.iv_scales()]
    out = []
    for sym in symbols:
        for block in blocks:
            for role in scalp_sim.ROLES:
                by_scale = {}
                for scale in scales:
                    rows, scored = streams.get((sym, block, role, scale), ([], []))
                    by_scale[scale] = scalp_model.verdict_by_day(scored, rows, min_n,
                                                                 min_margin=margin)
                across = scalp_model.edge_across_scales(
                    {s: by_scale[s] for s in study_scales if s in by_scale})
                for bucket in scalp_model.BUCKETS:
                    body = {"scales": {str(s): by_scale[s][bucket] for s in scales},
                            "edge": bool(across.get(bucket, False)),
                            "edge_scales": study_scales, "unit": "day",
                            # The gate the edge was judged under, so a reader
                            # never re-reads a config changed since.
                            "min_margin": margin, "min_samples": min_n}
                    out.append((sym, block, role, bucket, json.dumps(
                        body, sort_keys=True, separators=(",", ":"))))
    return out


def rebuild(db_path=None) -> dict:
    """Recompute the intraday study from the stored bars and replace its two
    tables.

    Returns counts: ``days`` replayed, ``slots`` (day x slot x symbol readings
    taken, short sessions included), ``outcomes`` written, ``skipped`` by reason
    (per day x slot x symbol), ``verdicts`` written and ``seconds`` taken."""
    started = time.monotonic()
    symbols = scalp_config.symbols()
    scales = scalp_study._scales()
    rules = _rules()
    stop_min = rules["time_stop_min"]
    cuts = scalp_config.buckets()
    days = scalp_study._stored_days(symbols, db_path)

    outcome_rows, slots_seen, blocks = [], 0, []
    skipped = Counter()
    for day in days:
        slots = scalp_config.intraday_slots(day)
        for b in _blocks(slots):
            if b not in blocks:
                blocks.append(b)
        # An early close: no call is made at a slot whose trade would pass it.
        cut = [s for s in slots if scalp_config.intraday_slot_cut(day, s, stop_min)]
        if cut:
            slots_seen += len(cut) * len(symbols)
            skipped["early_close"] += len(cut) * len(symbols)
            slots = [s for s in slots if s not in cut]
        if scalp_study._short(day, db_path):
            slots_seen += len(slots) * len(symbols)
            skipped["short_session"] += len(slots) * len(symbols)
            continue
        bars = _day_bars(symbols, day, db_path)
        cache = {}     # one reading cache per COMPLETED day (never across days)
        for slot in slots:
            block = scalp_config.intraday_block(slot)
            for symbol in symbols:
                slots_seen += 1
                if block is None:
                    skipped["no_block"] += 1
                    continue
                r = scalp_intraday_inputs.reading(symbol, day, slot, db_path=db_path,
                                                  cache=cache)
                if r.direction is None or r.entry_spot is None:
                    skipped["no_direction"] += 1
                    continue
                if r.vol_at_entry is None:
                    skipped["no_vol"] += 1
                    continue
                path = _path(bars[symbol], r.entry_ts, stop_min)
                if path is None:
                    skipped["data_hole"] += 1
                    continue
                bucket = scalp_model.bucket_of(scalp_intraday_inputs.alignment(r), cuts)
                _simulate_slot(r, block, bucket, scales, path, rules, outcome_rows)

    if not blocks:
        blocks = _blocks(scalp_config.intraday_slots(None))
    verdict_rows = _verdict_rows(outcome_rows, symbols, blocks, scales)

    conn = scalp_store.connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM intraday_outcomes")
            conn.execute("DELETE FROM intraday_verdicts")
            conn.executemany(
                f"INSERT INTO intraday_outcomes ({', '.join(COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(COLUMNS))})", outcome_rows)
            conn.executemany(
                "INSERT INTO intraday_verdicts (symbol, block, role, bucket, json) "
                "VALUES (?, ?, ?, ?, ?)", verdict_rows)
    finally:
        conn.close()

    counts = {
        "days": len(days),
        "slots": slots_seen,
        "outcomes": len(outcome_rows),
        "skipped": dict(skipped),
        "verdicts": len(verdict_rows),
        "seconds": round(time.monotonic() - started, 2),
    }
    log.info("scalp intraday study rebuilt: %s", counts)
    return counts


def current_rates(symbol, block, bucket, db_path=None) -> dict:
    """Per role, ``bucket``'s win rate in ``block`` over EVERY stored day at the
    current ``iv_scale``: ``{p, lo, hi, n_days, p_base, n_base_days, edge}``.

    ``p`` is the mean over the cell's labelled rows and ``lo``/``hi`` its Wilson
    interval with ``n`` = ``n_days``, the distinct days those rows came from (the
    day is the unit); ``p_base`` / ``n_base_days`` are the same for every labelled
    row of the role in the block. ``p``/``lo``/``hi`` are ``None`` with no
    labelled row (and always for ``bucket=None``, an unknown alignment);
    ``p_base`` is ``None`` when the role has none in the block. ``edge`` is the
    stored verdict's (every study scale)."""
    scale = scalp_study._scale_key(scalp_config.iv_scale())
    conn = scalp_store.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT day, role, bucket, result FROM intraday_outcomes WHERE symbol = ? "
            "AND block = ? AND iv_scale = ?", (symbol, block, scale)).fetchall()
        edges = {}
        if bucket is not None:
            for role, body in conn.execute(
                    "SELECT role, json FROM intraday_verdicts WHERE symbol = ? AND "
                    "block = ? AND bucket = ?", (symbol, block, bucket)):
                edges[role] = bool(json.loads(body).get("edge"))
    finally:
        conn.close()
    out = {}
    for role in scalp_sim.ROLES:
        n = w = n_base = w_base = 0
        days, base_days = set(), set()
        for day, r_role, r_bucket, result in rows:
            win = scalp_study._label(result)
            if r_role != role or win is None:
                continue
            n_base += 1
            w_base += win
            base_days.add(day)
            if bucket is not None and r_bucket == bucket:
                n += 1
                w += win
                days.add(day)
        lo, hi = scalp_model.wilson_days(w, n, len(days)) if n else (None, None)
        out[role] = {"p": w / n if n else None, "lo": lo, "hi": hi,
                     "n_days": len(days),
                     "p_base": w_base / n_base if n_base else None,
                     "n_base_days": len(base_days),
                     "edge": edges.get(role, False)}
    return out


__all__ = ["rebuild", "current_rates", "scored_streams", "COLUMNS", "INTERVAL"]
