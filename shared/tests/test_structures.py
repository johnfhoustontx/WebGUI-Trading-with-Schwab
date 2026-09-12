"""The structure taxonomy has ONE home.

Seven copies of these sets had spread across three tiers, and one of them
(``paper_adjust``'s put-side test) was wrong — see
docs/plans/2026-09-11-income-exit-rules-design.md §5. The tests that matter here
are the ones that would fail if a consumer went back to its own literal, and
those live beside each consumer; this file pins the vocabulary itself.
"""
import pytest

from shared import structures


# --- the sets --------------------------------------------------------------

def test_both_spellings_of_the_short_put_are_one_structure():
    """SHORT_PUT on the scan side, NAKED_PUT on the Calculator/rescue side. Both
    can sit in the paper book, so anything keyed on one must accept the other."""
    assert structures.is_short_put("SHORT_PUT")
    assert structures.is_short_put("NAKED_PUT")
    assert set(structures.SHORT_PUT) == {"SHORT_PUT", "NAKED_PUT"}


def test_the_income_structures_are_the_single_leg_set():
    assert set(structures.SINGLE_LEG) == {"SHORT_PUT", "NAKED_PUT", "COVERED_CALL"}


@pytest.mark.parametrize("strategy", ["PCS", "CCS", "IC", "VERT_CALL_DEBIT"])
def test_a_spread_is_not_single_leg(strategy):
    assert not structures.is_single_leg(strategy)


# --- the side test ---------------------------------------------------------
# This is the one that was wrong in paper_adjust: a short put resolved to the
# CALL side, so a roll would have been priced off the call chain.

@pytest.mark.parametrize("strategy", ["PCS", "IC", "SHORT_PUT", "NAKED_PUT"])
def test_put_side_structures(strategy):
    assert structures.is_put_side(strategy)


@pytest.mark.parametrize("strategy", ["CCS", "COVERED_CALL", "NAKED_CALL"])
def test_call_side_structures(strategy):
    assert not structures.is_put_side(strategy)


def test_an_iron_condor_counts_as_put_side():
    """It is both sides, but ``short_strike`` holds its PUT short — the field
    every proximity test reads — so the put side is the right answer here."""
    assert structures.is_put_side("IC")


def test_the_short_right_follows_the_side():
    assert structures.short_right("SHORT_PUT") == "PUT"
    assert structures.short_right("COVERED_CALL") == "CALL"
    assert structures.short_right("PCS") == "PUT"
    assert structures.short_right("CCS") == "CALL"


# --- the leg count ---------------------------------------------------------

def test_option_legs_counts_what_it_takes_to_close():
    assert structures.option_legs("SHORT_PUT") == 1
    assert structures.option_legs("NAKED_PUT") == 1
    assert structures.option_legs("COVERED_CALL") == 1
    assert structures.option_legs("PCS") == 2
    assert structures.option_legs("CCS") == 2
    assert structures.option_legs("IC") == 4


def test_an_unknown_structure_counts_as_a_spread():
    """The pre-existing default, kept deliberately: commissions are the consumer,
    and under-billing an unrecognised structure makes an action look cheaper than
    it is against the alternatives it is ranked against."""
    assert structures.option_legs("SOMETHING_NEW") == 2


# --- normalisation ---------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "   ", 0])
def test_absence_is_not_a_structure(value):
    assert not structures.is_single_leg(value)
    assert not structures.is_put_side(value)
    assert structures.option_legs(value) == 2


def test_case_and_whitespace_are_normalised():
    """``paper_engine`` upper-cased before testing and ``rescue`` did not, so the
    two disagreed about ``"short_put"``. One answer now."""
    assert structures.is_single_leg(" short_put ")
    assert structures.is_put_side("Short_Put")
    assert structures.option_legs("ic") == 4


# --- the canonical name ----------------------------------------------------
# The rule table is keyed on this, so the two spellings of the short put cannot
# be given different exit rules by someone editing one table and not the other.

def test_the_two_short_put_spellings_share_one_canonical_name():
    assert structures.canonical("NAKED_PUT") == "SHORT_PUT"
    assert structures.canonical("SHORT_PUT") == "SHORT_PUT"


def test_canonical_leaves_a_structure_with_one_spelling_alone():
    assert structures.canonical("COVERED_CALL") == "COVERED_CALL"
    assert structures.canonical("PCS") == "PCS"
    assert structures.canonical("ic") == "IC"


def test_canonical_of_nothing_is_the_empty_name():
    assert structures.canonical(None) == ""


# --- nobody may spell the side test out again -------------------------------
# The guard that matters. Four copies of ``strategy in ("PCS", "IC")`` had
# accumulated and ONE WAS WRONG - ``paper_adjust.apply_roll`` resolved a short
# put to ``right = "CALL"``. A behavioural test would only cover the copy it
# knows about; this one fails on the copy nobody has written yet.
#
# Source-level and AST-based on purpose: it imports none of the modules it reads,
# so it cannot trigger the documented cross-app ``scoring`` collision, and a
# tuple inside a comment or a docstring cannot trip it.

import ast          # noqa: E402 - the guard's own imports, kept beside it
import pathlib      # noqa: E402

_REPO = pathlib.Path(__file__).resolve().parents[2]
_SCANNED = ("options-scanner", "services", "shared")
_ALLOWED = {("shared", "structures.py")}


def _strings(node):
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set()
    return {e.value for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _structure_sets(tree):
    """Every literal set of strings that could BE one of these sets — both the
    inline ``x in (...)`` test and the assigned constant, with its line.

    Both shapes matter, and the assigned one is how the copies actually
    accumulated: five of the seven were module constants with a comment above
    them asking the next editor to keep the mirror in step.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, cmp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _strings(cmp):
                    yield node.lineno, _strings(cmp)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if _strings(node.value):
                yield node.lineno, _strings(node.value)


def _python_files():
    for folder in _SCANNED:
        for path in sorted((_REPO / folder).rglob("*.py")):
            rel = path.relative_to(_REPO)
            if "tests" in rel.parts or rel.parts[-2:] in _ALLOWED:
                continue
            yield rel, path


def test_no_module_spells_the_put_side_test_as_a_literal():
    """``shared.structures.is_put_side`` is the one answer."""
    offenders = []
    for rel, path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, names in _structure_sets(tree):
            if {"PCS", "IC"} <= names and "CCS" not in names:
                offenders.append(f"{rel}:{lineno} {sorted(names)}")
    assert not offenders, (
        "these spell the put-side membership test by hand; call "
        "shared.structures.is_put_side instead:\n  " + "\n  ".join(offenders))


def test_no_module_spells_the_single_leg_set_as_a_literal():
    """``is_single_leg`` / ``is_short_put`` / ``is_covered_call`` are the answers.
    ``webgui`` is deliberately out of scope: Tier 1 takes no ``services.*``
    import and widening its allow-list was not part of this change."""
    offenders = []
    for rel, path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, names in _structure_sets(tree):
            if {"SHORT_PUT", "NAKED_PUT"} <= names:
                offenders.append(f"{rel}:{lineno} {sorted(names)}")
    assert not offenders, (
        "these spell a structure set by hand; call the shared.structures "
        "predicates instead:\n  " + "\n  ".join(offenders))
