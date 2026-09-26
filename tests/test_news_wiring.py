"""news_svc is wired everywhere a service must be: port, data dir, guards."""
import pathlib

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


def test_feedparser_is_locked():
    txt = (ROOT / "requirements.txt").read_text()
    lock = (ROOT / "requirements.lock").read_text()
    for pkg in ("feedparser", "sgmllib3k"):
        assert pkg in txt, pkg
        assert pkg in lock, f"{pkg} missing from requirements.lock — prod would not install it"
