"""Rank Board — today's whole cross-section, ranked, with its gates showing.

The Analyze tab answers "what about THIS name?". This answers "of everything the
model can see, what is best and worst right now?" — the shortlist the
single-symbol card was always missing.

Tier-1 reader of ``cache:trade:rank_board``. Every number is computed
service-side by ``trade_svc.rank_board``; this module formats and wires only.

Two things the page must not overclaim, and both have their own line:

**What it ranks BY.** The composite is ~48% volatility weight, and Phase 4
measured it at cross-sectional IC +0.16 when the market rises and −0.11 when it
falls. On a ranked board that means the top of the ordering IS the high-beta
end, which is the single most important thing to know before reading down it.

**Why a short pool looks the way it does.** Three different situations produce a
thin or unusual short list — the tape has not cleared the short side, the
cross-section is too small to have a bottom decile, or there genuinely are no
candidates — and they are indistinguishable unless the page says which.
"""
from nicegui import ui

import bus_client
from pages import busy as _busy
from pages import fmt
from pages.options.theme import (QUASAR_INTERNAL_CSS, PAGE, CARD, EYEBROW,
                                 LABEL, MUTED, BTN_PRIMARY)
from pages.ui_guard import guard
from pages.view_watch import watch_view

VIEW = "trade:rank_board"
POLL_SEC = 5.0

_COLS = [
    {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left",
     "sortable": True},
    {"name": "decile", "label": "Decile", "field": "decile", "sortable": True},
    {"name": "composite", "label": "Composite", "field": "composite",
     "sortable": True},
    {"name": "percentile", "label": "Rank", "field": "percentile"},
    {"name": "verdict", "label": "Read", "field": "verdict"},
    {"name": "expected_fwd", "label": "Expected", "field": "expected_fwd"},
    {"name": "gates", "label": "Gates", "field": "gates", "align": "left"},
]


def board_rows(board):
    """Display rows, best first. Missing numbers render as an em dash — the
    board must never print a 0 it did not measure."""
    out = []
    for r in ((board or {}).get("rows") or []):
        comp = fmt.num(r.get("composite"))
        pct = fmt.num(r.get("percentile"))
        exp = fmt.num(r.get("expected_fwd"))
        gates = r.get("gates") or []
        out.append({
            "symbol": r.get("symbol", "?"),
            "decile": r.get("decile") if r.get("decile") is not None else "—",
            "composite": f"{comp:+.2f}" if comp is not None else "—",
            "percentile": f"{int(pct)}th" if pct is not None else "—",
            "verdict": r.get("verdict") or "—",
            "expected_fwd": f"{exp:+.1%}" if exp is not None else "—",
            "gates": "; ".join(gates) if gates else "—",
            "pool": r.get("pool") or "",
            "disqualified": bool(r.get("disqualified")),
        })
    return out


def pool_headline(board, side):
    """``{"title", "note"}`` for one pool.

    The note is the load-bearing half: an unusual short pool has three possible
    causes and they look identical without it."""
    board = board or {}
    pool = board.get(f"{side}_pool") or []
    label = "Long candidates" if side == "long" else "Short candidates"
    title = f"{label} — {len(pool)}"

    if board.get("thin_cross_section"):
        return {"title": title,
                "note": ("Too few names in today's cross-section to form a "
                         "top and bottom decile — this is a sample-size "
                         "limit, not a reading of the market.")}

    mf = (board.get("market_filter") or {}).get(side) or {}
    reasons = "; ".join(mf.get("reasons") or [])
    if side == "short" and board.get("short_expression") == "relative":
        note = ("Express these RELATIVE, not as directional shorts: the model "
                "predicts excess return vs SPY, so a bottom-decile name is "
                "predicted to LAG, not to fall.")
        return {"title": title, "note": f"{note} {reasons}".strip()}
    if mf.get("state") == "blocked":
        return {"title": title,
                "note": f"The tape has blocked this side. {reasons}".strip()}
    return {"title": title,
            "note": ("Directional expression is cleared."
                     + (f" {reasons}" if reasons else ""))}


def board_exposure_note(board):
    """What the ordering is actually ranking by. '' when unknown."""
    share = fmt.num((board or {}).get("risk_share"))
    if share is None:
        return ""
    return (f"{share:.0%} of the ranking weight sits on volatility factors — "
            "so the top of this board is the high-beta end of the universe, "
            "and that ordering reverses when the market falls.")


def gates_note(board):
    """Which gates the board actually checked. '' when it lists none.

    Without this, a row showing no gates reads as "cleared everything the
    Analyze card checks", which it has not."""
    gates = (board or {}).get("gates_evaluated") or []
    if not gates:
        return ""
    return "Gates checked here: " + "; ".join(gates) + "."


def meta_line(board):
    board = board or {}
    bits = [f"model {board.get('model_version') or '?'}",
            f"{board.get('n', 0)} names"]
    if board.get("as_of"):
        bits.append(f"as of {board['as_of']}")
    if board.get("regime_key"):
        bits.append(f"regime {board['regime_key']}")
    return " · ".join(bits)


def render():
    ui.add_css(QUASAR_INTERNAL_CSS)
    state = {"board": bus_client.read(VIEW) or {}}

    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label("RANK BOARD").classes(EYEBROW)
                ui.label("Today's cross-section, ranked").classes(
                    f"text-h6 {LABEL}")
            rebuild = ui.button("Rebuild", color=None) \
                .props("no-caps").classes(BTN_PRIMARY)

        meta = ui.label("").classes(f"text-xs {MUTED}")
        exposure = ui.label("").classes("text-xs text-amber-9")
        gates = ui.label("").classes(f"text-xs {MUTED}")

        pools = {}
        for side in ("long", "short"):
            with ui.column().classes(f"{CARD} w-full gap-1"):
                pools[side] = {
                    "title": ui.label("").classes(f"text-subtitle2 {LABEL}"),
                    "note": ui.label("").classes(f"text-xs {MUTED}"),
                    "names": ui.label("").classes(f"text-sm {LABEL}"),
                }

        with ui.column().classes(f"{CARD} w-full gap-2") as board_card:
            ui.label("Full cross-section").classes(EYEBROW)
            table = ui.table(columns=_COLS, rows=[], row_key="symbol") \
                .classes("w-full").props("dense flat")

    # A rebuild rescores the whole universe snapshot; without a wait the button
    # looks inert for as long as that takes.
    spinner = _busy.build_busy(board_card, "Rebuilding the board…")

    @guard
    def _request():
        spinner.show("Rebuilding the board…")
        bus_client.request("trade", {"type": "rank_board", "args": {}})

    rebuild.on_click(_request)

    def _paint():
        spinner.hide()
        b = state["board"] or {}
        meta.text = meta_line(b)
        exposure.text = board_exposure_note(b)
        exposure.set_visibility(bool(exposure.text))
        gates.text = gates_note(b)
        for side, refs in pools.items():
            head = pool_headline(b, side)
            refs["title"].text = head["title"]
            refs["note"].text = head["note"]
            names = b.get(f"{side}_pool") or []
            refs["names"].text = ", ".join(names) if names else "—"
        table.rows = board_rows(b)
        table.update()

    @guard
    def _on_change():
        state["board"] = bus_client.read(VIEW) or {}
        _paint()

    _paint()
    watch_view(VIEW, _on_change, interval=POLL_SEC)
