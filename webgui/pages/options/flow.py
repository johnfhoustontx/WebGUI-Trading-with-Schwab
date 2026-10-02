"""Flow Alerts — Tier-1 reader of cache:options:flow_alerts.

The options service detects its flow alerts on each 1-min GEX tick (premium
crossover, contract-level unusual activity, dealer gamma-regime flip, outsized
delta, and the HIRO hedging-flow model's surge and reversal), pushes the ones
configured to push to the phone, and publishes a day-scoped rolling list. Until this
page existed the webgui only chimed and toasted them, so a missed toast meant a
lost alert. This is the durable view: today's alerts, newest first.

Pure builders are module-level and NiceGUI-free for testing; ``render()`` mounts
the table and version-polls the bus. Tier-1: imports ONLY ``nicegui`` +
``bus_client`` + ``pages.*`` helpers — no engine/service imports.

Built on the page kit (``pages/ui_kit.py``, the 2026-09-19 consistency
standard): the header line carries the title and the Updated stamp, the filters
sit in a control bar, the status line carries counts only, and one region covers
the table a publish replaces. ⚠ The stamp is NOT ``stale``-aware: this view is
published when an alert fires, so a quiet tape leaves it legitimately old and
calling that stale would report a working feed as broken.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import fmt as _fmt    # the shared numeric vocabulary (``num``)
from shared.symbols import clean_symbol

VIEW = "options:flow_alerts"

_CT_TZ = ZoneInfo("America/Chicago")

# ── the symbol opens that symbol's Dealer Positioning ───────────────────────
# ⚠ The click moved from the ROW to the SYMBOL CELL (the 2026-09-19 standard: a
# page with no detail panel links the cell that names the row, and leaves the
# row click alone — a whole row that navigates gives a reader nowhere safe to
# click). Mirrors ``matrix.py``'s dossier link, including the re-check in the
# handler: the event carries whatever the browser sent.
#
# Drawn only where ``shell.can_navigate`` says the route exists. On the public
# origin ``/options/gamma`` IS published (as ``/gamma``), so the link survives
# there — exactly as the row click did — while an origin serving no Dealer
# Positioning gets plain text instead of a link that leads nowhere.
GAMMA_EVENT = "open_gamma"
# No colour of its own — the cell keeps the table's text colour; the dotted
# underline and the pointer are the affordance (matrix.DOSSIER_LINK_CLASS).
_GAMMA_LINK_CLASS = ("cursor-pointer underline decoration-dotted underline-offset-4 "
                     "hover:decoration-solid")


def gamma_symbol_slot(linked):
    """The symbol cell: a link to that symbol's Dealer Positioning where the
    route exists, else plain text (the public screen). PURE."""
    if not linked:
        return '<q-td :props="props">{{ props.value }}</q-td>'
    return (
        '<q-td :props="props">'
        f'<span class="{_GAMMA_LINK_CLASS}" '
        f"@click.stop=\"() => $parent.$emit('{GAMMA_EVENT}', props.row.symbol)\">"
        "{{ props.value }}<q-tooltip>Open Dealer Positioning</q-tooltip></span>"
        "</q-td>")


# The words a reader sees, keyed by the words the service sends. The KEYS are
# the ``options_svc`` contract, the ``config/flow_alerts.toml`` section names and
# ``_TONE``'s own keys, and they do not change — that separation is what lets the
# label be chosen for the reader rather than for the detector.
#
# And these name the EVENT, not the detector. "Big delta · Call" said which of
# the four things the service runs produced the row; it said nothing about what
# the market did, which is the only reason the row is on screen.
# ⚠ Every one of these is a NOUN PHRASE, and "Hedging flip" is not a typo for
# "Hedging flipped". ``voice.flow_phrase`` builds its contract-less form as
# f"{kind} alert" — the form a gamma flip ALWAYS takes, since it names no
# contract — and a clause there speaks as "Hedging flipped alert, now damping."
# The two HIRO kinds follow the same rule, and the reversal is "Hedging
# reversal" — never "flip", which is already the gamma flip's word.
_KIND_LABEL = {"crossover": "Premium shift", "uoa": "Unusual volume",
               "gamma_flip": "Hedging flip", "big_delta": "Outsized bet",
               "hiro_surge": "Hedging surge", "hiro_flip": "Hedging reversal"}
# ⚠ The two gamma sides had to move WITH their kind. "Hedging flipped · To
# positive" would read worse than the name it replaced: "to positive" is only
# interpretable once you already know the subject is gamma sign, and that is
# precisely the word the new kind name takes away.
#
# The call/put four are untouched, and deliberately literal. For those rows this
# page can say WHICH SIDE traded and never who initiated — Schwab publishes no
# time-and-sales tape to this app — so "Call" must keep meaning the contract
# class and nothing more.
#
# ⚠ The HIRO sides are the one exception, and an honest one only because they
# are a MODEL: options_svc infers each print's initiator from where it sits
# against the quote and turns that into the stock dealers would have to trade
# to hedge it. "Dealers buying" is that model's estimate, not an observed trade,
# and the row's own text and the help guide say so.
_SIDE_LABEL = {"calls_over": "Calls over", "puts_over": "Puts over",
               "call": "Call", "put": "Put",
               "to_positive": "Now damping", "to_negative": "Now amplifying",
               "dealers_buying": "Dealers buying", "dealers_selling": "Dealers selling",
               "to_buying": "Now buying", "to_selling": "Now selling"}

# Direction → a FIXED Tailwind class (the finite-set mapping the UI standard
# requires — never a computed color, never an inline style).
_TONE_POS = "text-emerald-400"
_TONE_NEG = "text-rose-400"
_TONE_NEUTRAL = "text-slate-300"
# big_delta measures exposure changing hands, not a bullish/bearish claim (option
# volume is unsigned) — it gets its own hue rather than reusing pos/neg.
_TONE_BIG_DELTA_CALL = "text-violet-400"
_TONE_BIG_DELTA_PUT = "text-fuchsia-400"
_TONE = {
    ("crossover", "calls_over"): _TONE_POS,
    ("crossover", "puts_over"): _TONE_NEG,
    ("uoa", "call"): _TONE_POS,
    ("uoa", "put"): _TONE_NEG,
    # A gamma flip to POSITIVE means dealer hedging damps volatility, to NEGATIVE
    # that it amplifies it — constructive vs risky rather than bullish vs bearish,
    # but the same two colors carry it.
    ("gamma_flip", "to_positive"): _TONE_POS,
    ("gamma_flip", "to_negative"): _TONE_NEG,
    ("big_delta", "call"): _TONE_BIG_DELTA_CALL,
    ("big_delta", "put"): _TONE_BIG_DELTA_PUT,
    # Dealers BUYING stock to hedge is upward pressure on price; selling, downward.
    ("hiro_surge", "dealers_buying"): _TONE_POS,
    ("hiro_surge", "dealers_selling"): _TONE_NEG,
    ("hiro_flip", "to_buying"): _TONE_POS,
    ("hiro_flip", "to_selling"): _TONE_NEG,
}

# Colored cells bind the stamped ``_tone_class`` field via :class (Tailwind-first
# — no inline style). Raw <q-td> template, the scanner.py add_slot idiom.
_TONE_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._tone_class">{{ props.value }}</span>
  </q-td>
'''

# Share cell: render the numeric share_pct as "35%" (big_delta), or a muted dash
# for the other alert types (which carry no share).
_SHARE_SLOT = r'''
  <q-td :props="props" class="text-right">
    <span v-if="props.value != null">{{ props.value }}%</span>
    <span v-else class="text-grey-6">—</span>
  </q-td>
'''


def alert_kind_label(a):
    return _KIND_LABEL.get((a or {}).get("type"), "Flow")


def side_label(a):
    return _SIDE_LABEL.get((a or {}).get("side"), "")


def tone_class(a):
    d = a or {}
    return _TONE.get((d.get("type"), d.get("side")), _TONE_NEUTRAL)


def _money(v):
    """Compact dollars: $2.13M / $400k / $912. '' when unusable."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    a = abs(v)
    if a >= 999_500:            # rounds to >= $1.00M -> use the M form
        return f"${v/1e6:.2f}M"
    if a >= 1e3:
        return f"${v/1e3:.0f}k"
    return f"${v:,.0f}"


def _hiro_money(v):
    """Signed dollars with a B form ($SPX hedging runs to billions): $2.40B /
    -$310.00M / -$4k. '' when unusable. Page-local: Tier 1 cannot import the
    service's formatter. A value that ROUNDS to zero carries no sign — "-$0"
    would read as a direction the number does not have."""
    v = _fmt.num(v)
    if v is None:
        return ""
    if abs(v) >= 999_500_000:            # rounds to >= $1.00B -> use the B form
        body = f"${abs(v)/1e9:.2f}B"
    else:
        body = _money(abs(v))
    return ("-" + body) if v < 0 and body != "$0" else body


def _approx(money):
    """``≈`` in front of a modelled dollar figure. A negative takes a space
    (``≈ -$310.00M``) so the sign stays legible rather than fusing with the
    ``≈`` into one glyph-cluster; a positive takes none (``≈$2.40B``)."""
    return f"≈ {money}" if money.startswith("-") else f"≈{money}"


# Every HIRO detail cell ends with this. The Desk's flow panel and the Symbol
# page draw ``detail``, not the alert's own text, so the "this is a model"
# qualifier must ride the detail or those two surfaces lose it.
_HIRO_MODEL = "model"


def _hiro_detail(d):
    """The two HIRO detail cells. A missing or non-finite reading DROPS its
    clause rather than printing an invented zero ("0% unlabelled", "spot 0");
    the headline dollar figure is the one thing the row cannot do without.
    Dollar figures carry ``≈`` and the cell ends in "model": every number here
    is an estimate of hedging nobody observed."""
    if d.get("type") == "hiro_surge":
        impact = _fmt.num(d.get("impact"))
        if impact is None:
            return ""
        head = _approx(_hiro_money(abs(impact)))
        window = _fmt.num(d.get("window_min"))
        if window is not None and window > 0:
            head += f" in {window:g} min"
        parts = [head]
        mult = _fmt.num(d.get("mult"))
        if mult is not None:
            parts.append(f"{mult:.1f}× normal")
        share = _fmt.num(d.get("unclassified_share"))
        if share is not None:
            parts.append(f"{share:.0%} unlabelled")
        parts.append(_HIRO_MODEL)
        return " · ".join(parts)
    cum = _fmt.num(d.get("cum"))
    if cum is None:
        return ""
    parts = [f"running total {_approx(_hiro_money(cum))}"]
    spot = _fmt.num(d.get("spot"))
    if spot is not None:
        parts.append(f"spot {spot:g}")
    parts.append(_HIRO_MODEL)
    return " · ".join(parts)


def _exp_short(expiry, dte):
    if dte == 0:
        return "0DTE"
    try:
        _, m, d = str(expiry).split("-")
        return f"{int(m):02d}/{int(d):02d}"
    except (AttributeError, TypeError, ValueError):
        return str(expiry or "")


def alert_detail(a):
    """The type-specific detail cell. Total — missing fields yield ''."""
    d = a or {}
    t = d.get("type")
    try:
        if t == "crossover":
            cp, pp = _money(d.get("call_prem")), _money(d.get("put_prem"))
            if not cp or not pp:
                return ""
            return f"{cp} calls vs {pp} puts"
        if t == "uoa":
            if d.get("strike") is None or d.get("volume") is None:
                return ""
            cp = "C" if d.get("side") == "call" else "P"
            return (f"{_exp_short(d.get('expiry'), d.get('dte'))} {float(d['strike']):g}{cp} · "
                    f"{int(d['volume']):,} vol / {int(d.get('oi') or 0):,} OI "
                    f"({float(d.get('vol_oi') or 0):.1f}×) · {_money(d.get('premium'))}")
        if t == "gamma_flip":
            spot, flip = d.get("spot"), d.get("flip")
            if spot is None or flip is None:
                return ""
            return f"spot {float(spot):g} vs flip {float(flip):g}"
        if t == "big_delta":
            if d.get("strike") is None or d.get("delta_notional") is None:
                return ""
            cp = "C" if d.get("side") == "call" else "P"
            pct = d.get("pct_of_gross")
            pct_txt = f"{float(pct):.0%}" if isinstance(pct, (int, float)) else "—"
            return (f"{_exp_short(d.get('expiry'), d.get('dte'))} {float(d['strike']):g}{cp} · "
                    f"{_money(d.get('delta_notional'))} · {pct_txt} of gross")
        if t in ("hiro_surge", "hiro_flip"):
            return _hiro_detail(d)
    except (TypeError, ValueError):
        return ""
    return ""


def fmt_time(ts):
    """Unix seconds → Central 'HH:MM:SS'. '' when unusable."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return ""
    try:
        return _dt.datetime.fromtimestamp(ts, tz=_CT_TZ).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def age_text(ts, now):
    """How long ago the alert fired: 'just now' / '2m ago' / '1h 14m ago'.

    Clamped at zero — a ts slightly in the future (service/GUI clock skew) reads
    'just now', never a negative age."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return ""
    try:
        secs = now.timestamp() - float(ts)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return ""
    if secs < 60:
        return "just now"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m ago"
    return f"{mins // 60}h {mins % 60}m ago"


def _share_pct(a):
    """big_delta's share of its symbol's gross delta-notional as a NUMBER (35.0 =
    35%), for the sortable Share column. None for other alert types / a missing or
    invalid share — so the column stays blank there and sorts them to one end."""
    if not isinstance(a, dict) or a.get("type") != "big_delta":
        return None
    p = a.get("pct_of_gross")
    if not isinstance(p, (int, float)) or isinstance(p, bool):
        return None
    return round(float(p) * 100, 1)


def _hiding():
    """True when this render must hide non-public alerts: on the public origin,
    and in a screenshot session of the private app (its pictures are published
    to the neuralstrike.co gallery). Imported lazily, as ``render`` does."""
    import shell as _shell
    return _shell.hides_non_public()


def _shown(alerts):
    """The alerts THIS render may display. While hiding (see ``_hiding``) an
    alert the service stamped ``public: False`` (HIRO until ``[hiro].public`` is
    on) is dropped; an alert with no ``public`` key stays, which is every older
    type. Otherwise the list comes back UNCHANGED.

    This is the one filter: ``alert_rows`` (the Flow page, the Desk panel and
    its speech, the Symbol band) and ``status_text``'s count both go through
    it, so a hidden alert cannot reach a published row OR count here.

    ⚠ Call it on the event loop, inside a page build or a timer callback. The
    capture check reads the current client's request; a worker thread
    (``run.io_bound``) has no client, so a gallery capture would quietly stop
    hiding. The public origin is unaffected (``is_public`` is process state)."""
    if not isinstance(alerts, list) or not _hiding():
        return alerts
    return [a for a in alerts
            if not (isinstance(a, dict) and a.get("public") is False)]


def alert_rows(view):
    """Display rows, NEWEST FIRST. The service appends oldest-first.

    Total over a missing/malformed view — ``render()`` does no validation."""
    alerts = (view or {}).get("alerts") if isinstance(view, dict) else None
    if not isinstance(alerts, list):
        return []
    rows = []
    for a in _shown(alerts):
        if not isinstance(a, dict):
            continue
        ts = a.get("ts")
        ts = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
        rows.append({
            # row_key: a missing/duplicate id would collapse rows in the table,
            # so fall back to a positional key rather than an empty string.
            "id": a.get("id") or f"{a.get('symbol', '')}|{len(rows)}",
            "ts": ts,
            "time": fmt_time(ts),
            "age": "",                      # filled by the live age tick
            "symbol": a.get("symbol", ""),
            "kind": alert_kind_label(a),
            "_kind_key": a.get("type") or "",
            "side": side_label(a),
            # The contract itself, carried RAW for the Desk's spoken alert
            # (``voice.flow_phrase`` hardens them; ``detail`` below is the
            # printed form). Additive — no column declares them. They ride this
            # row rather than being read a second time out of the raw payload,
            # because two readers of one payload is how the documented
            # sectors-vs-rotation split happened. Absent for the alert kinds
            # that name no contract, and absent means ``None``: a ``dte`` of 0
            # means 0DTE, which is the last value to manufacture from a gap.
            "strike": a.get("strike"),
            "expiry": a.get("expiry"),
            "dte": a.get("dte"),
            "detail": alert_detail(a),
            "share_pct": _share_pct(a),     # numeric % for the sortable Share column
            "text": a.get("text", ""),
            "_tone_class": tone_class(a),
            # Stamped by the service: an unvalidated alert the Desk must not
            # speak (``desk.fold_flow_arrivals``). Only a real True silences;
            # an alert with no flag speaks as it always has.
            "quiet": a.get("quiet") is True,
        })
    rows.reverse()
    return rows


# The kinds whose alerts the service may stamp ``public: False`` today.
_NON_PUBLIC_KINDS = ("hiro_surge", "hiro_flip")


def kind_options(rows, hiding):
    """The "Alert type" picker's options. While hiding non-public alerts, a
    HIRO kind with no VISIBLE row is left out, so the public picker never
    offers a type it will never show; once ``[hiro].public`` puts one on screen
    its kind comes back. Otherwise every kind. PURE."""
    if not hiding:
        return dict(_KIND_LABEL)
    seen = {r.get("_kind_key") for r in rows or () if isinstance(r, dict)}
    return {k: v for k, v in _KIND_LABEL.items()
            if k not in _NON_PUBLIC_KINDS or k in seen}


# ── the "Alert type" chips, and remembering them ────────────────────────────
# The reader's choice is stored as the types switched OFF (``app_settings``),
# never the types switched on: a type that did not exist when the choice was
# saved — a new detector, or a HIRO kind the public picker had left out — is
# then SHOWN, the same default every type gets on a first visit. Stored the
# other way round, a new type would arrive hidden and the page would quietly
# drop the one alert nobody had seen before.
HIDDEN_KINDS_KEY = "flow_hidden_kinds"


def parse_hidden_kinds(raw):
    """The saved hidden set, keeping only kinds this page knows. Anything else
    — a missing key, a hand-edited string, a retired kind — reads as "nothing
    hidden", never as an error. PURE."""
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(k for k in raw if isinstance(k, str) and k in _KIND_LABEL)


def toggle_kind(hidden, key):
    """The hidden set after clicking ``key``'s chip; ``key=None`` is the All
    chip, which shows everything. Never mutates ``hidden``. PURE."""
    if key is None:
        return frozenset()
    return frozenset(set(hidden) ^ {key})


def shown_kinds(hidden):
    """The kind keys ``filter_rows`` keeps for a hidden set. PURE."""
    return {k for k in _KIND_LABEL if k not in hidden}


def kind_chips(rows, options, hidden, symbol):
    """One chip per offered kind: ``(key, label, count, active)``, in the
    picker's order, the count being today's alerts of that kind for the chosen
    symbol (all symbols when none is). A kind with no alerts still gets its
    chip — a zero is the answer to "did any trade?", and a chip that appears
    and vanishes with the tape would move every other chip under the cursor.
    PURE."""
    counts = {}
    for r in rows or ():
        if not isinstance(r, dict) or (symbol and r.get("symbol") != symbol):
            continue
        k = r.get("_kind_key")
        counts[k] = counts.get(k, 0) + 1
    return [(k, label, counts.get(k, 0), k not in hidden)
            for k, label in (options or {}).items()]


def filter_rows(rows, kinds, symbol):
    """Rows matching the selected kind keys and symbol.

    ``kinds=None`` and ``symbol=None`` mean unfiltered; an EMPTY kinds set means
    the user deselected everything and should see nothing."""
    out = []
    for r in rows or []:
        if kinds is not None and r.get("_kind_key") not in kinds:
            continue
        if symbol and r.get("symbol") != symbol:
            continue
        out.append(r)
    return out


def symbol_options(rows):
    """Sorted distinct symbols present in today's alerts (for the filter dropdown)."""
    return sorted({r.get("symbol") for r in rows or [] if r.get("symbol")})


def status_text(view):
    """Status line. Distinguishes a quiet day from a service that isn't publishing —
    on an empty table those look identical otherwise.

    The quiet-day line describes the MARKET, not this page: "No flow alerts yet
    today" reads as a screen with nothing on it, where "Nothing unusual has
    traded yet today" reads as a tape that has done nothing — the true statement,
    and already the Desk's wording for its own empty flow panel."""
    if not isinstance(view, dict) or not view:
        return _copy.WAITING_OPTIONS
    n = len(_shown(view.get("alerts")) or [])
    date = view.get("date") or ""
    if not n:
        return f"Nothing unusual has traded yet today · {date}".rstrip(" ·")
    return f"{n} alert{'' if n == 1 else 's'} today · {date}".rstrip(" ·")


def filtered_status(base, total, shown):
    """The status line with the filter's effect appended when it hides rows —
    "9 alerts today · 2026-10-02 · 5 shown". Since the hidden types are
    remembered, a reader coming back tomorrow would otherwise read the day's
    count over a shorter table with nothing to say why. PURE."""
    if shown < total:
        return f"{base} · {shown} shown"
    return base


def flow_columns():
    # "Alert type" and "What traded" are the DESK's words for these same two
    # quantities — its flow panel prints both — because one number labelled two
    # ways on two screens is the drift that page exists to avoid. "Alert" became
    # "Summary": it sat directly beside "Alert type" and named a different thing.
    spec = [("time", "Time"), ("age", "Age"), ("symbol", "Symbol"),
            ("kind", "Alert type"), ("side", "Side"),
            ("detail", "What traded"), ("text", "Summary")]
    cols = [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]
    # Share = big_delta's % of its symbol's gross delta-notional (numeric, so the
    # table sorts by conviction — click it to rank the day's fires biggest-first).
    # Blank for the other alert types. Sits just before the Alert text.
    # "Share" alone never said share OF WHAT.
    cols.insert(6, {"name": "share", "label": "Share of flow",
                    "field": "share_pct", "sortable": True, "align": "right"})
    return cols


def render():
    """Build the Flow Alerts page: today's alerts newest-first, version-polling
    ``cache:options:flow_alerts``.

    Tier-1 (engine-free). Two cadences share one 2 s timer: the payload is re-read
    only when the cache VERSION moves, while the Age column is recomputed every
    tick against the rows already on screen — so age stays live without churning
    the table."""
    import app_settings
    import bus_client
    import shell as _shell
    from nicegui import run, ui

    from pages import ui_kit as kit
    from pages.ui_guard import guard, guard_async

    from .handoff import GAMMA_ROUTE, send_to_gamma
    from .swing import strategy_chip   # the app's one filter-chip look

    # The hidden types come back from the settings store, so a reload — or a
    # new tab — keeps what the reader switched off. On the public origin the
    # store is frozen: every visitor starts from the default and a click there
    # is never written (``app_settings.set`` is a no-op).
    state = {"version": None, "rows": [], "symbol": None, "chips": None,
             "status": _copy.WAITING_OPTIONS,
             "hidden": parse_hidden_kinds(app_settings.get(HIDDEN_KINDS_KEY))}

    # No description line: the standard keeps the body to the header, the
    # filters, the status line and the table. What it said — today's alerts,
    # newest first, and where a click goes — lives in the page help
    # (``page_help.HELP_MD["/options/flow"]``).
    linked = _shell.can_navigate(GAMMA_ROUTE)
    # One answer per page: the origin is process state and a client's request
    # (the capture cookie) does not change for the life of the page.
    hiding = _shell.hides_non_public()
    with kit.page():
        kit.header("Flow Alerts", view=VIEW)
        with kit.control_bar():
            symbol_sel = kit.select_field("Symbol", ["All"], value="All", width="w-40")
            # One chip per alert type, each carrying today's count — the
            # Strategy Finder's filter row. It replaced a multi-select whose
            # chips wrapped inside a fixed-width box: six names in a 288px
            # field read as a pile, not a control. ``min-h-10`` matches the
            # dense input beside it, so the two sit on one line; ``basis-80``
            # sends the group onto a line of its own on a phone rather than
            # squeezing it into a column beside the Symbol box.
            with kit.field("Alert type") as kind_field:
                kind_field.classes("flex-1 basis-80 min-w-0")
                chips_row = ui.row().classes("min-h-10 items-center gap-2 flex-wrap")
        status = kit.status_line(_copy.WAITING_OPTIONS)
        # Today's alerts arrive as one payload; until it lands an empty table
        # reads as "a quiet session" rather than "not loaded yet".
        region = kit.region("Loading today's alerts…")
        with region.content:
            table = kit.table(flow_columns(), numeric=("share",))
    table.add_slot("body-cell-symbol", gamma_symbol_slot(linked))
    table.add_slot("body-cell-side", _TONE_SLOT)
    table.add_slot("body-cell-text", _TONE_SLOT)
    table.add_slot("body-cell-share", _SHARE_SLOT)

    @guard
    def _open_gamma(e):
        """The symbol cell's click. Re-checks the symbol — the event carries
        whatever the browser sent, and the handed-off symbol reaches a chain
        fetch and a Redis key name — then hands off through the seam, which is
        a no-op on an origin that serves no Dealer Positioning."""
        sym = clean_symbol(e.args) if isinstance(e.args, str) else None
        if sym is not None:
            send_to_gamma(sym)

    if linked:
        table.on(GAMMA_EVENT, _open_gamma)

    def _paint_chips():
        """Rebuild the chip row — only when a count or a state moved, since
        the 2 s age tick reaches here too."""
        chips = kind_chips(state["rows"], kind_options(state["rows"], hiding),
                           state["hidden"], state["symbol"])
        if chips == state["chips"]:
            return
        state["chips"] = chips
        chips_row.clear()
        with chips_row:
            strategy_chip(f"All {sum(n for _k, _l, n, _a in chips)}",
                          active=all(a for _k, _l, _n, a in chips),
                          on_click=lambda: _on_chip(None)).classes("whitespace-nowrap")
            for key, label, n, active in chips:
                strategy_chip(f"{label} {n}", active=active,
                              on_click=lambda k=key: _on_chip(k)) \
                    .classes("whitespace-nowrap")

    def _apply_filters():
        _paint_chips()
        table.rows = filter_rows(state["rows"], shown_kinds(state["hidden"]),
                                 state["symbol"])
        table.update()
        status.text = filtered_status(state["status"], len(state["rows"]),
                                      len(table.rows))
        region.busy.hide()

    def _tick_age():
        now = _dt.datetime.now(tz=_CT_TZ)
        for r in state["rows"]:
            r["age"] = age_text(r.get("ts"), now)
        _apply_filters()

    def _paint(payload):
        state["rows"] = alert_rows(payload)
        opts = ["All"] + symbol_options(state["rows"])
        if list(symbol_sel.options) != opts:
            symbol_sel.options = opts
            # A symbol that stopped appearing today would otherwise leave the table
            # permanently empty with no visible cause.
            if state["symbol"] is not None and state["symbol"] not in opts:
                state["symbol"] = None
                symbol_sel.value = "All"
            symbol_sel.update()
        state["status"] = status_text(payload)
        _tick_age()

    @guard
    def _on_chip(key):
        state["hidden"] = toggle_kind(state["hidden"], key)
        app_settings.set(HIDDEN_KINDS_KEY, sorted(state["hidden"]))
        _apply_filters()

    @guard
    def _on_symbol_change(e):
        state["symbol"] = None if e.value in (None, "All") else e.value
        _apply_filters()

    symbol_sel.on_value_change(_on_symbol_change)

    @guard_async
    async def _poll():
        # Cheap :ver probe off the loop; the full payload read only on a change.
        v = await run.io_bound(bus_client.read_version, VIEW)
        if v is not None and v != state["version"]:
            payload = await run.io_bound(bus_client.read, VIEW)
            if payload:
                state["version"] = v
                _paint(payload)
                return
        _tick_age()      # no new data — just keep the ages honest

    payload, version = bus_client.read_full(VIEW)
    if payload:
        state["version"] = version
        _paint(payload)
    else:
        _paint_chips()
        region.busy.show()
    ui.timer(2.0, _poll)
