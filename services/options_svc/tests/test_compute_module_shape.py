"""compute.py stops growing, and code leaves it behind stable names.

Audit CQ-03 (2026-10-03): ``services/options_svc/compute.py`` was 10,548 lines
and growing about 1,000 a month, with tests patching it by attribute name at
roughly 495 sites. Splitting it is a long job done in slices; these tests hold
the two rules each slice follows.

1. A piece that moves out keeps its name on ``compute`` (a re-export), so every
   caller and every ``monkeypatch.setattr(compute, ...)`` on a name compute
   itself CALLS keeps working.
2. The file may shrink and may not grow. New code goes in a sibling module.
"""
import pathlib

from services.options_svc import collection_tiers as tiers_mod
from services.options_svc import compute

COMPUTE = pathlib.Path(compute.__file__)

# Lines in compute.py. LOWER this when code moves out; never raise it. To add
# behaviour, write it in a sibling module under services/options_svc/ and import
# it into compute only if compute's own code calls it.
COMPUTE_MAX_LINES = 10550


def test_compute_does_not_grow():
    lines = len(COMPUTE.read_text(encoding="utf-8").splitlines())
    assert lines <= COMPUTE_MAX_LINES, (
        f"compute.py is {lines} lines, over its ceiling of {COMPUTE_MAX_LINES}. "
        "Put new code in a sibling module (see collection_tiers.py).")


def test_the_ceiling_is_kept_close_to_the_file():
    """A ceiling far above the file is no ceiling. Lower it after moving code."""
    lines = len(COMPUTE.read_text(encoding="utf-8").splitlines())
    assert COMPUTE_MAX_LINES - lines <= 150, (
        f"compute.py is {lines} lines; lower COMPUTE_MAX_LINES to about {lines + 20}.")


def test_the_collector_tiers_live_in_their_own_module():
    assert tiers_mod.collection_tiers.__module__ == tiers_mod.__name__
    assert tiers_mod.flip_alert_symbols.__module__ == tiers_mod.__name__


def test_compute_still_offers_the_moved_names():
    assert compute.collection_tiers is tiers_mod.collection_tiers
    assert compute._flip_alert_symbols is tiers_mod.flip_alert_symbols
    assert compute.MAX_TAIL_INTERVAL_MIN == tiers_mod.MAX_TAIL_INTERVAL_MIN


def test_the_tiers_module_does_not_import_compute():
    """The point of moving it: it can be read, tested and changed alone."""
    import ast
    tree = ast.parse(pathlib.Path(tiers_mod.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    assert not any(n.endswith("compute") for n in names), names


def test_time_to_expiry_lives_in_its_own_module():
    from services.options_svc import expiry_time
    assert compute.time_to_expiry_years is expiry_time.time_to_expiry_years
    assert compute._leg_days_to_expiry is expiry_time._leg_days_to_expiry
    assert compute._year_fraction is expiry_time._year_fraction
    import ast
    tree = ast.parse(pathlib.Path(expiry_time.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported <= {"datetime", "zoneinfo"}, imported
