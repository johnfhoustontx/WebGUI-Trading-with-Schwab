"""The engine folders are linted like everything else (audit CQ-08).

They were excluded as "copied verbatim from the source monorepo", but they are
the most actively changed code in the repo and they pass the configured rules.
An excluded folder is one where an undefined name ships.
"""
import pathlib
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
ENGINES = ("options-scanner", "sentiment-dashboard", "trade-analyzer",
           "portfolio-analyzer")


def _excluded():
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    ruff = cfg["tool"]["ruff"]
    return list(ruff.get("extend-exclude", [])) + list(ruff.get("exclude", []))


def test_no_engine_folder_is_excluded_from_lint():
    assert [e for e in _excluded() if e.strip("/*") in ENGINES] == []


def test_the_engine_folders_are_still_there_to_lint():
    for name in ENGINES:
        assert (REPO / name).is_dir(), name


def _lint():
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return cfg["tool"]["ruff"]["lint"]


def test_unused_imports_and_redefinitions_are_checked():
    # Audit CQ-08, second half. A redefinition is how one test came to shadow
    # another of the same name, which then never ran.
    assert {"F401", "F811"} <= set(_lint()["select"])


def test_neither_rule_is_fixed_automatically():
    """The editor hook runs ``ruff --fix`` on every edited file. Fixed
    automatically, F401 would delete an import the moment it was added (before
    the line that uses it), and a re-export that looks unused; F811 would
    delete the second of two same-named tests. Both are reported instead."""
    assert {"F401", "F811"} <= set(_lint().get("unfixable", []))
