"""The minimal HTML escaper the hand-drawn SVG dials share (``console_dial``).

This module used to hold the Highcharts angular-gauge ("speedometer") builder.
Every gauge it drew was replaced — the Sentiment/Trend gauges by the Market
Regime Console, the Trade-detail gauge by the SVG score bar — and the builder
was removed; the escaper its callers shared stayed.
"""


def _esc(text):
    """Minimal HTML escape so a label can't break the markup it is spliced into."""
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))
