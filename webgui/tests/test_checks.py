"""The Go / No-Go checklist - PURE."""
import pytest

from pages.options import checks

LIMITS = {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
          "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
          "max_risk_per_trade": 250.0}
CAPS = {"limits": LIMITS, "equity": 25000.0, "open": [], "sectors": {"ORCL": "IT"},
        "unmapped_prefix": "?"}


def _risk(n):
    return {"ledger_risk_per_contract": n, "ledger_risk_basis": {"per_contract": n}}


def _pcs(**over):
    row = {"id": "a", "symbol": "ORCL", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0,
           "long_strike": 97.5, "credit": 0.60, "iv_rank": 55.0, "iv_rank_known": True,
           "vol_floor": 30, "friction_pct": 8.0, "em_to_expiry": 6.0,
           "earnings_status": "none_scheduled", "earnings_date": None,
           **_risk(190.0), "_allow_paper": True,
           "underlying_price": 110.0}
    row.update(over)
    return row


MATRIX = {"spot": 110.0, "put_wall": 102.0, "call_wall": 120.0, "gex_regime": "above",
          "trend_dir": 0.4, "trend_state": "up"}
REGIME = {"direction": 1}


def _tones(row, matrix=MATRIX, regime=REGIME, calibration=None, caps=CAPS):
    return {c["key"]: c["tone"] for c in
            checks.build_checks(row, matrix, regime, calibration or {}, caps)}


def test_a_clean_short_put_spread_is_all_green():
    tones = _tones(_pcs())
    # With no calibration published the track record is honestly grey.
    assert tones.pop("record") == "muted"
    assert set(tones.values()) == {"pos"}
    assert set(tones) >= {"book", "earnings", "vol", "cost", "em", "wall", "gamma", "direction"}


def test_book_fit_is_the_only_red():
    tones = _tones(_pcs(**_risk(425.0)))
    assert tones["book"] == "neg"
    assert all(t != "neg" for k, t in tones.items() if k != "book")


def test_earnings_before_expiry_is_amber_with_the_date():
    cs = checks.build_checks(_pcs(earnings_status="upcoming", earnings_date="2026-10-09"),
                             MATRIX, REGIME, {}, CAPS)
    e = [c for c in cs if c["key"] == "earnings"][0]
    assert e["tone"] == "warn" and "Oct 9" in e["text"]


def test_earnings_after_expiry_is_green():
    assert _tones(_pcs(earnings_status="upcoming", earnings_date="2026-11-20"))["earnings"] == "pos"


def test_no_earnings_coverage_is_grey():
    assert _tones(_pcs(earnings_status="not_listed"))["earnings"] == "muted"


@pytest.mark.parametrize("rank,tone", [(40.0, "pos"), (39.9, "warn"), (30.0, "warn")])
def test_vol_rank_margin_over_the_floor(rank, tone):
    assert _tones(_pcs(iv_rank=rank))["vol"] == tone


def test_vol_rank_is_omitted_for_long_premium():
    row = _pcs(type="LONG_CALL", legs=[{"side": "long", "kind": "call", "strike": 110}],
               net_vega=0.5)
    assert "vol" not in _tones(row)


def test_unknown_iv_rank_is_grey_not_a_zero():
    assert _tones(_pcs(iv_rank=0, iv_rank_known=False))["vol"] == "muted"


@pytest.mark.parametrize("friction,tone,word", [(10.0, "pos", None), (10.1, "warn", None),
                                                (26.0, "warn", "very wide")])
def test_cost_to_trade(friction, tone, word):
    c = [c for c in checks.build_checks(_pcs(friction_pct=friction), MATRIX, REGIME, {}, CAPS)
         if c["key"] == "cost"][0]
    assert c["tone"] == tone
    if word:
        assert word in c["text"]


def test_short_strike_inside_one_expected_move_is_amber():
    # spot 110, short put 105, em 6 -> 0.83 EM
    assert _tones(_pcs(short_strike=105.0))["em"] == "warn"


def test_expected_move_uses_the_live_price_from_the_board():
    # the scan's price says 1.67 EM; the live board price says 0.5 EM
    assert _tones(_pcs(), matrix={**MATRIX, "spot": 103.0})["em"] == "warn"


def test_put_spread_inside_the_put_wall_is_amber():
    assert _tones(_pcs(short_strike=104.0))["wall"] == "warn"


def test_iron_condor_checks_both_walls():
    row = _pcs(type="IC", short_strike=101.0, call_short=121.0, long_strike=99.0,
               call_long=123.0)
    assert _tones(row)["wall"] == "pos"
    assert _tones({**row, "call_short": 118.0})["wall"] == "warn"


def test_below_the_flip_is_amber():
    assert _tones(_pcs(), matrix={**MATRIX, "gex_regime": "below"})["gamma"] == "warn"


def test_direction_opposing_the_market_is_amber_for_a_swing_hold():
    assert _tones(_pcs(), regime={"direction": -1})["direction"] == "warn"


def test_zero_dte_direction_reads_the_symbols_intraday_trend():
    row = _pcs(trade_type="0-DTE", dte=0)
    assert _tones(row, matrix={**MATRIX, "trend_dir": -0.5, "trend_state": "down"},
                  regime={"direction": 1})["direction"] == "warn"


def test_no_direction_is_grey():
    assert _tones(_pcs(), regime={"direction": 0})["direction"] == "muted"


def test_track_record_is_omitted_on_a_finder_row():
    assert "record" not in _tones(_pcs(fit_score=70.0, composite_score=72.0))


def test_a_row_the_ledger_cannot_open_has_no_book_line():
    assert "book" not in _tones(_pcs(_allow_paper=False))


def test_summary_chip():
    clean = checks.build_checks(_pcs(), MATRIX, REGIME, {}, CAPS)
    assert checks.summary(clean)["text"].startswith("Clear · ")
    cautioned = checks.build_checks(_pcs(short_strike=104.0), MATRIX, REGIME, {}, CAPS)
    assert checks.summary(cautioned)["state"] == "warn"
    blocked = checks.build_checks(_pcs(**_risk(425.0)), MATRIX, REGIME, {}, CAPS)
    assert checks.summary(blocked)["text"] == "Blocked · over $250 per trade"


def test_summary_with_no_context_is_unchecked():
    assert checks.summary(checks.build_checks(_pcs(), None, None, None, None))["state"] in ("warn", "pos", "muted")
    assert checks.summary([])["text"] == "unchecked"


def test_checks_come_in_the_documented_order():
    keys = [c["key"] for c in checks.build_checks(_pcs(), MATRIX, REGIME, {}, CAPS)]
    assert keys == ["book", "earnings", "vol", "cost", "em", "wall", "gamma", "direction", "record"]


def test_every_check_carries_label_tone_and_text():
    for c in checks.build_checks(_pcs(), MATRIX, REGIME, {}, CAPS):
        assert c["label"] and c["text"] and c["tone"] in checks.TONE_CLASS


def test_the_book_line_says_how_many_contracts_fit():
    book = [c for c in checks.build_checks(_pcs(), MATRIX, REGIME, {}, CAPS) if c["key"] == "book"][0]
    # per-trade $250 / $190 -> 1
    assert book["text"] == "Fits the paper book — up to 1 contract"


def test_a_blocked_book_line_names_the_cap():
    book = [c for c in checks.build_checks(_pcs(**_risk(425.0)), MATRIX, REGIME, {}, CAPS)
            if c["key"] == "book"][0]
    assert book["text"] == "Risks $425, over the $250 per-trade limit"


def test_a_row_without_a_risk_stamp_has_a_grey_book_line_not_green():
    row = _pcs(ledger_risk_per_contract=None, ledger_risk_basis=None)
    assert _tones(row)["book"] == "muted"


def test_scan_price_is_named_when_the_board_has_no_price():
    em = [c for c in checks.build_checks(_pcs(), {**MATRIX, "spot": None}, REGIME, {}, CAPS)
          if c["key"] == "em"][0]
    assert "scan price" in em["text"]


def test_a_finder_short_put_is_checked_on_its_legs():
    row = {"id": "f", "symbol": "ORCL", "type": "SHORT_PUT", "expiration": "2026-10-17",
           "dte": 12, "legs": [{"kind": "put", "side": "short", "strike": 104.0}],
           "net_vega": -0.3, "bias": "bullish", "fit_score": 60.0, "iv_rank": 55.0,
           "iv_rank_known": True, "vol_floor": 30, "friction_pct": 5.0, "em_to_expiry": 6.0,
           "earnings_status": "none_scheduled", "earnings_date": None,
           "_allow_paper": False, "underlying_price": 110.0}
    tones = _tones(row)
    assert tones["wall"] == "warn"            # short 104 inside the 102 put wall
    assert "book" not in tones and "record" not in tones


def test_the_summary_counts_checked_and_unchecked():
    clean = checks.build_checks(_pcs(), MATRIX, REGIME, {}, CAPS)   # record is muted
    assert checks.summary(clean)["text"] == "Clear · 8 of 9 checked"
    two = checks.build_checks(_pcs(short_strike=104.0, friction_pct=12.0), MATRIX, REGIME, {}, CAPS)
    assert checks.summary(two)["text"].endswith("cautions")


def test_nothing_raises_on_junk_inputs():
    junk = {"type": object(), "legs": "nope", "dte": "x", "earnings_date": "not-a-date",
            "expiration": None, "short_strike": "?"}
    checks.build_checks(junk, {"spot": "x"}, {"direction": "up"}, "bad", {"limits": []})


def test_checks_imports_nothing_it_must_not():
    import ast, inspect
    tree = ast.parse(inspect.getsource(checks))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names.add(("." * n.level) + (n.module or ""))
    assert not {x for x in names if x.split(".")[0] in {"nicegui", "bus_client", "services", "sqlite3"}}
