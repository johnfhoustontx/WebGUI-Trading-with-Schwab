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

**The layout** (redesigned 2026-11-14). A level rail on top places the price
among the put wall, max pain and the call wall, with each one's distance. Under
it the strike map puts the expiry cells and the put and call bars on ONE strike
axis, row for row, with the walls and max pain tagged on their rows. A side
rail states the read in sentences the code writes from the counts, lists the
levels and gives the whole chain's split.

**Pure builders, one fragment.** ``model`` turns the grid into rows and
columns; ``panel`` draws them as one ``ui.html`` fragment (inline styles, the
same documented exemption as ``flow_panels``); ``bind_js`` wires the two
controls in the browser: which expiry the bars, rail and read show, and Total
or Net. The choice is kept on ``window`` so a repaint (one a minute) does not
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
TITLE = "Open Interest"
MODES = (("total", "Total"), ("net", "Net calls less puts"))

NO_CHAIN = ("No option chain for this symbol yet. It fills from the next "
            "refresh.")
ALL_ZERO = ("Every contract reads zero open interest right now. Schwab clears "
            "index open interest outside the session; it returns when the "
            "market opens.")
NO_STRIKES = "No strike with open interest sits near the price."

# ── geometry (CSS pixels) ───────────────────────────────────────────────────
ROW_H = 18.0                    # one strike row; every column shares it
TOP = 0.0                       # the rows start under the columns' headers
CELL_H = 15.0
BAR_H = 12.0
HEAD_H = 62.0                   # every column's header, expiry buttons included
HEAD_GAP = 10.0                 # clear space between a header and the rows
STRIKE_W = 64.0
HEAT_SHOWN = 5                  # expiries on screen before the strip scrolls
HEAT_COL_W = 80.0               # one expiry's width once it does
HEAT_GAP = 3.0
HEAT_W = HEAT_SHOWN * (HEAT_COL_W + HEAT_GAP) - HEAT_GAP
# strike · expiry strip · puts · calls · tag
COLUMNS = (f"{STRIKE_W:.0f}px {HEAT_W:.0f}px minmax(0,1fr) minmax(0,1fr) "
           f"150px")
MAP_MIN_W = 860.0
RAIL_H = 126                    # the level rail's height
RAIL_LINE = 46                  # where its line sits
MIN_ALPHA, MAX_ALPHA = 0.06, 0.95
DIM_COLUMN = "0.55"             # an expiry column that is not the chosen one
RAIL_PAD = 6.0                  # per cent kept clear at each end of the rail


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


def _sub(date, first):
    """The line under an expiry's label: its weekday, ``next`` for the nearest
    and ``monthly`` for a third Friday."""
    try:
        day = _dt.date.fromisoformat(str(date))
    except ValueError:
        return "next" if first else ""
    parts = [day.strftime("%a")]
    if first:
        parts.append("next")
    if day.weekday() == 4 and 15 <= day.day <= 21:
        parts.append("monthly")
    return " · ".join(parts)


def _largest(strikes, side):
    """The strike holding the most calls (``side`` 0) or puts (1), anywhere on
    the chain, or ``None`` with none open."""
    held = [(v[side], k) for k, v in strikes.items() if v[side] > 0]
    return max(held)[1] if held else None


def model(term_grid, spot, cfg=None):
    """``(model, reason)``. ``model`` is ``None`` when there is nothing to
    draw and ``reason`` is then the sentence to show.

    ``model`` holds ``spot``; ``step`` (see ``_row_step``); ``rows`` (strikes,
    highest first); ``calls`` / ``puts`` over every expiry; ``expiries``, each
    ``{date, label, sub, calls, puts, ratio, call_wall, put_wall, max_pain,
    top_call, top_put, cells, wall_rows, pain_row}`` where ``cells`` is one
    ``(calls, puts)`` per row; and ``cap_total``, where the colour scale tops
    out. Totals, walls and max pain are over the WHOLE chain; only the rows
    are cut to the strikes near the price."""
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

    def drawn_row(level):
        """The row a level sits on, or ``None`` when it has no row."""
        if level is None or abs(level - spot) > span:
            return None
        return index.get(row_of(level))

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
        pain = max_pain(ks)
        expiries.append({
            "date": date, "label": _label(date),
            "sub": _sub(date, not expiries), "calls": calls, "puts": puts,
            "ratio": puts / calls if calls > 0 else None,
            "call_wall": call_wall, "put_wall": put_wall, "max_pain": pain,
            "top_call": _largest(ks, 0), "top_put": _largest(ks, 1),
            "cells": [tuple(c) for c in cells],
            "wall_rows": (drawn_row(call_wall), drawn_row(put_wall)),
            "pain_row": drawn_row(pain),
        })
    every = [c for e in expiries for c in e["cells"]]
    return {
        "spot": spot, "step": step, "rows": rows, "expiries": expiries,
        "calls": sum(e["calls"] for e in expiries),
        "puts": sum(e["puts"] for e in expiries),
        "cap_total": _quantile([c + p for c, p in every], cfg["cap_quantile"]),
        "cap_pct": round(cfg["cap_quantile"] * 100),
    }, ""


def summary(mdl):
    """The status strip's line for this tab."""
    if not mdl:
        return VIEW
    return (f"{VIEW}  ·  calls {mdl['calls']:,.0f}  ·  puts {mdl['puts']:,.0f}  ·  "
            f"{len(mdl['expiries'])} expiries")


#############################################
# THE READ
#############################################

def _lean(calls, puts):
    """Which side holds more contracts, as a clause."""
    if calls <= 0 and puts <= 0:
        return "nothing is open"
    if calls <= 0:
        return "only puts are open"
    if puts <= 0:
        return "only calls are open"
    if puts == calls:
        return "puts and calls are even"
    if puts > calls:
        return f"puts outnumber calls {_fmt.ratio(puts / calls)} to 1"
    return f"calls outnumber puts {_fmt.ratio(calls / puts)} to 1"


def _away(level, spot):
    """How far a level is from the price, in per cent of the price."""
    return (level - spot) / spot * 100.0


def read(mdl, exp):
    """The side rail's sentences for one expiry. Each states a count or a
    distance the model holds; none says who holds which side."""
    spot, label = mdl["spot"], exp["label"]
    out = []
    pain = exp["max_pain"]
    if pain is not None:
        gap = _away(pain, spot)
        if abs(gap) < 0.005:
            out.append(f"The price is at max pain ({_fmt.strike(pain)}).")
        else:
            side = "under" if gap > 0 else "over"
            out.append(f"The price is {_fmt.pct(abs(gap))} {side} max pain "
                       f"({_fmt.strike(pain)}).")
    if exp["top_put"] is not None and exp["top_put"] > spot:
        out.append(f"The strike with the most {label} puts is above the "
                   f"price, at {_fmt.strike(exp['top_put'])}.")
    if exp["top_call"] is not None and exp["top_call"] < spot:
        out.append(f"The strike with the most {label} calls is below the "
                   f"price, at {_fmt.strike(exp['top_call'])}.")
    lean = f"On {label} {_lean(exp['calls'], exp['puts'])}."
    if len(mdl["expiries"]) > 1:
        lean += (f" Over all {len(mdl['expiries'])} expiries "
                 f"{_lean(mdl['calls'], mdl['puts'])}.")
    out.append(lean)
    sides = []
    if exp["put_wall"] is not None:
        sides.append(f"the put wall at {_fmt.strike(exp['put_wall'])} is "
                     f"{_fmt.pct(abs(_away(exp['put_wall'], spot)))} below the price")
    if exp["call_wall"] is not None:
        sides.append(f"the call wall at {_fmt.strike(exp['call_wall'])} is "
                     f"{_fmt.pct(abs(_away(exp['call_wall'], spot)))} above it"
                     if sides else
                     f"the call wall at {_fmt.strike(exp['call_wall'])} is "
                     f"{_fmt.pct(abs(_away(exp['call_wall'], spot)))} above the price")
    if sides:
        text = " and ".join(sides)
        out.append(text[0].upper() + text[1:] + ".")
    return out


#############################################
# DRAWING
#############################################
# Type follows the app: no font is named here, so the panel inherits the app's
# family, and every size is one of the app's own text classes (the ones
# ``theme.build_typography_css`` resizes from ``[typography]``).
T_TITLE = "text-subtitle1"       # the panel's title
T_HEAD = "text-subtitle2"        # a card's heading, and a figure in a line
T_BIG = "text-h6"                # a tile's one large figure
T_SMALL = "text-xs"              # captions, row labels, tags

_DIM = _fx._tone("label", ".62")
_FAINT = _fx._tone("label", ".42")
_RULE = _fx._tone("ice", ".10")
# What each level wears, on the rail, on its row's tag and in the Levels list.
_LEVEL_COLOR = {"call": C["call"], "pain": C["ice"], "put": C["put"],
                "spot": C["spot"]}
_LEVEL_NAME = {"call": "Call wall", "pain": "Max pain", "put": "Put wall"}


def _alpha(value, cap):
    """Opacity for a cell: the square root of its share of the cap, so the
    small cells stay visible beside the large ones."""
    if cap <= 0 or value <= 0:
        return 0.0
    return MIN_ALPHA + (MAX_ALPHA - MIN_ALPHA) * math.sqrt(min(1.0, value / cap))


def _cell_fill(value, cap):
    return _fx._tone("ice", format(_alpha(value, cap), ".3f"))


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


def _tip(exp, strike, calls, puts):
    return (f"{exp['label']} · {_fmt.strike(strike)} · "
            f"calls {calls:,.0f} · puts {puts:,.0f}")


def _span(cls, body, style="", node_id=""):
    """Text in one of the app's sizes. ``body`` is already safe HTML."""
    ident = f' id="{node_id}"' if node_id else ""
    return f'<span{ident} class="{cls}" style="{style}">{body}</span>'


def _card(body, extra="", node_id=""):
    ident = f' id="{node_id}"' if node_id else ""
    return (f'<div{ident} style="background:{_fx._CHIP_BG};'
            f'box-shadow:{_fx._CHIP_RING};padding:16px 18px;min-width:0;{extra}">'
            f'{body}</div>')


def _heading(text, note=""):
    note = _span(T_SMALL, _fx._esc(note), f"color:{_FAINT}") if note else ""
    return (_span(T_HEAD, _fx._esc(text), f"font-weight:600;color:{C['title']}")
            + note)


def _signed(value):
    """A distance in price units with a true minus sign."""
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{_fmt.price(abs(value))}"


def _signed_pct(level, spot):
    return _fmt.pct(_away(level, spot), signed=True).replace("-", "−")


def _distance(level, spot):
    """``+19.49 · +2.60%``: how far a level is from the price."""
    return f"{_signed(level - spot)} · {_signed_pct(level, spot)}"


def _split_bar(calls, puts):
    share = calls / (calls + puts) * 100.0 if calls + puts > 0 else 50.0
    return (f'<div style="display:flex;height:8px;gap:2px">'
            f'<div style="width:{share:.1f}%;background:{C["call"]}"></div>'
            f'<div style="flex:1 1 0;background:{C["put"]}"></div></div>')


def _split_head(calls, puts):
    def number(value, color):
        return _span(T_HEAD, f"{value:,.0f}", f"font-weight:600;color:{color}")

    return (f'<div class="{T_SMALL}" style="display:flex;'
            f'justify-content:space-between;align-items:baseline;color:{_DIM}">'
            f'<span>Calls {number(calls, C["call"])}</span>'
            f'<span>{number(puts, C["put"])} Puts</span></div>')


# ── the level rail ──────────────────────────────────────────────────────────

def _rail_marker(left, kind, value, distance, top, tick, below=False):
    """One level on the rail: its name, its strike, a tick to the line and its
    distance from the price. ``below`` hangs it under the line."""
    color = _LEVEL_COLOR[kind]
    name = _span(T_SMALL, _LEVEL_NAME[kind],
                 f"line-height:1;font-weight:600;color:{color};white-space:nowrap")
    strike = _span(T_HEAD, value, f"line-height:1;font-weight:600;color:{C['title']}")
    tick_html = f'<span style="width:2px;height:{tick}px;background:{color}"></span>'
    away = _span(T_SMALL, distance, f"line-height:1;color:{_DIM};white-space:nowrap")
    parts = ((tick_html, strike, name, away) if below else
             (name, strike, tick_html, away))
    return (f'<div style="position:absolute;left:{left:.1f}%;top:{top}px;'
            f'transform:translateX(-50%);display:flex;flex-direction:column;'
            f'align-items:center;gap:5px">{"".join(parts)}</div>')


def _tile(label, body):
    return (f'<div style="display:flex;flex-direction:column;gap:7px;'
            f'padding:12px 14px;background:{_fx._tone("ice", ".04")};min-width:0">'
            f'{_span(T_SMALL, _fx._esc(label), f"color:{_DIM}")}{body}</div>')


def _big(text):
    return _span(T_BIG, _fx._esc(text),
                 f"line-height:1.2;font-weight:600;color:{C['title']}")


def _rail(mdl, exp, u, j):
    """Where the price sits among one expiry's put wall, max pain and call
    wall, on a line that also spans the strikes the map draws."""
    spot, rows = mdl["spot"], mdl["rows"]
    levels = [v for v in (exp["put_wall"], exp["max_pain"], exp["call_wall"])
              if v is not None]
    lo, hi = min([rows[-1], spot] + levels), max([rows[0], spot] + levels)

    def at(value):
        if hi <= lo:
            return 50.0
        return RAIL_PAD + (100.0 - 2 * RAIL_PAD) * (value - lo) / (hi - lo)

    line = RAIL_LINE
    parts = []
    if exp["put_wall"] is not None and exp["call_wall"] is not None:
        left, right = at(exp["put_wall"]), at(exp["call_wall"])
        parts.append(f'<div style="position:absolute;left:{left:.1f}%;'
                     f'width:{right - left:.1f}%;top:{line - 5}px;height:12px;'
                     f'background:{_fx._tone("ice", ".08")}"></div>')
    parts.append(f'<div style="position:absolute;left:0;right:0;top:{line}px;'
                 f'height:2px;background:{_fx._tone("ice", ".16")}"></div>')
    for kind, key in (("put", "put_wall"), ("call", "call_wall")):
        if exp[key] is not None:
            parts.append(_rail_marker(at(exp[key]), kind, _fmt.strike(exp[key]),
                                      _distance(exp[key], spot), 0, 22))
    if exp["max_pain"] is not None:
        parts.append(_rail_marker(at(exp["max_pain"]), "pain",
                                  _fmt.strike(exp["max_pain"]),
                                  _distance(exp["max_pain"], spot), line - 5, 14,
                                  below=True))
    parts.append(
        f'<div style="position:absolute;left:{at(spot):.1f}%;top:0;'
        f'transform:translateX(-50%);display:flex;flex-direction:column;'
        f'align-items:center;gap:5px">'
        + _span(T_SMALL, "Price", f"line-height:1;font-weight:600;color:{C['spot']}")
        + _span(T_HEAD, _fmt.price(spot),
                f"line-height:1;font-weight:700;background:{C['spot']};"
                f"color:{C['panel_to']};padding:2px 6px")
        + f'<span style="width:12px;height:12px;margin-top:1px;border-radius:50%;'
          f'background:{C["spot"]};box-shadow:0 0 0 4px {C["panel_mid"]}"></span>'
          f'</div>')
    for side, value in (("left", lo), ("right", hi)):
        parts.append(f'<div class="{T_SMALL}" style="position:absolute;{side}:0;'
                     f'bottom:0;line-height:1;color:{_FAINT}">'
                     f'{_fmt.strike(value)}</div>')

    ratio = _fmt.ratio(exp["ratio"]) if exp["ratio"] is not None else "—"
    every = (_fmt.ratio(mdl["puts"] / mdl["calls"]) if mdl["calls"] > 0 else "—")
    total = exp["calls"] + exp["puts"]
    share = round(exp["calls"] / total * 100) if total > 0 else 0
    tiles = "".join((
        _tile(f"Put / call ratio · {exp['label']}", _big(ratio)),
        '<div style="grid-column:span 2;min-width:0">'
        + _tile(f"{exp['label']} open interest · {share}% calls / "
                f"{100 - share if total > 0 else 0}% puts",
                _split_head(exp["calls"], exp["puts"])
                + _split_bar(exp["calls"], exp["puts"])) + "</div>",
        _tile(f"Whole chain put / call · {len(mdl['expiries'])} expiries",
              _big(every))))
    shown = "flex" if j == 0 else "none"
    return _card(
        '<div style="display:flex;flex-wrap:wrap;align-items:baseline;'
        'gap:8px 16px">'
        + _heading("Where the price sits",
                   f"{exp['label']} · walls and max pain over the whole chain")
        + '</div><div style="overflow-x:auto;overflow-y:hidden">'
        f'<div style="position:relative;min-width:720px;height:{RAIL_H}px">'
        f'{"".join(parts)}</div></div>'
        f'<div style="display:grid;gap:10px;'
        f'grid-template-columns:repeat(4,minmax(0,1fr))">{tiles}</div>',
        f"display:{shown};flex-direction:column;gap:14px", f"{u}-rail-{j}")


# ── the strike map ──────────────────────────────────────────────────────────

def _segment_style(on):
    fill = (f"color:{C['panel_to']};background:{C['ice']}" if on else
            f"color:{_DIM};background:transparent")
    return f"padding:7px 13px;cursor:pointer;user-select:none;border:0;{fill}"


def _mode_toggle(u):
    segments = "".join(
        f'<button type="button" id="{u}-m-{key}" class="{T_SMALL}" '
        f'style="{_segment_style(key == "total")}">{_fx._esc(label)}</button>'
        for key, label in MODES)
    return (f'<div style="display:flex;margin-left:auto;'
            f'box-shadow:inset 0 0 0 1px {_fx._tone("ice", ".12")}">{segments}</div>')


def _expiry_style(on):
    look = (f"box-shadow:inset 0 0 0 1px {_fx._tone('ice', '.6')};"
            f"background:{_fx._tone('ice', '.14')};color:{C['title']}" if on else
            f"box-shadow:inset 0 0 0 1px {_RULE};background:transparent;"
            f"color:{_DIM}")
    return (f"height:{HEAD_H - HEAD_GAP:.0f}px;padding:4px 2px;overflow:hidden;"
            f"text-align:center;cursor:pointer;border:0;line-height:1.25;{look}")


def _tag_style(kind):
    color = _LEVEL_COLOR[kind]
    return (f"line-height:{ROW_H - 2:.0f}px;font-weight:600;white-space:nowrap;"
            f"padding:0 5px;color:{color};box-shadow:inset 0 0 0 1px {color}")


def tags(mdl, exp):
    """``{row: (text, kind)}``: the walls and max pain named on their rows.
    A row that sums several strikes names the strike itself, and two levels on
    one row share its tag."""
    exact = mdl["step"] is not None
    found = {}
    for kind, row, level in (
            ("call", exp["wall_rows"][0], exp["call_wall"]),
            ("pain", exp["pain_row"], exp["max_pain"]),
            ("put", exp["wall_rows"][1], exp["put_wall"])):
        if row is None:
            continue
        name = _LEVEL_NAME[kind]
        text = f"{name} {_fmt.strike(level)}" if exact else name
        if row in found:
            found[row] = (f"{found[row][0]} · {text}", found[row][1])
        else:
            found[row] = (text, kind)
    return found


def _bar_shares(cells):
    """``(puts, calls)`` per row as a per cent of the longest bar (Total)."""
    top = max((max(c, p) for c, p in cells), default=0.0)
    if top <= 0:
        return [(0.0, 0.0) for _ in cells]
    return [(p / top * 100.0, c / top * 100.0) for c, p in cells]


def _major_step(rows):
    """Strikes on this multiple are printed brighter: five rows' worth."""
    gaps = [a - b for a, b in zip(rows, rows[1:]) if a - b > 0]
    return (min(gaps) if gaps else 1.0) * 5.0


def _column(head, cells, extra=""):
    """One column of the map: a header of the shared height, then its rows."""
    return (f'<div style="min-width:0;{extra}"><div class="{T_SMALL}" '
            f'style="height:{HEAD_H:.0f}px;display:flex;align-items:flex-end;'
            f'padding-bottom:{HEAD_GAP:.0f}px;box-sizing:border-box;'
            f'line-height:1;{head[1]}">{head[0]}</div>{"".join(cells)}</div>')


def _expiry_columns(mdl, u):
    """The expiry buttons over their cells, in ONE strip so they scroll
    together. Up to ``HEAT_SHOWN`` expiries fill the strip; past that each
    keeps its width and the strip scrolls sideways."""
    rows, exps = mdl["rows"], mdl["expiries"]
    if len(exps) > HEAT_SHOWN:
        template = f"repeat({len(exps)},{HEAT_COL_W:.0f}px)"
        width = f"width:{len(exps) * (HEAT_COL_W + HEAT_GAP) - HEAT_GAP:.0f}px;"
    else:
        template, width = f"repeat({len(exps)},minmax(0,1fr))", ""
    strip = (f"display:grid;gap:{HEAT_GAP:.0f}px;{width}"
             f"grid-template-columns:{template}")
    buttons = "".join(
        f'<button type="button" id="{u}-e-{j}" class="{T_SMALL}" '
        f'style="{_expiry_style(j == 0)}">'
        f'<span style="display:block;font-weight:600">{_fx._esc(exp["label"])}'
        f'</span><span style="display:block;opacity:.8">'
        f'{_fx._esc(exp["sub"])}</span></button>'
        for j, exp in enumerate(exps))
    columns = []
    for j, exp in enumerate(exps):
        cells = "".join(
            f'<div title="{_fx._esc(_tip(exp, rows[i], calls, puts))}" '
            f'style="height:{CELL_H:.0f}px;margin-bottom:{ROW_H - CELL_H:.0f}px;'
            f'background:{_cell_fill(calls + puts, mdl["cap_total"])}"></div>'
            for i, (calls, puts) in enumerate(exp["cells"]))
        # A gap UNDER each cell and half of one above the first: vertical
        # margins on both sides would collapse and the rows would drift.
        columns.append(f'<div id="{u}-h-{j}" style="cursor:pointer;'
                       f'opacity:{"1" if j == 0 else DIM_COLUMN};'
                       f'padding-top:{(ROW_H - CELL_H) / 2:.1f}px">{cells}</div>')
    return (f'<div id="{u}-x" style="min-width:0;overflow-x:auto;overflow-y:hidden">'
            f'<div style="{strip};height:{HEAD_H:.0f}px;align-items:start">'
            f'{buttons}</div><div style="{strip}">{"".join(columns)}</div></div>')


def _map(mdl, u):
    rows = mdl["rows"]
    first = mdl["expiries"][0]
    row = f"height:{ROW_H:.0f}px;display:flex;align-items:center"

    major = _major_step(rows)
    labels = []
    for strike in rows:
        bright = abs(strike / major - round(strike / major)) < 1e-9
        labels.append(
            f'<div class="{T_SMALL}" style="{row};justify-content:flex-end;'
            f'line-height:1;font-weight:{"600" if bright else "400"};'
            f'color:{C["title"] if bright else _FAINT}">{_fmt.strike(strike)}</div>')

    shares = _bar_shares(first["cells"])
    first_tags = tags(mdl, first)
    puts_col, calls_col, tag_col = [], [], []
    for i, (calls, puts) in enumerate(first["cells"]):
        tip = _fx._esc(_tip(first, rows[i], calls, puts))
        put_w, call_w = shares[i]
        puts_col.append(
            f'<div id="{u}-pr-{i}" title="{tip}" style="{row};'
            f'justify-content:flex-end;border-right:1px solid '
            f'{_fx._tone("ice", ".22")}"><div id="{u}-p-{i}" style="height:'
            f'{BAR_H:.0f}px;background:{C["put"]};width:{put_w:.1f}%"></div></div>')
        calls_col.append(
            f'<div id="{u}-cr-{i}" title="{tip}" style="{row}">'
            f'<div id="{u}-c-{i}" style="height:{BAR_H:.0f}px;'
            f'background:{C["call"]};width:{call_w:.1f}%"></div></div>')
        text, kind = first_tags.get(i, ("", None))
        style = _tag_style(kind) if kind else "display:none"
        tag_col.append(f'<div style="{row}"><span id="{u}-t-{i}" '
                       f'class="{T_SMALL}" style="{style}">{_fx._esc(text)}'
                       f'</span></div>')

    y = HEAD_H + _spot_y(rows, mdl["spot"])
    spot = (f'<div style="position:absolute;left:0;right:0;top:{y - 9:.1f}px;'
            f'height:18px;display:grid;column-gap:12px;align-items:center;'
            f'grid-template-columns:{STRIKE_W:.0f}px minmax(0,1fr);'
            f'pointer-events:none">'
            + _span(T_SMALL, _fmt.price(mdl["spot"]),
                    f"line-height:1;font-weight:700;color:{C['panel_to']};"
                    f"background:{C['spot']};text-align:center;padding:3px 0")
            + f'<div style="height:0;border-top:1.5px dashed {C["spot"]}"></div>'
              f'</div>')
    side = "font-weight:600;"
    body = (
        f'<div style="position:relative;display:grid;column-gap:12px;'
        f'grid-template-columns:{COLUMNS}">'
        + _column(("Strike", f"justify-content:flex-end;color:{_DIM}"), labels)
        + _expiry_columns(mdl, u)
        + _column((f'<span id="{u}-ph">Puts</span>',
                   f"justify-content:flex-end;{side}color:{C['put']}"), puts_col)
        + _column((f'<span id="{u}-ch">Calls</span>', f"{side}color:{C['call']}"),
                  calls_col)
        + _column(("", ""), tag_col) + spot + "</div>")

    note = ("One strike axis" if mdl["step"] is None else
            f"One strike axis · each row sums the strikes within "
            f"{_fmt.strike(mdl['step'])}")
    if len(mdl["expiries"]) > HEAT_SHOWN:
        note += f" · {len(mdl['expiries'])} expiries, scroll sideways for the rest"
    swatch = "width:10px;height:10px"
    item = "display:flex;align-items:center;gap:7px"
    legend = (
        f'<div class="{T_SMALL}" style="display:flex;flex-wrap:wrap;'
        f'align-items:center;gap:10px 22px;padding-top:12px;'
        f'border-top:1px solid {_RULE};color:{_DIM}">'
        f'<span style="{item}">Total open interest<span style="width:96px;'
        f'height:8px;background:linear-gradient(90deg,{_fx._tone("ice", ".08")},'
        f'{_fx._tone("ice", ".95")})"></span>'
        f'tops out at the {mdl["cap_pct"]}th percentile</span>'
        f'<span style="{item}"><span style="{swatch};background:{C["call"]}">'
        f'</span>Calls</span>'
        f'<span style="{item}"><span style="{swatch};background:{C["put"]}">'
        f'</span>Puts</span>'
        f'<span style="{item}"><span style="width:14px;border-top:1.5px dashed '
        f'{C["spot"]}"></span>Price</span>'
        f'<span>Click an expiry to load its bars</span></div>')
    return _card(
        f'<div style="display:flex;flex-wrap:wrap;align-items:baseline;'
        f'gap:10px 16px">{_heading("Strike map", note)}{_mode_toggle(u)}</div>'
        f'<div style="overflow-x:auto"><div style="min-width:{MAP_MIN_W:.0f}px">'
        f'{body}</div></div>{legend}',
        "flex:999 1 640px;display:flex;flex-direction:column;gap:16px")


# ── the side rail ───────────────────────────────────────────────────────────

def _read_card(mdl, exp):
    items = "".join(
        f'<div style="display:flex;gap:10px"><span style="flex:none;'
        f'color:{_FAINT}">{n}.</span><span>{_fx._esc(line)}</span></div>'
        for n, line in enumerate(read(mdl, exp), 1))
    return _card(
        _heading("The read")
        + f'<div style="display:flex;flex-direction:column;gap:10px;'
          f'line-height:1.5;color:{C["label"]}">{items}</div>'
        + _span(T_SMALL, "A count of open contracts says where positions sit, "
                         "not who holds which side.", f"color:{_FAINT}"),
        "display:flex;flex-direction:column;gap:12px")


def _levels_card(mdl, exp):
    spot = mdl["spot"]
    levels = [(v, kind) for v, kind in (
        (exp["call_wall"], "call"), (exp["max_pain"], "pain"), (spot, "spot"),
        (exp["put_wall"], "put")) if v is not None]
    levels.sort(key=lambda level: -level[0])
    cells = []
    for value, kind in levels:
        is_spot = kind == "spot"
        away = "—" if is_spot else _signed_pct(value, spot)
        shown = _fmt.price(value) if is_spot else _fmt.strike(value)
        cells.append(
            f'<span style="display:flex;align-items:center;gap:8px">'
            f'<span style="width:8px;height:8px;'
            f'{"border-radius:50%;" if is_spot else ""}'
            f'background:{_LEVEL_COLOR[kind]}"></span>'
            f'{"Price" if is_spot else _LEVEL_NAME[kind]}</span>'
            f'<span style="font-weight:{"700" if is_spot else "600"};'
            f'color:{C["title"]}">{shown}</span>'
            + _span(T_SMALL, away, f"color:{_DIM};text-align:right"))
    return _card(
        _heading("Levels", exp["label"])
        + f'<div style="display:grid;gap:10px 16px;align-items:baseline;'
          f'grid-template-columns:minmax(0,1fr) auto auto;'
          f'color:{C["label"]}">{"".join(cells)}</div>',
        "display:flex;flex-direction:column;gap:12px")


def _chain_card(mdl):
    exps = mdl["expiries"]
    span = (exps[0]["label"] if len(exps) == 1 else
            f"{exps[0]['label']} – {exps[-1]['label']}")
    return _card(
        _heading("Whole chain") + _split_head(mdl["calls"], mdl["puts"])
        + _split_bar(mdl["calls"], mdl["puts"])
        + _span(T_SMALL, f"{len(exps)} expiries · {_fx._esc(span)}",
                f"color:{_DIM}"),
        "display:flex;flex-direction:column;gap:11px")


def _side(mdl, u):
    blocks = "".join(
        f'<div id="{u}-side-{j}" style="display:{"flex" if j == 0 else "none"};'
        f'flex-direction:column;gap:14px">{_read_card(mdl, exp)}'
        f'{_levels_card(mdl, exp)}</div>'
        for j, exp in enumerate(mdl["expiries"]))
    return (f'<div style="flex:1 1 300px;min-width:0;display:flex;'
            f'flex-direction:column;gap:14px">{blocks}{_chain_card(mdl)}</div>')


def _frame(body):
    """The panel's shell. It keeps the Flow panels' ground and frame and none
    of their type."""
    return (f'<div class="fx-panel" style="width:100%;background:{_fx._PANEL_BG};'
            f'box-shadow:{_fx._PANEL_SHADOW};color:{C["label"]}">'
            f'<div style="padding:20px 24px;display:flex;flex-direction:column;'
            f'gap:16px">{body}</div></div>')


def _panel_title(note=""):
    note = _span(T_SMALL, _fx._esc(note), f"color:{_FAINT}") if note else ""
    return (f'<div style="display:flex;align-items:baseline;gap:12px">'
            + _span(T_TITLE, TITLE, f"font-weight:600;color:{C['title']}")
            + note + "</div>")


def panel(term_grid, spot, symbol, uid, cfg=None):
    """``(html, model)`` for the tab. ``model`` is ``None`` in the empty state,
    which keeps the panel's frame and says why it is empty."""
    mdl, reason = model(term_grid, spot, cfg)
    if mdl is None:
        return _frame(_panel_title() + f'<div style="color:{_DIM};'
                      f'padding:20px 0 24px">{_fx._esc(reason)}</div>'), None
    u = _fx._id(uid)
    rails = "".join(_rail(mdl, exp, u, j)
                    for j, exp in enumerate(mdl["expiries"]))
    return _frame(
        _panel_title(f"{str(symbol or '').upper()} · by strike and expiry")
        + rails + '<div style="display:flex;flex-wrap:wrap;gap:16px;'
        f'align-items:flex-start">{_map(mdl, u)}{_side(mdl, u)}</div>'), mdl


#############################################
# THE TWO CONTROLS
#############################################

_BIND_JS = r"""
(function(){
  var D=__DATA__, id=D.id;
  var S=window.__oiMap=window.__oiMap||{};
  function el(s){ return document.getElementById(id+'-'+s); }
  function show(node, on){ if(node) node.style.display = on ? 'flex' : 'none'; }
  function count(v){ return Math.round(v).toLocaleString('en-US'); }
  function share(v, top){ return (top>0 ? v/top*100 : 0).toFixed(1)+'%'; }
  function apply(){
    var j=D.dates.indexOf(S.date); if(j<0) j=0;
    var net=(S.mode==='net'), i, r;
    for(i=0;i<D.dates.length;i++){
      show(el('rail-'+i), i===j);
      show(el('side-'+i), i===j);
      var b=el('e-'+i); if(b) b.setAttribute('style', i===j ? D.expOn : D.expOff);
      var h=el('h-'+i); if(h) h.style.opacity = i===j ? '1' : D.dim;
    }
    var cells=D.cells[j], tags=D.tags[j], top=0;
    for(r=0;r<cells.length;r++){
      top=Math.max(top, net ? Math.abs(cells[r][0]-cells[r][1])
                            : Math.max(cells[r][0], cells[r][1]));
    }
    for(r=0;r<cells.length;r++){
      var c=cells[r][0], p=cells[r][1];
      var pb=el('p-'+r), cb=el('c-'+r), t=el('t-'+r), tag=tags[r];
      if(pb) pb.style.width=share(net ? Math.max(0,p-c) : p, top);
      if(cb) cb.style.width=share(net ? Math.max(0,c-p) : c, top);
      var tip=D.labels[j]+' · '+D.strikes[r]+' · calls '+count(c)+' · puts '+count(p);
      ['pr-'+r,'cr-'+r].forEach(function(s){
        var node=el(s); if(node) node.setAttribute('title', tip);
      });
      if(t){
        t.textContent = tag ? tag[0] : '';
        t.setAttribute('style', tag ? D.tagStyle[tag[1]] : 'display:none');
      }
    }
    var ph=el('ph'), ch=el('ch');
    if(ph) ph.textContent = net ? 'Net puts' : 'Puts';
    if(ch) ch.textContent = net ? 'Net calls' : 'Calls';
    ['total','net'].forEach(function(m){
      var seg=el('m-'+m);
      if(seg) seg.setAttribute('style', (m==='net')===net ? D.on : D.off);
    });
    return j;
  }
  // A repaint rebuilds the strip at its left edge: put the chosen expiry and
  // the reader's own scroll back.
  function restore(j){
    var x=el('x'), b=el('e-'+j);
    if(!x) return;
    if(typeof S.left==='number') x.scrollLeft=S.left;
    if(b && (b.offsetLeft < x.scrollLeft ||
             b.offsetLeft + b.offsetWidth > x.scrollLeft + x.clientWidth)){
      x.scrollLeft = b.offsetLeft - (x.clientWidth - b.offsetWidth)/2;
    }
    x.addEventListener('scroll', function(){ S.left=x.scrollLeft; });
  }
  D.dates.forEach(function(date, i){
    ['e-'+i,'h-'+i].forEach(function(s){
      var node=el(s);
      if(node) node.addEventListener('click', function(){ S.date=date; apply(); });
    });
  });
  ['total','net'].forEach(function(m){
    var seg=el('m-'+m);
    if(seg) seg.addEventListener('click', function(){ S.mode=m; apply(); });
  });
  restore(apply());
})();
"""


def bind_js(uid, mdl):
    """The script that wires the expiry buttons and the Total / Net toggle, or
    "" in the empty state. The choice lives on ``window`` keyed by expiry DATE,
    so it survives the next repaint and falls back to the nearest expiry once
    the chosen one has gone; so does how far the expiry strip is scrolled. The
    bars are drawn in the browser from the same counts the server drew the
    first expiry from."""
    if not mdl:
        return ""
    data = {"id": _fx._id(uid),
            "dates": [e["date"] for e in mdl["expiries"]],
            "labels": [e["label"] for e in mdl["expiries"]],
            "strikes": [_fmt.strike(r) for r in mdl["rows"]],
            "cells": [[list(c) for c in e["cells"]] for e in mdl["expiries"]],
            "tags": [{str(row): list(tag) for row, tag in tags(mdl, e).items()}
                     for e in mdl["expiries"]],
            "tagStyle": {kind: _tag_style(kind) for kind in ("call", "pain", "put")},
            "expOn": _expiry_style(True), "expOff": _expiry_style(False),
            "dim": DIM_COLUMN,
            "on": _segment_style(True), "off": _segment_style(False)}
    return _BIND_JS.replace("__DATA__", json.dumps(data))
