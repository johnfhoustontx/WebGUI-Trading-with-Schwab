"""Pytest fixtures shared across SentimentDashboard scoring tests."""
import json
import sys
from pathlib import Path

import pytest

# Ensure SentimentDashboard package root is importable. Tests live in
# SentimentDashboard/tests/, so the parent of that is the package root.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def bridge_v39_snapshot():
    """Load the pre-refactor bridge snapshot used as a regression oracle."""
    with open(FIXTURES_DIR / "bridge_v39_snapshot.json", "r", encoding="utf-8") as f:
        return json.load(f)
