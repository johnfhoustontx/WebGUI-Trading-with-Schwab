# services/market_svc/tests/conftest.py
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def site_root(tmp_path, monkeypatch):
    """Every test's homepage folder is its own temp dir: refresh_read writes the
    weather card (site_weather), and no test may write into deploy/site/."""
    from services.market_svc import site_weather
    root = tmp_path / "site"
    root.mkdir()
    monkeypatch.setattr(site_weather, "SITE_ROOT", root)
    return root
