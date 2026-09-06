"""Shares — the equity lots the paper account holds.

A second READER of ``cache:options:paper_account``, not a second book. Put
assignment converts a cash-secured put into stock, and the lots ride the account
view the Paper Account page already reads, so one database has one publish
cadence and a lot cannot exist on one screen and not the other.

Pure builders (rows / columns / status line / the covering-call lookup) are
module-level and NiceGUI-free so they are unit-tested without a browser;
``render()`` mounts the table and version-polls the bus.

Tier-1: imports ONLY ``nicegui`` + ``bus_client`` + ``pages.*`` helpers. No
engine or service import, no direct database access, no Schwab call, and no path
glue into an app folder. (Spelled out in prose rather than with the module names
themselves — several guards in this suite grep page SOURCE for a banned token,
comments included, so naming one here would trip them.)

⚠ **There is no live equity quote in this payload, and the page says so.** The
account snapshot carries cash, reserved buying power and the OPTION book's
unrealized; nothing in it prices a bare share. Rendering cost basis in the Mark
column, or a 0.00 unrealized, would fabricate exactly the reading this page is
opened for — so both cells render an em-dash and the column header names the
reason. The builders DO read a ``mark`` off a lot if one is ever attached
upstream, so filling those columns later is a service change with no page edit.
"""
from __future__ import annotations

import bus_client
from pages import busy as _busy
from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import fmt as _fmt    # the ONE numeric vocabulary (pages/fmt.py)
from pages.view_watch import watch_view
from nicegui import run, ui

from pages.ui_guard import guard_async

from . import strategy_table as _st
from .theme import (CARD, EYEBROW, LABEL, PAGE, QUASAR_INTERNAL_CSS,
                    TXT_NEG, TXT_NEUTRAL, TXT_POS)

# The lots ride the EXISTING paper-account view. See the module docstring.
VIEW = "options:paper_account"


def _money(v) -> str:
    """A dollar amount with thousands separators, or a dash when unread.

    ``fmt.fixed`` is the two-decimal formatter everywhere else, but a share lot's
    cost runs to five and six figures where an unseparated ``119500.00`` is
    genuinely hard to read at a glance. Same absent-reading contract.
    """
    f = _fmt.num(v)
    return _fmt.NO_READING if f is None else f"{f:,.2f}"


# ── provenance ──────────────────────────────────────────────────────────────
# ``equity_lots.source`` is 'assignment' | 'manual'. The words below name the
# EVENT from the reader's side rather than echoing the column: a reader deciding
# whether to write a call against these shares needs to know they arrived because
# a short put finished in the money, not that a string says "assignment".
#
# Anything else — including an absent source — is a dash. Defaulting an unknown
# provenance to "Bought" would assert a fact the row does not carry, and the two
# are not interchangeable.
SOURCE_LABELS = {
    "assignment": "Assigned",
    "manual": "Bought",
}


def source_label(lot) -> str:
    """How these shares came to be held, or :data:`fmt.NO_READING`."""
    src = str((lot or {}).get("source") or "").strip().lower()
    return SOURCE_LABELS.get(src, _fmt.NO_READING)


# ── the covering call ───────────────────────────────────────────────────────
# The one structure in this book that is a call written against stock. Mirrors
# the engine's own covered-call identifier; restated rather than imported
# because Tier 1 takes no service import, and pinned by a test on both sides.
#
# ⚠ Matching on SYMBOL alone is the trap. An open call CREDIT SPREAD on the same
# symbol is not a call written against the shares — reporting it as one would
# tell a reader the position is covered when the shares are still naked to the
# upside, which is the single reading this column exists to give.
COVERED_CALL_STRATEGIES = ("COVERED_CALL",)


def covering_call(symbol, positions):
    """The open covered call on ``symbol``, or None.

    ⚠ Coverage is recorded per SYMBOL: the paper book has no link from a call
    back to the lot it was written against, so this cannot be resolved per lot.
    Showing the same call on every lot of the symbol is the honest rendering of
    what is stored — hiding it on all but one would imply those shares are
    uncovered, which the book does not say.
    """
    want = str(symbol or "").strip().upper()
    if not want:
        return None
    for pos in positions or []:
        pos = pos or {}
        if str(pos.get("symbol") or "").strip().upper() != want:
            continue
        if str(pos.get("strategy") or "").strip().upper() in COVERED_CALL_STRATEGIES:
            return pos
    return None


def covering_text(pos) -> str:
    """``'210c 10/16'`` for a covering call, with ``×N`` only when N > 1.

    A single contract is the default a covered call is written in, so stamping
    ``×1`` on every row would add noise to the common case and hide the one that
    matters — a lot covered more than once over.

    ⚠ The strike is read from ``call_short`` FIRST. ``short_strike`` holds the
    call strike for a call structure in this schema, but a row that carries both
    is naming the call side explicitly, and that is the field to believe.
    """
    if not pos:
        return _fmt.NO_READING
    strike = _fmt.num(pos.get("call_short"))
    if strike is None:
        strike = _fmt.num(pos.get("short_strike"))
    parts = []
    if strike is not None:
        parts.append(f"{strike:g}c")
    exp = _st._short_exp(pos.get("expiration"))
    if exp != _fmt.NO_READING:
        parts.append(exp)
    # A call IS open even when neither field reads — say so rather than dashing,
    # which would report the shares as uncovered.
    if not parts:
        return "Call open"
    text = " ".join(parts)
    qty = _fmt.num(pos.get("quantity"))
    if qty is not None and qty > 1:
        text = f"{text} ×{qty:.0f}"
    return text


# ── cells ───────────────────────────────────────────────────────────────────
# A fixed, finite palette for the unrealized cell. The three states are mapped
# from a computed sign through this dict — never a class string built at runtime.
UNREALIZED_CLASSES = {
    "up": TXT_POS,
    "down": TXT_NEG,
    # An absent reading is neither a gain nor a loss, and today it is EVERY row.
    "none": TXT_NEUTRAL,
}


def lot_cost(lot):
    """Capital committed to a lot at basis (``shares × cost_basis``), or None."""
    n = _fmt.num((lot or {}).get("shares"))
    basis = _fmt.num((lot or {}).get("cost_basis"))
    if n is None or basis is None:
        return None
    return n * basis


def unrealized(lot):
    """``(mark − basis) × shares``, or None when there is no mark to read.

    ``num`` on every operand, not ``float_or``: a NaN mark would otherwise reach
    a sign comparison, and every comparison against NaN is False, so it would
    paint as a loss on a page about money. Absence is the common case here — the
    published view carries no equity quote at all.
    """
    mark = _fmt.num((lot or {}).get("mark"))
    basis = _fmt.num((lot or {}).get("cost_basis"))
    n = _fmt.num((lot or {}).get("shares"))
    if mark is None or basis is None or n is None:
        return None
    return (mark - basis) * n


def _opened_text(lot) -> str:
    """The date part of ``opened_ts``. Kept as a full ISO date rather than the
    ``MM/DD`` the option tables use: a share lot has no expiry and can sit for
    years, where a bare month/day is genuinely ambiguous."""
    ts = str((lot or {}).get("opened_ts") or "")
    return ts[:10] if len(ts) >= 10 else _fmt.NO_READING


def lot_rows(lots, positions):
    """Display rows for the share inventory, in the order the book stored them.

    Deliberately not re-sorted: ``fetch_open_lots`` returns oldest first, which
    is the order the lots were acquired and the order a holding period reads in.
    The table's own headers stay click-sortable.
    """
    rows = []
    for i, lot in enumerate(lots or []):
        lot = lot or {}
        pnl = unrealized(lot)
        if pnl is None:
            tone = "none"
        else:
            tone = "up" if pnl > 0 else ("down" if pnl < 0 else "none")
        rows.append({
            # ``row_key`` is the table's identity — the index fallback keeps two
            # lots with no id from collapsing into one row.
            "id": lot.get("lot_id") if lot.get("lot_id") is not None else f"row{i}",
            "symbol": lot.get("symbol") or "",
            "shares": _fmt.fixed(lot.get("shares"), 0),
            "basis": _fmt.fixed(lot.get("cost_basis")),
            "cost": _money(lot_cost(lot)),
            "mark": _money(lot.get("mark")),
            "unrealized": _money(pnl),
            "_unrealized_class": UNREALIZED_CLASSES[tone],
            "source": source_label(lot),
            "opened": _opened_text(lot),
            "covering": covering_text(covering_call(lot.get("symbol"), positions)),
        })
    return rows


def share_columns():
    """``ui.table`` column defs. Each label names the READING, not the field.

    "Mark (not tracked)" is the load-bearing one. An unexplained column of
    em-dashes reads as a broken feed; naming the reason in the header puts the
    explanation where the reader is already looking, and the page help says it
    again at length.
    """
    spec = [
        ("symbol", "Symbol"), ("shares", "Shares"),
        ("basis", "Cost basis $/share"), ("cost", "Cost $"),
        ("mark", "Mark (not tracked)"), ("unrealized", "Unrealized $"),
        ("source", "How acquired"), ("opened", "Held since"),
        ("covering", "Covering call"),
    ]
    return [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]


# ── the status line ─────────────────────────────────────────────────────────
# FIVE states. Four of them are empty tables, and they are four different facts:
# a cold feed, an account never opened, a payload written before the inventory
# field existed, and a book that genuinely holds no stock. Wording any of them
# with the shared cold-feed sentence would report a healthy account as an outage.
NO_ACCOUNT = "No paper account yet — nothing has been opened in the book."
NO_SHARES = ("The paper account holds no shares. A cash-secured put that "
             "finishes in the money becomes a lot here.")
# ``lots`` absent means the inventory was never written — NOT that it is empty.
# Reading an absent key as zero is exactly the number-nobody-read this app
# refuses to print. It names no service, for the reason pages/copy.py gives.
NO_INVENTORY = "Share inventory has not been published yet."


def status_text(payload) -> str:
    """The line under the title — see the five states above."""
    p = payload or {}
    if not p:
        return _copy.WAITING_OPTIONS
    if p.get("has_account") is False:
        return NO_ACCOUNT
    if "lots" not in p:
        return NO_INVENTORY

    lots = p.get("lots") or []
    if not lots:
        return NO_SHARES

    n = len(lots)
    # Sum only what reads. A lot with an unreadable share count is left out of
    # the totals rather than counted as zero, and the lot count still shows it.
    total_shares = sum(s for s in (_fmt.num(l.get("shares")) for l in lots)
                       if s is not None)
    total_cost = sum(c for c in (lot_cost(l) for l in lots) if c is not None)
    return (f"{n} lot{'' if n == 1 else 's'} · {total_shares:,.0f} shares · "
            f"${total_cost:,.0f} at cost")


# The one coloured cell, binding the stamped ``_unrealized_class``. Tailwind-first
# — no inline style, and no Vue style binding (which the test_no_inline_style
# guard greps this source for, comments included).
_UNREALIZED_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._unrealized_class">{{ props.value }}</span>
  </q-td>
'''


def render():
    """Build the Shares page body: the open equity lots, repainting when
    ``cache:options:paper_account`` publishes.

    Read-only. Lots are created by assignment on the manage cycle and closed by
    the engine, so there is nothing to press here — the page's whole job is to
    say what stock the book owns and whether a call is already written on it.
    """
    ui.add_css(QUASAR_INTERNAL_CSS)
    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):
        with ui.column().classes(f"{CARD} w-full gap-2"):
            ui.label("Shares").classes(f"text-h6 {LABEL}")
            ui.label("Stock the paper account holds — mostly put assignments, "
                     "which is where covered calls come from. Shares are not "
                     "repriced here, so Mark and Unrealized stay blank rather "
                     "than showing a number nothing measured.").classes(EYEBROW)
            status = ui.label(_copy.WAITING_OPTIONS).classes(EYEBROW)
            table_box = ui.element("div").classes("w-full")
            with table_box:
                table = ui.table(columns=share_columns(), rows=[], row_key="id",
                                 pagination={"rowsPerPage": 0}) \
                    .classes("w-full").props("dense")
            table.add_slot("body-cell-unrealized", _UNREALIZED_SLOT)

    # Until the first payload lands, an empty grid is indistinguishable from an
    # account that holds no stock — which is the normal state of this page.
    board_busy = _busy.build_busy(table_box, "Loading the share inventory…")

    def _paint(payload):
        p = payload or {}
        table.rows = lot_rows(p.get("lots"), p.get("positions"))
        table.update()
        status.text = status_text(payload)
        board_busy.hide()

    @guard_async
    async def _reread():
        # The bus read is blocking; keep it off the event loop (house pattern).
        _paint(await run.io_bound(bus_client.read, VIEW))

    payload = bus_client.read(VIEW)
    if payload:
        _paint(payload)
    else:
        board_busy.show()
    # One line for the version-gated repaint idiom: seeds the current version, so
    # the first tick does not fire, and a cold view still fills in on first publish.
    watch_view(VIEW, _reread)
