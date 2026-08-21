"""The standalone streaming mirror of /desk (``webgui/desk_stream.py``).

Two things are under test, and only one of them is arithmetic. ``snapshot`` is
PURE — plain dicts in, a JSON-safe dict of display-ready strings out — so the
whole mirror is testable without a browser and without Redis, which is the same
shape ``pages/desk.py`` itself takes.

The other thing is the invariant that justifies the module existing at all: the
mirror must DELEGATE. Every number it prints has to come from the same builder
``/desk`` calls, so the two screens cannot drift. Several tests below assert
against ``pages.desk`` directly rather than against a literal, precisely so a
change to a formatter shows up here as agreement rather than as a stale copy.
"""
import asyncio
import datetime
import json

import desk_stream as ds
from pages import bullbear as _bb
from pages import desk as d
from pages.options import flow as _flow
from pages.options import header as _hdr
from pages.options import matrix as _mx


# ── fixtures: the smallest payload map that exercises every region ───────────
def _matrix():
    return {"rows": [
        {"symbol": "$SPX", "spot": 6700.0, "day_pct": 0.31, "flip": 6680.0,
         "call_wall": 6750.0, "put_wall": 6650.0, "net_gex": 1.42e9,
         "gex_regime": "above", "hotness": 91.0, "atm_iv": 14.2,
         "iv_state": "stable", "dealer_regime": "delta_wall_pin",
         "trend_state": "up", "pc_ratio": 1.1, "net_prem_m": 3.4},
        {"symbol": "SPY", "spot": 668.0, "day_pct": -0.2, "flip": 669.0,
         "call_wall": 675.0, "put_wall": 660.0, "net_gex": -5.4e8,
         "gex_regime": "below", "hotness": 55.0, "atm_iv": 12.0,
         "iv_state": "spiking", "dealer_regime": "neutral"},
    ]}


def _payloads():
    return {
        "options:header": {"vix": 18.42, "vix_regime": {"label": "Elevated"}},
        "options:gex_status": {"age_seconds": 41.0},
        "options:matrix": _matrix(),
        "options:flow_alerts": {"alerts": [
            {"id": "a1", "ts": 1_755_000_000, "symbol": "SPY", "type": "uoa",
             "side": "call", "text": "SPY calls 4.4x OI"},
        ]},
        "options:paper_account": {"positions": [
            {"position_id": "p1", "symbol": "SPY",
             "strategy": "put_credit_spread", "short_strike": 600.0,
             "long_strike": 595.0, "expiration": "2099-01-15", "quantity": 1,
             "entry_credit": 1.1, "current_value": 0.6,
             "unrealized_pnl": 50.0, "rescue_state": "tested"},
        ]},
        "options:driver_paper_account": {"positions": []},
        "options:captured": {"signals": []},
        "sentiment:regime": {"committed_label": "trending", "confidence": 0.78,
                             "direction": 1, "direction_strong": True},
        "sentiment:composite": {"live": {"composite": {"bias": "Cautious",
                                                       "total_score": 4.45}},
                                "derived": {"trend": {"state": "resilient",
                                                      "score": 62.0,
                                                      "confidence": 0.8}}},
        "sentiment:history": {"snaps": []},
        "sentiment:bullbear": {"levels": {"sector": [
            {"symbol": "XLK", "label": "Technology", "day_pct": 1.25,
             "raw": {"trend": 3.0, "excess": 1.0, "participation": 0.8}},
        ]}},
    }


_NOW = datetime.datetime(2026, 8, 20, 10, 30, tzinfo=ds._CT)


# ── the SSE wire format ──────────────────────────────────────────────────────
def test_sse_frame_carries_the_event_name_and_a_single_data_line():
    frame = ds.sse_frame("desk", {"a": 1})
    assert frame.startswith("event: desk\n")
    assert frame.endswith("\n\n")            # the blank line TERMINATES an event
    data = [ln for ln in frame.splitlines() if ln.startswith("data: ")]
    assert data == ["data: " + json.dumps({"a": 1}, separators=(",", ":"))]


def test_sse_frame_never_emits_a_raw_newline_inside_data():
    """A newline inside ``data:`` would split one event into two malformed ones."""
    frame = ds.sse_frame("desk", {"note": "line one\nline two"})
    assert len([ln for ln in frame.splitlines() if ln.startswith("data: ")]) == 1


# ── snapshot: shape and JSON-safety ──────────────────────────────────────────
def test_snapshot_is_json_serializable():
    """It goes out over the wire, so a stray float('nan') or datetime is fatal."""
    text = json.dumps(ds.snapshot(_payloads(), _NOW))
    assert "NaN" not in text and "Infinity" not in text


def test_snapshot_carries_every_region_even_with_no_payloads_at_all():
    snap = ds.snapshot({}, _NOW)
    for region in ("clock", "freshness", "vix", "regime", "cards", "bullbear",
                   "dealer", "board", "flow", "positions"):
        assert region in snap, region


def test_snapshot_regions_wait_rather_than_render_zero_when_a_service_is_cold():
    snap = ds.snapshot({}, _NOW)
    for region in ("dealer", "board", "flow", "positions"):
        assert snap[region]["rows"] == []
        assert snap[region]["note"] == d.WAITING_OPTIONS, region
    assert snap["bullbear"]["note"] == d.WAITING_BULLBEAR


# ── delegation: the numbers must come from pages/desk.py ─────────────────────
def test_clock_delegates_to_the_desk_countdown():
    facts = d.countdown_facts(_NOW)
    assert ds.snapshot({}, _NOW)["clock"] == {"label": facts["label"],
                                              "text": facts["text"]}


def test_freshness_prints_the_desks_own_label_and_warns_when_stale():
    live = ds.snapshot(_payloads(), _NOW)["freshness"]
    assert live["text"] == d.freshness_facts({"age_seconds": 41.0})["label"]
    assert live["stale"] is False
    assert live["tone"] == d._C["positive"]

    stale = ds.snapshot({"options:gex_status": {"age_seconds": 9_000.0}},
                        _NOW)["freshness"]
    assert stale["stale"] is True
    assert stale["tone"] == d._C["warning"]


def test_no_probe_data_reads_unknown_and_never_live():
    fresh = ds.snapshot({}, _NOW)["freshness"]
    assert fresh["stale"] is True
    assert "Live" not in fresh["text"]


def test_vix_band_colour_agrees_with_the_header_pages_own_map():
    """One palette, read out of ``header._REGIME_BG`` rather than restated."""
    for label, cls in _hdr._REGIME_BG.items():
        assert ds.vix_band_hex(label) == cls[len("bg-["):-1]


def test_vix_value_is_formatted_by_the_desks_price_formatter():
    vix = ds.snapshot(_payloads(), _NOW)["vix"]
    assert vix["value"] == d.fmt_price(18.42)
    assert vix["band"] == "Elevated"


def test_regime_word_and_tone_follow_the_committed_direction():
    up = ds.snapshot(_payloads(), _NOW)["regime"]
    assert up["word"] == d.regime_display(_payloads()["sentiment:regime"])["word"]
    assert up["tone"] == d._C["positive"]
    assert up["sub"] == "confidence 78%"

    down = dict(_payloads())
    down["sentiment:regime"] = {**down["sentiment:regime"], "direction": -1}
    assert ds.snapshot(down, _NOW)["regime"]["tone"] == d._C["negative"]


def test_an_unclear_regime_is_muted_and_a_withheld_confidence_prints_nothing():
    snap = ds.snapshot({"sentiment:regime": {"unclear": True}}, _NOW)
    assert snap["regime"]["tone"] == d._C["muted"]
    assert snap["regime"]["sub"] == ""


def test_the_two_score_cards_print_the_desks_own_pill_text():
    cards = ds.snapshot(_payloads(), _NOW)["cards"]
    assert [c["title"] for c in cards] == ["MARKET SENTIMENT", "MARKET TREND"]
    live = _payloads()["sentiment:composite"]["live"]
    assert cards[0]["pill"] == d.sentiment_pill_text(live, [])
    assert len(cards[0]["arcs"]) == 3


# ── dealer positioning ───────────────────────────────────────────────────────
def test_dealer_rows_follow_desk_symbol_order_and_the_desks_formatters():
    rows = ds.snapshot(_payloads(), _NOW)["dealer"]["rows"]
    assert [r["symbol"] for r in rows] == ["$SPX", "SPY"]
    assert rows[0]["spot"] == d.fmt_price(6700.0)
    assert rows[0]["net_gex"] == d.fmt_gex(1.42e9)
    assert rows[0]["regime_word"] == d.regime_word("above")


def test_a_stale_feed_withholds_the_walls_and_says_why():
    p = dict(_payloads())
    p["options:gex_status"] = {"age_seconds": 9_000.0}
    dealer = ds.snapshot(p, _NOW)["dealer"]
    assert dealer["rows"][0]["call_wall"] == "—"
    assert "withheld" in dealer["warning"].lower()


def test_a_live_feed_carries_no_withheld_warning():
    assert ds.snapshot(_payloads(), _NOW)["dealer"]["warning"] == ""


# ── opportunity board ────────────────────────────────────────────────────────
def test_board_rows_are_hottest_first_and_capped_at_the_desks_own_count():
    board = ds.snapshot(_payloads(), _NOW)["board"]
    assert [r["symbol"] for r in board["rows"]] == ["$SPX", "SPY"]
    assert board["subtitle"] == "HOTTEST {}".format(d.BOARD_ROWS_N)
    assert board["rows"][0]["hotness"] == d.fmt_hotness(91.0)
    assert board["rows"][0]["setup"] == d.setup_word("delta_wall_pin")


# ── flow feed ────────────────────────────────────────────────────────────────
def test_flow_rows_are_the_flow_pages_own_rows_newest_first():
    rows = ds.snapshot(_payloads(), _NOW)["flow"]["rows"]
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["kind"] == d.flow_kind_text(
        d.flow_rows(_payloads()["options:flow_alerts"])[0])


def test_every_flow_tone_the_flow_page_can_stamp_resolves_to_a_real_hex():
    """The mirror cannot render a Tailwind class, so it resolves the finite set.

    ``FLOW_TONE_HEX`` is BUILT from ``flow._TONE``, so key coverage is vacuous —
    a new alert type would appear in it automatically. The hex being non-None is
    the assertion with teeth: it fails when the new tone names a colour
    ``TAILWIND_HEX`` has never heard of, which is exactly when the mirror would
    otherwise paint a brand-new alert type in a silent fallback hue.
    """
    stamped = set(_flow._TONE.values()) | {_flow._TONE_NEUTRAL}
    unresolved = sorted(c for c in stamped if not ds.FLOW_TONE_HEX.get(c))
    assert unresolved == [], "no hex for {}".format(unresolved)


def test_an_unknown_tailwind_colour_resolves_to_nothing_rather_than_a_guess():
    """What makes the coverage tests above capable of failing at all."""
    assert ds.tw_hex("text-cerulean-400") is None
    assert ds.tw_hex("font-bold uppercase") is None
    assert ds.tw_hex("text-[#abcdef]") == "#abcdef"
    assert ds.tw_hex("text-emerald-200/80") == ds.TAILWIND_HEX["emerald-200"]
    assert ds.tw_hex("bg-slate-600/40 text-slate-200", prefix="bg") == \
        ds.TAILWIND_HEX["slate-600"]


def test_every_bullbear_quadrant_and_board_signal_resolves_to_a_real_hex():
    """The same guard for the two other finite class maps the mirror reads."""
    for q in _bb.QUADRANTS:
        assert ds.tw_hex(_bb.quadrant_class(q)) is not None, q
    for s in ("buy", "neutral", "sell"):
        assert ds.tw_hex(_mx.signal_class(s), prefix="bg") is not None, s


# ── positions ────────────────────────────────────────────────────────────────
def test_positions_summary_totals_the_whole_book_not_the_visible_slice():
    """The one figure on this panel somebody acts on. A cap that reached the
    summary would report no trades in trouble while trades were in trouble."""
    p = dict(_payloads())
    p["options:paper_account"] = {"positions": [
        {"position_id": "p{}".format(i), "symbol": "SPY",
         "strategy": "put_credit_spread", "expiration": "2099-01-15",
         "unrealized_pnl": 10.0, "rescue_state": "tested"}
        for i in range(d.POSITION_ROWS_N + 4)
    ]}
    pos = ds.snapshot(p, _NOW)["positions"]
    assert len(pos["rows"]) == d.POSITION_ROWS_N
    full = d.position_rows(p["options:paper_account"], None, None)
    assert pos["summary"] == d.summary_line(d.positions_summary(full),
                                            d.POSITION_ROWS_N)
    assert "AT RISK {}".format(d.POSITION_ROWS_N + 4) in pos["summary"]


def test_a_position_row_carries_its_book_flag_and_click_through_route():
    row = ds.snapshot(_payloads(), _NOW)["positions"]["rows"][0]
    assert row["source"] == d.PAPER_SOURCE
    assert row["flag"] == d.position_flag("tested")
    assert row["href"] == d.POSITION_ROUTES[d.PAPER_SOURCE]
    assert row["expiry"] == d.expiry_text(
        d.position_rows(_payloads()["options:paper_account"], None, None)[0])


def test_an_empty_book_says_so_rather_than_waiting_forever():
    p = {"options:paper_account": {"positions": []},
         "options:driver_paper_account": {"positions": []},
         "options:captured": {"signals": []}}
    assert ds.snapshot(p, _NOW)["positions"]["note"] == "No open positions."


# ── bull / bear strip ────────────────────────────────────────────────────────
def test_bullbear_chips_and_headline_come_from_the_map_page():
    bb = ds.snapshot(_payloads(), _NOW)["bullbear"]
    view = _payloads()["sentiment:bullbear"]
    assert bb["headline"] == d.bullbear_headline(view)
    assert [c["label"] for c in bb["chips"]] == \
        [c["label"] for c in d.bullbear_chips(view)]
    assert bb["chips"][0]["quadrant"] == "rising_leading"


# ── the document ─────────────────────────────────────────────────────────────
def test_document_is_a_complete_standalone_page_wired_to_the_stream():
    doc = ds.document()
    assert doc.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in doc.rstrip()
    assert ds.STREAM_ROUTE in doc
    assert "EventSource" in doc


def test_document_carries_its_own_styling_and_needs_no_app_stylesheet():
    doc = ds.document()
    assert "<style>" in doc
    # The console palette, read out of the theme rather than hand-copied.
    assert d._C["positive"] in doc and d._C["negative"] in doc


def test_document_links_back_to_the_real_desk():
    assert 'href="/desk"' in ds.document()


def test_the_panel_grid_is_two_by_two_at_every_width():
    """An explicit requirement, so it gets an explicit guard.

    A string assertion on CSS is a weak test and the browser is the real check —
    but the failure it catches is a REGRESSION to ``auto-fit``/``minmax(Npx,…)``,
    which reads as perfectly reasonable CSS and silently reintroduces the
    width-dependent collapse this rules out. ``minmax(0, 1fr)`` is load-bearing
    and not cosmetic: a grid item's automatic minimum is its content, so a bare
    ``1fr`` would let the tables push the page wider instead of shrinking.
    """
    css = ds.document()
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "auto-fit" not in css
    # The floor that keeps a narrow column's table inside its own panel.
    assert ".pbody" in css and css.count('class="pbody"') == 4


# ── the stream loop ──────────────────────────────────────────────────────────
class _FakeRequest:
    """Stands in for a Starlette Request — disconnects after ``alive`` polls."""

    def __init__(self, alive):
        self.alive = alive

    async def is_disconnected(self):
        self.alive -= 1
        return self.alive < 0


def _drain(request, monkeypatch, payloads, versions):
    """Run ``event_stream`` to completion with no real sleeping."""
    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(ds, "_sleep", _no_sleep)
    monkeypatch.setattr(ds, "read_versions", lambda: dict(versions))
    monkeypatch.setattr(ds, "read_payloads", lambda views=None: dict(payloads))

    async def _run():
        return [frame async for frame in ds.event_stream(request)]

    return asyncio.run(_run())


def test_the_stream_opens_with_a_full_desk_snapshot(monkeypatch):
    frames = _drain(_FakeRequest(0), monkeypatch, _payloads(),
                    {v: 1 for v in d.VIEWS})
    assert frames[0].startswith("event: desk\n")
    body = json.loads(frames[0].split("data: ", 1)[1])
    assert body["vix"]["value"] == d.fmt_price(18.42)


def test_the_stream_ticks_the_clock_without_resending_an_unchanged_desk(
        monkeypatch):
    frames = _drain(_FakeRequest(3), monkeypatch, _payloads(),
                    {v: 1 for v in d.VIEWS})
    kinds = [f.split("\n", 1)[0] for f in frames]
    assert kinds[0] == "event: desk"
    assert kinds.count("event: clock") >= 1
    # Versions never moved, so exactly ONE desk frame — the opening one.
    assert kinds.count("event: desk") == 1


def test_the_stream_resends_the_desk_when_a_cache_version_moves(monkeypatch):
    versions = {v: 1 for v in d.VIEWS}
    payloads = _payloads()

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(ds, "_sleep", _no_sleep)
    monkeypatch.setattr(ds, "read_payloads", lambda views=None: dict(payloads))

    calls = {"n": 0}

    def _versions():
        calls["n"] += 1
        if calls["n"] > 1:
            versions["options:matrix"] = 2
        return dict(versions)

    monkeypatch.setattr(ds, "read_versions", _versions)

    async def _run():
        return [f async for f in ds.event_stream(_FakeRequest(4))]

    kinds = [f.split("\n", 1)[0] for f in asyncio.run(_run())]
    assert kinds.count("event: desk") == 2


def test_the_stream_stops_when_the_client_disconnects(monkeypatch):
    frames = _drain(_FakeRequest(0), monkeypatch, _payloads(),
                    {v: 1 for v in d.VIEWS})
    assert len(frames) == 1          # the opening snapshot, then the hang-up


# ── routes ───────────────────────────────────────────────────────────────────
def test_both_routes_are_registered_on_the_app():
    import main
    from nicegui import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert ds.PAGE_ROUTE in paths
    assert ds.STREAM_ROUTE in paths
    assert main is not None
