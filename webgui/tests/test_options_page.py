"""Tests for webgui/pages/options.py.

Cover the pure transforms (signal dicts -> table rows/columns) and that the
page exposes a render() entrypoint. The NiceGUI rendering itself is exercised
by the shell smoke run; here we keep the data marshalling honest.

The "Why no trade?" panel is covered at the bottom: its pure builders over the
``options:scan_funnel`` payload, and the render wiring that keeps the Redis read
on OPEN and off the event loop.
"""
import inspect

import pytest

import bus_client
import pages.options.scanner as options
from pages.options import funnel_view as fv

SAMPLE = {
    "symbol": "SPY",
    "type": "PCS",
    "expiration": "2026-06-14",
    "dte": 0,
    "short_strike": 450.0,
    "long_strike": 445.0,
    "width": 5.0,
    "credit": 0.34,
    "max_loss": 4.66,
    "rr_pct": 7.3,
    "pop_pct": 73.2,
    "composite_score": 72,
    "grade": "A",
}


def test_render_is_callable():
    assert callable(options.render)


def test_signal_columns_expose_key_fields():
    fields = {c["field"] for c in options.signal_columns()}
    assert {"symbol", "type", "grade", "composite_score", "credit", "pop_pct"} <= fields


def test_signal_rows_maps_fields():
    rows = options.signal_rows([SAMPLE])
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "SPY"
    assert r["type"] == "PCS"
    assert r["grade"] == "A"
    assert r["composite_score"] == 72
    assert r["strikes"] == "450/445"          # Short/Long merged into one column


def test_signal_rows_handles_missing_fields():
    # A sparse signal must not raise (engine fields vary by trade type).
    rows = options.signal_rows([{"symbol": "X"}])
    assert rows[0]["symbol"] == "X"


def test_signal_rows_keep_id_for_detail():
    rows = options.signal_rows([{"symbol": "SPY", "id": "SPY_PCS_1"}])
    assert rows[0]["id"] == "SPY_PCS_1"


def test_signal_rows_sorted_by_score_desc():
    rows = options.signal_rows([
        {"symbol": "LO", "composite_score": 10},
        {"symbol": "HI", "composite_score": 90},
    ])
    assert [r["symbol"] for r in rows] == ["HI", "LO"]


def test_page_imports_no_engine_or_autoscan():
    """Regression: the page is a Tier-3 reader — the engine import, the
    in-process result cache, and the auto-scan scheduler all moved to
    services/options_svc. None of their symbols may remain on the module."""
    assert not hasattr(options, "run_full_scan")
    assert not hasattr(options, "_LAST_RESULTS")
    assert not hasattr(options, "start_autoscan")
    assert not hasattr(options, "autoscan_due")
    assert not hasattr(options, "_run_scan_sync")
    assert not hasattr(options, "_autoscan_loop")


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service not running / cold start) — the Tier-3 graceful-empty path.

    The webgui suite has no NiceGUI User fixture; rendering inside a slot
    context (a card) is enough to exercise the widget wiring + initial paint.
    """
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:scan") is None  # confirm empty
    with ui.card():
        options.render()  # must not raise


# ── the "Why no trade?" panel ────────────────────────────────────────────────
# The scanner's funnel dialog: chips per bucket, a symbol picker, and three
# cards built by the PURE ``pages/options/funnel_view``. The page owns the read
# and the widgets; every sentence on screen comes from that module, so a symbol
# the scan never reached, a cold view and a ``stop`` all read as words rather
# than as a stage list of confident zeroes.


def _funnel_entry(emitted_swing=0, **over):
    e = {"price": 178.42,
         "buckets": {
             "0DTE": {"chain": True,
                      "strikes": {"delta_pass": 4, "mark_fail": 0,
                                  "delta_ceiling": 0, "em_fail": 0,
                                  "liq_fail_short": 0, "width_found": 2,
                                  "width_reasons": {}},
                      "spreads": {"built": 2, "momentum_veto": 0,
                                  "kept_after_cap": 2, "regime_pass_added": 0,
                                  "regime_filter": 0, "below_iv_floor": 0,
                                  "no_iv_history": 0, "gamma_gate": 0,
                                  "emitted": 2}},
             "SWING": {"chain": True,
                       "strikes": {"delta_pass": 4, "mark_fail": 0,
                                   "delta_ceiling": 0, "em_fail": 0,
                                   "liq_fail_short": 4, "width_found": 0,
                                   "width_reasons": {}},
                       "spreads": {"built": 0, "momentum_veto": 0,
                                   "kept_after_cap": 0, "regime_pass_added": 0,
                                   "regime_filter": 0, "below_iv_floor": 0,
                                   "no_iv_history": 0, "gamma_gate": 0,
                                   "emitted": emitted_swing}},
             "DIRECTIONAL": {"windows_without_candidates": 0, "built": 3,
                             "vol_gate": 0, "score_cut": 3, "capped": 0,
                             "emitted": 0, "build_failed": False}}}
    e.update(over)
    return e


def _funnel_payload():
    return {"timestamp": "2026-09-15T09:31:00-05:00",
            "symbols": {"MU": _funnel_entry(),
                        "AAPL": _funnel_entry(emitted_swing=3),
                        "ZM": _funnel_entry()}}


# — the pure helpers —
def test_funnel_symbols_are_every_symbol_in_the_payload_sorted():
    assert options.funnel_symbols(_funnel_payload()) == ["AAPL", "MU", "ZM"]


@pytest.mark.parametrize("payload", [None, {}, {"symbols": None},
                                     {"symbols": "junk"}, "junk"])
def test_funnel_symbols_never_raises_on_junk(payload):
    assert options.funnel_symbols(payload) == []


def test_funnel_seed_keeps_the_readers_symbol_when_it_is_still_there():
    assert options.funnel_seed(["AAPL", "MU"], "MU") == "MU"
    assert options.funnel_seed(["AAPL", "MU"], "GONE") == "AAPL"
    assert options.funnel_seed(["AAPL", "MU"], None) == "AAPL"
    assert options.funnel_seed([], "MU") is None


def test_funnel_chips_count_the_symbols_each_bucket_left_empty():
    chips = options.funnel_chips(_funnel_payload())
    by_bucket = {c["bucket"]: c for c in chips}
    assert [c["bucket"] for c in chips] == list(options.FUNNEL_BUCKETS)
    assert by_bucket["0DTE"]["count"] == 0          # every symbol emitted 2
    assert by_bucket["SWING"]["count"] == 2         # MU + ZM
    assert by_bucket["DIRECTIONAL"]["count"] == 3
    assert by_bucket["SWING"]["label"] == fv.BUCKET_LABELS["SWING"]
    assert "2 of 3" in by_bucket["SWING"]["text"]


@pytest.mark.parametrize("payload", [None, {}, {"symbols": {}},
                                     {"symbols": None}, "junk"])
def test_funnel_chips_say_nothing_at_all_for_a_cold_view(payload):
    """A chip reading "0 of 0 produced nothing" is a zero nobody read."""
    assert options.funnel_chips(payload) == []


# — the cards —
def test_funnel_cards_are_one_per_bucket_from_the_funnel_view_module():
    cards = options.funnel_cards(_funnel_payload(), "MU")
    assert len(cards) == len(options.FUNNEL_BUCKETS)
    assert set(cards[0]) == {"headline", "stages", "note"}
    swing = cards[1]
    assert swing["headline"].startswith("MU · Swing:")
    assert "liquidity" in swing["headline"]
    assert [s["label"] for s in swing["stages"]][0] == fv.LABELS["delta_band"]


def test_funnel_cards_mark_exactly_the_binding_stage():
    swing = options.funnel_cards(_funnel_payload(), "MU")[1]
    assert [s["label"] for s in swing["stages"] if s["binding"]] == \
        [fv.LABELS["liquid"]]


def test_a_symbol_absent_from_the_payload_reads_as_the_modules_sentence():
    cards = options.funnel_cards(_funnel_payload(), "NVDA")
    for card in cards:
        assert fv.NOT_SCANNED in card["headline"]
        assert card["stages"] == []


@pytest.mark.parametrize("payload", [None, {}, {"symbols": {}}, "junk"])
def test_a_cold_view_reads_as_the_modules_sentence_never_as_zeros(payload):
    for card in options.funnel_cards(payload, "MU"):
        assert card["stages"] == []
        assert fv.NOT_SCANNED in card["headline"]


def test_a_stop_reads_as_the_modules_sentence_in_every_bucket():
    payload = {"timestamp": "T",
               "symbols": {"MU": _funnel_entry(stop="no_quote")}}
    for card in options.funnel_cards(payload, "MU"):
        assert card["stages"] == []
        assert card["headline"].endswith(fv.STOP_SENTENCES["no_quote"])


def test_funnel_cards_carry_the_stale_note_when_the_stamps_differ():
    payload = _funnel_payload()
    fresh = options.funnel_cards(payload, "MU", payload["timestamp"])
    assert {c["note"] for c in fresh} == {None}
    old = options.funnel_cards(payload, "MU", "2026-09-15T14:02:00-05:00")
    assert {c["note"] for c in old} == {fv.STALE}


def test_the_binding_stage_carries_the_warn_class_and_the_rest_do_not():
    from pages.options.theme import TXT_WARN
    swing = options.funnel_cards(_funnel_payload(), "MU")[1]
    classes = {s["label"]: options.stage_class(s) for s in swing["stages"]}
    assert classes[fv.LABELS["liquid"]] == TXT_WARN
    assert classes[fv.LABELS["delta_band"]] != TXT_WARN


# — the read —
def test_read_funnel_reads_the_funnel_view_and_the_live_scans_stamp():
    bus_client.reset()
    assert options._read_funnel() == ({}, {})
    assert options._FUNNEL_VIEW == "options:scan_funnel"


# — the render wiring —
def test_render_builds_the_panel_and_reads_it_off_the_event_loop():
    src = inspect.getsource(options.render)
    # Both the button and the dialog are the kit's since the 2026-09-19 page-kit
    # migration: ``kit.info_dialog`` carries the 720px card and its own close ✕,
    # so the page no longer spells either out.
    assert "kit.button(FUNNEL_TITLE" in src
    assert options.FUNNEL_TITLE == "Why no trade?"
    assert "kit.info_dialog(FUNNEL_TITLE)" in src
    # The three cards need the width they had; the kit's default carries it now.
    from pages import ui_kit as kit
    assert inspect.signature(kit.info_dialog).parameters["width"].default \
        == "w-[720px]"
    # The Redis read goes off the loop, and only on OPEN - never at page build.
    assert "run.io_bound(_read_funnel)" in src
    assert "funnel_cards(" in src and "funnel_chips(" in src
    assert "stage_class(" in src
    # Picking a symbol repaints the cards from the stored payload; it re-reads
    # nothing.
    assert "funnel_sel.on_value_change(" in src


def test_render_puts_the_why_button_left_of_run_scan():
    """Both live in the header's actions row now, and the kit adds buttons left
    to right - so source order is still what puts Run scan rightmost."""
    src = inspect.getsource(options.render)
    assert src.index("kit.button(FUNNEL_TITLE") < src.index('kit.button("Run scan"')


def test_the_panel_builds_on_a_cold_bus_without_raising():
    from nicegui import ui

    bus_client.reset()
    with ui.card() as card:
        options.render()
    texts = [e.text for e in card.descendants()
             if isinstance(e, (ui.button, ui.label))]
    assert options.FUNNEL_TITLE in texts


# — the shapes a chain that never loaded publishes —
# ``screen_spreads`` returns early on a chain it could not read, so the strike
# tally can arrive EMPTY (no keys at all); and a zero underlying publishes
# ``chain: True`` with everything zero-filled. Neither may reach the reader as a
# bare column of numbers with nothing saying what they mean.
_ZERO_SPREADS = {"built": 0, "momentum_veto": 0, "iron_condors": 0,
                 "kept_after_cap": 0, "regime_pass_added": 0, "regime_filter": 0,
                 "below_iv_floor": 0, "no_iv_history": 0, "gamma_gate": 0,
                 "emitted": 0}
_ZERO_STRIKES = {"expiration_sides_in_window": 0,
                 "expiration_sides_skipped_earnings": 0, "delta_reject": 0,
                 "delta_pass": 0, "mark_fail": 0, "delta_ceiling": 0,
                 "em_fail": 0, "liq_fail_short": 0, "width_found": 0,
                 "strikes_dropped_no_delta": 0,
                 "strikes_dropped_off_increment": 0, "width_reasons": {}}


def _swing_card(strikes, spreads):
    payload = {"timestamp": "T", "symbols": {"MU": {"buckets": {
        "SWING": {"chain": True, "strikes": strikes, "spreads": spreads}}}}}
    return options.funnel_cards(payload, "MU")[1]


def test_a_window_whose_strike_tally_was_never_written_still_reads_as_words():
    card = _swing_card({}, dict(_ZERO_SPREADS))
    assert fv.LABELS["delta_band"] not in [s["label"] for s in card["stages"]]
    assert [s["label"] for s in card["stages"] if s["binding"]] == \
        [fv.LABELS["built"]]
    assert card["headline"].endswith(".")


def test_a_window_with_no_tally_at_all_says_so_instead_of_printing_zeros():
    card = _swing_card({}, {})
    assert card["stages"] == []
    assert card["headline"].endswith(fv.NO_BUCKET)


def test_an_all_zero_window_names_the_chain_rather_than_bare_zeros():
    """The zero-filled shape: every stage really is 0, so the zeros were read -
    and the headline is the binding stage's own sentence, which is what stops a
    column of zeroes being the whole message."""
    card = _swing_card(dict(_ZERO_STRIKES), dict(_ZERO_SPREADS))
    assert [s["label"] for s in card["stages"] if s["binding"]] == \
        [fv.LABELS["delta_band"]]
    assert "expiration" in card["headline"]
    assert all(s["remaining"] == 0 for s in card["stages"])


def test_the_panel_reads_the_funnel_only_through_funnel_view():
    """No counter name and no tally indexing anywhere in the panel.

    A symbol whose chain never loaded publishes an EMPTY tally, so anything
    reaching past ``funnel_view``'s own accessors is a KeyError waiting for an
    off-hours scan - and a hand-rolled ``.get(..., 0)`` would print exactly the
    zero this app must never print.
    """
    src = "".join(inspect.getsource(fn) for fn in
                  (options.funnel_symbols, options.funnel_seed,
                   options.funnel_chips, options.funnel_cards,
                   options.stage_class, options._read_funnel, options.render))
    for name in ("strikes", "spreads", "buckets", "delta_pass", "width_found",
                 "emitted", "built", "chain"):
        assert f'"{name}"' not in src, name
