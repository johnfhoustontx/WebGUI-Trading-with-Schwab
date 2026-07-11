"""Explain infographic renders a forward 'Into the close' block when given a projection."""
from gamma_infographic import GammaRead, render_infographic


def _read():
    return GammaRead(
        spot=7521, call_wall=7570, put_wall=7525, gamma_flip=7507,
        charm_flip=7521, charm_max_pos=7495, charm_max_neg=7600,
        dex_flow_usd=1.6e9, sentiment_score=6, sentiment_trend="bull_trend",
        sentiment_confidence=100, vix=None)


def test_terminal_renders_projection_block():
    proj = "Into the close (15:00 CT), holding spot flat: projected gamma flip ~7510; call wall firms ~7570."
    html = render_infographic(_read(), style="terminal", projection=proj)
    assert "Into the close" in html
    assert "call wall firms ~7570" in html


def test_terminal_no_projection_block_when_empty():
    html = render_infographic(_read(), style="terminal", projection="")
    # The forward section header is absent when no projection is supplied.
    assert "gi-close" not in html          # the section's marker class


def test_render_infographic_projection_is_optional():
    # Back-compat: the 2-arg call still works (existing callers).
    html = render_infographic(_read(), "terminal")
    assert "<html" in html.lower()
