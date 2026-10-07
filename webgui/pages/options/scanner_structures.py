"""Display builders for the Market Scanner's "Other structures" tables (PURE).

The 0-DTE and Swing scans build more than credit spreads: debit spreads,
straddles and strangles, butterflies and condors, calendars and diagonals. The
options service publishes them in two lists of their own, ``structures_0dte`` and
``structures_swing``, in the NORMALIZED candidate shape the Directional tab
already renders - a ``legs`` list, per-contract dollars, a Fit + Quality score.

They are shown in their own table on each tab and never mixed into the credit
table: the two are scored on different scales and are not comparable. So the
rows here are ``scanner.directional_rows`` plus three things that tab has no use
for: the build family (for the filter chips), the raw structure type, and a note
on a row kept through an earnings report.

No ``ui.`` call lives here; ``webgui/tests/test_scanner_structures.py`` covers
every function.
"""
from . import scanner

# Build family -> the reader's words, in chip order. The keys are the service's
# ``group`` values (options-scanner ``structure_scan``; RATIO arrives with the
# backspread builder).
GROUPS = (("VERTICAL", "Debit spreads"),
          ("STRADDLE", "Straddles and strangles"),
          ("BUTTERFLY", "Butterflies and condors"),
          ("CALENDAR", "Calendars"),
          ("RATIO", "Ratio spreads"))
_LABEL = dict(GROUPS)

# Which day lists sit on which tab. A tab's header counts every list on it.
TAB_LISTS = {"0-DTE": ("signals_0dte", "structures_0dte"),
             "Swing": ("signals_swing", "structures_swing"),
             "Directional": ("signals_directional",)}
STRUCTURE_LISTS = ("structures_0dte", "structures_swing")

VIEW_CREDIT, VIEW_OTHER = "credit", "other"
_VIEW_WORDS = {VIEW_CREDIT: "Credit spreads", VIEW_OTHER: "Other structures"}

# The switch's hover text. It has to say the one thing the two tables' shared
# Score column would otherwise hide.
VIEW_TIP = ("Credit spreads are scored as trades that sell premium. Other "
            "structures - debit spreads, straddles and strangles, butterflies "
            "and condors, calendars - are scored on how well they fit the "
            "market view and how sound they are. The two scores are on "
            "different scales, so the tables are kept apart and a score in one "
            "is not comparable with a score in the other.")


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
    """Display rows for an Other-structures table, best score first."""
    rows = scanner.directional_rows(signals)
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for row in rows:
        sig = by_id.get(row.get("id")) or {}
        group = str(sig.get("group") or "").strip().upper()
        row["type"] = sig.get("type", "")
        row["_group"] = group
        row["group_label"] = _LABEL.get(group, "")
        row["_earnings"] = _earnings_text(sig)
    return rows


def all_groups():
    """Every family switched on - the chips' starting state."""
    return {group for group, _label in GROUPS}


def toggled(on, group):
    """``on`` with ``group`` flipped, as a NEW set."""
    return set(on) ^ {group}


def filter_groups(rows, on):
    """The rows whose family chip is on. A row whose family has no chip is
    always kept: there would be nothing to turn it back on with."""
    return [r for r in rows if r.get("_group") in on or r.get("_group") not in _LABEL]


def chips(rows):
    """One chip per family that HAS rows, in :data:`GROUPS` order."""
    counts = {}
    for row in rows:
        counts[row.get("_group")] = counts.get(row.get("_group"), 0) + 1
    return [{"group": group, "label": label, "count": counts[group],
             "text": f"{label} · {counts[group]}"}
            for group, label in GROUPS if counts.get(group)]


def tab_totals(full, shown):
    """``{tab: (rows painted, rows shown)}``, each summed over the tab's lists."""
    return {tab: (sum(len(full.get(k) or ()) for k in keys),
                  sum(len(shown.get(k) or ()) for k in keys))
            for tab, keys in TAB_LISTS.items()}


def view_options(credit_n, other_n, *, have):
    """The two-way switch's labels, with each table's row count once today's day
    union exists. Before it does there is no count: a 0 there was never read."""
    if not have:
        return dict(_VIEW_WORDS)
    return {VIEW_CREDIT: f"{_VIEW_WORDS[VIEW_CREDIT]} · {credit_n}",
            VIEW_OTHER: f"{_VIEW_WORDS[VIEW_OTHER]} · {other_n}"}
