"""news_svc is wired everywhere a service must be: port, data dir, guards."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_news_has_a_service_port():
    import repo_paths
    assert repo_paths.SERVICE_PORTS["news"] == 8216
    assert repo_paths.SERVICE_URLS["news"] == "http://127.0.0.1:8216"


def test_news_db_lives_under_the_service_and_is_guarded():
    import repo_paths
    import conftest
    assert repo_paths.NEWS_DB == ROOT / "services" / "news_svc" / "data" / "news.db"
    assert repo_paths.NEWS_SVC_DATA in conftest._LIVE_DIRS


def test_news_data_is_backed_up():
    from tools import backup_local
    assert "services/news_svc/data" in backup_local.DATA_TREES


def _pins(path, pkg):
    """Lines of ``path`` that PIN ``pkg`` - anchored at the line start, so a
    mention in a comment or another package's note cannot satisfy the check."""
    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
    return [ln for ln in lines if re.match(rf"^{re.escape(pkg)}==", ln)]


def test_feedparser_is_locked():
    for pkg in ("feedparser", "sgmllib3k"):
        assert _pins("requirements.txt", pkg), pkg
        assert _pins("requirements.lock", pkg), (
            f"{pkg} missing from requirements.lock — prod would not install it")
