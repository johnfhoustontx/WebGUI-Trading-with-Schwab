"""Tests for the weekly re-rank of the listed-stocks classification file.

The file is a roster a person curated: which rows it holds and what its own
columns say are theirs. The tool may only replace the columns it owns, so what
is pinned here is:

  * the arithmetic of the score and the two rankings;
  * that a re-rank replaces its own columns and touches nothing else;
  * that a run which could not get its data leaves the file as it was and says
    so with its exit code -- under a timer that is the only signal.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_rank_listed_stocks.py -v
"""
import csv

import pytest

from tools import rank_listed_stocks as R

BASE = ["Symbol", "Name", "Industry", "Sub_Industry", "Market_Cap_USD"]


def _row(symbol, industry="Banks", sub="Big", cap="0", name=None):
    return {"Symbol": symbol, "Name": name or symbol + " Inc", "Industry": industry,
            "Sub_Industry": sub, "Market_Cap_USD": cap}


def _fund(v3=0, v10=0, cap=0):
    return {"avg3MonthVolume": v3, "avg10DaysVolume": v10, "marketCap": cap}


def _by_symbol(rows):
    return {r["Symbol"]: r for r in rows}


# ---- percentiles -------------------------------------------------------------

def test_percentiles_are_mid_ranks_among_the_values_present():
    assert R.percentiles([10.0, 30.0, 20.0, 40.0]) == [12.5, 62.5, 37.5, 87.5]


def test_a_missing_value_scores_zero_and_is_not_counted_in_the_field():
    assert R.percentiles([0.0, 5.0, 0.0, 9.0]) == [0.0, 25.0, 0.0, 75.0]


def test_equal_values_share_one_percentile():
    assert R.percentiles([7.0, 7.0]) == [50.0, 50.0]


def test_percentiles_of_nothing_are_all_zero():
    assert R.percentiles([0.0, 0.0]) == [0.0, 0.0]


# ---- the score ---------------------------------------------------------------

def test_dollar_volume_is_share_volume_times_price():
    out = R.rank_rows([_row("AAA")], {"AAA": _fund(v3=1000)}, {"AAA": 12.5})
    assert out[0]["Avg_Volume"] == "1000"
    assert out[0]["Price_USD"] == "12.5"
    assert out[0]["Avg_Dollar_Volume_USD"] == "12500"


def test_the_three_month_volume_is_used_and_labelled():
    out = R.rank_rows([_row("AAA")], {"AAA": _fund(v3=300, v10=900)}, {"AAA": 1.0})
    assert (out[0]["Avg_Volume"], out[0]["Avg_Volume_Basis"]) == ("300", "3M")


def test_the_ten_day_volume_stands_in_when_there_is_no_three_month_figure():
    out = R.rank_rows([_row("AAA")], {"AAA": _fund(v3=0, v10=900)}, {"AAA": 1.0})
    assert (out[0]["Avg_Volume"], out[0]["Avg_Volume_Basis"]) == ("900", "10D")


def test_the_score_is_the_even_blend_of_the_two_percentiles():
    rows = [_row("BIG"), _row("SMALL")]
    fund = {"BIG": _fund(v3=100, cap=900), "SMALL": _fund(v3=10, cap=90)}
    out = _by_symbol(R.rank_rows(rows, fund, {"BIG": 1.0, "SMALL": 1.0}))
    assert out["BIG"]["Popularity_Score"] == "75.00"
    assert out["SMALL"]["Popularity_Score"] == "25.00"


def test_the_weight_moves_the_blend_toward_dollar_volume():
    """TRADED leads on dollar volume, OWNED on market cap."""
    rows = [_row("TRADED"), _row("OWNED")]
    fund = {"TRADED": _fund(v3=100, cap=90), "OWNED": _fund(v3=10, cap=900)}
    px = {"TRADED": 1.0, "OWNED": 1.0}
    even = _by_symbol(R.rank_rows(rows, fund, px))
    assert even["TRADED"]["Popularity_Score"] == even["OWNED"]["Popularity_Score"]
    tilted = _by_symbol(R.rank_rows(rows, fund, px, volume_weight=0.8))
    assert tilted["TRADED"]["Popularity_Score"] == "65.00"
    assert tilted["OWNED"]["Popularity_Score"] == "35.00"


def test_a_symbol_with_no_data_keeps_its_row_and_scores_zero():
    out = _by_symbol(R.rank_rows([_row("AAA"), _row("GONE")],
                                 {"AAA": _fund(v3=10, cap=10)}, {"AAA": 1.0}))
    gone = out["GONE"]
    assert gone["Popularity_Score"] == "0.00"
    assert (gone["Avg_Volume"], gone["Price_USD"], gone["Avg_Dollar_Volume_USD"],
            gone["Market_Cap_Used_USD"]) == ("", "", "", "")
    assert gone["Industry_Rank"] == "2"


def test_a_number_that_is_not_usable_counts_as_missing():
    fund = {"AAA": {"avg3MonthVolume": float("nan"), "avg10DaysVolume": None,
                    "marketCap": "n/a"}}
    out = R.rank_rows([_row("AAA", cap="")], fund, {"AAA": float("inf")})
    assert out[0]["Popularity_Score"] == "0.00"
    assert out[0]["Avg_Volume"] == "" and out[0]["Market_Cap_Used_USD"] == ""


# ---- market cap --------------------------------------------------------------

def test_schwab_market_cap_is_used_before_the_files_own():
    out = R.rank_rows([_row("AAA", cap="500")], {"AAA": _fund(cap=700)}, {})
    assert out[0]["Market_Cap_Used_USD"] == "700"


def test_the_files_market_cap_fills_in_where_schwab_has_none():
    out = R.rank_rows([_row("AAA", cap="500")], {"AAA": _fund(cap=0)}, {})
    assert out[0]["Market_Cap_Used_USD"] == "500"


def test_the_file_can_be_put_first_instead():
    out = R.rank_rows([_row("AAA", cap="500"), _row("BBB", cap="0")],
                      {"AAA": _fund(cap=700), "BBB": _fund(cap=300)}, {},
                      market_cap="file")
    out = _by_symbol(out)
    assert out["AAA"]["Market_Cap_Used_USD"] == "500"
    assert out["BBB"]["Market_Cap_Used_USD"] == "300"


def test_the_files_own_market_cap_column_is_never_rewritten():
    out = R.rank_rows([_row("AAA", cap="500")], {"AAA": _fund(cap=700)}, {})
    assert out[0]["Market_Cap_USD"] == "500"


# ---- the two rankings --------------------------------------------------------

def _three():
    rows = [_row("LOW", "Banks", "Big"), _row("TOP", "Banks", "Small"),
            _row("MID", "Banks", "Big"), _row("SOLO", "Mines", "Small")]
    fund = {"LOW": _fund(v3=10, cap=10), "MID": _fund(v3=20, cap=20),
            "TOP": _fund(v3=30, cap=30), "SOLO": _fund(v3=5, cap=5)}
    return rows, fund, {s: 1.0 for s in fund}


def test_rank_one_is_the_highest_score_in_the_industry():
    out = _by_symbol(R.rank_rows(*_three()))
    assert [out[s]["Industry_Rank"] for s in ("TOP", "MID", "LOW")] == ["1", "2", "3"]
    assert {out[s]["Industry_Count"] for s in ("TOP", "MID", "LOW")} == {"3"}
    assert (out["SOLO"]["Industry_Rank"], out["SOLO"]["Industry_Count"]) == ("1", "1")


def test_the_sub_industry_is_ranked_on_its_own_not_inside_the_industry():
    """TOP (Banks) and SOLO (Mines) share the sub-industry "Small"."""
    out = _by_symbol(R.rank_rows(*_three()))
    assert (out["TOP"]["Sub_Industry_Rank"], out["TOP"]["Sub_Industry_Count"]) == ("1", "2")
    assert (out["SOLO"]["Sub_Industry_Rank"], out["SOLO"]["Sub_Industry_Count"]) == ("2", "2")
    assert (out["MID"]["Sub_Industry_Rank"], out["LOW"]["Sub_Industry_Rank"]) == ("1", "2")


def test_a_row_with_no_group_gets_no_rank_in_that_grouping():
    out = R.rank_rows([_row("AAA", industry=" ", sub="")], {"AAA": _fund(v3=1, cap=1)},
                      {"AAA": 1.0})
    assert (out[0]["Industry_Rank"], out[0]["Industry_Count"]) == ("", "")
    assert (out[0]["Sub_Industry_Rank"], out[0]["Sub_Industry_Count"]) == ("", "")


def test_equal_scores_are_ranked_by_market_cap():
    """TRADED leads on dollar volume, OWNED on market cap: both score 50."""
    rows = [_row("TRADED"), _row("OWNED")]
    fund = {"TRADED": _fund(v3=100, cap=90), "OWNED": _fund(v3=10, cap=900)}
    out = _by_symbol(R.rank_rows(rows, fund, {"TRADED": 1.0, "OWNED": 1.0}))
    assert (out["OWNED"]["Industry_Rank"], out["TRADED"]["Industry_Rank"]) == ("1", "2")


def test_rows_with_nothing_to_tell_them_apart_are_ranked_by_symbol():
    out = _by_symbol(R.rank_rows([_row("ZED"), _row("ABC"), _row("MID")], {}, {}))
    assert [out[s]["Industry_Rank"] for s in ("ABC", "MID", "ZED")] == ["1", "2", "3"]


# ---- what a re-rank may touch ------------------------------------------------

def test_row_order_and_the_files_own_columns_are_left_alone():
    rows, fund, px = _three()
    out = R.rank_rows(rows, fund, px)
    assert [r["Symbol"] for r in out] == [r["Symbol"] for r in rows]
    for before, after in zip(rows, out):
        assert {k: after[k] for k in before} == before


def test_ranking_twice_replaces_the_rank_columns_instead_of_adding_more():
    rows, fund, px = _three()
    once = R.rank_rows(rows, fund, px)
    fund["LOW"] = _fund(v3=999, cap=999)
    twice = R.rank_rows(once, fund, px)
    assert list(twice[0]) == list(once[0])
    assert _by_symbol(twice)["LOW"]["Industry_Rank"] == "1"


def test_the_input_rows_are_not_modified():
    rows, fund, px = _three()
    R.rank_rows(rows, fund, px)
    assert all(list(r) == BASE for r in rows)


# ---- fetching ----------------------------------------------------------------

class Proxy:
    """Stands in for the proxy: records each request, answers from a table."""

    def __init__(self, instruments=None, quotes=None, fail=()):
        self.calls, self.fail = [], set(fail)
        self.instruments, self.quotes = instruments or {}, quotes or {}

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if len(self.calls) in self.fail:
            return None
        if path == "/instruments":
            asked = params["symbol"].split(",")
            return {"instruments": [{"symbol": s, "fundamental": self.instruments[s]}
                                    for s in asked if s in self.instruments]}
        asked = params["symbols"].split(",")
        return {s: {"quote": self.quotes[s]} for s in asked if s in self.quotes}


def test_fundamentals_are_asked_for_in_batches():
    proxy = Proxy({s: _fund(v3=1) for s in "ABCDE"})
    got = R.fetch_fundamentals(list("ABCDE"), proxy, batch=2, pause=0, sleep=lambda s: None)
    assert [c[1]["symbol"] for c in proxy.calls] == ["A,B", "C,D", "E"]
    assert all(c[1]["projection"] == "fundamental" for c in proxy.calls)
    assert sorted(got) == list("ABCDE")


def test_a_class_share_is_asked_for_on_its_own():
    """The proxy spells BRK.B the way Schwab wants only when it is asked alone."""
    proxy = Proxy({s: _fund(v3=1) for s in ("A", "BRK.B", "C")})
    got = R.fetch_fundamentals(["A", "BRK.B", "C"], proxy, batch=10, pause=0,
                               sleep=lambda s: None)
    assert [c[1]["symbol"] for c in proxy.calls] == ["A,C", "BRK.B"]
    assert sorted(got) == ["A", "BRK.B", "C"]


def test_a_batch_that_fails_costs_only_its_own_symbols():
    proxy = Proxy({s: _fund(v3=1) for s in "ABCD"}, fail={1})
    got = R.fetch_fundamentals(list("ABCD"), proxy, batch=2, pause=0, sleep=lambda s: None)
    assert sorted(got) == ["C", "D"]


def test_calls_are_spaced_by_the_configured_pause():
    naps = []
    R.fetch_fundamentals(list("ABCD"), Proxy(), batch=2, pause=1.5, sleep=naps.append)
    assert naps == [1.5, 1.5]


def test_the_price_is_the_previous_close():
    proxy = Proxy(quotes={"A": {"closePrice": 10.0, "lastPrice": 11.0}})
    assert R.fetch_prices(["A"], proxy, batch=5, pause=0, sleep=lambda s: None) == {"A": 10.0}


def test_the_last_price_stands_in_when_there_is_no_close():
    proxy = Proxy(quotes={"A": {"closePrice": 0, "lastPrice": 11.0}, "B": {}})
    assert R.fetch_prices(["A", "B"], proxy, batch=5, pause=0, sleep=lambda s: None) == {"A": 11.0}


def test_the_error_entry_in_a_quote_reply_is_not_read_as_a_symbol():
    def proxy(path, params):
        return {"A": {"quote": {"closePrice": 2.0}}, "errors": {"invalidSymbols": ["B"]}}
    assert R.fetch_prices(["A", "B"], proxy, batch=5, pause=0, sleep=lambda s: None) == {"A": 2.0}


# ---- a whole run -------------------------------------------------------------

SETTINGS = {"blend": {"dollar_volume_weight": 0.5}, "inputs": {"market_cap": "schwab"},
            "fetch": {"fundamentals_batch": 2, "quotes_batch": 2, "pause_sec": 0.0},
            "safety": {"min_coverage": 0.9}}


@pytest.fixture
def roster(tmp_path, monkeypatch):
    path = tmp_path / "stocks.csv"
    rows = [_row("AAA", cap="100", name="Alpha, Inc."), _row("BBB", cap="200"),
            _row("CCC", "Mines", "Gold", cap="300")]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, BASE, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    monkeypatch.setattr(R, "load_config", lambda: SETTINGS)
    return path


def _proxy_for(symbols):
    return Proxy({s: _fund(v3=10 * (i + 1), cap=1000 * (i + 1)) for i, s in enumerate(symbols)},
                 {s: {"closePrice": 2.0} for s in symbols})


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _run(path, proxy, **kw):
    return R.run(path, get=proxy, sleep=lambda s: None, log=lambda *_: None, **kw)


def test_a_run_writes_the_ranks_into_the_file(roster):
    assert _run(roster, _proxy_for(["AAA", "BBB", "CCC"])) == 0
    rows = _read(roster)
    assert list(rows[0]) == BASE + list(R.RANK_COLUMNS)
    got = _by_symbol(rows)
    assert (got["BBB"]["Industry_Rank"], got["AAA"]["Industry_Rank"]) == ("1", "2")
    assert got["AAA"]["Avg_Dollar_Volume_USD"] == "20"


def test_a_run_keeps_the_files_own_text_and_line_endings(roster):
    before = roster.read_bytes().split(b"\n")
    _run(roster, _proxy_for(["AAA", "BBB", "CCC"]))
    after = roster.read_bytes()
    assert b"\r" not in after and after.endswith(b"\n")
    for old, new in zip(before, after.split(b"\n")):
        assert new.startswith(old)
    assert _by_symbol(_read(roster))["AAA"]["Name"] == "Alpha, Inc."


def test_a_second_run_does_not_grow_the_file(roster):
    _run(roster, _proxy_for(["AAA", "BBB", "CCC"]))
    first = roster.read_bytes()
    _run(roster, _proxy_for(["AAA", "BBB", "CCC"]))
    assert roster.read_bytes() == first


def test_a_run_short_of_data_leaves_the_file_alone_and_fails(roster):
    """Two of three symbols is under the 90% floor: a proxy that stumbled, or a
    Schwab sign-in that lapsed, must not blank the rankings."""
    before = roster.read_bytes()
    assert _run(roster, _proxy_for(["AAA", "BBB"])) == 1
    assert roster.read_bytes() == before
    assert not list(roster.parent.glob("*.tmp"))


def test_prices_are_held_to_the_same_floor_as_fundamentals(roster):
    proxy = _proxy_for(["AAA", "BBB", "CCC"])
    proxy.quotes = {}
    before = roster.read_bytes()
    assert _run(roster, proxy) == 1
    assert roster.read_bytes() == before


def test_the_floor_comes_from_the_settings(roster, monkeypatch):
    loose = dict(SETTINGS, safety={"min_coverage": 0.5})
    monkeypatch.setattr(R, "load_config", lambda: loose)
    assert _run(roster, _proxy_for(["AAA", "BBB"])) == 0


def test_the_batch_sizes_and_the_weight_come_from_the_settings(roster, monkeypatch):
    cfg = dict(SETTINGS, fetch={"fundamentals_batch": 3, "quotes_batch": 1, "pause_sec": 0.0},
               blend={"dollar_volume_weight": 1.0})
    monkeypatch.setattr(R, "load_config", lambda: cfg)
    proxy = _proxy_for(["AAA", "BBB", "CCC"])
    _run(roster, proxy)
    assert [p for p, _ in proxy.calls] == ["/instruments"] + ["/quotes"] * 3
    # Weight 1.0: the score is the dollar-volume percentile alone (3 symbols).
    assert _by_symbol(_read(roster))["CCC"]["Popularity_Score"] == "83.33"


def test_a_dry_run_fetches_and_writes_nothing_to_the_file(roster):
    before = roster.read_bytes()
    proxy = _proxy_for(["AAA", "BBB", "CCC"])
    assert _run(roster, proxy, dry_run=True) == 0
    assert proxy.calls and roster.read_bytes() == before


def test_a_missing_file_fails_without_asking_the_proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "load_config", lambda: SETTINGS)
    proxy = Proxy()
    assert _run(tmp_path / "nope.csv", proxy) == 1
    assert proxy.calls == []


def test_with_no_path_under_pytest_the_run_is_skipped_not_pointed_at_the_live_file():
    proxy = Proxy()
    assert R.run(get=proxy, sleep=lambda s: None, log=lambda *_: None) == 0
    assert proxy.calls == []


# ---- the shipped settings ----------------------------------------------------

def test_the_shipped_settings_file_matches_the_built_in_defaults():
    import tomllib

    from repo_paths import LISTED_STOCKS_TOML
    with open(LISTED_STOCKS_TOML, "rb") as f:
        assert tomllib.load(f) == R.DEFAULTS


def test_the_settings_are_read_from_the_file(tmp_path, monkeypatch):
    from shared.config_toml import toml_loader
    path = tmp_path / "listed_stocks.toml"
    path.write_text("[safety]\nmin_coverage = 0.42\n", encoding="utf-8")
    load, _reset = toml_loader(path, R.DEFAULTS)
    assert load()["safety"]["min_coverage"] == 0.42
    assert load()["fetch"] == R.DEFAULTS["fetch"]
