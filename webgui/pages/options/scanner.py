"""Options scanner page (0-4 DTE / 5-15 DTE / Directional).

Tier-3 reader: this page holds **no engine call, no in-process result cache, and
no auto-scan loop**. The scan engine (``scanner_engine.run_full_scan``), its
scheduling (08:00–15:15 CT cadence), and the cross-app ``scoring`` collision
guard all live in ``services/options_svc`` — the only process that imports the
options engine. The service runs the scan and writes the result to the Redis bus;
this page only **reads** those payloads, **formats** them into the three signal
tables, and **enqueues a rescan command** on the bus.

Two cache views are read, and the split is deliberate:

* ``options:scan_day`` — the **day's union** of signals, and what the TABLES
  render. Each entry carries ``live`` (still qualifying on the latest scan) +
  ``stale_since`` (when it dropped out), so the day's signals stay visible all
  session with the dropped-out ones dimmed and frozen at their last numbers.
  The envelope is ``{date, signals_0dte, signals_swing, signals_directional,
  truncated?}``. **``date`` is a GATE, not decoration** — see ``day_is_today``.
* ``options:scan`` — the LIVE, last-scan-only view (``ScanResult.model_dump()``),
  read ONLY for the bottom status bar's scan timestamp / errors / warnings, which
  the day union does not carry. It stays live-only because the autonomous driver
  reads it and must never be offered a signal that no longer qualifies.

The pure display transforms (``signal_columns``, ``signal_rows``,
``directional_columns``, ``directional_rows``, ``stamp_stale``,
``stamp_persistence``, ``day_is_today``,
``day_signals``, ``day_note``, ``unseen_ids``/``acknowledge_ids``/
``new_ids_for_paint``, ``status_line``, ``_round``) are unit-tested. ``render()``
wires the two-pane widgets (tables + shared Trade detail panel), a small "Run
scan" button that enqueues a ``cmd:options`` command, a slim bottom status bar,
and a version-poll ``ui.timer`` that repaints when either bus cache version
changes. The poll itself is fetch-free (cheap ``:ver`` ints, on-loop); the day
union is ~4.5 MB by day's end, so BOTH payload reads — the deferred first paint
and the on-change repaint — go through ``run.io_bound``, sharing one in-flight
guard so they cannot stack (the ``gamma.py`` precedent).

Built on the page kit (``pages/ui_kit.py``, the 2026-09-19 consistency
standard): the header line carries the Updated stamp with Why no trade? and Run
scan, the status line under it counts signals and drops the clock it used to
open with, and the selected row's Calculator / Paper trade / Expected Move
buttons live in the detail panel's footer instead of a per-row icon column.
``SCAN_CSS`` stays — this table's column count still needs its compact padding.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import bus_client
from pages.fmt import round_or_none as _round  # the ONE copy (pages/fmt.py)
from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import ui_kit as kit
import page_help as _page_help
from nicegui import run, ui

from pages.ui_guard import guard, guard_async

from . import detail, funnel_view, handoff
from . import persistence as _persistence
from .checks_table import (  # re-exported: the scanner's names predate the move
    CHECKS_SLOT as _CHECKS_SLOT, ONLY_CLEAR_TIP as _ONLY_CLEAR_TIP, filtered_tab_label,
    only_clear, only_clear_empty_label, restamp, stamp_checks)
from .theme import (BADGE_MUTED, BADGE_WARN, CARD, EYEBROW, LABEL, MUTED,
                    TXT_NEG, TXT_NEUTRAL, TXT_POS, TXT_WARN)


def iv_rank_value(value):
    """Vol Rank cell value: the rank rounded to a whole number (0-100) for a clean,
    numerically-sortable cell, or ``None`` (blank) when it's missing/non-numeric."""
    return round(value) if isinstance(value, (int, float)) else None


# Quality zones for the composite score (match the speedometer in svg.py /
# the colors in detail.py): <40 RED, <55 AMBER, <75 BLUE, else GREEN.
RED, AMBER, BLUE, GREEN = "#ef5350", "#ffa726", "#42a5f5", "#66bb6a"


def score_zone_color(score):
    """Hex color for a composite score by quality zone (None -> grey)."""
    if score is None:
        return "#666666"
    if score < 40:
        return RED
    if score < 55:
        return AMBER
    if score < 75:
        return BLUE
    return GREEN


def score_zone_class(score):
    """Tailwind ``bg-[<hex>]`` class for a composite score by quality zone (same
    thresholds as ``score_zone_color``; None -> grey). Stamped on each row so the
    score-badge slot binds it via ``:class`` (Tailwind-only, no inline style)."""
    return f"bg-[{score_zone_color(score)}]"


def _short_exp(exp):
    """Compact an ISO expiration 'YYYY-MM-DD' -> 'MM/DD' (the DTE column already
    shows the day count). Returns the value unchanged if not a parseable ISO date."""
    s = str(exp or "")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[5:7]}/{s[8:10]}"
    return s


def _fmt_k(v):
    """Strike -> compact string: drop a trailing '.0' on whole numbers (1085.0 ->
    '1085'), keep fractional strikes (1085.5), '?' when missing."""
    if v is None:
        return "?"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _strikes_text(s):
    """One compact 'Strikes' cell. Iron condors show both put + call pairs; other
    spreads show short/long. '—' when strikes are absent."""
    if (s.get("type") == "IC") or (s.get("call_short") is not None):
        return (f"P{_fmt_k(s.get('short_strike'))}/{_fmt_k(s.get('long_strike'))} "
                f"C{_fmt_k(s.get('call_short'))}/{_fmt_k(s.get('call_long'))}")
    sk, lk = s.get("short_strike"), s.get("long_strike")
    return f"{_fmt_k(sk)}/{_fmt_k(lk)}" if sk is not None else "—"


def _col(field, label, sortable=True):
    return {"name": field, "label": label, "field": field, "sortable": sortable,
            "align": "left"}


# The checklist's verdict sorts alphabetically ("1 caution" before "Blocked"
# before "Clear"), which is an order in nothing the reader cares about - so the
# column does not sort, as the Strategy Finder's has not since it was built.
def _checks_col():
    return _col("checks", "Checks", sortable=False)


# The per-row ``actions`` column went with the 2026-09-19 page-kit migration:
# Calculator / Paper trade / Expected Move act on the SELECTED row from the
# detail panel's footer, so nothing acts on an unselected row and the icon
# column's width goes back to the data.

# When a signal dropped out of the live scan. Only ever populated on a stale row.
# "Dropped at", not "Dropped": the cell holds ``stale_since``, a TIMESTAMP -
# WHEN the signal stopped appearing in a scan, not whether it did.
_DROPPED_COL = ("stale_since", "Dropped at")

# How long the SETUP has been live today, and which way its score is going.
# "Seen since" carries a time + a count, so it is not the bare "Age" a reader
# would take for a DTE. "Score trend" and not "Score": the composite already
# owns that word, and these are different quantities.
_SEEN_COL = ("seen_since", "Seen since")
_TREND_COL = ("score_trend", "Score trend")


def signal_columns():
    """ui.table column defs for a credit-spread signal table (0-DTE / Swing).

    Short + Long are merged into one compact 'Strikes' column and the expiration
    is shown MM/DD so the right-hand columns (Score/Grade) fit."""
    # WARNING: "Credit" is CORRECT here, and this is the one somebody will
    # "fix". The Paper Ledger and Captured Signals both had a wrong column of
    # that name, because those books mix credits and debits and a debit is
    # stored as a negative credit. This table is credit spreads BY
    # CONSTRUCTION - the Directional tab, which holds the debits, does not use
    # these columns at all (see ``directional_columns``, which deliberately
    # carries no credit or R:R economics).
    #
    # "DTE" likewise stays bare where Captured Signals had to say "DTE at
    # entry": that page's value is frozen at capture, this scan reruns every
    # 15 minutes. The two differ because the quantities do.
    spec = [
        ("symbol", "Symbol"),
        ("type", "Strategy"),          # PCS / CCS / IC - the structure
        ("expiration", "Expiry"),
        ("dte", "DTE"),
        ("strikes", "Strikes"),
        ("credit", "Credit"),
        ("max_loss", "Max loss"),
        ("rr_pct", "R/R %"),
        ("pop_pct", "PoP %"),
        # "Vol Rank", not "IV Rank" - the field ranks current ATM IV inside the
        # 52-week REALIZED-vol distribution. See detail.py's FACTOR_LABELS note.
        ("iv_rank", "Vol Rank"),
        ("composite_score", "Score"),
        ("grade", "Grade"),
        _SEEN_COL,
        _TREND_COL,
        _DROPPED_COL,
    ]
    cols = [_col(field, label) for field, label in spec]
    # The go / no-go checklist's one-chip verdict, after Grade (the two are both
    # verdicts) and BEFORE the lifecycle trio (Seen since / Score trend /
    # Dropped at), which must stay adjacent. By NAME, not by offset: the old
    # ``len(cols) - 1`` silently moved whenever a column was appended, and it
    # landed Checks inside the trio the moment these two arrived.
    cols.insert(next(i for i, c in enumerate(cols) if c["name"] == "seen_since"),
                _checks_col())
    return cols


def directional_columns():
    """ui.table column defs for the Directional tab (single-leg long/short
    calls+puts).

    REUSES the shared multi-strategy columns (``strategy_table.strategy_columns``,
    which already render this exact normalized shape for the Swing page) plus two
    Scanner-only columns:

    * **Symbol** — the Scanner's directional pass spans the WHOLE watchlist, while
      ``strategy_columns`` was built for the single-symbol Swing page (whose one
      symbol is in the input box) and therefore has no symbol column at all.
    * **Dropped** — the day union's stale marker (the Swing page has no day union,
      so this stays out of the shared columns rather than showing Swing an
      always-empty column).

    Deliberately carries NO premium credit-spread economics (credit / R:R %): a
    directional trade is scored by ``strategy_scoring``'s Fit+Quality model, which
    is not comparable to the premium ``scoring.py`` composite — that
    incommensurability is the whole reason this is a separate tab.
    """
    from . import strategy_table          # lazy: strategy_table imports scanner
    shared = strategy_table.strategy_columns()
    body = [c for c in shared if c["name"] != "actions"]
    return ([_col("symbol", "Symbol")] + body
            + [_checks_col(), _col(*_SEEN_COL), _col(*_TREND_COL),
               _col(*_DROPPED_COL)])


def signal_rows(signals):
    """Map engine signal dicts to display rows, sorted by score (desc).

    Robust to sparse signals — fields vary by trade type (PCS/CCS/IC). Each row
    keeps ``id`` so the detail panel can look up the raw engine signal.
    """
    rows = []
    for s in signals or []:
        rows.append({
            "id": s.get("id"),
            "symbol": s.get("symbol", ""),
            "type": s.get("type", ""),
            "expiration": _short_exp(s.get("expiration")),
            "dte": s.get("dte"),
            "strikes": _strikes_text(s),
            "credit": _round(s.get("credit")),
            "max_loss": _round(s.get("max_loss")),
            "rr_pct": _round(s.get("rr_pct"), 1),
            "pop_pct": _round(s.get("pop_pct"), 1),
            "iv_rank": iv_rank_value(s.get("iv_rank")),
            "composite_score": s.get("composite_score"),
            "_score_class": score_zone_class(s.get("composite_score")),
            "grade": s.get("grade", ""),
        })
    rows.sort(key=lambda r: (r["composite_score"] is not None, r["composite_score"] or 0),
              reverse=True)
    return rows


def directional_rows(signals):
    """Display rows for the Directional tab, sorted by Fit+Quality score (desc).

    REUSES ``strategy_table.strategy_rows`` — the normalized single-leg shape is
    exactly what it already renders for the Swing page, including the ``∞`` max-loss
    cell and the ``_undefined_risk`` / ``_allow_paper`` flags that keep a naked
    short (SHORT_CALL/SHORT_PUT — unlimited loss) badged and un-paper-tradeable.
    Adds only the Scanner-only ``symbol`` (see ``directional_columns``), joined by
    id since ``strategy_rows`` re-sorts.

    Two windows may emit the same ``type`` (a 0-DTE and a swing LONG_CALL): those
    are two distinct trades with different ids + expirations, disambiguated by the
    DTE column. Never deduped.
    """
    from . import strategy_table          # lazy: strategy_table imports scanner
    rows = strategy_table.strategy_rows(signals)
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for r in rows:
        r["symbol"] = (by_id.get(r.get("id")) or {}).get("symbol", "")
    return rows


def _sig_key(r):
    """Stable identity for a signal, across BOTH shapes this key is asked about.

    Two callers, two shapes: ``alerts.py`` passes RAW engine signals, this page
    passes DISPLAY rows. The engine's ``id`` is the only field present and unique
    in both — it is deterministic and content-derived (``SYMBOL_TYPE_EXP_STRIKES``,
    see ``scanner_engine`` / ``strategy_scanner._assemble``), so it is stable
    across scans, which is what a cross-scan identity needs.

    This USED to rebuild the key from ``short_strike``/``long_strike``, which is
    correct for a raw signal but silently wrong for a display row: ``signal_rows``
    merges both strikes into ONE ``strikes`` cell, so every row on a symbol/type/
    expiry collapsed onto ``'SPY|PCS|None|None|07/17'`` and a genuinely new signal
    at different strikes went unmarked whenever anything on that symbol/type/expiry
    was already present.

    The legs fallback is for an id-less signal (defensive only — the engine always
    assigns one, and ``merge_day_signals`` drops any signal without one); it keeps
    id-less raw signals from collapsing onto a single ``None`` key.
    """
    sid = r.get("id")
    if sid:
        return str(sid)
    return f'{r.get("symbol")}|{r.get("type")}|{r.get("short_strike")}|{r.get("long_strike")}|{r.get("expiration")}'


# ── "New" markers: unseen since you last VIEWED the page ─────────────────────
# Semantics changed deliberately. The old marker meant "wasn't in the PREVIOUS
# scan" and cleared on the next scan whether or not anyone looked — so stepping
# away for 30 minutes silently lost every marker. It now means **unseen since you
# last viewed the page**: ids are marked seen only when the page is actually built
# (``render``'s initial paint), never by a background version-poll repaint, so
# markers ACCUMULATE while you are away and are still there when you come back.
#
# Date-scoped + keyed on the engine's unique id, mirroring the day union's own
# reset contract (``compute.merge_day_signals``) so the marks and the rows they
# mark turn over on the same boundary.
#
# ACCEPTED CAVEAT: this is page-side module state (single-user, like _NAV_OPEN),
# so a **webgui restart re-marks everything New**. The day's SIGNALS survive a
# restart — they live in Redis — only the read-marks don't. Deliberate: pushing
# GUI read-state server-side is not worth the machinery for a single-user app.
_SEEN = {"date": None, "ids": set()}


def _reset_seen_state():
    """Clear the module-level seen-set (test seam / fresh session)."""
    _SEEN.update(date=None, ids=set())


def unseen_ids(current_ids, today):
    """Ids in ``current_ids`` not yet acknowledged for ``today``. Pure QUERY —
    does NOT mark anything seen (that is ``acknowledge_ids``' job, and keeping the
    two apart is what lets a paint snapshot the marks before clearing them).

    A cold state, or a date turnover, means nothing has been viewed today, so
    everything is unseen."""
    if _SEEN["date"] != today:
        return set(current_ids)
    return set(current_ids) - _SEEN["ids"]


def acknowledge_ids(current_ids, today):
    """Mark ``current_ids`` seen for ``today`` (resetting on a date change)."""
    if _SEEN["date"] != today:
        _SEEN.update(date=today, ids=set())
    _SEEN["ids"] |= set(current_ids)
    return _SEEN["ids"]


def new_ids_for_paint(current_ids, today, acknowledge):
    """Ids to badge "new" on this paint, acknowledging them when it is a real VIEW.

    ORDER IS LOAD-BEARING: the unseen set is snapshotted BEFORE acknowledging.
    Acknowledge first and the snapshot is always empty — nothing is ever New.
    Kept as one function so that ordering has exactly one home (and one test).

    ``acknowledge`` — True for ``render``'s initial paint (the user is looking at
    the page), False for a background version-poll repaint (they may not be).
    """
    unseen = unseen_ids(current_ids, today)
    if acknowledge:
        acknowledge_ids(current_ids, today)
    return unseen


def stamp_new(rows, new_ids):
    """Stamp each row with ``_new`` = (its signal identity is in ``new_ids``)."""
    for r in rows:
        r["_new"] = _sig_key(r) in new_ids
    return rows


# ── Day union (cache:options:scan_day) ───────────────────────────────────────
_CT = ZoneInfo("America/Chicago")

# The lists the day envelope carries, in tab order.
DAY_LISTS = ("signals_0dte", "signals_swing", "signals_directional")

# A dropped-out signal is frozen at its last numbers and must READ as inert.
STALE_ROW_CLASS = "opacity-50"


def today_ct():
    """Today's date (ISO) in CENTRAL time — the basis the day envelope is stamped
    with (``options_svc.handlers.rescan`` → ``shared.notify.channels._today_ct``).
    Local time would disagree for any non-CT user around midnight."""
    return dt.datetime.now(_CT).date().isoformat()


def day_is_today(env, today=None):
    """True when the day-union envelope is stamped with TODAY's CT date.

    **This is a GATE, not decoration.** The service's merge is best-effort and, on
    failure, leaves the key UNTOUCHED — so the failure mode is STALE, not absent.
    If it throws on the first scan of a new day, the key still holds YESTERDAY's
    envelope *including entries stamped ``live=True`` by yesterday's last scan*.
    Rendering that blind would present day-old signals as live and tradeable.
    """
    env = env or {}
    return bool(env) and env.get("date") == (today or today_ct())


def day_signals(env, key, today=None):
    """One of the day envelope's signal lists — but ONLY when the envelope is
    today's (see ``day_is_today``). Absent/malformed → ``[]``."""
    if not day_is_today(env, today):
        return []
    value = (env or {}).get(key)
    return value if isinstance(value, list) else []


def truncated_note(env):
    """Notice for the day cap having evicted signals; ``''`` when it hasn't.

    The union caps each list and evicts oldest-stale-first, reporting the count in
    an additive ``truncated`` block (ABSENT when nothing dropped — which is also
    what a pre-cap envelope looks like, so absence is unambiguously "no notice").
    The cap does not bind on a calm or mid-churn day, so when it fires it fires on
    a VOLATILE one — exactly the day a trader is watching. Silently showing a
    shorter day would break the feature's promise (no silent caps).
    """
    truncated = (env or {}).get("truncated") or {}
    total = sum(v for v in truncated.values() if isinstance(v, (int, float)))
    if not total:
        return ""
    total = int(total)
    noun = "signal" if total == 1 else "signals"
    return (f"{total} earlier {noun} dropped at the day cap — "
            f"the day's coverage is incomplete.")


def day_note(env, today=None):
    """Bottom-bar note about the DAY union: the date gate, or the cap. ``''`` when
    the day's table is complete and current.

    A cold service says nothing here — ``status_line`` already reports it, and two
    "waiting" messages read like two problems."""
    env = env or {}
    if not env:
        return ""
    if not day_is_today(env, today):
        return ("Waiting for today's scan — the day's signals are from a previous "
                "session and are not shown.")
    return truncated_note(env)


def stamp_stale(rows, signals):
    """Stamp the day-union state (``_stale`` / ``_row_class`` / ``stale_since``)
    onto display rows, joined to their signals **by id**.

    Joined, not zipped: the row builders re-sort by score, so row order does not
    track signal order. One stamper for all three tabs — the 0-DTE/Swing rows come
    from ``signal_rows`` and the Directional rows from the SHARED
    ``strategy_table.strategy_rows``, and the day state must not be able to drift
    between them.

    ``live is False`` — NOT ``not live``: a payload from the live-only
    ``cache:options:scan`` (or any pre-day-union cache) carries no ``live`` key at
    all, and an absent key must never read as "dropped out".

    Also **closes the Paper button** on a stale row (``_allow_paper``). A dropped
    signal is frozen at the price it last qualified at — possibly hours old — and
    ``paper_create`` records ``signal['credit']`` VERBATIM with no re-pricing, so
    booking one writes a fictional entry (a 9:30 credit stamped with a 2pm
    ``entry_time``) into the manual book. That book is the app's
    scanner-baseline-vs-decider benchmark, so a fictional fill corrupts the very
    measurement it exists for. This hazard is NEW: before the day union the page
    only ever rendered the last scan's live signals (≤15 min old), so the path did
    not exist — persistence created it. (Same reflex as ``rescue_apply``, which
    refuses to mutate on price drift.) Reviewing a dropped signal is the POINT of
    the day union, so Calculator/Expected-Move stay open — only booking is barred.

    The gate only ever NARROWS: a live naked short whose ``_allow_paper``
    ``strategy_rows`` already cleared (undefined risk) must never be re-enabled.
    """
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for r in rows:
        signal = by_id.get(r.get("id")) or {}
        stale = signal.get("live") is False
        r["_stale"] = stale
        r["_row_class"] = STALE_ROW_CLASS if stale else ""
        r["stale_since"] = _short_time(signal.get("stale_since")) if stale else ""
        r["_allow_paper"] = bool(r.get("_allow_paper", True)) and not stale
    return rows


# A data-driven colour maps a FINITE state to a fixed class (the Tailwind-first
# standard). These are the theme's SEMANTIC tokens, not named-palette classes, so
# the cell follows ``config/theme.toml`` like every other coloured cell in the
# app. ``persistence.score_trend`` returns exactly these four states, and
# ``_TREND_CLASSES`` is asserted to cover all of them — an unmapped state would
# render unstyled, silently, and only for the rows in that state.
_TREND_CLASSES = {"rising": TXT_POS, "fading": TXT_NEG,
                  "steady": TXT_NEUTRAL, "new": MUTED}

# Plain text, no badge — the ``body-cell-bias`` shape. The ``|| '—'`` is the net
# for a row that reached a table WITHOUT passing through ``stamp_persistence``
# (there is exactly one call site): undefined renders as the dash rather than as
# a blank cell. It duplicates ``persistence.DASH`` in a Vue string, so a test
# pins the two equal.
_TREND_SLOT = r'''
      <q-td :props="props">
        <span :class="props.row._trend_class">{{ props.value || '—' }}</span>
      </q-td>
    '''


def stamp_persistence(rows, signals, setups):
    """Stamp ``seen_since`` / ``score_trend`` / ``_trend_state`` onto display rows.

    Joined by ``id`` for the same reason ``stamp_stale`` is: the row builders
    re-sort by score, so row order does not track signal order. One stamper for
    all three tabs.

    The row reaches its setup through the SIGNAL's ``setup_key`` — Tier 2 stamps
    that field, and Tier 1 never derives it, so there is no coarse-key logic here
    to drift from the service's.

    A STALE row is stamped like any other. Reviewing a dropped signal is the
    point of the day union, and its age is frozen at what it was, not erased —
    the opposite of ``_allow_paper``, which narrows on exactly that row.

    ⚠ An absent map dashes every row. A pre-change envelope and a scan whose
    setups block degraded both look like this, and both mean "no reading" — never
    "brand new", which is a claim about the signal rather than about the data.
    """
    setups = setups if isinstance(setups, dict) else {}
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for r in rows:
        signal = by_id.get(r.get("id")) or {}
        entry = setups.get(signal.get("setup_key"))
        facts = _persistence.persistence_facts(entry)
        r["seen_since"] = facts["since"]
        r["score_trend"] = facts["trend_text"]
        r["_trend_state"] = facts["trend"]
        # Mapped here, beside the state that produces it, so a state and its
        # colour cannot be stamped from two different places.
        r["_trend_class"] = _TREND_CLASSES.get(facts["trend"], MUTED)
    return rows


def repaint_action(moved, *, timer=False, matrix_moved=False):
    """``'rebuild'`` / ``'restamp'`` / ``'skip'`` for one repaint decision.

    A scan view moving re-reads and rebuilds everything (the ~4.5 MB day union).
    Only the checklist's refresh views moving re-stamps the rows already painted.
    The timer re-stamps only when the Opportunity Board moved since the last stamp,
    so it costs nothing off-hours."""
    from . import checks_feed
    moved = set(moved or ())
    if timer:
        return "restamp" if matrix_moved else "skip"
    if moved & {_DAY_VIEW, _LIVE_VIEW}:
        return "rebuild"
    if moved & set(checks_feed.REFRESH_VIEWS):
        return "restamp"
    return "skip"


def _read_and_restamp(rows_by_key, sigs_by_key, ctx_out=None):
    """Read the checklist's live context and re-stamp copies of the painted rows,
    through the reader the Strategy Finder shares. **Blocking** — go through
    ``run.io_bound``.

    ``ctx_out`` (a dict) receives the context under ``"ctx"``, so the page can
    hand the Trade detail panel the SAME context the rows were stamped against
    rather than the panel reading its own."""
    from . import checks_table
    ctx, out, _memo = checks_table.read_and_restamp_tables(rows_by_key, sigs_by_key)
    if isinstance(ctx_out, dict):
        ctx_out["ctx"] = ctx
    return out


def checklist_candidate_for(row_id, by_id, painted):
    """The Trade detail panel's checklist candidate for a clicked id: the raw
    signal plus the PAINTED row's ``_allow_paper`` - the gate ``stamp_stale``
    settled and the table's chip was judged with (a stale row's is closed). Read
    from the server's own rows, never the browser's copy. ``None`` for an id with
    no signal; a signal with no painted row yet hands no gate across."""
    sig = (by_id or {}).get(row_id)
    if not isinstance(sig, dict):
        return None
    row = next((r for rows in (painted or {}).values() for r in rows or []
                if r.get("id") == row_id), None)
    return detail.checklist_candidate(sig, (row or {}).get("_allow_paper"))


def _short_time(iso):
    """ISO timestamp -> short local time like '1:32 PM'; '' on failure/None."""
    if not iso:
        return ""
    try:
        t = dt.datetime.fromisoformat(iso)
        return t.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return ""


def status_line(results):
    """The status line under the header: signal count (+ errors) + the cadence.

    Reads the LIVE ``options:scan`` view, not the day union: this line is about the
    last SCAN (its count, its errors), which the day envelope does not carry.

    **No clock.** It used to open "Last scan 1:32 PM"; the header's own Updated
    stamp carries that time now, in Central and turning amber once a scan is
    genuinely overdue, so printing it twice would be two answers to one
    question - and only one of them knows what late looks like.

    The count says **live** deliberately. The tab headers carry the DAY's counts
    (hundreds by 3pm) while this sums the last scan (dozens), so a bare
    "37 signals" sitting under tabs reading "(412)" reads as a bug. That
    correspondence was true before the day union — the tables rendered the same
    payload this line summed — and persistence broke it; the word is what makes the
    gap legible. Empty results (service cold) -> a waiting note."""
    results = results or {}
    if not results:
        return _copy.WAITING_OPTIONS
    n = sum(len(results.get(key) or []) for key in DAY_LISTS)
    parts = [f"{n} live signal" + ("" if n == 1 else "s")]
    errs = results.get("errors") or []
    if errs:
        parts.append(f"{len(errs)} errors")
    parts.append("auto-scans every 15 min")
    return " · ".join(parts)


# Distinct, clearly-defined tab accent colors so the three tabs read at a glance:
# 0-DTE = amber (short-fuse/urgent), Swing = blue (multi-day), Directional = purple
# (a different KIND of trade — and a different scorer). Each tab carries its accent
# in the text color and a thick colored underline when active (the shared Quasar
# indicator is hidden so the per-tab color is unambiguous). Purple deliberately
# avoids the score palette's red/amber/blue/green so it can't read as a quality.
TAB_0DTE_COLOR, TAB_SWING_COLOR, TAB_DIR_COLOR = "#ffa726", "#42a5f5", "#ab47bc"

# Compact the signal tables (dense + tight cell padding) so the right-hand
# columns (R/R %, PoP %, Score, Grade, actions) stay visible. Scoped to
# .scan-table so it never leaks to other tables. The per-tab accent TEXT color is
# applied as a Tailwind class on each ui.tab; only the Quasar-internal tab chrome
# (text-transform/weight, hidden indicator, the active-underline box-shadow which
# can't be reached via .classes()) stays here. Run scan is a kit.button.
SCAN_CSS = '''
.scan-table td, .scan-table th { padding: 2px 4px; }
.scan-tabs .q-tab { font-weight: 600; }
/* Quasar gives each q-tab-panel 16px padding — drop it so the tables sit flush
   with the column (aligned with the right-justified Run scan button). */
.scan-panels .q-tab-panel { padding: 0; }
'''


_DAY_VIEW, _LIVE_VIEW = "options:scan_day", "options:scan"


def _read_all():
    """Read both views' payloads → ``(day_env, live)``. **Blocking + big** — the day
    union serializes to ~4.5 MB by day's end (~880 B x ~5,238 entries), so every
    caller must go through ``run.io_bound`` and keep it off the event loop."""
    return (bus_client.read(_DAY_VIEW) or {}), (bus_client.read(_LIVE_VIEW) or {})


def _build_populate(day_env, live, ctx=None):
    """PURE, heavy row construction — the ~5,238 display-row dicts + the by-id map,
    stamped with the day-union state and then the checklist chip (``ctx`` is a
    ``checks_feed.read_context()`` result; None paints every chip unchecked).

    Runs OFF the event loop (via _read_and_build): only the New-marker stamps + the
    UI assignment are left for the loop (see _apply_populate). Returns everything
    _apply_populate needs."""
    day_env, live = day_env or {}, live or {}
    today = today_ct()
    sigs = {key: day_signals(day_env, key, today) for key in DAY_LISTS}
    by_id = {}
    for signals in sigs.values():
        for s in signals:
            if s.get("id"):
                by_id[s["id"]] = s
    rows = {
        "signals_0dte": signal_rows(sigs["signals_0dte"]),
        "signals_swing": signal_rows(sigs["signals_swing"]),
        "signals_directional": directional_rows(sigs["signals_directional"]),
    }
    for key in DAY_LISTS:
        # ORDER IS LOAD-BEARING: stamp_stale settles ``_allow_paper``, which the
        # checklist's Paper book line reads.
        stamp_stale(rows[key], sigs[key])
        # Order-independent: this writes four keys neither of its neighbours
        # reads or writes. It sits after stamp_stale so the block reads
        # lifecycle-then-verdict. ``day_env`` is normalised to {} above, and
        # stamp_persistence guards a non-dict ``setups`` inside the envelope.
        stamp_persistence(rows[key], sigs[key], day_env.get("setups"))
        stamp_checks(rows[key], sigs[key], ctx)
    return {"today": today, "sigs": sigs, "by_id": by_id, "rows": rows,
            "have": day_is_today(day_env, today), "day_env": day_env, "live": live,
            "ctx": ctx}


def _read_and_build():
    """Read both payloads and the checklist's live context, AND build the rows in
    ONE off-thread call, so the event loop is left only the UI assignment.
    **Blocking + heavy** — go through ``run.io_bound``."""
    from . import checks_feed
    day_env, live = _read_all()
    return _build_populate(day_env, live, checks_feed.read_context())


# Quasar reads ``rowsPerPage: 0`` as INFINITE, and NiceGUI's ui.table defaults to
# exactly that. The old page rendered one scan (~40 rows/table); the day union
# reaches ~1,746 per list, i.e. ~5,238 rows x 13-16 columns (~75k cells, many
# carrying a q-badge/q-tooltip) rebuilt wholesale on every scan — worst at 3pm,
# exactly when a trader is looking. Page it.
_TABLE_PAGINATION = {"rowsPerPage": 100}

# A dropped-out row is dimmed via Quasar's `table-row-class-fn`. The page's own
# ``_ROW_CLASS_PROP`` went with the 2026-09-19 page-kit migration: ``kit.table``
# writes ``kit.ROW_CLASS_FN``, which COMPOSES this page's ``_row_class`` (the
# stale dimming) with the kit's selected-row accent, so neither has to give way.

# Composite-score chip (0-DTE / Swing only — a directional signal's Fit+Quality
# score gets the same chip from the shared strategy columns).
_SCORE_SLOT = r'''
  <q-td :props="props">
    <q-badge :class="props.row._score_class + ' text-[#111]'" :label="props.value ?? '—'"/>
  </q-td>
'''

# Small, persistent "new" tag on signals unseen since the page was last viewed.
_SYMBOL_SLOT = r'''
  <q-td :props="props">
    {{ props.value }}
    <q-badge v-if="props.row._new" label="new"
             class="q-ml-xs text-[9px] px-1 py-0 bg-[#1565c0] text-white"/>
  </q-td>
'''


# ── "Why no trade?" — the scan funnel panel ─────────────────────────────────
#
# The tables answer "what qualified"; this panel answers the other question,
# off ``cache:options:scan_funnel`` — the per-symbol account
# ``scanner_engine.run_full_scan`` keeps of where each window stopped.
#
# Every sentence on screen is built by the PURE ``funnel_view`` module. The page
# owns only the read, the widgets and the symbol picker, which is what makes the
# absent cases safe: a symbol the scan never reached, a payload that was never
# published, and a whole-symbol ``stop`` all land on ``bucket_card(None, …)`` /
# its stop branch and read as words, never as a stage list of confident zeroes.
#
# ⚠ The read happens on OPEN, never at page build. Nobody reading the tables has
# asked for the funnel, and it goes through ``run.io_bound`` like every other bus
# read on this page — the two big reads already share an in-flight guard because
# a loop-side read here blocks every tab.

_FUNNEL_VIEW = "options:scan_funnel"

FUNNEL_TITLE = "Why no trade?"
FUNNEL_LEAD = ("The tables show what qualified. This shows where each symbol "
               "stopped in the last scan.")
FUNNEL_LOADING = "Reading the last scan…"

# The three scan windows, in the tab strip's own order. The keys are the
# engine's own spellings (``funnel_view.BUCKET_LABELS`` carries the reader's).
FUNNEL_BUCKETS = ("0DTE", "SWING", "DIRECTIONAL")


def funnel_symbols(payload):
    """Every symbol the funnel accounts for, sorted. PURE."""
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, dict):
        return []
    return sorted(str(s) for s in symbols)


def funnel_seed(payload_symbols, current=None):
    """Which symbol to show: the reader's own while the new payload still
    carries it, else the first. PURE; ``None`` when there is nothing to show."""
    if current in payload_symbols:
        return current
    return payload_symbols[0] if payload_symbols else None


def funnel_chips(payload):
    """One chip per window: how many symbols it left empty, of how many. PURE.

    ⚠ ``[]`` for a payload with no symbols. ``empty_symbols`` answers ``[]``
    both for "nothing was empty" and for "there is nothing to read", and a chip
    reading "0 of 0 produced nothing" off a cold view is exactly the zero this
    app must never print.
    """
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, dict) or not symbols:
        return []
    total = len(symbols)
    out = []
    for bucket in FUNNEL_BUCKETS:
        n = len(funnel_view.empty_symbols(payload, bucket))
        label = funnel_view.BUCKET_LABELS[bucket]
        out.append({"bucket": bucket, "label": label, "count": n,
                    "text": f"{label} · {n} of {total} produced nothing"})
    return out


def funnel_cards(payload, symbol, scan_timestamp=None):
    """The three window cards for one symbol, straight from ``funnel_view``.

    PURE. The entry is LOOKED UP here rather than handed in, so every absent
    case reaches ``bucket_card(None, …)`` — which says so in words — instead of
    the caller having to remember to.
    """
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    entry = symbols.get(symbol) if isinstance(symbols, dict) else None
    note = funnel_view.stale_note(payload, scan_timestamp)
    return [funnel_view.bucket_card(entry, bucket, symbol=symbol, note=note)
            for bucket in FUNNEL_BUCKETS]


def stage_class(stage):
    """One stage row's Tailwind class: the BINDING stage — the wall the symbol
    actually hit — in warn, every other row muted. PURE, mapped off a finite
    state (the Tailwind-first standard)."""
    return TXT_WARN if (stage or {}).get("binding") else MUTED


def _read_funnel():
    """Read the funnel view and the LIVE scan's stamp → ``(payload, live)``.
    **Blocking** — go through ``run.io_bound``.

    The live view is read for its ``timestamp`` alone: two stamps are what let
    ``funnel_view.stale_note`` say the account predates the scan on screen, and
    with one it correctly says nothing.
    """
    return (bus_client.read(_FUNNEL_VIEW) or {}), (bus_client.read(_LIVE_VIEW) or {})


def render():
    """Build the Options scanner page body (two-pane: tables + detail panel).

    Reads the day union (``options:scan_day``) for the tables and the live view
    (``options:scan``) for the status bar's scan time / errors / warnings; the
    options service owns the engine + the auto-scan schedule. Graceful-empty: when
    the service is cold (no cache) the page paints empty tables + a waiting status.
    """
    from . import checks_feed
    ui.add_css(SCAN_CSS)  # compact signal-table columns
    # 0-DTE / Swing / Directional as SUBTABS directly under the main tab strip
    # (like Gamma's view tabs, 2026-07-11): rendered into shell.subtab_slot(),
    # folder-styled by .compact-subtabs; the per-tab accent TEXT colors are kept.
    # Falls back inline if the slot is absent. tab_panels below reference the
    # element regardless of where it is mounted.
    import shell as _shell
    _slot = _shell.subtab_slot()

    def _build_scan_tabs():
        with ui.tabs().classes("compact-subtabs scan-tabs").props(
                "dense no-caps inline-label align=left") as tabs:
            t0 = ui.tab("0-DTE").classes(f"tab-0dte text-[{TAB_0DTE_COLOR}]")
            ts = ui.tab("Swing").classes(f"tab-swing text-[{TAB_SWING_COLOR}]")
            td = ui.tab("Directional").classes(f"tab-dir text-[{TAB_DIR_COLOR}]")
            for _tab, _key in ((t0, "0-DTE"), (ts, "Swing"), (td, "Directional")):
                with _tab:
                    ui.tooltip(_page_help.subtab_help("/options/scanner", _key)
                               ).props("delay=350 max-width=340px")
        return tabs, t0, ts, td

    if _slot is not None:
        with _slot:
            tabs, tab_0dte, tab_swing, tab_dir = _build_scan_tabs()
    else:
        tabs, tab_0dte, tab_swing, tab_dir = _build_scan_tabs()
    # initial= because the default lives on the tab_panels below, and that
    # reaches tabs.value through a binding that has not propagated yet.
    _shell.bind_breadcrumb_leaf(tabs, initial="0-DTE")

    def _table(columns):
        t = kit.table(columns, rows_per_page=_TABLE_PAGINATION["rowsPerPage"],
                      numeric=("dte", "credit", "max_loss", "rr_pct", "pop_pct",
                               "iv_rank", "composite_score"),
                      classes="w-full scan-table")
        return t

    with kit.page():
        head = kit.header("Market Scanner", view="options:scan", stale=True)
        with head.actions:
            # Quiet, and left of Run scan: it explains the tables rather than
            # changing them, so it must not read as the page's action.
            why_btn = kit.button(FUNNEL_TITLE, kind="quiet", icon="help_outline")
            scan_btn = kit.button("Run scan", kind="primary", icon="play_arrow")
        with ui.row().classes("w-full items-center gap-3"):
            status = kit.status_line()
            ui.space()
            clear_toggle = ui.switch("Only clear", value=False)
            with clear_toggle:
                ui.tooltip(_ONLY_CLEAR_TIP).props("delay=350")
        # Only when something is off: the day-union note (stale date / day cap).
        # Named for the BOX, not ``day_note`` - that is the module's function.
        day_note_box = ui.row().classes("w-full")
        with ui.row().classes("w-full no-wrap gap-4 items-start"):
            # A rescan takes tens of seconds; the tables meanwhile show the
            # PREVIOUS scan, which is indistinguishable from a finished one.
            scan = kit.region("Scanning…", classes="flex-grow min-w-0")
            with scan.content:
                scan_panels = ui.tab_panels(tabs, value=tab_0dte).classes(
                    "w-full scan-panels")
                with scan_panels:
                    with ui.tab_panel(tab_0dte):
                        table_0dte = _table(signal_columns())
                    with ui.tab_panel(tab_swing):
                        table_swing = _table(signal_columns())
                    with ui.tab_panel(tab_dir):
                        table_dir = _table(directional_columns())
            # Narrower than the 360px default so the compacted signal table has
            # room to show all columns without horizontal scroll.
            detail_panel = detail.render(width=290)

    by_id: dict = {}
    # Last-seen bus cache versions for the fetch-free repaint timer. (NEW-signal
    # tracking lives at module level so it persists across navigation.)
    _probe_views = (_DAY_VIEW, _LIVE_VIEW) + tuple(checks_feed.REFRESH_VIEWS)
    seen = {v: None for v in _probe_views}
    # The full stamped rows per table, so the "Only clear" switch can re-filter
    # without re-reading the bus.
    painted = {key: [] for key in DAY_LISTS}
    # The signals those rows were built from (a re-stamp needs them) and whether
    # today's day union exists (the tab counts need it).
    painted_sigs = {key: [] for key in DAY_LISTS}
    counts = {"have": False}
    # The Opportunity Board version the rows were last stamped against, so the
    # 5-minute timer re-stamps only when the board has actually moved.
    stamped = {"matrix": None}
    # Shared by the deferred first read + the 2 s poll so the two big
    # off-loop reads can never stack (the gamma.py precedent).
    state = {"fetching": False}
    # The checklist context the painted rows were last stamped against (None
    # until the first off-loop read lands), handed to the Trade detail panel so
    # its checklist and the row's chip judge against the same read.
    checks_ctx = {"ctx": None}

    def _clicked(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        return by_id.get(row.get("id")) if isinstance(row, dict) else None

    def _candidate(sig):
        return checklist_candidate_for(sig.get("id"), by_id, painted)

    def _select(event):
        sig = _clicked(event)
        if sig:
            detail_panel.update(sig, candidate=_candidate(sig), ctx=checks_ctx["ctx"])
            _remember(event, sig, False)

    def _select_dir(event):
        # The normalized multi-leg shape needs the shared adapter (net_credit →
        # credit, breakevens[0] → breakeven) the Swing page uses.
        from . import strategy_table
        sig = _clicked(event)
        if sig:
            detail_panel.update(strategy_table.detail_signal(sig),
                                candidate=_candidate(sig), ctx=checks_ctx["ctx"])
            # The RAW signal, not the adapted one: the legs-aware Calculator and
            # Paper paths read ``legs``, which the adapter does not carry.
            _remember(event, sig, True)

    # The panel asks this for the open row on every refresh: the gate as the rows
    # now carry it, or None once a rebuild dropped the row (cap eviction, a new
    # day's first scan) - which the panel shows as gone, never as a verdict.
    detail_panel.set_candidate_source(
        lambda row_id: checklist_candidate_for(row_id, by_id, painted),
        gone_text=detail.GONE_SCAN_TEXT)

    def _refresh_detail_checks():
        """After a rebuild or re-stamp, repaint the open checklist against the new
        context (a no-op when no checklist shows)."""
        detail_panel.refresh_checks(checks_ctx["ctx"])

    for _t in (table_0dte, table_swing):
        _t.on("rowClick", _select)
        _t.add_slot('body-cell-composite_score', _SCORE_SLOT)
        _t.add_slot('body-cell-symbol', _SYMBOL_SLOT)
        _t.add_slot('body-cell-checks', _CHECKS_SLOT)
        _t.add_slot('body-cell-score_trend', _TREND_SLOT)

    table_dir.on("rowClick", _select_dir)
    # Every Paper click's answer - opened, or refused and why - becomes a toast.
    handoff.watch_paper_results()
    table_dir.add_slot('body-cell-symbol', _SYMBOL_SLOT)
    table_dir.add_slot('body-cell-composite_score', _SCORE_SLOT)
    table_dir.add_slot('body-cell-checks', _CHECKS_SLOT)
    table_dir.add_slot('body-cell-score_trend', _TREND_SLOT)
    table_dir.add_slot('body-cell-bias', r'''
      <q-td :props="props">
        <span :class="props.row._bias_class">{{ props.value || '—' }}</span>
      </q-td>
    ''')
    # A naked short's max loss is a margin proxy, not a cap — say so on the cell.
    table_dir.add_slot('body-cell-max_loss', r'''
      <q-td :props="props">
        {{ props.value }}
        <q-badge v-if="props.row._undefined_risk" label="undefined risk"
                 class="q-ml-xs text-[9px] px-1 py-0 bg-[#b71c1c] text-white"/>
      </q-td>
    ''')
    table_dir.add_slot('body-cell-grade', r'''
      <q-td :props="props">
        <span :class="props.row._grade_class">{{ props.value || '—' }}</span>
        <q-tooltip v-if="props.row.grade_reason">{{ props.row.grade_reason }}</q-tooltip>
      </q-td>
    ''')

    # The clicked row IS the selection: it drives the detail panel and the
    # actions in its footer. ``multi`` records which tab it came from - a
    # directional row is a normalized multi-leg signal and takes the legs-aware
    # Calculator path; ``allow_paper`` is the gate ``stamp_stale`` settled.
    sel = {"sig": None, "multi": False, "allow_paper": False, "id": None}

    # Built ONCE: the footer is visible only while a signal is shown, so nothing
    # here can be pressed with no selection and nothing prints "click a row
    # first". Primary last, so Paper trade sits rightmost.
    with detail_panel.actions:
        kit.button("Expected Move", kind="secondary", icon="show_chart",
                   on_click=lambda: _send_em())
        kit.button("Calculator", kind="secondary", icon="calculate",
                   on_click=lambda: _send_calc())
        paper_btn = kit.button("Paper trade", kind="primary", icon="request_quote",
                               on_click=lambda: _send_paper())

    def _remember(event, sig, multi):
        """Latch the clicked row as the selection and re-stamp the accent.

        The Paper button HIDES rather than disables on a row that may not be
        booked (a dropped signal frozen at an hours-old price, or a naked short
        with undefined risk) - the same gate the per-row icon used, read off the
        server's own painted row."""
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sel.update(sig=sig, multi=multi, id=(row or {}).get("id"),
                   allow_paper=bool((row or {}).get("_allow_paper")))
        paper_btn.set_visibility(sel["allow_paper"])
        _paint_tables()                      # re-stamps _selected

    @guard
    def _send_em():
        if sel["sig"]:
            handoff.send_to_expected_move(handoff.signal_to_em_payload(sel["sig"]))

    @guard
    def _send_calc():
        if sel["sig"]:
            (handoff.send_signal_to_calculator if sel["multi"]
             else handoff.send_to_calculator)(sel["sig"])

    @guard
    def _send_paper():
        if sel["sig"] and sel["allow_paper"]:
            handoff.send_to_paper(sel["sig"])

    def _populate(day_env, live, *, notify=True, acknowledge=False):
        """Build (heavy) + paint the tables. Sync convenience wrapper used for the
        instant empty paint; the async load paths build OFF the loop via
        ``_read_and_build`` then call ``_apply_populate`` directly."""
        _apply_populate(_build_populate(day_env, live), notify=notify,
                        acknowledge=acknowledge)

    def _paint_tables():
        """Assign the stored rows to the tables, filtered when "Only clear" is on,
        with the tab counts and the empty-state line following the filter."""
        filtering = bool(clear_toggle.value)
        for key, table, tab, base in (
                ("signals_0dte", table_0dte, tab_0dte, "0-DTE"),
                ("signals_swing", table_swing, tab_swing, "Swing"),
                ("signals_directional", table_dir, tab_dir, "Directional")):
            full = painted[key]
            shown = only_clear(painted[key]) if filtering else full
            kit.mark_selected(shown, sel["id"])
            empty = only_clear_empty_label(full, shown, filtering=filtering)
            # Written to _props directly: a props STRING would be re-parsed.
            if empty is None:
                table._props.pop("no-data-label", None)
            else:
                table._props["no-data-label"] = empty
            table.rows = shown
            table.update()
            label = filtered_tab_label(base, len(full), len(shown),
                                       have=counts["have"], filtering=filtering)
            tab.props(f'label="{label}"')
            tab.update()

    @guard
    def _on_clear_toggle(_event):
        _paint_tables()             # re-filter the stored rows; no bus read

    clear_toggle.on_value_change(_on_clear_toggle)

    def _apply_populate(built, *, notify=True, acknowledge=False):
        """Paint the tables + detail map + bottom status from the OFF-LOOP-built
        ``built`` dict (see _build_populate). Only the New-marker stamps + the UI
        assignment run here on the event loop.

        ``acknowledge`` — True only when the user is actually VIEWING the page (the
        initial paint), so a background repaint never clears their New markers.
        """
        today, rows, sigs = built["today"], built["rows"], built["sigs"]
        live = built["live"]

        by_id.clear()
        by_id.update(built["by_id"])

        new_ids = new_ids_for_paint(set(by_id), today, acknowledge)
        for key in DAY_LISTS:
            # stamp_stale + stamp_checks already ran off the loop (_build_populate).
            stamp_new(rows[key], new_ids)
            painted[key] = rows[key]
            painted_sigs[key] = sigs[key]
        # Day counts in each tab header — no count until a day union for TODAY
        # exists, so the tabs don't show a misleading "(0)" before the first scan
        # or while a stale-dated envelope is gated out. _paint_tables writes them.
        counts["have"] = built["have"]
        _paint_tables()
        if built.get("ctx") is not None:
            checks_ctx["ctx"] = built["ctx"]
        _refresh_detail_checks()

        scan.busy.hide()
        kit.set_busy(scan_btn, False)
        status.text = status_line(live)
        day_note_box.clear()
        note = day_note(built["day_env"], today)
        if note:
            with day_note_box:
                kit.notice(note, icon="warning")
        if notify:
            for w in (live.get("warnings") or []):
                kit.toast("warn", w)

    # ── "Why no trade?" ──────────────────────────────────────────────────────
    # Built at the PAGE's own level, never inside a container a repaint clears:
    # a ui.dialog leaves a canary in the slot it is built from and deletes
    # itself when that canary goes (the swing.py precedent).
    funnel_state = {"payload": {}, "scan_ts": None, "fetching": False}

    funnel = kit.info_dialog(FUNNEL_TITLE)
    with funnel.content:
        ui.label(FUNNEL_LEAD).classes(f"text-xs {MUTED}")
        funnel_chips_box = ui.row().classes("gap-2 items-center flex-wrap")
        # The field's LABEL sits above its dropdown (the kit's one field shape),
        # so a cold view has to hide the pair - hiding the select alone would
        # leave a bare "Symbol" over nothing.
        with ui.element("div") as funnel_sel_box:
            funnel_sel = kit.select_field("Symbol", [], width="w-56", with_input=True)
        funnel_status = ui.label(FUNNEL_LOADING).classes(f"text-sm {MUTED}")
        funnel_box = ui.column().classes("w-full gap-3")

    def _paint_funnel_cards():
        """Repaint the three cards from the STORED payload — no bus read, so
        picking a symbol costs nothing."""
        funnel_box.clear()
        with funnel_box:
            for card in funnel_cards(funnel_state["payload"], funnel_sel.value,
                                     funnel_state["scan_ts"]):
                with ui.column().classes(f"{CARD} w-full gap-1"):
                    ui.label(card["headline"]).classes(f"text-sm {LABEL}")
                    if card["note"]:
                        ui.label(card["note"]).classes(f"text-xs {EYEBROW}")
                    for stage in card["stages"]:
                        cls = f"text-xs {stage_class(stage)}"
                        with ui.row().classes(
                                "w-full justify-between items-baseline no-wrap gap-2"):
                            ui.label(stage["label"]).classes(cls)
                            ui.label(str(stage["remaining"])).classes(cls)

    @guard
    def _on_funnel_symbol(_event):
        _paint_funnel_cards()       # from the stored payload; no bus read

    funnel_sel.on_value_change(_on_funnel_symbol)

    def _apply_funnel(payload, live):
        funnel_state["payload"] = payload or {}
        funnel_state["scan_ts"] = (live or {}).get("timestamp")
        symbols = funnel_symbols(funnel_state["payload"])
        funnel_sel.set_options(symbols,
                               value=funnel_seed(symbols, funnel_sel.value))
        funnel_sel_box.set_visibility(bool(symbols))
        funnel_chips_box.clear()
        with funnel_chips_box:
            for chip in funnel_chips(funnel_state["payload"]):
                ui.badge(chip["text"]).classes(
                    BADGE_WARN if chip["count"] else BADGE_MUTED)
        # Nothing published at all is the ONE thing the cards cannot say for the
        # panel as a whole (they speak per symbol), so the waiting line carries it.
        funnel_status.text = "" if symbols else _copy.WAITING_OPTIONS
        funnel_status.set_visibility(bool(funnel_status.text))
        _paint_funnel_cards()

    @guard_async
    async def _open_funnel():
        # Read on OPEN, never at page build — and OFF the loop, like every other
        # bus read here. Closing and reopening re-reads; picking a symbol does not.
        funnel.open()
        if funnel_state["fetching"]:
            return
        funnel_state["fetching"] = True
        # Placeholder first: the previous open's verdict under a fresh dialog
        # reads as this scan's answer, which is worse than no answer.
        funnel_chips_box.clear()
        funnel_box.clear()
        funnel_status.text = FUNNEL_LOADING
        funnel_status.set_visibility(True)
        try:
            payload, live = await run.io_bound(_read_funnel)
        finally:
            funnel_state["fetching"] = False
        _apply_funnel(payload, live)

    why_btn.on_click(_open_funnel)

    @guard
    def _request_scan():
        # No toast: the region's spinner and the button's own wait already say
        # the scan is running, which is all a toast would have repeated.
        bus_client.request("options", {"type": "rescan"})
        scan.busy.show()
        kit.set_busy(scan_btn)

    scan_btn.on_click(_request_scan)

    @guard_async
    async def _initial_load():
        # Big-payload first read, OFF the loop (see _read_all). Shares the
        # `fetching` guard with the poll so the two can't stack. This paint IS the
        # user viewing the page, so it acknowledges the New markers.
        if state["fetching"]:
            return
        state["fetching"] = True
        stamped["matrix"] = bus_client.read_version(checks_feed.MATRIX_VIEW)
        try:
            built = await run.io_bound(_read_and_build)
        finally:
            state["fetching"] = False
        _apply_populate(built, notify=False, acknowledge=True)

    async def _rebuild():
        """Re-read + rebuild off the loop and repaint, under the ``fetching``
        guard. Only a scan view moving comes here."""
        if state["fetching"]:
            return
        state["fetching"] = True
        stamped["matrix"] = bus_client.read_version(checks_feed.MATRIX_VIEW)
        try:
            built = await run.io_bound(_read_and_build)
        finally:
            state["fetching"] = False
        # NOT a view — the user may be away, so their New markers must survive.
        _apply_populate(built, notify=False, acknowledge=False)

    async def _restamp():
        """Re-stamp the painted rows against a fresh context, off the loop, under
        the ``fetching`` guard - no day-union read."""
        if state["fetching"]:
            return
        state["fetching"] = True
        holder = {}
        try:
            fresh = await run.io_bound(_read_and_restamp, dict(painted), dict(painted_sigs),
                                       holder)
        finally:
            state["fetching"] = False
        painted.update(fresh)
        _paint_tables()
        if holder.get("ctx") is not None:
            checks_ctx["ctx"] = holder["ctx"]
        _refresh_detail_checks()

    @guard_async
    async def _maybe_repaint():
        # Cheap on-loop probe: only the `:ver` counters (a few tiny ints, one
        # pipelined round-trip) - the two scan views plus the views the checklist
        # re-stamps on. The ~4.5 MB payload read happens ONLY when a scan view
        # moved, and then off the loop; a context view alone re-stamps.
        versions = bus_client.read_versions(_probe_views)
        if versions == seen or state["fetching"]:
            return
        moved = {v for v, ver in versions.items() if ver != seen.get(v)}
        seen.update(versions)          # latch BEFORE the await so we don't re-enter
        action = repaint_action(moved)
        if action == "rebuild":
            await _rebuild()
        elif action == "restamp":
            stamped["matrix"] = bus_client.read_version(checks_feed.MATRIX_VIEW)
            await _restamp()

    @guard_async
    async def _force_repaint():
        # The Opportunity Board moves every minute and its walls / flip / trend
        # feed the checks, but it is not a re-stamp trigger: re-stamp on a fixed
        # cadence instead (operator decision, checks_feed.TABLE_REFRESH_SEC), and
        # only when the board moved since the last stamp (one cheap :ver probe).
        if state["fetching"]:
            return
        ver = bus_client.read_version(checks_feed.MATRIX_VIEW)
        if repaint_action((), timer=True, matrix_moved=ver != stamped["matrix"]) != "restamp":
            return
        stamped["matrix"] = ver        # latch BEFORE the await
        await _restamp()

    seen.update(bus_client.read_versions(_probe_views))
    _populate({}, {}, notify=False)             # instant empty paint
    ui.timer(0.05, _initial_load, once=True)    # big day-union read off-loop
    ui.timer(2.0, _maybe_repaint)
    ui.timer(checks_feed.TABLE_REFRESH_SEC, _force_repaint)
