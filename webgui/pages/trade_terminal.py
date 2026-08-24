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

    # `company_name` comes from Schwab's symbol-search projection and is the
    # real name; `description` is the TICKER (the fundamental projection carries
    # no name at all), so it is dropped when it merely repeats the symbol rather
    # than rendered as "MU · MU · Technology".
    desc = (a.get("company_name") or "").strip()
    if not desc:
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
        # `chg` is already a percentage figure, not a fraction, so this is
        # `signed` plus a unit rather than `signed_pct`.
        "change": (signed(chg, 2) + "%") if chg is not None else "—",
        "change_class": T.sign_text(chg) if chg is not None else T.OFF,
        "bias": bias or "—",
        "bias_class": _BIAS_CLASS.get(bias, T.DIM),
        "model_stamp": (f"MODEL {version}" if version
                        else "MODEL — no artifact loaded"),
    }


def should_commit(draft, committed, explicit):
    """Should committing ``draft`` enqueue an analyze?

    ⚠ Blur fires constantly — clicking any button, leaving the page, switching
    screens — and an analyze is a dozen proxy calls. So blur and Tab commit only
    a CHANGE; Enter is the deliberate refresh gesture and always requests. An
    empty draft is never a request: it means "I cleared the box", not "analyze
    nothing"."""
    d = (draft or "").strip().upper()
    if not d:
        return False
    return bool(explicit) or d != (committed or "").strip().upper()


# The rail's caption used to read "of today's cross-section", which is the
# natural reading of a percentile and is NOT what this number is. The score's
# INPUTS are today's cross-section — each factor z-scored against the same 78
# names the model was fitted on — but the BUCKETS are fixed thresholds cut from
# the 5-year training panel, so a band is a claim about the model's own score
# history, not a rank among today's names. Measured live on 2026-08-22 the 78
# names landed 20/20/12/14/12 across the five bands; a real percentile of a
# 78-name cross-section is ~15.6 per band by construction and cannot do that.
_RAIL_NOTE = "its band, against this model's own history"
_RAIL_TIP = (
    "The model's scores are sorted into five bands, cut from years of its own "
    "output. 90th means the top band, 10th the bottom.\n\n"
    "This is NOT the top 10% of today's names — the bands are fixed score "
    "thresholds, so on any given day they fill unevenly. A defensive market "
    "puts most names in the low bands and leaves the top one nearly empty."
)


def percentile_rail(swing):
    """The band reading, its marker position, and the calibrated stats."""
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
        "note": (_RAIL_NOTE if pct is not None
                 else "unranked — too few peers to score it against"),
        # Nothing to explain when there is no reading; an unranked rail showing
        # a tooltip about bands would be explaining a number it isn't showing.
        "tip": _RAIL_TIP if pct is not None else "",
        "stats": " · ".join(stats),
    }


def verdict_word(verdict):
    """A BUY/HOLD/SELL verdict as a headline word, in SENTENCE case.

    The two headline cards on the Overview sit side by side, and one of them
    carries PHRASES rather than single words — "Pair short", "Stand aside",
    "No recommendation". Those set in caps at 30px shout and wrap, so sentence
    case is the convention both cards follow. Absent renders as a dash, never
    as an empty headline."""
    v = (verdict or "").strip()
    return v.capitalize() if v else "—"


def verdict_class(verdict):
    """Terminal-palette text class for a BUY/HOLD/SELL verdict.

    NOT ``pages.trade.verdict_text_class``, whose own comment records that its
    hexes are deliberately DARKER than the theme's — that palette belongs to
    the old light-background page. On the Signal Desk's near-black ground it
    rendered the Long Term "Buy" in a different green from the Short Term one
    beside it (measured: rgb(46,125,50) against rgb(52,211,153))."""
    v = (verdict or "").strip().upper()
    if v == "BUY":
        return T.POS
    if v == "SELL":
        return T.NEG
    if v == "HOLD":
        return T.WARN
    return T.OFF


# The Long Term verdict flips at +/-40, and only +/-85 is reachable because
# `earnings_traj`'s 15 points can never score (Schwab publishes neither
# earnings surprises nor guidance). So "how decisive is this" is measured
# against the boundary it actually had to clear, on the range it actually had.
_INV_BOUNDARY = 40.0
_INV_REACHABLE = 85.0


def investor_confidence(verdict):
    """``(word, note)`` for the Long Term card's confidence chip.

    ⚠ This does NOT mean what the Short Term card's confidence means. That one
    is a backtested hit rate — how often the band beat the index. This card is
    a weighted scorecard that was never tested against forward returns, so
    there is no hit rate to quote; the honest reading is how decisively the
    score cleared its own verdict boundary. The note says so, because a chip
    reading "Moderate" beside a tested one would otherwise borrow credibility
    it has not got.

    Shares the Short Term card's four words, so one chip palette covers both."""
    iv = verdict or {}
    score = fmt.num(iv.get("score"))
    if score is None or not iv.get("breakdown"):
        return "Unknown", ("No fundamentals arrived for this symbol, so no "
                           "reading was taken.")
    mag = abs(score)
    word = ("Moderate" if mag >= 60 else
            "Low" if mag >= _INV_BOUNDARY else "Very low")
    return word, (
        f"Scored {score:+.0f} against a verdict boundary of "
        f"±{_INV_BOUNDARY:.0f}, on the ±{_INV_REACHABLE:.0f} this scorecard "
        f"can actually reach. This is how decisive the score is — it is NOT a "
        f"backtested hit rate like the Short Term card's, because this "
        f"scorecard was never tested against forward returns.")


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


# Schwab's `/instruments?projection=fundamental` carries 56 fields and neither
# `epsSurprises` nor `guidanceDirection` (verified live 2026-08-22), so both of
# `earnings_traj`'s inputs score 0 and the component contributes exactly 0 for
# every symbol, always. Flagged off the SCORE rather than the factor name, so a
# fundamentals source that does supply them renders normally.
_UNPUBLISHED_FACTOR = "earnings_traj"


def investor_bars(verdict):
    """Investor factor scores on the shared centred bar."""
    out = []
    for b in ((verdict or {}).get("breakdown") or []):
        v = fmt.num(b.get("contribution"))
        # Strictly the 0, not a None: a None contribution means the engine
        # returned nothing for this row, which is "n/a" — a different claim
        # from "the data source does not carry it".
        unpublished = b.get("factor") == _UNPUBLISHED_FACTOR and v == 0
        left, width = T.centred(v, 60.0) if v is not None else (50.0, 0.0)
        if unpublished:
            left, width = 50.0, 0.0
        out.append({
            "key": b.get("factor", ""),
            "label": humanize_factor(b.get("factor", "")),
            "value": ("" if unpublished else
                      _signed(v, 0, dash="n/a") if v is not None else "n/a"),
            # Words in the bar track, where a zero-width bar leaves the space
            # free. The 46px value column can only hold an abbreviation, and
            # "the data source does not carry this" is not abbreviable into
            # something a reader would decode correctly.
            "track_text": "not published by Schwab" if unpublished else "",
            "value_class": (T.OFF if unpublished or v is None else T.sign_text(v)),
            "bar_class": (T.BAR_DIM if unpublished or v is None else T.sign_bar(v)),
            "left_pct": left,
            "width_pct": width,
            "absent": v is None,
            "unpublished": unpublished,
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
            # The raw key travels beside the humanized name: `trade_help` is
            # keyed by engine key, so a renamed label must not drop a tooltip.
            "key": c.get("factor", ""),
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


# ── the plan's hand-off to the options tools ────────────────────────────────
# `structure.choose` names a STRUCTURE and a tenor and deliberately not strikes,
# so the Calculator is where a plan becomes a contract. These are the only four
# option structures it produces; a relative PAIR is a stock trade with no
# options template at all.
_CALC_STRATEGY = {
    "call debit spread": "VERT_CALL_DEBIT",
    "put debit spread": "VERT_PUT_DEBIT",
    "call credit spread": "CCS",
    "put credit spread": "PCS",
}


def calculator_strategy(structure):
    """The Calculator template for a plan structure, or None.

    None for anything unmapped — including the relative pair — so the caller
    hands over a symbol without pre-selecting a structure that does not fit."""
    return _CALC_STRATEGY.get((structure or "").strip().lower())


def calculator_handoff(analysis):
    """The signal `handoff.set_pending_calculator` expects, or None.

    Carries symbol, strategy and the underlying price; NO strikes and no expiry,
    because the plan has none. The Calculator loads the chain and seeds the
    template's default legs, which is exactly the step the plan leaves open."""
    a = analysis or {}
    plan = a.get("trade_plan") or {}
    sym = (a.get("symbol") or "").strip().upper()
    if not sym or not plan.get("structure"):
        return None
    return {
        "symbol": sym,
        "type": calculator_strategy(plan.get("structure")),
        "underlying_price": fmt.num(a.get("price")),
    }


# ── the Short Term card's recommendation ──────────────────────────────────────
# This card led with a RANK for most of its life, and `swing_tilt`'s docstring
# still records why: "a coin-flip-plus-2% edge shown as a bold green BUY invites
# over-reading". Leading with an action is a deliberate product decision, so the
# honesty has to move INTO the recommendation rather than leave with the rank —
# hence `confidence`, drawn from the band's own hit rate, and `caveat`, which
# carries the volatility-exposure share that used to sit only on Evidence.
#
# The action is (side x clearance), never the verdict alone. A bottom-band name
# is predicted to LAG the index, not to fall, so "SELL" plus a tape that has not
# cleared a directional short must NOT render as "Sell short".

_REC_UNKNOWN = {
    "action": "No recommendation",
    "action_class": T.OFF,
    "detail": ("No validated model reading for this symbol, so there is no "
               "action to recommend. The card falls back to the legacy "
               "heuristic below."),
    "confidence": "Unknown",
    "confidence_note": "",
    "rank_line": "",
    "caveat": "",
}


def _confidence(hit):
    """``(word, note)`` from the band's beat-the-index rate.

    The edge is the DISTANCE from a coin flip, not the rate itself: a bottom
    band that beats the index only 44% of the time is a strong reading, and
    scoring it as weak would invert the short side."""
    h = fmt.num(hit)
    if h is None:
        return "Unknown", "No calibrated hit rate for this band."
    edge = abs(h - 0.5)
    word = ("Moderate" if edge >= 0.05 else
            "Low" if edge >= 0.02 else "Very low")
    return word, (f"{h:.0%} of past readings in this band beat the S&P over 20 "
                  f"trading days — a real but small edge, so size it as one.")


def _rec_action(side, state, action):
    """``(headline, class, verb)`` for (side x clearance x plan action)."""
    if side not in ("long", "short"):
        return ("No trade", T.DIM, "")
    if state == "blocked":
        return ("Stand aside", T.OFF, "")
    if state == "relative_only":
        return (("Buy paired" if side == "long" else "Pair short"),
                T.WARN, "pair")
    if action == "none":
        return ("Stand aside", T.OFF, "")
    return (("Buy" if side == "long" else "Sell short"),
            (T.POS if side == "long" else T.NEG), action or "debit")


def _rec_detail(side, state, action, structure):
    """The plain-English instruction under the headline."""
    what = structure or ("a call spread" if side == "long" else "a put spread")
    if side not in ("long", "short"):
        return ("The composite sits in the middle band, where the model has no "
                "edge to express. There is no directional read to hold.")
    if state == "blocked":
        return ("The tape has blocked this side outright. The ranking still "
                "stands, but there is no version of the trade to take today.")
    if state == "relative_only":
        other = "short" if side == "long" else "long"
        return (f"Express it as a pair — {side} this name against a {other} in "
                f"the S&P — so the position is paid for the relative move the "
                f"model predicted rather than for the market's direction.")
    if action == "none":
        return ("The tape clears this side but no structure fits today's "
                "volatility. Wait, or take it in the underlying.")
    if action == "credit":
        return (f"Sell premium: {what}, sized so the level it depends on is "
                f"the one the stock would have to break through.")
    return (f"Take it {'long' if side == 'long' else 'short'}: {what}, or the "
            f"underlying if you would rather not carry the expiry.")


def recommendation(analysis):
    """What to DO, with what it is worth and what it is really betting on.

    Replaces the ranked-tilt headline on the Short Term card. The rank survives
    as ``rank_line`` — informational, beneath — because it is still the honest
    description of what the model computed."""
    a = analysis or {}
    sm = a.get("swing_model") or {}
    if not sm.get("verdict"):
        return dict(_REC_UNKNOWN)

    plan = a.get("trade_plan") or {}
    clearance = a.get("direction_clearance") or {}
    side = (plan.get("side") or "").lower() or None
    state = ((clearance.get(side) or {}).get("state") or "cleared"
             if side else "")
    action = (plan.get("action") or "").lower()

    head, cls, _verb = _rec_action(side, state, action)
    detail = _rec_detail(side, state, action, plan.get("structure"))
    word, note = _confidence(sm.get("hit_rate"))

    rail = percentile_rail(sm)
    rank_line = ""
    if rail["percentile"] != "—":
        rank_line = f"{rail['percentile']} band"
        if rail["stats"]:
            rank_line += f" · {rail['stats']}"

    # No exposure to disclose when nothing is being recommended.
    share = fmt.num(sm.get("risk_share"))
    caveat = ""
    if share is not None and side in ("long", "short") and head != "Stand aside":
        caveat = (f"{share:.0%} of this model's weight sits on volatility "
                  f"factors, so the ranking is partly a bet on the market "
                  f"rather than on this company.")

    return {
        "action": head,
        "action_class": cls,
        "detail": detail,
        "confidence": word,
        "confidence_note": note,
        "rank_line": rank_line,
        "caveat": caveat,
    }
