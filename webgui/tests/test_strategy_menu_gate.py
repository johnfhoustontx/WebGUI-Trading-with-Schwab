"""D4: the Strategy picker can EXCLUDE codes, and two mounts must.

``test_strategies.py`` requires ``STRATEGY_MENU`` to cover every template
exactly, so a template cannot be hidden by leaving it out of the menu data — a
hidden template would be unreachable, which is what that guard is for. The three
stock structures are therefore gated at each MOUNT.

⚠ **Both exclusions are safety, not taste:**

* the **Simulator**'s Replay and IV-shock engines price a ``ContractRow`` pulled
  from the option chain and have no share concept at all, so a covered call
  selected there would draw an option-only curve while the frame said "covered
  call";
* the **Rescue ad-hoc form BOOKS into the paper account**, which cannot hold
  shares inside ``paper_positions`` — that is precisely why ``equity_lots``
  exists — so a covered call submitted there would be stored as a bare short
  call. That is the shape of assessment defect 12, where the same form lists an
  iron butterfly and relabels it an iron condor on the way out.
"""
import inspect

import pytest

from pages.options import strategies as S
from pages.options import strategy_menu


def _codes(families):
    return [c for _f, variants in families for _v, c in variants]


# ── the pure filter ───────────────────────────────────────────────────────

def test_no_exclusion_returns_the_whole_menu():
    assert S.menu_families() == S.STRATEGY_MENU


def test_excluding_the_stock_strategies_drops_exactly_those():
    got = _codes(S.menu_families(exclude=S.STOCK_STRATEGIES))
    assert set(got) == set(S.STRATEGY_TEMPLATES) - set(S.STOCK_STRATEGIES)


def test_a_family_left_EMPTY_is_dropped_entirely():
    """⚠ All three stock structures share one family, so filtering them leaves an
    empty 'Stock + options' row — a submenu that opens onto nothing."""
    families = [f for f, _v in S.menu_families(exclude=S.STOCK_STRATEGIES)]
    assert "Stock + options" not in families


def test_a_PARTIALLY_excluded_family_keeps_its_other_variants():
    families = dict(S.menu_families(exclude=("COVERED_CALL",)))
    assert [c for _l, c in families["Stock + options"]] == ["PROTECTIVE_PUT",
                                                            "COLLAR"]


def test_excluding_everything_yields_an_empty_menu_rather_than_raising():
    assert S.menu_families(exclude=tuple(S.STRATEGY_TEMPLATES)) == []


@pytest.mark.parametrize("junk", [None, (), [], ("NOPE",), "COVERED_CALL"])
def test_an_unusable_exclusion_never_drops_a_real_strategy(junk):
    """⚠ A bare STRING is in here deliberately: ``exclude="COVERED_CALL"``
    iterates as characters, and a substring test would then drop everything.
    Membership is by exact code."""
    got = _codes(S.menu_families(exclude=junk))
    assert set(got) == set(S.STRATEGY_TEMPLATES)


def test_the_filter_does_not_MUTATE_the_menu():
    """It is module-level shared state read by three mounts."""
    before = [(f, list(v)) for f, v in S.STRATEGY_MENU]
    S.menu_families(exclude=S.STOCK_STRATEGIES)
    assert [(f, list(v)) for f, v in S.STRATEGY_MENU] == before


# ── the picker honours it ─────────────────────────────────────────────────

def test_build_strategy_menu_accepts_an_exclude_argument():
    assert "exclude" in inspect.signature(
        strategy_menu.build_strategy_menu).parameters


def test_the_picker_reads_menu_families_rather_than_the_raw_table():
    """The source-level check: iterating ``S.STRATEGY_MENU`` directly inside the
    builder would make the parameter inert while the signature advertised it."""
    src = inspect.getsource(strategy_menu.StrategyMenu)
    assert "menu_families" in src
    assert "S.STRATEGY_MENU" not in src, (
        "the picker must go through menu_families() or exclude= does nothing")


# ── the two mounts that must exclude, and the one that must not ──────────

def test_the_SIMULATOR_excludes_the_stock_structures():
    from pages.options import simulator
    src = inspect.getsource(simulator.render)
    assert "STOCK_STRATEGIES" in src, (
        "the Simulator prices a ContractRow off the chain and has no share "
        "concept — it must not offer a structure holding stock")


def test_the_RESCUE_adhoc_form_excludes_the_stock_structures():
    from pages.options import rescue
    src = inspect.getsource(rescue)
    assert "STOCK_STRATEGIES" in src, (
        "the ad-hoc form BOOKS into the paper account, which holds shares in "
        "equity_lots and not in paper_positions")


def test_the_CALCULATOR_does_NOT_exclude_them():
    """⚠ The converse. D4 asks for covered call / protective put / collar
    ANALYSIS, and the Calculator is the analysis surface — gating it there would
    mean shipping the whole feature switched off."""
    from pages.options import calculator
    src = inspect.getsource(calculator.render)
    assert "STOCK_STRATEGIES" not in src


def test_the_calculators_flat_code_list_still_offers_them():
    """The dropdown's flat order comes from STRATEGY_GROUPS, a separate path
    from the cascading menu — both have to include them."""
    from pages.options import calculator
    assert set(S.STOCK_STRATEGIES) <= set(calculator.strategy_options())
