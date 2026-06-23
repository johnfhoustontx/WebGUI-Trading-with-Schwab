from pages.options import handoff


def test_signal_to_em_payload_pcs():
    sig = {"type": "PCS", "symbol": "SPY", "expiration": "2026-07-18",
           "short_strike": 540, "long_strike": 535}
    out = handoff.signal_to_em_payload(sig)
    assert out["symbol"] == "SPY" and out["expiry"] == "2026-07-18"
    assert out["legs"] == [
        {"strike": 540.0, "option_type": "put", "side": "short"},
        {"strike": 535.0, "option_type": "put", "side": "long"},
    ]


def test_signal_to_em_payload_iron_condor():
    sig = {"type": "IC", "symbol": "QQQ", "expiration": "2026-07-18",
           "short_strike": 470, "long_strike": 465,
           "call_short": 490, "call_long": 495}
    legs = handoff.signal_to_em_payload(sig)["legs"]
    assert {"strike": 470.0, "option_type": "put", "side": "short"} in legs
    assert {"strike": 495.0, "option_type": "call", "side": "long"} in legs
    assert len(legs) == 4


def test_signal_to_em_payload_strips_dollar_symbol():
    sig = {"type": "LONG_CALL", "symbol": "$SPX", "expiration": "2026-07-18",
           "long_strike": 5400}
    out = handoff.signal_to_em_payload(sig)
    assert out["symbol"] == "SPX"
    assert out["legs"] == [{"strike": 5400.0, "option_type": "call", "side": "long"}]


from pages.options import expected_move as em


def _payload():
    return {
        "symbol": "SPY", "expiry": "2026-07-18", "spot": 100.0, "atm_iv": 0.2, "dte": 5,
        "candles": [[1, 100, 101, 99, 100], [2, 100, 102, 99, 101]],
        "em_upper": [[2, 100.0], [3, 101.0]],
        "em_lower": [[2, 100.0], [3, 99.0]],
        "legs": [{"strike": 540.0, "option_type": "put", "side": "short"},
                 {"strike": 535.0, "option_type": "put", "side": "long"}],
        "error": None,
    }


def test_leg_lines_short_solid_long_dashed():
    lines = em.leg_lines(_payload()["legs"])
    assert lines[0]["value"] == 540.0 and "dashStyle" not in lines[0]
    assert lines[1]["value"] == 535.0 and lines[1]["dashStyle"] == "Dash"


def test_expected_move_figure_series_and_crosshair():
    fig = em.expected_move_figure(_payload())
    types = {s["type"] for s in fig["series"]}
    assert "candlestick" in types
    assert fig["series"][0]["data"][0] == [1, 100, 101, 99, 100]
    assert fig["xAxis"]["type"] == "datetime"
    # Ordinal axis collapses non-trading-day gaps (no blank weekend/holiday candles).
    assert fig["xAxis"]["ordinal"] is True
    # X crosshair keeps the vertical LINE but drops its (raw-ms) label box; the
    # DATE is shown via the tooltip header instead (Highcharts won't date-format
    # the datetime X crosshair label in this build).
    assert fig["xAxis"]["crosshair"]["label"]["enabled"] is False
    assert "%" in fig["tooltip"]["xDateFormat"]      # date in the tooltip header
    assert fig["tooltip"]["valueDecimals"] == 2       # EM/price values to 2dp
    # Y crosshair shows a PRICE label box.
    assert fig["yAxis"]["crosshair"]["label"]["enabled"] is True
    assert "value" in fig["yAxis"]["crosshair"]["label"]["format"]
    assert any(s.get("dashStyle") == "Dash" for s in fig["series"] if s["type"] == "spline")
    assert len(fig["yAxis"]["plotLines"]) == 2


def test_expected_move_figure_handles_empty_payload():
    fig = em.expected_move_figure({})
    assert fig["series"] == [] or all(not s.get("data") for s in fig["series"])


def test_em_lookback_options_has_auto_and_overrides():
    opts = em.em_lookback_options()
    assert opts["auto"].startswith("Auto")
    for key in ("1mo", "3mo", "6mo", "1y"):
        assert key in opts


def test_render_callable():
    assert callable(em.render)


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path. Rendering inside a
    slot context exercises the widget wiring + the initial fetch-free paint.
    """
    import bus_client
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:expected_move") is None
    with ui.card():
        em.render()  # must not raise


def test_pending_expected_move_round_trip():
    handoff.set_pending_expected_move({"symbol": "SPY"})
    assert handoff.take_pending_expected_move() == {"symbol": "SPY"}
    assert handoff.take_pending_expected_move() is None  # one-shot clear


def test_send_to_expected_move_navigates(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        handoff.ui, "navigate",
        type("N", (), {"to": staticmethod(
            lambda *a, **k: calls.setdefault("to", (a, k)))}))
    monkeypatch.setattr(handoff.ui, "notify", lambda *a, **k: None)
    handoff.send_to_expected_move(
        {"symbol": "SPY", "expiry": "2026-07-18", "legs": []})
    assert calls["to"][0][0] == "/options/expected-move"
    assert calls["to"][1].get("new_tab") is True
    assert handoff.take_pending_expected_move()["symbol"] == "SPY"  # stashed


def test_actions_slot_has_expected_move_button():
    assert "to_em" in handoff._ACTIONS_SLOT
    assert "show_chart" in handoff._ACTIONS_SLOT


def test_em_action_slot_is_expected_move_only():
    assert "to_em" in handoff._EM_ACTION_SLOT
    assert "to_calc" not in handoff._EM_ACTION_SLOT
    assert "to_paper" not in handoff._EM_ACTION_SLOT


def test_send_to_expected_move_no_symbol_does_not_navigate(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        handoff.ui, "navigate",
        type("N", (), {"to": staticmethod(
            lambda *a, **k: calls.setdefault("to", True))}))
    monkeypatch.setattr(handoff.ui, "notify", lambda *a, **k: None)
    handoff.send_to_expected_move({"symbol": "", "legs": []})
    assert "to" not in calls  # warned, did not navigate
    handoff.take_pending_expected_move()  # clean up stash
