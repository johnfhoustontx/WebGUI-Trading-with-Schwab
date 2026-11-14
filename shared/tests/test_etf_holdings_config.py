"""config/etf_holdings.toml - the Macro Board's ETF hover card lists."""
import pytest

from shared import etf_holdings_config as eh
from shared import symbols as shared_symbols


@pytest.fixture(autouse=True)
def _fresh():
    eh.reset_cache()
    yield
    eh.reset_cache()


def _with(monkeypatch, **over):
    """Patch the RAW loader, so a test proves the value is read, not merely equal."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in eh.DEFAULTS.items()}
    cfg.update(over)
    monkeypatch.setattr(eh, "load_raw", lambda: cfg)


# ── the shipped file ─────────────────────────────────────────────────────────
def test_shipped_file_covers_every_fund_in_the_four_frames():
    from services.market_svc import symbols as S
    frames = ("Broad Market", "Sectors", "Industries", "Countries")
    funds = {e["display"] for e in S.symbol_map() if e["category"] in frames}
    assert set(eh.load()["funds"]) == funds


def test_shipped_lists_are_full_and_clean():
    cfg = eh.load()
    assert cfg["as_of"], "the card prints the date the lists were checked"
    for fund, syms in cfg["funds"].items():
        if fund in cfg["notes"] and not syms:
            continue                          # a fund with no stocks (USO)
        assert len(syms) >= cfg["top_n"], fund
        for s in syms:
            assert shared_symbols.clean_symbol(s) == s, (fund, s)


def test_every_shipped_holding_has_a_name():
    cfg = eh.load()
    missing = {s for syms in cfg["funds"].values() for s in syms} - set(cfg["names"])
    assert not missing


# ── cleaning ─────────────────────────────────────────────────────────────────
def test_a_missing_file_is_switched_on_with_no_funds(monkeypatch):
    monkeypatch.setattr(eh, "load_raw", lambda: dict(eh.DEFAULTS))
    cfg = eh.load()
    assert cfg["enabled"] is True and cfg["funds"] == {} and cfg["top_n"] == 5


@pytest.mark.parametrize("value,want", [(False, False), (True, True), ("no", True)])
def test_only_a_real_false_switches_it_off(monkeypatch, value, want):
    _with(monkeypatch, enabled=value)
    assert eh.load()["enabled"] is want


def test_tickers_are_cleaned_deduplicated_and_kept_in_order(monkeypatch):
    _with(monkeypatch, funds={"xlk": ["nvda", "AAPL", "NVDA", "bad ticker", 7, "BRK.B"],
                              "not a fund": ["AAPL"]})
    assert eh.load()["funds"] == {"XLK": ["NVDA", "AAPL", "BRK.B"]}


def test_a_fund_with_no_stocks_survives_only_with_a_note(monkeypatch):
    _with(monkeypatch, funds={"USO": [], "XLE": []}, notes={"USO": "Oil futures"})
    cfg = eh.load()
    assert cfg["funds"] == {"USO": []} and cfg["notes"] == {"USO": "Oil futures"}


def test_a_note_alone_still_gives_the_fund_a_card(monkeypatch):
    _with(monkeypatch, funds={}, notes={"USO": "Oil futures"})
    assert eh.load()["funds"] == {"USO": []}


@pytest.mark.parametrize("key,value,want", [
    ("top_n", 3, 3), ("top_n", 0, 5), ("top_n", 11, 5), ("top_n", "4", 5),
    ("rth_refresh_sec", 30, 30), ("rth_refresh_sec", 1, 15),
    ("offhours_refresh_sec", 600, 600), ("offhours_refresh_sec", 5, 300),
    ("stale_after_h", 48, 48), ("stale_after_h", 0, 96),
])
def test_numbers_out_of_range_fall_back(monkeypatch, key, value, want):
    _with(monkeypatch, **{key: value})
    assert eh.load()[key] == want


def test_long_names_are_cut_and_blank_ones_dropped(monkeypatch):
    _with(monkeypatch, names={"AAPL": "x" * 200, "MSFT": "  ", "NVDA": 5})
    assert eh.load()["names"] == {"AAPL": "x" * eh.MAX_TEXT}
