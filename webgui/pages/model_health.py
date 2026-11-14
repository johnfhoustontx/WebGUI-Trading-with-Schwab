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


def drift_lines(model) -> list:
    """One line per moved or shifted input, worst first as the service sent them,
    with the share of "no reading" before and after."""
    out = []
    for f in (model or {}).get("drift") or []:
        before, after = _fmt.num(f.get("missing_before")), _fmt.num(f.get("missing_after"))
        psi = _fmt.num(f.get("psi"))
        line = f"{f.get('input')}: {f.get('band')}"
        if psi is not None:
            line += f", stability index {psi:.2f}"
        p = _fmt.num(f.get("p"))
        if p is not None:
            line += f", chance alone {p:.1%}"
        if before is not None and after is not None and (before or after):
            line += f"; no reading {before:.0%} before, {after:.0%} now"
        out.append(line)
    return out


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
        table.add_slot("body-cell-verdict", _VERDICT_SLOT)
        table.add_slot("body-cell-drift", _DRIFT_SLOT)

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
