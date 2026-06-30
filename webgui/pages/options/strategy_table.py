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
from .theme import TXT_POS, TXT_NEG, TXT_NEUTRAL


# Credit-creditable structures the Paper-trade button is allowed for (the paper
# engine builds credit spreads; ``IC`` is the engine's internal iron-condor key,
# ``IRON_CONDOR`` is the normalized one).
_PAPER_TYPES = {"PCS", "CCS", "IC", "IRON_CONDOR"}

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
        ("debit_credit", "Debit/Credit"),
        ("max_profit", "Max P"),
        ("max_loss", "Max L"),
        ("rr", "R:R"),
        ("pop_pct", "PoP"),
        ("breakevens", "BE"),
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


def _fmt_max_profit(signal):
    """Max profit cell: '∞' when unbounded / None, else a 2dp string."""
    mp = signal.get("max_profit")
    if mp is None or signal.get("unbounded"):
        return "∞" if (signal.get("unbounded") or mp is None) else f"{mp:.2f}"
    return f"{mp:.2f}" if isinstance(mp, (int, float)) else "—"


def _fmt_2(value):
    return f"{value:.2f}" if isinstance(value, (int, float)) else "—"


def _fmt_1(value):
    return f"{value:.1f}" if isinstance(value, (int, float)) else "—"


def strategy_rows(signals):
    """Display rows for the multi-strategy table, sorted by composite score (desc).

    Robust to missing keys. Each row carries ``id`` (detail lookup), the formatted
    cells, plus ``_score_class`` / ``_bias_class`` (Tailwind ``:class`` bindings)
    and ``_allow_paper`` (gates the Paper button to credit-creditable types).
    """
    rows = []
    for s in signals or []:
        score = s.get("composite_score")
        rows.append({
            "id": s.get("id"),
            "strategy_label": s.get("strategy_label", ""),
            "bias": s.get("bias", ""),
            "legs": legs_summary(s.get("legs")),
            "debit_credit": debit_credit_text(s),
            "max_profit": _fmt_max_profit(s),
            "max_loss": _fmt_2(s.get("max_loss")),
            "rr": _fmt_2(s.get("rr")),
            "pop_pct": _fmt_1(s.get("pop_pct")),
            "breakevens": breakeven_text(s),
            "composite_score": score,
            "grade": s.get("grade", ""),
            "_score_class": scanner.score_zone_class(score),
            "_bias_class": _bias_class(s.get("bias")),
            "_allow_paper": s.get("type") in _PAPER_TYPES,
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
    fill ``credit`` (from credit, else debit) and ``breakeven`` (first breakeven) when
    absent. The input is NOT mutated."""
    out = dict(signal or {})
    if out.get("credit") is None:
        out["credit"] = out.get("net_credit")
        if out["credit"] is None:
            out["credit"] = out.get("net_debit")
    if out.get("breakeven") is None:
        bes = out.get("breakevens") or []
        out["breakeven"] = bes[0] if bes else None
    return out
