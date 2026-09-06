"""Constants that are DUPLICATED across tiers on purpose, pinned by source text.

Some values genuinely cannot be shared by import: the webgui takes no engine
imports (Tier 1), and two services cannot import each other or the
`sentiment-dashboard/scoring` package (the documented cross-app `scoring`
module-name collision). Where a config file is not the right home - display
WORDS are not operator-tunable - the copies stay, and the only thing keeping them
in step is discipline.

This module replaces that discipline with a test. It reads the files as TEXT and
AST-parses the constants out, importing nothing, so it is safe to run from
anywhere and cannot itself trigger a collision.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _const(rel_path, name):
    """A module-level literal constant, read WITHOUT importing the module."""
    path = ROOT / rel_path
    assert path.exists(), f"mirror source moved: {rel_path}"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    pytest.fail(f"{rel_path} no longer defines {name} - "
                "if it moved, this mirror test must move with it")


# --- the five market-regime display words -----------------------------------
# Renamed 2026-08-14 (Mean Reversion -> Balanced, Choppy -> Whipsaw, Volatile ->
# Stressed) while the internal KEYS stayed - they are the RegimeState contract,
# the regime_intraday DB columns and the driver packet. Root CLAUDE.md says
# "keep them in step" across four tiers; one rename in one tier used to drift
# silently until a user noticed a screen disagreeing with another.

REGIME_SOURCE = "sentiment-dashboard/scoring/market_regime.py"
REGIME_MIRRORS = [
    ("services/driver_svc/compute.py", "_REGIME_LABELS"),
    ("services/options_svc/market_console.py", "REGIME_LABELS"),
    ("webgui/pages/regime_mix.py", "REGIME_LABELS"),
]


def test_regime_display_words_agree_across_every_tier():
    source = _const(REGIME_SOURCE, "REGIME_DISPLAY")
    assert source, "the source dict is empty - the pin would be vacuous"
    for rel, name in REGIME_MIRRORS:
        assert _const(rel, name) == source, (
            f"{rel}:{name} has drifted from {REGIME_SOURCE}:REGIME_DISPLAY.\n"
            "The words are duplicated because these tiers cannot import each "
            "other; changing one means changing all of them.")


def test_the_regime_keys_are_the_documented_five():
    """Non-vacuity, and a guard on the OTHER half: the keys are a contract
    (RegimeState, the regime_intraday columns, the driver packet), so a key
    change is a migration, not a rename."""
    assert set(_const(REGIME_SOURCE, "REGIME_DISPLAY")) == {
        "mean_reversion", "trending", "breakout", "choppy", "crisis"}


def test_sentiment_svc_delegates_rather_than_copying():
    """sentiment_svc CAN import the scoring package, so it must not hold a
    fourth copy - it calls market_regime.regime_label. Recorded so nobody
    'helpfully' adds one for symmetry."""
    src = (ROOT / "services/sentiment_svc/compute.py").read_text(encoding="utf-8")
    assert "market_regime.regime_label(" in src
    assert "\"mean_reversion\": \"Balanced\"" not in src


# --- the covered-call identifier --------------------------------------------
# ONE string, "COVERED_CALL", in three tiers that cannot import each other:
#
#   services/options_svc/compute.py  COVERED_CALL_TYPE       - the scan-row TYPE
#   options-scanner/paper_engine.py  COVERED_CALL_STRATEGIES - the position
#                                                              STRATEGY it settles
#   webgui/pages/options/shares.py   COVERED_CALL_STRATEGIES - the position
#                                                              STRATEGY it displays
#
# ⚠ Those are two DIFFERENT FIELDS, and until ``compute.open_income_position``
# existed the mirror was not real: a scan row's ``type`` and a paper position's
# ``strategy`` merely happened to spell the same word, with nothing carrying one
# into the other. That function is the link — it stores ``strategy = row["type"]``
# — so the three now genuinely have to agree, and this is the test that says so.
# ``shares.py`` claimed to be "pinned by a test on both sides" for a while when
# no such test existed; writing it was cheaper than deleting the claim.

COVERED_CALL_WORD = "COVERED_CALL"


def test_the_covered_call_identifier_is_one_word_in_three_tiers():
    scan_type = _const("services/options_svc/compute.py", "COVERED_CALL_TYPE")
    engine = _const("options-scanner/paper_engine.py", "COVERED_CALL_STRATEGIES")
    page = _const("webgui/pages/options/shares.py", "COVERED_CALL_STRATEGIES")
    assert scan_type == COVERED_CALL_WORD, (
        "the scan-row type changed; the position strategy written by "
        "open_income_position changes with it, so both engine and page must move")
    assert tuple(engine) == (COVERED_CALL_WORD,)
    assert tuple(page) == (COVERED_CALL_WORD,)
    assert scan_type in engine and scan_type in page, (
        "open_income_position stores the scan row's TYPE as the position's "
        "STRATEGY, so a type the engine cannot recognise is a covered call that "
        "is never called away and a lot that can never leave the book.")


def test_the_openable_income_structures_are_the_two_single_leg_products():
    """Non-vacuity for the pin above, and a guard on the page's own gate.

    ``handoff.INCOME_OPENABLE_TYPES`` decides which rows GET an Open button and
    ``compute.INCOME_OPEN_STRUCTURES`` decides which the service will accept. A
    button on a row the service refuses is a dead control; a row the service
    accepts with no button is a feature nobody can reach.
    """
    page = tuple(_const("webgui/pages/options/handoff.py", "INCOME_OPENABLE_TYPES"))
    assert page == ("SHORT_PUT", COVERED_CALL_WORD)
    # The service side names the covered call through COVERED_CALL_TYPE rather
    # than a literal, so its tuple is not a literal AST node - read the source.
    src = (ROOT / "services/options_svc/compute.py").read_text(encoding="utf-8")
    assert 'INCOME_OPEN_STRUCTURES = ("SHORT_PUT", COVERED_CALL_TYPE)' in src


# --- the manuals dual registration ------------------------------------------
# A manual has to be registered in TWO places: docs/manuals/build_docs.py to be
# BUILT, and webgui/pages/manuals.py to be SERVED (that dict is also the path
# whitelist, so an unlisted file is refused rather than served). Root CLAUDE.md
# flags the trap. The existing webgui test checks catalog -> built file; this
# checks the CONVERSE, which was the unguarded half: a manual that is built but
# never listed is silently unreachable in the app.

def _manual_keys(rel_path):
    path = ROOT / rel_path
    assert path.exists(), f"manuals registry moved: {rel_path}"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "MANUALS" for t in node.targets):
            val = node.value
            if isinstance(val, ast.Dict):
                return {ast.literal_eval(k) for k in val.keys}
            if isinstance(val, (ast.List, ast.Tuple)):
                out = set()
                for el in val.elts:
                    lit = ast.literal_eval(el)
                    out.add(lit[0] if isinstance(lit, (tuple, list)) else lit)
                return out
    pytest.fail(f"{rel_path} no longer defines MANUALS")


def test_every_built_manual_is_also_served():
    built = _manual_keys("docs/manuals/build_docs.py")
    served = _manual_keys("webgui/pages/manuals.py")
    assert built, "no manuals found - the pin would be vacuous"
    unreachable = built - served
    assert not unreachable, (
        f"built but NOT listed in webgui/pages/manuals.py: {sorted(unreachable)}. "
        "That dict is the serving whitelist, so these are unreachable in the app.")


def test_every_served_manual_is_also_built():
    built = _manual_keys("docs/manuals/build_docs.py")
    served = _manual_keys("webgui/pages/manuals.py")
    orphans = served - built
    assert not orphans, (
        f"served but never BUILT: {sorted(orphans)} - the page would 404.")
