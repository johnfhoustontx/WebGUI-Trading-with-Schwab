"""Options scanner page (0-4 DTE / 5-15 DTE).

Tier-3 reader: this page holds **no engine call, no in-process result cache, and
no auto-scan loop**. The scan engine (``scanner_engine.run_full_scan``), its
scheduling (08:00–15:15 CT cadence), and the cross-app ``scoring`` collision
guard all live in ``services/options_svc`` — the only process that imports the
options engine. The service runs the scan and writes the result to the Redis bus
under ``cache:options:scan``; this page only **reads** that payload, **formats**
it into the two signal tables, and **enqueues a rescan command** on the bus.

Cache view read: ``options:scan`` → ``ScanResult.model_dump()`` with keys
``{signals_0dte, signals_swing, vix_term_structure, timestamp, errors,
warnings}`` (see ``shared/contracts/options.py`` + ``services/options_svc``).

The pure display transforms (``signal_columns``, ``signal_rows``,
``compute_new_keys``, ``status_line``, ``_round``) are unit-tested. ``render()``
wires the two-pane widgets (tables + shared Trade detail panel), a small "Run
scan" button that enqueues a ``cmd:options`` command, a slim bottom status bar,
and a fetch-free version-poll ``ui.timer`` that repaints when the bus cache
version changes.
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from . import detail, handoff


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


def signal_columns():
    """ui.table column defs for a signal table.

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
    ]
    cols = [
        {"name": field, "label": label, "field": field, "sortable": True, "align": "left"}
        for field, label in spec
    ]
    cols.append({"name": "actions", "label": "", "field": "actions", "align": "center"})
    return cols


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
            "_score_color": score_zone_color(s.get("composite_score")),
            "grade": s.get("grade", ""),
        })
    rows.sort(key=lambda r: (r["composite_score"] is not None, r["composite_score"] or 0),
              reverse=True)
    return rows


def _sig_key(r):
    """Stable identity for a signal row across scans (symbol + legs + expiry)."""
    return f'{r.get("symbol")}|{r.get("type")}|{r.get("short_strike")}|{r.get("long_strike")}|{r.get("expiration")}'


# NEW-signal tracking tied to the scan VERSION (not the page session). Stored at
# module level (single-user, like _NAV_OPEN/_CACHE) so the "new since last scan"
# markers PERSIST across navigation / page rebuilds and only recompute when a
# fresh scan lands. {version, prev_keys: that scan's keys, new_keys: new vs the
# scan before it}.
_NEW = {"version": None, "prev_keys": set(), "new_keys": set()}


def _reset_new_state():
    """Clear the module-level NEW tracker (test seam / fresh session)."""
    _NEW.update(version=None, prev_keys=set(), new_keys=set())


def compute_new_keys(version, current_keys):
    """Signal keys that are NEW vs the previous scan, memoized by scan version.

    Returns the SAME set on repeated calls for one ``version`` (so the markers
    survive navigation / page rebuilds), and recomputes only when ``version``
    changes. The first scan ever (and a cold/None version) marks nothing.
    ``current_keys`` is the union of both tables' keys for this scan."""
    if version is None:
        return set()
    if version == _NEW["version"]:
        return _NEW["new_keys"]
    first = _NEW["version"] is None
    new_keys = set() if first else (set(current_keys) - _NEW["prev_keys"])
    _NEW.update(version=version, prev_keys=set(current_keys), new_keys=new_keys)
    return new_keys


def stamp_new(rows, new_keys):
    """Stamp each row with ``_new`` = (its signal key is in ``new_keys``)."""
    for r in rows:
        r["_new"] = _sig_key(r) in new_keys
    return rows


def _short_time(iso):
    """ISO timestamp -> short local time like '1:32 PM'; '' on failure/None."""
    if not iso:
        return ""
    try:
        import datetime as dt
        t = dt.datetime.fromisoformat(iso)
        return t.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return ""


def status_line(results):
    """Slim bottom status-bar text: last-scan time + signal count (+ errors).

    Replaces the old top auto-scan / VIX-term lines — the page's only chrome is
    now this single line at the bottom. Empty results (service cold) -> a waiting
    note."""
    results = results or {}
    if not results:
        return "Waiting for options service…"
    n = len(results.get("signals_0dte") or []) + len(results.get("signals_swing") or [])
    parts = []
    when = _short_time(results.get("timestamp"))
    if when:
        parts.append(f"Last scan {when}")
    parts.append(f"{n} signals")
    errs = results.get("errors") or []
    if errs:
        parts.append(f"{len(errs)} errors")
    parts.append("auto-scans every 15 min")
    return " · ".join(parts)


# Compact the signal tables (dense + tight cell padding) so the right-hand
# columns (R/R %, PoP %, Score, Grade, actions) stay visible. Scoped to
# .scan-table so it never leaks to other tables.
SCAN_CSS = '''
.scan-table td, .scan-table th { padding: 2px 4px; }
'''


def render():
    """Build the Options scanner page body (two-pane: tables + detail panel).

    Sources scan data from the Redis bus cache (``options:scan``); the options
    service owns the engine + the auto-scan schedule. Graceful-empty: when the
    service is cold (no cache) the page paints empty tables + a waiting status.
    """
    ui.add_css(SCAN_CSS)  # compact signal-table columns
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            # Top chrome removed (market strip + title + auto-scan/VIX-term lines):
            # just a small Run scan button, the tabs, and a slim bottom status bar.
            scan_btn = ui.button("Run scan", icon="play_arrow").props("dense outline")
            with ui.tabs() as tabs:
                tab_0dte = ui.tab("0-DTE")
                tab_swing = ui.tab("Swing")
            with ui.tab_panels(tabs, value=tab_0dte).classes("w-full"):
                with ui.tab_panel(tab_0dte):
                    table_0dte = ui.table(columns=signal_columns(), rows=[],
                                          row_key="id").classes("w-full scan-table").props("dense")
                with ui.tab_panel(tab_swing):
                    table_swing = ui.table(columns=signal_columns(), rows=[],
                                           row_key="id").classes("w-full scan-table").props("dense")
            # Slim bottom status bar: last scan + counts + cadence.
            status = ui.label("").classes("opacity-60 text-sm q-mt-sm")
        # Narrower detail panel here (vs the 360px default) so the compacted
        # signal table has room to show all columns without horizontal scroll.
        detail_panel = detail.render(width=290)

    by_id: dict = {}
    # Last-seen bus cache version for the fetch-free repaint timer. (NEW-signal
    # tracking lives at module level so it persists across navigation.)
    seen = {"version": None}

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            detail_panel.update(sig)

    for _t in (table_0dte, table_swing):
        _t.on("rowClick", _select)
        # per-row buttons: Send to Calculator / Send to Paper trade
        handoff.add_row_actions(_t, lambda row: by_id.get(row.get("id")))
        # Render the composite score as a quality-colored chip.
        _t.add_slot('body-cell-composite_score', r'''
          <q-td :props="props">
            <q-badge :style="`background:${props.row._score_color};color:#111`" :label="props.value ?? '—'"/>
          </q-td>
        ''')
        # Small, persistent "new" tag on signals that appeared since the last scan.
        _t.add_slot('body-cell-symbol', r'''
          <q-td :props="props">
            {{ props.value }}
            <q-badge v-if="props.row._new" label="new" class="q-ml-xs"
                     style="font-size:9px;padding:0 4px;background:#1565c0;color:#fff"/>
          </q-td>
        ''')

    def _populate(results, version=None, *, notify=True):
        """Paint the tables + detail map + bottom status from a scan-result dict."""
        results = results or {}
        by_id.clear()
        for s in (results.get("signals_0dte") or []) + (results.get("signals_swing") or []):
            if s.get("id"):
                by_id[s["id"]] = s
        rows_0dte = signal_rows(results.get("signals_0dte"))
        rows_swing = signal_rows(results.get("signals_swing"))
        # NEW markers are tied to the scan VERSION (module-level), so they persist
        # across navigation and only recompute when a fresh scan lands. Diff BOTH
        # tables against the same prior-scan key set (their union).
        keys = {_sig_key(r) for r in rows_0dte} | {_sig_key(r) for r in rows_swing}
        new_keys = compute_new_keys(version, keys)
        stamp_new(rows_0dte, new_keys)
        stamp_new(rows_swing, new_keys)
        table_0dte.rows = rows_0dte
        table_swing.rows = rows_swing
        table_0dte.update()
        table_swing.update()
        status.text = status_line(results)
        if notify:
            for w in (results.get("warnings") or []):
                ui.notify(w, type="warning")

    @guard
    def _request_scan():
        bus_client.request("options", {"type": "rescan"})
        ui.notify("Scan requested")

    scan_btn.on_click(_request_scan)

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    scan = bus_client.read("options:scan") or {}
    seen["version"] = bus_client.read_version("options:scan")
    _populate(scan, version=seen["version"], notify=False)

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps the version when a
        # (scheduled or requested) scan finishes.
        version = bus_client.read_version("options:scan")
        if version == seen["version"]:
            return
        seen["version"] = version
        _populate(bus_client.read("options:scan") or {}, version=version, notify=False)

    ui.timer(2.0, _maybe_repaint)
