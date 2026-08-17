"""oklch → sRGB hex, shared by the pages whose palettes are authored in oklch.

Two screens now carry supplied designs written in oklch — the Sector & Industry
heat grid (``pages/sector_heat.py``) and the Sector Rotation board
(``pages/rotation_view.py``). Both need the same conversion, and both need it at
*import* time: the Tailwind-first standard bans a per-datum arbitrary colour, so
each builds its finite class vocabulary once from a closed enumeration of oklch
stops and hands Tailwind plain ``#rrggbb`` values.

Why the designs are authored in oklch at all: it is the space in which "one step
brighter" is one step brighter to the *eye*. Both screens live at the dark end of
the range, where an sRGB interpolation bunches the low steps together.
"""
import math


def oklch_hex(lightness, chroma, hue):
    """``oklch(L C H)`` → ``#RRGGBB``, gamut-clamped.

    Out-of-gamut requests clamp per channel rather than raising or wrapping, so
    a mis-tuned palette degrades to a flat-looking colour instead of taking the
    page down — the house rule for anything on the styling path."""
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    lin = (
        4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
        -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
        -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3,
    )
    out = []
    for x in lin:
        x = min(1.0, max(0.0, x))
        x = 1.055 * x ** (1 / 2.4) - 0.055 if x > 0.0031308 else 12.92 * x
        out.append(round(min(1.0, max(0.0, x)) * 255))
    return "#%02X%02X%02X" % tuple(out)
