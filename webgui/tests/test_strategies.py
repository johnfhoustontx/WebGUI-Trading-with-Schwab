# webgui/tests/test_strategies.py
from pages.options import strategies as S


def test_templates_cover_all_families():
    names = set(S.STRATEGY_TEMPLATES)
    for n in ("LONG_CALL", "NAKED_PUT", "PCS", "CCS", "IC",
              "VERT_CALL_DEBIT", "CONDOR_CALL", "BUTTERFLY_CALL",
              "IRON_BUTTERFLY", "CALENDAR_CALL", "DIAGONAL_PUT"):
        assert n in names, n


def test_build_default_legs_butterfly_is_1_2_1():
    strikes = [90, 95, 100, 105, 110]
    legs = S.build_default_legs("BUTTERFLY_CALL", spot=100,
                                strikes=strikes, expiries=["2026-07-17"])
    assert [l["qty"] for l in legs] == [1, 2, 1]
    assert [l["side"] for l in legs] == ["long", "short", "long"]
    assert all(l["option_type"] == "call" for l in legs)
    ks = [l["strike"] for l in legs]
    assert ks == [95, 100, 105]
    assert all(k in strikes for k in ks)


def test_build_default_legs_calendar_uses_near_and_far_expiry():
    legs = S.build_default_legs("CALENDAR_CALL", spot=100,
                                strikes=[95, 100, 105],
                                expiries=["2026-07-17", "2026-08-21"])
    assert len(legs) == 2
    assert {l["expiry"] for l in legs} == {"2026-07-17", "2026-08-21"}
    assert legs[0]["strike"] == legs[1]["strike"] == 100
    near = next(l for l in legs if l["expiry"] == "2026-07-17")
    far = next(l for l in legs if l["expiry"] == "2026-08-21")
    assert near["side"] == "short" and far["side"] == "long"


def test_normalized_leg_keys():
    legs = S.build_default_legs("PCS", 100, [90, 95, 100, 105], ["2026-07-17"])
    for l in legs:
        assert set(l) == {"option_type", "side", "strike", "expiry", "qty", "premium"}


def test_summary_code_shape_and_expiry_based():
    strikes = [90, 95, 100, 105, 110]
    pcs = S.build_default_legs("PCS", 100, strikes, ["2026-07-17"])
    # canonical PCS -> analytic code
    assert S.summary_code("PCS", pcs) == "PCS"
    # swap a leg's option_type (shape change) -> CUSTOM
    swapped = [dict(l) for l in pcs]
    swapped[0]["option_type"] = "call"
    assert S.summary_code("PCS", swapped) == "CUSTOM"
    # two different expiries -> CUSTOM (analytic assumes a single expiry)
    multi = [dict(l) for l in pcs]
    multi[0]["expiry"] = "2026-08-21"
    assert S.summary_code("PCS", multi) == "CUSTOM"
    # a non-analytic family is always CUSTOM
    fly = S.build_default_legs("BUTTERFLY_CALL", 100, strikes, ["2026-07-17"])
    assert S.summary_code("BUTTERFLY_CALL", fly) == "CUSTOM"
    assert S.summary_code("IRON_CONDOR" if False else "IC", S.build_default_legs("IC", 100, strikes, ["2026-07-17"])) == "IC"


def test_summary_code_mismatched_leg_count_is_custom():
    # The copy-from-Simulator case: a butterfly/calendar is pasted into the
    # Calculator while the strategy dropdown still reads an analytic code (e.g.
    # "PCS"). The leg COUNT (or expiry count) won't match the PCS template, so the
    # summary must route to the generic numeric path, NOT the analytic PCS formula.
    strikes = [90, 95, 100, 105, 110]
    fly = S.build_default_legs("BUTTERFLY_CALL", 100, strikes, ["2026-07-17"])
    assert len(fly) == 3
    assert S.summary_code("PCS", fly) == "CUSTOM"        # 3 legs != PCS's 2
    cal = S.build_default_legs("CALENDAR_CALL", 100, strikes, ["2026-07-17", "2026-08-21"])
    assert S.summary_code("CCS", cal) == "CUSTOM"        # two expiries


def test_strategy_groups_reference_real_templates():
    for _label, codes in S.STRATEGY_GROUPS:
        for c in codes:
            assert c in S.STRATEGY_TEMPLATES, c


def test_strategy_menu_covers_every_template_once():
    codes = [code for _f, variants in S.STRATEGY_MENU for _v, code in variants]
    assert len(codes) == len(set(codes)), "duplicate code in STRATEGY_MENU"
    assert set(codes) == set(S.STRATEGY_TEMPLATES), "menu must cover all templates exactly"


def test_strategy_label():
    assert S.strategy_label("PCS") == "Credit spread · put"
    assert S.strategy_label("CCS") == "Credit spread · call"
    assert S.strategy_label("LONG_CALL") == "Long call"
    assert S.strategy_label("IC") == "Condor · iron"
    assert S.strategy_label("DIAGONAL_PUT") == "Diagonal · put"
    assert S.strategy_label("WHat") == "WHat"   # unknown → itself
