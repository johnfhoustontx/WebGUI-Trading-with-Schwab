"""The Signal Desk's DATA colours — a finite Tailwind palette.

What a reading on the Trade Analyzer's four screens (Overview, Evidence, Rank
Board, Trade Plan) is coloured BY: sign, state, chip and bar. Tokens are
Tailwind class strings applied with ``.classes(...)``, per the repo's
Tailwind-first standard — the source design was authored in inline styles,
which `test_no_inline_style.py` forbids here.

**Not config-driven, deliberately.** These are a fixed encoding, the same
category as `sector_heat`'s ramps and `rotation_view`'s quadrant hues: the
numbers are chosen against each other, not knobs anyone would turn
independently. The app-wide palette in `config/theme.toml` is unaffected.

**One bar language.** The percentile rail, the investor factors and the factor
contributions all read against a CENTRED axis, so a bar's meaning is the same
wherever it appears: distance from the middle, coloured by side.

⚠ **The SURFACE half retired on 2026-09-20** — ``FONT_HTML``, ``PAGE``,
``SHELL``, ``PANEL``, ``MONO``, ``EYEBROW``, ``PANEL_TITLE``, ``SCREEN_TITLE``,
``SUBTLE``, ``NOTE``, ``BODY``, ``LABEL``, ``VALUE``, ``HAIRLINE``, ``RULE``,
``BTN_PRIMARY``, ``BTN_GHOST`` and ``TOOLTIP``. Between them they were a second
ground, a second neutral ladder, a second type face, a second panel and a
second button set, which is what made these four screens the app's seventh
visual family. Their colours are the app's now (``pages.options.theme``); the
type GEOMETRY they also carried lives on ``trade_shell``, which is where this
family's other shared widget vocabulary already was. ``MONO`` was replaced by
nothing: the app sets ``font-variant-numeric: tabular-nums`` on ``body``, so
the columns it marked align on tabular figures rather than on a monospaced
face — and its JetBrains Mono stopped loading with ``FONT_HTML``, so every
numeric it marked had been falling back to the system mono anyway.
"""

# ── semantic colour, as a FINITE set ────────────────────────────────────────
# Data-driven colour maps to one of these, never to a runtime-built class: the
# repo's rule is that a dynamic colour comes from a known finite palette.
POS = "text-[#34d399]"
NEG = "text-[#f87171]"
WARN = "text-[#fbbf24]"
DIM = "text-[#7d8db0]"
# "No reading" — deliberately the dimmest thing on the screen, and measured at
# 2.7:1 on the app's card. It is a DATA colour (absence), not a muted label, so
# it is not the neutral ladder's bottom rung and does not take the app's.
OFF = "text-[#4a5b7d]"
STATE_TEXT = f"{POS} {NEG} {WARN} {DIM} {OFF}"      # for .classes(remove=…)

BAR_POS = "bg-[#34d399]"
BAR_NEG = "bg-[#f87171]"
# The Trade Plan's two card accents — a plan and a refusal — were the POS and
# WARN hexes written out beside these. A bar is a bar; the set is finite.
BAR_WARN = "bg-[#fbbf24]"
BAR_DIM = "bg-[#4a5b7d]"

# Chips: (border, background, text) as one class string per state.
CHIP_POS = "border-[#1f6b52] bg-[rgba(52,211,153,0.08)] text-[#34d399]"
CHIP_WARN = "border-[#4a3c17] bg-[rgba(251,191,36,0.08)] text-[#fbbf24]"
CHIP_NEG = "border-[#5b2733] bg-[rgba(248,113,113,0.08)] text-[#f87171]"
CHIP_OFF = "border-[#263353] bg-[rgba(15,23,40,0.6)] text-[#8b9bb4]"
CHIP_BASE = ("inline-flex items-center gap-2 rounded-lg border px-[14px] py-2 "
             "text-[11.5px] font-bold tracking-[0.09em] whitespace-nowrap")

CALLOUT = ("flex gap-[11px] rounded-[10px] border border-[#4a3c17] "
           "bg-[rgba(251,191,36,0.07)] px-[14px] py-3")
CALLOUT_TEXT = "text-[12px] leading-[1.6] text-[#cbb98a]"

# ── controls ────────────────────────────────────────────────────────────────
# The Rank Board's Hide gated toggle, and the one control on these four screens
# that ``kit.button`` cannot express: it carries a SELECTED STATE and a label
# that changes with it ("Hide gated" ⇄ "Showing ungated only"), which none of
# the kit's four kinds encode. It is the guard's one permanent Phase 5 entry.
FILTER_ON = ("rounded-lg border border-[#3a4a72] bg-[#151f36] px-[14px] py-2 "
             "text-[12px] font-semibold normal-case text-[#e6edf7]")
FILTER_OFF = ("rounded-lg border border-[#22304c] bg-transparent px-[14px] "
              "py-2 text-[12px] font-semibold normal-case text-[#7d8db0]")

# Every table sits in this wrapper over a min-width grid, so columns scroll
# rather than collide or clip — the design's rule, and the reason a 9-column
# rank table survives a narrow window.
SCROLL_X = "w-full overflow-x-auto min-w-0"


def sign_text(v, zero=DIM):
    """Semantic text class for a signed number. A finite map, never computed."""
    if v is None:
        return OFF
    return POS if v > 0 else NEG if v < 0 else zero


def sign_bar(v):
    return BAR_POS if (v or 0) >= 0 else BAR_NEG


def centred(value, half_range):
    """``(left_pct, width_pct)`` for a bar drawn from a centre axis.

    The shared bar language: width is |value| against the scale's half-range,
    and the bar grows right from the middle for positive, left for negative.
    Returned as percentages so the caller can put them in a style-free
    ``flex``/``width`` arbitrary class."""
    try:
        v = float(value)
        half = float(half_range) or 1.0
    except (TypeError, ValueError):
        return 50.0, 0.0
    w = min(abs(v) / half, 1.0) * 50.0
    return (50.0 if v >= 0 else 50.0 - w), w
