"""The console's regime block — confidence dial, diagnostic tags, the regime
share table and the 3-up callout strip.

Design: docs/design/2026-08-14-market-regime-console/README.md.

Most of the logic already existed: ``regime_mix.rank_rows`` (ranking, share bars
normalised to the leader, self-scaled sparklines, change-vs-open, the dormant
state) and ``regime_mix.lead_margin`` (which returns exactly the dial card's
LEAD / TIGHTEST TODAY pair). This module is the console's presentation of it.
"""
from pages import console as K
from pages import console_dial
from pages import regime_mix as RM
from pages.options import theme
from pages.options.theme import (CONSOLE_CARD, CONSOLE_CELL, CONSOLE_DISPLAY,
                                 CONSOLE_DIVIDER, CONSOLE_HAIRLINE, CON_TXT,
                                 CON_TXT_DIM, CON_TXT_MUTED)

_C = theme.CONSOLE_COLORS
_CARD = f"{CONSOLE_CARD} rounded-none overflow-hidden w-full"

# The share table's columns. Fixed name/spark/change cells with the bar flexing,
# and the HEAD ROW USES THE SAME GRID as the data rows — that alignment is the
# handoff's own instruction, and it is the thing that breaks first if the two
# ever drift apart.
GRID = "grid grid-cols-[190px_1fr_132px_74px] gap-[26px] items-center w-full"

SPARK_W, SPARK_H = 132, 34


def regime_hex(key, share):
    """A regime's colour, muted when it holds nothing.

    The dormant state is signalled on THREE channels — muted colour, no glow,
    and a dashed flat sparkline — because at 0.0% there is no bar length left to
    carry the meaning."""
    if K._safe(share) is not None and K._safe(share) <= 0.0:
        return _C["regime_zero"]
    return _C["regimes"].get(key, _C["muted"])


def sparkline_svg(series, color, dashed=False, width=SPARK_W, height=SPARK_H):
    """One row's intraday sparkline, scaled to its OWN range.

    A dead-flat series draws a dashed rule down the middle rather than an
    auto-scaled line — scaling a constant to its own range would amplify
    floating-point dust into a convincing squiggle. (Same rule as the ranked
    panel this replaces.)

    ``preserveAspectRatio="none"`` so the polyline fills the cell exactly, per
    the handoff.
    """
    pts = [K._safe(v) or 0.0 for v in (series or [])]
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'preserveAspectRatio="none">')
    lo, hi = (min(pts), max(pts)) if pts else (0.0, 0.0)
    if dashed or len(pts) < 2 or (hi - lo) < 1e-9:
        mid = height / 2.0
        return (head + f'<path d="M 0 {mid} L {width} {mid}" fill="none" '
                f'stroke="{color}" stroke-width="1.6" opacity="0.45" '
                f'stroke-dasharray="3 4"/></svg>')
    dx = width / (len(pts) - 1)
    coords = " ".join(
        f"{i * dx:.1f},{height - (v - lo) / (hi - lo) * height:.1f}"
        for i, v in enumerate(pts))
    return (head + f'<polyline points="{coords}" fill="none" '
            f'stroke="{color}" stroke-width="1.6" stroke-linejoin="round"/>'
            f'</svg>')


def change_text(row):
    """"+2.0pp" / "−8.4pp", or an em-dash for a band that never moved."""
    if not isinstance(row, dict):
        return "—", _C["dim"]
    if row.get("flat"):
        return "—", _C["dim"]
    change = K._safe(row.get("change")) or 0.0
    if change >= 0:
        return f"+{change * 100:.1f}pp", _C["positive"]
    return f"−{abs(change) * 100:.1f}pp", _C["negative"]


# --- 5.3 dial card ----------------------------------------------------------
def render_dial_card(regime, points):
    """REGIME IDENTIFIED — the dial plus the LEAD / TIGHTEST stat pair."""
    from nicegui import ui
    r = regime if isinstance(regime, dict) else {}
    rows = RM.rank_rows(points)
    _key, margin, tightest = RM.lead_margin(points)
    name = r.get("label") or (RM.REGIME_LABELS.get(r.get("committed_label"))
                              or "Unclear")
    runner = rows[1]["label"] if len(rows) > 1 else "—"
    with ui.column().classes(f"{_CARD} px-[24px] pt-[22px] pb-[24px] gap-4"):
        ui.label("REGIME IDENTIFIED").classes(
            f"text-[10px] tracking-[.26em] {CON_TXT_DIM}")
        ui.html(console_dial.dial_svg(
            None if r.get("unclear") else r.get("confidence"), name,
            uid="regime")).classes("w-full max-w-[244px] self-center")
        with ui.element("div").classes(
                f"grid grid-cols-2 gap-px w-full {CONSOLE_HAIRLINE} "
                f"border {CONSOLE_DIVIDER}"):
            _stat("LEAD",
                  "—" if margin is None else f"+{margin * 100:.1f} pp",
                  f"over {runner}")
            _stat("TIGHTEST TODAY",
                  "—" if tightest is None else f"{tightest * 100:.1f} pp",
                  "intraday minimum")


def _stat(label, value, note):
    from nicegui import ui
    with ui.column().classes(f"gap-1 px-3 py-[10px] {CONSOLE_CELL}"):
        ui.label(label).classes(f"text-[9.5px] tracking-[.2em] {CON_TXT_DIM}")
        ui.label(value).classes(f"text-[17px] {CON_TXT}")
        ui.label(note).classes(f"text-[10px] {CON_TXT_MUTED}")


# --- 5.4 diagnostic tags ----------------------------------------------------
def render_tags_card(evidence_detail):
    """The classifier's evidence as chips, split info/warn.

    Severity is Tier 2's (``warn`` = a choppy/crisis scorer produced the line),
    not a guess made from the wording here."""
    from nicegui import ui
    tags = [t for t in (evidence_detail or []) if isinstance(t, dict)]
    with ui.column().classes(f"{_CARD} px-[20px] pt-[18px] pb-[20px] gap-3"):
        with ui.row().classes("items-baseline justify-between w-full"):
            ui.label("DIAGNOSTIC TAGS").classes(
                f"text-[10px] tracking-[.26em] {CON_TXT_DIM}")
            ui.label(f"{len(tags)} ACTIVE").classes(
                f"text-[10px] text-[{_C['accent']}]")
        if not tags:
            ui.label("No active tags").classes(f"text-[11.5px] {CON_TXT_MUTED}")
            return
        with ui.row().classes("flex-wrap gap-2 w-full"):
            for tag in tags:
                K.mount_chip(tag.get("text"), tag.get("severity", "info"))


# --- 5.2 share table --------------------------------------------------------
def render_share_table(points):
    """One row per regime, ranked, with share bar / sparkline / change."""
    from nicegui import ui
    rows = RM.rank_rows(points) if RM.session_points(points) else []
    with ui.column().classes(f"{_CARD} px-[24px] pt-[20px] pb-2 gap-3"):
        with ui.column().classes(
                f"gap-3 w-full border-b {CONSOLE_DIVIDER} pb-[10px]"):
            with ui.row().classes("items-baseline justify-between w-full"):
                ui.label("REGIME SHARE").classes(
                    f"{CONSOLE_DISPLAY} text-[20px] font-bold tracking-[.16em] "
                    f"{CON_TXT}")
                ui.label("BY SHARE · CHANGE VS OPEN").classes(
                    f"text-[10px] tracking-[.2em] whitespace-nowrap "
                    f"{CON_TXT_DIM}")
            # Same grid as the data rows — that is what keeps the labels over
            # their columns.
            with ui.element("div").classes(GRID):
                ui.element("div")
                for text, extra in (("SHARE", ""), ("TODAY", ""),
                                    ("CHANGE", "text-right")):
                    ui.label(text).classes(
                        f"text-[10px] tracking-[.2em] {CON_TXT_DIM} {extra}")
        if not rows:
            ui.label("Waiting for regime…").classes(
                f"text-[11.5px] {CON_TXT_MUTED} py-4")
            return
        lead = rows[0]["now"]
        for row in rows:
            _share_row(row, lead)


def _share_row(row, lead):
    from nicegui import ui
    hexv = regime_hex(row["key"], row["now"])
    zero = K._safe(row["now"]) <= 0.0
    with ui.element("div").classes(
            f"{GRID} py-[15px] border-b {CONSOLE_DIVIDER}"):
        with ui.row().classes("items-center gap-3"):
            ui.element("div").classes(
                f"w-[8px] h-[26px] shrink-0 bg-[{hexv}] "
                + ("" if zero else theme.console_glow(hexv, px=10, alpha=0.45)))
            with ui.column().classes("gap-[2px] min-w-0"):
                ui.label(row["label"].upper()).classes(
                    f"{CONSOLE_DISPLAY} text-[19px] font-semibold "
                    f"tracking-[.1em] {CON_TXT}")
                ui.label(RM.regime_note(row)).classes(
                    f"text-[9.5px] tracking-[.16em] {CON_TXT_DIM}")
        with ui.row().classes("items-center gap-3 w-full"):
            ui.label(f"{row['now'] * 100:.1f}%").classes(
                f"w-[74px] shrink-0 text-[20px] font-medium text-[{hexv}]")
            with ui.element("div").classes(
                    f"flex-1 h-[12px] {K.track_classes()}"):
                if not zero and lead > 0:
                    ui.element("div").classes(
                        f"absolute left-0 top-0 bottom-0 bg-[{hexv}] "
                        f"{K.width_class(row['now'] / lead * 100)} "
                        f"{theme.console_glow(hexv, px=14, alpha=0.45)}")
        ui.html(sparkline_svg(row["series"], hexv,
                              dashed=row["flat"] or zero)).classes("w-full")
        text, chex = change_text(row)
        ui.label(text).classes(f"text-[15px] text-right text-[{chex}]")


# --- 5.5 callout strip ------------------------------------------------------
def render_callouts(points):
    """DOMINANT / BIGGEST MOVE / EMERGING — the day's regime story in three."""
    from nicegui import ui
    c = RM.callouts(points)
    items = [
        ("DOMINANT", c["dominant"], _dominant_note(points, c["dominant"])),
        ("BIGGEST MOVE", c["biggest_move"], _move_note(c["biggest_move"])),
        ("EMERGING", c["emerging"], _emerging_note(c["emerging"])),
    ]
    with ui.element("div").classes(
            f"grid grid-cols-3 gap-px w-full {CONSOLE_HAIRLINE} "
            f"border {CONSOLE_DIVIDER}"):
        for kicker, row, note in items:
            with ui.column().classes(f"gap-1 px-[18px] py-4 {CONSOLE_CELL}"):
                ui.label(kicker).classes(
                    f"text-[9.5px] tracking-[.22em] {CON_TXT_DIM}")
                ui.label((row["label"] if row else "—").upper()).classes(
                    f"{CONSOLE_DISPLAY} text-[22px] font-bold tracking-[.12em] "
                    + (f"text-[{regime_hex(row['key'], row['now'])}]"
                       if row else CON_TXT_MUTED))
                ui.label(note).classes(f"text-[11px] {CON_TXT_MUTED}")


def _dominant_note(points, row):
    if not row:
        return "no reading yet"
    _key, margin, _tight = RM.lead_margin(points)
    lead = "" if margin is None else f" · leads by {margin * 100:.1f} pp"
    return f"{row['now'] * 100:.1f}% share{lead}"


def _move_note(row):
    if not row:
        return "—"
    text, _hex = change_text(row)
    if row["change"] < 0:
        return f"{text} · share rotating out"
    return f"{text} · share building"


def _emerging_note(row):
    if not row:
        return "nothing rising"
    text, _hex = change_text(row)
    opened = (row["series"][0] if row["series"] else 0.0)
    if opened <= RM._EMERGING_FLOOR:
        return f"{text} from zero · watch"
    return f"{text} · building"
