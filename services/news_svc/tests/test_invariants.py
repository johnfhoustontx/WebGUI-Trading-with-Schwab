"""Source-level invariants of news_svc."""
import pathlib
import sys

SVC = pathlib.Path(__file__).resolve().parents[1]


def _modules():
    return [p for p in SVC.rglob("*.py") if "tests" not in p.parts]


def test_news_svc_never_calls_the_proxy():
    for p in _modules():
        src = p.read_text(encoding="utf-8")
        assert "proxy_client" not in src and "PROXY_URL" not in src and ":8100" not in src, p


def test_no_news_svc_module_shadows_the_stdlib():
    names = {p.stem for p in _modules()} - {"__init__"}
    assert not names & set(sys.stdlib_module_names)


def test_the_fred_key_is_read_only_from_the_environment():
    """``FRED_API_KEY`` is named only where the process environment is read."""
    hits = [p.name for p in _modules() if "FRED_API_KEY" in p.read_text(encoding="utf-8")]
    assert hits == ["econ_calendar.py"]
