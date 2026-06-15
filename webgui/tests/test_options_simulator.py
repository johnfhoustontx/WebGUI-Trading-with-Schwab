"""Tests for the Simulator page pure figure/transform builders + Tier-3 wiring.

The ChainSnapshot fetch + sweep engines moved to ``services/options_svc/compute``
(``sim_fetch``/``sim_run``); the page now renders from the Redis cache and drives
compute via commands. The snapshot-object helpers (``expiries_of``/``strikes_of``/
``find_contract``) moved to the service, so their tests live in the service suite.
"""
from pages.options import simulator as sim


def test_whatif_figure_is_plotly_dict():
    df = [{"S": 440, "theo_price": -50}, {"S": 450, "theo_price": 0}, {"S": 460, "theo_price": 80}]
    fig = sim.whatif_figure(df, spot=450.0, target_s=455.0)
    assert "data" in fig and "layout" in fig
    xs = fig["data"][0]["x"]
    assert xs[0] == 440 and xs[-1] == 460
    # target_s adds a second vline shape (baseline + spot + target).
    assert len(fig["layout"]["shapes"]) == 3


def test_whatif_figure_no_target_omits_overlay():
    fig = sim.whatif_figure([{"S": 1, "theo_price": 2}], spot=1.0)
    # Only the zero baseline + spot line — no ΔS overlay.
    assert len(fig["layout"]["shapes"]) == 2


def test_ivshock_figure_two_series():
    base = {"theo_price": 1.0, "delta": 0.5, "gamma": 0.02, "theta": -0.1, "vega": 0.3}
    shock = {"theo_price": 1.6, "delta": 0.55, "gamma": 0.018, "theta": -0.12, "vega": 0.45}
    fig = sim.ivshock_figure(base, shock, mult=1.5)
    assert "data" in fig and len(fig["data"]) == 2


def test_records_normalizes_df_and_list():
    class _DF:
        def to_dict(self, orient):
            return [{"S": 1}]

    assert sim._records(_DF()) == [{"S": 1}]
    assert sim._records([{"S": 2}]) == [{"S": 2}]
    assert sim._records(None) == []


def test_vline_shape_fields():
    line = sim._vline(450.0, "#fff", dash="dash")
    assert line["x0"] == 450.0 and line["x1"] == 450.0
    assert line["line"]["dash"] == "dash"


def test_simulator_module_imports_no_engine_or_proxy():
    """Regression: the page must NOT import the engine, proxy, numpy, or splice
    OPTIONS_SCANNER onto sys.path — all of that moved into the options service."""
    import pathlib

    src = pathlib.Path(sim.__file__).read_text(encoding="utf-8")
    for forbidden in ("options_simulator", "import proxy", "OPTIONS_SCANNER",
                      "import numpy"):
        assert forbidden not in src, f"simulator.py must not reference {forbidden!r}"


def test_render_callable():
    assert callable(sim.render)


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path. Mirrors the swing
    page test: rendering inside a slot context exercises the widget wiring + the
    initial fetch-free paint (no meta → fetch prompt, no result → select prompt).
    """
    import bus_client
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:sim_meta") is None
    assert bus_client.read("options:sim_result") is None
    with ui.card():
        sim.render()  # must not raise
