"""Tests for the Trade Analyzer's read of the listed-stocks classification file.

The file is a roster a person curated and a weekly tool ranks; this reader only
looks things up in it. What is worth pinning is where a lookup could quietly
lie:

  * the file lives only on the server (gitignored), so every test here writes
    its own, and a read with no path given is skipped under pytest;
  * a symbol the file does not list is ABSENT, never a blank classification;
  * an industry nobody has ranked yet has no leaders, rather than five rows in
    file order presented as a ranking;
  * the count of leaders is the operator's, from config/listed_stocks.toml.
"""
import csv
import os

import pytest

from services.trade_svc import listed_stocks as ls
from shared import listed_stocks_config

COLUMNS = ["Symbol", "Name", "Sector", "Industry", "Sub_Industry",
           "Market_Cap_USD", "Market_Cap_Used_USD", "Industry_Rank",
           "Industry_Count"]


def _row(symbol, name, rank, cap="", *, sector="Technology",
         industry="Semiconductors", sub="Semiconductors & Related Devices",
         count="7", own_cap=""):
    return {"Symbol": symbol, "Name": name, "Sector": sector,
            "Industry": industry, "Sub_Industry": sub,
            "Market_Cap_USD": own_cap, "Market_Cap_Used_USD": cap,
            "Industry_Rank": rank, "Industry_Count": count}


# Deliberately NOT in rank order: the file's row order is the curator's, and a
# reader that took the first five rows would pass on a file sorted by rank.
ROWS = [
    _row("SYNA", "Synaptics Incorporated Common Stock $0.001 Par Value", "6", "3000000000"),
    _row("AMD", "Advanced Micro Devices Inc. Common Stock", "4", "400000000000"),
    _row("NVDA", "NVIDIA Corporation Common Stock", "1", "5525648000000"),
    _row("TSM", "Taiwan Semiconductor Manufacturing Company Ltd.", "5", "1200000000000"),
    _row("MU", "Micron Technology Inc. Common Stock", "2", "900000000000"),
    _row("AVGO", "Broadcom Inc. Common Stock", "3", "1500000000000"),
    _row("ADI", "Analog Devices Inc. Common Stock", "7", "120000000000"),
    _row("JPM", "JP Morgan Chase & Co. Common Stock", "1", "885149421073",
         sector="Finance", industry="Major Banks",
         sub="National Commercial Banks", count="1"),
    _row("NOIND", "No Industry Corp. Common Stock", "", "1000000",
         sector="Miscellaneous", industry="", sub="", count=""),
]


def _write(path, rows, columns=COLUMNS):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture(autouse=True)
def _fresh():
    ls.reset_cache()
    listed_stocks_config.reset_config()
    yield
    ls.reset_cache()
    listed_stocks_config.reset_config()


@pytest.fixture
def roster(tmp_path):
    return _write(tmp_path / "listed.csv", ROWS)


class TestTheThreeClassifications:
    def test_they_are_the_files_own_columns(self, roster):
        block = ls.classification("NVDA", csv_path=roster)
        assert block["sector"] == "Technology"
        assert block["industry"] == "Semiconductors"
        assert block["sub_industry"] == "Semiconductors & Related Devices"

    def test_the_symbols_own_name_travels_with_them(self, roster):
        assert ls.classification("MU", csv_path=roster)["name"] == \
            "Micron Technology Inc."

    def test_the_lookup_ignores_case_and_padding(self, roster):
        assert ls.classification(" nvda ", csv_path=roster)["industry"] == \
            "Semiconductors"

    def test_a_symbol_the_file_does_not_list_is_absent(self, roster):
        assert ls.classification("ZZZZ", csv_path=roster) is None
        assert ls.classification("", csv_path=roster) is None
        assert ls.classification(None, csv_path=roster) is None

    def test_a_row_with_no_industry_keeps_its_sector_and_has_no_leaders(self, roster):
        block = ls.classification("NOIND", csv_path=roster)
        assert block["sector"] == "Miscellaneous"
        assert block["industry"] == "" and block["sub_industry"] == ""
        assert block["leaders"] == []
        assert block["industry_rank"] is None and block["industry_count"] is None


class TestTheIndustryLeaders:
    def test_they_are_the_top_ranks_in_rank_order(self, roster):
        block = ls.classification("ADI", csv_path=roster, top=5)
        assert [(r["rank"], r["symbol"]) for r in block["leaders"]] == [
            (1, "NVDA"), (2, "MU"), (3, "AVGO"), (4, "AMD"), (5, "TSM")]

    def test_only_the_symbols_own_industry_is_ranked(self, roster):
        block = ls.classification("JPM", csv_path=roster, top=5)
        assert [r["symbol"] for r in block["leaders"]] == ["JPM"]

    def test_the_symbols_own_place_travels_with_them(self, roster):
        block = ls.classification("ADI", csv_path=roster, top=5)
        assert block["industry_rank"] == 7 and block["industry_count"] == 7

    def test_a_leader_carries_a_trimmed_name_and_a_market_cap(self, roster):
        first = ls.classification("ADI", csv_path=roster)["leaders"][0]
        assert first["name"] == "NVIDIA Corporation"
        assert first["market_cap"] == 5525648000000.0

    def test_the_files_own_cap_stands_in_where_the_tool_wrote_none(self, tmp_path):
        path = _write(tmp_path / "f.csv", [
            _row("AAA", "Aaa Inc. Common Stock", "1", "", own_cap="42")])
        assert ls.classification("AAA", csv_path=path)["leaders"][0]["market_cap"] == 42.0

    def test_no_cap_at_all_is_absent_not_zero(self, tmp_path):
        path = _write(tmp_path / "f.csv", [
            _row("AAA", "Aaa Inc. Common Stock", "1", "")])
        assert ls.classification("AAA", csv_path=path)["leaders"][0]["market_cap"] is None

    def test_an_unranked_industry_has_no_leaders(self, tmp_path):
        """Before the first Saturday run the rank columns are empty. File order
        is the curator's, and five rows in that order are not a ranking."""
        rows = [_row(r["Symbol"], r["Name"], "", r["Market_Cap_Used_USD"])
                for r in ROWS[:7]]
        path = _write(tmp_path / "f.csv", rows)
        block = ls.classification("NVDA", csv_path=path)
        assert block["industry"] == "Semiconductors"
        assert block["leaders"] == [] and block["industry_rank"] is None

    def test_a_file_without_the_rank_columns_still_classifies(self, tmp_path):
        columns = ["Symbol", "Name", "Sector", "Industry", "Sub_Industry"]
        path = _write(tmp_path / "f.csv", [
            {"Symbol": "AAA", "Name": "Aaa Inc.", "Sector": "Energy",
             "Industry": "Oil", "Sub_Industry": "Crude"}], columns)
        block = ls.classification("AAA", csv_path=path)
        assert (block["sector"], block["industry"], block["sub_industry"]) == \
            ("Energy", "Oil", "Crude")
        assert block["leaders"] == []


class TestTheCountIsTheOperators:
    def test_the_shipped_count_is_five(self):
        assert ls.industry_top() == 5

    def test_the_count_is_read_from_the_config(self, roster, monkeypatch):
        monkeypatch.setattr(ls, "load_config",
                            lambda: {"display": {"industry_top": 3}})
        block = ls.classification("ADI", csv_path=roster)
        assert [r["symbol"] for r in block["leaders"]] == ["NVDA", "MU", "AVGO"]

    @pytest.mark.parametrize("bad", [0, -2, "many", None, True, 2.5])
    def test_a_count_that_is_not_a_whole_number_above_zero_is_five(
            self, monkeypatch, bad):
        monkeypatch.setattr(ls, "load_config",
                            lambda: {"display": {"industry_top": bad}})
        assert ls.industry_top() == 5

    def test_the_shipped_file_carries_the_key(self):
        import tomllib
        from repo_paths import LISTED_STOCKS_TOML
        with open(LISTED_STOCKS_TOML, "rb") as f:
            assert tomllib.load(f)["display"]["industry_top"] == 5


class TestTheFile:
    def test_a_missing_file_is_absent_and_does_not_raise(self, tmp_path):
        assert ls.classification("NVDA", csv_path=tmp_path / "nope.csv") is None

    def test_a_file_with_no_symbol_column_is_absent(self, tmp_path):
        path = tmp_path / "f.csv"
        path.write_text("Ticker,Name\nNVDA,NVIDIA\n", encoding="utf-8")
        assert ls.classification("NVDA", csv_path=path) is None

    def test_no_path_under_pytest_never_reaches_the_live_file(self, monkeypatch):
        """The live file is gitignored: it exists on the server and on no other
        checkout, so a test that read it would pass in one place only."""
        def _boom(*_a, **_k):
            raise AssertionError("the live file was opened under pytest")
        monkeypatch.setattr(ls, "_table", _boom)
        assert ls.classification("NVDA") is None

    def test_the_file_is_read_once_until_it_changes(self, roster, monkeypatch):
        reads = []
        real = ls._read

        def _counting(path):
            reads.append(path)
            return real(path)
        monkeypatch.setattr(ls, "_read", _counting)
        ls.classification("NVDA", csv_path=roster)
        ls.classification("MU", csv_path=roster)
        assert len(reads) == 1

    def test_a_rewritten_file_is_read_again(self, roster):
        """The Saturday run replaces the file under a running service."""
        assert ls.classification("NVDA", csv_path=roster)["industry_rank"] == 1
        rows = [dict(r) for r in ROWS]
        for r in rows:
            if r["Symbol"] == "NVDA":
                r["Industry_Rank"] = "2"
            elif r["Symbol"] == "MU":
                r["Industry_Rank"] = "1"
        _write(roster, rows)
        stamp = os.stat(roster).st_mtime + 5
        os.utime(roster, (stamp, stamp))
        block = ls.classification("NVDA", csv_path=roster)
        assert block["industry_rank"] == 2
        assert block["leaders"][0]["symbol"] == "MU"


class TestTheDisplayName:
    @pytest.mark.parametrize("raw, shown", [
        ("Apple Inc. Common Stock", "Apple Inc."),
        ("Coinbase Global Inc. Class A Common Stock", "Coinbase Global Inc."),
        ("Synaptics Incorporated Common Stock $0.001 Par Value",
         "Synaptics Incorporated"),
        ("Accenture plc Class A Ordinary Shares (Ireland)", "Accenture plc"),
        ("Alibaba Group Holding Limited American Depositary Shares each "
         "representing eight Ordinary share", "Alibaba Group Holding Limited"),
        ("Some Bank common stock", "Some Bank"),
        ("Nokia Corporation Sponsored American Depositary Shares",
         "Nokia Corporation"),
        ("Cemex S.A.B. de C.V. Sponsored ADR", "Cemex S.A.B. de C.V."),
        ("Anheuser-Busch Inbev SA Sponsored ADR (Belgium)",
         "Anheuser-Busch Inbev SA"),
        ("Petroleo Brasileiro S.A. Petrobras ADS",
         "Petroleo Brasileiro S.A. Petrobras"),
        ("Alphabet Inc. Class C Capital Stock", "Alphabet Inc."),
        ("Brookfield Corporation Class A Limited Voting Shares",
         "Brookfield Corporation"),
        ("GFL Environmental Inc. Subordinate voting shares no par value",
         "GFL Environmental Inc."),
        ("ASML Holding N.V. New York Registry Shares", "ASML Holding N.V."),
        ("Arcelor Mittal NY Registry Shares NEW", "Arcelor Mittal"),
        ("Unifi Inc. New Common Stock", "Unifi Inc."),
        ("Boeing Company (The) Common Stock", "The Boeing Company"),
        ("Goldman Sachs Group Inc. (The) Common Stock",
         "The Goldman Sachs Group Inc."),
        # "Ads" inside a name is the company's, not a share class.
        ("Lamar Ads Media Inc. Common Stock", "Lamar Ads Media Inc."),
        ("News Corporation Class A Common Stock", "News Corporation"),
        ("Taiwan Semiconductor Manufacturing Company Ltd.",
         "Taiwan Semiconductor Manufacturing Company Ltd."),
        ("Berkshire Hathaway Inc.", "Berkshire Hathaway Inc."),
        ("", ""),
    ])
    def test_the_share_class_wording_is_dropped(self, raw, shown):
        assert ls.short_name(raw) == shown

    def test_a_name_that_is_only_share_class_wording_is_kept_whole(self):
        assert ls.short_name("Common Stock") == "Common Stock"
