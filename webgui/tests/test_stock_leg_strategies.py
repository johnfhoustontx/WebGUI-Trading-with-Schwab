"""D4: stock legs in the shared leg model, and the three structures they unlock.

⚠ **Adding a template EXPOSES it on every page that mounts the picker**, and
``test_strategies.py`` requires the menu to cover every template exactly — so the
three stock structures cannot simply be left out of the menu. They are gated at
the MOUNT instead: the Calculator offers them (D4 asks for *analysis*), the
Simulator and the Rescue ad-hoc form do not.

That gate is a safety decision, not tidiness. The Rescue ad-hoc form **books into
the paper account**, and that account cannot hold shares inside
``paper_positions`` at all — shares live in ``equity_lots``, which is the whole
reason that table exists. A covered call submitted there would be stored as a bare
short call, which is the same defect shape as assessment defect 12 (the form
lists an iron butterfly and relabels it an iron condor before submitting).
"""
import pytest

from pages.options import strategies as S

_STOCK_STRATS = ("COVERED_CALL", "PROTECTIVE_PUT", "COLLAR")


# ── the leg shapes ─────────────────────────────────────────────────────────

def test_a_covered_call_is_LONG_SHARES_plus_a_SHORT_CALL():
    specs = S.STRATEGY_TEMPLATES["COVERED_CALL"]
    assert len(specs) == 2
    stock = next(s for s in specs if s["option_type"] == "stock")
    call = next(s for s in specs if s["option_type"] == "call")
    assert stock["side"] == "long" and call["side"] == "short"


def test_a_protective_put_is_LONG_SHARES_plus_a_LONG_PUT():
    specs = S.STRATEGY_TEMPLATES["PROTECTIVE_PUT"]
    assert len(specs) == 2
    assert all(s["side"] == "long" for s in specs)
    assert {s["option_type"] for s in specs} == {"stock", "put"}


def test_a_collar_is_SHARES_plus_a_LONG_PUT_and_a_SHORT_CALL():
    specs = S.STRATEGY_TEMPLATES["COLLAR"]
    assert len(specs) == 3
    by_kind = {s["option_type"]: s for s in specs}
    assert by_kind["stock"]["side"] == "long"
    assert by_kind["put"]["side"] == "long"
    assert by_kind["call"]["side"] == "short"


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_the_stock_leg_is_ONE_LOT_against_ONE_contract(code):
    """⚠ ``qty`` counts 100-share LOTS, so 1 lot covers 1 contract. Writing 100
    here would make the position 10,000 shares and every dollar figure 100x."""
    specs = S.STRATEGY_TEMPLATES[code]
    assert all(s["qty"] == 1 for s in specs)


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_the_shares_are_bought_AT_THE_MONEY(code):
    """You own the stock at whatever it costs now; there is no strike to place.
    ``atm`` is the role that resolves to spot's nearest ladder rung, and
    ``build_default_legs`` then drops it — see below."""
    stock = next(s for s in S.STRATEGY_TEMPLATES[code]
                 if s["option_type"] == "stock")
    assert stock["strike_role"] == "atm"


def test_the_option_legs_sit_OUT_of_the_money():
    """A covered call is written above the shares and a protective put bought
    below them, or neither structure does what it is for."""
    cc = next(s for s in S.STRATEGY_TEMPLATES["COVERED_CALL"]
              if s["option_type"] == "call")
    pp = next(s for s in S.STRATEGY_TEMPLATES["PROTECTIVE_PUT"]
              if s["option_type"] == "put")
    assert "up" in cc["strike_role"]
    assert "dn" in pp["strike_role"]


# ── built legs: no strike, no expiry on the share leg ─────────────────────

@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_a_built_stock_leg_carries_NO_strike_and_NO_expiry(code):
    """⚠ The invariant the pricing core relies on. A strike on a share leg would
    look meaningful and mean nothing; an expiry would make it the FRONT leg of a
    calendar-style horizon and pin the whole grid at T=0."""
    legs = S.build_default_legs(code, spot=100.0, strikes=[90, 95, 100, 105, 110],
                                expiries=["2026-10-16"])
    stock = next(l for l in legs if l["option_type"] == "stock")
    assert stock["strike"] is None
    assert stock["expiry"] is None


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_the_OPTION_legs_still_get_a_strike_and_expiry(code):
    legs = S.build_default_legs(code, spot=100.0, strikes=[90, 95, 100, 105, 110],
                                expiries=["2026-10-16"])
    for leg in legs:
        if leg["option_type"] == "stock":
            continue
        assert leg["strike"] in (90, 95, 100, 105, 110)
        assert leg["expiry"] == "2026-10-16"


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_a_built_stock_leg_keeps_the_NORMALIZED_key_set(code):
    """Every consumer reads these six keys; a stock leg is not a special dict."""
    legs = S.build_default_legs(code, 100.0, [95, 100, 105], ["2026-10-16"])
    for leg in legs:
        assert set(leg) == {"option_type", "side", "strike", "expiry", "qty",
                            "premium"}


def test_a_covered_call_builds_with_NO_strike_ladder_at_all():
    """Off-hours the chain can be empty. The share leg needs no ladder, so the
    structure must still build rather than returning []."""
    legs = S.build_default_legs("COVERED_CALL", 100.0, [], [])
    assert len(legs) == 2
    assert next(l for l in legs if l["option_type"] == "stock")["strike"] is None


# ── the summary must stay NUMERIC ─────────────────────────────────────────

@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_a_stock_structure_routes_to_the_NUMERIC_summary(code):
    """⚠ There is no analytic formula for these in ``options_calculator``, and
    inventing one would be the unmeasured change this audit keeps refusing. The
    numeric path prices the legs on a grid and reads the figures off the curve,
    which is exact for a payoff diagram."""
    legs = S.build_default_legs(code, 100.0, [95, 100, 105], ["2026-10-16"])
    assert S.summary_code(code, legs) == "CUSTOM"


def test_a_stock_leg_pasted_into_an_ANALYTIC_strategy_is_CUSTOM():
    """The real hazard: the dropdown still reads PCS while the legs were edited
    to hold shares. The shape multiset cannot match, so the analytic PCS formula
    — which would price the share leg as an option — is never reached."""
    pcs = S.build_default_legs("PCS", 100.0, [90, 95, 100, 105], ["2026-10-16"])
    edited = [dict(l) for l in pcs]
    edited[0]["option_type"] = "stock"
    assert S.summary_code("PCS", edited) == "CUSTOM"


# ── menu, groups, labels and facts ────────────────────────────────────────

@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_each_stock_structure_is_in_a_display_GROUP(code):
    grouped = {c for _label, codes in S.STRATEGY_GROUPS for c in codes}
    assert code in grouped


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_each_stock_structure_is_in_the_MENU_exactly_once(code):
    codes = [c for _f, variants in S.STRATEGY_MENU for _v, c in variants]
    assert codes.count(code) == 1


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_each_stock_structure_has_a_readable_LABEL(code):
    assert S.strategy_label(code) != code


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_each_stock_structure_leads_its_tags_with_DEBIT(code):
    """⚠ A judgement call worth stating: the option leg of a covered call is a
    CREDIT, and the POSITION is a debit — you pay for the shares. The tag names
    the cash flow at entry, which is what the frame colours, so DEBIT is the
    honest word. The blurb carries the nuance.

    Note the rest of the app calls ``COVERED_CALL`` a credit structure, and is
    right to: ITS covered call is the option leg only (``paper_positions`` holds
    no shares). These are two different objects with one name."""
    assert S.strategy_tags(code)[0] == "DEBIT"


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_each_stock_structure_says_it_needs_SHARES(code):
    """The reader has to know this is not a pure options trade before building
    it — it needs capital for 100 shares, not a spread's margin."""
    blob = " ".join(S.strategy_tags(code)) + " " + S.strategy_blurb(code)
    assert "share" in blob.lower() or "stock" in blob.lower(), blob


# ── the mount gate ────────────────────────────────────────────────────────

def test_the_stock_structures_are_named_in_ONE_place():
    """A second copy of this tuple is how the gate and the templates drift."""
    assert set(S.STOCK_STRATEGIES) == set(_STOCK_STRATS)


@pytest.mark.parametrize("code", _STOCK_STRATS)
def test_every_STOCK_STRATEGY_really_holds_a_stock_leg(code):
    """The converse guard: a name in the tuple whose template has no share leg
    would be gated off the Simulator for no reason."""
    assert any(s["option_type"] == "stock"
               for s in S.STRATEGY_TEMPLATES[code])


def test_no_OTHER_template_holds_a_stock_leg():
    """And nothing outside the tuple may grow one silently — it would reach the
    Simulator, which cannot price it."""
    for code, specs in S.STRATEGY_TEMPLATES.items():
        has_stock = any(s["option_type"] == "stock" for s in specs)
        assert has_stock == (code in S.STOCK_STRATEGIES), code
