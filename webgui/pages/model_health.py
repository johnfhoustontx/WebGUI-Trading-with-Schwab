"""Model Health (More -> Model Health, ``/model-health``): how well the app's
stated chances have matched what happened, and whether their inputs drifted.

A READER of ``cache:options:model_health``, which ``options_svc`` builds each
trading night after the 16:20 scalp job (and on Recompute). The page only
reports: it changes no chance, no rule and no other page, and it shows no
recalibrated figure. The operator's words: "Just flag it on the page, don't
change anything." Design: docs/plans/2026-11-14-model-health-design.md

Pure builders (rows / verdict text / status line / the detail lines) are
module-level and NiceGUI-free, so they are tested without a browser.

Tier 1: the page kit, the formatters and the bus client only; no service or
engine import.

The Brier score prints THREE decimals, a deliberate exception to the
two-decimal rule for ratios: the differences that decide a verdict are often in
the third place, and two decimals would print 0.24 vs 0.24 for a real gap.
"""
from __future__ import annotations

import bus_client
from nicegui import run, ui

from pages import fmt as _fmt
from pages import ui_kit as kit
from pages.options.theme import MUTED, TXT_NEG, TXT_NEUTRAL, TXT_POS, TXT_WARN
from pages.ui_guard import guard_async
from pages.view_watch import watch_view

VIEW = "options:model_health"

# The verdict words, and a FIXED class per verdict (never a runtime-built one).
VERDICT_WORDS = {
    "beats": "Beats the usual rate",
    "matches": "Matches the usual rate",
    "does_not_beat": "Does not beat the usual rate",
    "too_few_days": "Too few days",
    "no_data": "No results yet",
}
VERDICT_CLASSES = {
    "beats": TXT_POS,
    "matches": TXT_NEUTRAL,
    "does_not_beat": TXT_NEG,
    "too_few_days": MUTED,
    "no_data": MUTED,
}
DRIFT_WORDS = {"steady": "Steady", "moved": "moved", "shifted": "shifted",
               "too_little_history": "Too little history"}
DRIFT_CLASSES = {"steady": TXT_NEUTRAL, "moved": TXT_WARN, "shifted": TXT_NEG,
                 "too_little_history": MUTED}

NOT_YET = ("Model Health has not been computed yet. It runs each trading night "
           "after the 16:20 scalp job; Recompute runs it now.")
NO_MODELS = "Model Health ran, but found no stated chances with results to score."


def brier(v) -> str:
    """A Brier score to three decimals (see the module docstring), or a dash."""
    f = _fmt.num(v)
    return _fmt.NO_READING if f is None else f"{f:.3f}"


def diff_text(diff) -> str:
    """``0.024 (-0.010 to 0.059)``: the model's Brier minus the usual rate's,
    with its 95% interval over whole days. Negative means the model was closer."""
    d = diff or {}
    mean, lo, hi = _fmt.num(d.get("mean")), _fmt.num(d.get("lo")), _fmt.num(d.get("hi"))
    if mean is None:
        return _fmt.NO_READING
    if lo is None or hi is None:
        return f"{mean:+.3f}"
    return f"{mean:+.3f} ({lo:+.3f} to {hi:+.3f})"


def verdict_text(model) -> str:
    """The verdict in words; "Too few days (16 of 20)" counts toward the floor."""
    m = model or {}
    word = m.get("verdict")
    text = VERDICT_WORDS.get(word, _fmt.NO_READING)
    if word == "too_few_days":
        days, floor = _fmt.num(m.get("days")), _fmt.num(m.get("min_days"))
        if days is not None and floor is not None:
            text = f"{text} ({days:.0f} of {floor:.0f})"
    return text


def drift_text(model) -> str:
    """``Steady`` / ``2 moved`` / ``1 shifted, 1 moved`` / ``Too little history``."""
    m = model or {}
    status = m.get("drift_status")
    flagged = m.get("drift") or []
    if status in (None, "steady", "too_little_history") or not flagged:
        return DRIFT_WORDS.get(status, _fmt.NO_READING)
    shifted = sum(1 for f in flagged if f.get("band") == "shifted")
    moved = sum(1 for f in flagged if f.get("band") == "moved")
    parts = [f"{n} {word}" for n, word in ((shifted, "shifted"), (moved, "moved")) if n]
    return ", ".join(parts)


def rows(payload) -> list:
    """The table: one row per model, and under each scalp model a row for its
    weekly refit's own out-of-sample history, labelled as the refit's."""
    out = []
    for m in (payload or {}).get("models") or []:
        m = m or {}
        key = m.get("key") or m.get("label") or str(len(out))
        verdict = m.get("verdict")
        status = m.get("drift_status")
        out.append({
            "id": key, "model": m.get("label") or key,
            "verdict": verdict_text(m),
            "_verdict_class": VERDICT_CLASSES.get(verdict, MUTED),
            "brier": brier(m.get("brier")), "usual": brier(m.get("brier_usual")),
            "diff": diff_text(m.get("diff")),
            "days": _fmt.fixed(m.get("days"), 0),
            "drift": drift_text(m),
            "_drift_class": DRIFT_CLASSES.get(status, MUTED),
        })
        h = m.get("history")
        if h:
            word = h.get("verdict")
            text = VERDICT_WORDS.get(word, _fmt.NO_READING)
            trades, beat = _fmt.num(h.get("trades")), _fmt.num(h.get("beat_trades"))
            if trades is not None and beat is not None:
                text = f"{text} ({beat:.0f} of {trades:.0f} trades)"
            weeks = _fmt.num(h.get("weeks"))
            out.append({
                "id": f"{key}:history", "model": "Weekly refit history",
                "verdict": text,
                "_verdict_class": VERDICT_CLASSES.get(word, MUTED),
                "brier": brier(h.get("brier")), "usual": brier(h.get("brier_usual")),
                "diff": _fmt.NO_READING,
                "days": (f"{_fmt.fixed(h.get('days'), 0)} ({weeks:.0f} weeks)"
                         if weeks is not None else _fmt.fixed(h.get("days"), 0)),
                "drift": _fmt.NO_READING, "_drift_class": MUTED,
            })
    return out


def columns():
    spec = [("model", "Model"), ("verdict", "Verdict"), ("brier", "Brier"),
            ("usual", "Usual rate's Brier"), ("diff", "Difference (95%)"),
            ("days", "Days"), ("drift", "Inputs")]
    return [{"name": f, "label": l, "field": f, "sortable": False, "align": "left"}
            for f, l in spec]


def status_text(payload) -> str:
    """The line under the title: the data date and the model count."""
    p = payload or {}
    if not p:
        return NOT_YET
    models = p.get("models") or []
    if not models:
        return NO_MODELS
    n = len(models)
    date = p.get("data_date") or _fmt.NO_READING
    return f"Results to {date} · {n} model{'' if n == 1 else 's'}"


def reliability_lines(model) -> list:
    """``30% to 40%: said 36%, happened 31% (212 rows)`` for each filled slice."""
    out = []
    for item in (model or {}).get("reliability") or []:
        try:
            lo, hi, said, rate, n = item
        except (TypeError, ValueError):
            continue
        said, rate, n = _fmt.num(said), _fmt.num(rate), _fmt.num(n)
        if not n or said is None or rate is None:
            continue
        out.append(f"{lo:.0%} to {hi:.0%}: said {said:.0%}, happened {rate:.0%} "
                   f"({n:.0f} rows)")
    return out


#: The range forecast's inputs in words (the scalp inputs print as stored).
INPUT_WORDS = {"rv_30": "realised volatility, last 30 minutes",
               "rv_day": "realised volatility, session so far",
               "rv_prev": "realised volatility, previous session",
               "rv_5": "realised volatility, previous 5 sessions",
               "gap": "overnight gap", "iv": "volatility index",
               "iv_change": "volatility index since the open",
               "volume": "volume against its usual level",
               "short_delta": "short delta now",
               "cushion": "distance from the short strike",
               "entry_iv_rank": "volatility rank at entry"}


def drift_lines(model) -> list:
    """One line per moved or shifted input, worst first as the service sent them,
    with the share of "no reading" before and after."""
    out = []
    for f in (model or {}).get("drift") or []:
        before, after = _fmt.num(f.get("missing_before")), _fmt.num(f.get("missing_after"))
        psi = _fmt.num(f.get("psi"))
        name = f.get("input")
        line = f"{INPUT_WORDS.get(name, name)}: {f.get('band')}"
        if psi is not None:
            line += f", stability index {psi:.2f}"
        p = _fmt.num(f.get("p"))
        if p is not None:
            line += f", chance alone {p:.1%}"
        if before is not None and after is not None and (before or after):
            line += f"; no reading {before:.0%} before, {after:.0%} now"
        out.append(line)
    return out


# ── the range forecast (shadow) ─────────────────────────────────────────────
RANGE_TITLE = "Range forecast (shadow)"
RANGE_NOTE = ("How far each symbol still travels (high minus low) over the next 30 "
              "minutes, 60 minutes and the rest of the session, forecast every 15 "
              "minutes from bars already closed. Scored against recent realised "
              "volatility and against the implied move from the volatility index. "
              "Loss is the pinball loss of the 10th, 50th and 90th percentiles; "
              "below zero the forecast was closer. Nothing uses it.")
RANGE_OFF = "The range forecast is switched off or could not be scored."
_SHORT = {"beats": "Beats", "matches": "Matches", "does_not_beat": "Does not beat",
          "too_few_days": "Too few days", "no_data": "No results yet"}


def horizon_text(minutes) -> str:
    m = _fmt.num(minutes)
    if m is None:
        return _fmt.NO_READING
    return "Rest of session" if m == 0 else f"{m:.0f} min"


def _short_verdict(word, entry) -> str:
    text = _SHORT.get(word, _fmt.NO_READING)
    if word == "too_few_days":
        days, floor = _fmt.num(entry.get("days")), _fmt.num(entry.get("min_days"))
        if days is not None and floor is not None:
            text = f"{text} ({days:.0f} of {floor:.0f})"
    return text


def range_rows(payload) -> list:
    """One row per (symbol, horizon), in the service's order."""
    out = []
    for e in (payload or {}).get("range") or []:
        e = e or {}
        vy, vi = e.get("verdict_yardstick"), e.get("verdict_implied")
        cover = _fmt.num(e.get("coverage"))
        out.append({
            "id": f"{e.get('symbol')}:{e.get('horizon')}",
            "symbol": e.get("symbol") or _fmt.NO_READING,
            "horizon": horizon_text(e.get("horizon")),
            "vs_yardstick": _short_verdict(vy, e),
            "_vs_yardstick_class": VERDICT_CLASSES.get(vy, MUTED),
            "diff_yardstick": diff_text(e.get("diff_yardstick")),
            "vs_implied": _short_verdict(vi, e),
            "_vs_implied_class": VERDICT_CLASSES.get(vi, MUTED),
            "diff_implied": diff_text(e.get("diff_implied")),
            "coverage": _fmt.NO_READING if cover is None else f"{cover:.0%}",
            "days": _fmt.fixed(e.get("days"), 0),
            "drift": drift_text(e),
            "_drift_class": DRIFT_CLASSES.get(e.get("drift_status"), MUTED),
        })
    return out


def range_columns():
    spec = [("symbol", "Symbol"), ("horizon", "Horizon"),
            ("vs_yardstick", "vs realised volatility"), ("diff_yardstick", "Loss difference (95%)"),
            ("vs_implied", "vs implied move"), ("diff_implied", "Loss difference (95%)"),
            ("coverage", "Inside 10th to 90th"), ("days", "Days"), ("drift", "Inputs")]
    return [{"name": f, "label": l, "field": f, "sortable": False, "align": "left"}
            for f, l in spec]


def range_drift(payload) -> list:
    """``[(symbol, [lines])]`` for each symbol whose inputs moved, once each."""
    seen, out = set(), []
    for e in (payload or {}).get("range") or []:
        sym = (e or {}).get("symbol")
        if sym in seen:
            continue
        seen.add(sym)
        lines = drift_lines(e)
        if lines:
            out.append((sym, lines))
    return out


# ── the exit chance (shadow) ────────────────────────────────────────────────
EXIT_TITLE = "Exit chance (shadow)"
EXIT_NOTE = ("At each mark of a closed captured credit signal, before it first reached "
             "its profit target, the chance it still would before its own exits ended "
             "it. Scored against the rate by how far the trade had already travelled, "
             "learned on earlier trades only. Below zero the model was closer. The "
             "outcomes follow the exit rules running at the time. Nothing uses it.")
EXIT_OFF = "The exit chance is switched off or could not be scored."


def exit_rows(payload) -> list:
    """One row per scanner type, in the service's order."""
    out = []
    for e in (payload or {}).get("exit") or []:
        e = e or {}
        word = e.get("verdict")
        out.append({
            "id": str(e.get("type")),
            "type": e.get("type") or _fmt.NO_READING,
            "verdict": _short_verdict(word, e),
            "_verdict_class": VERDICT_CLASSES.get(word, MUTED),
            "difference": diff_text(e.get("diff")),
            "brier": brier(e.get("brier")),
            "brier_distance": brier(e.get("brier_distance")),
            "rows": _fmt.fixed(e.get("rows"), 0),
            "trades": _fmt.fixed(e.get("trades"), 0),
            "days": _fmt.fixed(e.get("days"), 0),
            "drift": drift_text(e),
            "_drift_class": DRIFT_CLASSES.get(e.get("drift_status"), MUTED),
        })
    return out


def exit_columns():
    spec = [("type", "Scanner type"), ("verdict", "vs distance travelled"),
            ("difference", "Brier difference (95%)"), ("brier", "Brier"),
            ("brier_distance", "Brier, distance"), ("rows", "Marks"),
            ("trades", "Trades"), ("days", "Days"), ("drift", "Inputs")]
    return [{"name": f, "label": l, "field": f, "sortable": False, "align": "left"}
            for f, l in spec]


def exit_drift(payload) -> list:
    """``[(scanner type, [lines])]`` for each type whose inputs moved."""
    out = []
    for e in (payload or {}).get("exit") or []:
        lines = drift_lines(e)
        if lines:
            out.append(((e or {}).get("type"), lines))
    return out


def _class_slot(cls_field):
    """A cell drawn in the row's own fixed class, read from ``cls_field``."""
    return (r'''
  <q-td :props="props">
    <span :class="props.row.''' + cls_field + r'''">{{ props.value }}</span>
  </q-td>
''')


_VERDICT_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._verdict_class">{{ props.value }}</span>
  </q-td>
'''
_DRIFT_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._drift_class">{{ props.value }}</span>
  </q-td>
'''


def render():
    """Build the page: the verdict table and, per model, what it said against
    what happened and which inputs moved. Repaints when the view publishes."""
    with kit.page():
        head = kit.header("Model Health", view=VIEW)
        status = kit.status_line(NOT_YET)
        board = kit.region("Loading Model Health…")
        with board.content:
            table = kit.table(columns(), rows_per_page=0)
            details = ui.column().classes("w-full gap-3")
            ui.label(RANGE_TITLE).classes("text-base font-semibold pt-2")
            ui.label(RANGE_NOTE).classes(f"text-sm {MUTED}")
            range_status = ui.label("").classes(f"text-sm {MUTED}")
            range_table = kit.table(range_columns(), rows_per_page=0)
            range_details = ui.column().classes("w-full gap-1")
            ui.label(EXIT_TITLE).classes("text-base font-semibold pt-2")
            ui.label(EXIT_NOTE).classes(f"text-sm {MUTED}")
            exit_status = ui.label("").classes(f"text-sm {MUTED}")
            exit_table = kit.table(exit_columns(), rows_per_page=0)
            exit_details = ui.column().classes("w-full gap-1")
        exit_table.add_slot("body-cell-verdict", _VERDICT_SLOT)
        exit_table.add_slot("body-cell-drift", _DRIFT_SLOT)
        table.add_slot("body-cell-verdict", _VERDICT_SLOT)
        table.add_slot("body-cell-drift", _DRIFT_SLOT)
        range_table.add_slot("body-cell-vs_yardstick", _class_slot("_vs_yardstick_class"))
        range_table.add_slot("body-cell-vs_implied", _class_slot("_vs_implied_class"))
        range_table.add_slot("body-cell-drift", _DRIFT_SLOT)

    def _paint(payload):
        p = payload or {}
        table.rows = rows(p)
        table.update()
        status.text = status_text(payload)
        details.clear()
        with details:
            for m in p.get("models") or []:
                with ui.expansion(m.get("label") or "").classes("w-full"):
                    if m.get("note"):
                        ui.label(m["note"]).classes(f"text-sm {MUTED}")
                    ui.label("Said against happened").classes("text-sm font-semibold")
                    lines = reliability_lines(m) or ["No results yet."]
                    for line in lines:
                        ui.label(line).classes("text-sm tabular-nums")
                    ui.label("Inputs that moved").classes("text-sm font-semibold")
                    for line in drift_lines(m) or [drift_text(m)]:
                        ui.label(line).classes("text-sm tabular-nums")
        range_table.rows = range_rows(p)
        range_table.update()
        range_status.text = "" if p.get("range") or not p else RANGE_OFF
        range_details.clear()
        with range_details:
            for sym, lines in range_drift(p):
                ui.label(f"{sym} inputs that moved").classes("text-sm font-semibold")
                for line in lines:
                    ui.label(line).classes("text-sm tabular-nums")
        exit_table.rows = exit_rows(p)
        exit_table.update()
        exit_status.text = "" if p.get("exit") or not p else EXIT_OFF
        exit_details.clear()
        with exit_details:
            for kind, lines in exit_drift(p):
                ui.label(f"{kind} inputs that moved").classes("text-sm font-semibold")
                for line in lines:
                    ui.label(line).classes("text-sm tabular-nums")
        board.busy.hide()
        kit.set_busy(recompute, False)

    @guard_async
    async def _reread():
        _paint(await run.io_bound(bus_client.read, VIEW))

    @guard_async
    async def _recompute():
        try:
            await run.io_bound(bus_client.request, "options",
                               {"type": "model_health_rebuild"})
        except Exception:  # noqa: BLE001 - the toast is the report
            kit.toast("error", "Could not ask for a recompute.")
            return
        kit.set_busy(recompute)
        kit.toast("info", "Recomputing. The table repaints if any figure changed.")

    with head.actions:
        recompute = kit.button("Recompute", kind="primary", icon="refresh",
                               on_click=_recompute)

    payload = bus_client.read(VIEW)
    if payload:
        _paint(payload)
    else:
        board.busy.hide()
    watch_view(VIEW, _reread)
