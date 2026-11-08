"""shared.card_draw: the drawing kit every card renderer sits on."""
import io

import pytest

pytest.importorskip("PIL")

from shared import card_draw as D  # noqa: E402


def test_new_image_is_2400_by_1260():
    im = D.new_image((10, 10, 10))
    assert im.size == (2400, 1260) and im.mode == "RGB"


def test_new_image_takes_a_layout_size():
    assert D.new_image((0, 0, 0), 1200, 675).size == (2400, 1350)


def test_text_records_its_box():
    c = D.Canvas(D.new_image((10, 10, 10)))
    w = c.text(400, 100, "SPY", 96, "extrabold")
    s, x0, y0, x1, y1 = c.drawn[-1]
    assert s == "SPY" and x0 == 400 and x1 > 400
    assert y1 > y0 and w > 0


def test_in_strip_bounds():
    assert D.in_strip(364, 836) is True
    assert D.in_strip(363, 500) is False
    assert D.in_strip(500, 837) is False


def test_jpeg_from_png_round_trips():
    from PIL import Image
    im = D.new_image((30, 40, 50))
    png = D.png_bytes(im)
    jpg = D.jpeg_from_png(png, 90)
    assert jpg and jpg[:2] == b"\xff\xd8"
    assert Image.open(io.BytesIO(jpg)).size == im.size


def test_jpeg_from_png_bad_bytes_is_none():
    assert D.jpeg_from_png(b"not a png", 90) is None


def test_font_falls_back_without_inter(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "_INTER", tmp_path / "missing.woff2")
    D.font.cache_clear()
    try:
        assert D.font(20) is not None
    finally:
        D.font.cache_clear()


def test_mix_blends():
    assert D.mix((100, 100, 100), (0, 0, 0), 0.5) == (50, 50, 50)


def test_tracked_width_adds_the_spacing_between_letters():
    c = D.Canvas(D.new_image((0, 0, 0)))
    assert c.width("ABC", 20, "bold", 3.0) == pytest.approx(c.width("ABC", 20, "bold") + 6.0)
