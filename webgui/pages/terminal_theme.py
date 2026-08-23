"""The Signal Desk terminal look — a Tailwind token vocabulary.

The design language for the Trade Analyzer's four screens (Overview, Evidence,
Rank board, Trade plan). Tokens are Tailwind class strings applied with
``.classes(...)``, per the repo's Tailwind-first standard — the source design
was authored in inline styles, which `test_no_inline_style.py` forbids here.

**Not config-driven, deliberately.** This is a page-scoped language with a fixed
palette, the same category as `sector_heat`'s ramps and `rotation_view`'s
quadrant hues: the numbers are chosen against each other, not knobs anyone would
turn independently. The app-wide palette in `config/theme.toml` is unaffected.

**Mono is reserved for numerics.** JetBrains Mono renders numbers and nothing
else, so any monospaced text on screen IS a number — which is what makes a dense
table scannable. Manrope carries every label and sentence. Use `MONO` only on
values.

**One bar language.** The percentile rail, the investor factors and the factor
contributions all read against a CENTRED axis, so a bar's meaning is the same
wherever it appears: distance from the middle, coloured by side.
"""

# Google Fonts is the one font host the app's CSP admits. Injected per page via
# ``ui.add_head_html``; both faces carry real fallbacks so a blocked request
# degrades to the system stack rather than to nothing.
FONT_HTML = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2'
    '?family=Manrope:wght@400;500;600;700;800'
    '&family=JetBrains+Mono:wght@400;500;700&display=swap">'
)

# ── ground + panels ─────────────────────────────────────────────────────────
PAGE = ("min-h-screen w-full text-[#e6edf7] font-[Manrope,system-ui,sans-serif] "
        "bg-[#080d17] "
        "bg-[radial-gradient(1400px_700px_at_50%_-20%,#101a2e_0%,#080d17_60%)] "
        "px-6 pt-5 pb-11")
SHELL = "w-full max-w-[1440px] mx-auto flex flex-col gap-[18px]"

PANEL = ("flex flex-col rounded-xl border border-[#1c2740] "
         "bg-[linear-gradient(180deg,#0e1626,#0b1220)] px-5 pt-[19px] pb-5")
PANEL_TIGHT = ("flex flex-col rounded-[11px] border border-[#1c2740] "
               "bg-[linear-gradient(180deg,#0e1626,#0b1220)] px-[18px] py-[13px]")

# ── type ────────────────────────────────────────────────────────────────────
MONO = "font-['JetBrains_Mono',ui-monospace,monospace]"
EYEBROW = "text-[9.5px] font-bold tracking-[0.14em] text-[#56678a] whitespace-nowrap"
EYEBROW_WIDE = "text-[10px] font-bold tracking-[0.15em] text-[#56678a] whitespace-nowrap"
PANEL_TITLE = "text-base font-bold tracking-[-0.01em] text-[#f2f6fc] whitespace-nowrap"
SCREEN_TITLE = "text-[19px] font-extrabold tracking-[-0.015em] text-[#f2f6fc]"
SUBTLE = "text-[11px] text-[#56678a]"
NOTE = "text-[11.5px] leading-[1.55] text-[#7d8db0]"
BODY = "text-[13px] leading-[1.6] text-[#a8b6cf]"
LABEL = "text-[12px] text-[#a8b6cf]"
VALUE = f"{MONO} text-[12.5px] text-[#cfdaee] whitespace-nowrap"
BIG_NUM = f"{MONO} text-[40px] font-bold leading-none tracking-[-0.02em] text-[#f2f6fc]"

# ── semantic colour, as a FINITE set ────────────────────────────────────────
# Data-driven colour maps to one of these, never to a runtime-built class: the
# repo's rule is that a dynamic colour comes from a known finite palette.
POS = "text-[#34d399]"
NEG = "text-[#f87171]"
WARN = "text-[#fbbf24]"
DIM = "text-[#7d8db0]"
OFF = "text-[#4a5b7d]"
STATE_TEXT = f"{POS} {NEG} {WARN} {DIM} {OFF}"      # for .classes(remove=…)

BAR_POS = "bg-[#34d399]"
BAR_NEG = "bg-[#f87171]"
BAR_DIM = "bg-[#4a5b7d]"
BAR_CLASSES = f"{BAR_POS} {BAR_NEG} {BAR_DIM}"

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
BTN_PRIMARY = ("rounded-[9px] px-[22px] py-3 text-[13.5px] font-bold "
               "normal-case bg-[#6366f1] text-white "
               "shadow-[0_6px_20px_rgba(99,102,241,0.32)] hover:bg-[#7c7ff5]")
BTN_GHOST = ("rounded-[9px] border border-[#2b3a57] px-[22px] py-3 "
             "text-[13.5px] font-semibold normal-case bg-transparent "
             "text-[#cfdaee] hover:border-[#4a5b7d] hover:text-[#f2f6fc]")
FILTER_ON = ("rounded-lg border border-[#3a4a72] bg-[#151f36] px-[14px] py-2 "
             "text-[12px] font-semibold normal-case text-[#e6edf7]")
FILTER_OFF = ("rounded-lg border border-[#22304c] bg-transparent px-[14px] "
              "py-2 text-[12px] font-semibold normal-case text-[#7d8db0]")

HAIRLINE = "border-b border-[#131d31]"
RULE = "border-b border-[#1c2740]"

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
