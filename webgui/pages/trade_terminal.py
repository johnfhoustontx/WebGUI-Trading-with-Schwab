"""Signal Desk — the pure builders behind the Trade Analyzer's four screens.

Overview, Evidence, Rank board and Trade plan share one command bar and one bar
language; both live here so the four screens cannot drift apart. No widgets, no
I/O — every function takes a payload and returns display values.

**Mono is reserved for numerics** (see `terminal_theme`), so anything this
module formats as a monospaced value is a number and nothing else.

**Absent is not zero.** Every builder here renders a missing reading as "—" or
"n/a", never as 0. That is this app's documented failure mode — a confident
number over no data — and the terminal look makes it worse, because a dense
mono grid reads as measured whether or not it was.
"""
from pages import fmt
from pages import terminal_theme as T
from pages.trade import humanize_factor

_BIAS_CLASS = {"BULLISH": T.POS, "BEARISH": T.NEG, "NEUTRAL": T.DIM}
_CLEARANCE = {
    "cleared": (T.CHIP_POS, "✓", "CLEARED"),
    "relative_only": (T.CHIP_WARN, "≈", "RELATIVE ONLY"),
    "blocked": (T.CHIP_NEG, "✕", "BLOCKED"),
}


def signed(v, nd=2, dash="—"):
    """A signed fixed-width number, or ``dash`` when absent.

    Two deliberate details. The sign is a true MINUS, not a hyphen: at mono
    sizes a hyphen reads as a dash and a negative number stops looking negative.
    And a value that ROUNDS to zero carries no sign at all — "−0.00" reads as a
    small negative at a glance, which is the wrong impression for a factor
    contributing nothing."""
    n = fmt.num(v)
    if n is None:
        return dash
    body = f"{abs(n):.{nd}f}"
    if float(body) == 0.0:
        return body
    return ("+" if n >= 0 else "−") + body


def signed_pct(v, nd=1, dash="—"):
    """The same sign convention, as a percentage."""
    n = fmt.num(v)
    if n is None:
        return dash
    body = f"{abs(n) * 100:.{nd}f}%"
    if float(body[:-1]) == 0.0:
        return body
    return ("+" if n >= 0 else "−") + body


_signed = signed          # the module used the private name before it was shared


def command_bar(analysis):
    """The persistent bar: model stamp, symbol, company, price, change, bias."""
    a = analysis or {}
    sym = (a.get("symbol") or "").strip().upper()
    price = fmt.num(a.get("price"))
    # The quote's OWN change, stored top-level by `analyze`. The momentum block
    # carries indicators (RSI/ADX/MACD/VWAP) and never had a change field.
    chg = fmt.num(a.get("change_pct"))
    sm = a.get("swing_model") or {}

    # Schwab's quote has no company name — `description` is the SYMBOL — so a
    # description that merely repeats the ticker is dropped rather than
    # rendered as "MU · MU · Technology".
    desc = (a.get("description") or "").strip()
    if desc.upper() == sym:
        desc = ""
    sect = a.get("sector") or {}
    bits = [b for b in (desc, sect.get("name"), sect.get("etf")) if b]
    name = " · ".join(bits) if bits else "not in today's cross-section"

    bias = (a.get("bias") or "").strip().upper()
    version = sm.get("model_version")
    return {
        "symbol": sym,
        "name": name,
        "price": f"{price:.2f}" if price is not None else "—",
        "change": f"{chg:+.2f}%" if chg is not None else "—",
        "change_class": T.sign_text(chg) if chg is not None else T.OFF,
        "bias": bias or "—",
        "bias_class": _BIAS_CLASS.get(bias, T.DIM),
        "model_stamp": (f"MODEL {version}" if version
                        else "MODEL — no artifact loaded"),
    }


def percentile_rail(swing):
    """The cross-section rank, its marker position, and the calibrated stats."""
    sm = swing or {}
    pct = fmt.num(sm.get("percentile"))
    exp = fmt.num(sm.get("expected_fwd"))
    hit = fmt.num(sm.get("hit_rate"))

    stats = []
    if exp is not None:
        stats.append(f"{exp:+.1%} vs SPY / {int(fmt.num(sm.get('horizon_days')) or 20)}d")
    if hit is not None:
        stats.append(f"{hit:.0%} beat-SPY")
    return {
        "percentile": f"{int(pct)}th" if pct is not None else "—",
        "pos_pct": pct if pct is not None else 50.0,
        "note": ("of today's cross-section" if pct is not None
                 else "unranked — no cross-section to place it in"),
        "stats": " · ".join(stats),
    }


def gate_chips(clearance):
    """One chip per side, coloured by what the tape permits."""
    c = clearance or {}
    out = []
    for side in ("long", "short"):
        blk = c.get(side)
        if not blk:
            continue
        chip, icon, word = _CLEARANCE.get(blk.get("state"), (T.CHIP_OFF, "·", "UNKNOWN"))
        out.append({
            "side": side,
            "icon": icon,
            "label": f"{side.upper()} {word}",
            "chip_class": chip,
            "reasons": "; ".join(blk.get("reasons") or []),
        })
    return out


def investor_bars(verdict):
    """Investor factor scores on the shared centred bar."""
    out = []
    for b in ((verdict or {}).get("breakdown") or []):
        v = fmt.num(b.get("contribution"))
        left, width = T.centred(v, 60.0) if v is not None else (50.0, 0.0)
        out.append({
            "label": humanize_factor(b.get("factor", "")),
            "value": _signed(v, 0, dash="n/a") if v is not None else "n/a",
            "value_class": T.sign_text(v) if v is not None else T.OFF,
            "bar_class": T.sign_bar(v) if v is not None else T.BAR_DIM,
            "left_pct": left,
            "width_pct": width,
            "absent": v is None,
        })
    return out


def dealer_ladder(dealer, spot):
    """put wall · flip · spot · call wall, positioned along one rail.

    ⚠ Returns NOTHING when the context is uncollected or stale. Those levels are
    withheld off-hours precisely because they are untrustworthy then, and a
    drawn ladder is a much stronger claim than an absent one."""
    d = dealer or {}
    px = fmt.num(spot)
    if not d.get("collected") or d.get("stale") or px is None:
        return []
    marks = [("put_wall", fmt.num(d.get("put_wall")), "put wall", T.NEG, False),
             ("flip", fmt.num(d.get("flip")), "flip", T.DIM, False),
             ("spot", px, "spot", "text-white", True),
             ("call_wall", fmt.num(d.get("call_wall")), "call wall", T.POS, False)]
    marks = [m for m in marks if m[1] is not None]
    if len(marks) < 2:
        return []
    lows = [m[1] for m in marks]
    lo, hi = min(lows), max(lows)
    pad = (hi - lo) * 0.12 or 1.0
    lo, hi = lo - pad, hi + pad
    span = (hi - lo) or 1.0
    out = []
    for kind, val, label, cls, emph in sorted(marks, key=lambda m: m[1]):
        out.append({
            "kind": kind,
            "label": f"{label} {val:g}",
            "pos_pct": max(0.0, min(100.0, (val - lo) / span * 100.0)),
            "text_class": cls,
            "emphasis": emph,
        })
    return out


def evidence_rows(swing):
    """One row per weighted factor, each with a centred contribution bar."""
    out = []
    for c in ((swing or {}).get("contributions") or []):
        contrib = fmt.num(c.get("contribution"))
        left, width = T.centred(contrib, 0.12)
        out.append({
            "name": humanize_factor(c.get("factor", "")),
            "z": _signed(c.get("z"), 2),
            "weight": _signed(c.get("weight"), 3),
            "weight_class": T.sign_text(fmt.num(c.get("weight"))),
            "contribution": _signed(contrib, 3),
            "bar_class": T.sign_bar(contrib),
            "left_pct": left,
            "width_pct": width,
            "ic": _signed(c.get("ic"), 3),
            "ic_class": T.sign_text(fmt.num(c.get("ic"))),
        })
    return out


def evidence_composite(swing):
    """The weighted sum the rows add up to, or None when there are no rows."""
    rows = (swing or {}).get("contributions") or []
    vals = [fmt.num(c.get("contribution")) for c in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None
