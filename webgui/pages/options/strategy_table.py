"""Display builders for the multi-strategy swing-scan table (Tier-1, PURE).

The options service publishes ``cache:options:swing`` as
``{signals:[...], view:{...}, symbol, params}`` where each signal is the NORMALIZED
multi-strategy shape (LONG_CALL / LONG_PUT / SHORT_CALL / SHORT_PUT / BULL_CALL /
BEAR_PUT / PCS / CCS / IRON_CONDOR, each carrying a ``legs`` list, ``family``,
``bias``, ``net_debit``/``net_credit``, ``breakevens``, ``rr``, …). These pure
functions format that shape into ``ui.table`` columns/rows + a market-view banner,
and adapt a signal so the SHARED Trade detail panel (``detail.py``) renders it.

No ``ui.`` calls live here — everything is unit-tested in
``webgui/tests/test_strategy_table.py``. Dynamic colors map a FINITE state (bias,
score zone) to a fixed Tailwind class (Tailwind-first standard), never a runtime
``.style()`` hex.
"""
from . import scanner
from .theme import TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL


# Structures the Paper-trade button is allowed for. Credit spreads (``IC`` is the
# engine's internal iron-condor key, ``IRON_CONDOR`` the normalized one) PLUS the
# defined-risk DEBIT structures the ledger now supports (long options + debit verticals).
# Naked shorts (SHORT_CALL/SHORT_PUT) are excluded — undefined risk.
_PAPER_TYPES = {"PCS", "CCS", "IC", "IRON_CONDOR",
                "LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT"}

# Inferred-view → what option families it favors (kept short for the banner).
_FAVORS = {
    "bullish": "long / debit",
    "bearish": "short / put-debit / call-credit",
    "neutral": "condors / flies / credit",
}


def _fmt_strike(value):
    """Strike → compact string: drop a trailing '.0' on whole numbers
    (450.0 → '450'), keep fractional strikes (437.5)."""
    if value is None:
        return "?"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _short_exp(expiration):
    """Expiration ``'YYYY-MM-DD'`` → ``'MM/DD'`` for a compact cell; '—' if absent."""
    if not expiration:
        return "—"
    try:
        _y, m, d = str(expiration).split("-")[:3]
        return f"{int(m):02d}/{int(d):02d}"
    except (ValueError, AttributeError):
        return str(expiration)


def legs_summary(legs):
    """Compact one-line summary of the legs, e.g. ``"L 450C / S 455C"``.

    ``L`` = long, ``S`` = short; strike + ``C``/``P`` for call/put. Empty/None → '—'.
    """
    if not legs:
        return "—"
    parts = []
    for leg in legs:
        side = "L" if (leg.get("side") == "long") else "S"
        kind = "C" if (leg.get("kind") == "call") else "P"
        parts.append(f"{side} {_fmt_strike(leg.get('strike'))}{kind}")
    return " / ".join(parts) if parts else "—"


def debit_credit_text(signal):
    """Net debit/credit as a signed 2dp string: ``"-2.50 debit"`` /
    ``"+1.70 credit"`` / ``"—"`` when neither is set."""
    s = signal or {}
    debit = s.get("net_debit")
    credit = s.get("net_credit")
    if isinstance(debit, (int, float)):
        return f"-{abs(debit):.2f} debit"
    if isinstance(credit, (int, float)):
        return f"+{abs(credit):.2f} credit"
    return "—"


def breakeven_text(signal):
    """Join ``breakevens`` (2dp) with ``" / "``, or ``"—"`` when empty."""
    bes = (signal or {}).get("breakevens") or []
    vals = [f"{b:.2f}" for b in bes if isinstance(b, (int, float))]
    return " / ".join(vals) if vals else "—"


def strategy_columns():
    """``ui.table`` column defs for the multi-strategy results table.

    Same dict shape as ``scanner.signal_columns()`` (``name``/``label``/``field``/
    ``sortable``/``align`` keys); the trailing actions column is centered + not
    sortable."""
    spec = [
        ("strategy_label", "Strategy"),
        ("bias", "Bias"),
        ("legs", "Legs"),
        ("expiration", "Expiry"),
        ("dte", "DTE"),
        ("debit_credit", "Debit/Credit"),
        ("max_profit", "Max P"),
        ("max_loss", "Max L"),
        ("rr", "R:R"),
        ("pop_pct", "PoP"),
        ("breakevens", "BE"),
        ("iv_rank", "IV Rank"),
        ("composite_score", "Score"),
        ("grade", "Grade"),
    ]
    cols = [
        {"name": field, "label": label, "field": field, "sortable": True, "align": "left"}
        for field, label in spec
    ]
    cols.append({"name": "actions", "label": "", "field": "actions", "align": "center"})
    return cols


def _bias_class(bias):
    """Map a finite bias state to a fixed Tailwind text-color class."""
    if bias == "bullish":
        return TXT_POS
    if bias == "bearish":
        return TXT_NEG
    return TXT_NEUTRAL


def grade_class(grade):
    """Map a quality grade (Strong/Good/Marginal/Weak) to a fixed Tailwind class.

    Strong/Good → green, Marginal → amber, Weak → red, anything else → neutral.
    """
    if grade in ("Strong", "Good"):
        return TXT_POS
    if grade == "Marginal":
        return TXT_WARN
    if grade == "Weak":
        return TXT_NEG
    return TXT_NEUTRAL


# Which SIDE of a signal's payoff is unbounded. The engine emits explicit
# ``unbounded_profit``/``unbounded_loss`` flags, but a signal cached BEFORE those
# existed carries only the legacy ``unbounded`` (True for either side). The two
# helpers below are the single place that reasoning lives — keep them together so
# the profit and loss cells can never drift into disagreeing about the same signal.
#
# The legacy fallback partitions ``unbounded`` by ``max_profit``, which is exact:
# only an unbounded-PROFIT long carries max_profit=None (``payoff_metrics``'
# ``call_coeff > 0`` branch), a naked short carries its credit-capped max_profit,
# and ``_normalize_credit``'s unknown-reward case (max_profit=None too) carries
# unbounded=False so it never reaches the fallback. Exactly one side can be
# unbounded, so the two are exclusive. Both engine invariants are pinned by
# ``options-scanner/tests/test_strategy_scanner.py`` (see the adapter's
# unknown-reward + flag-normalization tests) — they are enforced across a process
# boundary and a Redis cache, so a comment here would not survive alone.
#
# TODO: delete the legacy fallback (and this note) once no signal cached before
# the unbounded_profit/unbounded_loss keys existed can still be served — i.e.
# after options_svc has restarted and republished every cache:options:swing /
# cache:options:scan view. Until then a stale naked short would render its margin
# proxy as a finite risk cap, so the fallback is load-bearing, not decoration.

def _profit_is_unbounded(signal):
    """True when the PROFIT side is unbounded (a long call)."""
    if signal.get("unbounded_profit"):
        return True
    return bool(signal.get("unbounded")) and signal.get("max_profit") is None


def _loss_is_unbounded(signal):
    """True when the LOSS side is unbounded (a naked short)."""
    if signal.get("unbounded_loss"):
        return True
    return bool(signal.get("unbounded")) and signal.get("max_profit") is not None


def _fmt_max_profit(signal):
    """Max profit cell: '∞' only when the PROFIT side is unbounded, else 2dp.

    Never gates on the legacy ``unbounded`` alone — that is also True for a naked
    short, whose profit is capped at the credit. A missing ``max_profit`` is not a
    synonym for unlimited either: ``_normalize_credit`` leaves it None when a credit
    spread's source credit is absent (defined risk, UNKNOWN reward) → '—', not '∞'.
    """
    if _profit_is_unbounded(signal):
        return "∞"
    mp = signal.get("max_profit")
    return f"{mp:.2f}" if isinstance(mp, (int, float)) else "—"


def _fmt_max_loss(signal):
    """Max loss cell: '∞' when the LOSS side is unbounded (a naked short).

    The engine substitutes a margin proxy for an unbounded max_loss, which would
    otherwise render as a finite figure and read as a risk cap. It is not one.
    """
    if _loss_is_unbounded(signal):
        return "∞"
    return _fmt_2(signal.get("max_loss"))


def _fmt_2(value):
    return f"{value:.2f}" if isinstance(value, (int, float)) else "—"


def _fmt_1(value):
    return f"{value:.1f}" if isinstance(value, (int, float)) else "—"


def strategy_rows(signals):
    """Display rows for the multi-strategy table, sorted by composite score (desc).

    Robust to missing keys. Each row carries ``id`` (detail lookup), the formatted
    cells, plus ``_score_class`` / ``_bias_class`` (Tailwind ``:class`` bindings),
    ``_allow_paper`` (gates the Paper button to credit-creditable types) and
    ``_undefined_risk`` (badges a naked short, whose max loss has no cap).
    """
    rows = []
    for s in signals or []:
        score = s.get("composite_score")
        rows.append({
            "id": s.get("id"),
            "strategy_label": s.get("strategy_label", ""),
            "bias": s.get("bias", ""),
            "legs": legs_summary(s.get("legs")),
            "expiration": _short_exp(s.get("expiration")),
            "dte": s.get("dte"),
            "debit_credit": debit_credit_text(s),
            "max_profit": _fmt_max_profit(s),
            "max_loss": _fmt_max_loss(s),
            "rr": _fmt_2(s.get("rr")),
            "pop_pct": _fmt_1(s.get("pop_pct")),
            "breakevens": breakeven_text(s),
            "iv_rank": scanner.iv_rank_value(s.get("iv_rank")),
            "composite_score": score,
            "grade": s.get("grade", ""),
            "grade_reason": s.get("grade_reason", ""),
            "_score_class": scanner.score_zone_class(score),
            "_bias_class": _bias_class(s.get("bias")),
            "_grade_class": grade_class(s.get("grade")),
            "_allow_paper": s.get("type") in _PAPER_TYPES,
            "_undefined_risk": _loss_is_unbounded(s),
        })
    rows.sort(key=lambda r: (r["composite_score"] is not None, r["composite_score"] or 0),
              reverse=True)
    return rows


def _favors(view):
    """The option families an inferred direction favors (short banner phrase)."""
    return _FAVORS.get((view or {}).get("direction"), "—")


def view_banner_text(view):
    """One-line inferred-market-view banner, e.g.
    ``"Inferred view: Bullish · conviction 0.60 · IV low → favors long / debit"``.

    Empty/None view → a neutral placeholder prompting a scan."""
    view = view or {}
    direction = view.get("direction")
    if not direction:
        return "Run a scan to infer the market view."
    conviction = view.get("conviction")
    conv_txt = f"{conviction:.2f}" if isinstance(conviction, (int, float)) else "—"
    vol = view.get("vol_regime") or "—"
    return (f"Inferred view: {direction.capitalize()} · conviction {conv_txt} · "
            f"IV {vol} → favors {_favors(view)}")


def detail_signal(signal):
    """Shallow copy adapted for the SHARED detail panel.

    ``detail.py`` reads ``credit``/``breakeven``/``pop_pct``/``dte``; the normalized
    multi-leg signal stores ``net_credit``/``net_debit`` + a ``breakevens`` list, so
    fill ``credit`` from ``net_credit`` ONLY and ``breakeven`` (first breakeven) when
    absent. A debit structure (``net_credit`` None) deliberately leaves ``credit``
    unset → the panel's green "Credit" tile shows "—" rather than the DEBIT amount
    mislabeled as a credit. The input is NOT mutated.

    UNITS: ``net_credit`` is per-CONTRACT (payoff_metrics, x100) while ``credit``
    is per-SHARE across every other adapter — detail.py's ``CONTRACT_MULTIPLIER``
    comment states that convention and the panel multiplies by 100 to display. So
    the fill DIVIDES, exactly as ``_fill_net_cost`` does below. This path is only
    reachable for a NATIVELY-built structure (a naked SHORT_PUT/SHORT_CALL):
    ``_normalize_credit`` copies the source dict, so an adapted PCS/CCS/IC keeps
    its per-share source ``credit`` and never reaches the fallback."""
    out = dict(signal or {})
    if out.get("credit") is None:
        credit_d = _num(out.get("net_credit"))
        out["credit"] = (round(credit_d / _CONTRACT_MULT, 6)
                         if credit_d is not None else None)
    # ``max_loss`` is the SAME collision, and unlike credit it has no per-share
    # source field to shadow it: _normalize_credit scales it x100 and
    # payoff_metrics builds it per-contract natively, while the panel's other
    # three feeds are all per-share (raw scanner signal, paper's
    # _max_loss_per_share, captured's entry_max_loss). Normalize unconditionally
    # — every value on this path is per-contract.
    max_loss_d = _num(out.get("max_loss"))
    if max_loss_d is not None:
        out["max_loss"] = round(max_loss_d / _CONTRACT_MULT, 6)
    if out.get("breakeven") is None:
        bes = out.get("breakevens") or []
        out["breakeven"] = bes[0] if bes else None
    _fill_net_cost(out)
    return out


# strategy_scanner.payoff_metrics emits net_debit/net_credit in per-CONTRACT
# dollars (net * _CONTRACT_MULT); ``net_cost`` is per-SHARE, so they divide.
_CONTRACT_MULT = 100.0


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _fill_net_cost(out):
    """Signed cost in PER-SHARE dollars: positive = credit in, negative = debit out.

    ``credit`` deliberately stays None for a debit so the green Credit tile
    cannot show a DEBIT mislabelled as a credit — but without this the cost
    vanished entirely, so a long call showed no price at all.

    UNITS: ``net_credit``/``net_debit`` arrive in per-CONTRACT dollars and are
    both POSITIVE (the structure's side is carried by which one is None), while
    the source ``credit`` is already per-share. They are reconciled onto the
    per-share scale here — the one place all three units are known.
    """
    if out.get("net_cost") is not None:
        return
    credit_d, debit_d = _num(out.get("net_credit")), _num(out.get("net_debit"))
    credit_s = _num(out.get("credit"))
    if credit_d is not None:
        out["net_cost"] = round(credit_d / _CONTRACT_MULT, 6)
    elif debit_d is not None:
        out["net_cost"] = round(-abs(debit_d) / _CONTRACT_MULT, 6)
    elif credit_s is not None:
        out["net_cost"] = credit_s
    else:
        out["net_cost"] = None
