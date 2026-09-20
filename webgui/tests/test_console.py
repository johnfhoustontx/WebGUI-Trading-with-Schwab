"""Tests for the Market Regime Console primitives (pages/console.py, console_dial.py)."""
import re

import pytest

from pages import console as C
from pages import console_dial as D


# ------------------------------------------------------------- score bands
@pytest.mark.parametrize("value,band", [
    (83, "positive"), (73, "positive"), (68, "yellow"), (60, "olive"),
    (53, "warning"),
])
def test_bands_reproduce_every_reading_in_the_handoff(value, band):
    """The thresholds are fitted to the design's own five readings; if a change
    breaks one of these, the page no longer matches the source of truth."""
    assert C.score_band(value) == band


def test_low_scores_go_red_not_amber():
    """An extrapolation beyond the handoff (its lowest sample is 53): a bearish
    20 must not read as the same 'caution' as a middling 53."""
    assert C.score_band(20) == "negative"
    assert C.score_band(34.9) == "negative" and C.score_band(35) == "warning"


@pytest.mark.parametrize("junk", [None, "x", {}, float("nan"), float("inf"), True])
def test_band_of_junk_is_muted(junk):
    assert C.score_band(junk) == "muted"


def test_band_hex_covers_every_band_key():
    for key in ("positive", "yellow", "olive", "warning", "negative", "muted"):
        assert re.fullmatch(r"#[0-9a-f]{6}", C.band_hex(key)), key


# ------------------------------------------------------------ timeframe meter
def test_meter_row_carries_geometry_and_classes():
    row = C.meter_row("DAY", 73)
    assert row["no_read"] is False and row["pct"] == 73 and row["text"] == "73"
    assert row["band"] == "positive"
    assert "linear-gradient(90deg," in row["fill"]
    assert row["glow"].startswith("shadow-[")


def test_a_missing_horizon_is_no_read_never_a_zero():
    """The honesty rule the rings already follow — a fabricated neutral is worse
    than an admitted gap."""
    row = C.meter_row("WEEK", None)
    assert row["no_read"] is True
    assert row["text"] == "—" and row["pct"] == 0.0
    assert row["fill"] == "" and row["glow"] == ""


def test_meter_clamps_out_of_range_scores():
    assert C.meter_row("X", 140)["pct"] == 100.0
    assert C.meter_row("X", -20)["pct"] == 0.0


def test_only_a_strong_reading_glows():
    """The handoff glows the day meter and leaves week/month flat, so the eye
    lands on the live number."""
    assert C.meter_row("DAY", 73)["glow"]
    assert C.meter_row("WEEK", 60)["glow"] == ""


def test_marker_is_derived_from_the_series_colour():
    """Derived, so a new band colour cannot arrive without its marker."""
    m = C.marker_hex("#35d68a")
    assert re.fullmatch(r"#[0-9a-f]{6}", m)
    assert m != "#35d68a" and m > "#9"          # a near-white tint
    assert C.marker_hex("garbage") == "#ffffff"


def test_width_and_left_classes_are_bounded_and_spaceless():
    for v in (-10, 0, 33.333, 100, 250, None, "x"):
        for cls in (C.width_class(v), C.left_class(v)):
            assert " " not in cls
            pct = float(re.search(r"\[([\d.]+)%\]", cls).group(1))
            assert 0.0 <= pct <= 100.0


# --------------------------------------------------------- segmented meter
@pytest.mark.parametrize("conf,lit", [(0.7, 7), (0.0, 0), (1.0, 10), (0.44, 4),
                                      (0.45, 5), (None, 0), (2.0, 10), (-1, 0)])
def test_segmented_cells_count(conf, lit):
    cells = C.segmented_cells(conf)
    assert len(cells) == C.SEGMENTS
    assert sum(cells) == lit
    assert cells == sorted(cells, reverse=True)   # lit cells lead


def test_segmented_rounds_halves_up_consistently():
    """Python's round() goes to EVEN, so round(4.5)==4 while round(5.5)==6 — a
    meter whose midpoint flips on parity. Both halves must round the same way."""
    assert sum(C.segmented_cells(0.45)) == 5
    assert sum(C.segmented_cells(0.55)) == 6


# ---------------------------------------------------------- bipolar meter
@pytest.mark.parametrize("value,pct", [(1.63, 32.6), (0.48, 9.6), (1.11, 22.2)])
def test_bipolar_widths_match_the_handoffs_rendering(value, pct):
    """The handoff states BOTH a scale ("0 -> +5 for ROC") and the widths, and
    they DISAGREE — at scale 5 the ROC values render 16.3% and 4.8%, not the
    stated 32% and 10%. All three stated widths are self-consistent at 2.5, and
    the README calls the rendering the visual source of truth."""
    assert C.bipolar_geometry(value)["pct"] == pytest.approx(pct, abs=0.05)


def test_bipolar_side_and_text():
    pos, neg = C.bipolar_geometry(1.2), C.bipolar_geometry(-1.2)
    assert pos["side"] == "right" and neg["side"] == "left"
    assert pos["text"] == "+1.20" and neg["text"] == "-1.20"
    assert pos["hex"] != neg["hex"]
    assert pos["pct"] == neg["pct"]              # magnitude mirrors


def test_bipolar_never_exceeds_half_the_track():
    assert C.bipolar_geometry(99)["pct"] == 50.0
    assert C.bipolar_geometry(-99)["pct"] == 50.0


def test_bipolar_no_data_is_a_dash_not_a_zero():
    """Zero is a real position on a signed scale, so it must not stand in for
    'not enough history' — which is exactly what Tier 2 sends as None."""
    g = C.bipolar_geometry(None)
    assert g["side"] == "none" and g["text"] == "—" and g["pct"] == 0.0
    assert C.bipolar_geometry(0.0)["side"] == "right"   # a real zero still reads


def test_bipolar_survives_a_zero_or_junk_scale():
    assert C.bipolar_geometry(1.0, scale=0)["pct"] > 0
    assert C.bipolar_geometry(1.0, scale=None)["pct"] > 0


# ------------------------------------------------------------------- chips
@pytest.mark.parametrize("text,label,value", [
    ("Balanced profile 0.53", "BALANCED PROFILE", "0.53"),
    ("Band-hug 50%", "BAND-HUG", "50%"),
    ("3 failed OR breaks", "FAILED OR BREAKS", "3"),
    ("11 EMA whipsaws", "EMA WHIPSAWS", "11"),
    ("EMA flat", "EMA FLAT", ""),
    ("", "", ""),
])
def test_split_tag_lifts_the_number_from_either_end(text, label, value):
    """The classifier puts the number at either end ("3 failed OR breaks" vs
    "Band-hug 50%"), and the design colours it separately."""
    assert C.split_tag(text) == (label, value)


def test_chip_severity_changes_the_colour_family():
    warn, info = C.chip_classes("warn"), C.chip_classes("info")
    assert warn != info
    assert C.chip_value_class("warn") != C.chip_value_class("info")
    for cls in (warn, info):
        for arb in re.findall(r"\[[^\]]*\]", cls):
            assert " " not in arb


# -------------------------------------------------------------------- dial
def test_dial_draws_an_arc_proportional_to_confidence():
    svg = D.dial_svg(0.56, "Whipsaw")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "WHIPSAW" in svg and "56%" in svg and "CONFIDENCE" in svg
    assert svg.count("<path") == 2                # halo + value, no filter
    assert "<filter" not in svg and "<style" not in svg
    assert "dominant-baseline" not in svg


def test_full_confidence_draws_a_circle_not_a_degenerate_arc():
    """A 360 degree sweep's endpoints coincide and SVG draws NOTHING for it
    (measured: getTotalLength() == 0), so 100% would render an EMPTY ring —
    the most misleading thing this dial could do."""
    svg = D.dial_svg(1.0, "Whipsaw")
    assert "<path" not in svg
    # track + outer + inner + halo + value = 5 circles
    assert svg.count("<circle") == 5
    assert "100%" in svg


def test_zero_confidence_draws_track_only():
    svg = D.dial_svg(0.0, "Whipsaw")
    assert "<path" not in svg
    assert svg.count("<circle") == 3              # outer + track + inner
    assert "0%" in svg


def test_missing_confidence_is_a_dash_not_a_zero():
    svg = D.dial_svg(None, "Unclear")
    assert "—" in svg and "<path" not in svg


@pytest.mark.parametrize("junk", ["x", {}, [], float("nan"), float("inf"), True])
def test_dial_never_raises(junk):
    out = D.dial_svg(junk, junk)
    assert out.startswith("<svg") and out.endswith("</svg>")


def test_dial_escapes_its_name():
    assert "<script" not in D.dial_svg(0.5, "<script>x</script>")


def test_dial_emits_nothing_the_sanitizer_would_strip():
    """Mirrors test_rings' guard. Only checkable here: a stripped attribute
    changes nothing server-side, so the page renders — just wrong."""
    from test_rings import _dompurify_allowlist

    allow = _dompurify_allowlist()
    svg = D.dial_svg(0.56, "Whipsaw")
    tags = set(re.findall(r"<([a-zA-Z][\w-]*)", svg))
    attrs = set(re.findall(r'([a-zA-Z][\w-]*)="', svg))
    stripped = sorted(n for n in tags | attrs if n.lower() not in allow)
    assert not stripped, f"the sanitizer would strip: {stripped}"
    assert {"svg", "circle", "path", "text"} <= tags


def test_dial_reuses_the_rings_arc_geometry():
    """Not a re-derivation: the same tested helper, so the two graphics cannot
    disagree about where 12 o'clock is."""
    from pages import rings
    d = rings._arc_path(D.CX, D.CY, D.R_TRACK, 0.0, 360.0 * 0.56)
    assert d and d in D.dial_svg(0.56, "X")


# ------------------------------------- the app surface (2026-09-19) ---------
def test_the_shared_surface_vocabulary_is_the_apps_own_palette():
    """The console's ground, its one grey ``line`` and its six-step neutral
    ladder retired with the consistency standard. These constants are what the
    three console modules draw their cards, cells, rules, grooves and text
    steps from, and every one of them must resolve to an APP palette value —
    the thing a console token may still be is a READING.

    They live here, in the module both card files already import, so a card in
    ``console_cards`` cannot drift from a card in ``console_regime``."""
    from pages.options import theme
    P = theme.THEME["palette"]
    assert C.CARD == theme.CARD
    assert C.CELL == f"bg-[{P['card_bg']}]"
    assert C.HAIRLINE == f"bg-[{P['card_border']}]"
    assert C.RULE == f"border-[{P['card_border']}]"
    assert C.RULE_STRONG == f"border-[{P['btn_border']}]"
    assert C.TXT == theme.LABEL
    assert C.MUTED == theme.MUTED
    assert C.DIM == f"text-[{P['icon']}]"
    assert C._ABSENT == P["muted"]


def test_the_meter_track_is_the_app_groove_with_a_brighter_rule():
    """A track is what a fill moves in; only the FILL encodes anything. The
    card border is the groove and the button border its own rule, one step
    brighter — the pairing ``momentum_view``'s LEVEL_TRACK / LEVEL_GROOVE and
    the Desk's breadth track already use."""
    from pages.options import theme
    P = theme.THEME["palette"]
    track = C.track_classes()
    assert f"bg-[{P['card_border']}]" in track
    assert f"border-[{P['btn_border']}]" in track
    assert "#788ca0" not in track, "the console's own line colour is retired"


def test_a_missing_reading_is_the_apps_muted_step_on_every_primitive():
    """"No read" is an ABSENCE, and an absence is surface. All three primitives
    that can report one must agree on the colour, or the same non-reading would
    look different in the meter, the band and the signed row."""
    from pages.options import theme
    muted = theme.THEME["palette"]["muted"]
    assert C.band_hex("muted") == muted
    assert C.band_hex("nonsense") == muted
    assert C.meter_row("DAY", None)["hex"] == muted
    assert C.bipolar_geometry(None)["hex"] == muted
    assert muted in C.NO_READ_HATCH


def test_the_band_colours_and_the_glow_are_untouched():
    """Charts keep their data colours: the half the sweep may not reach.
    Passes before and after by design — a must-not-change guard."""
    assert C.band_hex("positive") == "#35d68a"
    assert C.band_hex("yellow") == "#d7d76a"
    assert C.band_hex("olive") == "#b9cf6a"
    assert C.band_hex("warning") == "#e0b74e"
    assert C.band_hex("negative") == "#f2646b"
    assert C.meter_row("DAY", 83)["glow"].startswith("shadow-[")


def test_the_dial_keeps_its_accent_arc_and_loses_its_display_face():
    """The ARC is the reading and stays on the console accent. The regime word
    was drawn in Rajdhani over a near-black ground; the face retired with the
    ground, so the dial emits no ``font-family`` of its own."""
    from pages.options import theme
    svg = D.dial_svg(0.56, "Whipsaw")
    assert theme.CONSOLE_COLORS["accent"] in svg
    assert "font-family" not in svg
    assert theme.THEME["palette"]["title"] in svg     # the word
    assert theme.THEME["palette"]["icon"] in svg      # the CONFIDENCE caption
