"""The repo-root conftest must reach EVERY suite, or its guards are decoration.

Both suite-wide guards live in the repo-root ``conftest.py``: the SQLite one
(no test may open a live store) and the network one (no test may reach a real
HTTP server). pytest only loads conftests from ``rootdir`` downward, and
``rootdir`` is the nearest directory holding a pytest CONFIG — so a sub-folder
that grows its own config silently moves ``rootdir`` into itself and **both
guards stop applying to that suite, while every test there stays green.**

⚠ That had already happened. ``trade-analyzer/pytest.ini``, copied in with the
backend on day one of this repo, made ``rootdir`` = ``trade-analyzer/``. Measured
2026-09-12: in that suite ``sqlite3.connect`` and ``HTTPAdapter.send`` were both
the REAL functions — the SQLite guard, which CLAUDE.md documented as covering
per-app runs, had never covered it. Removing the file changed the collected set
by nothing (406 node IDs before and after, compared by set, not by count); it
only moved ``rootdir`` back and switched both guards on. That fix sat on two
side branches and never merged, so it was all still true when it was
re-measured on ``main`` on 2026-10-06: the same real functions, and 442 node IDs
before and after the removal.

Nothing was leaking there — an inventory of every outbound request across all 18
suites found none from trade-analyzer — which is exactly why this has to be a
test rather than a memory: the next such file will be just as quiet.
"""
import configparser
import os
import pathlib
import sys
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Never a suite of ours.
_SKIP = {".venv", ".git", "node_modules", "__pycache__",
         ".pytest_cache", ".ruff_cache", "site-packages"}
_CONFIG_NAMES = {"pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"}


def _candidate_files():
    """Config-named files below the root, never descending into another checkout.

    A main checkout holds nested git worktrees (``.claude/worktrees/*``), each a
    full copy of the repo with its own configs. They are recognised by their own
    ``.git`` entry, NOT by skipping ``.claude`` wholesale: ``.claude/hooks/tests``
    is one of the CI suites, and a config dropped there must be caught.
    """
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        here = pathlib.Path(dirpath)
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP and not (here / d / ".git").exists()]
        if here == _ROOT:                        # the root's own config is THE config
            continue
        for name in filenames:
            if name in _CONFIG_NAMES:
                yield here / name


def _walk_configs():
    """Every file below the root that pytest would treat as a CONFIG.

    Mirrors pytest's rootdir rules precisely, so it cannot fire on a file that
    does not move ``rootdir``: a ``pytest.ini`` always counts (even empty), the
    other three only when they carry a pytest section. A sub-folder
    ``conftest.py`` does NOT move ``rootdir`` and is fine.
    """
    found = []
    for path in _candidate_files():
        rel = path.relative_to(_ROOT)
        name = path.name
        if name == "pytest.ini":
            found.append(rel)
        elif name == "pyproject.toml":
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                continue
            if "ini_options" in data.get("tool", {}).get("pytest", {}):
                found.append(rel)
        elif name in ("tox.ini", "setup.cfg"):
            cp = configparser.ConfigParser()
            try:
                cp.read(path, encoding="utf-8")
            except (OSError, configparser.Error):
                continue
            section = "pytest" if name == "tox.ini" else "tool:pytest"
            if cp.has_section(section):
                found.append(rel)
    return sorted(found)


def test_no_subfolder_carries_a_pytest_config_that_moves_rootdir():
    found = _walk_configs()
    assert not found, (
        "these files move pytest's rootdir away from the repo root, which switches "
        "OFF the repo-root conftest guards (no live SQLite store, no real HTTP) "
        f"for every test beneath them: {[str(p) for p in found]}\n"
        "Put the setting in the root pyproject.toml, or in a sub-folder "
        "conftest.py, which does not move rootdir.")


def test_the_detector_catches_a_pytest_ini(tmp_path, monkeypatch):
    """Power check — the empty result above means nothing unless the walker can
    find the file it exists for. An EMPTY pytest.ini still moves rootdir."""
    (tmp_path / "some-app").mkdir()
    (tmp_path / "some-app" / "pytest.ini").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_ROOT", tmp_path)
    assert [str(p) for p in _walk_configs()] == [str(pathlib.Path("some-app/pytest.ini"))]


def test_the_detector_catches_a_pyproject_with_a_pytest_section(tmp_path, monkeypatch):
    app = tmp_path / "some-app"
    app.mkdir()
    (app / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-v"\n', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_ROOT", tmp_path)
    assert len(_walk_configs()) == 1


def test_the_detector_IGNORES_a_pyproject_with_no_pytest_section(tmp_path, monkeypatch):
    """A package's own pyproject (ruff, build metadata) does not move rootdir,
    and failing on it would train people to delete the test."""
    app = tmp_path / "some-app"
    app.mkdir()
    (app / "pyproject.toml").write_text('[tool.ruff]\nline-length = 100\n',
                                        encoding="utf-8")
    (app / "setup.cfg").write_text("[metadata]\nname = x\n", encoding="utf-8")
    (app / "conftest.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_ROOT", tmp_path)
    assert _walk_configs() == []


def test_the_detector_reaches_the_hooks_suite_but_not_a_nested_checkout(tmp_path, monkeypatch):
    """``.claude/hooks/tests`` is a CI suite; ``.claude/worktrees/<name>`` is a
    whole other checkout whose configs are that checkout's own business."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "pytest.ini").write_text("", encoding="utf-8")
    nested = tmp_path / ".claude" / "worktrees" / "some-branch"
    (nested / "trade-analyzer").mkdir(parents=True)
    (nested / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (nested / "trade-analyzer" / "pytest.ini").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_ROOT", tmp_path)
    assert [str(p) for p in _walk_configs()] == [str(pathlib.Path(".claude/hooks/pytest.ini"))]


def test_the_root_config_is_NOT_itself_flagged(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-rf"\n', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_ROOT", tmp_path)
    assert _walk_configs() == []
