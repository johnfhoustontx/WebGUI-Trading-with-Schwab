"""Pure builders for the two Options Flow console panels on ``/options/gamma``.

- **Premium Divergence** (the ``Flow`` view) — call/put premium as a two-tone
  ribbon with the spot line on its own scale, status chips, a strike ladder, and
  a readout rail.
- **Flow Field** (the ``Net Prem`` view) — net premium per symbol on a shared
  scale with terminus labels and a live leaderboard rail.

Design: docs/plans/2026-08-15-options-flow-redesign-design.md

**Why these are SVG strings and not Highcharts.** The spec is SVG (hairline
strokes over a translucent halo, a per-segment two-tone ribbon, decluttered
terminus labels), the repo already has this idiom twice (``pages/rings.py``,
``pages/regime_mix.py``), and it sidesteps both documented ``ui.highchart``
hazards at once — the ESM-import-map trap, and the ``chart.update()`` merge
leakage that makes ``gamma._set_chart`` recreate the element on a kind switch.

**Why the whole panel is one raw HTML fragment.** Chart, chips, ladder and rail
all move together under one cursor, and the scrub is client-side. Building the
chrome from NiceGUI components would mean JS reaching across into Quasar's DOM;
as one fragment the script only ever rewrites text nodes and bar widths on ids
it owns. Raw ``ui.html`` fragments with inline styles are the documented
out-of-scope case for the Tailwind-first standard — the same exemption the
Calculator's P&L heatmap and the Gamma Explain block use. The one thing that
genuinely cannot be inlined, the pulse keyframes, lives in
``theme.FLOW_KEYFRAMES_CSS``.

**Sanitizer constraint.** ``ui.html`` sanitizes through NiceGUI's bundled
DOMPurify (it monkeypatches ``Element.prototype.setHTML``), whose allowlist is
NOT the native sanitizer's. Notably ``dominant-baseline`` is stripped — the
obvious way to centre SVG ``<text>``, and a defect that cost real time on the
sentiment rings because the server-side string stays correct. So: ``dy`` shifts
(``_BASELINE_DY``), no ``foreignObject``, no ``<filter>``, no ``data-*``
attributes. ``test_flow_panels.py`` pins the whole emitted surface against the
allowlist read out of the shipped bundle.
"""
import datetime as _dt
import math
from zoneinfo import ZoneInfo

from pages.options.theme import FLOW_COLORS as C

_CT = ZoneInfo("America/Chicago")

# Vertical centring for SVG <text>. See the module docstring — this is the
# pre-dominant-baseline idiom, and it is em-relative so ONE constant serves
# every font size on the panel.
_BASELINE_DY = "0.35em"

MONO = "'IBM Plex Mono',ui-monospace,monospace"
DISPLAY = "'Rajdhani',system-ui,sans-serif"

# ── Premium Divergence geometry (viewBox 980 x 470) ─────────────────────────
DIV_VB = (980, 470)
DIV_PLOT = (52.0, 44.0, 932.0, 418.0)      # x0, y0, x1, y1
DIV_XLABEL_Y = 436.0
DIV_YGRID = 5                               # horizontal gridlines / y ticks

# ── Flow Field geometry (viewBox 940 x 560) ─────────────────────────────────
FLD_VB = (940, 560)
FLD_PLOT = (52.0, 48.0, 786.0, 500.0)
FLD_TICK_X = 800.0                          # terminus leader line ends here
FLD_LABEL_X = 806.0                         # ticker
FLD_VALUE_X = 884.0                         # closing value
FLD_XLABEL_Y = 524.0
FLD_FOOTER_Y = 546.0
FLD_LABEL_GAP = 15.0                        # min vertical gap between termini

XTICKS = 7                                  # time labels along the bottom
GLOW_W = 5.0                                # halo stroke width
GLOW_O = 0.18                               # halo stroke opacity
HAIRLINE = 0.75


#############################################
# PRIMITIVES
#############################################

def _esc(text):
    """Escape for HTML/SVG text content and attribute values."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _id(uid):
    """``uid`` reduced to characters legal in a DOM id. Not an escaper — an id
    has no business carrying quotes or markup at all."""
    return "".join(c for c in str(uid or "") if c.isalnum() or c in "_-") or "fx"


def _num(value):
    """A finite float, or None. Rejects bools (``True`` is an ``int``, so an
    unguarded flag would plot as 1.0) and nan/inf, which survive the JSON
    round-trip through Redis and would render as a break in the line."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _scale(value, lo, hi, p0, p1):
    """Value in [lo, hi] → pixel in [p0, p1]. A degenerate range pins to the
    midpoint rather than dividing by zero — a flat series is a real reading
    (nothing traded all session), not an error."""
    if hi - lo <= 0:
        return (p0 + p1) / 2.0
    return p0 + (value - lo) / (hi - lo) * (p1 - p0)


def _path(points):
    """``[(x, y), …]`` → an SVG ``d``. "" for fewer than two points: a one-point
    path draws nothing anyway, and emitting ``M`` alone trips strict parsers."""
    if len(points) < 2:
        return ""
    return "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points)


def _glow_line(d, color, width=HAIRLINE, glow_w=GLOW_W, glow_o=GLOW_O):
    """A hairline over a wide translucent copy of itself.

    The spec's glow, and the same technique ``rings.py`` uses: deliberately NOT
    an SVG ``<filter>``, both because DOMPurify strips it and because a blur
    filter on a multi-panel page is expensive to composite on a desk full of
    monitors."""
    if not d:
        return ""
    return (f'<path d="{d}" fill="none" stroke="{color}" '
            f'stroke-width="{glow_w}" stroke-opacity="{glow_o}" '
            f'stroke-linejoin="round"></path>'
            f'<path d="{d}" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linejoin="round"></path>')


def _text(x, y, body, size, fill, anchor="middle", weight=None, spacing=None,
          family=MONO, opacity=None, node_id=None):
    extra = "".join([
        f' font-weight="{weight}"' if weight else "",
        f' letter-spacing="{spacing}"' if spacing else "",
        f' fill-opacity="{opacity}"' if opacity is not None else "",
        f' id="{node_id}"' if node_id else "",
    ])
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'dy="{_BASELINE_DY}" font-size="{size}" font-family="{family}" '
            f'fill="{fill}"{extra}>{_esc(body)}</text>')


def _nice_ticks(lo, hi, count):
    """``count`` evenly spaced values across [lo, hi], inclusive.

    Deliberately NOT a "nice number" algorithm: premium axes run 0 → a few
    hundred $M and the labels are rounded at render, so even spacing reads fine
    and keeps the gridline positions exactly reproducible in tests."""
    if count < 2 or hi - lo <= 0:
        return [lo]
    step = (hi - lo) / (count - 1)
    return [lo + step * i for i in range(count)]


#############################################
# FORMATTING
#############################################

def fmt_m(value, decimals=1):
    """A premium in $M, unsigned: ``251.6``. ``—`` for a missing reading."""
    v = _num(value)
    return "—" if v is None else f"{v:,.{decimals}f}"


# Stored premiums are already in MILLIONS, so the axis starts at M and steps up
# from there. Ordered smallest-first; the loop takes the last unit the value
# clears.
_MONEY_UNITS = ((1.0, "M"), (1_000.0, "B"), (1_000_000.0, "T"))


def fmt_axis_money(value, signed=False):
    """An axis tick as a scaled dollar amount: ``946M`` / ``1.2B`` / ``−90M``.

    The bare number these labels used to carry said nothing about magnitude — a
    premium axis reading ``0 237 473 710 946`` could be dollars, thousands or
    millions, and the only clue was a ``$M`` suffix on a chip elsewhere in the
    panel. Carrying the unit on the axis makes it readable on its own.

    A value that clears the next unit switches to it with one decimal (``1.2B``),
    and a whole number drops the decimal (``1B`` not ``1.0B``) so the column
    stays narrow. Zero is plain ``0`` — signed or unit-suffixed zero reads as a
    measurement rather than as the origin.
    """
    v = _num(value)
    if v is None:
        return "—"
    a = abs(v)
    if a < 0.05:
        return "0"
    scale, unit = _MONEY_UNITS[0]
    for factor, suffix in _MONEY_UNITS:
        if a >= factor:
            scale, unit = factor, suffix
    n = a / scale
    # Sub-unit and fractional values keep a decimal; whole ones drop it.
    text = f"{n:,.1f}".rstrip("0").rstrip(".") if n < 100 else f"{n:,.0f}"
    if not signed:
        return f"{text}{unit}"
    # U+2212 MINUS, matching fmt_signed — a hyphen makes the column jitter.
    return f"{'+' if v >= 0 else '−'}{text}{unit}"


def fmt_signed(value, decimals=1):
    """A net reading with the sign LEADING (``+425`` / ``−90.2``), so it reads as
    a direction rather than as a negative quantity. Uses U+2212 MINUS, which is
    the same width as ``+`` in the mono face — a hyphen makes the column jitter
    as the sign flips."""
    v = _num(value)
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else '−'}{abs(v):,.{decimals}f}"


def fmt_price(value):
    v = _num(value)
    return "—" if v is None else f"{v:,.2f}"


def fmt_time(ts):
    """A collection timestamp as ``HH:MM`` in market (Central) time.

    Explicitly CT rather than the host's local zone: every other time on this
    page — the collection window, the status strip, the flow-alert tape — is
    stated in CT, and a cursor reading in a different zone would be read as the
    same clock.
    """
    v = _num(ts)
    if v is None:
        return "—"
    try:
        return _dt.datetime.fromtimestamp(v, _CT).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


# A sample older than this is not "streaming". Collection polls every minute, so
# five minutes is several missed polls rather than one slow tick.
LIVE_MAX_AGE_SEC = 300


def session_pill(times, now=None):
    """``{"text", "short", "live"}`` for the panel's status pill.

    The pill used to be the constant string ``LIVE · STREAMING``, which asserted
    a live feed at 9pm on a Saturday over Friday's series — the one moment a
    reader most needs to be told otherwise. Three honest states, derived from
    the DATA rather than from a clock, so a holiday or a stalled collector reads
    correctly without this needing to know the calendar:

    * a sample within ``LIVE_MAX_AGE_SEC`` — genuinely streaming;
    * an older sample from today — collection has stopped for the session
      (after the 15:20 CT close, or a stalled collector mid-session);
    * a sample from an earlier date — the last session, named.
    """
    stamps = [int(t) for t in (times or ()) if _num(t) is not None]
    if not stamps:
        return {"text": "NO DATA", "short": "NO DATA", "live": False}
    newest = max(stamps)
    now_ts = int(now if now is not None else _dt.datetime.now(_CT).timestamp())
    at = _dt.datetime.fromtimestamp(newest, _CT)
    if now_ts - newest <= LIVE_MAX_AGE_SEC:
        return {"text": "LIVE · STREAMING", "short": "LIVE", "live": True}
    if at.date() == _dt.datetime.fromtimestamp(now_ts, _CT).date():
        short = f"{at:%H:%M} CT"
        return {"text": f"SESSION CLOSED · {short}", "short": short,
                "live": False}
    short = f"{at:%a %d %b}".upper()
    return {"text": f"LAST SESSION · {short}", "short": short, "live": False}


def dte_label(dte):
    """The panel header's tenor chip: ``0DTE`` / ``3DTE`` / ``NEAREST EXPIRY``.

    The premium series is summed over the collected window (today → +7d), so the
    number is the NEAREST expiry in it, not the only one — the chip says which
    book dominates, and falls back to a neutral phrase rather than inventing a
    number when the snapshot carries none."""
    v = _num(dte)
    return "NEAREST EXPIRY" if v is None else f"{int(v)}DTE"


def net_color(value):
    """Cyan when call-led, magenta when put-led. A missing reading takes the
    muted label colour — never one of the two, which would assert a side."""
    v = _num(value)
    if v is None:
        return C["label"]
    return C["call"] if v >= 0 else C["put"]


#############################################
# SCALE TOGGLE (Flow Field)
#############################################
# A real control inside a raw fragment. DOMPurify strips inline ``on*`` handlers,
# so the click cannot ride the markup — it is bound by the same script channel the
# scrub uses (``addEventListener``), and reaches Python through NiceGUI's global
# ``emitEvent`` / ``ui.on`` pair.
#
# The KEYS are the ones ``gamma.NET_PREM_MODES`` already persists, so the toggle
# writes exactly the values ``app_settings`` and ``net_prem_value`` expect and no
# translation layer exists to drift. The spec called the second segment
# "PERCENTILE"; it is labelled SKEW % here because that is what the mode actually
# computes (a signed share of session premium) and what the rest of the app calls
# it — a label naming a statistic the code does not compute would be worse than
# the spec mismatch.
FIELD_MODES = (("dollars", "DOLLARS"), ("skew", "SKEW %"))
FIELD_MODE_KEYS = tuple(key for key, _label in FIELD_MODES)
DEFAULT_MODE = "dollars"

# The custom event name the panel emits and the page listens on.
MODE_EVENT = "fx_net_prem_mode"


def normalize_mode(mode):
    """A known mode key, defaulting to dollars.

    Applied on BOTH sides — when building the toggle and when handling the click
    — because the click payload arrives from the browser and is therefore
    untrusted: it is persisted to ``settings.json``, so an arbitrary string would
    be written to disk and then read back on the next page build.
    """
    return mode if mode in FIELD_MODE_KEYS else DEFAULT_MODE


def mode_toggle_html(uid, active):
    """The two-segment scale toggle; the ACTIVE segment is filled.

    Rendered as one control rather than the spec's pair of look-alike chips, so
    it reads as a toggle with a current state rather than two buttons.
    """
    u = _id(uid)
    active = normalize_mode(active)
    segments = []
    for key, label in FIELD_MODES:
        on = key == active
        fill = (f"color:{C['panel_to']};background:{C['ice']}" if on else
                f"color:rgba(174,205,232,.5);background:transparent")
        segments.append(
            f'<span id="{u}-m-{key}" title="Scale: {_esc(label)}" '
            f'style="padding:7px 13px;font:500 9px/1 {MONO};'
            f'letter-spacing:.14em;cursor:pointer;user-select:none;{fill}">'
            f'{_esc(label)}</span>')
    return (f'<div style="display:flex;margin-left:auto;'
            f'box-shadow:inset 0 0 0 1px rgba(190,248,255,.12)">'
            f'{"".join(segments)}</div>')


_TOGGLE_JS = r"""
(function(){
  var id=__ID__, keys=__KEYS__, ev=__EVENT__;
  keys.forEach(function(k){
    var el=document.getElementById(id+'-m-'+k);
    if(!el) return;
    el.addEventListener('click', function(){
      if (typeof emitEvent === 'function') emitEvent(ev, k);
    });
  });
})();
"""


def toggle_js(uid):
    """Bind the scale toggle's segments to the page's ``ui.on`` handler.

    Emitted on EVERY paint, independently of ``scrub_js`` — the toggle exists in
    the empty state too, where there is no scrub payload to carry it.
    """
    import json
    return (_TOGGLE_JS
            .replace("__ID__", json.dumps(_id(uid)))
            .replace("__KEYS__", json.dumps(list(FIELD_MODE_KEYS)))
            .replace("__EVENT__", json.dumps(MODE_EVENT)))


#############################################
# PREMIUM DIVERGENCE — series
#############################################

def divergence_series(rows):
    """The snapshot's ``flow`` rows → parallel arrays for the panel.

    Only rows carrying BOTH premiums are kept: the ribbon is the area *between*
    call and put, so a one-sided row has no ribbon to draw and would leave a
    wedge anchored to whichever side happened to be present. Spot may be missing
    on a kept row (off-hours snapshots) and is carried as None — the price line
    breaks there rather than interpolating across a gap it cannot see.

    Rows are ts-ascending on the way in (the service sorts them); re-sorted here
    anyway so a hand-built or partially-written payload cannot shear the ribbon.
    """
    out = {"ts": [], "spot": [], "call": [], "put": []}
    kept = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        ts = _num(row.get("ts"))
        call, put = _num(row.get("call_prem")), _num(row.get("put_prem"))
        if ts is None or call is None or put is None:
            continue
        kept.append((int(ts), _num(row.get("spot")), call / 1e6, put / 1e6))
    kept.sort(key=lambda r: r[0])
    for ts, spot, call, put in kept:
        out["ts"].append(ts)
        out["spot"].append(spot)
        out["call"].append(call)
        out["put"].append(put)
    return out


def align_ladder(times, ladder):
    """``[rows or None]``, one entry per flow timestamp.

    The collector writes the ``prem`` row at the SAME snapped boundary as the
    Greek views, so alignment is normally exact. A timestamp with no ladder gets
    None rather than the previous one carried forward: a carried ladder would
    render as a live reading at a time it was never taken.
    """
    by_ts = {}
    for entry in ladder or ():
        if isinstance(entry, dict) and _num(entry.get("ts")) is not None:
            by_ts[int(entry["ts"])] = entry.get("rows") or None
    return [by_ts.get(ts) for ts in times]


def divergence_session(series):
    """The rail's SESSION SKEW block: closing totals + the spot range.

    Totals are the LAST reading, not a sum: the stored premiums are already
    daily-cumulative, so summing them would multiply the session by its own
    sample count.
    """
    call, put = series.get("call") or [], series.get("put") or []
    spots = [s for s in (series.get("spot") or []) if _num(s) is not None]
    return {
        "call_total": call[-1] if call else None,
        "put_total": put[-1] if put else None,
        "high": max(spots) if spots else None,
        "low": min(spots) if spots else None,
    }


def ribbon_segments(xs, y_call, y_put):
    """Split the call/put band into runs of one sign, in PIXEL space.

    Returns ``(call_led_paths, put_led_paths)`` — closed polygons, top edge
    forward and bottom edge back. Runs are split AT the crossing (linearly
    interpolated), which is what makes the two fills meet in a point rather than
    overlapping by one sample either side of it. The crossing is the read this
    panel exists for, so an approximate one would blunt exactly the thing it
    is meant to show.

    Pixel y grows downward, so "call-led" (call premium above put) is
    ``y_call < y_put``.
    """
    pos, neg, run, sign = [], [], [], 0

    def _flush():
        if len(run) < 2:
            return
        top = [(x, a) for x, a, _ in run]
        bottom = [(x, b) for x, _, b in run]
        d = (_path(top) + " L " +
             " L ".join(f"{x:.1f} {y:.1f}" for x, y in reversed(bottom)) + " Z")
        (pos if sign > 0 else neg).append(d)

    for i, (x, yc, yp) in enumerate(zip(xs, y_call, y_put)):
        cur = 0 if yc == yp else (1 if yc < yp else -1)
        if run and cur and sign and cur != sign:
            # Interpolate the crossing so both runs terminate on the same point.
            x0, yc0, yp0 = run[-1]
            gap0, gap1 = yp0 - yc0, yp - yc
            t = gap0 / (gap0 - gap1) if (gap0 - gap1) else 0.0
            t = min(1.0, max(0.0, t))
            xm = x0 + (x - x0) * t
            ym = yc0 + (yc - yc0) * t
            run.append((xm, ym, ym))
            _flush()
            run, sign = [(xm, ym, ym)], cur
        elif not sign:
            sign = cur
        run.append((x, yc, yp))
    _flush()
    return pos, neg


def divergence_geometry(series):
    """Pixel coordinates for every plotted point, plus the axis descriptions.

    Returned rather than drawn straight so the scrub payload and the SVG share
    ONE set of coordinates — the cursor dots must land exactly on the lines they
    track, and recomputing them in JS would be two implementations of the same
    scale.
    """
    x0, y0, x1, y1 = DIV_PLOT
    call, put, spot = series["call"], series["put"], series["spot"]
    n = len(call)
    if not n:
        return None

    prem_hi = max(max(call), max(put))
    prem_hi = prem_hi * 1.06 if prem_hi > 0 else 1.0
    # Premium is cumulative from zero, so the axis starts at zero: a zoomed
    # baseline would exaggerate a quiet session into a dramatic one.
    prem_lo = 0.0

    live = [s for s in spot if _num(s) is not None]
    if live:
        s_lo, s_hi = min(live), max(live)
        pad = (s_hi - s_lo) * 0.18 or (abs(s_hi) * 0.001 or 1.0)
        s_lo, s_hi = s_lo - pad, s_hi + pad
    else:
        s_lo, s_hi = 0.0, 1.0

    xs = [_scale(i, 0, max(n - 1, 1), x0, x1) for i in range(n)]
    return {
        "n": n, "xs": xs,
        "prem": (prem_lo, prem_hi), "spot_range": (s_lo, s_hi),
        "y_call": [_scale(v, prem_lo, prem_hi, y1, y0) for v in call],
        "y_put": [_scale(v, prem_lo, prem_hi, y1, y0) for v in put],
        "y_spot": [None if _num(s) is None
                   else _scale(s, s_lo, s_hi, y1, y0) for s in spot],
    }


def _broken_paths(xs, ys):
    """One ``d`` per unbroken run — a None y ENDS the run.

    Bridging a gap would draw a straight line across minutes the collector never
    saw, which on a price track reads as a period of calm rather than as missing
    data."""
    out, run = [], []
    for x, y in zip(xs, ys):
        if y is None:
            if len(run) > 1:
                out.append(_path(run))
            run = []
        else:
            run.append((x, y))
    if len(run) > 1:
        out.append(_path(run))
    return out


def divergence_svg(series, geom, times, uid):
    """The Premium Divergence chart as one inline SVG string."""
    u = _id(uid)
    x0, y0, x1, y1 = DIV_PLOT
    w, h = DIV_VB
    parts = [
        f'<svg viewBox="0 0 {w} {h}" style="display:block;width:100%;height:auto">',
        '<defs>',
        f'<linearGradient id="{u}-cpos" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{C["call"]}" stop-opacity="0.42"></stop>'
        f'<stop offset="100%" stop-color="{C["call_deep"]}" stop-opacity="0.14">'
        f'</stop></linearGradient>',
        f'<linearGradient id="{u}-cneg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{C["put"]}" stop-opacity="0.38"></stop>'
        f'<stop offset="100%" stop-color="{C["put_deep"]}" stop-opacity="0.14">'
        f'</stop></linearGradient>',
        '</defs>',
    ]

    lo, hi = geom["prem"]
    ticks = _nice_ticks(lo, hi, DIV_YGRID)
    for value in ticks:
        y = _scale(value, lo, hi, y1, y0)
        parts.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
                     f'stroke="{C["grid"]}" stroke-opacity="0.07" '
                     f'stroke-width="1"></line>')
        parts.append(_text(x0 - 8, y, fmt_axis_money(value), 10, C["label"],
                           anchor="end", opacity="0.42"))

    pos, neg = ribbon_segments(geom["xs"], geom["y_call"], geom["y_put"])
    for d in pos:
        parts.append(f'<path d="{d}" fill="url(#{u}-cpos)"></path>')
    for d in neg:
        parts.append(f'<path d="{d}" fill="url(#{u}-cneg)"></path>')

    parts.append(_glow_line(_path(list(zip(geom["xs"], geom["y_call"]))),
                            C["call"]))
    parts.append(_glow_line(_path(list(zip(geom["xs"], geom["y_put"]))),
                            C["put"]))
    # The spot track is drawn last and slightly heavier: it is the reference the
    # premium series are read AGAINST, so it must stay legible through both.
    for d in _broken_paths(geom["xs"], geom["y_spot"]):
        parts.append(f'<path d="{d}" fill="none" stroke="{C["spot"]}" '
                     f'stroke-width="0.9"></path>')

    step = max(1, (geom["n"] - 1) // max(XTICKS - 1, 1)) if geom["n"] > 1 else 1
    for i in range(0, geom["n"], step):
        parts.append(_text(geom["xs"][i], DIV_XLABEL_Y, times[i], 10,
                           C["label"], opacity="0.38", spacing="0.06em"))

    cx = geom["xs"][-1]
    parts += [
        f'<line id="{u}-cur" x1="{cx:.1f}" y1="{y0}" x2="{cx:.1f}" y2="{y1}" '
        f'stroke="{C["ice"]}" stroke-width="1" stroke-opacity="0.5"></line>',
        f'<circle id="{u}-dspot" cx="{cx:.1f}" cy="{y1}" r="3.5" '
        f'fill="{C["spot"]}"></circle>',
        f'<circle id="{u}-dcall" cx="{cx:.1f}" cy="{y1}" r="3" '
        f'fill="{C["call"]}"></circle>',
        f'<circle id="{u}-dput" cx="{cx:.1f}" cy="{y1}" r="3" '
        f'fill="{C["put"]}"></circle>',
        # The hit area spans exactly the plot box, so the scrub can map a pointer
        # x straight onto a sample index by proportion of its own width.
        f'<rect id="{u}-hit" x="{x0}" y="{y0}" width="{x1 - x0}" '
        f'height="{y1 - y0}" fill="transparent"></rect>',
        '</svg>',
    ]
    return "".join(parts)


#############################################
# FLOW FIELD — series
#############################################

def declutter(ys, min_gap=FLD_LABEL_GAP, lo=None, hi=None):
    """Nudge label y positions apart, preserving their ORDER.

    A shared dollar scale routinely stacks several symbols within a pixel of
    zero (measured on the spec's own sample: four of seven inside 40px), so
    without this the terminus labels — which exist to REPLACE the legend —
    overprint into an unreadable smear and the panel loses its only key.

    One forward pass pushing each label below its predecessor, then a backward
    pass to pull the whole block up if it overflowed the bottom. Order is never
    changed: a label that swapped past its neighbour would point at the wrong
    line, which is worse than one sitting slightly off its own.
    """
    if not ys:
        return []
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    placed = [0.0] * len(ys)
    prev = None
    for i in order:
        y = ys[i] if prev is None else max(ys[i], prev + min_gap)
        placed[i] = y
        prev = y
    if hi is not None and prev is not None and prev > hi:
        shift = prev - hi
        for i in order:
            placed[i] -= shift
        if lo is not None and placed[order[0]] < lo:
            # Cannot satisfy both bounds — more labels than room. Pin to the top
            # and let the bottom overflow, which at least keeps the leaders
            # attached to the lines they name.
            bump = lo - placed[order[0]]
            for i in order:
                placed[i] += bump
    return placed


def field_series(rows_by_symbol, order):
    """``{symbol: [(ts, value), …]}`` → the panel's shared-timeline model.

    Every series is indexed into the UNION of timestamps, for the reason
    ``net_prem_figure`` documents: the symbols do not share a clock, so plotting
    each against its own row index would shear the lines apart and put one
    name's 09:15 above another's 08:30.
    """
    times = sorted({ts for pairs in rows_by_symbol.values() for ts, _ in pairs})
    index = {ts: i for i, ts in enumerate(times)}
    lines = []
    for sym in order:
        pairs = rows_by_symbol.get(sym) or []
        values = [None] * len(times)
        for ts, value in pairs:
            values[index[ts]] = value
        if any(v is not None for v in values):
            lines.append({"k": sym, "v": values})
    return {"times": times, "lines": lines}


def field_geometry(model):
    """Pixel coordinates for every line, plus the zero line and the y range."""
    x0, y0, x1, y1 = FLD_PLOT
    n = len(model["times"])
    if not n or not model["lines"]:
        return None
    flat = [v for line in model["lines"] for v in line["v"] if v is not None]
    if not flat:
        return None
    lo, hi = min(flat), max(flat)
    # Always straddle zero: this panel's whole claim is "call-led above the flat
    # line", which is meaningless if zero is off-canvas.
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.08, hi + span * 0.08

    xs = [_scale(i, 0, max(n - 1, 1), x0, x1) for i in range(n)]
    # ``times`` rides along so the panel's status pill can date the series it is
    # actually drawing, rather than asserting a live feed from a clock.
    out = {"n": n, "xs": xs, "range": (lo, hi), "times": list(model["times"]),
           "zero_y": _scale(0.0, lo, hi, y1, y0), "lines": []}
    for line in model["lines"]:
        out["lines"].append({
            "k": line["k"], "v": line["v"],
            "y": [None if v is None else _scale(v, lo, hi, y1, y0)
                  for v in line["v"]],
        })
    return out


FIELD_FOOTER = {
    "dollars": ("SHARED DOLLAR SCALE · NET PREMIUM $M · "
                "CALL-LED ABOVE THE FLAT LINE"),
    # Named for what it fixes: on a shared dollar scale a dominant name
    # compresses the small ones toward zero (the spec's own "known trade-off"),
    # and a share-of-premium scale is what makes them comparable.
    "skew": ("SKEW SCALE · NET AS A SHARE OF SESSION PREMIUM · "
             "COMPARABLE ACROSS SIZES"),
}


def field_svg(geom, times, colors, uid, mode=DEFAULT_MODE):
    """The Flow Field chart as one inline SVG string."""
    u = _id(uid)
    mode = normalize_mode(mode)
    x0, y0, x1, y1 = FLD_PLOT
    w, h = FLD_VB
    parts = [f'<svg viewBox="0 0 {w} {h}" '
             f'style="display:block;width:100%;height:auto">']

    # Both modes plot signed numbers of similar magnitude, so without a unit a
    # skew axis and a dollar axis are indistinguishable at a glance. Skew keeps
    # its bare percentage; dollars now carry their scale (M / B) rather than a
    # bare count that could be read as any magnitude.
    skew = mode == "skew"
    lo, hi = geom["range"]
    for value in _nice_ticks(lo, hi, XTICKS):
        y = _scale(value, lo, hi, y1, y0)
        label = (fmt_signed(value, 0) + "%" if skew
                 else fmt_axis_money(value, signed=True))
        parts.append(_text(x0 - 8, y, label, 10,
                           C["label"], anchor="end", opacity="0.42"))
    step = max(1, (geom["n"] - 1) // max(XTICKS - 1, 1)) if geom["n"] > 1 else 1
    for i in range(0, geom["n"], step):
        parts.append(_text(geom["xs"][i], FLD_XLABEL_Y, times[i], 10,
                           C["label"], opacity="0.38"))

    zy = geom["zero_y"]
    parts.append(f'<line x1="{x0}" y1="{zy:.1f}" x2="{x1}" y2="{zy:.1f}" '
                 f'stroke="{C["ice"]}" stroke-opacity="0.26" '
                 f'stroke-width="1"></line>')
    parts.append(_text(x0 + 2, zy - 8, "FLAT", 9, C["ice"], anchor="start",
                       opacity="0.4", spacing="0.2em"))

    for line in geom["lines"]:
        color = colors.get(line["k"], C["label"])
        for d in _broken_paths(geom["xs"], line["y"]):
            parts.append(_glow_line(d, color))

    # Terminus labels: ticker + closing value at each line's right end, nudged
    # apart. They replace the legend, so they must not collide.
    ends = [(i, line) for i, line in enumerate(geom["lines"])
            if any(y is not None for y in line["y"])]
    raw_y = [next(y for y in reversed(line["y"]) if y is not None)
             for _i, line in ends]
    placed = declutter(raw_y, lo=y0, hi=y1)
    for (idx, line), y_line, y_lab in zip(ends, raw_y, placed):
        color = colors.get(line["k"], C["label"])
        value = next(v for v in reversed(line["v"]) if v is not None)
        parts.append(f'<line x1="{x1}" y1="{y_line:.1f}" x2="{FLD_TICK_X}" '
                     f'y2="{y_lab:.1f}" stroke="{color}" '
                     f'stroke-opacity="0.55"></line>')
        parts.append(_text(FLD_LABEL_X, y_lab, line["k"], 12, color,
                           anchor="start", weight="600", spacing="0.06em"))
        # Same units as the axis it sits beside — a terminus reading "+425"
        # against an axis reading "+425M" invites the reader to guess.
        end_label = (fmt_signed(value) + "%" if skew
                     else fmt_axis_money(value, signed=True))
        parts.append(_text(FLD_VALUE_X, y_lab, end_label, 11,
                           C["label"], anchor="start", opacity="0.55"))

    parts.append(_text(x0, FLD_FOOTER_Y, FIELD_FOOTER[mode], 9, C["label"],
                       anchor="start", opacity="0.32", spacing="0.16em"))

    cx = geom["xs"][-1]
    parts.append(f'<line id="{u}-cur" x1="{cx:.1f}" y1="{y0}" x2="{cx:.1f}" '
                 f'y2="{y1}" stroke="{C["ice"]}" stroke-width="1" '
                 f'stroke-opacity="0.4" stroke-dasharray="3 4"></line>')
    for idx, line in enumerate(geom["lines"]):
        color = colors.get(line["k"], C["label"])
        parts.append(f'<circle id="{u}-d{idx}" cx="{cx:.1f}" cy="{y1}" '
                     f'r="2.8" fill="{color}"></circle>')
    parts.append(f'<rect id="{u}-hit" x="{x0}" y="{y0}" width="{x1 - x0}" '
                 f'height="{y1 - y0}" fill="transparent"></rect>')
    parts.append('</svg>')
    return "".join(parts)


#############################################
# CHROME — shared fragment pieces
#############################################
# Inline styles, not Tailwind: these are raw ui.html fragments, the documented
# out-of-scope case for the Tailwind-first standard (see the module docstring).

_PANEL_BG = (f"linear-gradient(150deg,{C['panel_from']} 0%,"
             f"{C['panel_mid']} 48%,{C['panel_to']} 100%)")
_PANEL_SHADOW = ("0 0 0 1px rgba(53,200,255,.12),0 40px 90px -40px #000")
_CHIP_BG = "rgba(190,248,255,.035)"
_CHIP_RING = "inset 0 0 0 1px rgba(190,248,255,.10)"
_HAIR = "rgba(53,200,255,.10)"
_RAIL_BG = "linear-gradient(180deg,rgba(53,200,255,.05),transparent 38%)"


def _live_pill(text, live=True):
    """The status pill. ``live=False`` drops the green and the pulse.

    Both are assertions — a pulsing green dot says "arriving now" on its own,
    regardless of the words beside it — so a stale panel must not keep them, or
    the colour contradicts the text."""
    if live:
        tint, ring, dot = "rgba(94,240,184,.10)", "rgba(94,240,184,.30)", C["live"]
        glow = "box-shadow:0 0 10px 2px rgba(94,240,184,.85);"
        pulse = ' class="fx-pulse"'
    else:
        tint, ring, dot = "rgba(174,205,232,.06)", "rgba(174,205,232,.22)", C["label"]
        glow = ""
        pulse = ""
    return (f'<span style="flex:none;white-space:nowrap;display:flex;'
            f'align-items:center;gap:8px;padding:6px 12px;'
            f'background:{tint};'
            f'box-shadow:inset 0 0 0 1px {ring}">'
            f'<span{pulse} style="width:6px;height:6px;'
            f'background:{dot};{glow}"></span>'
            f'<span style="font:500 9px/1 {MONO};letter-spacing:.18em;'
            f'color:{dot}">{_esc(text)}</span></span>')


def _title(text):
    return (f'<span style="flex:none;white-space:nowrap;font:700 18px/1 '
            f'{DISPLAY};letter-spacing:.2em;color:{C["title"]}">'
            f'{_esc(text)}</span>')


def _meta(text):
    return (f'<span style="font:400 9px/1 {MONO};letter-spacing:.2em;'
            f'color:rgba(174,205,232,.42)">{_esc(text)}</span>')


def _chip(label, value, color, swatch, node_id):
    """A status chip: colour swatch, letter-spaced label, live value."""
    return (f'<span style="display:flex;align-items:center;gap:9px;'
            f'padding:7px 12px;background:{_CHIP_BG};box-shadow:{_CHIP_RING}">'
            f'<span style="width:14px;height:2px;background:{swatch}"></span>'
            f'<span style="font:500 9px/1 {MONO};letter-spacing:.16em;'
            f'color:rgba(174,205,232,.6)">{_esc(label)}</span>'
            f'<span id="{node_id}" style="font:600 12px/1 {MONO};'
            f'color:{color}">—</span></span>')


def _rail_cell(label, node_id, color, size=20, swatch=None, unit=""):
    dot = (f'<span style="width:10px;height:2px;background:{swatch};'
           f'box-shadow:0 0 8px 1px {swatch}"></span>' if swatch else "")
    suffix = (f'<span style="font-size:11px;opacity:.5"> {_esc(unit)}</span>'
              if unit else "")
    return (f'<div style="padding:13px 22px;border-bottom:1px solid '
            f'rgba(53,200,255,.08)">'
            f'<div style="display:flex;align-items:center;gap:8px;'
            f'margin-bottom:8px">{dot}'
            f'<span style="font:500 9px/1 {MONO};letter-spacing:.2em;'
            f'color:rgba(174,205,232,.45)">{_esc(label)}</span></div>'
            f'<div id="{node_id}" style="font:600 {size}px/1 {MONO};'
            f'color:{color}">—</div>{suffix}</div>')


def _bipolar_bar(fill_id, height=6):
    """A centre-anchored bar: a hairline tick at 50% and a fill that grows left
    for put-led, right for call-led. The zero tick is what makes it readable as
    a direction rather than as a magnitude."""
    return (f'<div style="margin-top:12px;height:{height}px;'
            f'background:rgba(190,248,255,.07);position:relative">'
            f'<div style="position:absolute;top:0;left:50%;width:1px;'
            f'height:{height}px;background:rgba(190,248,255,.35)"></div>'
            f'<div id="{fill_id}" style="position:absolute;top:0;'
            f'height:{height}px;left:50%;width:0%"></div></div>')


def _empty_panel(title, message, header_extra=""):
    """The panel's shell with a message where the chart would be.

    Not a bare label: keeping the frame means an empty view reads as "this panel
    has nothing to show yet" rather than as a page that failed to render.

    ``header_extra`` carries any CONTROL that must survive the empty state — the
    Flow Field's scale toggle does, or a session with nothing collected yet would
    leave the reader no way to change scale until data arrived.
    """
    return (f'<div class="fx-panel" style="width:100%;'
            f'background:{_PANEL_BG};box-shadow:{_PANEL_SHADOW}">'
            f'<div style="padding:20px 24px">'
            f'<div style="display:flex;align-items:center;gap:12px;'
            f'margin-bottom:18px">{_title(title)}{header_extra}</div>'
            f'<div style="font:400 12px/1.7 {MONO};'
            f'color:rgba(174,205,232,.55);padding:26px 0 30px">'
            f'{_esc(message)}</div></div></div>')


#############################################
# PREMIUM DIVERGENCE — panel
#############################################

LADDER_EMPTY = ("No premium ladder for this session yet — it is collected "
                "going forward, from the next 1-minute poll.")


def divergence_panel(rows, ladder, symbol, dte_label, uid):
    """``(html, payload)`` for the Premium Divergence panel.

    ``payload`` is None when there is nothing to scrub — the caller then skips
    installing the script rather than binding a handler to an absent plot.
    """
    u = _id(uid)
    series = divergence_series(rows)
    geom = divergence_geometry(series)
    if geom is None:
        return _empty_panel(
            "PREMIUM DIVERGENCE",
            "No call/put premium collected for this session yet. The series is "
            "recorded going forward by the options service — it fills in from "
            "the next 1-minute poll."), None

    times = [fmt_time(ts) for ts in series["ts"]]
    status = session_pill(series["ts"])
    session = divergence_session(series)
    lad = align_ladder(series["ts"], ladder)
    has_ladder = any(rows_ is not None for rows_ in lad)

    chips = "".join([
        _chip("SPOT", "", C["spot"], C["spot"], f"{u}-cspot"),
        _chip("CALL PREM", "", C["call"], C["call"], f"{u}-ccall"),
        _chip("PUT PREM", "", C["put"], C["put"], f"{u}-cput"),
        _chip("NET", "", C["label"],
              f"linear-gradient(90deg,{C['call']},{C['put']})", f"{u}-cnet"),
    ])

    ladder_block = (
        f'<div style="margin:4px 24px 0;border-top:1px solid {_HAIR};'
        f'padding-top:14px">'
        f'<div style="display:flex;align-items:center;'
        f'justify-content:space-between;margin-bottom:11px">'
        f'<span style="font:500 9px/1 {MONO};letter-spacing:.2em;'
        f'color:rgba(174,205,232,.45)">STRIKE LADDER · PREMIUM BY STRIKE @ '
        f'<span id="{u}-ladtime">—</span></span>'
        f'<span style="display:flex;gap:18px">'
        f'<span style="display:flex;align-items:center;gap:6px;'
        f'font:400 9px/1 {MONO};letter-spacing:.14em;color:rgba(255,77,141,.8)">'
        f'<span style="width:10px;height:3px;background:{C["put"]}"></span>'
        f'PUT $M</span>'
        f'<span style="display:flex;align-items:center;gap:6px;'
        f'font:400 9px/1 {MONO};letter-spacing:.14em;color:rgba(53,200,255,.8)">'
        f'<span style="width:10px;height:3px;background:{C["call"]}"></span>'
        f'CALL $M</span></span></div>'
        f'<div id="{u}-lad" style="display:flex;flex-direction:column;gap:3px">'
        f'</div></div>')

    rail = (
        f'<div style="width:300px;flex:none;display:flex;'
        f'flex-direction:column;border-left:1px solid rgba(53,200,255,.12);'
        f'background:{_RAIL_BG}">'
        f'<div style="padding:20px 22px 16px;border-bottom:1px solid {_HAIR}">'
        f'<div style="font:500 9px/1 {MONO};letter-spacing:.24em;'
        f'color:rgba(174,205,232,.4);margin-bottom:9px">TIME</div>'
        f'<div id="{u}-time" style="font:600 20px/1 {MONO};'
        f'color:{C["title"]}">—</div></div>'
        f'<div style="display:flex;flex-direction:column">'
        + _rail_cell("SPOT", f"{u}-rspot", C["spot"])
        + _rail_cell("CALL PREMIUM", f"{u}-rcall", C["call"],
                     swatch=C["call"], unit="$M")
        + _rail_cell("PUT PREMIUM", f"{u}-rput", C["put"],
                     swatch=C["put"], unit="$M")
        + f'<div style="padding:18px 22px;background:rgba(53,200,255,.05);'
          f'border-bottom:1px solid {_HAIR}">'
          f'<div style="font:500 9px/1 {MONO};letter-spacing:.2em;'
          f'color:rgba(174,205,232,.45);margin-bottom:10px">'
          f'NET PREMIUM · CALL − PUT</div>'
          f'<div id="{u}-rnet" style="font:700 26px/1 {MONO};'
          f'color:{C["label"]}">—</div>'
        + _bipolar_bar(f"{u}-bar")
        + f'<div style="display:flex;justify-content:space-between;'
          f'margin-top:7px;font:400 9px/1 {MONO};letter-spacing:.14em;'
          f'color:rgba(174,205,232,.35)"><span>PUT-HEAVY</span>'
          f'<span>CALL-HEAVY</span></div></div></div>'
        + f'<div style="margin-top:auto;padding:18px 22px">'
          f'<div style="font:500 9px/1 {MONO};letter-spacing:.2em;'
          f'color:rgba(174,205,232,.4);margin-bottom:12px">SESSION SKEW</div>'
          f'<div style="display:flex;flex-direction:column;gap:9px">'
        + _skew_row("CALL TOTAL", f"{fmt_m(session['call_total'])} $M", C["call"])
        + _skew_row("PUT TOTAL", f"{fmt_m(session['put_total'])} $M", C["put"])
        + _skew_row("SESSION HIGH", fmt_price(session["high"]), C["spot"])
        + _skew_row("SESSION LOW", fmt_price(session["low"]), C["spot"])
        + '</div></div></div>')

    html = (
        f'<div class="fx-panel" id="{u}-root" style="display:flex;width:100%;'
        f'background:{_PANEL_BG};box-shadow:{_PANEL_SHADOW}">'
        f'<div style="flex:1;min-width:0;padding:20px 0 16px 4px">'
        f'<div style="display:flex;align-items:center;gap:12px;'
        f'padding:0 24px 14px;flex-wrap:wrap">'
        f'{_live_pill(status["text"], status["live"])}'
        f'{_title("PREMIUM DIVERGENCE")}'
        f'{_meta(f"{symbol} · {dte_label} · SESSION")}</div>'
        f'<div style="display:flex;align-items:center;gap:8px;'
        f'padding:0 24px 14px;flex-wrap:wrap">{chips}</div>'
        f'{divergence_svg(series, geom, times, u)}'
        f'{ladder_block if has_ladder else _ladder_placeholder()}'
        f'</div>{rail}</div>')

    payload = {
        "n": geom["n"], "def": geom["n"] - 1,
        "t": times,
        "spot": [None if _num(s) is None else round(s, 2)
                 for s in series["spot"]],
        "call": [round(v, 3) for v in series["call"]],
        "put": [round(v, 3) for v in series["put"]],
        "xs": [round(x, 1) for x in geom["xs"]],
        "ySpot": [None if y is None else round(y, 1) for y in geom["y_spot"]],
        "yCall": [round(y, 1) for y in geom["y_call"]],
        "yPut": [round(y, 1) for y in geom["y_put"]],
        "lad": lad if has_ladder else None,
        "col": {"call": C["call"], "put": C["put"], "spot": C["spot"],
                "label": C["label"], "title": C["title"]},
    }
    return html, payload


def _skew_row(label, value, color):
    return (f'<div style="display:flex;justify-content:space-between;'
            f'font:400 11px/1 {MONO};color:rgba(207,230,247,.75)">'
            f'<span>{_esc(label)}</span>'
            f'<span style="color:{color}">{_esc(value)}</span></div>')


def _ladder_placeholder():
    return (f'<div style="margin:4px 24px 0;border-top:1px solid {_HAIR};'
            f'padding-top:14px;font:400 11px/1.7 {MONO};'
            f'color:rgba(174,205,232,.45)">{_esc(LADDER_EMPTY)}</div>')


#############################################
# FLOW FIELD — panel
#############################################

def field_panel(rows_by_symbol, order, colors, mode, uid):
    """``(html, payload)`` for the Flow Field panel.

    ``mode`` is a KEY from ``FIELD_MODES`` ("dollars" / "skew"), not a display
    label — the panel owns the toggle, so it needs the value it will echo back.
    """
    u = _id(uid)
    mode = normalize_mode(mode)
    toggle = mode_toggle_html(u, mode)
    model = field_series(rows_by_symbol, order)
    geom = field_geometry(model)
    if geom is None:
        return _empty_panel(
            "FLOW FIELD",
            "Nothing to plot yet — select symbols above, or wait for the "
            "options service to collect this session's premium.",
            header_extra=toggle), None

    times = [fmt_time(ts) for ts in model["times"]]
    # Skew is a percentage of session premium; dollars carry their scale in the
    # footer, so a bare number there would be ambiguous only in skew.
    unit = "%" if mode == "skew" else ""

    chips = "".join(
        f'<span style="display:flex;align-items:center;gap:7px;'
        f'padding:6px 10px;background:{_CHIP_BG};box-shadow:{_CHIP_RING}">'
        f'<span style="width:12px;height:2px;'
        f'background:{colors.get(line["k"], C["label"])};'
        f'box-shadow:0 0 8px 1px {colors.get(line["k"], C["label"])}"></span>'
        f'<span style="font:500 9px/1 {MONO};letter-spacing:.12em;'
        f'color:rgba(174,205,232,.62)">{_esc(line["k"])}</span>'
        f'<span id="{u}-c{i}" style="font:600 11px/1 {MONO};'
        f'color:{colors.get(line["k"], C["label"])}">—</span></span>'
        for i, line in enumerate(geom["lines"]))

    board = "".join(
        f'<div id="{u}-row{i}" style="padding:13px 22px;'
        f'border-bottom:1px solid rgba(53,200,255,.07)">'
        f'<div style="display:flex;align-items:baseline;'
        f'justify-content:space-between;margin-bottom:8px">'
        f'<span style="display:flex;align-items:center;gap:9px">'
        f'<span style="width:8px;height:8px;'
        f'background:{colors.get(line["k"], C["label"])};'
        f'box-shadow:0 0 10px 1px {colors.get(line["k"], C["label"])}"></span>'
        f'<span style="font:600 14px/1 {MONO};color:#dff1ff;'
        f'letter-spacing:.06em">{_esc(line["k"])}</span></span>'
        f'<span id="{u}-v{i}" style="font:600 17px/1 {MONO};'
        f'color:{colors.get(line["k"], C["label"])}">—</span></div>'
        f'<div style="height:5px;background:rgba(190,248,255,.06);'
        f'position:relative">'
        f'<div style="position:absolute;top:0;left:50%;width:1px;height:5px;'
        f'background:rgba(190,248,255,.3)"></div>'
        f'<div id="{u}-b{i}" style="position:absolute;top:0;height:5px;'
        f'left:50%;width:0%"></div></div></div>'
        for i, line in enumerate(geom["lines"]))

    rail = (
        f'<div style="width:340px;flex:none;display:flex;flex-direction:column;'
        f'border-left:1px solid rgba(53,200,255,.12);background:{_RAIL_BG}">'
        f'<div style="padding:20px 22px 14px;border-bottom:1px solid {_HAIR}">'
        f'<div style="font:500 9px/1 {MONO};letter-spacing:.24em;'
        f'color:rgba(174,205,232,.4);margin-bottom:9px">'
        f'LEADERBOARD · CURSOR</div>'
        f'<div id="{u}-time" style="font:600 30px/1 {MONO};'
        f'color:{C["title"]}">—</div></div>'
        f'<div id="{u}-board" style="display:flex;flex-direction:column">'
        f'{board}</div>'
        f'<div style="margin-top:auto;padding:16px 22px">'
        f'<div style="display:flex;justify-content:space-between;'
        f'font:400 10px/1.9 {MONO};letter-spacing:.12em;'
        f'color:rgba(207,230,247,.7)"><span>MOST CALL-LED</span>'
        f'<span id="{u}-lead">—</span></div>'
        f'<div style="display:flex;justify-content:space-between;'
        f'font:400 10px/1.9 {MONO};letter-spacing:.12em;'
        f'color:rgba(207,230,247,.7)"><span>LEAST CALL-LED</span>'
        f'<span id="{u}-least">—</span></div></div></div>')

    n_sym = len(geom["lines"])
    # The SHORT status here, not the full sentence — this pill already carries
    # the symbol count, and "LAST SESSION · FRI 14 AUG · 8 SYMBOLS" wraps.
    status = session_pill(geom.get("times") or [])
    pill = (f"{status['short']} · {n_sym} SYMBOL"
            + ("" if n_sym == 1 else "S"))
    html = (
        f'<div class="fx-panel" id="{u}-root" style="display:flex;width:100%;'
        f'background:{_PANEL_BG};box-shadow:{_PANEL_SHADOW}">'
        f'<div style="flex:1;min-width:0;padding:20px 0 14px 4px">'
        f'<div style="display:flex;align-items:center;gap:12px;'
        f'padding:0 22px 14px;flex-wrap:wrap">'
        f'{_live_pill(pill, status["live"])}'
        f'{_title("NET PREMIUM")}'
        f'<span style="display:flex;align-items:center;gap:7px;'
        f'flex-wrap:wrap">{chips}</span>'
        f'{toggle}</div>'
        f'{field_svg(geom, times, colors, u, mode)}'
        f'</div>{rail}</div>')

    payload = {
        "n": geom["n"], "def": geom["n"] - 1, "t": times, "unit": unit,
        "xs": [round(x, 1) for x in geom["xs"]],
        "lines": [{"k": line["k"],
                   "c": colors.get(line["k"], C["label"]),
                   "v": [None if v is None else round(v, 3) for v in line["v"]],
                   "y": [None if y is None else round(y, 1) for y in line["y"]]}
                  for line in geom["lines"]],
        "col": {"label": C["label"], "call": C["call"], "put": C["put"]},
    }
    return html, payload


#############################################
# CLIENT-SIDE SCRUB
#############################################
# Shipped through ``ui.run_javascript`` (NOT ``ui.html``, so nothing here is
# sanitized) each time a panel repaints. The fragment is replaced wholesale on
# every repaint, so the DOM nodes are new and the listeners are bound fresh —
# there is no accumulation to guard against.
#
# Everything the cursor shows is computed here from coordinates the SERVER
# already derived (``xs``/``y*`` in payload). The alternative — re-deriving the
# scales in JS — would be two implementations of one mapping, and the first
# symptom of their drifting apart is a cursor dot sitting off its own line.

_SCRUB_JS = r"""
(function(){
  var id=__ID__, kind=__KIND__, D=__DATA__;
  if(!D) return;
  var g=function(s){return document.getElementById(id+'-'+s);};
  var root=g('root'), hit=g('hit'), cur=g('cur');
  if(!root||!hit||!cur) return;
  var n=D.n;
  function num(v,d){
    if(v===null||v===undefined) return '—';
    return v.toLocaleString(undefined,{minimumFractionDigits:d,
                                       maximumFractionDigits:d});
  }
  function sgn(v,d){
    if(v===null||v===undefined) return '—';
    // The unit suffix is empty in dollars (the footer states the scale) and '%'
    // in skew, where the two modes' magnitudes are otherwise indistinguishable.
    return (v>=0?'+':'−')+num(Math.abs(v),d)+(D.unit||'');
  }
  function dec(v){ v=Math.abs(v); return v>=100?0:(v>=10?1:2); }
  function txt(s,v){ var e=g(s); if(e) e.textContent=v; }
  function paint_text(s,v,c){ var e=g(s); if(e){e.textContent=v; e.style.color=c;} }
  function dot(s,x,y){
    var e=g(s); if(!e) return;
    if(y===null||y===undefined){ e.setAttribute('r','0'); return; }
    e.setAttribute('r', s==='dspot'?'3.5':(kind==='field'?'2.8':'3'));
    e.setAttribute('cx',x); e.setAttribute('cy',y);
  }
  function bar(e,v,max,cols){
    if(!e) return;
    var pct = max? Math.abs(v||0)/max*50 : 0;
    var c = (v||0)>=0 ? cols.call : cols.put;
    e.style.left = ((v||0)>=0 ? 50 : 50-pct)+'%';
    e.style.width = pct+'%';
    e.style.background = c;
    e.style.boxShadow = '0 0 12px 0 '+c;
  }

  var netMax=0;
  if(kind==='div'){
    for(var j=0;j<n;j++){
      var m=Math.abs(D.call[j]-D.put[j]); if(m>netMax) netMax=m;
    }
  }

  function ladder(i){
    var box=g('lad'); if(!box||!D.lad) return;
    var rows=D.lad[i];
    txt('ladtime', D.t[i]);
    if(!rows||!rows.length){
      box.innerHTML='<div style="font:400 10px/1.7 \'IBM Plex Mono\',monospace;'
        +'color:rgba(174,205,232,.4)">No ladder captured at this timestamp.</div>';
      return;
    }
    var mx=0, ci=-1, pi=-1, cv=-1, pv=-1, k, c, p;
    for(k=0;k<rows.length;k++){
      c=rows[k][1]/1e6; p=rows[k][2]/1e6;
      if(c>mx) mx=c; if(p>mx) mx=p;
      if(c>cv){cv=c;ci=k;} if(p>pv){pv=p;pi=k;}
    }
    var spot=D.spot[i], si=-1, sd=Infinity;
    if(spot!==null&&spot!==undefined){
      for(k=0;k<rows.length;k++){
        var d=Math.abs(rows[k][0]-spot); if(d<sd){sd=d;si=k;}
      }
    }
    var mono="'IBM Plex Mono',monospace", h='';
    for(k=0;k<rows.length;k++){
      c=rows[k][1]/1e6; p=rows[k][2]/1e6;
      var cw=mx?c/mx*100:0, pw=mx?p/mx*100:0;
      var tag = k===ci?'CALL WALL':(k===pi?'PUT WALL':'');
      var tc  = k===ci?D.col.call:D.col.put;
      var bg  = k===si?'rgba(190,248,255,.06)':'transparent';
      var kc  = k===si?D.col.title:'rgba(207,230,247,.72)';
      h += '<div style="display:flex;align-items:center;gap:10px;height:19px;'
         + 'background:'+bg+'">'
         + '<span style="width:46px;text-align:right;font:400 10px/1 '+mono
         + ';color:rgba(255,77,141,.7)">'+num(p,dec(p))+'</span>'
         + '<span style="flex:1;display:flex;justify-content:flex-end">'
         + '<span style="height:9px;width:'+pw.toFixed(1)+'%;background:'
         + 'linear-gradient(90deg,rgba(255,77,141,.2),'+D.col.put+')"></span></span>'
         + '<span style="width:72px;text-align:center;font:600 11px/1 '+mono
         + ';letter-spacing:.06em;color:'+kc+'">'+rows[k][0]+'</span>'
         + '<span style="flex:1;display:flex">'
         + '<span style="height:9px;width:'+cw.toFixed(1)+'%;background:'
         + 'linear-gradient(90deg,'+D.col.call+',rgba(53,200,255,.2))"></span></span>'
         + '<span style="width:46px;font:400 10px/1 '+mono
         + ';color:rgba(53,200,255,.7)">'+num(c,dec(c))+'</span>'
         + '<span style="width:70px;font:500 8px/1 '+mono
         + ';letter-spacing:.14em;color:'+tc+'">'+tag+'</span></div>';
    }
    box.innerHTML=h;
  }

  function paintDiv(i){
    var x=D.xs[i];
    cur.setAttribute('x1',x); cur.setAttribute('x2',x);
    dot('dcall',x,D.yCall[i]); dot('dput',x,D.yPut[i]); dot('dspot',x,D.ySpot[i]);
    var net=D.call[i]-D.put[i], nc = net>=0?D.col.call:D.col.put;
    txt('cspot', num(D.spot[i],2)); txt('ccall', num(D.call[i],1));
    txt('cput', num(D.put[i],1)); paint_text('cnet', sgn(net,2), nc);
    txt('time', D.t[i]);
    txt('rspot', num(D.spot[i],2)); txt('rcall', num(D.call[i],1));
    txt('rput', num(D.put[i],1)); paint_text('rnet', sgn(net,2), nc);
    bar(g('bar'), net, netMax, D.col);
    ladder(i);
  }

  function paintField(i){
    var x=D.xs[i];
    cur.setAttribute('x1',x); cur.setAttribute('x2',x);
    txt('time', D.t[i]);
    var live=[], dead=[], mx=0, k;
    for(k=0;k<D.lines.length;k++){
      var L=D.lines[k], v=L.v[i];
      dot('d'+k, x, L.y[i]);
      var chip=g('c'+k); if(chip) chip.textContent = sgn(v,1);
      if(v===null||v===undefined) dead.push({i:k}); else {
        live.push({i:k,k:L.k,c:L.c,v:v});
        if(Math.abs(v)>mx) mx=Math.abs(v);
      }
    }
    live.sort(function(a,b){ return b.v-a.v; });
    var board=g('board');
    live.concat(dead).forEach(function(o){
      var row=g('row'+o.i); if(row&&board) board.appendChild(row);
      var ve=g('v'+o.i);
      if(ve) ve.textContent = ('v' in o) ? sgn(o.v,1) : '—';
      bar(g('b'+o.i), ('v' in o)?o.v:0, mx, D.col);
    });
    if(live.length){
      var top=live[0], bot=live[live.length-1];
      paint_text('lead',  top.k+' '+sgn(top.v,1), top.c);
      paint_text('least', bot.k+' '+sgn(bot.v,1), bot.c);
    }
  }

  var paint = kind==='field' ? paintField : paintDiv;
  hit.addEventListener('mousemove', function(e){
    var r=hit.getBoundingClientRect();
    if(!r.width) return;
    var f=(e.clientX-r.left)/r.width;
    var i=Math.round(f*(n-1));
    paint(Math.max(0, Math.min(n-1, i)));
  });
  // Leaving the plot returns to the session's latest reading rather than
  // stranding the rail on wherever the pointer happened to exit.
  root.addEventListener('mouseleave', function(){ paint(D.def); });
  paint(D.def);
})();
"""


def scrub_js(uid, kind, payload):
    """The scrub script for one panel, with its data inlined.

    ``kind`` is ``"div"`` or ``"field"``. Returns "" when there is no payload,
    so the caller can hand the result straight to ``ui.run_javascript``.
    """
    if not payload:
        return ""
    import json
    return (_SCRUB_JS
            .replace("__ID__", json.dumps(_id(uid)))
            .replace("__KIND__", json.dumps("field" if kind == "field" else "div"))
            .replace("__DATA__", json.dumps(payload)))
