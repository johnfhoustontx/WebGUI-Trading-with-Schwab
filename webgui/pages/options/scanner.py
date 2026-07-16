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
``directional_columns``, ``directional_rows``, ``stamp_stale``, ``day_is_today``,
``day_signals``, ``day_note``, ``unseen_ids``/``acknowledge_ids``/
``new_ids_for_paint``, ``status_line``, ``_round``) are unit-tested. ``render()``
wires the two-pane widgets (tables + shared Trade detail panel), a small "Run
scan" button that enqueues a ``cmd:options`` command, a slim bottom status bar,
and a version-poll ``ui.timer`` that repaints when either bus cache version
changes. The poll itself is fetch-free (cheap ``:ver`` ints, on-loop); the day
union is ~4.5 MB by day's end, so BOTH payload reads — the deferred first paint
and the on-change repaint — go through ``run.io_bound``, sharing one in-flight
guard so they cannot stack (the ``gamma.py`` precedent).
"""
import datetime as dt
from zoneinfo import ZoneInfo

import bus_client
from nicegui import run, ui

from pages.ui_guard import guard, guard_async

from . import detail, handoff
from .theme import BTN_3D


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


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


def _col(field, label):
    return {"name": field, "label": label, "field": field, "sortable": True,
            "align": "left"}


def _actions_col():
    return {"name": "actions", "label": "", "field": "actions", "align": "center"}


# When a signal dropped out of the live scan. Only ever populated on a stale row.
_DROPPED_COL = ("stale_since", "Dropped")


def signal_columns():
    """ui.table column defs for a credit-spread signal table (0-DTE / Swing).

    Short + Long are merged into one compact 'Strikes' column and the expiration
    is shown MM/DD so the right-hand columns (Score/Grade/actions) fit."""
    spec = [
        ("symbol", "Symbol"),
        ("type", "Type"),
        ("expiration", "Exp"),
        ("dte", "DTE"),
        ("strikes", "Strikes"),
        ("credit", "Credit"),
        ("max_loss", "Max Loss"),
        ("rr_pct", "R/R %"),
        ("pop_pct", "PoP %"),
        ("composite_score", "Score"),
        ("grade", "Grade"),
        _DROPPED_COL,
    ]
    return [_col(field, label) for field, label in spec] + [_actions_col()]


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
    return [_col("symbol", "Symbol")] + body + [_col(*_DROPPED_COL), _actions_col()]


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


def _short_time(iso):
    """ISO timestamp -> short local time like '1:32 PM'; '' on failure/None."""
    if not iso:
        return ""
    try:
        t = dt.datetime.fromisoformat(iso)
        return t.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return ""


def tab_label(base, count):
    """Tab header text with the live signal count, e.g. ``'0-DTE (3)'``.

    ``count is None`` (no scan yet) renders just the base label so the tabs don't
    show a misleading ``(0)`` before the first paint."""
    return base if count is None else f"{base} ({count})"


def status_line(results):
    """Slim bottom status-bar text: last-scan time + signal count (+ errors).

    Reads the LIVE ``options:scan`` view, not the day union: this line is about the
    last SCAN (its clock time, its errors), which the day envelope does not carry.

    The count says **live** deliberately. The tab headers carry the DAY's counts
    (hundreds by 3pm) while this sums the last scan (dozens), so a bare
    "37 signals" sitting under tabs reading "(412)" reads as a bug. That
    correspondence was true before the day union — the tables rendered the same
    payload this line summed — and persistence broke it; the word is what makes the
    gap legible. Empty results (service cold) -> a waiting note."""
    results = results or {}
    if not results:
        return "Waiting for options service…"
    n = sum(len(results.get(key) or []) for key in DAY_LISTS)
    parts = []
    when = _short_time(results.get("timestamp"))
    if when:
        parts.append(f"Last scan {when}")
    parts.append(f"{n} live signal" + ("" if n == 1 else "s"))
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
# can't be reached via .classes()) stays here. The 3D "Run scan" button uses the
# shared BTN_3D token.
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


# Quasar reads ``rowsPerPage: 0`` as INFINITE, and NiceGUI's ui.table defaults to
# exactly that. The old page rendered one scan (~40 rows/table); the day union
# reaches ~1,746 per list, i.e. ~5,238 rows x 13-16 columns (~75k cells, many
# carrying a q-badge/q-tooltip) rebuilt wholesale on every scan — worst at 3pm,
# exactly when a trader is looking. Page it.
_TABLE_PAGINATION = {"rowsPerPage": 100}

# A dropped-out row is dimmed via Quasar's `table-row-class-fn` (a Function prop →
# NiceGUI's ':'-dynamic binding, evaluated as JS and assigned to the camelized
# prop). It is the only per-row class hook QTable exposes short of overriding the
# whole `body` slot, which would cost us the per-cell slots below. The class is a
# fixed Tailwind utility off a finite state (Tailwind-first standard). Hoisted to a
# constant so a test can pin the `_row_class` binding — see _SCORE_SLOT/_SYMBOL_SLOT.
_ROW_CLASS_PROP = ':table-row-class-fn="row => row._row_class"'

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


def render():
    """Build the Options scanner page body (two-pane: tables + detail panel).

    Reads the day union (``options:scan_day``) for the tables and the live view
    (``options:scan``) for the status bar's scan time / errors / warnings; the
    options service owns the engine + the auto-scan schedule. Graceful-empty: when
    the service is cold (no cache) the page paints empty tables + a waiting status.
    """
    ui.add_css(SCAN_CSS)  # compact signal-table columns
    # 0-DTE / Swing / Directional as SUBTABS directly under the main tab strip
    # (like Gamma's view tabs, 2026-07-11): rendered into main.subtab_slot(),
    # folder-styled by .compact-subtabs; the per-tab accent TEXT colors are kept.
    # Falls back inline if the slot is absent. tab_panels below reference the
    # element regardless of where it is mounted.
    import main as _shell
    _slot = _shell.subtab_slot()

    def _build_scan_tabs():
        with ui.tabs().classes("compact-subtabs scan-tabs").props(
                "dense no-caps inline-label align=left") as tabs:
            t0 = ui.tab("0-DTE").classes(f"tab-0dte text-[{TAB_0DTE_COLOR}]")
            ts = ui.tab("Swing").classes(f"tab-swing text-[{TAB_SWING_COLOR}]")
            td = ui.tab("Directional").classes(f"tab-dir text-[{TAB_DIR_COLOR}]")
        return tabs, t0, ts, td

    if _slot is not None:
        with _slot:
            tabs, tab_0dte, tab_swing, tab_dir = _build_scan_tabs()
    else:
        tabs, tab_0dte, tab_swing, tab_dir = _build_scan_tabs()

    def _table(columns):
        return ui.table(columns=columns, rows=[], row_key="id",
                        pagination=dict(_TABLE_PAGINATION)) \
            .classes("w-full scan-table") \
            .props(f"dense {_ROW_CLASS_PROP}")

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            # Run scan sits right-aligned with the table's right edge.
            with ui.row().classes("w-full justify-end"):
                scan_btn = ui.button("Run scan", icon="play_arrow", color=None) \
                    .props("no-caps").classes(BTN_3D)
            with ui.tab_panels(tabs, value=tab_0dte).classes("w-full scan-panels"):
                with ui.tab_panel(tab_0dte):
                    table_0dte = _table(signal_columns())
                with ui.tab_panel(tab_swing):
                    table_swing = _table(signal_columns())
                with ui.tab_panel(tab_dir):
                    table_dir = _table(directional_columns())
            # Slim bottom status bar: last scan + counts + cadence, and — only when
            # something is off — the day-union note (stale date / day cap).
            status = ui.label("").classes("opacity-60 text-sm q-mt-sm")
            day_msg = ui.label("").classes("text-sm text-[#ffa726]")
        # Narrower detail panel here (vs the 360px default) so the compacted
        # signal table has room to show all columns without horizontal scroll.
        detail_panel = detail.render(width=290)

    by_id: dict = {}
    # Last-seen bus cache versions for the fetch-free repaint timer. (NEW-signal
    # tracking lives at module level so it persists across navigation.)
    seen = {_DAY_VIEW: None, _LIVE_VIEW: None}
    # Shared by the deferred first read + the 2 s poll so the two big
    # off-loop reads can never stack (the gamma.py precedent).
    state = {"fetching": False}

    def _clicked(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        return by_id.get(row.get("id")) if isinstance(row, dict) else None

    def _select(event):
        sig = _clicked(event)
        if sig:
            detail_panel.update(sig)

    def _select_dir(event):
        # The normalized multi-leg shape needs the shared adapter (net_credit →
        # credit, breakevens[0] → breakeven) the Swing page uses.
        from . import strategy_table
        sig = _clicked(event)
        if sig:
            detail_panel.update(strategy_table.detail_signal(sig))

    for _t in (table_0dte, table_swing):
        _t.on("rowClick", _select)
        # per-row buttons: Send to Calculator / Send to Paper trade
        handoff.add_row_actions(_t, lambda row: by_id.get(row.get("id")))
        _t.add_slot('body-cell-composite_score', _SCORE_SLOT)
        _t.add_slot('body-cell-symbol', _SYMBOL_SLOT)

    table_dir.on("rowClick", _select_dir)
    # Legs-aware actions (the directional signal carries `legs`, not strikes), and
    # `_allow_paper` gates the Paper button — a naked short is undefined risk.
    handoff.add_strategy_row_actions(table_dir, lambda row: by_id.get(row.get("id")))
    table_dir.add_slot('body-cell-symbol', _SYMBOL_SLOT)
    table_dir.add_slot('body-cell-composite_score', _SCORE_SLOT)
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

    def _populate(day_env, live, *, notify=True, acknowledge=False):
        """Paint the tables + detail map + bottom status.

        ``day_env`` — the day union (drives every table; gated on its CT date).
        ``live``    — the last-scan view (drives the status line + warnings only).
        ``acknowledge`` — True only when the user is actually VIEWING the page (the
        initial paint), so a background repaint never clears their New markers.
        """
        day_env, live = day_env or {}, live or {}
        today = today_ct()
        sigs = {key: day_signals(day_env, key, today) for key in DAY_LISTS}

        by_id.clear()
        for signals in sigs.values():
            for s in signals:
                if s.get("id"):
                    by_id[s["id"]] = s

        rows = {
            "signals_0dte": signal_rows(sigs["signals_0dte"]),
            "signals_swing": signal_rows(sigs["signals_swing"]),
            "signals_directional": directional_rows(sigs["signals_directional"]),
        }
        new_ids = new_ids_for_paint(set(by_id), today, acknowledge)
        for key, table in (("signals_0dte", table_0dte), ("signals_swing", table_swing),
                           ("signals_directional", table_dir)):
            stamp_stale(rows[key], sigs[key])
            stamp_new(rows[key], new_ids)
            table.rows = rows[key]
            table.update()

        # Day counts in each tab header — None (no count) until a day union for
        # TODAY exists, so the tabs don't show a misleading "(0)" before the first
        # scan or while a stale-dated envelope is gated out.
        have = day_is_today(day_env, today)
        for tab, base, key in ((tab_0dte, "0-DTE", "signals_0dte"),
                               (tab_swing, "Swing", "signals_swing"),
                               (tab_dir, "Directional", "signals_directional")):
            tab.props(f'label="{tab_label(base, len(rows[key]) if have else None)}"')
            tab.update()

        status.text = status_line(live)
        day_msg.text = day_note(day_env, today)
        day_msg.set_visibility(bool(day_msg.text))
        if notify:
            for w in (live.get("warnings") or []):
                ui.notify(w, type="warning")

    @guard
    def _request_scan():
        bus_client.request("options", {"type": "rescan"})
        ui.notify("Scan requested")

    scan_btn.on_click(_request_scan)

    @guard_async
    async def _initial_load():
        # Big-payload first read, OFF the loop (see _read_all). Shares the
        # `fetching` guard with the poll so the two can't stack. This paint IS the
        # user viewing the page, so it acknowledges the New markers.
        if state["fetching"]:
            return
        state["fetching"] = True
        try:
            day_env, live = await run.io_bound(_read_all)
        finally:
            state["fetching"] = False
        _populate(day_env, live, notify=False, acknowledge=True)

    @guard_async
    async def _maybe_repaint():
        # Cheap on-loop probe: only the `:ver` counters (two tiny ints, one
        # pipelined round-trip). The ~4.5 MB payload read happens ONLY on a change,
        # and then off the loop.
        versions = bus_client.read_versions((_DAY_VIEW, _LIVE_VIEW))
        if versions == seen or state["fetching"]:
            return
        seen.update(versions)          # latch BEFORE the await so we don't re-enter
        state["fetching"] = True
        try:
            day_env, live = await run.io_bound(_read_all)
        finally:
            state["fetching"] = False
        # NOT a view — the user may be away, so their New markers must survive.
        _populate(day_env, live, notify=False, acknowledge=False)

    seen.update(bus_client.read_versions((_DAY_VIEW, _LIVE_VIEW)))
    _populate({}, {}, notify=False)             # instant empty paint
    ui.timer(0.05, _initial_load, once=True)    # big day-union read off-loop
    ui.timer(2.0, _maybe_repaint)
