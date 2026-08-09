"""Shared, persistent Trade detail panel.

One panel, reused by every signal table (scanner, captured, paper, swing). Each
table synthesizes a signal-like dict and calls ``handle.update(signal)``. The
**header** (signal title + composite-score speedometer + 2x2 tiles) is built ONCE
in ``render()`` and updated in place; the five cards (Trade Info, Greeks, Composite
Score factor bars, IV Analysis, Expected Move) rebuild per selection. The
speedometer is the shared Highcharts angular gauge (``gauge.py`` — painted
rainbow face + needle); the factor/IV bars + range markers are SVG (``svg.py``).
Robust to missing keys (fields vary by trade type / source).

The gauge is persistent (not recreated per selection) so the Highcharts ESM is
registered at initial page render — a gauge added only on selection, on a page
with no other chart at load, fails with "Failed to resolve module specifier".
"""
from nicegui import ui

from ..gauge import gauge_figure
from . import svg
from .theme import (TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL, STATE_TEXT_CLASSES,
                    TILE_3D, CARD)

# Semantic state-color class tokens (Tailwind text-[...] arbitrary values). Names
# kept (many refs) but the VALUES are now class strings applied via .classes().
GREEN, AMBER, RED, NEUTRAL = TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL


def _set_color(lbl, cls):
    """Reactively swap a label's state-color class. .classes() ACCUMULATES, so we
    must remove the whole finite state set before adding, or repeated repaints
    stack conflicting text-[...] classes (equal specificity → unpredictable)."""
    lbl.classes(remove=STATE_TEXT_CLASSES, add=cls)

FACTOR_LABELS = [
    ("rr", "R:R"), ("pop", "PoP"), ("theta", "Theta"), ("iv", "IV Rank"),
    ("iv_hv", "IV/HV"), ("vega", "Vega Risk"), ("em", "EM Buffer"),
    ("liq", "Liquidity"), ("trend", "Trend"), ("gex", "GEX"), ("dex", "DEX"),
]

_PLACEHOLDER = "Select a signal to view details…"

# 2x2 header tiles: (key, label, value-fn, color-fn).
_TILES = [
    ("credit", "Credit", lambda s: _money(s.get("credit")), lambda s: GREEN),
    ("pop", "PoP", lambda s: _pct(s.get("pop_pct")), lambda s: pop_color(s.get("pop_pct"))),
    ("breakeven", "Breakeven", lambda s: _money(s.get("breakeven")), lambda s: NEUTRAL),
    ("dte", "DTE", lambda s: (f"{s.get('dte')} days" if s.get("dte") is not None else "—"),
     lambda s: NEUTRAL),
]


def pop_color(pop):
    try:
        p = float(pop)
    except (TypeError, ValueError):
        return NEUTRAL
    if p >= 70:
        return GREEN
    if p >= 50:
        return AMBER
    return RED


def factor_rows(factor_scores, trade_type, unavailable=None):
    """[(label, value, known), ...] for the Score factors card.

    ``known`` is False when the factor was never measured. This matters because
    scoring.py collapses "unavailable" into a real-looking number in BOTH
    directions -- 0.0 for rr/pop/theta and 50.0 for the other eight -- and the
    panel previously drew both as confident bars. A calm mid-grey 50 could mean
    "never measured", which for triage is false reassurance.

    Absence from the dict is proof. ``unavailable`` is the additive
    ``factors_unavailable`` list from Tier 2 when present -- exact rather than
    inferred. A value that IS present and is not listed is treated as real,
    including a genuine 0 (a real wide-spread liquidity reading).
    """
    fs = factor_scores or {}
    missing = set(unavailable or ())
    if trade_type == "IC":
        keys = [("pcs_leg", "Put leg"), ("ccs_leg", "Call leg"),
                ("delta_bonus", "Delta bonus")]
    else:
        keys = FACTOR_LABELS
    rows = []
    for key, label in keys:
        raw = fs.get(key)
        known = raw is not None and key not in missing
        rows.append((label, raw if known else None, known))
    return rows


def factor_value_text(value, known):
    """Numeric text for a factor bar, or an em-dash when it was never measured."""
    if not known or not isinstance(value, (int, float)):
        return "—"
    return f"{value:g}"


#############################################
# DEALBREAKER FLAGS
#############################################
# Triage is mostly fast rejection, so the four dealbreakers get their own
# treatment above the fold rather than being four of eleven identical bars.
#
# Three bars fall out of scoring.py's own definitions and invent nothing:
#   em    < 50  -- norm_em_buffer returns 0-50 ONLY inside 1 sigma (scoring.py:208)
#   trend < 50  -- 25 = partially against, 0 = against    (scoring.py:248)
#   liq   < 50  -- 50 => spread > 3% of mark, zero at 5%  (scoring.py:231)
#
# The two below are judgment calls. Tune in use; promote to Settings only if
# they turn out to change often.
MIN_RR_PCT = 20        # credit too thin: norm_rr reaches 100 at 50%
WALL_FLAG_BAR = 30     # too close to a gamma wall: 100 = >=1% of spot away

_SEMANTIC_BAR = 50     # the scorer's own neutral/boundary point

# WHY THERE IS NO "== 50.0 MEANS UNMEASURED" INFERENCE.
#
# An earlier cut treated an exact 50.0 on em/trend/gex/dex as proof the factor
# was never measured. That inference is unsound, and measurably so: driving the
# REAL scorer with an ordinary 7-DTE PCS returns em/gex/dex = 50.0 (no walls
# supplied, no DTE-sized expected move) AND trend = 50.0 -- so a perfectly
# normal signal raised four "not measured" chips. Flags that fire on every trade
# are wallpaper, and the layout depends on their being ABSENT when a trade is
# clean.
#
# trend is the case that proves the inference wrong rather than merely noisy:
# norm_trend returns exactly 50.0 BOTH for a missing trend AND for a real
# NEUTRAL reading (scoring.py:250-251). Those are opposite meanings collapsed
# into one number upstream, so the page cannot tell them apart -- and for a
# NEUTRAL trend the correct answer is silence anyway ("not against the
# structure" is not a dealbreaker).
#
# So em/trend/gex/dex now emit ONLY "tripped". This knowingly accepts a silent
# gap -- a genuinely-absent em/gex/dex says nothing. The real fix is Tier 2
# emitting ``factors_unavailable``; that EXPLICIT path is still honored below,
# only the inferred one is gone.

# A real iron condor is scored leg-wise: scanner_engine.py:1654 sets
# factor_scores to {pcs_leg, ccs_leg, delta_bonus} ONLY, so em/trend/gex/dex/liq
# simply do not exist on it and every check below would silently find nothing.
# Left alone an IC renders a clean, flagless panel -- a false negative in a
# safety signal, worse than a spurious warning. It gets an explicit note instead.
_IC_NOTE = {"key": "ic", "label": "Dealbreaker checks unavailable for iron condors",
            "state": "unavailable"}


# THE THIRD VOCABULARY: swing / Strategy Finder.
#
# strategy_scoring.py:664 scores multi-family structures on a DIFFERENT factor
# set -- {fit_dir, fit_vol, q_rr, q_be, q_pop, q_liq} -- reached from BOTH
# /options/swing and the scanner's Directional subtab (both route through
# strategy_table.detail_signal).
#
# Its numbers must NEVER be compared against the scanner bars above. q_be
# (q_breakeven_vs_em, strategy_scoring.py:369) REWARDS a breakeven inside the
# 1-sigma move -- "closer / inside EM = higher", because a directional debit
# trade only needs a small move to win -- whereas norm_em_buffer rewards the
# exact opposite. Same concept, inverted sign, because the payoff profiles are
# opposite. A `q_be < 50` test would flag good trades as bad.
#
# So flags come from the engine's OWN per-family hard gates instead
# (evaluate_gates, strategy_scoring.py:555), read off the `grade_reason` string
# it stamps on the signal. That is family-aware (separate credit/debit/naked
# bars), it is the engine's judgment rather than a second opinion, and it cannot
# invert the semantics.
#
# KNOWN GAP, deliberately silent: breakeven-vs-EM is intentionally NOT a hard
# gate ("a ranking quality factor, not a hard filter" -- evaluate_gates' own
# docstring), so the swing path covers liquidity, R:R and PoP but NOT the
# expected-move dealbreaker. No chip is emitted for that: it would appear on
# every swing signal, which is exactly the wallpaper this engine avoids.
_SWING_KEYS = frozenset(("fit_dir", "fit_vol", "q_rr", "q_be", "q_pop", "q_liq"))

# evaluate_gates' reason vocabulary -> (flag key, readable label). Keys are
# prefixed `gate_` to stay distinct from the scanner keys.
_GATE_FLAGS = {
    "liquidity": ("gate_liquidity", "Thin liquidity"),
    "R:R": ("gate_rr", "Reward too thin for the risk"),
    "PoP": ("gate_pop", "Low probability of profit"),
}


def _is_swing(factor_scores):
    """True for the swing/Strategy-Finder factor shape.

    Any overlap with the six q_*/fit_* names is proof: none of them appears in
    the scanner's eleven or the IC's three, so this cannot false-positive, and
    testing the whole set rather than one key survives a partial dict.
    """
    return bool(_SWING_KEYS & set(factor_scores))


def _swing_flags(grade_reason):
    """Flags for a swing signal, taken from the engine's own gate verdict.

    ``grade_reason`` is one of: "Fails: <dims>", "Excellent on all quality
    gates", "Passes all quality gates", "Fillable but middling quality", or
    "unscored" (strategy_scoring.py:653-680). Only the first and last say
    anything a trader must act on.
    """
    reason = (grade_reason or "").strip()
    if reason == "unscored":
        return [{"key": "unscored", "label": "Signal could not be scored",
                 "state": "unavailable"}]
    if not reason.startswith("Fails:"):
        return []
    out = []
    for token in reason[len("Fails:"):].split(","):
        dim = token.strip()
        if not dim:
            continue
        # Unknown dimensions are forwarded rather than dropped, so a new gate
        # added upstream surfaces instead of silently vanishing.
        key, label = _GATE_FLAGS.get(dim, (f"gate_{dim}", f"Fails {dim} quality gate"))
        out.append({"key": key, "label": label, "state": "tripped"})
    return out


def _is_iron_condor(signal, factor_scores):
    """True for an IC by EITHER its type or its leg-scored factor shape.

    Both, because each covers the other's blind spot: ``type`` is the app-wide
    convention (``factor_rows``/``_strikes_text`` already branch on it) and is
    the only evidence on an adapter-synthesized row that carries no
    factor_scores at all, such as a paper trade; ``pcs_leg`` is direct proof of
    the shape that causes the blindness, and survives a row whose type field was
    renamed or dropped. Neither predicate can fire on a non-IC -- only
    score_iron_condor emits pcs_leg -- so the union adds no false positives.
    """
    return signal.get("type") == "IC" or "pcs_leg" in factor_scores


def _score(fs, key):
    v = fs.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def flags_for(signal):
    """Dealbreaker flags for a signal: [{key, label, state}, ...].

    ``state`` is "tripped" (measured and beyond the bar), "unmeasured" (Tier 2
    said so, or bid/ask are absent), or "unavailable" (the iron-condor note --
    the checks cannot run at all).

    Flags are ABSENT for a clean trade. Only liquidity infers its own
    provenance, from bid/ask presence; the other four report "tripped" only.
    See the block comment above for why the 50.0 sentinel was removed.

    Total over adversarial input: never raises, always returns a list.
    """
    s = signal or {}
    if not isinstance(s, dict):
        return []
    fs = s.get("factor_scores") or {}
    if not isinstance(fs, dict):
        fs = {}
    unavailable = set(s.get("factors_unavailable") or ())
    grade_reason = s.get("grade_reason")
    out = []

    def add(key, label, tripped, measured):
        if not measured:
            out.append({"key": key, "label": f"{label} not measured",
                        "state": "unmeasured"})
        elif tripped:
            out.append({"key": key, "label": label, "state": "tripped"})

    # A row carrying NO scores at all is not triageable: synth_from_captured and
    # synth_from_trade emit neither factor_scores nor grade_reason, because an
    # already-open position has nothing left to reject. Silence, not a chip.
    # This precedes the IC check on purpose -- both adapters set `type` from
    # `strategy`, so a captured/paper iron condor would otherwise collect a lone
    # IC note while a captured/paper credit spread collected nothing.
    if not fs and not grade_reason:
        return []

    # Swing / Strategy Finder: the engine already decided, per family.
    if _is_swing(fs):
        return _swing_flags(grade_reason)

    is_ic = _is_iron_condor(s, fs)
    if is_ic:
        # Say so outright, then skip the five checks whose keys an IC lacks.
        out.append(dict(_IC_NOTE))
    else:
        # Liquidity -- bid/ask presence on the signal is direct proof of
        # measurement, so this one keeps its "unmeasured" state.
        liq = _score(fs, "liq")
        liq_measured = (s.get("bid") is not None and s.get("ask") is not None
                        and "liq" not in unavailable)
        if liq is not None or not liq_measured:
            add("liq", "Thin liquidity", liq is not None and liq < _SEMANTIC_BAR,
                liq is not None and liq_measured)

    # Credit vs risk -- rr_pct rides on the signal, so presence is proof. This is
    # the ONE dealbreaker that also works for an iron condor
    # (scanner_engine.py:1065 sets rr_pct on the IC itself), so it stays outside
    # the branch above.
    rr = s.get("rr_pct")
    rr_val = float(rr) if isinstance(rr, (int, float)) and not isinstance(rr, bool) else None
    if rr_val is not None:
        add("rr", "Credit thin for the risk", rr_val < MIN_RR_PCT, True)

    if is_ic:
        return out

    # Expected move / trend / walls -- "tripped" only. An exact 50.0 is NOT
    # treated as evidence of anything (see above); an explicit
    # factors_unavailable entry from Tier 2 still is.
    for key, label, bar in (("em", "Short strike inside 1σ move", _SEMANTIC_BAR),
                            ("trend", "Trend against the structure", _SEMANTIC_BAR),
                            ("gex", "Near a gamma wall", WALL_FLAG_BAR),
                            ("dex", "Near a delta wall", WALL_FLAG_BAR)):
        val = _score(fs, key)
        if val is None:
            continue
        add(key, label, val < bar, key not in unavailable)
    return out


def flag_count(signal):
    """How many flags a signal raises -- drives the collapsed-strip badge.

    Counts the iron-condor note too: a zero badge on an IC would restore exactly
    the false confidence that note exists to prevent.
    """
    return len(flags_for(signal))


def flag_class(state):
    """Finite state -> a fixed palette class (never build a class from a value).

    "unavailable" styles as a warning, not neutral: being unable to triage is
    something to be told about, not a clean bill of health.
    """
    return TXT_NEG if state == "tripped" else TXT_WARN


def _money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _pct(v):
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"


# Every adapter emits PER-SHARE dollars; the panel displays per-contract. One
# option contract covers 100 shares. Keeping the conversion in exactly one place
# is the fix for the unit collision documented in the design doc -- the paper
# adapter previously mixed per-share credit with whole-position max loss in the
# same row.
CONTRACT_MULTIPLIER = 100


def per_contract(per_share):
    """Per-share dollars -> per-contract dollars. None for anything non-numeric."""
    if isinstance(per_share, bool) or not isinstance(per_share, (int, float)):
        return None
    return float(per_share) * CONTRACT_MULTIPLIER


def money_per_contract(per_share):
    """Formatted per-contract dollars carrying an explicit unit, or an em-dash."""
    v = per_contract(per_share)
    return "—" if v is None else f"${v:,.2f} per contract"


def cost_row(signal):
    """(label, text) for the money-in/out row — 'Credit' or 'Debit'.

    Magnitude is always shown positive; the LABEL carries the direction, so a
    debit can never read as a credit. Input is PER-SHARE dollars (see
    strategy_table._fill_net_cost, which reconciles the per-contract
    ``net_credit``/``net_debit`` onto this scale).
    """
    s = signal or {}
    v = s.get("net_cost")
    if v is None:
        v = s.get("credit")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return ("Credit", "—")
    label = "Debit" if v < 0 else "Credit"
    return (label, money_per_contract(abs(v)))


def breakevens(raw):
    """Normalize a breakeven field to a list of floats.

    Iron condors store TWO breakevens as the string "put_be/call_be"
    (scanner_engine.py:1069). The old code formatted with ``_money``, which
    requires a number, so every IC rendered an em-dash. Credit spreads store a
    plain float. Both shapes are handled; anything unparseable yields [].
    """
    if raw is None:
        return []
    if isinstance(raw, bool):
        return []
    if isinstance(raw, (int, float)):
        return [float(raw)]
    out = []
    for part in str(raw).split("/"):
        try:
            out.append(float(part.strip()))
        except (TypeError, ValueError):
            return []
    return out


def breakeven_text(raw):
    """Display string for one or two breakevens, or an em-dash when absent."""
    vals = breakevens(raw)
    if not vals:
        return "—"
    return " / ".join(f"${v:,.2f}" for v in vals)


def iv_marker_value(signal):
    """Current IV for the 52-week range marker, or None to draw no marker.

    The old default of ``current_iv or iv_low_52w`` planted the marker at the
    52-week LOW whenever current IV was missing, which reads as a confident
    "IV is cheap" rather than "unknown".
    """
    s = signal or {}
    v = s.get("current_iv")
    if v is None:
        v = s.get("short_iv")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    return None


def dte_text(signal):
    """Days to expiry, saying so when the figure is days-at-ENTRY.

    Captured signals store ``dte_at_entry``; an aged signal was displaying a DTE
    that had already elapsed under the same label as a live one.
    """
    s = signal or {}
    dte = s.get("dte")
    if not isinstance(dte, (int, float)) or isinstance(dte, bool):
        return "—"
    return f"{dte:g} DTE at entry" if s.get("dte_is_entry") else f"{dte:g} DTE"


def gauge_metric(signal):
    """What the gauge shows, always with the caption naming it.

    The old code fell back from composite_score to pop_pct while keeping the
    composite's grade caption -- two different 0-100 scales on one unlabelled
    face. The grade belongs to the composite ONLY, so the PoP fallback carries
    no grade.
    """
    s = signal or {}
    score = s.get("composite_score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return {"value": score, "caption": "Composite score",
                "grade": s.get("grade") or ""}
    pop = s.get("pop_pct")
    if isinstance(pop, (int, float)) and not isinstance(pop, bool):
        return {"value": pop, "caption": "Probability of profit", "grade": ""}
    return {"value": 0, "caption": "No score available", "grade": ""}


def _leg_pair(short_k, long_k, right):
    """One 'Sell X / Buy Y' instruction line, or None when strikes are absent."""
    if short_k is None or long_k is None:
        return None
    return f"Sell {short_k:g} {right}  /  Buy {long_k:g} {right}"


def _leg_instruction(leg):
    """One leg as 'Sell 2x 410 C', or None when it carries no usable strike.

    ``qty`` is shown ONLY when it is not 1 — a butterfly body trades at 2x, and
    an invisible multiplier misstates the position. ``kind`` decides the right,
    so a call can never be labelled as a put.
    """
    strike = leg.get("strike")
    if not isinstance(strike, (int, float)) or isinstance(strike, bool):
        return None
    action = "Sell" if str(leg.get("side", "")).lower() == "short" else "Buy"
    right = "C" if str(leg.get("kind", "")).lower() == "call" else "P"
    qty = leg.get("qty", 1)
    mult = f"{qty:g}x " if isinstance(qty, (int, float)) and not isinstance(
        qty, bool) and qty != 1 else ""
    return f"{action} {mult}{strike:g} {right}"


def _lines_from_legs(legs):
    """Group canonical legs by ``kind`` onto one line each, in emitted order.

    The emitted order is already the trade-natural one in every family, so it is
    PRESERVED rather than re-sorted: adapt_credit_spread emits [short, long]
    ('Sell 400 P / Buy 395 P') while build_debit_verticals emits [long, short]
    ('Buy 400 C / Sell 410 C') — in both cases the defining leg leads. Grouping
    by kind keeps an iron condor's two verticals on separate lines.
    """
    groups, order = {}, []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        text = _leg_instruction(leg)
        if text is None:
            continue
        kind = str(leg.get("kind", "")).lower()
        if kind not in groups:
            groups[kind] = []
            order.append(kind)
        groups[kind].append(text)
    return ["  /  ".join(groups[k]) for k in order]


def contract_lines(signal):
    """The position as instructions rather than a range.

    '$400 - $395 (5-wide)' read as a descending range and hid which leg was
    short. An iron condor yields two lines, one per vertical.

    Prefers the canonical ``legs`` list when present: every natively-built swing
    family (LONG_CALL/SHORT_PUT/BULL_CALL/…) carries legs and NO strike keys, so
    the strike-key path below rendered nothing at all for them. Scanner credit
    spreads and iron condors carry only the strike keys and still take it.
    """
    s = signal or {}
    lines = _lines_from_legs(s.get("legs") or [])
    if lines:
        # A single vertical may still name its width; two lines are an iron
        # condor, whose two different widths one number could not describe.
        w = s.get("width")
        if len(lines) == 1 and isinstance(w, (int, float)) and not isinstance(w, bool):
            lines.append(f"{w:g} wide")
        return lines
    if s.get("type") == "IC":
        for sk, lk, right in ((s.get("short_strike"), s.get("long_strike"), "P"),
                              (s.get("call_short"), s.get("call_long"), "C")):
            line = _leg_pair(sk, lk, right)
            if line:
                lines.append(line)
        return lines
    # "CC" alone matched only CCS, so a legless LONG_CALL/BULL_CALL reaching this
    # path would be labelled as PUTS. Match the call families by name too.
    t = str(s.get("type", "")).upper()
    right = "C" if (t.startswith("CC") or "CALL" in t) else "P"
    line = _leg_pair(s.get("short_strike"), s.get("long_strike"), right)
    if line:
        lines.append(line)
    w = s.get("width")
    if lines and isinstance(w, (int, float)) and not isinstance(w, bool):
        lines.append(f"{w:g} wide")
    return lines


def _signal_title(s):
    return " · ".join(x for x in (s.get("symbol", ""), s.get("type", ""),
                                  s.get("trade_type", "")) if x) or "Signal"


def _tile_slot(label):
    """A header tile (label + value); returns the value label to update in place.

    ``min-w-0 w-full`` — the tile fills its grid cell and may SHRINK below its
    content's natural width, so the 2×2 grid always fits the panel (at 290px the
    old ``min-w-[92px]`` tiles + the gauge overflowed the panel edge)."""
    with ui.card().classes(f"p-2 min-w-0 w-full {TILE_3D}"):
        ui.label(label).classes("text-xs opacity-60")
        return ui.label("—").classes("text-base font-bold")


def _kv(label, value, color=None):
    with ui.row().classes("justify-between w-full"):
        ui.label(label).classes("opacity-70 text-sm")
        lbl = ui.label(value).classes("text-sm")
        if color:
            lbl.classes(add=color)


def _strikes_text(s):
    if s.get("type") == "IC":
        return (f"P {s.get('short_strike','?')}/{s.get('long_strike','?')}  "
                f"C {s.get('call_short','?')}/{s.get('call_long','?')}")
    sk, lk, w = s.get("short_strike"), s.get("long_strike"), s.get("width")
    if sk is None:
        return "—"
    wtxt = f" ({w}-wide)" if isinstance(w, (int, float)) else ""
    return f"${sk} - ${lk}{wtxt}"


def _build_cards(s):
    """Build the five expansion cards (run inside the cleared body container)."""
    # Card 1 — Trade Info
    with ui.expansion("Trade Info", value=True).classes("w-full"):
        _kv("Expiration", f"{s.get('expiration','—')} ({s.get('dte','?')} DTE)")
        if isinstance(s.get("underlying_price"), (int, float)):
            _kv("Current price", _money(s.get("underlying_price")))
        _kv("Strikes", _strikes_text(s))
        _kv("Max Loss", _money(s.get("max_loss")), RED)
        if s.get("max_contracts") is not None:
            _kv("Max Contracts", str(s.get("max_contracts")))
        if isinstance(s.get("expected_pnl_10"), (int, float)):
            v = s["expected_pnl_10"]
            _kv("E[P&L] (10ct)", f"${v:+,.0f}", GREEN if v >= 0 else RED)
        if s.get("rr_pct") is not None:
            _kv("R:R Ratio", _pct(s.get("rr_pct")))

    # Card 2 — Greeks
    with ui.expansion("Greeks", value=True).classes("w-full"):
        with ui.grid(columns=4).classes("gap-2 w-full"):
            _greek("Δ", s.get("short_delta"), fmt="{:+.4f}")
            theta = s.get("net_theta")
            _greek("Θ", theta, color=(GREEN if isinstance(theta, (int, float)) and theta > 0 else RED))
            _greek("Vega", s.get("net_vega"), fmt="{:+.3f}")
            _greek("IV", s.get("short_iv"), fmt="{:.1f}%")

    # Card 3 — Composite Score (factor bars)
    with ui.expansion(f"Composite Score: {s.get('composite_score','?')} "
                      f"({s.get('grade','?')})").classes("w-full"):
        for label, val, known in factor_rows(s.get("factor_scores"), s.get("type"),
                                             s.get("factors_unavailable")):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(label).classes("text-xs w-20 opacity-80")
                ui.html(svg.gradient_bar_svg(val if known else 0))
                ui.label(factor_value_text(val, known)).classes("text-xs w-8 text-right")

    # Card 4 — IV Analysis (best-effort from available keys)
    if any(s.get(k) is not None for k in ("current_iv", "iv_rank", "iv_percentile", "short_iv")):
        with ui.expansion("IV Analysis").classes("w-full"):
            _kv("ATM IV", _pct(s.get("current_iv") if s.get("current_iv") is not None else s.get("short_iv")))
            marker = iv_marker_value(s)
            if (marker is not None
                    and isinstance(s.get("iv_low_52w"), (int, float))
                    and isinstance(s.get("iv_high_52w"), (int, float))):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label("52w").classes("text-xs w-12 opacity-80")
                    ui.html(svg.range_marker_svg(s["iv_low_52w"], s["iv_high_52w"], marker))
            if s.get("iv_rank") is not None:
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label("Rank").classes("text-xs w-12 opacity-80")
                    ui.html(svg.gradient_bar_svg(s["iv_rank"]))
                    ui.label(f"{s['iv_rank']:g}").classes("text-xs w-8 text-right")

    # Card 5 — Expected Move (best-effort)
    em = s.get("expected_moves")
    if isinstance(em, dict):
        with ui.expansion("Expected Move").classes("w-full"):
            for key, label in (("daily", "1-day"), ("weekly", "1-week"), ("monthly", "30-day")):
                blk = em.get(key) or {}
                d = blk.get("move_dollars")
                p = blk.get("move_percent", blk.get("move_pct"))
                if isinstance(d, (int, float)):
                    pct = f" ({p:.2f}%)" if isinstance(p, (int, float)) else ""
                    _kv(label, f"±${d:,.2f}{pct}")


def _greek(label, value, fmt="{:.3f}", color=None):
    with ui.card().classes(f"p-2 items-center {TILE_3D}"):
        ui.label(label).classes("text-xs opacity-60")
        txt = fmt.format(value) if isinstance(value, (int, float)) else "—"
        lbl = ui.label(txt).classes("text-sm font-bold")
        if color and isinstance(value, (int, float)):
            lbl.classes(add=color)


class _Handle:
    def __init__(self, state, header, sig_title, gauge_el, tiles, body):
        self._state = state          # shared with the collapse toggle
        self._header = header        # persistent header (title + gauge + tiles)
        self._sig_title = sig_title
        self._gauge = gauge_el
        self._tiles = tiles          # {key: value-label}
        self._body = body            # cleared + rebuilt per selection

    def clear(self):
        self._state["has_signal"] = False
        self._header.set_visibility(False)
        self._body.clear()
        with self._body:
            ui.label(_PLACEHOLDER).classes("opacity-60")

    def update(self, signal):
        if not signal:
            self.clear()
            return
        s = signal
        self._state["has_signal"] = True
        self._header.set_visibility(self._state["open"])
        self._sig_title.text = _signal_title(s)
        # Gauge value: the composite score when the signal has one (scanner/swing/
        # captured); for a paper trade — which never stored a composite score, so
        # the gauge used to sit at 0 — fall back to PoP, a real 0-100 quality read.
        score = s.get("composite_score")
        if score is None:
            score = s.get("pop_pct")
        self._gauge.options = gauge_figure(score or 0, s.get("grade", ""), height=104)
        self._gauge.update()
        for key, _label, value_fn, color_fn in _TILES:
            lbl = self._tiles[key]
            lbl.text = value_fn(s)
            _set_color(lbl, color_fn(s))
        self._body.clear()
        with self._body:
            _build_cards(s)


def render(width: int = 360):
    """Build the collapsible detail panel; returns a handle with update()/clear().

    The panel owns its own column so it can collapse to a thin strip (reclaiming
    horizontal space) and expand again via the header toggle.
    """
    expanded_w = f"w-[{width}px]"
    # The panel is a Deep Slate CARD (navy bg + hairline border + radius + padding)
    # so it reads as a bordered panel like the rest of the app / the mockup.
    col = ui.column().classes(f"shrink-0 gap-2 {CARD}").classes(expanded_w)
    with col:
        with ui.row().classes("items-center justify-between w-full no-wrap"):
            title = ui.label("Trade detail").classes("text-subtitle1 font-bold")
            toggle_btn = ui.button(icon="last_page").props("flat round dense") \
                .tooltip("Collapse panel")
        # Persistent signal header (built once → registers the Highcharts ESM at
        # page load; updated in place per selection). Hidden until a signal lands.
        header = ui.column().classes("w-full gap-1")
        tiles = {}
        with header:
            sig_title = ui.label("").classes("text-subtitle1 font-bold")
            # Gauge STACKED above a full-width 2×2 tile grid: side-by-side the
            # gauge (160px) + min-width tiles overflowed the 290px panel (tiles
            # bled past the right edge). Stacking fits every panel width.
            gauge_el = ui.highchart(gauge_figure(0, "", height=104)) \
                .classes("self-center w-[160px] h-[104px]")
            with ui.grid(columns=2).classes("gap-2 w-full"):
                for key, label, _vf, _cf in _TILES:
                    tiles[key] = _tile_slot(label)
        header.set_visibility(False)
        body = ui.column().classes("w-full gap-2")
        with body:
            ui.label(_PLACEHOLDER).classes("opacity-60")

    state = {"open": True, "has_signal": False}

    def toggle():
        state["open"] = not state["open"]
        title.visible = state["open"]
        body.visible = state["open"]
        header.visible = state["open"] and state["has_signal"]
        if state["open"]:
            col.classes(remove="w-11", add=expanded_w)
            toggle_btn.props("icon=last_page").tooltip("Collapse panel")
        else:
            col.classes(remove=expanded_w, add="w-11")
            toggle_btn.props("icon=first_page").tooltip("Expand panel")

    toggle_btn.on_click(toggle)
    return _Handle(state, header, sig_title, gauge_el, tiles, body)
