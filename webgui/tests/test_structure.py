"""pages/structure.py — the structure bar's shared geometry.

Its behaviour is pinned by the seven ``test_structure_positions_*`` tests in
``test_desk.py``, which reach it through the Desk's re-export. This file pins
only that the re-export IS the shared function: a stale copy left in
``desk.py`` would keep those seven green while the Symbol Dossier drifted.
"""
from pages import desk, structure


def test_desk_re_exports_the_shared_function_not_a_copy():
    assert desk.structure_positions is structure.structure_positions


def test_desk_re_exports_the_moved_structure_vocabulary_not_copies():
    """The wall-trust rule, the flip read, the regime word and the drawn bar
    moved here when the Symbol Dossier became their second reader. A copy left
    behind in ``desk.py`` would keep every Desk test green while the two pages
    drifted apart — the scorecard move found exactly that kind of shadowing."""
    assert desk._walls_trustworthy is structure.walls_trustworthy
    assert desk._flip_read is structure.flip_read
    assert desk.regime_word is structure.regime_word
    assert desk.REGIME_WORDS is structure.REGIME_WORDS
    assert desk._NO_REGIME is structure.NO_REGIME
    assert desk._structure_map is structure.structure_map
    for name in ("CALL_HEX", "PUT_HEX", "FLIP_HEX", "SPOT_HEX"):
        assert getattr(desk, name) is getattr(structure, name), name


def test_the_dossier_uses_the_shared_structure_helpers():
    from pages import symbol
    src = __import__("inspect").getsource(symbol)
    for private in ("_desk._walls_trustworthy", "_desk._flip_read",
                    "_desk._structure_map", "_paper._dte_from_expiration"):
        assert private not in src, private


def test_the_paper_dte_helper_is_public_and_the_old_name_is_the_same_function():
    from pages.options import paper
    assert paper._dte_from_expiration is paper.dte_from_expiration
