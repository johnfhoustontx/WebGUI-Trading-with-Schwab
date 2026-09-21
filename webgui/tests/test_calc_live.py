"""The PUBLIC Calculator (pages/options/calc_live.py).

The page is built for real; the service's side is played by writing the answer
and result keys straight to the test bus, and the page's own timers are driven
by hand - the harness ``test_rescue_live.py`` uses.

⚠ Collecting the timers pokes NiceGUI internals. If an upgrade moves them this
SKIPS with the reason rather than failing.
"""
import ast
import asyncio
import datetime as dt
import inspect
import pathlib

import pytest
from nicegui import ui

import bus_client
from pages.options import calc_live, calculator, pub_chain_view
from shared import public_tools as pt

_TODAY = dt.date.today()
NEAR = (_TODAY + dt.timedelta(days=5)).isoformat()
MID = (_TODAY + dt.timedelta(days=12)).isoformat()
FAR = (_TODAY + dt.timedelta(days=120)).isoformat()
LISTED = [NEAR, MID, FAR]
STRIKES = [490.0, 495.0, 500.0, 505.0, 510.0]
SRC = pathlib.Path(calc_live.__file__)


def _quotes(loaded):
    return {e: {side: {str(k): {"bid": 1.0, "ask": 1.2, "mark": 1.1,
                                "delta": 0.4 if side == "call" else -0.4}
                       for k in STRIKES} for side in ("call", "put")}
            for e in loaded}


def _chain(loaded=(NEAR, MID), symbol="SPY", quotes=False):
    out = {"symbol": symbol, "api": symbol, "spot": 502.0,
           "expirations": list(LISTED),
           "strikes": {e: {"call": list(STRIKES), "put": list(STRIKES)}
                       for e in loaded},
           "loaded_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if quotes:
        out["quotes"] = _quotes(loaded)
    return out


def _answer(key, outcome, **extra):
    bus_client.bus().cache_set(pt.cache_key(pt.answer_view(key)),
                               {"outcome": outcome, **extra,
                                "at": dt.datetime.now(dt.timezone.utc).isoformat()})


def _result(key, payload):
    bus_client.bus().cache_set(pt.cache_key(pt.result_view(key)), payload)


def _publish_chain(chain):
    bus_client.bus().cache_set(pt.cache_key(pt.chain_view(chain["symbol"])), chain)


# ── harness ─────────────────────────────────────────────────────────────────

def _walk(root):
    yield root
    for slot in getattr(root, "slots", {}).values():
        for child in slot.children:
            yield from _walk(child)


def _texts(root):
    return [el.text for el in _walk(root) if isinstance(getattr(el, "text", None), str)]


def _joined(root):
    return " | ".join(_texts(root))


def _rating_texts(root):
    """The Rate my trade dialog's texts. NiceGUI mounts a ui.dialog in the
    client's LAYOUT, not in the slot it was built from - so it is not under
    ``root`` (the private page's test finds it the same way)."""
    dialogs = [el for el in root.client.elements.values() if isinstance(el, ui.dialog)
               and any("rate-status" in getattr(e, "_classes", []) for e in _walk(el))]
    assert dialogs, "the page built no Rate my trade dialog"
    return " | ".join(_texts(dialogs[-1]))


def _all(root, cls):
    return [el for el in _walk(root) if cls in getattr(el, "_classes", [])]


def _timer(root, name):
    timers = [el for el in _walk(root) if isinstance(el, ui.timer)]
    if not timers:
        pytest.skip("cannot collect any ui.timer from the page")
    by_name = {getattr(t.callback, "__name__", ""): t for t in timers}
    assert name in by_name, f"no {name} timer; found {sorted(by_name)}"
    return by_name[name]


def _run(root, name):
    with root:
        result = _timer(root, name).callback()
        if asyncio.iscoroutine(result):
            asyncio.new_event_loop().run_until_complete(result)


def _click(root, label):
    btn = [el for el in _walk(root)
           if isinstance(el, ui.button) and getattr(el, "text", "") == label]
    assert len(btn) == 1, f"expected one {label!r} button, found {len(btn)}"
    with root:
        for listener in list(getattr(btn[0], "_event_listeners", {}).values()):
            if listener.type == "click":
                arity = len(inspect.signature(listener.handler).parameters)
                listener.handler(*((None,) if arity else ()))


_HANDLES = {}


def _editor():
    return _HANDLES["editor"]


def _sent(stream):
    return [c for _id, c in bus_client.bus().consume_commands(
        stream, group="t", consumer="c", block_ms=50)]


def _tools():
    return _sent(pt.TOOLS_STREAM)


def _math(kind=None):
    return [c for c in _sent(pt.MATH_STREAM)
            if kind is None or c.args.get("kind") == kind]


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(calc_live, "RECALC_DELAY_SEC", 0.0)
    bus_client.reset()
    _HANDLES.clear()
    calc_live.TOOLS._seen.clear()
    calc_live.MATH._seen.clear()
    from pages.options import leg_editor
    real = leg_editor.build_leg_editor

    def _capture(*a, **kw):
        handle = real(*a, **kw)
        _HANDLES["editor"] = handle
        _HANDLES["editor_kw"] = kw
        return handle

    monkeypatch.setattr(leg_editor, "build_leg_editor", _capture)
    with ui.card() as root:
        calculator.render(public=True)
    return root


def _loaded(root, quotes=False):
    """Load SPY and land its chain."""
    _click(root, "Load")
    cmd = _tools()
    assert [c.args for c in cmd] == [{"kind": "chain", "symbol": "SPY"}]
    _publish_chain(_chain(quotes=quotes))
    _answer(pt.request_key({"type": cmd[0].type, "args": cmd[0].args}), "done")
    _run(root, "_poll")


def _priced(root):
    """A landed chain with every leg's price typed, and the queues drained."""
    _loaded(root)
    legs = _editor().get_legs()
    for leg in legs:
        leg["premium"] = 1.5 if leg["side"] == "short" else 0.5
    with root:
        _editor().set_legs(legs)
    _math()
    _tools()


def _key(cmd):
    return pt.request_key({"type": cmd.type, "args": cmd.args})


# ── pure helpers ────────────────────────────────────────────────────────────

def test_the_chain_readers_live_in_one_module_both_pages_import():
    from pages.options import rescue_live
    for name in ("listed_expirations", "loaded_expirations", "ladder_strikes"):
        assert getattr(rescue_live, name) is getattr(pub_chain_view, name)
    chain = _chain()
    assert pub_chain_view.listed_expirations(chain) == LISTED
    assert pub_chain_view.loaded_expirations(chain) == [NEAR, MID]
    assert pub_chain_view.ladder_strikes(chain, NEAR, "put") == STRIKES


def test_the_quotes_block_becomes_the_chain_shape_the_grid_reads():
    from pages.options import chain_grid as cg
    assert pub_chain_view.grid_chain(_chain()) is None
    assert pub_chain_view.has_quotes(_chain()) is False
    chain = pub_chain_view.grid_chain(_chain(quotes=True))
    assert cg.chain_expiries(chain) == [NEAR, MID]
    assert cg.chain_strikes(chain, NEAR, "put") == STRIKES
    assert cg.extract_price(chain, "call", 500.0, NEAR, "bid") == 1.0
    assert cg.extract_price(chain, "call", 500.0, NEAR, "ask") == 1.2
    assert cg.extract_premium(chain, "put", 500.0, NEAR) == 1.1
    assert cg.extract_delta(chain, "put", 500.0, NEAR) == -0.4
    rows = cg.chain_grid_rows(chain, NEAR, 502.0)["rows"]
    assert [r["strike"] for r in rows] == STRIKES


def test_the_grid_adapter_is_total_and_drops_junk():
    for bad in (None, {}, {"quotes": {}}, {"quotes": "x"}, {"quotes": {NEAR: 3}}):
        assert pub_chain_view.grid_chain(bad) is None
    chain = {"quotes": {NEAR: {"call": {"nan": {"bid": 1.0}, "-5": {"bid": 1.0},
                                        "500": {"bid": float("nan"), "ask": True,
                                                "mark": 1.0, "junk": 9}}}}}
    out = pub_chain_view.grid_chain(chain)
    assert out["callExpDateMap"] == {f"{NEAR}:0": {"500.0": [
        {"bid": None, "ask": None, "mark": 1.0, "delta": None}]}}


def test_the_quotes_stamp_is_in_central_time_and_absent_without_quotes():
    chain = _chain(quotes=True)
    chain["loaded_at"] = "2026-09-21T15:05:00+00:00"
    assert pub_chain_view.quotes_as_of(chain) == "Quotes as of 10:05 CT"
    assert pub_chain_view.quotes_as_of(_chain()) is None
    assert pub_chain_view.quotes_as_of({**chain, "loaded_at": "junk"}) is None


def test_the_price_request_is_built_like_the_private_do_calc():
    legs = [{"option_type": "put", "side": "short", "strike": 500.0, "expiry": NEAR,
             "qty": 1, "premium": 1.5},
            {"option_type": "stock", "side": "long", "strike": None, "expiry": None,
             "qty": 1, "premium": None}]
    req, why = calc_live.price_request("SPY", "COVERED_CALL", legs, True, spot=502.0,
                                       iv_pct=20.0, rate_pct=4.5, ivadj_pct=0.0,
                                       contracts=1, num_strikes=24, expiry=NEAR)
    assert why is None
    assert req["kind"] == "price" and req["strategy"] == "CUSTOM"
    assert req["iv"] == pytest.approx(0.20) and req["rate"] == pytest.approx(0.045)
    assert req["legs"][1]["premium"] == 0.0          # the service fills spot
    assert pt.math_command(req) is not None


def test_an_unpriced_option_leg_is_price_needed_and_builds_nothing():
    legs = [{"option_type": "put", "side": "short", "strike": 500.0, "expiry": NEAR,
             "qty": 1, "premium": None}]
    req, why = calc_live.price_request("SPY", "NAKED_PUT", legs, False, spot=502.0,
                                       iv_pct=20.0, rate_pct=4.5, ivadj_pct=0.0,
                                       contracts=1, num_strikes=24, expiry=NEAR)
    assert req is None and why == "price_needed"


def test_the_iv_target_is_the_option_leg_nearest_spot():
    legs = [{"option_type": "put", "side": "short", "strike": 495.0, "expiry": NEAR},
            {"option_type": "call", "side": "long", "strike": 505.0, "expiry": MID},
            {"option_type": "stock", "side": "long", "strike": None, "expiry": None}]
    assert calc_live.iv_target(legs, 504.0) == ("call", 505.0, MID)
    assert calc_live.iv_target(legs[2:], 504.0) is None
    assert calc_live.iv_target(legs, None) is None


# ── what the page is, and is not ────────────────────────────────────────────

def test_a_page_load_spends_nothing(page):
    _run(page, "_load_default")
    assert _tools() == [] and _math() == []


def test_a_page_load_shows_a_recently_loaded_spy_chain_without_asking(page):
    _publish_chain(_chain())
    _run(page, "_load_default")
    assert _tools() == [] and _math() == []
    legs = _editor().get_legs()
    assert legs and all(l["strike"] in STRIKES for l in legs)


def test_load_sends_one_chain_request_on_the_tools_stream_only(page):
    _click(page, "Load")
    cmds = _tools()
    assert [(c.type, c.args) for c in cmds] == [
        (pt.TOOLS_TYPE, {"kind": "chain", "symbol": "SPY"})]
    assert _math() == []
    assert bus_client.bus().consume_commands(
        "cmd:options", group="t", consumer="c", block_ms=50) == []


def test_a_bad_symbol_sends_nothing_and_says_so(page):
    field = _all(page, "entry-ticker")[0]
    with page:
        field.value = "NOT A SYMBOL!!"
    _click(page, "Load")
    assert _tools() == []
    assert field.error and "not a symbol this page can load" in field.error


def test_a_landed_chain_fills_the_strip_and_seeds_legs_on_real_strikes(page):
    _loaded(page)
    pills = _all(page, "entry-expiry")
    assert len(pills) == len(LISTED)
    legs = _editor().get_legs()
    assert legs and all(l["strike"] in STRIKES for l in legs)
    assert all(l["expiry"] == NEAR for l in legs)


def test_a_refused_load_says_why(page):
    _click(page, "Load")
    cmd = _tools()[0]
    _answer(_key(cmd), "closed")
    _run(page, "_poll")
    assert pt.OUTCOME_TEXT["closed"] in _joined(page)


def test_quotes_off_builds_no_grid_and_no_price_source(page):
    _loaded(page, quotes=False)
    assert _all(page, "entry-gridbody") == []
    assert _all(page, "leg-price-source") == []
    assert _all(page, "leg-delta") == []
    assert len(_all(page, "leg-price")) >= 1          # the typed price box
    assert "Price is what you paid (long) or received (short), per share" \
        in _joined(page)
    kw = _HANDLES["editor_kw"]
    assert kw["price_for"] is None and kw["delta_for"] is None


def test_quotes_on_builds_the_grid_the_price_source_and_the_stamp(page):
    _loaded(page, quotes=True)
    assert len(_all(page, "entry-gridbody")) == 1
    assert _all(page, "leg-price-source")
    assert _all(page, "leg-delta")
    assert "Quotes as of" in _joined(page)
    assert _all(page, "entry-columns") == []           # the published four only
    # a quoted leg is priced from the published quotes
    assert all(l["premium"] == 1.1 for l in _editor().get_legs())


# ── pricing ─────────────────────────────────────────────────────────────────

def test_untyped_prices_send_nothing_and_ask_for_a_price(page):
    _loaded(page)
    _math()
    _run(page, "_recalc_tick")
    assert _math("price") == []
    assert pt.OUTCOME_TEXT["price_needed"] in _joined(page)


def test_an_edit_sends_one_price_request_and_an_identical_one_none(page):
    _priced(page)
    _run(page, "_recalc_tick")
    sent = _math("price")
    assert len(sent) == 1
    assert sent[0].args["symbol"] == "SPY" and len(sent[0].args["legs"]) == 2
    with page:
        _editor().apply_expiry(NEAR)                 # an edit to the same position
    _run(page, "_recalc_tick")
    assert _math("price") == []


def test_a_price_result_draws_six_metric_cards_and_the_matrix(page):
    _priced(page)
    _run(page, "_recalc_tick")
    cmd = _math("price")[0]
    key = _key(cmd)
    _result(key, {"summary": {"net_premium": 100.0, "max_profit": 100.0,
                              "max_loss": -400.0},
                  "eval_labels": ["09/22", "09/26"],
                  "pnl_data": [{"price": 500.0, "pnl": [10.0, 20.0]},
                               {"price": 505.0, "pnl": [30.0, 40.0]}]})
    _answer(key, "done")
    _run(page, "_poll")
    metrics = _all(page, "calc-live-metrics")[0]
    assert len(list(metrics.default_slot.children)) == 6


@pytest.mark.parametrize("outcome", ["price_needed", "load_first", "off_ladder"])
def test_a_refused_price_shows_its_text(page, outcome):
    _priced(page)
    _run(page, "_recalc_tick")
    _answer(_key(_math("price")[0]), outcome)
    _run(page, "_poll")
    assert pt.OUTCOME_TEXT[outcome] in _joined(page)


def test_a_visitor_over_the_math_limit_sends_nothing(page, monkeypatch):
    _priced(page)
    monkeypatch.setattr(calc_live.MATH, "allow", lambda key: False)
    _run(page, "_recalc_tick")
    assert _math() == []
    assert "limit" in _joined(page)


def test_a_landed_chain_asks_for_the_implied_volatility(page):
    _click(page, "Load")
    cmd = _tools()[0]
    _publish_chain(_chain())
    _answer(_key(cmd), "done")
    _run(page, "_poll")
    ivs = _math("iv")
    assert len(ivs) == 1 and ivs[0].args["strike"] in STRIKES
    key = _key(ivs[0])
    _result(key, {"iv": 23.4})
    _answer(key, "done")
    _run(page, "_poll")
    iv_field = [el for el in _walk(page) if isinstance(el, ui.number)
                and el.value == pytest.approx(23.4)]
    assert iv_field


# ── Rate my trade ───────────────────────────────────────────────────────────

def _row():
    return {"symbol": "SPY", "type": "PCS", "grade": "Good", "composite_score": 71,
            "expiration": NEAR, "underlying_price": 502.0,
            "legs": [{"kind": "put", "side": "short", "strike": 500.0,
                      "expiration": NEAR, "qty": 1, "mark": 1.5}]}


def test_rate_sends_one_rating_and_draws_it_with_no_paper_book_line(page):
    _priced(page)
    _click(page, "Rate my trade")
    cmds = [c for c in _tools() if c.args.get("kind") == "rate"]
    assert len(cmds) == 1
    assert cmds[0].args["structure"] in pt.STRUCTURE_CODES
    key = _key(cmds[0])
    _result(key, {"row": _row(), "public": True, "quotes": False})
    _answer(key, "done")
    _run(page, "_poll")
    text = _rating_texts(page)
    assert "Good · 71" in text                         # the banner drew
    assert any(w in text for w in ("BUY", "CAUTION", "PASS"))
    assert "Earnings" in text                          # the checklist drew
    assert "Paper book" not in text                    # ...with no book line


def test_a_rating_error_shows_its_sentence(page):
    _priced(page)
    _click(page, "Rate my trade")
    key = _key([c for c in _tools() if c.args.get("kind") == "rate"][0])
    _answer(key, "error", error_text="No quote for the 500 put.")
    _run(page, "_poll")
    assert "No quote for the 500 put." in _rating_texts(page)


def test_a_rating_error_without_a_sentence_is_the_generic_text(page):
    _priced(page)
    _click(page, "Rate my trade")
    key = _key([c for c in _tools() if c.args.get("kind") == "rate"][0])
    _answer(key, "throttled")
    _run(page, "_poll")
    assert pt.OUTCOME_TEXT["throttled"] in _rating_texts(page)


def test_the_rating_context_never_reads_the_owners_ledger_caps(monkeypatch):
    reads = []
    monkeypatch.setattr(bus_client, "read_gated",
                        lambda view, memo: (reads.append(view), (None, False))[1])
    ctx = calc_live.rating_context()
    assert ctx["caps"] is None
    assert "options:ledger_caps" not in reads


# ── the hand-off ────────────────────────────────────────────────────────────

def test_open_in_simulator_writes_the_hand_off_and_navigates(page, monkeypatch):
    from pages.options import public_handoff
    writes, went = [], []
    monkeypatch.setattr(public_handoff, "write", lambda s, l: writes.append((s, l)))
    monkeypatch.setattr(ui.navigate, "to", lambda target, *a, **k: went.append(target))
    _priced(page)
    writes.clear()
    _click(page, "Open in Simulator")
    assert went == ["/simulator"]
    assert writes and writes[-1][0] == "SPY" and writes[-1][1] == _editor().get_legs()


def test_every_leg_change_and_load_writes_the_hand_off(page, monkeypatch):
    from pages.options import public_handoff
    writes = []
    monkeypatch.setattr(public_handoff, "write", lambda s, l: writes.append((s, l)))
    _loaded(page)
    assert writes, "a load wrote nothing"
    writes.clear()
    legs = _editor().get_legs()
    legs[0]["premium"] = 2.0
    with page:
        _editor().add_leg(legs[0])
    assert writes


# ── source-level ────────────────────────────────────────────────────────────

def test_the_page_never_reads_the_hand_off():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "public_handoff.write" in calls
    assert not any(c.endswith(".read") and "handoff" in c for c in calls)


def test_the_page_module_enqueues_only_through_the_public_requests():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    writes = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            name = n.func.attr
            if name.startswith(("request", "enqueue")) or name in ("cache_set",
                                                                    "publish"):
                writes.add(ast.unparse(n.func))
    # ``pt.request_key`` is named like a write but only hashes a command.
    assert writes - {"pt.request_key"} == {"bus_client.request_public_tool",
                                          "bus_client.request_public_math"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                and (n.module or "").split(".")[-1] == "bus_client"]


def test_the_page_module_names_no_owner_view():
    text = SRC.read_text(encoding="utf-8")
    for view in ("calc_chain", "calc_result", "calc_iv", "calc_rating",
                 "shared_position", "page_state", "_LAST_CALC", "cmd:options"):
        assert view not in text, view


_ALLOWED_IMPORTS = {"nicegui", "bus_client", "visitor_limit", "shared.public_tools",
                    "shared.public_rescue", "shared.public_scan", "shared.symbols",
                    "shared.market_calendar", "shared"}
_STDLIB = {"__future__", "datetime", "logging", "math", "time", "zoneinfo"}


def test_the_page_imports_only_the_tier1_allow_list():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            if n.level:                  # a relative import: pages.options.*
                continue
            mods = [n.module or ""]
            if n.module == "shared":
                mods = [f"shared.{a.name}" for a in n.names]
        else:
            continue
        for mod in mods:
            ok = (mod in _ALLOWED_IMPORTS or mod.split(".")[0] in _STDLIB
                  or mod == "pages" or mod.startswith("pages."))
            assert ok, f"calc_live imports {mod}"
