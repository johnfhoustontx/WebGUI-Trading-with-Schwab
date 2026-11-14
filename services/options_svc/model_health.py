"""Model Health: how often the app's stated chances came true, and whether the
inputs they read have drifted -> ``cache:options:model_health`` and a dated
report under ``services/options_svc/data/model_health/<date>/report.md``.

It ONLY reports. Nothing here changes a chance, a rule or another page.
Design: docs/plans/2026-11-14-model-health-design.md.

The models, each scored against the usual rate (a group's win share on earlier
days) over the same rows, each day counting once:

* ``scalp_morning`` / ``scalp_intraday``: the chance each Opening Scalp call
  froze for each of its four trades, joined to the study's outcome for that
  trade at the current ``iv_scale``. The four trades are pooled into one row;
  the usual rate is per symbol and trade (and block, for the 15-minute calls).
  Beside it, the latest weekly refit's own out-of-sample result.
* ``signals_<scanner_type>``: the captured signals' delta-implied win rate
  (``1 - |entry delta|``) against whether the signal closed with R above 0.

Beside them, two shadow forecasts scored against their own baselines:
``range`` (``range_forecast``) and ``exit`` (``exit_chance``: an open capture's
chance of still reaching its target, against the distance it has travelled).

Runs as the last step of the 16:20 scalp branch (``scheduler``) and on the
``model_health_rebuild`` command. Imports nothing from ``compute``.
"""
import datetime as _dt
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from repo_paths import MODEL_HEALTH_DIR
from shared import model_health_config as cfg
from shared import scalp_config
from shared.calibration import priced_win_rate, r_multiple
from shared.ml import baselines, metrics
from shared.numeric import finite

from services import _degrade
from services.options_svc import exit_chance, range_forecast, scalp_store, scalp_study

log = logging.getLogger("options_svc.model_health")

CACHE_KEY = "cache:options:model_health"
MORNING_SLOT = "08:36"
#: The scanner types scored, in page order. Another type found in the store is
#: appended after these.
SIGNAL_TYPES = ("0DTE", "SWING", "INCOME")
#: The inputs of a captured signal whose drift is read.
SIGNAL_INPUTS = ("entry_iv_rank", "entry_short_delta", "entry_score", "dte_at_entry")

LABELS = {"scalp_morning": "08:36 scalp chance",
          "scalp_intraday": "15-minute scalp chance"}
NOTES = {
    "scalp": "The four trades of each call pooled; the usual rate is per symbol "
             "and trade.",
    "signals": "The chance is the short strike's delta, which states the odds of "
               "expiring out of the money; the app closes trades earlier, at a "
               "target or a stop, so part of any gap is that difference. Many "
               "captured signals from September to November 2026 were also cut by "
               "the time stop on their first mark.",
}


@dataclass
class Row:
    """One stated chance and what happened. ``group`` names its usual-rate group."""
    day: str
    slot: str
    group: tuple
    p: float
    y: int
    weight: float
    usual: float


# ── the verdict ─────────────────────────────────────────────────────────────
def verdict(days, min_days, diff) -> str:
    """``no_data`` / ``too_few_days`` / ``beats`` / ``does_not_beat`` /
    ``matches``, in that order of checking. ``diff`` is the day-clustered
    interval of model loss minus usual loss: negative means the model was closer."""
    if not days or diff is None:
        return "no_data"
    if days < min_days:
        return "too_few_days"
    if diff["hi"] < 0:
        return "beats"
    if diff["lo"] > 0:
        return "does_not_beat"
    return "matches"


# ── the scalp adapters ──────────────────────────────────────────────────────
def _weights(keys):
    """``1 / count`` of each key, so each key's rows sum to one."""
    count = defaultdict(int)
    for k in keys:
        count[k] += 1
    return [1.0 / count[k] for k in keys]


def scalp_rows(kind, db_path=None):
    """``(rows, inputs)`` for ``kind`` (``"morning"`` or ``"intraday"``).

    ``rows`` are the live calls' chances joined to their outcomes, each with the
    usual rate of its group over the study's FULL outcome history (earlier days
    only); a row whose group has no earlier day is left out. ``inputs`` is one
    ``(day, {factor: reading})`` per live call, for drift."""
    scale = scalp_study._scale_key(scalp_config.iv_scale())
    conn = scalp_store.connect(db_path)
    try:
        if kind == "morning":
            outcome_rows = conn.execute(
                "SELECT day, ?, symbol, role, result FROM outcomes "
                "WHERE interval = ? AND iv_scale = ?",
                (MORNING_SLOT, scalp_study.VERDICT_INTERVAL, scale)).fetchall()
            calls = conn.execute(
                "SELECT day, ?, symbol, json FROM calls", (MORNING_SLOT,)).fetchall()
        else:
            outcome_rows = conn.execute(
                "SELECT day, slot, symbol, role, result FROM intraday_outcomes "
                "WHERE iv_scale = ?", (scale,)).fetchall()
            calls = conn.execute(
                "SELECT day, slot, symbol, json FROM intraday_calls").fetchall()
    finally:
        conn.close()

    # The usual rate over every labelled outcome.
    labelled = {}
    for day, slot, symbol, role, result in outcome_rows:
        win = scalp_study._label(result)
        if win is not None:
            labelled[(day, slot, symbol, role)] = win
    keys = sorted(labelled)
    blocks = {}

    def group(slot, symbol, role):
        if kind == "morning":
            return (symbol, role)
        if slot not in blocks:
            blocks[slot] = scalp_config.intraday_block(slot)
        return (symbol, role, blocks[slot])

    usual = baselines.usual_rates(
        [group(k[1], k[2], k[3]) for k in keys], [k[0] for k in keys],
        [labelled[k] for k in keys], _weights([(k[0], k[2], k[3]) for k in keys]))
    usual_of = dict(zip(keys, usual))

    found, inputs = [], []
    for day, slot, symbol, body in sorted(calls):
        try:
            chance = (json.loads(body) or {}).get("chance")
        except (TypeError, ValueError):
            continue
        if not isinstance(chance, dict):
            continue
        by_role = chance.get("by_role")
        if not isinstance(by_role, dict):
            continue
        readings = {}
        for push in chance.get("pushes") or ():
            if isinstance(push, dict) and isinstance(push.get("factor"), str):
                readings[push["factor"]] = finite(push.get("reading"))
        inputs.append((day, readings))
        for role, p in sorted(by_role.items()):
            p = finite(p)
            key = (day, slot, symbol, role)
            if p is None or not 0.0 <= p <= 1.0 or key not in labelled \
                    or usual_of.get(key) is None:
                continue
            found.append((key, p))
    weights = _weights([(k[0], k[2], k[3]) for k, _p in found])
    rows = [Row(day=k[0], slot=k[1], group=group(k[1], k[2], k[3]), p=p,
                y=labelled[k], weight=w, usual=usual_of[k])
            for (k, p), w in zip(found, weights)]
    return rows, inputs


def history_row(models, kind):
    """The latest weekly refit's out-of-sample result for ``kind``, its trades
    pooled by their row counts; ``None`` when no trade was scored."""
    scored = []
    for (k, _role), body in (models or {}).items():
        if k != kind or not isinstance(body, dict):
            continue
        wf = body.get("walk_forward") or {}
        brier, usual = finite(wf.get("brier")), finite(wf.get("brier_usual"))
        n = finite(body.get("rows"))
        if brier is None or usual is None or not n or n <= 0:
            continue
        scored.append((n, brier, usual, bool(wf.get("beats_usual")),
                       int(wf.get("weeks") or 0), int(body.get("n_days") or 0)))
    if not scored:
        return None
    total = sum(s[0] for s in scored)
    brier = sum(s[0] * s[1] for s in scored) / total
    usual = sum(s[0] * s[2] for s in scored) / total
    beat = sum(1 for s in scored if s[3])
    if beat == len(scored) == 4:
        word = "beats"
    elif brier > usual:
        word = "does_not_beat"
    else:
        word = "matches"
    return {"verdict": word, "brier": brier, "brier_usual": usual,
            "weeks": max(s[4] for s in scored), "days": max(s[5] for s in scored),
            "beat_trades": beat, "trades": len(scored)}


# ── the signal adapter ──────────────────────────────────────────────────────
def _load_signal_rows(db_path=None):
    """Closed captured signals (tracked rows and out-of-session captures left
    out), from the reader ``cache:options:calibration`` already uses."""
    from tools.signal_calibration import DEFAULT_DB, load_rows
    return load_rows(db_path or DEFAULT_DB)


def signal_rows(raw):
    """``{scanner_type: (rows, inputs)}`` from closed-signal dicts."""
    by_type = defaultdict(list)
    for r in raw or ():
        if not isinstance(r, dict) or not r.get("first_seen_date"):
            continue
        rv = r_multiple(r.get("realized_pnl"), r.get("entry_max_loss"))
        if rv is None:
            continue
        by_type[str(r.get("scanner_type") or "")].append((r, 1 if rv > 0 else 0))
    out = {}
    for kind, items in by_type.items():
        items.sort(key=lambda t: str(t[0]["first_seen_date"]))
        days = [str(r["first_seen_date"]) for r, _y in items]
        weights = _weights(days)
        usual = baselines.usual_rates([kind] * len(items), days,
                                      [y for _r, y in items], weights)
        rows, inputs = [], []
        for (r, y), day, u in zip(items, days, usual):
            inputs.append((day, {k: finite(r.get(k)) for k in SIGNAL_INPUTS}))
            p = priced_win_rate(r.get("entry_short_delta"), r.get("strategy"))
            if p is None or u is None:
                continue
            rows.append(Row(day=day, slot="", group=(kind,), p=p, y=y, weight=0.0,
                            usual=u))
        for row, w in zip(rows, _weights([row.day for row in rows])):
            row.weight = w
        out[kind] = (rows, inputs)
    return out


# ── drift ───────────────────────────────────────────────────────────────────
def drift(inputs):
    """``(status, flagged)``: the worst band across inputs (``steady``,
    ``moved``, ``shifted``) or ``too_little_history``, and the moved and shifted
    inputs, worst first. The recent window is the last ``recent_sessions``
    sessions; the reference is every session before it. An input no reference
    session carried is not judged. An input is flagged only when its index
    reaches ``psi_moved`` AND a shuffle of whole sessions moved that far in
    fewer than ``drift_p`` of the draws: with a handful of recent sessions the
    index alone is mostly sampling noise."""
    recent_n, min_ref = cfg.recent_sessions(), cfg.min_reference_sessions()
    sessions = sorted({day for day, _r in inputs})
    if len(sessions) < recent_n + min_ref:
        return "too_little_history", []
    recent_days = set(sessions[-recent_n:])
    names = sorted({k for day, r in inputs if day not in recent_days for k in r})
    moved, shifted = cfg.psi_moved(), cfg.psi_shifted()
    n_perm, p_max = cfg.drift_permutations(), cfg.drift_p()
    flagged = []
    for name in names:
        by_session = defaultdict(list)
        for day, r in inputs:
            by_session[day].append(r.get(name))
        out = metrics.psi_by_session(dict(by_session), recent_n, n_perm=n_perm, seed=0)
        if out is None:
            continue
        value = out["psi"]
        if value is None or value < moved or out["p"] >= p_max:
            continue
        flagged.append({"input": name, "psi": value, "p": out["p"],
                        "band": "shifted" if value >= shifted else "moved",
                        "missing_before": out["missing_before"],
                        "missing_after": out["missing_after"]})
    flagged.sort(key=lambda f: (-f["psi"], f["input"]))
    if any(f["band"] == "shifted" for f in flagged):
        return "shifted", flagged
    return ("moved" if flagged else "steady"), flagged


# ── scoring and the payload ─────────────────────────────────────────────────
def score(key, label, rows, inputs, note=None, history=None) -> dict:
    """One model's entry in the payload."""
    p = [r.p for r in rows]
    y = [r.y for r in rows]
    w = [r.weight for r in rows]
    days = len({r.day for r in rows})
    diff = None
    if rows:
        diff = metrics.day_bootstrap_diff(
            [(r.p - r.y) ** 2 for r in rows], [(r.usual - r.y) ** 2 for r in rows],
            [r.day for r in rows], w, n_boot=cfg.bootstrap_draws())
    status, flagged = drift(inputs)
    return {"key": key, "label": label, "verdict": verdict(days, cfg.min_days(), diff),
            "days": days, "min_days": cfg.min_days(), "rows": len(rows),
            "brier": metrics.brier(p, y, w),
            "brier_usual": metrics.brier([r.usual for r in rows], y, w),
            "diff": diff, "reliability": metrics.reliability(p, y, w),
            "drift_status": status, "drift": flagged, "note": note,
            "history": history,
            "last_day": max((r.day for r in rows), default=None)}


def range_entries(scalp_db=None):
    """The range forecast's rows for the page: ``None`` when it is switched off
    or failed (counted as ``options.range_forecast``), else one dict per
    (symbol, horizon) with both verdicts and the symbol's drift."""
    if not cfg.range_enabled():
        return None
    try:
        raw = range_forecast.evaluate(scalp_db)
    except Exception:
        _degrade.degraded("options.range_forecast", detail="evaluate")
        return None
    min_days = cfg.range_min_days()
    drift_by_symbol = {}
    out = []
    for e in raw:
        inputs = e.pop("inputs")
        if e["symbol"] not in drift_by_symbol:
            drift_by_symbol[e["symbol"]] = drift(inputs)
        status, flagged = drift_by_symbol[e["symbol"]]
        out.append({**e, "min_days": min_days,
                    "verdict_yardstick": verdict(e["days"], min_days, e["diff_yardstick"]),
                    "verdict_implied": verdict(e["days"], min_days, e["diff_implied"]),
                    "drift_status": status, "drift": flagged})
    return out


def exit_entries(signals_db=None):
    """The exit chance's rows for the page: ``None`` when it is switched off or
    failed (counted as ``options.exit_chance``), else one dict per scanner type
    with its verdict against the distance baseline and its drift."""
    if not cfg.exit_enabled():
        return None
    try:
        raw = exit_chance.evaluate(signals_db)
    except Exception:
        _degrade.degraded("options.exit_chance", detail="evaluate")
        return None
    min_days = cfg.exit_min_days()
    out = []
    for e in raw:
        inputs = e.pop("inputs")
        word = verdict(e["days"], min_days, e["diff"])
        if word == "no_data" and inputs:
            # Trades exist but none could be scored yet: not enough closed
            # history before them, which is "too few days", not "no results".
            word = "too_few_days"
        status, flagged = drift(inputs)
        out.append({**e, "min_days": min_days, "verdict": word,
                    "drift_status": status, "drift": flagged})
    return out


def build(scalp_db=None, signals_db=None) -> dict:
    """The payload. Raises on an unreadable store (``run`` counts it); has no
    wall-clock field, so an unchanged rerun publishes nothing new."""
    latest = scalp_store.latest_prob_models(db_path=scalp_db) or {}
    stored = latest.get("models") or {}
    models = []
    for kind, key in (("morning", "scalp_morning"), ("intraday", "scalp_intraday")):
        rows, inputs = scalp_rows(kind, scalp_db)
        models.append(score(key, LABELS[key], rows, inputs, NOTES["scalp"],
                            history_row(stored, kind)))
    by_type = signal_rows(_load_signal_rows(signals_db))
    order = [t for t in SIGNAL_TYPES if t in by_type] + sorted(
        t for t in by_type if t not in SIGNAL_TYPES)
    for kind in order:
        rows, inputs = by_type[kind]
        if not rows:
            continue
        models.append(score(f"signals_{kind}", f"Delta win rate · {kind or 'unknown'}",
                            rows, inputs, NOTES["signals"]))
    last = [m.pop("last_day") for m in models]
    ranges = range_entries(scalp_db)
    last += [e.pop("last_day", None) for e in ranges or ()]
    exits = exit_entries(signals_db)
    last += [e.pop("last_day", None) for e in exits or ()]
    return {"data_date": max((d for d in last if d), default=None), "models": models,
            "range": ranges, "exit": exits}


# ── the report file ─────────────────────────────────────────────────────────
WORDS = {"beats": "Beats the usual rate", "matches": "Matches the usual rate",
         "does_not_beat": "Does not beat the usual rate",
         "too_few_days": "Too few days", "no_data": "No results yet"}


def _f(v, places=3):
    return "-" if v is None else f"{v:.{places}f}"


def report_markdown(payload) -> str:
    """The report: every number the page shows, as Markdown."""
    lines = [f"# Model Health, data to {payload.get('data_date') or 'no results'}", "",
             "Report only: nothing here changes a chance, a rule or a page.", "",
             "| Model | Verdict | Brier | Usual | Difference (95%) | Days | Drift |",
             "|---|---|---|---|---|---|---|"]
    for m in payload.get("models") or ():
        word = WORDS.get(m["verdict"], m["verdict"])
        if m["verdict"] == "too_few_days":
            word = f"{word} ({m['days']} of {m['min_days']})"
        d = m.get("diff")
        span = "-" if not d else f"{_f(d['mean'])} ({_f(d['lo'])} to {_f(d['hi'])})"
        lines.append(f"| {m['label']} | {word} | {_f(m['brier'])} | "
                     f"{_f(m['brier_usual'])} | {span} | {m['days']} | "
                     f"{m['drift_status']} |")
        h = m.get("history")
        if h:
            lines.append(f"| {m['label']}, weekly refit history | "
                         f"{WORDS[h['verdict']]} ({h['beat_trades']} of {h['trades']} "
                         f"trades) | {_f(h['brier'])} | {_f(h['brier_usual'])} | - | "
                         f"{h['days']} ({h['weeks']} weeks) | - |")
    for m in payload.get("models") or ():
        lines += ["", f"## {m['label']}", ""]
        if m.get("note"):
            lines += [m["note"], ""]
        lines += ["| Chance said | Mean said | Happened | Rows |", "|---|---|---|---|"]
        for lo, hi, mean_p, rate, n in m["reliability"]:
            if n:
                lines.append(f"| {lo:.0%} to {hi:.0%} | {_f(mean_p, 2)} | "
                             f"{_f(rate, 2)} | {n} |")
        if m["drift"]:
            lines += ["", "Inputs that moved:", ""]
            for f in m["drift"]:
                lines.append(f"- {f['input']}: {f['band']}, PSI {_f(f['psi'], 2)} "
                             f"(chance {_f(f.get('p'), 3)}); "
                             f"no reading {_f(f['missing_before'], 2)} -> "
                             f"{_f(f['missing_after'], 2)}")
    ranges = payload.get("range")
    if ranges:
        lines += ["", "## Range forecast (shadow)", "",
                  "Pinball loss of the 10th, 50th and 90th percentiles of the log "
                  "range still to come; a negative difference means the forecast "
                  "was closer. Nothing uses it.", "",
                  "| Symbol | Horizon | Loss | vs realised volatility | vs implied move "
                  "| Inside 10-90 | Days | Drift |", "|---|---|---|---|---|---|---|---|"]
        for e in ranges:
            h = "rest of session" if e["horizon"] == 0 else f"{e['horizon']} min"
            short = {k: v.replace(" the usual rate", "") for k, v in WORDS.items()}
            dy, di = e.get("diff_yardstick") or {}, e.get("diff_implied") or {}
            lines.append(
                f"| {e['symbol']} | {h} | {_f(e.get('loss'), 4)} | "
                f"{short[e['verdict_yardstick']]} {_f(dy.get('mean'), 4)} "
                f"({_f(dy.get('lo'), 4)} to {_f(dy.get('hi'), 4)}) | "
                f"{short[e['verdict_implied']]} {_f(di.get('mean'), 4)} "
                f"({_f(di.get('lo'), 4)} to {_f(di.get('hi'), 4)}) | "
                f"{_f(e.get('coverage'), 2)} | {e['days']} | {e['drift_status']} |")
    exits = payload.get("exit")
    if exits:
        short = {k: v.replace(" the usual rate", "") for k, v in WORDS.items()}
        lines += ["", "## Exit chance (shadow)", "",
                  "At each mark of a closed captured credit signal, before it first "
                  "reached its profit target, the chance it still would before its own "
                  "exits ended it, against the rate by how far it had already travelled. "
                  "A negative difference means the model was closer. The labels follow "
                  "the exit rules running at the time. Nothing uses it.", "",
                  "| Type | Verdict vs distance | Brier | Distance | Difference (95%) "
                  "| Rows | Trades | Days | Drift |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for e in exits:
            word = short.get(e["verdict"], e["verdict"])
            if e["verdict"] == "too_few_days":
                word = f"{word} ({e['days']} of {e['min_days']})"
            d = e.get("diff")
            span = "-" if not d else f"{_f(d['mean'])} ({_f(d['lo'])} to {_f(d['hi'])})"
            lines.append(f"| {e['type']} | {word} | {_f(e.get('brier'))} | "
                         f"{_f(e.get('brier_distance'))} | {span} | {e['rows']} | "
                         f"{e['trades']} | {e['days']} | {e['drift_status']} |")
    return "\n".join(lines) + "\n"


def run(bus, scalp_db=None, signals_db=None, out_dir=None):
    """Build, publish ``cache:options:model_health`` and write the report. On any
    failure nothing is published, the failure is counted and ``None`` returned."""
    try:
        payload = build(scalp_db=scalp_db, signals_db=signals_db)
    except Exception:
        _degrade.degraded("options.model_health", detail="build")
        return None
    bus.cache_set(CACHE_KEY, payload, skip_unchanged=True)
    try:
        folder = Path(out_dir or MODEL_HEALTH_DIR) / (payload["data_date"] or
                                                      _dt.date.today().isoformat())
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.md").write_text(report_markdown(payload), encoding="utf-8")
    except Exception:
        _degrade.degraded("options.model_health", detail="report")
    log.info("model health: %d model(s), data to %s", len(payload["models"]),
             payload["data_date"])
    return payload


def nightly(bus):
    """The scheduler's step: ``run`` unless ``[model_health] enabled`` is off."""
    if not cfg.enabled():
        log.info("model health: skipped (enabled = false)")
        return None
    return run(bus)


__all__ = ["CACHE_KEY", "Row", "verdict", "scalp_rows", "history_row", "signal_rows",
           "drift", "score", "range_entries", "build", "report_markdown", "run", "nightly"]
