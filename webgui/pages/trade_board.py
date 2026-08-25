"""Signal Desk — Rank board.

Long and short candidate tables, nine columns each, plus the model paper book
that follows them. The short table carries the market-filter note, because a
bottom-decile name in an uptrend is predicted to LAG the index rather than to
fall, and an unlabelled short list invites the trade the tape has refused.

The two pools sit SIDE BY SIDE from `xl` up and stack below it: the board's
question is which side the model prefers today, and that is a comparison, so the
two lists belong on one screen rather than one above the fold and one below.

Every table sits in an `overflow-x: auto` wrapper over a `min-width` grid, so
columns scroll rather than collide or clip at any width — nine columns of mono
do not fit a narrow window, and clipping the gates column would hide exactly the
thing the board exists to surface. That wrapper is what makes the two-up layout
safe: a pane narrower than the table scrolls itself. It only works because the
panel carries `min-w-0` — a grid item's default `min-width: auto` would refuse
to shrink and blow the column out to the table's full width instead.

⚠ The scroller is the fallback, not the plan: the columns are sized so that at
a normal desktop width both panes fit whole, because a board you have to drag
sideways to read the GATES column defeats the point of showing both sides at
once.

⚠ The amber line above the tables states what share of the ranking weight sits
on volatility factors. On a RANKED board that is the single most important thing
to know: the top of the ordering is the high-beta end of the universe.
"""
from nicegui import ui

import bus_client
from pages import fmt
from pages import terminal_theme as T
from pages import trade_help as th
from pages import trade_shell as sh
from pages import trade_terminal as tt
from pages.ui_guard import guard
from pages.view_watch import watch_view

VIEW = "trade:rank_board"
BOOK_VIEW = "trade:model_book"
POLL_SEC = 5.0

# Every fixed column is its MEASURED worst case plus ~10px, so the eight of
# them plus a one-line GATES chip fit a half-width pane without scrolling. The
# widths were taken from the rendered page in its own fonts (Manrope 12px /
# JetBrains Mono 12.5px), not estimated — measured need, in order:
# 46 38 45 56 30 40 96 89. Both wide columns are CLOSED vocabularies, which is
# what makes sizing them to content safe: DEALER's longest is `gamma_cascade`
# (96px) out of six labels plus the page's own "not collected", and IV's is
# `128 · collapsing` (89px) out of four states. A vocabulary that grows needs
# these re-measured — DEALER is `whitespace-nowrap` and would spill into IV.
# `w-full` for the same reason the Evidence grid needs it: a column child
# sizes to its own content, so a trailing `1fr` would differ per row.
_COLS = ("w-full [grid-template-columns:58px_48px_56px_64px_42px_50px_104px_96px_1fr]")
# 594px of fixed columns, gaps and padding + ~155px so one gate reads on a
# single line. A pane narrower than this still scrolls, as it always did.
_TABLE_MIN = "min-w-[750px]"
# `items-start`, not the default stretch: the pools are rarely the same
# length, and a two-row short pool padded to the height of a nine-row long
# pool reads as missing rows.
_BOARD_GRID = "grid grid-cols-1 xl:grid-cols-2 items-start"

# "BAND", not "PCTL": the number is a calibration band cut from the model's own
# score history, and on any given day the bands fill unevenly — so it is not a
# percentile of the names in this table. See `trade_terminal._RAIL_TIP`.
_HEAD = ("SYMBOL", "BAND", "SCORE", "EXP / 20D", "HIT", None, "DEALER", "IV",
         "GATES")


def metric_cell(row, side):
    """``(header, value)`` for the side-specific sixth column.

    Long shows **today's decile** — where the name sits among the ~78 scored
    right now. That is a different question from the BAND in column 2, which
    asks where the score sat against five years of the model's own output: a
    universe where every name is mid-band still has a best and a worst. The
    long metric used to be `band`, i.e. column 2 restated as a raw index, which
    only became visible when that column stopped being mislabelled "PCTL".

    Short shows days-to-cover, the squeeze read that side actually needs."""
    r = row or {}
    if side != "long":
        return "DTC", r.get("dtc", "—")
    d = fmt.num(r.get("decile"))
    return "DECILE", f"{int(d)}" if d is not None else "—"


def board_rows(board):
    """Display rows, best first. Missing numbers render as an em dash — the
    board must never print a 0 it did not measure."""
    out = []
    for r in ((board or {}).get("rows") or []):
        comp = fmt.num(r.get("composite"))
        pct = fmt.num(r.get("percentile"))
        exp = fmt.num(r.get("expected_fwd"))
        hit = fmt.num(r.get("hit_rate"))
        iv = fmt.num(r.get("atm_iv"))
        dtc = fmt.num(r.get("dtc"))
        gates = r.get("gates") or []
        out.append({
            "symbol": r.get("symbol", "?"),
            "decile": r.get("decile"),
            "pctl": f"{int(pct)}th" if pct is not None else "—",
            "score": tt.signed(comp, 2),
            "score_class": T.sign_text(comp),
            "exp": tt.signed_pct(exp, 1),
            "exp_class": T.sign_text(exp) if exp is not None else T.OFF,
            "hit": f"{hit:.0%}" if hit is not None else "—",
            "band": r.get("band"),
            "dtc": f"{dtc:.1f}" if dtc is not None else "—",
            "dealer": r.get("dealer") or "not collected",
            "dealer_class": _dealer_class(r.get("dealer")),
            "iv": f"{iv:.0f}" if iv is not None else "—",
            "iv_state": r.get("iv_state") or "",
            "iv_class": _iv_class(r.get("iv_state")),
            "gate": "; ".join(gates) if gates else "clear",
            "gate_chip": T.CHIP_WARN if gates else T.CHIP_POS,
            "pool": r.get("pool") or "",
        })
    return out


def _dealer_class(word):
    w = (word or "").lower()
    if "above" in w:
        return T.POS
    if "below" in w:
        return T.NEG
    return T.OFF


def _iv_class(state):
    return {"cheap": T.POS, "rich": T.WARN}.get((state or "").lower(), T.DIM)


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


# An empty board has kinds, and only one of them is about the market. Found
# live: a cached snapshot in the documented LEGACY flat shape carries factor
# values but no symbol names, so the board came back with zero rows — on screen
# indistinguishable from "the market offered nothing today".
_STATUS_NOTES = {
    "legacy_snapshot": (
        "Today's universe snapshot has no per-symbol detail yet, so there is "
        "nothing to rank. This is a data-shape limit, not a reading of the "
        "market — press Rebuild, or it clears on tomorrow's snapshot."),
    "no_snapshot": (
        "No universe snapshot yet today. The board fills in once the Trade "
        "service has built one — press Rebuild to build it now."),
    "no_artifact": (
        "The swing model artifact is missing, so nothing can be scored. The "
        "Analyze tab falls back to its legacy verdict; this board has no "
        "fallback because ranking IS the model."),
    "unscoreable": (
        "The snapshot has symbols but none of them could be scored — usually a "
        "thin factor history rather than anything about the market."),
}


def status_note(board):
    """Why the board is empty, when it is. '' when healthy or unrecognised.

    An unknown status returns '' rather than a guess: inventing a reason is
    worse than showing none."""
    return _STATUS_NOTES.get((board or {}).get("status"), "")


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


def book_rows(book):
    """Display rows for the model paper book, newest first."""
    out = []
    for p in ((book or {}).get("positions") or []):
        pnl = fmt.num(p.get("pnl_pct"))
        out.append({
            "symbol": p.get("symbol", "?"),
            "side": p.get("side") or "—",
            "expression": p.get("expression") or "—",
            "opened_on": p.get("opened_on") or "—",
            "pnl": tt.signed_pct(pnl, 1),
            "pnl_class": T.sign_text(pnl) if pnl is not None else T.OFF,
            "status": p.get("status") or "—",
            "close_reason": p.get("close_reason") or "",
        })
    return out


def _side_bit(label, s):
    n = (s or {}).get("n") or 0
    mean = fmt.num((s or {}).get("mean_pnl"))
    if not n or mean is None:
        return f"{label} — no closed trades"
    hr = fmt.num((s or {}).get("hit_rate"))
    hit = f", {hr:.0%} hit" if hr is not None else ""
    return f"{label} {mean:+.1%} mean over {n}{hit}"


def book_summary_line(book):
    """Realized performance, each side on its own. '' when the book is empty.

    Separate sides on purpose: this model's short pool is usually expressed
    RELATIVE to SPY, so averaging the two would hide which half is working."""
    s = (book or {}).get("summary") or {}
    if not s:
        return ""
    parts = [_side_bit("Long", s.get("long")), _side_bit("Short", s.get("short"))]
    tail = f" · {s.get('open', 0)} open, {s.get('closed', 0)} closed"
    return " · ".join(parts) + tail


def book_note():
    """What the book is, and the one thing about it that could mislead."""
    return ("Paper only, isolated from the driver's book. It trades the "
            "UNDERLYING rather than the options structure the Trade Plan "
            "suggests — a spread's theta and vega would swamp the question of "
            "whether the ranking works. A relative short is held as a pair "
            "against SPY, which is what the model actually predicts.")


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
    sh.page(_build)


def _build(state, refs):
    state["board"] = bus_client.read(VIEW) or {}
    state["book"] = bus_client.read(BOOK_VIEW) or {}
    state["hide_gated"] = False

    with ui.row().classes("w-full items-end justify-between gap-4 flex-wrap"):
        with ui.column().classes("gap-1"):
            ui.label("Rank board").classes(T.SCREEN_TITLE)
            meta = ui.label("").classes("text-[11.5px] text-[#6b7b9c]")
        filters = ui.row().classes("gap-[9px]")

    status = ui.label("").classes("text-[13px] text-[#fbbf24]")
    exposure = ui.label("").classes(f"{T.CALLOUT_TEXT} text-[12px]")
    with exposure:
        sh.tip(th.help_for("exposure_note"))
    gates = ui.label("").classes(f"{T.NOTE}")

    tables = ui.element("div").classes(f"w-full {_BOARD_GRID} gap-4")

    book_panel = sh.panel("Model paper book",
                          help=th.help_for("paper_book"))
    with book_panel:
        book_summary = ui.label("").classes("text-[13px] text-[#cfdaee]")
        ui.label(book_note()).classes(T.NOTE)
        book_wrap = ui.column().classes("w-full gap-0")

    def _paint(_a=None):
        b = state["board"] or {}
        meta.text = meta_line(b)
        status.text = status_note(b)
        status.set_visibility(bool(status.text))
        exposure.text = board_exposure_note(b)
        exposure.set_visibility(bool(exposure.text))
        gates.text = gates_note(b)

        filters.clear()
        with filters:
            on = state["hide_gated"]
            with ui.button("Hide gated" if not on else "Showing ungated only",
                           color=None).props("no-caps") \
                    .classes(T.FILTER_ON if on else T.FILTER_OFF) \
                    .on_click(_toggle_gated):
                sh.tip(th.help_for("hide_gated"))
            with ui.button("Rebuild", color=None).props("no-caps") \
                    .classes(T.FILTER_OFF).on_click(_rebuild):
                sh.tip(th.help_for("rebuild"))

        rows = board_rows(b)
        tables.clear()
        with tables:
            for side, accent in (("long", "bg-[#34d399]"), ("short", "bg-[#f87171]")):
                _table(b, side, accent, rows, state["hide_gated"])

        bk = state["book"] or {}
        book_summary.text = book_summary_line(bk) or "No positions yet."
        book_wrap.clear()
        with book_wrap:
            _book_table(book_rows(bk))

    @guard
    def _toggle_gated():
        state["hide_gated"] = not state["hide_gated"]
        _paint()

    @guard
    def _rebuild():
        # The wait is the SHELL's — all four screens share one frame, and a
        # second spinner would fight it. See test_busy_coverage's exemption.
        sp = state.get("spinner")
        if sp:
            sp.show("Rebuilding the board…")
        bus_client.request("trade", {"type": "rank_board", "args": {}})
        # The book follows the board, so one click advances both rather than
        # leaving the book a tick behind whatever it is reporting on.
        bus_client.request("trade", {"type": "model_book", "args": {}})

    @guard
    def _on_board():
        state["board"] = bus_client.read(VIEW) or {}
        state["book"] = bus_client.read(BOOK_VIEW) or {}
        _paint()

    refs["paint"].append(_paint)
    watch_view(VIEW, _on_board, interval=POLL_SEC)
    watch_view(BOOK_VIEW, _on_board, interval=POLL_SEC)


def _table(board, side, accent, rows, hide_gated):
    head = pool_headline(board, side)
    pool = set(board.get(f"{side}_pool") or [])
    picked = [r for r in rows if r["symbol"] in pool]
    if hide_gated:
        picked = [r for r in picked if r["gate"] == "clear"]
    metric_head = metric_cell(None, side)[0]

    with ui.column().classes(f"{T.PANEL} w-full gap-[14px] min-w-0 pb-3"):
        with ui.row().classes("items-baseline gap-3 flex-wrap"):
            ui.element("div").classes(f"w-[3px] h-[15px] rounded-[2px] {accent}")
            ui.label(head["title"]).classes(
                "text-[15px] font-bold tracking-[-0.01em] text-[#f2f6fc]")
        if head["note"]:
            with ui.row().classes(f"{T.CALLOUT} w-full"):
                ui.label("⚠").classes("text-[13px] text-[#fbbf24]")
                ui.label(head["note"]).classes(T.CALLOUT_TEXT)

        with ui.element("div").classes(T.SCROLL_X):
            with ui.column().classes(f"{_TABLE_MIN} gap-0"):
                with ui.element("div").classes(
                        f"grid {_COLS} gap-x-3 px-[6px] pb-[9px] {T.RULE} "
                        "text-[9.5px] font-bold tracking-[0.13em] "
                        "text-[#56678a]"):
                    for i, h in enumerate(_HEAD):
                        label = metric_head if h is None else h
                        with ui.label(label).classes(
                                "text-right" if 1 <= i <= 5 else ""):
                            sh.tip(th.column_help(label))
                if not picked:
                    ui.label("No candidates on this side today.").classes(
                        f"{T.NOTE} pt-3")
                for r in picked:
                    _row(r, side)


def _row(r, side):
    with ui.element("div").classes(
            f"grid {_COLS} gap-x-3 items-center px-[6px] py-[11px] "
            f"{T.HAIRLINE}"):
        ui.label(r["symbol"]).classes(
            f"{T.MONO} text-[13.5px] font-bold text-[#f2f6fc]")
        ui.label(r["pctl"]).classes(f"{T.VALUE} text-right")
        ui.label(r["score"]).classes(
            f"{T.MONO} text-[12.5px] text-right {r['score_class']}")
        ui.label(r["exp"]).classes(
            f"{T.MONO} text-[12.5px] text-right {r['exp_class']}")
        ui.label(r["hit"]).classes(
            f"{T.MONO} text-[12.5px] text-right text-[#8b9bb4]")
        ui.label(metric_cell(r, side)[1]).classes(
            f"{T.MONO} text-[12.5px] text-right text-[#a8b6cf]")
        ui.label(r["dealer"]).classes(
            f"text-[12px] whitespace-nowrap {r['dealer_class']}")
        with ui.row().classes("items-baseline gap-[7px] min-w-0"):
            ui.label(r["iv"]).classes(f"{T.MONO} text-[12.5px] text-[#cfdaee]")
            if r["iv_state"]:
                ui.label("· " + r["iv_state"]).classes(
                    f"text-[11.5px] {r['iv_class']}")
        ui.label(r["gate"]).classes(
            f"{T.CHIP_BASE} {r['gate_chip']} justify-self-start "
            "text-[11.5px] font-semibold tracking-normal px-[10px] py-[3px]")


_BOOK_COLS = "w-full [grid-template-columns:84px_70px_96px_100px_78px_1fr]"


def _book_table(rows):
    with ui.element("div").classes(T.SCROLL_X):
        with ui.column().classes("min-w-[520px] gap-0"):
            with ui.element("div").classes(
                    f"grid {_BOOK_COLS} gap-x-3 px-[6px] pb-[9px] {T.RULE} "
                    "text-[9.5px] font-bold tracking-[0.13em] text-[#56678a]"):
                for h in ("SYMBOL", "SIDE", "AS", "OPENED", "P&L", "STATUS"):
                    with ui.label(h):
                        sh.tip(th.column_help(h))
            if not rows:
                ui.label("The book opens positions from the pools above.") \
                    .classes(f"{T.NOTE} pt-3")
            for r in rows:
                with ui.element("div").classes(
                        f"grid {_BOOK_COLS} gap-x-3 items-center px-[6px] "
                        f"py-[9px] {T.HAIRLINE}"):
                    ui.label(r["symbol"]).classes(
                        f"{T.MONO} text-[13px] font-bold text-[#f2f6fc]")
                    ui.label(r["side"]).classes("text-[12px] text-[#a8b6cf]")
                    ui.label(r["expression"]).classes(
                        "text-[12px] text-[#7d8db0]")
                    ui.label(r["opened_on"]).classes(
                        f"{T.MONO} text-[12px] text-[#a8b6cf]")
                    ui.label(r["pnl"]).classes(
                        f"{T.MONO} text-[12.5px] {r['pnl_class']}")
                    ui.label(r["status"]).classes("text-[12px] text-[#7d8db0]")
