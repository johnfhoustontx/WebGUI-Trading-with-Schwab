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


# ── short premium is read from the trade's economics, not net_vega ────────


def _line(row, key, matrix=MATRIX, regime=REGIME, calibration=None, caps=CAPS):
    cal = {} if calibration is None else calibration
    found = [c for c in checks.build_checks(row, matrix, regime, cal, caps) if c["key"] == key]
    return found[0] if found else None


def _structures_tuples():
    """The named tuples in shared/structures.py, read from its SOURCE - Tier 1
    does not import that module."""
    import ast
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[2] / "shared" / "structures.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            names[node.targets[0].id] = node.value

    def resolve(value):
        if isinstance(value, ast.Tuple):
            return tuple(e.value for e in value.elts)
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
            return resolve(value.left) + resolve(value.right)
        if isinstance(value, ast.Name):
            return resolve(names[value.id])
        raise AssertionError(ast.dump(value))
    return {k: resolve(names[k])
            for k in ("LEDGER_CREDIT", "LEDGER_DEBIT", "SHORT_PUT", "COVERED_CALL")}


def test_credit_and_debit_names_mirror_shared_structures():
    t = _structures_tuples()
    assert set(checks.CREDIT_TYPES) == (set(t["LEDGER_CREDIT"]) | set(t["SHORT_PUT"])
                                        | set(t["COVERED_CALL"]) | {"SHORT_CALL"})
    assert set(checks.DEBIT_TYPES) == set(t["LEDGER_DEBIT"])


def _legged(type_, legs, **over):
    row = _pcs(type=type_, legs=legs, credit=None, bias="neutral")
    row.update(over)
    return row


_PCS_LEGS = [{"kind": "put", "side": "short", "strike": 100.0},
             {"kind": "put", "side": "long", "strike": 97.5}]


def test_a_finder_credit_spread_with_positive_net_vega_is_short_premium():
    # The scanner's net_vega is short.vega - long.vega: POSITIVE for a credit spread.
    row = _legged("PCS", _PCS_LEGS, net_vega=0.039, credit=0.60, net_credit=60.0)
    assert {"vol", "em", "wall", "gamma"} <= set(_tones(row))


def test_an_adapted_iron_condor_with_zero_vega_is_short_premium():
    legs = _PCS_LEGS + [{"kind": "call", "side": "short", "strike": 121.0},
                        {"kind": "call", "side": "long", "strike": 123.5}]
    row = _legged("IRON_CONDOR", legs, net_vega=0.0, net_credit=120.0)
    assert {"vol", "em", "wall", "gamma"} <= set(_tones(row))


def test_a_covered_call_is_short_premium_despite_its_share_debit():
    legs = [{"kind": "stock", "side": "long", "strike": None},
            {"kind": "call", "side": "short", "strike": 121.0}]
    assert "vol" in _tones(_legged("COVERED_CALL", legs, net_debit=9558.0, net_vega=-0.09))


@pytest.mark.parametrize("over,short", [
    ({"net_credit": 150.0, "net_vega": 0.04}, True),     # economics beat vega
    ({"net_debit": 400.0, "net_vega": -0.10}, False),    # a debit is long premium
    ({"net_credit": 150.0, "net_debit": 400.0}, False),  # a positive debit decides
    ({"net_vega": -0.10}, True),                         # nothing else: vega
    ({"net_vega": 0.10}, False),
])
def test_an_unnamed_structure_is_classified_by_economics_then_vega(over, short):
    row = _legged("CUSTOM", _PCS_LEGS, **over)
    assert ("vol" in _tones(row)) is short


def test_a_scanner_credit_row_without_legs_or_numbers_stays_short_premium():
    assert "vol" in _tones(_pcs(credit=None))


# ── same-strike structures are measured from their breakevens ─────────────


_STRADDLE = [{"kind": "put", "side": "short", "strike": 110.0},
             {"kind": "call", "side": "short", "strike": 110.0}]


def test_a_short_straddle_measures_expected_move_from_its_breakevens():
    # spot 110, em 6: lower 96 is 2.3 expected moves away, upper 115 only 0.8
    row = _legged("SHORT_STRADDLE", _STRADDLE, net_credit=900.0, breakevens=[96.0, 115.0])
    em = _line(row, "em")
    assert em["tone"] == "warn"
    assert em["text"] == "Breakeven 115 is 0.8 expected moves from the price"


def test_a_short_straddle_measures_walls_from_its_breakevens():
    row = _legged("SHORT_STRADDLE", _STRADDLE, net_credit=900.0, breakevens=[96.0, 115.0])
    wall = _line(row, "wall")
    assert wall["tone"] == "warn"                 # upper 115 is below the 120 call wall
    assert wall["text"] == "Breakeven 115 is below the 120 call wall"
    wide = _legged("SHORT_STRADDLE", _STRADDLE, net_credit=900.0, breakevens=[96.0, 125.0])
    assert _line(wide, "wall")["tone"] == "pos"
    assert _line(wide, "em")["tone"] == "pos"


def test_an_iron_butterfly_reads_a_breakeven_string():
    legs = ([{"kind": "put", "side": "long", "strike": 100.0}] + _STRADDLE
            + [{"kind": "call", "side": "long", "strike": 120.0}])
    row = _legged("IRON_BUTTERFLY", legs, net_credit=500.0, breakeven="95.3 / 125")
    em, wall = _line(row, "em"), _line(row, "wall")
    assert em["tone"] == "pos" and em["text"].startswith("Breakeven ")
    assert wall["text"] == ("Breakeven 95.3 is below the 102 put wall · "
                            "Breakeven 125 is above the 120 call wall")


def test_missing_breakevens_grey_both_lines():
    row = _legged("SHORT_STRADDLE", _STRADDLE, net_credit=900.0)
    for key in ("em", "wall"):
        line = _line(row, key)
        assert (line["tone"], line["text"]) == ("muted", "Breakevens unknown")


def test_a_strangle_still_measures_its_short_strikes():
    legs = [{"kind": "put", "side": "short", "strike": 100.0},
            {"kind": "call", "side": "short", "strike": 121.0}]
    row = _legged("SHORT_STRANGLE", legs, net_credit=300.0, breakevens=[97.0, 124.0])
    assert _line(row, "em")["text"].startswith("Short 100 put")


# ── a missing live view is never "Clear" ──────────────────────────────────


def test_no_board_row_marks_its_lines_and_is_partly_checked():
    cs = checks.build_checks(_pcs(), None, REGIME, {}, CAPS)
    by = {c["key"]: c for c in cs}
    assert by["wall"].get("missing_view") and by["gamma"].get("missing_view")
    assert by["em"]["tone"] == "pos" and "missing_view" not in by["em"]   # scan price
    assert "missing_view" not in by["record"]      # a published calibration, no bucket
    assert checks.summary(cs) == {"state": "muted", "text": "Partly checked · 6 of 9 checked",
                                  "class": checks.TONE_CLASS["muted"]}


def test_no_board_and_no_scan_price_marks_the_expected_move():
    line = _line(_pcs(underlying_price=None), "em", matrix=None)
    assert line["tone"] == "muted" and line.get("missing_view")


def test_no_regime_marks_a_swing_direction_but_not_a_zero_dte_one():
    assert _line(_pcs(), "direction", regime=None).get("missing_view")
    zero = _line(_pcs(trade_type="0-DTE", dte=0), "direction", regime=None)
    assert zero["tone"] == "pos" and "missing_view" not in zero
    assert _line(_pcs(trade_type="0-DTE", dte=0), "direction", matrix=None).get("missing_view")


def test_no_caps_or_calibration_view_is_partly_checked():
    cs = checks.build_checks(_pcs(), MATRIX, REGIME, None, None)
    by = {c["key"]: c for c in cs}
    assert by["book"].get("missing_view") and by["record"].get("missing_view")
    assert checks.summary(cs)["text"] == "Partly checked · 7 of 9 checked"


def test_a_caution_still_outranks_a_missing_view():
    cs = checks.build_checks(_pcs(friction_pct=12.0), None, REGIME, {}, CAPS)
    assert checks.summary(cs)["text"] == "1 caution"


def test_a_grey_line_with_its_view_present_is_not_marked():
    row = _pcs(ledger_risk_per_contract=None, ledger_risk_basis=None)
    assert "missing_view" not in _line(row, "book")


# ── the failure log is once per (check, exception type) ───────────────────


def test_a_failing_check_logs_once_and_counts_repeats(monkeypatch, caplog):
    monkeypatch.setattr(checks, "_LOGGED", set())
    monkeypatch.setattr(checks, "_REPEATS", {})

    def boom(row):
        raise ValueError("bad field")
    monkeypatch.setattr(checks, "_earnings", boom)
    with caplog.at_level("WARNING", logger=checks.log.name):
        for _ in range(3):
            line = _line(_pcs(), "earnings")
    assert (line["tone"], line["text"]) == ("muted", "Couldn't check")
    assert "missing_view" not in line
    assert len([r for r in caplog.records if r.name == checks.log.name]) == 1
    assert checks._REPEATS == {("earnings", ValueError): 2}

    def other(row):
        raise KeyError("x")
    monkeypatch.setattr(checks, "_earnings", other)
    with caplog.at_level("WARNING", logger=checks.log.name):
        _line(_pcs(), "earnings")
    assert len([r for r in caplog.records if r.name == checks.log.name]) == 2


# ── wording ───────────────────────────────────────────────────────────────


def test_a_short_strike_on_the_wall_says_at_and_is_amber():
    line = _line(_pcs(short_strike=102.0), "wall")
    assert (line["tone"], line["text"]) == ("warn", "Short 102 put is at the 102 put wall")


@pytest.mark.parametrize("friction,shown", [(10.19, "10.1%"), (8.96, "8.9%"), (8.0, "8%")])
def test_friction_is_truncated_not_rounded(friction, shown):
    assert f"round trip {shown} of" in _line(_pcs(friction_pct=friction), "cost")["text"]


def test_the_month_name_does_not_follow_the_host_locale():
    import inspect
    assert "%b" not in inspect.getsource(checks)
    row = _pcs(earnings_status="upcoming", earnings_date="2026-05-03", expiration="2026-05-15")
    assert _line(row, "earnings")["text"] == "Earnings May 3, before expiry"


def test_expected_move_text_says_from_the_price():
    assert _line(_pcs(), "em")["text"] == "Short 100 put is 1.6 expected moves from the price"


# ── the short chip (verdict) ──────────────────────────────────────────────


def test_verdict_short_for_a_block_is_one_word():
    blocked = checks.build_checks(_pcs(**_risk(425.0)), MATRIX, REGIME, {}, CAPS)
    assert checks.verdict(blocked)["short"] == "Blocked"


def test_verdict_short_for_cautions_is_the_count():
    one = checks.build_checks(_pcs(friction_pct=12.0), MATRIX, REGIME, {}, CAPS)
    assert checks.verdict(one)["short"] == "1 caution"
    two = checks.build_checks(_pcs(short_strike=104.0, friction_pct=12.0), MATRIX, REGIME, {}, CAPS)
    assert checks.verdict(two)["short"] == checks.summary(two)["text"]
    assert checks.verdict(two)["short"].endswith("cautions")


def test_verdict_short_for_clear_drops_the_word_checked():
    clean = checks.build_checks(_pcs(), MATRIX, REGIME, {}, CAPS)   # record is muted
    assert checks.verdict(clean)["short"] == "Clear · 8 of 9"


def test_verdict_short_for_partly_checked_drops_the_counts():
    cs = checks.build_checks(_pcs(), MATRIX, REGIME, None, None)
    assert checks.verdict(cs)["short"] == "Partly checked"


def test_verdict_short_for_nothing_checked():
    assert checks.verdict([])["short"] == "unchecked"


def test_verdict_carries_summary_unchanged():
    for cs in (checks.build_checks(_pcs(), MATRIX, REGIME, {}, CAPS),
               checks.build_checks(_pcs(**_risk(425.0)), MATRIX, REGIME, {}, CAPS),
               checks.build_checks(_pcs(), None, REGIME, {}, CAPS), []):
        full = checks.verdict(cs)
        assert {k: v for k, v in full.items() if k != "short"} == checks.summary(cs)
