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
``_scan_meta_strip``, ``_round``) are unit-tested. ``render()`` wires the
two-pane widgets (tables + shared Trade detail panel), a "Run scan" button that
enqueues a ``cmd:options`` command, and a fetch-free version-poll ``ui.timer``
that repaints when the bus cache version changes.
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from . import detail, handoff, header


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


def signal_columns():
    """ui.table column defs for a signal table."""
    spec = [
        ("symbol", "Symbol"),
        ("type", "Type"),
        ("expiration", "Exp"),
        ("dte", "DTE"),
        ("short_strike", "Short"),
        ("long_strike", "Long"),
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
            "expiration": s.get("expiration", ""),
            "dte": s.get("dte"),
            "short_strike": s.get("short_strike"),
            "long_strike": s.get("long_strike"),
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


def mark_new(rows, prev_keys):
    """Stamp each row with _new=True if its key wasn't in prev_keys.

    On first load (prev_keys empty/falsy) nothing is marked new.
    Returns (current_keys_set, rows).
    """
    keys = {_sig_key(r) for r in rows}
    first = not prev_keys
    for r in rows:
        r["_new"] = (not first) and _sig_key(r) not in prev_keys
    return keys, rows


_TERM_PHRASES = {
    "CONTANGO": "Contango (near-term calm)",
    "BACKWARDATION": "Backwardation (near-term stress)",
    "MIXED": "Mixed term structure",
}


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


def term_text(term, ts):
    """Plain-English VIX term-structure label, '' if unknown/missing."""
    structure = (term or {}).get("structure")
    if not structure or structure == "UNKNOWN":
        return ""
    phrase = _TERM_PHRASES.get(structure, structure.title())
    when = _short_time(ts)
    tail = f" · as of {when}" if when else ""
    return f"VIX term: {phrase}{tail}"


def _scan_meta_strip(container, results):
    """Post-scan info NOT already shown by the header strip (term + timestamp)."""
    container.clear()
    with container:
        text = term_text(results.get("vix_term_structure"), results.get("timestamp"))
        if text:
            ui.label(text).classes("opacity-70 text-sm")


def render():
    """Build the Options scanner page body (two-pane: tables + detail panel).

    Sources scan data from the Redis bus cache (``options:scan``); the options
    service owns the engine + the auto-scan schedule. Graceful-empty: when the
    service is cold (no cache) the page paints empty tables + a waiting status.
    """
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            header.render()
            ui.label("Options Scanner").classes("text-h5")
            with ui.row().classes("items-center gap-3"):
                scan_btn = ui.button("Run scan", icon="play_arrow")
                status = ui.label("").classes("opacity-70")
                auto_lbl = ui.label("Auto-scan: handled by options service").classes(
                    "opacity-60 text-sm")
            meta_strip = ui.row().classes("gap-4 items-center")
            with ui.tabs() as tabs:
                tab_0dte = ui.tab("0-DTE")
                tab_swing = ui.tab("Swing")
            with ui.tab_panels(tabs, value=tab_0dte).classes("w-full"):
                with ui.tab_panel(tab_0dte):
                    table_0dte = ui.table(columns=signal_columns(), rows=[], row_key="id").classes("w-full")
                with ui.tab_panel(tab_swing):
                    table_swing = ui.table(columns=signal_columns(), rows=[], row_key="id").classes("w-full")
        detail_panel = detail.render()

    by_id: dict = {}
    # Last-seen bus cache version for the fetch-free repaint timer +
    # the set of signal keys seen so far this session (for the NEW badge).
    seen = {"version": None}
    state = {"seen_keys": set()}

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
        # Flag signals that newly appeared since the previous scan this session.
        _t.add_slot('body-cell-symbol', r'''
          <q-td :props="props">
            {{ props.value }}
            <q-badge v-if="props.row._new" color="primary" label="NEW" class="q-ml-xs"/>
          </q-td>
        ''')

    def _populate(results, *, notify=True):
        """Paint the tables + detail map + meta strip from a scan-result dict."""
        results = results or {}
        by_id.clear()
        for s in (results.get("signals_0dte") or []) + (results.get("signals_swing") or []):
            if s.get("id"):
                by_id[s["id"]] = s
        _scan_meta_strip(meta_strip, results)
        rows_0dte = signal_rows(results.get("signals_0dte"))
        rows_swing = signal_rows(results.get("signals_swing"))
        # Diff BOTH lists against the SAME prior key set, then store the union so
        # a signal present last scan isn't re-flagged in either table.
        prev = state.get("seen_keys") or set()
        k0, rows_0dte = mark_new(rows_0dte, prev)
        k1, rows_swing = mark_new(rows_swing, prev)
        state["seen_keys"] = k0 | k1
        table_0dte.rows = rows_0dte
        table_swing.rows = rows_swing
        table_0dte.update()
        table_swing.update()
        n = len(table_0dte.rows) + len(table_swing.rows)
        errs = results.get("errors") or []
        if not results:
            status.text = "Waiting for options service…"
        else:
            status.text = f"{n} signals." + (f" {len(errs)} errors." if errs else "")
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
    _populate(scan, notify=False)

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps the version when a
        # (scheduled or requested) scan finishes.
        version = bus_client.read_version("options:scan")
        if version == seen["version"]:
            return
        seen["version"] = version
        _populate(bus_client.read("options:scan") or {}, notify=False)

    ui.timer(2.0, _maybe_repaint)
