"""A standalone, live-streaming mirror of ``/desk`` — no NiceGUI, no websocket.

``/desk`` is a NiceGUI page: it needs the framework's socket, its Vue runtime and
a live client connection per tab. That is the right trade for the screen you sit
in front of and click through, and the wrong one for the second monitor, the wall
display or the phone propped against the keyboard — all of which want a page that
paints, updates and survives a laptop sleeping with nothing to reconnect but an
HTTP request.

So this module serves the same screen as **one static HTML document plus a
server-sent event stream**:

    GET /desk/live     the document (styling, layout and a few dozen lines of JS)
    GET /desk/stream   ``text/event-stream`` — ``desk`` events on cache change,
                       ``clock`` events every second

**The load-bearing rule is the one ``pages/desk.py`` already states about itself:
this composes, it never restates.** Every number below comes out of a builder the
Desk itself calls — ``dealer_rows``, ``flow_rows``, ``positions_summary``,
``summary_line``, ``freshness_facts``, every ``fmt_*``. A mirror that re-derived
so much as a rounding rule would be a second screen quietly disagreeing with the
first, which is worse than no mirror; this app already carries one documented bug
of exactly that shape (``/sentiment/sectors`` vs ``/sentiment/rotation``).

``snapshot`` is therefore PURE and returns display-ready **strings** — plain
dicts in, a JSON-safe dict out — so the whole mirror is unit-tested without a
browser and without Redis, and the client JS has no formatting logic to drift
with. The only things the browser decides are layout and colour placement.

⚠ This file is deliberately OUTSIDE the Tailwind-first standard, which binds
NiceGUI components with ``.classes()``. It emits a raw ``HTMLResponse`` document
— the documented out-of-scope case, the same one the EOD report and the Gamma
Explain infographic sit in — so it carries its own ``<style>`` block. Its palette
is still read out of ``pages/options/theme`` rather than hand-picked.
"""
import asyncio
import datetime
import functools
import json
import re

from pages import console_cards as _CC
from pages import bullbear as _bb
from pages import desk as _d
from pages.options import flow as _flow
from pages.options import matrix as _matrix

# The trading clock, and the Desk's own — not a second reading of "now".
_CT = _d._CT

# The two routes this module owns. Constants because ``main`` registers them and
# ``document`` embeds one of them in the client JS; a literal in three places is
# how a rename half-lands.
PAGE_ROUTE = "/desk/live"
STREAM_ROUTE = "/desk/stream"

# The Desk's own view list, never a copy: a view added to that page joins this
# stream with no edit here, which is the whole point of importing it.
VIEWS = _d.VIEWS

# How often the stream emits, and how often it looks for new data. The clock tick
# doubles as the SSE keep-alive — a proxy or a sleeping laptop drops an idle
# event stream, and a page whose countdown had frozen would look identical to one
# whose data had. ``POLL_EVERY`` puts the version probe on the Desk's own 2 s
# cadence rather than inventing a second one.
CLOCK_SEC = 1.0
POLL_EVERY = 2

# The countdown is computed SERVER-side and pushed, rather than run in JS off a
# target timestamp. Every session bound in this app comes from
# ``shared.market_calendar`` — holidays included, derived rather than listed —
# and a JS countdown would need its own copy of the NYSE calendar to know what it
# was counting to. One second of bandwidth is cheaper than a second calendar.

_DASH = _d._DASH
_C = _d._C                                    # the console palette, raw hexes


# ── colour: resolving the app's finite Tailwind class maps to hexes ──────────
# The page modules express a data-driven colour as a FIXED Tailwind class, which
# is what the styling standard requires of them — and which this document cannot
# render, because it ships no Tailwind. So the named colours those maps use are
# resolved here, ONCE, against Tailwind's published palette.
#
# This is a translation table, not a second palette: the semantic maps (which
# quadrant is amber, which alert type is violet) stay where they live, and only
# the name→hex step happens here. ``FLOW_TONE_HEX`` below is the guard that keeps
# it honest.
TAILWIND_HEX = {
    "white": "#ffffff",
    "emerald-200": "#a7f3d0", "emerald-300": "#6ee7b7",
    "emerald-400": "#34d399", "emerald-600": "#059669",
    "amber-300": "#fcd34d", "amber-400": "#fbbf24",
    "rose-300": "#fda4af", "rose-400": "#fb7185", "rose-600": "#e11d48",
    "slate-200": "#e2e8f0", "slate-300": "#cbd5e1", "slate-400": "#94a3b8",
    "slate-500": "#64748b", "slate-600": "#475569",
    "violet-400": "#a78bfa", "fuchsia-400": "#e879f9",
}

# ``text-emerald-200/80`` — the opacity suffix is matched and DROPPED: this
# resolves a HUE, and the alpha in those class strings is chrome the mirror
# re-states in its own CSS rather than data anyone reads.
_TW_TOKEN = (r"(?:^|\s){}-(\[#[0-9a-fA-F]{{3,8}}\]|[a-z]+-\d{{2,3}}|white)"
             r"(?:/[\d.]+)?(?=\s|$)")


@functools.lru_cache(maxsize=None)
def _token_re(prefix):
    return re.compile(_TW_TOKEN.format(re.escape(prefix)))


def tw_hex(classes, prefix="text"):
    """The hex a Tailwind ``{prefix}-…`` token in ``classes`` resolves to.

    Handles both halves of what this app's class maps actually contain: a named
    palette entry (``text-rose-400``) and an arbitrary value (``text-[#eaf2f9]``).
    Returns ``None`` for a class string carrying no such token, or one naming a
    colour ``TAILWIND_HEX`` does not know — deliberately, so the coverage tests
    below FAIL rather than silently painting a new state in a fallback hue.
    """
    m = _token_re(prefix).search(" " + str(classes or ""))
    if not m:
        return None
    token = m.group(1)
    if token.startswith("["):
        return token[1:-1]
    return TAILWIND_HEX.get(token)


def _hue(classes, prefix="text", default=None):
    """``tw_hex`` with a fallback — for the places a miss must not blank a cell."""
    return tw_hex(classes, prefix) or (default or _C["muted"])


# The Flow Alerts page stamps every alert with a tone class from its own finite
# (type, side) map; this resolves that map's five classes. Built from
# ``flow._TONE`` rather than re-listed, so a NEW alert type appears here
# automatically — as a ``None``, which is what
# ``test_every_flow_tone_the_flow_page_can_stamp_has_a_hex`` catches.
FLOW_TONE_HEX = {cls: tw_hex(cls) for cls in
                 set(_flow._TONE.values()) | {_flow._TONE_NEUTRAL}}


def band_tone_hex(cls):
    """A BIAS/SIGNAL tile's colour, RESOLVED from the class the Desk stamps.

    ``desk.signal_band_facts`` already decided the tone — from the console's own
    ``_word_tone`` — so this resolves ``text-[#35d68a]`` to its hex rather than
    re-deciding which word is bullish. That is the difference between a mirror
    and a second opinion.
    """
    return _hue(cls)


def quadrant_hex(quadrant):
    """A Bull/Bear quadrant's colour, from ``bullbear``'s own chip classes."""
    return _hue(_bb.quadrant_class(quadrant))


def signal_hex(signal):
    """An Opportunity Board signal chip's colour, from ``matrix``'s own map.

    The board's chip is a FILLED badge, so it is the background token that
    carries the hue; the mirror paints it as a tint of the same hex.
    """
    return _hue(_matrix.signal_class(signal), prefix="bg")


# ── the pure snapshot ────────────────────────────────────────────────────────
def _mapping(payloads, view):
    """A view narrowed to a dict — ``{}`` for anything else.

    ``payloads.get(view) or {}`` is NOT enough, for the reason ``desk.render``'s
    own ``_mapping`` spells out: a half-written key or a service caught
    mid-restart yields a truthy malformed payload, and the ``or`` hands it
    straight to the first ``.get``.
    """
    v = payloads.get(view)
    return v if isinstance(v, dict) else {}


def clock_facts(now):
    """``{"label", "text"}`` — how much session is left, per the Desk's clock."""
    facts = _d.countdown_facts(now)
    return {"label": facts["label"], "text": facts["text"]}


def _num_text(value, digits=0):
    """An arc's 0-100 reading as text, or an em-dash when there is none.

    A missing horizon prints the dash rather than a 0 — the ring on ``/desk``
    draws that arc's track only for the same reason, and a 0 here would be a
    maximally bearish score nobody measured.
    """
    return _DASH if value is None else "{:.{d}f}".format(value, d=digits)


def _score_card(title, arcs, pill, delta):
    """One of the two hero cards, as data. ``delta`` is ``delta_parts``' tuple."""
    return {
        "title": title,
        "pill": pill,
        "arcs": [{"caption": a.get("caption", ""), "value": a.get("value"),
                  "text": _num_text(a.get("value"))} for a in arcs],
        "delta": (None if delta is None
                  else {"arrow": delta[0], "text": delta[1], "tone": delta[2]}),
    }


def _cards(payloads):
    comp = _mapping(payloads, "sentiment:composite")
    hist = _mapping(payloads, "sentiment:history")
    snaps = hist.get("snaps")
    snaps = snaps if isinstance(snaps, list) else []
    derived = comp.get("derived")
    derived = derived if isinstance(derived, dict) else {}
    live = comp.get("live")

    # Day vs WEEK on Sentiment, Day vs MONTH on Trend — the console's pairing,
    # and the Desk's. Each names the horizon its card is actually judged against.
    sent = _d._sentiment_arcs(live, snaps)
    trend = _d._trend_arcs(derived)
    return [
        _score_card("MARKET SENTIMENT", sent, _d.sentiment_pill_text(live, snaps),
                    _CC.delta_parts(_d._arc_value(sent, 0),
                                    _d._arc_value(sent, 1), "WEEK")),
        _score_card("MARKET TREND", trend, _d.trend_pill_text(derived),
                    _CC.delta_parts(_d._arc_value(trend, 0),
                                    _d._arc_value(trend, 2), "MONTH")),
    ]


def _regime(payloads):
    """The Market Regime word, its qualifier and the tone it is painted in.

    Colour follows the direction the service COMMITTED, and only then: a fixed
    green would paint "Retreating" as though it were bullish. A withheld
    confidence prints nothing — never 0%, which is a reading, and "absent" is
    not one.
    """
    reg = _d.regime_display(payloads.get("sentiment:regime"))
    tone = _C["muted"] if reg["unclear"] else _C["text"]
    if not reg["unclear"] and reg["direction"]:
        tone = _C["positive"] if reg["direction"] > 0 else _C["negative"]
    conf = reg["confidence"]
    return {"word": reg["word"], "tone": tone,
            "sub": "" if conf is None else "confidence {:.0f}%".format(conf * 100)}


def _freshness(payloads):
    """Is the GEX feed current — the Desk's honest replacement for a green dot.

    No probe data reads "unknown", never "live": ``stale`` is what gates the
    dealer walls, and a wrong "live" would promote off-hours walls (drawn from an
    all-zero, open-interest-less grid) back to trustworthy.
    """
    f = _d.freshness_facts(payloads.get("options:gex_status"))
    return {"text": f["label"], "stale": f["stale"],
            "tone": _C["warning"] if f["stale"] else _C["positive"]}


def _band(payloads):
    """BIAS and SIGNAL — the two tiles that replaced VIX on the strip.

    Straight off ``desk.signal_band_facts``, labels and descriptors included, so
    the mirror cannot describe the composite's verdict in different words than
    the page it mirrors.
    """
    comp = _mapping(payloads, "sentiment:composite")
    derived = comp.get("derived")
    return [{"label": f["label"], "value": f["value"],
             "descriptor": f["descriptor"], "tone": band_tone_hex(f["cls"])}
            for f in _d.signal_band_facts(derived)]


def _dealer(payloads, stale):
    """The dealer-positioning panel: four symbols, and why the walls may be gone.

    A silently wall-less row reads as a broken page. The ``warning`` line reads
    as a stopped feed, which is what it is.
    """
    view = payloads.get("options:matrix")
    if view is None:
        return {"rows": [], "note": _d.WAITING_OPTIONS, "warning": ""}
    rows = _d.dealer_rows(view, stale)
    fresh = _d.freshness_facts(payloads.get("options:gex_status"))
    out = []
    for r in rows:
        out.append({
            "symbol": r["symbol"],
            "spot": _d.fmt_price(r["spot"]),
            "day_pct": _d.fmt_signed_pct(r["day_pct"]),
            "day_tone": _hue(_d.signed_class(r["day_pct"])),
            "flip": _d.fmt_price(r["flip"]),
            "flip_sub": _d.flip_sub_text(r),
            "flip_tone": _hue(_d.flip_side_class(r["flip_side"])),
            "call_wall": _d.fmt_price(r["call_wall"]),
            "put_wall": _d.fmt_price(r["put_wall"]),
            "net_gex": _d.fmt_gex(r["net_gex"]),
            "net_gex_tone": _hue(_d.signed_class(r["net_gex"])),
            # The em-dash renders BARE, with no chip: an empty outlined box
            # reads as a broken widget rather than as an absent reading.
            "regime_word": r["regime_word"],
            "regime_tone": (_C["muted"] if r["regime_word"] == _d._NO_REGIME
                            else _hue(_d.regime_chip_class(r["regime_word"]))),
            "structure": r["structure"],
        })
    note = "" if out else "No dealer positioning published for these symbols yet."
    warning = ("Walls withheld — GEX feed {}".format(fresh["label"].lower())
               if stale and out else "")
    return {"rows": out, "note": note, "warning": warning}


def _board(payloads):
    view = payloads.get("options:matrix")
    subtitle = "HOTTEST {}".format(_d.BOARD_ROWS_N)
    if view is None:
        return {"rows": [], "note": _d.WAITING_OPTIONS, "subtitle": subtitle}
    rows = []
    for r in _d.opportunity_rows(view):
        rows.append({
            "symbol": r["symbol"],
            "hotness": _d.fmt_hotness(r["hotness"]),
            "rationale": r["rationale"],
            "atm_iv": _d.fmt_iv(r["atm_iv"]),
            "iv_state": r["iv_state"],
            "iv_tone": _hue(_d.iv_state_class(r["iv_state"])),
            "net_prem": _d.fmt_net_prem(r["net_prem_m"]),
            "net_prem_tone": _hue(_d.signed_class(r["net_prem_m"])),
            "pc_ratio": _d.fmt_ratio(r["pc_ratio"]),
            "signal": str(r["signal"]).upper(),
            "signal_tone": signal_hex(r["signal"]),
            # An empty setup tag renders NO chip: an empty cell reads as "no
            # setup", where a "NEUTRAL" chip would read as a finding.
            "setup": r["setup"],
        })
    return {"rows": rows, "subtitle": subtitle,
            "note": "" if rows else "No ranked symbols yet."}


def _flow_panel(payloads):
    view = payloads.get("options:flow_alerts")
    subtitle = "NEWEST {}".format(_d.FLOW_ROWS_N)
    if view is None:
        return {"rows": [], "note": _d.WAITING_OPTIONS, "subtitle": subtitle}
    rows = []
    for r in _d.flow_rows(view):
        rows.append({
            "time": r["time"] or _DASH,
            "symbol": r["symbol"],
            # ``detail`` is the Flow page's own wording for the premium the
            # alert fired on; ``text`` is its fallback sentence.
            "detail": r["detail"] or r["text"] or _DASH,
            # "Call"/"Put" names which side of the book MOVED, never who
            # initiated — Schwab publishes no time-and-sales tape to this app.
            "kind": _d.flow_kind_text(r),
            "tone": FLOW_TONE_HEX.get(r["_tone_class"]) or _C["text"],
        })
    return {"rows": rows, "subtitle": subtitle,
            "note": "" if rows else "No alerts today."}


def _positions(payloads):
    """All three books merged, most actionable first.

    ⚠ The summary reads the FULL book and only the DRAW is capped. Both figures
    it carries are book-level facts, and ``at_risk`` in particular is the one
    number on this panel somebody acts on: computing it off the visible slice
    would report no trades in trouble while trades were in trouble.
    """
    paper = payloads.get("options:paper_account")
    driver = payloads.get("options:driver_paper_account")
    captured = payloads.get("options:captured")
    subtitle = " · ".join(b["source"] for b in _d.BOOKS)
    if paper is None and driver is None and captured is None:
        return {"rows": [], "note": _d.WAITING_OPTIONS, "summary": "",
                "subtitle": subtitle, "at_risk": 0}
    book = _d.position_rows(paper, driver, captured)
    summary = _d.positions_summary(book)
    shown = book[:_d.POSITION_ROWS_N]
    rows = []
    for r in shown:
        rows.append({
            "source": r["source"],
            "source_tone": _hue(_d.source_chip_class(r["source"])),
            "symbol": r["symbol"],
            "strategy": _d.strategy_label(r["strategy"]),
            "expiry": _d.expiry_text(r),
            # Per-share option prices, not position dollars — the numbers the
            # paper ledger itself quotes.
            "entry": _d.fmt_money(r["entry_credit"]),
            "mark": _d.fmt_money(r["current_value"]),
            "strikes": r["strikes"],
            # An em-dash, never a 1: a captured signal was never sized, and a
            # printed quantity would be this page inventing a position.
            "quantity": (_DASH if r["quantity"] is None
                         else "{:g}".format(r["quantity"])),
            "unrealized": _d.fmt_money(r["unrealized_pnl"]),
            "unrealized_tone": _hue(_d.signed_class(r["unrealized_pnl"])),
            "flag": r["flag"],
            "flag_tone": (_C["dim"] if r["flag"] == _d.UNTAGGED_FLAG
                          else _hue(_d.flag_chip_class(r["flag"]))),
            "href": _d.POSITION_ROUTES.get(r["source"], "/options/paper"),
        })
    return {"rows": rows, "subtitle": subtitle,
            "summary": _d.summary_line(summary, len(shown)),
            "at_risk": summary["at_risk"],
            "note": "" if rows else "No open positions."}


def _bullbear(payloads):
    """The eleven-sector strip, and the map's own count sentence above it.

    ``payload["regime"]`` is deliberately never read — the map answers the
    risk-on/risk-off question by counting rows and stopping, and a strip
    pointing at it under a verdict word would reopen a hole this app already
    has one instance of.
    """
    view = payloads.get("sentiment:bullbear")
    chips = _d.bullbear_chips(view)
    out = []
    for c in chips:
        out.append({
            "label": c["label"],
            "day": c["day_text"],
            "quadrant": c["quadrant"],
            "quadrant_label": _bb.quadrant_label(c["quadrant"]),
            "tone": quadrant_hex(c["quadrant"]),
            # None ("no reading at all") and 0 ("nothing confirms") are two
            # different drawings — the first draws NO groove, the second an
            # empty one — so this stays the raw None.
            "breadth": c["breadth"],
            "breadth_tone": _C["negative"] if c["thin"] else _C["positive"],
        })
    return {"chips": out, "headline": _d.bullbear_headline(view),
            "note": "" if out else _d.WAITING_BULLBEAR}


def snapshot(payloads, now):
    """The whole Desk as one JSON-safe dict of display-ready strings.

    ``payloads`` maps a cache view name (``"options:matrix"``) to its payload, or
    omits it entirely when the service has published nothing. A missing view
    yields that region's WAITING note — never an empty panel, and never a zero:
    one dead service must not blank the page, and must not be mistaken for a
    reading of zero either.
    """
    payloads = payloads if isinstance(payloads, dict) else {}
    fresh = _freshness(payloads)
    return {
        "clock": clock_facts(now),
        "freshness": fresh,
        "band": _band(payloads),
        "regime": _regime(payloads),
        "cards": _cards(payloads),
        "bullbear": _bullbear(payloads),
        "dealer": _dealer(payloads, fresh["stale"]),
        "board": _board(payloads),
        "flow": _flow_panel(payloads),
        "positions": _positions(payloads),
    }


# ── the SSE wire format ──────────────────────────────────────────────────────
def sse_frame(event, data):
    """One server-sent event: a name, one ``data:`` line, and a blank line.

    ``json.dumps`` is what guarantees the single line — a raw newline inside the
    payload would split one event into two malformed ones, and every string in a
    snapshot is user-facing text that could legitimately contain one.
    """
    return "event: {}\ndata: {}\n\n".format(
        event, json.dumps(data, separators=(",", ":")))


# ── reading the bus ──────────────────────────────────────────────────────────
def read_versions():
    """The ``{key}:ver`` counter for every Desk view, in ONE pipelined trip."""
    import bus_client
    return bus_client.read_versions(list(VIEWS))


def read_payloads(views=None):
    """Full payloads for ``views`` (default: all of them) → ``{view: payload}``.

    Called with the CHANGED subset on every tick after the first, so a quiet
    session costs one version probe per two seconds and deserializes nothing.
    """
    import bus_client
    return {v: bus_client.read(v) for v in (VIEWS if views is None else views)}


# The sleep is a module attribute so the stream loop can be driven at full speed
# under test. Everything else in ``event_stream`` is real.
_sleep = asyncio.sleep


def _now():
    return datetime.datetime.now(_CT)


async def event_stream(request):
    """Yield SSE frames until the client goes away.

    Opens with a full ``desk`` snapshot, then emits a ``clock`` frame every
    second and a fresh ``desk`` frame whenever a cache version moves. The two
    cadences are separate on purpose: the countdown moves every second and the
    panels move rarely, so pushing the whole screen once a second would be ~50x
    the bytes to animate one tile.

    Every bus read goes through ``asyncio.to_thread`` — ``shared.bus`` is
    blocking, and this generator runs on the event loop that also serves the
    NiceGUI app. A stall here would stall every open tab.
    """
    data = await asyncio.to_thread(read_payloads)
    versions = await asyncio.to_thread(read_versions)
    yield sse_frame("desk", snapshot(data, _now()))

    tick = 0
    while True:
        await _sleep(CLOCK_SEC)
        # Checked BEFORE emitting: a disconnected client is the normal way this
        # generator ends (tab closed, laptop asleep), not an error path.
        if await request.is_disconnected():
            return
        tick += 1
        yield sse_frame("clock", clock_facts(_now()))
        if tick % POLL_EVERY:
            continue
        current = await asyncio.to_thread(read_versions)
        changed = [v for v in VIEWS
                   if current.get(v) is not None
                   and current.get(v) != versions.get(v)]
        if not changed:
            continue
        for v in changed:
            versions[v] = current.get(v)
        data.update(await asyncio.to_thread(read_payloads, changed))
        yield sse_frame("desk", snapshot(data, _now()))


# ── the document ─────────────────────────────────────────────────────────────
# Colours come from the console palette and the resolved Tailwind maps above, so
# this document follows a `config/theme.toml` edit exactly as `/desk` does.
_CSS = """
*, *::before, *::after {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  background: radial-gradient(120% 90% at 50% 0%, #101d2f 0%, #0a1220 55%, #070d18 100%);
  color: {text}; font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular,
  Menlo, monospace; font-size: 14px; min-height: 100vh; padding: 14px;
}}
a {{ color: inherit; text-decoration: none; }}
.wrap {{ display: flex; flex-direction: column; gap: 14px; max-width: 2400px; margin: 0 auto; }}

/* ── masthead ── */
.top {{ display: flex; align-items: center; gap: 14px; }}
.brand {{ font-size: 15px; letter-spacing: .22em; color: {label}; }}
.brand b {{ color: {text}; font-weight: 600; }}
.link {{ font-size: 12px; letter-spacing: .14em; color: {muted};
         border: 1px solid {line}59; border-radius: 2px; padding: 4px 9px; }}
.link:hover {{ color: {text}; border-color: {accent}; }}
.conn {{ margin-left: auto; display: flex; align-items: center; gap: 7px;
         font-size: 11px; letter-spacing: .14em; color: {muted}; }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; background: {muted}; }}
.dot.on {{ background: {positive}; animation: pulse 2.4s ease-in-out infinite; }}
.dot.off {{ background: {negative}; }}
@keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: .35; }} }}

/* ── the strip ── */
.strip {{ display: flex; flex-wrap: wrap; align-items: stretch; gap: 14px; }}
.tile {{ display: flex; flex-direction: column; gap: 6px; border: 1px solid {line}40;
         border-radius: 3px; background: {cell}; padding: 10px 12px; }}
.eyebrow {{ font-size: 11px; letter-spacing: .18em; color: {label}; }}
.big {{ font-size: 28px; line-height: 1; font-weight: 600; font-variant-numeric: tabular-nums; }}
.sub {{ font-size: 11px; letter-spacing: .1em; color: {muted}; margin-top: auto; }}
.tile.clock {{ width: 172px; flex: none; }}
.tile.band {{ width: 172px; flex: none; }}
/* BIAS and SIGNAL are WORDS: no tabular figures, and small enough that the
   longest of them ("Strong Bear") stays on one line. */
.tile.band .big {{ font-size: 19px; font-variant-numeric: normal;
                   white-space: nowrap; }}
.desc {{ font-size: 10px; letter-spacing: .08em; color: {label};
         white-space: nowrap; margin-top: auto; }}
.tile.regime {{ width: 250px; flex: none; }}
.card {{ flex: 1 1 420px; min-width: 400px; }}
.badge {{ align-self: flex-start; font-size: 10px; letter-spacing: .14em;
          padding: 2px 7px; border-radius: 2px; color: #0a1220; margin-top: auto; }}

/* the hero card: pill, three meters, a delta */
.cardhead {{ display: flex; align-items: baseline; gap: 10px; }}
.pill {{ font-size: 12px; letter-spacing: .16em; padding: 3px 9px; border-radius: 2px;
         border: 1px solid {accent}73; background: {accent}1f; color: {accent}; }}
.delta {{ margin-left: auto; font-size: 12px; letter-spacing: .08em; }}
.meters {{ display: flex; gap: 10px; margin-top: 8px; }}
.meter {{ flex: 1; }}
.meter .row {{ display: flex; align-items: baseline; justify-content: space-between; }}
.meter .cap {{ font-size: 10px; letter-spacing: .16em; color: {label}; }}
.meter .val {{ font-size: 17px; font-variant-numeric: tabular-nums; }}
.track {{ height: 4px; border-radius: 2px; background: {line}59; margin-top: 5px; overflow: hidden; }}
.fill {{ height: 100%; border-radius: 2px; background: {accent}; }}

/* ── bull / bear strip ── */
.bb {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.chip {{ flex: 1 1 124px; min-width: 124px; border: 1px solid; border-radius: 2px;
         padding: 6px 8px; display: flex; flex-direction: column; gap: 5px; }}
.chip .name {{ font-size: 13px; font-weight: 600; line-height: 1; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; }}
.chip .head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }}
.chip .day {{ font-size: 12px; font-variant-numeric: tabular-nums; opacity: .85; }}
.quad {{ font-size: 10px; letter-spacing: .1em; opacity: .8; }}
.chip .groove {{ height: 3px; border-radius: 2px; background: {line}59; overflow: hidden; }}
.chip .spacer {{ height: 3px; }}

/* ── panels ── */
/* ALWAYS 2x2 — never a width-dependent reflow. This screen is read at a glance
   from across a room, and a grid that collapsed to one column on a narrower
   display would move every panel somewhere the eye does not expect. `minmax(0,
   1fr)` rather than a bare `1fr` is what actually lets a column go narrower than
   its table's natural width: a grid item's automatic minimum is its content, so
   without the 0 the tables would push the page wider instead of shrinking. */
.grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
         grid-auto-rows: 1fr; gap: 14px; }}
.panel {{ border: 1px solid {line}40; border-radius: 3px; background: {cell};
          padding: 12px 14px 14px; display: flex; flex-direction: column;
          min-width: 0; }}
/* The floor, for a column too narrow even for the truncated table: the TABLE
   scrolls inside its own panel, never the page. A horizontally scrolling body
   is how a 2x2 becomes unreadable on the display it was pinned to. */
.pbody {{ min-width: 0; overflow-x: auto; }}
.phead {{ display: flex; align-items: baseline; gap: 10px; padding-bottom: 8px;
          border-bottom: 1px solid {line}40; }}
.ptitle {{ font-size: 14px; letter-spacing: .2em; color: {text}; }}
.psub {{ font-size: 11px; letter-spacing: .16em; color: {label}; margin-left: auto; }}
.note {{ font-size: 15px; color: {muted}; padding: 16px 0; }}
.warn {{ font-size: 13px; color: {warning}; padding: 6px 0 0; }}
.summary {{ font-size: 14px; letter-spacing: .16em; padding: 8px 0 2px; color: {muted}; }}
.summary.risk {{ color: {warning}; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 8px;
         table-layout: fixed; }}
th {{ font-size: 12px; letter-spacing: .1em; color: {label}; font-weight: 400;
      text-align: left; padding: 0 8px 6px 0; border-bottom: 1px solid #121b26; }}
td {{ padding: 7px 8px 7px 0; border-bottom: 1px solid #0d151e;
      font-variant-numeric: tabular-nums; vertical-align: middle; }}
tr.click:hover td {{ background: {line}0f; }}
.sym {{ font-size: 18px; font-weight: 700; letter-spacing: .08em; color: #eaf2f9; }}
.val {{ font-size: 17px; }}
.dim {{ color: {muted}; }}
.faint {{ color: {dim}; }}
.tag {{ display: inline-block; font-size: 11px; letter-spacing: .1em; padding: 2px 5px;
        border: 1px solid; border-radius: 2px; }}
.fillchip {{ display: inline-block; font-size: 11px; letter-spacing: .1em;
             padding: 2px 5px; border-radius: 2px; color: #0a1220; }}
.trunc, td {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
/* A column label may WRAP — it must never ellipsize. In a grid that is 2x2 at
   every width, the narrow case is normal rather than exceptional, and a header
   reading "NET GEX / REG…" has lost the one thing it was there to say. Two
   lines cost height once per panel; a truncated label costs the meaning. */
th {{ white-space: normal; overflow: visible; line-height: 1.25; }}

/* the structure map: put wall .. spot .. call wall */
.map {{ position: relative; height: 24px; min-width: 150px; }}
.map .rail {{ position: absolute; left: 0; right: 0; top: 11px; height: 2px;
              background: {line}73; }}
.map .mark {{ position: absolute; top: 5px; width: 2px; height: 14px; transform: translateX(-1px); }}
.map .spot {{ top: 2px; height: 20px; width: 3px; background: #eaf2f9; }}
.map .flipm {{ background: {accent}; opacity: .8; }}
.stale .val, .stale .sym {{ opacity: .72; }}
"""

# The wordmark's two faces: the console display face for headings, JetBrains Mono
# for the nine columns of numbers. Both are the ones ``/desk`` itself loads.
_FONTS = _d.DESK_FONT_HEAD_HTML

_JS = """
const $ = (s) => document.querySelector(s);

/* Every cell is written with textContent, never innerHTML: these strings are
   market data and free-text alert lines, and one of them containing a '<' must
   never become markup. */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}
function tint(node, hex, alpha) {
  node.style.color = hex;
  node.style.borderColor = hex;
  if (alpha) node.style.background = hex + alpha;
  return node;
}
function panel(id, note, warning) {
  const body = $(id);
  body.replaceChildren();
  if (warning) body.appendChild(el('div', 'warn', warning));
  if (note) { body.appendChild(el('div', 'note', note)); return null; }
  return body;
}
/* `heads` is [label, widthPercent] pairs, and the widths are NOT decoration:
   `table-layout: fixed` (which is what makes the truncating cells ellipsize
   instead of widening their column) splits the table EQUALLY when no widths are
   declared, so a 10-column panel would give the same room to QTY as to EXPIRY.
   The colgroup is what spends the width where the reading is. */
function table(body, heads) {
  const t = el('table'), cg = el('colgroup');
  heads.forEach(([, w]) => {
    const c = el('col'); c.style.width = w + '%'; cg.appendChild(c);
  });
  t.appendChild(cg);
  const thead = el('thead'), tr = el('tr');
  heads.forEach(([h]) => tr.appendChild(el('th', null, h)));
  thead.appendChild(tr); t.appendChild(thead);
  const tb = el('tbody'); t.appendChild(tb); body.appendChild(t);
  return tb;
}
function row(tb, href) {
  const tr = el('tr', href ? 'click' : null);
  if (href) { tr.style.cursor = 'pointer'; tr.onclick = () => { location.href = href; }; }
  tb.appendChild(tr);
  return tr;
}
function cell(tr, cls, text, hex) {
  const td = el('td', cls, text);
  if (hex) td.style.color = hex;
  tr.appendChild(td);
  return td;
}

function clock(c) {
  $('#clock-cap').textContent = c.label;
  $('#clock-val').textContent = c.text;
}

function scoreCard(c) {
  const box = el('div', 'tile card');
  const head = el('div', 'cardhead');
  head.appendChild(el('div', 'eyebrow', c.title));
  if (c.pill) head.appendChild(el('div', 'pill', c.pill));
  if (c.delta) {
    const d = el('div', 'delta', c.delta.arrow + ' ' + c.delta.text);
    d.style.color = c.delta.tone;
    head.appendChild(d);
  }
  box.appendChild(head);
  const meters = el('div', 'meters');
  c.arcs.forEach((a) => {
    const m = el('div', 'meter'), r = el('div', 'row');
    r.appendChild(el('div', 'cap', a.caption));
    r.appendChild(el('div', 'val', a.text));
    m.appendChild(r);
    const track = el('div', 'track');
    if (a.value !== null) {
      const f = el('div', 'fill');
      f.style.width = Math.max(0, Math.min(100, a.value)) + '%';
      track.appendChild(f);
    }
    m.appendChild(track);
    meters.appendChild(m);
  });
  box.appendChild(meters);
  return box;
}

function structure(s) {
  const box = el('div', 'map');
  box.appendChild(el('div', 'rail'));
  const put = el('div', 'mark'); put.style.left = '0%';
  const call = el('div', 'mark'); call.style.left = '100%';
  put.style.background = PUT_HEX; call.style.background = CALL_HEX;
  box.append(put, call);
  if (s.flip !== null && s.flip !== undefined) {
    const f = el('div', 'mark flipm'); f.style.left = s.flip + '%'; box.appendChild(f);
  }
  if (s.spot !== null && s.spot !== undefined) {
    const p = el('div', 'mark spot'); p.style.left = s.spot + '%'; box.appendChild(p);
  }
  return box;
}

function paint(s) {
  clock(s.clock);
  $('#fresh').textContent = s.freshness.text;
  $('#fresh').style.color = s.freshness.tone;
  $('#fresh-dot').style.background = s.freshness.tone;
  const band = $('#band');
  band.replaceChildren();
  s.band.forEach((b) => {
    const t = el('div', 'tile band');
    t.appendChild(el('div', 'eyebrow', b.label));
    const v = el('div', 'big', b.value);
    v.style.color = b.tone;
    t.appendChild(v);
    t.appendChild(el('div', 'desc', b.descriptor));
    band.appendChild(t);
  });
  $('#regime').textContent = s.regime.word;
  $('#regime').style.color = s.regime.tone;
  $('#regime-sub').textContent = s.regime.sub;

  const cards = $('#cards');
  cards.replaceChildren();
  s.cards.forEach((c) => cards.appendChild(scoreCard(c)));

  $('#bb-head').textContent = s.bullbear.headline;
  const bb = $('#bb');
  bb.replaceChildren();
  if (s.bullbear.note) { bb.appendChild(el('div', 'note', s.bullbear.note)); }
  s.bullbear.chips.forEach((c) => {
    const chip = tint(el('a', 'chip'), c.tone, '1f');
    chip.href = '/sentiment/bullbear';
    const head = el('div', 'head');
    head.appendChild(el('div', 'name', c.label));
    head.appendChild(el('div', 'day', c.day));
    chip.appendChild(head);
    chip.appendChild(el('div', 'quad', c.quadrant_label));
    /* No groove at all when there is no reading — a different drawing from an
       empty one, which would state that nothing confirms. */
    if (c.breadth === null || c.breadth === undefined) {
      chip.appendChild(el('div', 'spacer'));
    } else {
      const g = el('div', 'groove'), f = el('div');
      f.style.cssText = 'height:100%;border-radius:2px;width:' + c.breadth +
                        '%;background:' + c.breadth_tone;
      g.appendChild(f); chip.appendChild(g);
    }
    bb.appendChild(chip);
  });

  let b = panel('#dealer', s.dealer.note, s.dealer.warning);
  if (b) {
    const tb = table(b, [['SYMBOL', 9], ['SPOT', 17], ['GAMMA FLIP', 17],
                         ['STRUCTURE MAP', 15], ['CALL WALL', 13],
                         ['PUT WALL', 13], ['NET GEX / REGIME', 16]]);
    s.dealer.rows.forEach((r) => {
      const tr = row(tb, '/options/gamma');
      cell(tr, 'sym', r.symbol);
      const spot = cell(tr, null, null);
      spot.appendChild(el('div', 'val', r.spot));
      tint(spot.appendChild(el('div', 'quad', r.day_pct)), r.day_tone);
      const flip = cell(tr, null, null);
      flip.appendChild(el('div', 'val', r.flip));
      tint(flip.appendChild(el('div', 'quad', r.flip_sub)), r.flip_tone);
      const map = cell(tr, null, null);
      if (r.structure) { map.appendChild(structure(r.structure)); }
      else { map.appendChild(el('div', 'val dim', '\\u2014')); }
      cell(tr, 'val', r.call_wall, CALL_HEX);
      cell(tr, 'val', r.put_wall, PUT_HEX);
      const gex = cell(tr, null, null);
      tint(gex.appendChild(el('div', 'val', r.net_gex)), r.net_gex_tone);
      if (r.regime_word === '\\u2014') {
        tint(gex.appendChild(el('div', 'quad', r.regime_word)), r.regime_tone);
      } else {
        const tag = el('div');
        tag.appendChild(tint(el('span', 'tag', r.regime_word), r.regime_tone));
        gex.appendChild(tag);
      }
    });
  }

  $('#board-sub').textContent = s.board.subtitle;
  b = panel('#board', s.board.note);
  if (b) {
    const tb = table(b, [['SCORE', 7], ['SYMBOL', 11], ['WHY', 28],
                         ['ATM IV', 14], ['NET PREM', 12], ['P/C', 8],
                         ['SIGNAL', 10], ['SETUP', 10]]);
    s.board.rows.forEach((r) => {
      const tr = row(tb, '/options/matrix');
      cell(tr, 'val', r.hotness, ACCENT);
      cell(tr, 'sym', r.symbol);
      cell(tr, 'trunc dim', r.rationale || '\\u2014');
      const iv = cell(tr, null, null);
      iv.appendChild(el('span', 'val', r.atm_iv));
      const st = el('span', null, ' ' + r.iv_state);
      st.style.color = r.iv_tone; st.style.fontSize = '12px';
      iv.appendChild(st);
      cell(tr, 'val', r.net_prem, r.net_prem_tone);
      cell(tr, 'val', r.pc_ratio);
      const sig = cell(tr, null, null);
      const chip = el('span', 'fillchip', r.signal);
      chip.style.background = r.signal_tone;
      sig.appendChild(chip);
      const setup = cell(tr, null, null);
      if (r.setup) { setup.appendChild(tint(el('span', 'tag', r.setup), ACCENT, '1f')); }
    });
  }

  $('#flow-sub').textContent = s.flow.subtitle;
  b = panel('#flow', s.flow.note);
  if (b) {
    const tb = table(b, [['TIME', 10], ['SYMBOL', 13], ['DETAIL', 44],
                         ['KIND', 33]]);
    s.flow.rows.forEach((r) => {
      const tr = row(tb, '/options/flow');
      cell(tr, 'dim', r.time);
      cell(tr, 'sym', r.symbol);
      cell(tr, 'trunc dim', r.detail);
      cell(tr, 'trunc', r.kind, r.tone);
    });
  }

  $('#pos-sub').textContent = s.positions.subtitle;
  const sum = $('#pos-summary');
  sum.textContent = s.positions.summary;
  sum.className = s.positions.at_risk ? 'summary risk' : 'summary';
  b = panel('#positions', s.positions.note);
  if (b) {
    const tb = table(b, [['BOOK', 7], ['SYMBOL', 9], ['STRAT', 11],
                         ['EXPIRY', 13], ['ENTRY', 9], ['MARK', 9],
                         ['STRIKES', 11], ['QTY', 5], ['UNREALIZED', 16],
                         ['FLAG', 10]]);
    s.positions.rows.forEach((r) => {
      const tr = row(tb, r.href);
      const book = cell(tr, null, null);
      book.appendChild(tint(el('span', 'tag', r.source), r.source_tone, '1f'));
      cell(tr, 'sym', r.symbol);
      cell(tr, 'trunc dim', r.strategy);
      cell(tr, 'val', r.expiry);
      cell(tr, 'val', r.entry);
      cell(tr, 'val', r.mark);
      cell(tr, 'val', r.strikes);
      cell(tr, 'val', r.quantity);
      cell(tr, 'val', r.unrealized, r.unrealized_tone);
      const flag = cell(tr, null, null);
      /* An untagged book gets the dash BARE, with no chip: a box in the FLAG
         column reads as a verdict whatever is inside it. */
      if (r.flag === '\\u2014') { flag.appendChild(tint(el('span', 'faint', r.flag), r.flag_tone)); }
      else { flag.appendChild(tint(el('span', 'tag', r.flag), r.flag_tone, '1f')); }
    });
  }
}

/* EventSource reconnects on its own; the dot is what says whether the numbers on
   screen are still being refreshed. A frozen page that still LOOKS live is the
   one failure this whole screen exists to avoid. */
function connect() {
  const src = new EventSource(STREAM);
  src.addEventListener('desk', (e) => { paint(JSON.parse(e.data)); });
  src.addEventListener('clock', (e) => { clock(JSON.parse(e.data)); });
  src.onopen = () => { $('#conn-dot').className = 'dot on'; $('#conn-txt').textContent = 'LIVE'; };
  src.onerror = () => { $('#conn-dot').className = 'dot off'; $('#conn-txt').textContent = 'RECONNECTING'; };
}
connect();
"""


def document():
    """The standalone mirror page: styling, an empty skeleton, and the client.

    Everything visible is painted by the first ``desk`` event, so the skeleton
    ships with no numbers in it — there is no server-rendered first paint to keep
    in step with the streamed one.
    """
    css = _CSS.format(text=_C["text"], muted=_C["muted"], label=_C["label"],
                      dim=_C["dim"], line=_C["line"], cell=_C["cell"],
                      accent=_C["accent"], positive=_C["positive"],
                      negative=_C["negative"], warning=_C["warning"])
    consts = ("const STREAM = {stream};\nconst ACCENT = {accent};\n"
              "const CALL_HEX = {call};\nconst PUT_HEX = {put};\n").format(
        stream=json.dumps(STREAM_ROUTE), accent=json.dumps(_C["accent"]),
        call=json.dumps(_d.CALL_HEX), put=json.dumps(_d.PUT_HEX))
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Desk · Live</title>
{fonts}
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand">DESK <b>LIVE</b></div>
    <a class="link" href="/desk">OPEN THE FULL DESK</a>
    <div class="conn"><span id="conn-dot" class="dot"></span><span id="conn-txt">CONNECTING</span></div>
  </div>

  <div class="strip">
    <div class="tile clock">
      <div class="eyebrow" id="clock-cap">&nbsp;</div>
      <div class="big" id="clock-val">&mdash;</div>
      <div class="sub" style="display:flex;align-items:center;gap:7px">
        <span id="fresh-dot" class="dot"></span><span id="fresh">&mdash;</span>
      </div>
    </div>
    <div id="cards" style="display:contents"></div>
    <div id="band" style="display:contents"></div>
    <div class="tile regime">
      <div class="eyebrow">MARKET REGIME</div>
      <div class="big" id="regime">&mdash;</div>
      <div class="sub" id="regime-sub"></div>
    </div>
  </div>

  <div>
    <div class="eyebrow" id="bb-head" style="padding-bottom:7px"></div>
    <div class="bb" id="bb"></div>
  </div>

  <div class="grid">
    <div class="panel">
      <div class="phead"><div class="ptitle">DEALER POSITIONING</div></div>
      <div class="pbody" id="dealer"></div>
    </div>
    <div class="panel">
      <div class="phead"><div class="ptitle">OPPORTUNITY BOARD</div>
        <div class="psub" id="board-sub"></div></div>
      <div class="pbody" id="board"></div>
    </div>
    <div class="panel">
      <div class="phead"><div class="ptitle">LIVE FLOW ALERTS</div>
        <div class="psub" id="flow-sub"></div></div>
      <div class="pbody" id="flow"></div>
    </div>
    <div class="panel">
      <div class="phead"><div class="ptitle">POSITIONS</div>
        <div class="psub" id="pos-sub"></div></div>
      <div class="summary" id="pos-summary"></div>
      <div class="pbody" id="positions"></div>
    </div>
  </div>
</div>
<script>
{consts}{js}
</script>
</body>
</html>
""".format(fonts=_FONTS, css=css, consts=consts, js=_JS)
