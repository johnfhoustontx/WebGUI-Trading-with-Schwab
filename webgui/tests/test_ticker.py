import inspect

from pages import ticker


def _summary():
    return {"headline": "Bulls hold 7,480 into the close",
            "highlights": ["Breadth narrows as megacaps carry the tape",
                           "  VIX   drifts   higher  ",
                           "Dealers long gamma above 7,450"],
            "slot": "close", "slot_label": "Market close",
            "report_date": "2026-09-18", "as_of": "16:20 CT",
            "report_url": "https://neuralstrike.co/report.html"}


def test_report_items_headline_then_highlights_then_stamp():
    items = ticker.report_items(_summary())
    assert [i["kind"] for i in items] == [
        "headline", "highlight", "highlight", "highlight", "stamp"]
    assert items[0]["text"] == "Bulls hold 7,480 into the close"
    assert items[2]["text"] == "VIX drifts higher"          # whitespace squashed
    assert items[-1]["text"] == "Market close report · 2026-09-18 · 16:20 CT"


def test_report_items_drops_a_highlight_that_repeats_the_headline():
    # A report with no sections falls back to its headline as the one highlight.
    s = {"headline": "Quiet tape", "highlights": ["Quiet tape"]}
    assert [i["text"] for i in ticker.report_items(s)] == ["Quiet tape"]


def test_report_items_never_invents_a_line():
    for empty in (None, {}, [], "x", {"headline": "", "highlights": []},
                  {"slot_label": "Market close", "as_of": "16:20 CT"},
                  {"highlights": "not a list"}):
        assert ticker.report_items(empty) == []


def test_report_items_highlights_without_a_headline_still_render():
    items = ticker.report_items({"highlights": ["Oil bid", None, "  "]})
    assert [(i["text"], i["kind"]) for i in items] == [("Oil bid", "highlight")]


def test_item_class_maps_every_kind_to_fixed_class():
    for kind in ("headline", "highlight", "stamp"):
        assert ticker.item_class(kind)
    assert ticker.item_class("bogus") == ticker.item_class("highlight")


def test_ticker_reads_only_the_market_report():
    """The bar quotes the published report and nothing else: no view fed by
    Schwab polling (dashboard, sentiment composite) may come back."""
    src = inspect.getsource(ticker)
    assert ticker.VIEW == "market:summary"
    for view in ("market:dashboard", "sentiment:composite"):
        assert f'"{view}"' not in src


def test_speed_class_maps_numbers_to_finite_buckets():
    # Higher seconds = slower scroll.
    assert ticker.speed_class(90) == "mkt-dur-slow"
    assert ticker.speed_class(60) == "mkt-dur-med"
    assert ticker.speed_class(35) == "mkt-dur-fast"
    # Every result is one of the three finite classes.
    for v in (10, 45, 46, 74, 75, 200):
        assert ticker.speed_class(v) in {"mkt-dur-slow", "mkt-dur-med", "mkt-dur-fast"}
    # Unparseable falls back to the medium bucket (never raises).
    assert ticker.speed_class(None) == "mkt-dur-med"
    assert ticker.speed_class("nope") == "mkt-dur-med"


def test_speed_class_used_in_ticker_css():
    # Each bucket class must be defined in the marquee CSS escape hatch.
    for cls in ("mkt-dur-slow", "mkt-dur-med", "mkt-dur-fast"):
        assert f".{cls}" in ticker._TICKER_CSS


def test_poll_reads_off_the_event_loop():
    """The ticker runs on EVERY page; its 4s poll routes its Redis reads through
    run.io_bound."""
    src = inspect.getsource(ticker.render_ticker)
    assert "async def _poll" in src
    assert "run.io_bound(_read)" in src
    assert "run.io_bound(bus_client.read_version" in src
