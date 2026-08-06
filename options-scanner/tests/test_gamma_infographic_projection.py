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


# --- 0-DTE hedge pressure + projected flip on the Explain infographic ---

def _read_with_hedge(**kw):
    from gamma_infographic import GammaRead
    base = dict(spot=7723.0, call_wall=7770.0, put_wall=7700.0, gamma_flip=7733.0,
                charm_flip=7730.0, charm_max_pos=7750.0, charm_max_neg=7710.0,
                dex_flow_usd=1.6e9)
    base.update(kw)
    return GammaRead(**base)


def test_explain_states_hedge_pressure_and_projected_flip():
    import gamma_infographic as gi
    html = gi.render_infographic(_read_with_hedge(
        hedge_pressure=2.81e9, hedge_direction="buy",
        projected_flip=7721.7, delta_flip=7720.2))
    # The size, the DIRECTION, and where the flip is heading all have to be there —
    # the gap between the actual and projected flip is the whole point.
    assert "2.81B" in html
    assert "buy" in html.lower()
    # Levels use the house whole-dollar formatter, same as every other price here.
    assert gi._fmt_px(7721.7) in html          # "7,722"
    # The baseline must be the DELTA flip, not the gamma flip — they are different
    # curves, and pairing them would imply a move that never happens.
    assert gi._fmt_px(7720.2) in html
    assert "delta flip" in html.lower()


def test_explain_omits_hedge_block_when_there_is_no_0dte_book():
    """Most symbols never have a 0-DTE expiry, so the fields are None — the page
    must simply not mention them rather than print zeros or 'None'."""
    import gamma_infographic as gi
    html = gi.render_infographic(_read_with_hedge())
    assert "None" not in html
    # Assert on THIS block's own wording — the page already says "hedge pressure"
    # elsewhere in static copy, so that phrase can't distinguish present from absent.
    assert "0-DTE charm alone" not in html


def test_gamma_read_hedge_fields_default_to_none():
    r = _read_with_hedge()
    assert r.hedge_pressure is None and r.hedge_direction is None
    assert r.projected_flip is None
