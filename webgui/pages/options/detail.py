"""Shared, persistent Trade detail panel.

One panel, reused by every signal table (scanner, captured, paper, swing). Each
table synthesizes a signal-like dict and calls ``handle.update(signal)``.

LAYOUT = REJECT -> VERIFY -> EXPLORE, top to bottom, because triage is mostly
fast rejection. The **header** (title + gauge + its caption + the dealbreaker
flags) is built ONCE in ``render()`` and updated in place; below it the body
rebuilds per selection as the CONTRACT (what you would actually place), then the
ECONOMICS (four figures, each carrying its unit), then four COLLAPSED
expansions — score factors, greeks, implied volatility, expected move — for the
minority of signals that survive far enough to be explored.

Everything above the expansions answers "reject or not"; nothing that answers it
is behind a click. The flags are the reason the header is not just a score: they
are ABSENT for a clean trade, so their presence is the signal.

The speedometer is the shared Highcharts angular gauge (``gauge.py`` — painted
rainbow face + needle) and ALWAYS names its metric in the caption beneath, since
it shows the composite score for some sources and PoP for others. The factor/IV
bars + range markers are SVG (``svg.py``). Robust to missing keys (fields vary by
trade type / source).

The gauge is persistent (not recreated per selection) so the Highcharts ESM is
registered at initial page render — a gauge added only on selection, on a page
with no other chart at load, fails with "Failed to resolve module specifier".
"""
from nicegui import ui

from ..gauge import gauge_figure
from . import svg
from .theme import (TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL,
                    TILE_3D, CARD, EYEBROW, MUTED)

# Semantic state-color class tokens (Tailwind text-[...] arbitrary values). Names
# kept (many refs) but the VALUES are now class strings applied via .classes().
GREEN, AMBER, RED, NEUTRAL = TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL

FACTOR_LABELS = [
    ("rr", "R:R"), ("pop", "PoP"), ("theta", "Theta"), ("iv", "IV Rank"),
    ("iv_hv", "IV/HV"), ("vega", "Vega Risk"), ("em", "EM Buffer"),
    ("liq", "Liquidity"), ("trend", "Trend"), ("gex", "GEX"), ("dex", "DEX"),
]

# The swing / Strategy Finder vocabulary (strategy_scoring.py:664) — see the
# block comment above ``_is_swing``, which detects it by SHAPE.
#
# ORDER = descending influence on the composite, so the card reads top-down in
# order of what actually moved the score: quality first (0.7 of the composite vs
# fit's 0.3), and within each group by that group's own weights — QUALITY_WEIGHTS
# q_rr .30 > q_be .25 = q_pop .25 > q_liq .20, then FIT_DIR_W .6 > FIT_VOL_W .4.
#
# LABELS deliberately reuse the scanner's wording for the three concepts the two
# vocabularies share ("R:R", "PoP", "Liquidity"). Two reasons: the same concept
# should not read as two different things depending on which tab you came from,
# and the economics rows directly above this card already spell out the RAW
# figures as "Risk / reward" and "Probability" — so the spelled-out forms would
# each appear TWICE in one card, on two different scales (a percentage above, a
# 0-100 quality grade here). The scanner branch resolved that same collision the
# same way, and both are genuine trader terms rather than casual shortenings.
#
# ``q_be`` gets whole words instead: the scanner's nearest label is "EM Buffer",
# but the two are INVERTED (see the ``_is_swing`` comment) — reusing it would be
# actively wrong, and "EM" is a shortening anyway. It is one label covering a
# family-dependent measure (directional: breakeven within reach of the 1-sigma
# move; neutral: a profit zone wide relative to it), so it names the comparison
# rather than the direction.
_SWING_FACTOR_LABELS = [
    ("q_rr", "R:R"), ("q_be", "Breakeven vs move"), ("q_pop", "PoP"),
    ("q_liq", "Liquidity"), ("fit_dir", "Direction fit"),
    ("fit_vol", "Volatility fit"),
]

_PLACEHOLDER = "Select a signal to view details…"


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

    THREE vocabularies, and the swing one is chosen by SHAPE, not ``trade_type``.
    A swing signal's type can be "PCS" or "IC" (adapt_credit_spread /
    adapt_iron_condor re-score existing scanner structures on the swing model), so
    a type-keyed branch would send a swing-scored iron condor into the IC branch
    and print three em-dashes for leg factors it never had. ``_is_swing`` is the
    same predicate ``flags_for`` uses, so the two cannot drift apart.
    """
    fs = factor_scores or {}
    missing = set(unavailable or ())
    if _is_swing(fs):
        keys = _SWING_FACTOR_LABELS
    elif trade_type == "IC":
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
    convention (``factor_rows``/``contract_lines`` already branch on it) and is
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


def flag_badge_text(n):
    """Badge label for the collapse toggle; empty hides it. Caps at '9+'.

    The badge floats on the toggle button rather than inside the header, so it
    SURVIVES collapsing the panel — a user who has collapsed the detail strip to
    reclaim table width still sees that the selected signal raised warnings. A
    zero renders nothing at all: a "0" badge is visual noise on the clean trades,
    which are the common case.
    """
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return ""
    return "9+" if n > 9 else str(n)


def flag_class(state):
    """Finite state -> a fixed palette class (never build a class from a value).

    "unavailable" styles as a warning, not neutral: being unable to triage is
    something to be told about, not a clean bill of health.
    """
    return TXT_NEG if state == "tripped" else TXT_WARN


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


def _kv(label, value, color=None):
    with ui.row().classes("justify-between w-full"):
        ui.label(label).classes("opacity-70 text-sm")
        lbl = ui.label(value).classes("text-sm")
        if color:
            lbl.classes(add=color)


def _build_cards(s):
    """Contract, then economics, then collapsed detail — reject/verify/explore."""
    # 1 — THE CONTRACT. What you would actually place, as instructions. This is
    # first because a signal you cannot identify is one you cannot act on, and
    # the old panel buried the strikes in a "Strikes" key/value row.
    lines = contract_lines(s)
    if lines:
        with ui.column().classes(f"w-full gap-0 {CARD}"):
            for line in lines:
                ui.label(line).classes("text-sm font-bold")
            exp = s.get("expiration")
            if exp:
                ui.label(f"Exp {exp}").classes(f"text-xs {MUTED}")

    # 2 — THE ECONOMICS. Four figures, each carrying its unit: the two dollar
    # rows say "per contract" outright, so a per-share number can never be read
    # as a position total.
    with ui.column().classes("w-full gap-1"):
        cost_label, cost_text = cost_row(s)
        _kv(cost_label, cost_text, GREEN if cost_label == "Credit" else NEUTRAL)
        _kv("Max loss", money_per_contract(s.get("max_loss")), RED)
        _kv("Breakeven", breakeven_text(s.get("breakeven")))
        _kv("Probability", _pct(s.get("pop_pct")), pop_color(s.get("pop_pct")))

    # 3 — EXPLORE. All four collapsed: the ladder above already answered
    # reject-or-not, so nothing here competes with it for attention.
    with ui.expansion("Score factors").classes("w-full"):
        if s.get("rr_pct") is not None:
            _kv("Risk / reward", _pct(s.get("rr_pct")))
        if s.get("max_contracts") is not None:
            _kv("Max contracts", str(s.get("max_contracts")))
        if isinstance(s.get("expected_pnl_10"), (int, float)):
            v = s["expected_pnl_10"]
            _kv("Expected P&L (10 contracts)", f"${v:+,.0f}", GREEN if v >= 0 else RED)
        for label, val, known in factor_rows(s.get("factor_scores"), s.get("type"),
                                             s.get("factors_unavailable")):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(label).classes("text-xs w-20 opacity-80")
                ui.html(svg.gradient_bar_svg(val if known else 0))
                ui.label(factor_value_text(val, known)).classes("text-xs w-8 text-right")

    with ui.expansion("Greeks").classes("w-full"):
        with ui.grid(columns=4).classes("gap-2 w-full"):
            _greek("Δ", s.get("short_delta"), fmt="{:+.4f}")
            theta = s.get("net_theta")
            _greek("Θ", theta, color=(GREEN if isinstance(theta, (int, float)) and theta > 0 else RED))
            _greek("Vega", s.get("net_vega"), fmt="{:+.3f}")
            _greek("IV", s.get("short_iv"), fmt="{:.1f}%")

    # Implied volatility (best-effort from available keys)
    if any(s.get(k) is not None for k in ("current_iv", "iv_rank", "iv_percentile", "short_iv")):
        with ui.expansion("Implied volatility").classes("w-full"):
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
    def __init__(self, state, header, sig_title, sig_sub, gauge_el, gauge_caption,
                 flag_box, flag_badge, body):
        self._state = state          # shared with the collapse toggle
        self._header = header        # persistent header (title + gauge + flags)
        self._sig_title = sig_title
        self._sig_sub = sig_sub
        self._gauge = gauge_el
        self._caption = gauge_caption
        self._flag_box = flag_box    # rebuilt per selection; empty when clean
        self._flag_badge = flag_badge  # floats on the toggle; survives collapse
        self._body = body            # cleared + rebuilt per selection

    def _set_flag_badge(self, n):
        txt = flag_badge_text(n)
        self._flag_badge.text = txt
        self._flag_badge.set_visibility(bool(txt))

    def clear(self):
        self._state["has_signal"] = False
        self._header.set_visibility(False)
        self._flag_box.clear()
        self._set_flag_badge(0)
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
        self._sig_sub.text = " · ".join(
            x for x in (s.get("trade_type", ""), dte_text(s)) if x and x != "—")

        # gauge_metric decides the value AND the caption together, so the face can
        # never show PoP under a composite-score grade (they are different scales).
        m = gauge_metric(s)
        self._gauge.options = gauge_figure(m["value"] or 0, m["grade"], height=104)
        self._gauge.update()
        self._caption.text = m["caption"]

        flags = flags_for(s)
        self._flag_box.clear()
        with self._flag_box:
            for f in flags:
                ui.label(f"⚠ {f['label']}").classes(
                    f"text-xs {flag_class(f['state'])}")
        self._set_flag_badge(len(flags))

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
            # `relative` wrapper so Quasar's `floating` badge anchors to the
            # button's top-right corner rather than the layout row.
            with ui.element("div").classes("relative"):
                toggle_btn = ui.button(icon="last_page").props("flat round dense") \
                    .tooltip("Collapse panel")
                with toggle_btn:
                    flag_badge = ui.badge("", color="red").props("floating") \
                        .classes("text-xs")
                flag_badge.set_visibility(False)
        # Persistent signal header (built once → registers the Highcharts ESM at
        # page load; updated in place per selection). Hidden until a signal lands.
        # The gauge MUST stay here rather than move into _build_cards: a
        # ui.highchart created only on selection, on a page whose initial render
        # had no chart, fails with "Failed to resolve module specifier
        # nicegui-highcharts" (the ESM import map is fixed at first render).
        header = ui.column().classes("w-full gap-1")
        with header:
            sig_title = ui.label("").classes("text-subtitle1 font-bold")
            sig_sub = ui.label("").classes(f"text-xs {MUTED}")
            gauge_el = ui.highchart(gauge_figure(0, "", height=104)) \
                .classes("self-center w-[160px] h-[104px]")
            gauge_caption = ui.label("").classes(f"text-xs self-center {EYEBROW}")
            # Dealbreakers sit ABOVE the fold, directly under the score they
            # qualify. Empty for a clean trade — absence is the all-clear.
            flag_box = ui.column().classes("w-full gap-1")
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
    return _Handle(state, header, sig_title, sig_sub, gauge_el, gauge_caption,
                   flag_box, flag_badge, body)
