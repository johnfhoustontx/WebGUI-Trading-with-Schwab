"""Assembly of the Market Regime Console — header, the three cards, the regime
block, and the footer.

Design: docs/design/2026-08-14-market-regime-console/README.md.

The console is the TOP of ``/sentiment``; everything the page had before (the
intraday graphs, the components popup, the status bar, Refresh) survives below
it untouched. ``render()`` returns one container that ``apply()`` rebuilds — see
``console_cards`` for why a rebuild rather than element-level updates.

Width: the handoff is a fixed 1440px canvas, which this app has no room for
(icon rail + tab strip + ticker). The proportions are kept and capped instead,
so it fills a wide monitor without clipping a narrow window.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from pages import console_cards as CARDS
from pages import console_regime as REG
from pages import regime_mix as RM
from pages.options import theme
from pages.options.theme import (CONSOLE_DISPLAY, CONSOLE_DIVIDER, CONSOLE_PAGE,
                                 CONSOLE_RULE, CON_TXT, CON_TXT_DIM,
                                 CON_TXT_FAINT, CON_TXT_MUTED)

_C = theme.CONSOLE_COLORS
CT = ZoneInfo("America/Chicago")

# Fluid to the handoff's canvas width, then capped (decision recorded in the
# plan). Everything inside is proportional, so this is the only fixed number.
SHELL = f"{CONSOLE_PAGE} w-full max-w-[1440px] p-[28px] gap-[22px]"

# Past this the "DATA AS OF" chip flips to STALE. The composite publishes every
# 120 s, so ~3 missed cycles is a real stall rather than a slow tick.
STALE_AFTER_SEC = 420


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


def as_of_parts(composite_at, now=None):
    """``(text, stale)`` for the DATA AS OF chip.

    Stale is worth surfacing because every number on this page comes from one
    cache: if it stops publishing, the console keeps rendering confidently from
    the last snapshot, and nothing else on screen would say so."""
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = _parse_iso(composite_at)
    if stamp is None:
        return "NO DATA", True
    age = (now - stamp).total_seconds()
    local = stamp.astimezone(CT)
    return (f"{local:%H:%M} CT · {'STALE' if age > STALE_AFTER_SEC else 'LIVE'}",
            age > STALE_AFTER_SEC)


def _parse_iso(value):
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


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
        _header(ctx)
        # 1fr 1fr 1.05fr, stretched so the three cards share a baseline.
        with ui.element("div").classes(
                "grid grid-cols-[1fr_1fr_1.05fr] gap-5 w-full items-stretch"):
            CARDS.render_sentiment_card(
                ctx.get("sent_arcs"), ctx.get("bias"), ctx.get("total"),
                ctx.get("confidence"))
            CARDS.render_trend_card(
                ctx.get("trend_arcs"), ctx.get("trend_short"),
                ctx.get("trend_verdict"), ctx.get("trend_guidance"))
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


def _header(ctx):
    from nicegui import ui
    text, stale = as_of_parts(ctx.get("as_of"))
    with ui.row().classes(
            f"items-end justify-between w-full border-b {CONSOLE_RULE} pb-4"):
        with ui.column().classes("gap-1"):
            with ui.row().classes("items-center gap-3"):
                ui.element("div").classes(
                    f"w-[10px] h-[10px] rounded-full bg-[{_C['accent']}] "
                    f"con-pulse {theme.console_glow(_C['accent'], px=12, alpha=0.9)}")
                ui.label("MARKET REGIME CONSOLE").classes(
                    f"{CONSOLE_DISPLAY} text-[30px] font-bold tracking-[.16em] "
                    f"{CON_TXT}")
            # pl-[24px] aligns the subtitle under the title, clearing the dot.
            ui.label("SENTIMENT · TREND · SIGNALS · REGIME SHARE").classes(
                f"pl-[24px] text-[11.5px] tracking-[.24em] {CON_TXT_MUTED}")
        with ui.row().classes("items-center gap-[10px]"):
            _chip("SESSION", session_label(), accent=False)
            _chip("DATA AS OF", text, accent=not stale, warn=stale)


def _chip(label, value, accent=True, warn=False):
    from nicegui import ui
    hexv = _C["warning"] if warn else (_C["accent"] if accent else _C["line"])
    with ui.column().classes(
            f"gap-[2px] px-[14px] py-[9px] border border-[{hexv}]/[0.35] "
            f"bg-[{theme._alpha_hex(hexv, 0.08)}]"):
        ui.label(label).classes(
            f"text-[9.5px] tracking-[.22em] "
            + (f"text-[{hexv}]" if (accent or warn) else CON_TXT_DIM))
        ui.label(value).classes(
            f"text-[13px] " + (f"text-[{hexv}]" if (accent or warn) else CON_TXT))


def _footer(points):
    from nicegui import ui
    with ui.row().classes(
            f"items-center justify-between w-full border-t {CONSOLE_RULE} "
            f"pt-[14px] gap-4"):
        ui.label(footer_summary(points)).classes(
            f"text-[10.5px] tracking-[.2em] {CON_TXT_DIM}")
        ui.label("FOR INFORMATIONAL PURPOSES ONLY · NOT FINANCIAL ADVICE").classes(
            f"text-[10.5px] tracking-[.2em] {CON_TXT_FAINT}")
