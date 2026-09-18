"""pages/structure.py — the structure bar's shared geometry.

Its behaviour is pinned by the seven ``test_structure_positions_*`` tests in
``test_desk.py``, which reach it through the Desk's re-export. This file pins
only that the re-export IS the shared function: a stale copy left in
``desk.py`` would keep those seven green while the Symbol Dossier drifted.
"""
from pages import desk, structure


def test_desk_re_exports_the_shared_function_not_a_copy():
    assert desk.structure_positions is structure.structure_positions
