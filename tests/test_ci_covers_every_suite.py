"""CI runs every suite this repo has, and none of them is optional (audit CQ-05).

The workflow left out ``services/news_svc``, ``shared/notify/tests``, ``tests/``,
``deploy/`` and the hook tests; ran the four engine suites as non-blocking; still
deselected tests that pass; and had no type-check job. A suite CI does not run
is a suite that goes red without anyone being told.

Text checks on purpose: PyYAML is not a dependency of this repo.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

# Every line of the workflow that runs a test command (comments left out).
CMDS = [line.strip() for line in CI.splitlines()
        if "python -m pytest" in line and not line.lstrip().startswith("#")]

# The folder each root-level row must run, as it appears in its command.
ROOT_SUITES = [
    "shared/bus", "shared/contracts", "shared/tests", "shared/notify/tests",
    "services/tests", "services/sentiment_svc", "services/options_svc",
    "services/portfolio_svc", "services/trade_svc", "services/market_svc",
    "services/news_svc", "tools/tests", "tests", "deploy", ".claude/hooks/tests",
]
APP_DIRS = ["webgui", "schwab-proxy", "options-scanner", "sentiment-dashboard",
            "trade-analyzer", "portfolio-analyzer"]


def _runs(folder):
    """True when some command runs pytest on exactly this folder."""
    pattern = re.compile(rf"python -m pytest {re.escape(folder)}(\s|$)")
    return any(pattern.search(cmd) for cmd in CMDS)


def test_every_root_suite_has_a_row():
    missing = [f for f in ROOT_SUITES if not _runs(f)]
    assert not missing, f"ci.yml runs no pytest command on: {missing}"


def test_every_app_folder_has_a_row():
    missing = [d for d in APP_DIRS if not re.search(rf"dir:\s*{re.escape(d)}\s*$", CI,
                                                    flags=re.M)]
    assert not missing, f"ci.yml has no row running from: {missing}"


def test_no_suite_is_optional():
    assert not re.search(r"^\s*soft:\s*true", CI, flags=re.M), (
        "a `soft: true` row cannot fail the build; every suite here passes")


def test_nothing_is_deselected_or_ignored():
    assert "--deselect" not in CI and "--ignore=" not in CI


def test_there_is_a_type_check_job():
    assert re.search(r"^\s*run:\s*python -m pyright\s*$", CI, flags=re.M)


def test_the_listed_suites_exist():
    """So a renamed folder fails here rather than passing on an empty run."""
    for folder in ROOT_SUITES + APP_DIRS:
        assert (REPO / folder).is_dir(), folder
