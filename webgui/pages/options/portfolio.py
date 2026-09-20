"""Paper Portfolio page (Tier-3 reader).

This page holds **no engine/proxy/DB call**. The paper-account reads
(snapshot + open positions + fills) and the entry/manage/reset actions live in
``services/options_svc/compute`` + ``handlers``; the service writes the account
view under ``cache:options:paper_account`` and re-publishes it after every
action. This page only **reads** that payload and formats it, and enqueues
commands (``refresh_paper`` / ``paper_entry`` / ``paper_manage`` /
``paper_reset``) onto the Redis bus.

The cross-app ``scoring`` collision guard is gone (the service process loads no
sentiment code; the page does no engine call). A fetch-free version-poll
``ui.timer`` repaints when the bus cache version changes (graceful-empty when
the service is cold / no account exists yet).

Built on the page kit (``pages/ui_kit.py``, the 2026-09-19 consistency
standard): the header line carries the Updated stamp and the page actions
(Reset first, the primary manage cycle last), one region covers everything a
refresh replaces, and Reset asks before it clears the book.
"""
import bus_client
from pages.fmt import round_or_none as _round  # the ONE copy (pages/fmt.py)
from pages import ui_kit as kit
from nicegui import ui

from pages.ui_guard import guard

# ⚠ Imported as a MODULE, not by name: this page already has its own ``_money``
# (the unsigned account-card formatter — "Equity $24,184.20"), and a
# ``money as _money`` import was silently shadowed by it, printing the track
# record's realized P&L without its sign.
from pages import scorecard as _scorecard
from .perf_charts import equity_curve_figure, excursion_text
from .rescue import AT_RISK_STATES as _AT_RISK_STATES
# ``rescue_highlight`` was named TWICE on this line. ``heat_border_class`` is
# not called here and stays anyway: it is a deliberate re-export, and
# ``test_options_paper_portfolio`` reads it off this module to check that a row's
# tint IS the shared heat border rather than a second palette.
from .rescue import heat_border_class, rescue_highlight
from .theme import (BADGE_MUTED, BADGE_NEG, BADGE_POS, CARD, EYEBROW, LABEL,
                    MUTED)

# rescue_state values that mark a position at-risk. The options_svc manage cycle
# tags THIS view (cache:options:paper_account) with rescue_state/heat via
# handlers._apply_rescue_overlay, so the highlight is live here (unlike the
# paper-trades ledger / captured views, where the overlay is absent).

def _money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def account_cards(snapshot):
    """[(label, value), ...] for the account summary cards."""
    s = snapshot or {}
    return [
        ("Equity", _money(s.get("equity"))),
        ("Cash", _money(s.get("cash"))),
        ("BP Reserved", _money(s.get("buying_power_reserved"))),
        ("Session P&L", _money(s.get("session_pnl"))),
        ("Total P&L", _money(s.get("realized_pnl"))),
        ("Open", str(s.get("open_count", 0))),
        ("Engine", "HALTED" if s.get("halted") else "RUNNING"),
    ]


def position_columns():
    # "Entry", not "Credit": the engine's paper book holds directional DEBITS,
    # and a debit is stored as a negative credit - the same defect the Paper
    # Ledger and Captured Signals carried in their same-named column.
    #
    # "Mark", not "CurVal": a casual shortening AND the wrong idea. It is the
    # live price, which is what /desk and Captured Signals call Mark.
    #
    # "Open P&L": the field IS ``unrealized_pnl``, so these rows are open by
    # construction - the Captured Signals case, not the Paper Ledger one. The
    # "$" said nothing the number does not.
    spec = [
        ("position_id", "ID"), ("symbol", "Symbol"), ("strategy", "Strategy"),
        ("strikes", "Strikes"), ("expiration", "Expiry"),
        ("quantity", "Contracts"), ("entry_credit", "Entry"),
        ("current_value", "Mark"), ("unrealized_pnl", "Open P&L"),
        ("status", "Status"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def position_rows(positions):
    rows = []
    for p in positions or []:
        sk, lk = p.get("short_strike"), p.get("long_strike")
        rows.append({
            "id": p.get("position_id"),
            "position_id": p.get("position_id"),
            "symbol": p.get("symbol", ""),
            "strategy": p.get("strategy", ""),
            "strikes": f"{sk}/{lk}" if sk is not None else "—",
            "expiration": p.get("expiration", ""),
            "quantity": p.get("quantity"),
            "entry_credit": _round(p.get("entry_credit")),
            "current_value": _round(p.get("current_value")),
            "unrealized_pnl": _round(p.get("unrealized_pnl")),
            "status": p.get("status", ""),
            # At-risk rescue tint on the symbol cell (left border), fed from the
            # manage-cycle rescue overlay on this view. Safe no-op ('') when the
            # position is healthy / carries no rescue_state.
            "_rescue_class": rescue_highlight(p.get("rescue_state"), p.get("heat")),
        })
    return rows


def order_columns():
    spec = [
        ("order_id", "Order ID"), ("ts", "Time"), ("side", "Side"),
        ("symbol", "Symbol"), ("quantity", "Contracts"), ("order_type", "Type"),
        ("fill_price", "Fill price"), ("status", "Status"),
        ("reject_reason", "Reason"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def side_badge_class(side):
    """Deep Slate side-badge token: SELL → green pill, BUY → red pill (a credit
    seller reads SELL_TO_OPEN as the "good" side); anything else → muted."""
    s = (side or "").upper()
    if "SELL" in s:
        return BADGE_POS
    if "BUY" in s:
        return BADGE_NEG
    return BADGE_MUTED


def order_rows(orders):
    rows = []
    for o in orders or []:
        rows.append({
            "order_id": o.get("order_id"),
            "ts": (o.get("ts") or "")[:19],
            "side": o.get("side", ""),
            "_side_class": side_badge_class(o.get("side")),
            "symbol": o.get("symbol", ""),
            "quantity": o.get("quantity"),
            "order_type": o.get("order_type", ""),
            "fill_price": _round(o.get("fill_price")),
            "status": o.get("status", ""),
            "reject_reason": o.get("reject_reason") or "",
        })
    return rows



def scorecard_text(perf) -> str:
    """One-line track record for the manual book. PURE.

    ``'7 closed · 71.4% win · +$420.00 realized · 2.00 profit factor'``

    ⚠ **Empty until something has CLOSED.** A fresh or all-open book would
    otherwise read "0.0% win", which says the book loses rather than that it has
    no record yet - the same distinction the Desk's empty states are built around.

    ⚠ **An undefined profit factor is omitted, not printed.** ``None`` means "no
    losses yet" (gross win / gross loss is undefined), and an em-dash mid-sentence
    reads as a rendering fault; the driver page shows "—" because there it is a
    labelled chip, where the absence is legible.
    """
    p = perf or {}
    closed = p.get("closed") or 0
    if not closed:
        return ""
    bits = [f"{int(closed)} closed",
            f"{_scorecard.percent(p.get('win_rate'))} win",
            f"{_scorecard.money(p.get('realized_pnl'))} realized"]
    pf = p.get("profit_factor")
    if isinstance(pf, (int, float)) and not isinstance(pf, bool):
        bits.append(f"{float(pf):.2f} profit factor")
    return " · ".join(bits)



def greeks_text(greeks) -> str:
    """One-line book-level risk read. PURE. Gap assessment C4.

    ``'Net theta +$24.00 a day · long 1.44 delta · short 0.48 vega'``

    ⚠ **Theta is named in DOLLARS A DAY and delta as a DIRECTION**, and that
    asymmetry is deliberate. Theta is genuinely additive — it is what the book
    earns for a day passing. A cross-symbol delta TOTAL is not a hedge ratio:
    without a beta there is no sense in which a $970 MU delta and a $145 PG delta
    add up, so the line says which way the book leans and by how much, and never
    implies a share count to hedge with. The beta weighting the assessment calls
    "optional" is not shipped for exactly that reason — see the design doc.

    ⚠ **Blank when nothing is priced.** An unpriced book must not read as a flat
    one; zero delta is a real reading (a balanced condor) and says something
    quite different from "no readings yet".
    """
    g = greeks or {}
    theta, delta, vega = g.get("net_theta"), g.get("net_delta"), g.get("net_vega")
    if all(v is None for v in (theta, delta, vega)):
        return ""
    bits = []
    if isinstance(theta, (int, float)) and not isinstance(theta, bool):
        bits.append(f"Net theta {_scorecard.money(theta)} a day")
    if isinstance(delta, (int, float)) and not isinstance(delta, bool):
        lean = "flat" if delta == 0 else ("long" if delta > 0 else "short")
        bits.append(f"{lean} {abs(float(delta)):.2f} delta"
                    if delta else "delta flat")
    if isinstance(vega, (int, float)) and not isinstance(vega, bool):
        side = "flat" if vega == 0 else ("long" if vega > 0 else "short")
        bits.append(f"{side} {abs(float(vega)):.2f} vega")
    line = " · ".join(bits)
    # Say so when the sum covers only part of the book — a total that silently
    # omits positions is worse than one that admits the gap.
    priced, total = g.get("positions_priced"), g.get("positions_total")
    if (isinstance(priced, int) and isinstance(total, int)
            and 0 < priced < total):
        line += f" (priced {priced} of {total})"
    return line


def render():
    """Paper Account: the header line's actions, the account cards, the open
    positions and fills, and the book's own analytics (bus-fed)."""
    with kit.page():
        head = kit.header("Paper Account", view="options:paper_account")
        with head.actions:
            # Danger first, primary last: Reset clears the whole book, and the
            # manage cycle is the one action this page is usually opened for.
            reset_btn = kit.button(
                "Reset", kind="danger", icon="restart_alt",
                tooltip="Reset the paper account to a starting balance.",
                on_click=lambda: _reset())
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh",
                                     on_click=lambda: _reload())
            entry_btn = kit.button(
                "Run entry cycle", kind="secondary", icon="login",
                tooltip="Scan open captured signals now and open paper positions "
                        "for the eligible ones.",
                on_click=lambda: _cycle("entry"))
            # ⚠ HOURLY, and this tooltip claimed a five-minute cadence for
            # months. The manual account rides
            # ``options_svc.scheduler.paper_cycle_due`` — the top of each hour,
            # 09:00-14:00 CT on trading days, with no 15:00 run; the 1-minute
            # ``manage_due`` slot belongs to the isolated DRIVER account, not to
            # this book. A tooltip that overstates a cadence is worse than none:
            # it says a target hit at 09:15 is acted on within minutes, where it
            # really waits for 10:00.
            manage_btn = kit.button(
                "Run manage cycle", kind="primary", icon="manage_accounts",
                tooltip="Reprice open positions and close any that hit their target "
                        "or stop. Runs automatically hourly, 09:00-14:00 CT on "
                        "trading days; this runs it now.",
                on_click=lambda: _cycle("manage"))
        status = kit.status_line()
        # Refresh / entry / manage / reset all round-trip through the service;
        # one region covers everything the answer repaints.
        account = kit.region("Refreshing the account…")
        with account.content:
            cards_box = ui.row().classes("gap-3 flex-wrap")
            # The book's own track record (gap assessment C5) and its Greeks
            # (C4), one line each: this page already carries the account cards,
            # the positions and the fills, and the driver page's full card
            # would bury them.
            scorecard_label = ui.label("").classes(f"text-xs {MUTED}")
            greeks_label = ui.label("").classes(f"text-xs {MUTED}")
            kit.section_title("Open positions")
            pos_table = kit.table(position_columns(), numeric=(
                "quantity", "entry_credit", "current_value", "unrealized_pnl"))
            kit.section_title("Fills log (last 100)")
            ord_table = kit.table(order_columns(), row_key="order_id",
                                  numeric=("quantity", "fill_price"))

        # ── Analytics: realized equity curve + MAE/MFE (scanner-baseline) ────
        # Same builders as the driver monitor, so this book (auto-trades every
        # captured signal) reads directly against the driver book (Claude's
        # selection) — the benchmark that shows whether the decider adds edge
        # over the raw scanner.
        kit.section_title("Analytics")
        ui.label("Realized equity curve, and how far trades ran for and against "
                 "before closing (MAE/MFE), for the manual (scanner-baseline) "
                 "book. Compare with Claude Trades' Analytics.").classes(
                     f"text-xs {MUTED}")
        equity_chart = ui.highchart(equity_curve_figure([])).classes("w-full")
        excursion_label = ui.label("").classes(f"text-xs {MUTED}")
        analytics_empty = ui.label("No closed paper trades yet — analytics populate "
                                   "as positions close.").classes(f"text-xs {MUTED}")

    # Symbol cell gets a colored left-border + faint tint when the position is
    # at-risk (rescue_state tested/critical, from the manage-cycle overlay).
    pos_table.add_slot('body-cell-symbol', r'''
      <q-td :props="props">
        <span v-if="props.row._rescue_class" :class="props.row._rescue_class + ' pl-1.5'">
          {{ props.value }}
        </span>
        <span v-else>{{ props.value }}</span>
      </q-td>
    ''')
    # Side as a Deep Slate pill (SELL green / BUY red).
    ord_table.add_slot('body-cell-side', r'''
      <q-td :props="props">
        <q-badge :class="props.row._side_class" :label="props.value"/>
      </q-td>
    ''')

    # Built once at the page's own level: a dialog built inside a repainted
    # container dies with its slot. The balance field lives in its content.
    reset_dlg = kit.confirm("Reset the paper account?",
                            "Every position and fill is cleared, and the account "
                            "starts again at this balance.",
                            confirm_text="Reset", danger=True,
                            on_confirm=lambda: _confirm_reset())
    with reset_dlg.content:
        balance = kit.number_field("Starting balance", value=25000.0, min=1,
                                   format="%.2f", width="w-40")

    @guard
    def _populate_analytics(a):
        a = a or {}
        curve = a.get("equity_curve") or []
        equity_chart.options = equity_curve_figure(curve)
        equity_chart.update()
        excursion_label.text = excursion_text(a.get("excursions"))
        analytics_empty.set_visibility(not (curve or excursion_label.text))

    # Last-seen bus cache versions for the fetch-free repaint timers.
    seen = {"version": None, "analytics": None}

    def _populate(pa):
        """Paint the cards + tables from the cached paper-account view."""
        account.busy.hide()
        for b in (refresh_btn, entry_btn, manage_btn, reset_btn):
            kit.set_busy(b, False)
        pa = pa or {}
        snap = pa.get("snapshot")
        has_account = pa.get("has_account")
        cards_box.clear()
        with cards_box:
            if not pa or snap is None or has_account is False:
                kit.empty("No paper account yet. Use Reset to start one.")
            else:
                for label, value in account_cards(snap):
                    with ui.column().classes(f"{CARD} min-w-[110px] gap-0 py-2"):
                        ui.label(label).classes(EYEBROW)
                        ui.label(value).classes(f"text-base font-semibold {LABEL}")
        scorecard_label.text = scorecard_text(pa.get("perf"))
        greeks_label.text = greeks_text(pa.get("greeks"))
        pos_table.rows = position_rows(pa.get("positions"))
        ord_table.rows = order_rows(pa.get("orders"))
        pos_table.update()
        ord_table.update()
        status.text = "" if not pa else (
            f"{len(pos_table.rows)} open positions · {len(ord_table.rows)} fills")

    # All four actions wait the same way, and none of them toasts: the region's
    # spinner already names what is running, and the button that started it
    # stays disabled until ``_populate`` releases it. A toast reports an
    # OUTCOME, so a sentence promising the account will repaint once the work
    # is done says only what the spinner beside it is already saying.
    @guard
    def _reload():
        bus_client.request("options", {"type": "refresh_paper"})
        account.busy.show("Refreshing the account…")
        kit.set_busy(refresh_btn)

    @guard
    def _cycle(kind):
        cmd = "paper_entry" if kind == "entry" else "paper_manage"
        bus_client.request("options", {"type": cmd})
        account.busy.show(f"Running the {kind} cycle…")
        kit.set_busy(manage_btn if kind == "manage" else entry_btn)

    @guard
    def _reset():
        reset_dlg.open()

    def _confirm_reset():
        # A bad balance keeps the dialog open with its message under the field,
        # rather than closing on a value the service would refuse.
        if not balance.validate():
            return False
        bus_client.request("options", {"type": "paper_reset",
                                       "args": {"starting_balance": float(balance.value)}})
        account.busy.show("Resetting the account…")
        kit.set_busy(reset_btn)

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["version"] = bus_client.read_version("options:paper_account")
    _populate(bus_client.read("options:paper_account") or {})
    seen["analytics"] = bus_client.read_version("options:paper_analytics")
    _populate_analytics(bus_client.read("options:paper_analytics") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps it after every
        # paper action (refresh/entry/manage/reset).
        version = bus_client.read_version("options:paper_account")
        if version != seen["version"]:
            seen["version"] = version
            _populate(bus_client.read("options:paper_account") or {})
        av = bus_client.read_version("options:paper_analytics")
        if av != seen["analytics"]:
            seen["analytics"] = av
            _populate_analytics(bus_client.read("options:paper_analytics") or {})

    ui.timer(2.0, _maybe_repaint)
