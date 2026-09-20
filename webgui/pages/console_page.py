"""Assembly of the Market Regime Console — the three cards, the regime block,
and the footer.

Design: docs/design/2026-08-14-market-regime-console/README.md.

The console is the TOP of ``/sentiment``; everything the page had before (the
intraday graphs, the components popup, the status bar, Refresh) survives below
it untouched. ``render()`` returns one container that ``apply()`` rebuilds — see
``console_cards`` for why a rebuild rather than element-level updates.

Width: the handoff is a fixed 1440px canvas, which this app has no room for
(icon rail + tab strip + ticker). The proportions are kept and capped instead,
so it fills a wide monitor without clipping a narrow window.

⚠ **The console has no header of its own since 2026-09-19** (the consistency
standard, ``docs/plans/2026-09-19-app-ui-consistency-design.md``). It used to
draw a ``MARKET REGIME CONSOLE`` title, a descriptor eyebrow, a pulsing accent
dot and a SESSION / DATA AS OF chip pair; the page kit's header line carries the
name and the Updated stamp for every screen in the app, so all of it went.
:func:`session_label` survived the cut because it says the one thing a stamp
cannot — which session the market is in — and ``sentiment.py`` now shows it on
the page's status line. ``as_of_parts`` did NOT: it was a SECOND freshness rule,
with a 420 s threshold of its own, aging the same view differently from the nav
badge's ``alerts.stale_after``.

The page's own SURFACE went with it: ``SHELL`` no longer carries
``CONSOLE_PAGE`` or the page padding, because the console now sits inside
``kit.page()`` like every other screen. The cards, the dial, the share table and
the callouts are untouched — they are the data.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from pages import console_cards as CARDS
from pages import console_regime as REG
from pages import regime_mix as RM
from pages.options.theme import CONSOLE_RULE, CON_TXT_DIM, CON_TXT_FAINT

CT = ZoneInfo("America/Chicago")

# Fluid to the handoff's canvas width, then capped (decision recorded in the
# plan). Everything inside is proportional, so this is the only fixed number.
SHELL = "w-full max-w-[1440px] gap-[22px]"


def session_label(now=None):
    """"US EQUITIES · RTH" / "· EXT" / "· CLOSED", from the shared calendar."""
    try:
        from shared import market_calendar as cal
        now = now or dt.datetime.now(CT)
        if not cal.is_trading_day(now.date()):
            return "US EQUITIES · CLOSED"
        if cal.is_regular_hours(now):
            return "US EQUITIES · RTH"
        if cal.is_extended_hours(now):
            return "US EQUITIES · EXT"
    except Exception:  # noqa: BLE001 — a chip must never break the page.
        return "US EQUITIES"
    return "US EQUITIES · CLOSED"


def footer_summary(points):
    """The one-line reading under the console — leader, margin, and any band
    sitting dormant."""
    rows = RM.rank_rows(points) if RM.session_points(points) else []
    if not rows:
        return "Waiting for the regime classifier"
    _key, margin, tightest = RM.lead_margin(points)
    parts = []
    if len(rows) > 1 and margin is not None:
        parts.append(f"{rows[0]['label']} leads {rows[1]['label']} "
                     f"by {margin * 100:.1f} pp")
    if tightest is not None:
        parts.append(f"tightest spread today {tightest * 100:.1f} pp")
    dormant = [r["label"].lower() for r in rows if r["now"] <= 0.0]
    if dormant:
        parts.append(f"{', '.join(dormant)} dormant")
    return " · ".join(parts)


# --- render -----------------------------------------------------------------
def render():
    """Build the console shell and return the container ``apply`` repaints."""
    from nicegui import ui
    return ui.column().classes(SHELL)


def apply(container, ctx):
    """Repaint the console from ``ctx`` (see ``sentiment._apply``)."""
    from nicegui import ui
    ctx = ctx or {}
    container.clear()
    with container:
        # No header: the page kit's header line carries the name and the
        # Updated stamp (see the module docstring).
        # 1fr 1fr 1.05fr, stretched so the three cards share a baseline.
        with ui.element("div").classes(
                "grid grid-cols-[1fr_1fr_1.05fr] gap-5 w-full items-stretch"):
            CARDS.render_sentiment_card(
                ctx.get("sent_arcs"), ctx.get("bias"), ctx.get("total"),
                ctx.get("confidence"), ctx.get("bias_picture"))
            CARDS.render_trend_card(
                ctx.get("trend_arcs"), ctx.get("trend_short"),
                ctx.get("trend_verdict"), ctx.get("trend_guidance"),
                ctx.get("trend_picture"))
            CARDS.render_signals_card(
                ctx.get("signal_rows"), ctx.get("velocity_values"),
                ctx.get("divergence_detail"))
        points = ctx.get("regime_points")
        with ui.element("div").classes(
                "grid grid-cols-[minmax(340px,396px)_1fr] gap-[22px] w-full "
                "items-start"):
            with ui.column().classes("gap-[22px] w-full"):
                REG.render_dial_card(ctx.get("regime"), points)
                REG.render_tags_card(_evidence(ctx.get("regime")))
            with ui.column().classes("gap-[22px] w-full"):
                REG.render_share_table(points)
                REG.render_callouts(points)
        _footer(points)


def _evidence(regime):
    """Tier 2's structured evidence, with a graceful fall back to the flat list
    for a payload published before ``evidence_detail`` existed (the regime view
    is RTH-gated, so a stale overnight snapshot really can lack it)."""
    r = regime if isinstance(regime, dict) else {}
    detail = r.get("evidence_detail")
    if isinstance(detail, list) and detail:
        return detail
    return [{"text": str(t), "regime": "", "severity": "info"}
            for t in (r.get("evidence") or [])]


def _footer(points):
    from nicegui import ui
    with ui.row().classes(
            f"items-center justify-between w-full border-t {CONSOLE_RULE} "
            f"pt-[14px] gap-4"):
        ui.label(footer_summary(points)).classes(
            f"text-[10.5px] tracking-[.2em] {CON_TXT_DIM}")
        ui.label("FOR INFORMATIONAL PURPOSES ONLY · NOT FINANCIAL ADVICE").classes(
            f"text-[10.5px] tracking-[.2em] {CON_TXT_FAINT}")
