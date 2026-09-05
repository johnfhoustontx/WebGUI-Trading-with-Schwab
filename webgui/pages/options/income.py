"""Income Window — Tier-1 reader of ``cache:options:income``.

The 30-45 DTE premium-selling board: put credit spreads, call credit spreads and
the cash-secured put, jointly ranked across the whole watchlist by
``handlers.publish_income``. Pure builders (columns / rows / status line) are
module-level and NiceGUI-free so they are unit-tested without a browser;
``render()`` mounts the table and version-polls the bus.

Tier-1: imports ONLY ``nicegui`` + ``bus_client`` + ``pages.*`` helpers. No
engine or service import, no direct database access, no Schwab call, and no
path glue into an app folder. (Spelled out in prose rather than with the module
names themselves — several guards in this suite grep page SOURCE for a banned
token, comments included, so naming one here would trip them.)

⚠ **The rows are HETEROGENEOUS and this module must not assume one shape.** An
adapted credit spread carries BOTH the flat ``short_strike``/``credit``/``rr_pct``
contract AND the normalized one; the natively-built ``SHORT_PUT`` carries only
the normalized shape. Every cell here therefore reads a field BOTH shapes have —
``legs`` / ``net_credit`` / ``capital`` / ``max_profit`` / ``breakevens`` — which
is what ``_normalize_credit`` and ``payoff_metrics`` agree on. Reaching for
``short_strike`` would render the two spreads and silently blank the single. See
the ``IncomeScan`` and ``ScanResult`` docstrings.

⚠ **Units.** ``credit`` on an adapted spread is PER-SHARE (0.60) while
``net_credit`` is PER-CONTRACT (60.00); the single has only the latter. Putting a
$0.60 row beside a $640 row on one board is exactly the mistake the per-share
field invites, so this page reads ``net_credit`` everywhere.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import bus_client
from pages import busy as _busy
from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import fmt as _fmt    # the ONE numeric vocabulary (pages/fmt.py)
from pages.view_watch import watch_view
from nicegui import run, ui

from pages.ui_guard import guard_async

from . import scanner as _scanner
from . import strategy_table as _st
from .theme import CARD, EYEBROW, LABEL, PAGE, QUASAR_INTERNAL_CSS, TXT_NEUTRAL, TXT_POS

VIEW = "options:income"

# The service stamps ``ts`` in CENTRAL time (tz-aware), unlike the matrix view's
# UTC — see publish_income. Parse the offset it carries rather than assuming
# either, and only fall back to CT for a naive stamp.
_CT_TZ = ZoneInfo("America/Chicago")


# ── the side a reader picks on ──────────────────────────────────────────────
# Engine vocabulary ("PCS") names the builder; a board a human trades off names
# the position. Three known structures -> three fixed labels; anything else falls
# through as its raw type rather than being mislabelled as one of the three.
SIDE_LABELS = {
    "PCS": "Put spread",
    "CCS": "Call spread",
    "SHORT_PUT": "Cash-secured put",
}


def side_label(row) -> str:
    """The tradeable name of a candidate's structure."""
    kind = (row or {}).get("type") or ""
    return SIDE_LABELS.get(kind, str(kind))


# ── the earnings check ──────────────────────────────────────────────────────
# ``shared.earnings.coverage``'s three-state vocabulary, rendered as three states
# rather than reduced to a boolean. A row that SURVIVED the gate and carries
# ``upcoming`` has its report AFTER this expiration (``check_earnings_conflict``
# drops the straddling ones), so "After expiry" is a statement of fact, not a
# warning.
#
# ⚠ ``not_listed`` is the COMMON case, not an edge one: most checkouts have no
# Alpha Vantage key, so every row reads it. It must say "unknown" — the check did
# not run — without reading as an alarm, because a board painted entirely amber
# trains the reader to ignore the column. Hence the neutral class.
EARNINGS_LABEL = {
    "upcoming": "After expiry",
    "none_scheduled": "None scheduled",
    "not_listed": "Not checked",
}
EARNINGS_CLASS = {
    "upcoming": TXT_POS,
    "none_scheduled": TXT_POS,
    "not_listed": TXT_NEUTRAL,
}
# Absence of the stamp is absence of the check — never the cleared label. That
# fail-open shape is precisely what the three-state vocabulary exists to refuse.
_EARNINGS_UNKNOWN = (_fmt.NO_READING, TXT_NEUTRAL)


def earnings_display(status):
    """``(label, Tailwind class)`` for an ``earnings_status`` value."""
    if status in EARNINGS_LABEL:
        return EARNINGS_LABEL[status], EARNINGS_CLASS[status]
    return _EARNINGS_UNKNOWN


# ── cells ───────────────────────────────────────────────────────────────────

def return_on_capital(row):
    """``max_profit / capital`` as a percent, or None when either is unread.

    This is the column that makes the board comparable at all: a $60 credit
    against $441 of spread risk and a $640 credit against $39,361 of collateral
    are not comparable as dollars. ``num`` (not ``float_or``) on both, because
    a NaN capital would otherwise pass ``> 0`` — every comparison against NaN is
    False, so ``capital > 0`` correctly rejects it, but ``max_profit / nan`` is
    the reachable half — and a zero capital must not divide.
    """
    profit = _fmt.num((row or {}).get("max_profit"))
    capital = _fmt.num((row or {}).get("capital"))
    if profit is None or capital is None or capital <= 0:
        return None
    return profit / capital * 100.0


def _roc_text(row) -> str:
    pct = return_on_capital(row)
    return _fmt.NO_READING if pct is None else f"{pct:.1f}%"


def _dte_text(row) -> str:
    """DTE as a whole number. An absent horizon is a dash — this window IS a
    horizon, so a blank one is a fact worth showing rather than a 0."""
    dte = _fmt.num((row or {}).get("dte"))
    return _fmt.NO_READING if dte is None else f"{dte:.0f}"


def _expiry_text(row) -> str:
    exp = (row or {}).get("expiration")
    return _st._short_exp(exp) if exp else _fmt.NO_READING


def candidate_rows(candidates):
    """Display rows for the income board, in the order the service ranked them.

    **Deliberately not re-sorted.** ``handlers._income_rank`` already orders the
    merged board by composite score with an absent/NaN score pinned LAST, and
    says why: a non-reading must never sort to the top of a board a human picks a
    trade from. Re-sorting here would duplicate that rule at best and quietly
    contradict it at worst; the table's own column headers stay click-sortable.
    """
    rows = []
    for c in candidates or []:
        c = c or {}
        earn_label, earn_class = earnings_display(c.get("earnings_status"))
        score = c.get("composite_score")
        rows.append({
            "id": c.get("id"),
            "symbol": c.get("symbol") or "",
            "side": side_label(c),
            # From ``legs``, the ONLY strike source both shapes carry.
            "legs": _st.legs_summary(c.get("legs")),
            "expiration": _expiry_text(c),
            "dte": _dte_text(c),
            # Per-CONTRACT dollars on every row — see the module docstring.
            "credit": _fmt.fixed(c.get("net_credit")),
            "capital": _fmt.fixed(c.get("capital")),
            "roc": _roc_text(c),
            "pop": _fmt.fixed(c.get("pop_pct"), 1),
            "breakeven": _st.breakeven_text(c),
            "earnings": earn_label,
            "_earnings_class": earn_class,
            "score": score,
            "_score_class": _scanner.score_zone_class(score),
        })
    return rows


def income_columns():
    """``ui.table`` column defs. Each label names the READING, not the field.

    "Capital" is the cash actually committed — for a defined-risk spread that is
    its max loss, for a cash-secured put the full collateral — so the two shapes
    share one honest column instead of a "Max loss" that means different things
    on adjacent rows.
    """
    spec = [
        ("symbol", "Symbol"), ("side", "Side"), ("legs", "Strikes"),
        ("expiration", "Expiry"), ("dte", "DTE"),
        ("credit", "Credit $"), ("capital", "Capital $"),
        ("roc", "Return on capital"), ("pop", "PoP %"),
        ("breakeven", "Breakeven"), ("earnings", "Earnings"),
        ("score", "Score"),
    ]
    return [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]


def _short_ts(iso) -> str:
    """A tz-aware ISO stamp -> a short Central clock like '8:35 AM'; '' on failure.

    An unparseable stamp yields '' rather than the raw string: the status line is
    a sentence, and a stray ISO fragment in it reads as a fault.
    """
    if not iso:
        return ""
    try:
        dt = _dt.datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_CT_TZ)
    return dt.astimezone(_CT_TZ).strftime("%I:%M %p").lstrip("0")


def status_text(payload) -> str:
    """The status line — THREE states, not two.

    ⚠ ``WAITING_OPTIONS`` is for a feed that has published NOTHING. A pass that
    ran and found nothing is a healthy once-daily scan of a market with no
    qualifying premium in it, and wording the two the same would report that as
    an outage. So the empty-board line names what was actually scanned, which is
    also the only way a reader can tell "23 symbols, none qualified" from "the
    watchlist failed to load".
    """
    p = payload or {}
    if not p:
        return _copy.WAITING_OPTIONS

    n = len(p.get("candidates") or [])
    scanned = _fmt.num(p.get("scanned_symbols"))
    # ``scanned_symbols`` is what was ATTEMPTED (publish_income's own words), so
    # it is the honest denominator even when most symbols errored.
    scanned_txt = ("across {:.0f} symbol{}".format(scanned, "" if scanned == 1 else "s")
                   if scanned is not None else "")

    if n:
        parts = [f"{n} candidate" + ("" if n == 1 else "s")]
    else:
        # Never a bare "0 candidates" — that reads as a broken scan.
        parts = ["Nothing cleared the 30-45 day income window"]
    if scanned_txt:
        parts[0] = f"{parts[0]} {scanned_txt}"

    ts = _short_ts(p.get("ts"))
    if ts:
        parts.append(f"scanned {ts}")
    # A whole-watchlist outage would otherwise look exactly like a quiet tape.
    n_err = len(p.get("errors") or [])
    if n_err:
        parts.append(f"{n_err} symbol{'' if n_err == 1 else 's'} failed")
    return " · ".join(parts)


# ── colored-cell slots: each binds a stamped ``_*_class`` field via ``:class``.
# Tailwind-first — no inline style, and no Vue style binding (which the
# test_no_inline_style guard greps this source for, comments included).
_EARNINGS_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._earnings_class">{{ props.value }}</span>
  </q-td>
'''
_SCORE_SLOT = r'''
  <q-td :props="props">
    <q-badge :class="props.row._score_class + ' text-[#111]'" :label="props.value ?? '—'"/>
  </q-td>
'''


def render():
    """Build the Income page body: the ranked 30-45 DTE board, repainting when
    ``cache:options:income`` publishes.

    Read-only. The scan is a once-daily service slot, so there is no button here
    — the page's whole job is to present this morning's board and say when it
    was taken.
    """
    ui.add_css(QUASAR_INTERNAL_CSS)
    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):
        with ui.column().classes(f"{CARD} w-full gap-2"):
            ui.label("Income Window").classes(f"text-h6 {LABEL}")
            ui.label("Premium to sell 30 to 45 days out — put and call credit "
                     "spreads plus cash-secured puts, ranked across the whole "
                     "watchlist. Scanned once each morning. Click any column to "
                     "re-sort.").classes(EYEBROW)
            status = ui.label(_copy.WAITING_OPTIONS).classes(EYEBROW)
            table_box = ui.element("div").classes("w-full")
            with table_box:
                table = ui.table(columns=income_columns(), rows=[], row_key="id",
                                 pagination={"rowsPerPage": 0}) \
                    .classes("w-full").props("dense")
            table.add_slot("body-cell-earnings", _EARNINGS_SLOT)
            table.add_slot("body-cell-score", _SCORE_SLOT)

    # Until the first payload lands, an empty grid is indistinguishable from
    # "nothing qualified today" — which is a real and common outcome here.
    board_busy = _busy.build_busy(table_box, "Loading the board…")

    def _paint(payload):
        table.rows = candidate_rows((payload or {}).get("candidates"))
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
