"""Display builders for the Market Scanner's 0-DTE and Swing tables (PURE).

The 0-DTE and Swing scans build more than credit spreads: debit spreads,
straddles and strangles, butterflies and condors, calendars and diagonals. The
options service publishes those in two lists of their own, ``structures_0dte``
and ``structures_swing``, in the NORMALIZED candidate shape the Directional tab
already renders - a ``legs`` list, per-contract dollars, a Fit + Quality score.
The credit spreads stay in ``signals_0dte`` / ``signals_swing``, in the scanner's
own shape (strike fields, per-share dollars): those lists are what the paper
Account enters from, and nothing here changes them.

Each tab shows BOTH of its lists in ONE table (the owner's decision,
2026-10-07; until then they sat in two tables behind a switch). So this module
turns a credit spread into a row of the shared columns (``credit_rows``),
gives every row its build family for the checkboxes, and merges a tab's lists
into one ranking (``merged``).

⚠ The two kinds are scored on DIFFERENT SCALES - a credit spread as a trade
that sells premium, everything else on fit and quality - and the single ranking
compares them anyway, by choice. The page therefore says so: ``SCALE_NOTE``
under the checkboxes, and a hover on every score naming its scale
(``_score_tip``).

No ``ui.`` call lives here; ``webgui/tests/test_scanner_structures.py`` and
``test_scanner_combined.py`` cover every function.
"""
from .. import fmt as _fmt
from . import scanner, strategy_table

# The credit spreads' own family. Not a service ``group``: the service publishes
# them in lists of their own, and this is what the page files them under.
CREDIT_GROUP = "CREDIT"

# Build family -> the reader's words, in checkbox order. Past the first, the
# keys are the service's ``group`` values (options-scanner ``structure_scan``;
# RATIO arrives with the backspread builder).
GROUPS = ((CREDIT_GROUP, "Credit spreads"),
          ("VERTICAL", "Debit spreads"),
          ("STRADDLE", "Straddles and strangles"),
          ("BUTTERFLY", "Butterflies and condors"),
          ("CALENDAR", "Calendars"),
          ("RATIO", "Ratio spreads"))
_LABEL = dict(GROUPS)

# Which day lists sit on which tab, in merge order (a tie in score keeps the
# first list's row first).
TAB_LISTS = {"0-DTE": ("signals_0dte", "structures_0dte"),
             "Swing": ("signals_swing", "structures_swing"),
             "Directional": ("signals_directional",)}
STRUCTURE_LISTS = ("structures_0dte", "structures_swing")
CREDIT_LISTS = ("signals_0dte", "signals_swing")
# The tabs that hold more than one family, and so carry the family checkboxes.
FILTERED_TABS = ("0-DTE", "Swing")

# Under the checkboxes: the one thing a shared Score column would otherwise hide.
SCALE_NOTE = ("Credit spreads are scored as trades that sell premium; every "
              "other structure is scored on how well it fits the market view "
              "and how sound it is. The two scores are on different scales, so "
              "a credit spread's score is not comparable with another "
              "structure's, though the table ranks them together.")
# And on each score, which of the two it is.
SCORE_TIP_CREDIT = "Premium score: how good a trade this is at selling premium."
SCORE_TIP_OTHER = ("Fit and quality score: how well this structure fits the "
                   "market view, and how sound it is.")

# The engine's own names for the three (``strategy_scanner.adapt_credit_spread``
# / ``adapt_iron_condor``, which is how one reaches the Strategy Finder), so a
# trade does not read as two things on two pages. A test reads the engine's
# source for them.
_CREDIT_LABEL = {"PCS": "Put Credit Spread", "CCS": "Call Credit Spread",
                 "IC": "Iron Condor"}
_CREDIT_BIAS = {"PCS": "bullish", "CCS": "bearish", "IC": "neutral"}
_DASH = "—"
_CONTRACT = 100            # shares a contract covers: a credit row's dollars are per share


def _earnings_text(sig):
    """``"Earnings 11/05"`` for a row the scan KEPT through a report, else ``""``.

    Keyed on ``spans_earnings`` alone. ``earnings_date`` by itself is the
    checklist's stamp and sits on every row of a symbol with a scheduled report,
    whether or not this trade would be open through it.
    """
    if sig.get("spans_earnings") is not True:
        return ""
    when = sig.get("earnings_date")
    return f"Earnings {scanner._short_exp(when)}" if when else "Earnings"


def structure_rows(signals):
    """Display rows for a tab's other structures, best score first."""
    rows = scanner.directional_rows(signals)
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for row in rows:
        sig = by_id.get(row.get("id")) or {}
        group = str(sig.get("group") or "").strip().upper()
        row["type"] = sig.get("type", "")
        row["_group"] = group
        row["group_label"] = _LABEL.get(group, "")
        row["_earnings"] = _earnings_text(sig)
        row["_score_tip"] = SCORE_TIP_OTHER
    return rows


def _credit_legs(sig, kind):
    """The spread's legs from its strike fields, the SHORT leg of each wing
    first - the order ``strategy_table.legs_summary`` prints. A leg with no
    strike is left out; a put wing with only one strike is not half-drawn."""
    exp = sig.get("expiration")
    wings = {"PCS": (("put", "short_strike", "long_strike"),),
             "CCS": (("call", "short_strike", "long_strike"),),
             "IC": (("put", "short_strike", "long_strike"),
                    ("call", "call_short", "call_long"))}.get(kind, ())
    legs = []
    for right, short_key, long_key in wings:
        short, long_ = _fmt.num(sig.get(short_key)), _fmt.num(sig.get(long_key))
        if short is None or long_ is None:
            continue
        legs.append({"kind": right, "side": "short", "strike": short,
                     "expiration": exp, "qty": 1})
        legs.append({"kind": right, "side": "long", "strike": long_,
                     "expiration": exp, "qty": 1})
    return legs


def _credit_breakevens(raw):
    """``"99.40"``, or ``"98.90 / 121.10"`` for an iron condor's
    ``"put_be/call_be"`` string. A dash for anything unreadable."""
    if isinstance(raw, str):
        values = [_fmt.num(part.strip()) for part in raw.split("/")]
    else:
        values = [_fmt.num(raw)]
    if not values or any(v is None for v in values):
        return _DASH
    return " / ".join(f"{v:.2f}" for v in values)


def _dollars(per_share):
    return _DASH if per_share is None else f"{per_share * _CONTRACT:.2f}"


def credit_rows(signals):
    """Display rows for a tab's CREDIT spreads, in the shared columns, best
    score first.

    The scanner publishes a credit spread per SHARE with its strikes in fields;
    every other row of the table is per CONTRACT with a ``legs`` list. So the
    dollars are multiplied up (0.60 a share is ``+60.00 credit``), the reward to
    risk becomes the ratio the R:R column holds rather than a percentage, and
    the strikes are drawn as legs. A figure the signal lacks is a dash, never a
    zero.

    ``_allow_paper`` starts open: ``scanner.stamp_stale`` runs next and only
    ever narrows it."""
    rows = []
    for s in signals or []:
        kind = str(s.get("type") or "").strip().upper()
        credit, loss = _fmt.num(s.get("credit")), _fmt.num(s.get("max_loss"))
        score, grade = s.get("composite_score"), s.get("grade", "")
        bias = _CREDIT_BIAS.get(kind, "")
        rows.append({
            "id": s.get("id"),
            "symbol": s.get("symbol", ""),
            "type": kind,
            "strategy_label": _CREDIT_LABEL.get(kind, kind),
            "bias": bias,
            "legs": strategy_table.legs_summary(_credit_legs(s, kind)),
            "expiration": scanner._short_exp(s.get("expiration")),
            "dte": s.get("dte"),
            "debit_credit": (_DASH if credit is None
                             else f"+{credit * _CONTRACT:.2f} credit"),
            "max_profit": _dollars(credit),
            "max_loss": _dollars(loss),
            "rr": (f"{credit / loss:.2f}"
                   if credit is not None and loss is not None and loss > 0 else _DASH),
            "pop_pct": strategy_table._fmt_2(_fmt.num(s.get("pop_pct"))),
            "breakevens": _credit_breakevens(s.get("breakeven")),
            "iv_rank": scanner.iv_rank_value(s.get("iv_rank")),
            "composite_score": score,
            "grade": grade,
            "grade_reason": "",
            "_score_class": scanner.score_zone_class(score),
            "_bias_class": strategy_table._bias_class(bias),
            "_grade_class": strategy_table.grade_class(grade),
            "_allow_paper": True,
            "_undefined_risk": False,
            "_group": CREDIT_GROUP,
            "group_label": _LABEL[CREDIT_GROUP],
            "_earnings": "",
            "_score_tip": SCORE_TIP_CREDIT,
        })
    rows.sort(key=_rank, reverse=True)
    return rows


def _rank(row):
    score = row.get("composite_score")
    return (score is not None, score or 0)


def merged(*row_lists):
    """One ranking over several row lists, best score first. The SAME row
    dicts, not copies: the lifecycle and checklist stamps are written per list
    and have to show in the table. Stable, so a tie keeps the earlier list's
    row first and the order does not flip between repaints."""
    rows = [row for rows_ in row_lists for row in (rows_ or [])]
    rows.sort(key=_rank, reverse=True)
    return rows


def all_groups():
    """Every family switched on - the checkboxes' starting state."""
    return {group for group, _label in GROUPS}


def toggled(on, group):
    """``on`` with ``group`` flipped, as a NEW set."""
    return set(on) ^ {group}


def filter_groups(rows, on):
    """The rows whose family checkbox is on. A row whose family has no checkbox
    is always kept: there would be nothing to turn it back on with."""
    return [r for r in rows if r.get("_group") in on or r.get("_group") not in _LABEL]


def chips(rows):
    """One entry per family that HAS rows, in :data:`GROUPS` order."""
    counts = {}
    for row in rows:
        counts[row.get("_group")] = counts.get(row.get("_group"), 0) + 1
    return [{"group": group, "label": label, "count": counts[group],
             "text": f"{label} · {counts[group]}"}
            for group, label in GROUPS if counts.get(group)]
