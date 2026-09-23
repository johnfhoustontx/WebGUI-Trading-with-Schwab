"""The paper account's scorecard vocabulary — PURE formatters.

``money`` and ``percent`` format the scorecard ``services/options_svc/book_perf``
builds, for the Paper Account page's one-line track record. They sit in their own
module for the reason ``pages/fmt.py`` (the numeric vocabulary) and
``pages/copy.py`` (the shared sentences) do: formatting a screen shows for one
condition belongs in one place.

The chips, breakdown tables and best/worst line that used to live here drew the
Claude Trades page, and went with it on 2026-09-22.
"""


def money(v):
    """Signed dollar string for a P&L cell; exactly-zero is unsigned (``$0.00``),
    and ``None`` → ``$0.00`` so a fresh-account scorecard reads cleanly."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    if v == 0:
        return "$0.00"
    return f"{'+' if v > 0 else '-'}${abs(v):,.2f}"


def percent(frac):
    """A 0..1 fraction as a 1-dp percent (``0.6667 → '66.7%'``); None/junk → '0.0%'."""
    try:
        return f"{float(frac) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"
