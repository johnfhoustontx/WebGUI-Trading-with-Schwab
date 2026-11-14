"""The Open Interest tab on Dealer Positioning: open interest by strike and
expiry, with a put and call ladder for one expiry.

It replaced the Term tab (net gamma by strike and expiry) on 2026-11-14 and
reads the same per-expiry grid the service already built for it
(``snapshot["term"]``), whose cells now carry ``call_oi`` and ``put_oi``.

**Why expiry and not time.** Open interest is published once a day, so an
intraday time axis would repeat one column all session. The axis that varies is
the expiry.

**What it does not say.** A count of open contracts says where positions sit,
not who holds which side. Nothing here is labelled as dealer positioning.

**Pure builders, one fragment.** ``model`` turns the grid into rows and
columns; ``panel`` draws them as one ``ui.html`` fragment (SVG plus inline
styles, the same documented exemption as ``flow_panels``); ``bind_js`` wires
the two controls in the browser: which expiry the ladder shows, and Total or
Net. The choice is kept on ``window`` so a repaint (one a minute) does not
reset it. The sanitizer rules in ``flow_panels``' docstring apply here too, and
``tests/test_gamma_oi.py`` checks the emitted surface against the same list.

The /ES and /NQ feed never shows this tab (``gamma_feed._HIDDEN_VIEWS``): its
counts are scaled by contract size and are no number of contracts.
"""
import datetime as _dt
import json
import math

from pages import fmt as _fmt
from pages.options import flow_panels as _fx
from pages.options.theme import FLOW_COLORS as C
from shared import gamma_heat_config

VIEW = "Open Interest"
TITLE = "OPEN INTEREST"
MODES = (("total", "TOTAL"), ("net", "NET CALLS LESS PUTS"))

NO_CHAIN = ("No option chain for this symbol yet. It fills from the next "
            "refresh.")
ALL_ZERO = ("Every contract reads zero open interest right now. Schwab clears "
            "index open interest outside the session; it returns when the "
            "market opens.")
NO_STRIKES = "No strike with open interest sits near the price."

# ── geometry (viewBox units) ────────────────────────────────────────────────
VB_W = 980.0
ROW_H = 14.0
TOP = 30.0                      # expiry labels sit above the cells
BOTTOM = 14.0
HEAT_X0, HEAT_X1 = 58.0, 566.0
LAD_X0, LAD_X1 = 640.0, 968.0
LAD_GUTTER = 50.0               # the strike labels between puts and calls
CELL_GAP = 1.0
MIN_ALPHA, MAX_ALPHA = 0.04, 0.92


#############################################
# MODEL
#############################################

def _count(value):
    """Open interest as a finite float of zero or more, else 0."""
    n = _fx._num(value)
    return n if n is not None and n > 0 else 0.0


def _strikes(cells):
    """``{strike: (calls, puts)}`` for one expiry. Keys come back from Redis as
    strings; one that is not a number is dropped."""
    out = {}
    for key, cell in (cells or {}).items() if isinstance(cells, dict) else ():
        try:
            strike = float(key)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(strike) or not isinstance(cell, dict):
            continue
        out[strike] = (_count(cell.get("call_oi")), _count(cell.get("put_oi")))
    return out


def walls(strikes, spot):
    """``(call_wall, put_wall)``: the strike with the most call interest above
    the price and the one with the most put interest below it. ``None`` where
    that side holds nothing."""
    above = [(c, k) for k, (c, _p) in strikes.items() if k > spot and c > 0]
    below = [(p, k) for k, (_c, p) in strikes.items() if k < spot and p > 0]
    return (max(above)[1] if above else None, max(below)[1] if below else None)


def max_pain(strikes):
    """The strike at which the open contracts are worth the least in total at
    expiry, or ``None`` with nothing open. One pass each way over the sorted
    strikes rather than every strike against every other."""
    ks = sorted(k for k, (c, p) in strikes.items() if c + p > 0)
    if not ks:
        return None
    pay = [0.0] * len(ks)
    count = weight = 0.0
    for i, k in enumerate(ks):                 # calls below the settle price
        pay[i] += k * count - weight
        count += strikes[k][0]
        weight += strikes[k][0] * k
    count = weight = 0.0
    for i in range(len(ks) - 1, -1, -1):       # puts above it
        pay[i] += weight - ks[i] * count
        count += strikes[ks[i]][1]
        weight += strikes[ks[i]][1] * ks[i]
    return ks[min(range(len(ks)), key=pay.__getitem__)]


def _row_step(window, max_rows):
    """``None`` when every strike gets a row of its own, else the width of the
    steps the strikes are summed into so no more than ``max_rows`` are drawn."""
    if len(window) <= max_rows:
        return None
    gaps = [b - a for a, b in zip(window, window[1:]) if b - a > 0]
    base = min(gaps) if gaps else 1.0
    step = base * math.ceil(len(window) / max_rows)
    while (window[-1] - window[0]) / step + 1 > max_rows:
        step += base
    return step


def _quantile(values, q):
    values = sorted(v for v in values if v > 0)
    if not values:
        return 0.0
    return values[min(len(values) - 1, int(q * len(values)))]


def _label(date):
    try:
        day = _dt.date.fromisoformat(str(date))
    except ValueError:
        return str(date)
    return f"{day.strftime('%b')} {day.day}"


def model(term_grid, spot, cfg=None):
    """``(model, reason)``. ``model`` is ``None`` when there is nothing to
    draw and ``reason`` is then the sentence to show.

    ``model`` holds ``spot``; ``step`` (see ``_row_step``); ``rows`` (strikes,
    highest first); ``expiries``, each ``{date, label, calls, puts, ratio,
    call_wall, put_wall, max_pain, cells}`` where ``cells`` is one ``(calls,
    puts)`` per row; and ``cap_total`` / ``cap_net``, where the colour scale
    tops out. Totals, walls and max pain are over the WHOLE chain; only the
    rows are cut to the strikes near the price."""
    cfg = cfg or gamma_heat_config.open_interest()
    grid = term_grid if isinstance(term_grid, dict) else {}
    dates = [d for d in (grid.get("expirations") or []) if isinstance(d, str)]
    raw_cells = grid.get("cells") if isinstance(grid.get("cells"), dict) else {}
    spot = _fx._num(spot) or _fx._num(grid.get("underlying_price"))
    if not dates or not spot or spot <= 0:
        return None, NO_CHAIN
    chains = {d: _strikes(raw_cells.get(d)) for d in dates}
    if not any(c + p for ks in chains.values() for c, p in ks.values()):
        return None, ALL_ZERO

    span = spot * cfg["span_pct"] / 100.0
    window = sorted({k for ks in chains.values() for k, (c, p) in ks.items()
                     if abs(k - spot) <= span and c + p > 0})
    if not window:
        return None, NO_STRIKES
    step = _row_step(window, cfg["max_rows"])

    def row_of(strike):
        return strike if step is None else round(strike / step) * step

    rows = sorted({row_of(k) for k in window}, reverse=True)
    index = {r: i for i, r in enumerate(rows)}
    expiries = []
    for date in dates:
        ks = chains[date]
        cells = [[0.0, 0.0] for _ in rows]
        for k, (c, p) in ks.items():
            i = index.get(row_of(k)) if abs(k - spot) <= span else None
            if i is not None:
                cells[i][0] += c
                cells[i][1] += p
        calls = sum(c for c, _p in ks.values())
        puts = sum(p for _c, p in ks.values())
        call_wall, put_wall = walls(ks, spot)
        expiries.append({
            "date": date, "label": _label(date), "calls": calls, "puts": puts,
            "ratio": puts / calls if calls > 0 else None,
            "call_wall": call_wall, "put_wall": put_wall,
            "max_pain": max_pain(ks),
            "cells": [tuple(c) for c in cells],
            "wall_rows": tuple(index.get(row_of(w)) if w is not None
                               and abs(w - spot) <= span else None
                               for w in (call_wall, put_wall)),
        })
    every = [c for e in expiries for c in e["cells"]]
    return {
        "spot": spot, "step": step, "rows": rows, "expiries": expiries,
        "cap_total": _quantile([c + p for c, p in every], cfg["cap_quantile"]),
        "cap_net": _quantile([abs(c - p) for c, p in every], cfg["cap_quantile"]),
        "cap_pct": round(cfg["cap_quantile"] * 100),
    }, ""


def summary(mdl):
    """The status strip's line for this tab."""
    if not mdl:
        return VIEW
    calls = sum(e["calls"] for e in mdl["expiries"])
    puts = sum(e["puts"] for e in mdl["expiries"])
    return (f"{VIEW}  ·  calls {calls:,.0f}  ·  puts {puts:,.0f}  ·  "
            f"{len(mdl['expiries'])} expiries")


#############################################
# DRAWING
#############################################

def _alpha(value, cap):
    """Opacity for a cell: the square root of its share of the cap, so the
    small cells stay visible beside the large ones."""
    if cap <= 0 or value <= 0:
        return 0.0
    return MIN_ALPHA + (MAX_ALPHA - MIN_ALPHA) * math.sqrt(min(1.0, value / cap))


def _spot_y(rows, spot):
    """The price's height among the rows (highest strike first), between the
    two rows it sits between. Clamped to the plot."""
    centre = [TOP + (i + 0.5) * ROW_H for i in range(len(rows))]
    if spot >= rows[0]:
        return TOP
    if spot <= rows[-1]:
        return TOP + len(rows) * ROW_H
    for i in range(len(rows) - 1):
        hi, lo = rows[i], rows[i + 1]
        if lo <= spot <= hi:
            return centre[i] + (hi - spot) / (hi - lo) * (centre[i + 1] - centre[i])
    return centre[-1]


def _cell_title(exp, strike, calls, puts):
    return (f"<title>{_fx._esc(exp['label'])} · {_fmt.strike(strike)} · "
            f"calls {calls:,.0f} · puts {puts:,.0f}</title>")


def _heat_svg(mdl, u):
    rows, exps = mdl["rows"], mdl["expiries"]
    col_w = (HEAT_X1 - HEAT_X0) / len(exps)
    label_every = max(1, math.ceil(len(rows) / 16))
    parts = []
    for i, strike in enumerate(rows):
        if i % label_every == 0:
            parts.append(_fx._text(HEAT_X0 - 8, TOP + (i + 0.5) * ROW_H,
                                   _fmt.strike(strike), 9, C["label"],
                                   anchor="end", opacity="0.6"))
    for j, exp in enumerate(exps):
        parts.append(
            f'<g id="{u}-h-{j}" style="cursor:pointer">'
            + _fx._text(HEAT_X0 + (j + 0.5) * col_w, TOP - 14, exp["label"],
                        10, C["title"], node_id=f"{u}-hl-{j}")
            + "</g>")
    for mode in ("total", "net"):
        hidden = "" if mode == "total" else ' display="none"'
        parts.append(f'<g id="{u}-g-{mode}"{hidden}>')
        for j, exp in enumerate(exps):
            x = HEAT_X0 + j * col_w
            parts.append(f'<g id="{u}-{mode}-{j}" style="cursor:pointer">')
            for i, (calls, puts) in enumerate(exp["cells"]):
                if mode == "total":
                    fill = _fx._tone("ice", f"{_alpha(calls + puts, mdl['cap_total']):.3f}")
                else:
                    side = "call_deep" if calls >= puts else "put_deep"
                    fill = _fx._tone(side, f"{_alpha(abs(calls - puts), mdl['cap_net']):.3f}")
                parts.append(
                    f'<rect x="{x + CELL_GAP / 2:.1f}" y="{TOP + i * ROW_H + CELL_GAP / 2:.1f}" '
                    f'width="{col_w - CELL_GAP:.1f}" height="{ROW_H - CELL_GAP:.1f}" '
                    f'fill="{fill}">{_cell_title(exp, rows[i], calls, puts)}</rect>')
            parts.append("</g>")
        parts.append("</g>")
    # The walls, over both modes: an outline, so it reads on any colour.
    for j, exp in enumerate(exps):
        for i in exp["wall_rows"]:
            if i is not None:
                parts.append(
                    f'<rect x="{HEAT_X0 + j * col_w + 1:.1f}" y="{TOP + i * ROW_H + 1:.1f}" '
                    f'width="{col_w - 2:.1f}" height="{ROW_H - 2:.1f}" fill="none" '
                    f'stroke="{C["title"]}" stroke-width="1.2" '
                    f'style="pointer-events:none"></rect>')
    # The chosen expiry's column; bind_js moves it.
    parts.append(
        f'<rect id="{u}-sel" x="{HEAT_X0:.1f}" y="{TOP - 2:.1f}" width="{col_w:.1f}" '
        f'height="{len(rows) * ROW_H + 4:.1f}" fill="none" stroke="{C["ice"]}" '
        f'stroke-opacity="0.55" stroke-width="1" style="pointer-events:none"></rect>')
    return "".join(parts)


def _ladder_svg(mdl, u):
    rows = mdl["rows"]
    mid = (LAD_X0 + LAD_X1) / 2.0
    put_x, call_x = mid - LAD_GUTTER / 2.0, mid + LAD_GUTTER / 2.0
    width = (LAD_X1 - LAD_X0 - LAD_GUTTER) / 2.0
    label_every = max(1, math.ceil(len(rows) / 24))
    parts = [_fx._text(put_x, TOP - 14, "PUTS", 9, C["label"], anchor="end",
                       opacity="0.6", spacing="0.16em"),
             _fx._text(call_x, TOP - 14, "CALLS", 9, C["label"], anchor="start",
                       opacity="0.6", spacing="0.16em")]
    for i, strike in enumerate(rows):
        if i % label_every == 0:
            parts.append(_fx._text(mid, TOP + (i + 0.5) * ROW_H, _fmt.strike(strike),
                                   9, C["label"], opacity="0.6"))
    for j, exp in enumerate(mdl["expiries"]):
        top = max((max(c, p) for c, p in exp["cells"]), default=0.0)
        hidden = "" if j == 0 else ' display="none"'
        parts.append(f'<g id="{u}-lad-{j}"{hidden}>')
        for i, (calls, puts) in enumerate(exp["cells"]):
            y = TOP + i * ROW_H + 2
            for value, side, x0, sign, wall in (
                    (puts, "put_deep", put_x, -1, exp["wall_rows"][1]),
                    (calls, "call_deep", call_x, 1, exp["wall_rows"][0])):
                w = width * value / top if top > 0 else 0.0
                if w <= 0:
                    continue
                edge = (f' stroke="{C["title"]}" stroke-width="1.2"'
                        if wall == i else "")
                parts.append(
                    f'<rect x="{(x0 - w if sign < 0 else x0):.1f}" y="{y:.1f}" '
                    f'width="{w:.1f}" height="{ROW_H - 4:.1f}" '
                    f'fill="{_fx._tone(side, ".85")}"{edge}>'
                    f'{_cell_title(exp, rows[i], calls, puts)}</rect>')
        parts.append("</g>")
    return "".join(parts)


def _svg(mdl, u):
    height = TOP + len(mdl["rows"]) * ROW_H + BOTTOM
    y = _spot_y(mdl["rows"], mdl["spot"])
    spot_line = "".join(
        f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" '
        f'stroke="{C["spot"]}" stroke-width="1" stroke-dasharray="4 3" '
        f'style="pointer-events:none"></line>'
        for x0, x1 in ((HEAT_X0, HEAT_X1), (LAD_X0, LAD_X1)))
    spot_text = _fx._text((HEAT_X1 + LAD_X0) / 2.0, y,
                          _fmt.price(mdl["spot"]), 9, C["spot"])
    return (f'<svg viewBox="0 0 {VB_W:.0f} {height:.0f}" width="100%" '
            f'style="display:block">{_heat_svg(mdl, u)}{_ladder_svg(mdl, u)}'
            f'{spot_line}{spot_text}</svg>')


def _segment_style(on):
    fill = (f"color:{C['panel_to']};background:{C['ice']}" if on else
            "color:rgba(174,205,232,.5);background:transparent")
    return (f"padding:7px 13px;font:500 9px/1 {_fx.MONO};letter-spacing:.14em;"
            f"cursor:pointer;user-select:none;{fill}")


def _mode_toggle(u):
    segments = "".join(
        f'<span id="{u}-m-{key}" style="{_segment_style(key == "total")}">'
        f'{_fx._esc(label)}</span>' for key, label in MODES)
    return (f'<div style="display:flex;margin-left:auto;'
            f'box-shadow:inset 0 0 0 1px {_fx._tone("ice", ".12")}">{segments}</div>')


def _stat(label, value):
    return (f'<div style="padding:10px 14px;background:{_fx._CHIP_BG};'
            f'box-shadow:{_fx._CHIP_RING};min-width:0">'
            f'<div style="font:500 9px/1 {_fx.MONO};letter-spacing:.16em;'
            f'color:rgba(174,205,232,.55);margin-bottom:8px">{_fx._esc(label)}</div>'
            f'<div style="font:600 16px/1 {_fx.MONO};color:{C["title"]}">'
            f'{_fx._esc(value)}</div></div>')


def _strike_or_dash(value):
    return _fmt.strike(value) if value is not None else "—"


def _stats(mdl, u):
    blocks = []
    for j, exp in enumerate(mdl["expiries"]):
        ratio = _fmt.ratio(exp["ratio"]) if exp["ratio"] is not None else "—"
        cells = "".join((
            _stat(f"{exp['label']} call wall", _strike_or_dash(exp["call_wall"])),
            _stat("Put wall", _strike_or_dash(exp["put_wall"])),
            _stat("Max pain", _strike_or_dash(exp["max_pain"])),
            _stat("Put / call ratio", ratio),
            _stat("Calls", f"{exp['calls']:,.0f}"),
            _stat("Puts", f"{exp['puts']:,.0f}")))
        shown = "grid" if j == 0 else "none"
        blocks.append(
            f'<div id="{u}-st-{j}" style="display:{shown};gap:10px;'
            f'grid-template-columns:repeat(6,minmax(0,1fr));'
            f'margin-bottom:14px">{cells}</div>')
    return "".join(blocks)


def _footnote(mdl):
    notes = [f"Colour tops out at the {mdl['cap_pct']}th percentile of the cells shown",
             "outlined: the most call interest above the price and the most put "
             "interest below it",
             "walls, max pain and totals are over the whole chain"]
    if mdl["step"] is not None:
        notes.append(f"each row sums the strikes within {_fmt.strike(mdl['step'])}")
    return (f'<div style="font:400 10px/1.6 {_fx.MONO};'
            f'color:rgba(174,205,232,.45);margin-top:10px">'
            f'{_fx._esc(" · ".join(notes))}. Click an expiry to see its ladder.</div>')


def panel(term_grid, spot, symbol, uid, cfg=None):
    """``(html, model)`` for the tab. ``model`` is ``None`` in the empty state,
    which keeps the panel's frame and says why it is empty."""
    mdl, reason = model(term_grid, spot, cfg)
    if mdl is None:
        return _fx._empty_panel(TITLE, reason), None
    u = _fx._id(uid)
    header = (f'<div style="display:flex;align-items:center;gap:12px;'
              f'margin-bottom:16px">{_fx._title(TITLE)}'
              f'{_fx._meta(str(symbol or "").upper() + " · BY STRIKE AND EXPIRY")}'
              f'{_mode_toggle(u)}</div>')
    return (f'<div class="fx-panel" style="width:100%;background:{_fx._PANEL_BG};'
            f'box-shadow:{_fx._PANEL_SHADOW}"><div style="padding:20px 24px">'
            f'{header}{_stats(mdl, u)}{_svg(mdl, u)}{_footnote(mdl)}'
            f'</div></div>'), mdl


#############################################
# THE TWO CONTROLS
#############################################

_BIND_JS = r"""
(function(){
  var D=__DATA__, id=D.id;
  var S=window.__oiMap=window.__oiMap||{};
  function el(s){ return document.getElementById(id+'-'+s); }
  // An SVG group is hidden by its display ATTRIBUTE, which clearing a style
  // would leave in force; the figures are a div and take a style.
  function show(node, on){ if(node) node.setAttribute('display', on ? 'inline' : 'none'); }
  function apply(){
    var j=D.dates.indexOf(S.date); if(j<0) j=0;
    var mode=(S.mode==='net')?'net':'total';
    for(var i=0;i<D.dates.length;i++){
      show(el('lad-'+i), i===j);
      var st=el('st-'+i); if(st) st.style.display = i===j ? 'grid' : 'none';
      var h=el('hl-'+i); if(h) h.setAttribute('fill-opacity', i===j ? '1' : '0.5');
    }
    var sel=el('sel'); if(sel) sel.setAttribute('x', D.x0 + j*D.colW);
    ['total','net'].forEach(function(m){
      show(el('g-'+m), m===mode);
      var seg=el('m-'+m); if(seg) seg.setAttribute('style', m===mode ? D.on : D.off);
    });
  }
  D.dates.forEach(function(date, i){
    ['h-'+i,'total-'+i,'net-'+i].forEach(function(s){
      var node=el(s);
      if(node) node.addEventListener('click', function(){ S.date=date; apply(); });
    });
  });
  ['total','net'].forEach(function(m){
    var seg=el('m-'+m);
    if(seg) seg.addEventListener('click', function(){ S.mode=m; apply(); });
  });
  apply();
})();
"""


def bind_js(uid, mdl):
    """The script that wires the expiry columns and the Total / Net toggle, or
    "" in the empty state. The choice lives on ``window`` keyed by expiry DATE,
    so it survives the next repaint and falls back to the nearest expiry once
    the chosen one has gone."""
    if not mdl:
        return ""
    data = {"id": _fx._id(uid),
            "dates": [e["date"] for e in mdl["expiries"]],
            "x0": HEAT_X0, "colW": (HEAT_X1 - HEAT_X0) / len(mdl["expiries"]),
            "on": _segment_style(True), "off": _segment_style(False)}
    return _BIND_JS.replace("__DATA__", json.dumps(data))
