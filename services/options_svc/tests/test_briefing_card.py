"""The Pillow briefing card — a PNG drawn from the STRUCTURED briefing.

Why this exists rather than a browser: ``briefing_image.render_html_png`` shells
out to headless Chrome, and there is no Chrome on the Linux host. Installing one
is 350 MB on a machine holding brokerage credentials, and on Ubuntu 24.04
``chromium-browser`` is a snap whose confinement cannot read the temp ``file://``
the renderer hands it. The briefing is already stored fully structured, so the
HTML is an unnecessary intermediate: this draws the card directly.

The contract that matters is the one every push path depends on: **it never
raises**. It runs inside the always-on options_svc scheduler, and a briefing that
fails to render must degrade to a text push, never take the scheduler down.
"""
import io
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from services.options_svc import briefing_card as bc  # noqa: E402

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


SAMPLE = {
    "regime": "Pinned / negative gamma - fade the walls, respect the put wall",
    "bias": -25.0,
    "bias_label": "Mildly bearish",
    "headline": "Fade rallies into the call walls - geopolitical risk and a hawkish Fed keep a lid on it",
    "narrative": "Sell strength into the call walls. " * 6,
    "why": "Weekend US strikes near the Strait of Hormuz. " * 5,
    "macro_drivers": [
        "US strikes on Iranian rocket launchers near Strait of Hormuz spark futures selloff",
        "Oil surges 4% on supply risk",
        "Fed Chair signals higher-for-longer",
    ],
    "movers": [
        {"symbol": "IREN", "day_pct": 3.27, "last": 36.29},
        {"symbol": "NVDA", "day_pct": -1.84, "last": 181.4},
        {"symbol": "TSLA", "day_pct": 0.0, "last": 402.11},
    ],
    "indices": [
        {"symbol": "$SPX", "spot": 7679.14, "gamma_flip": 7708.0, "call_wall": 7700.0,
         "put_wall": 7650.0, "max_pain": 7650.0, "expected_move": 59.66, "pc_ratio": 1.64,
         "note": "Fade rallies into 7700-7730, buy dips toward 7650 with tight risk."},
        {"symbol": "SPY", "spot": 766.2, "gamma_flip": 769.0, "call_wall": 770.0,
         "put_wall": 762.0, "max_pain": 765.0, "expected_move": 5.9, "pc_ratio": 1.5,
         "note": "Mirror of SPX; watch 770."},
        {"symbol": "QQQ", "spot": 640.1, "gamma_flip": 643.5, "call_wall": 645.0,
         "put_wall": 636.0, "max_pain": 640.0, "expected_move": 6.4, "pc_ratio": 1.2,
         "note": "Heavier call wall at 645."},
    ],
}


def _png_size(raw):
    with Image.open(io.BytesIO(raw)) as im:
        return im.size


#############################################
# The contract: it never raises, whatever it is handed
#############################################

@pytest.mark.parametrize("payload", [
    None, {}, [], "not a dict", 42,
    {"headline": None, "indices": None, "movers": None, "macro_drivers": None},
    {"indices": [{}, {"symbol": None, "spot": None}]},
    {"bias": float("nan"), "headline": "x"},
    {"movers": [{"symbol": "X", "day_pct": float("inf"), "last": None}]},
    {"headline": "x" * 5000},
], ids=lambda p: repr(p)[:40])
def test_render_never_raises(payload):
    """It runs inside the scheduler. A bad briefing must degrade, not crash.

    None is an acceptable ANSWER here; an exception is not."""
    out = bc.render_briefing_png(payload, slot="premarket")
    assert out is None or isinstance(out, bytes)


def test_render_produces_a_real_png_from_a_real_briefing():
    raw = bc.render_briefing_png(SAMPLE, slot="premarket")
    assert isinstance(raw, bytes) and raw[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = _png_size(raw)
    assert w == bc.WIDTH * bc.SCALE
    assert h > 400, "a full briefing should not collapse to a stub"


def test_the_card_grows_with_content_rather_than_clipping():
    """Height is measured, not fixed. A briefing with more indices must be taller
    -- the failure this avoids is silently cutting the last index off."""
    small = dict(SAMPLE, indices=SAMPLE["indices"][:1], macro_drivers=[], movers=[])
    big = SAMPLE
    assert _png_size(bc.render_briefing_png(big, slot="open"))[1] > \
           _png_size(bc.render_briefing_png(small, slot="open"))[1]


#############################################
# Pure helpers
#############################################

def test_wrap_splits_on_width_and_keeps_every_word():
    font = bc._font(20)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
    lines = bc.wrap(" ".join(words), font, 120)
    assert len(lines) > 1, "a narrow box must wrap"
    assert " ".join(lines).split() == words, "wrapping must not lose or reorder words"


def test_wrap_does_not_hang_on_a_word_wider_than_the_box():
    """A single unbreakable token must be emitted, not loop forever looking for a
    break that does not exist."""
    font = bc._font(20)
    lines = bc.wrap("supercalifragilistic", font, 10)
    assert lines and "".join(lines).startswith("supercal")


def test_wrap_of_empty_text_is_empty_not_a_blank_line():
    assert bc.wrap("", bc._font(20), 500) == []
    assert bc.wrap("   ", bc._font(20), 500) == []


@pytest.mark.parametrize("bias,expected", [
    (-100, "neg"), (-25, "neg"), (-3, "flat"), (0, "flat"), (3, "flat"),
    (25, "pos"), (100, "pos"), (None, "flat"), (float("nan"), "flat"),
])
def test_bias_tone_maps_to_a_finite_set(bias, expected):
    """A colour must come from a known vocabulary, never be computed per value --
    the same rule the Tailwind standard applies in the web tier."""
    assert bc.bias_tone(bias) == expected
    assert bc.bias_tone(bias) in bc.TONES


@pytest.mark.parametrize("pct,expected", [
    (3.27, "pos"), (0.31, "pos"), (0.02, "pos"),
    (-1.84, "neg"), (-0.31, "neg"), (-0.02, "neg"),
    (0.0, "flat"), (None, "flat"), (float("nan"), "flat"),
])
def test_move_tone_is_not_the_bias_deadband(pct, expected):
    """A DAY MOVE is not a bias score and must not share its deadband.

    bias runs -100..+100, so BIAS_DEADBAND=5 is a sensible "no direction" band.
    A 5% deadband applied to a percentage move calls +3.27% flat -- which it very
    much is not, and which rendered every mover grey in the first draft.
    """
    assert bc.move_tone(pct) == expected
    assert bc.move_tone(pct) in bc.TONES


def test_a_typical_mover_is_not_grey():
    """The regression, stated in the terms a reader would notice."""
    assert bc.move_tone(3.27) != "flat"
    assert bc.bias_tone(3.27) == "flat", "the bias scale genuinely is flat there"


def test_fmt_number_drops_noise_and_survives_absence():
    assert bc.fmt(7679.14) == "7,679.14"
    assert bc.fmt(7700.0) == "7,700"
    assert bc.fmt(None) == "-"
    assert bc.fmt(float("nan")) == "-"
    assert bc.fmt(float("inf")) == "-"


def test_fmt_pct_is_signed_because_direction_is_the_point():
    assert bc.fmt_pct(3.27).startswith("+")
    assert bc.fmt_pct(-1.84).startswith("-")
    assert bc.fmt_pct(0.0) == "0.00%"
    assert bc.fmt_pct(None) == "-"


#############################################
# Fonts: the one environmental dependency
#############################################

def test_a_font_is_always_returned(monkeypatch):
    """Every candidate missing must still yield a usable font, not None. A card
    in the fallback bitmap font is ugly; a crashed scheduler is worse."""
    from services.options_svc import card_kit
    # Patched on card_kit, not bc: _font READS the candidates from its own
    # module, so patching the re-exported name would silently do nothing.
    monkeypatch.setattr(card_kit, "_FONT_CANDIDATES", {"regular": ("/nope/x.ttf",),
                                                       "bold": ("/nope/y.ttf",)})
    bc._font.cache_clear()
    f = bc._font(24)
    assert f is not None
    assert hasattr(f, "getbbox") or hasattr(f, "getsize")


def test_font_lookup_is_cached_so_layout_does_not_reopen_files():
    bc._font.cache_clear()
    a = bc._font(18)
    b = bc._font(18)
    assert a is b, "fonts must be memoised - layout asks for the same size many times"


#############################################
# Content actually reaches the image
#############################################

def test_real_sample_from_prod_renders(tmp_path):
    """Guards against a schema drift between what the model writes and what the
    card reads. The fixture is a genuine briefing pulled from the prod store."""
    p = pathlib.Path(__file__).resolve().parent / "fixtures" / "sample_briefing.json"
    if not p.exists():
        pytest.fail("the captured prod fixture is missing - schema drift would go unnoticed")
    payload = json.loads(p.read_text(encoding="utf-8"))
    raw = bc.render_briefing_png(payload, slot=payload.get("_meta", {}).get("slot"))
    assert isinstance(raw, bytes) and raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_scope_is_derived_from_the_indices_actually_drawn():
    """The header must not claim a scope the card does not show. If the model
    returns two indices, the header says two."""
    assert bc.scope_from(SAMPLE) == "$SPX / SPY / QQQ"
    two = dict(SAMPLE, indices=SAMPLE["indices"][:2])
    assert bc.scope_from(two) == "$SPX / SPY"
    assert bc.scope_from({}) == "$SPX / SPY / QQQ", "fall back, never blank"
    assert bc.scope_from({"indices": [{"symbol": None}]}) == "$SPX / SPY / QQQ"
