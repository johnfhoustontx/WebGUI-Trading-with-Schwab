import json
import math
import pathlib

from services.news_svc.adapters import edgar

FIX = pathlib.Path(__file__).parent / "fixtures"
NOW = "2026-09-26T00:00:00+00:00"


def test_current_filings_atom_yields_one_entry_per_accession():
    entries = edgar.parse_current((FIX / "edgar_current_4.xml").read_bytes())
    accessions = [e["accession"] for e in entries]
    assert accessions and len(accessions) == len(set(accessions))   # Reporting+Issuer rows collapse
    e = entries[0]
    assert e["index_url"].startswith("https://www.sec.gov/Archives/edgar/data/")
    assert e["form"] and e["company"] and e["cik"].isdigit()


def test_form4_purchase_is_parsed_into_detail():
    d = edgar.parse_form4((FIX / "form4_buy.xml").read_bytes())
    assert d["symbol"] == "LEN"
    assert d["insider"] and d["relationship"]
    assert d["total_value"] > 1_000_000
    assert all(g["code"] == "P" for g in d["groups"])


def test_form4_without_a_purchase_is_none():
    body = (FIX / "form4_buy.xml").read_bytes().replace(b"<transactionCode>P</transactionCode>",
                                                       b"<transactionCode>S</transactionCode>")
    assert edgar.parse_form4(body) is None


def test_form4_item_is_kept_for_a_tracked_ticker_or_a_big_buy():
    d = {"symbol": "ZZZ", "insider": "A", "relationship": "Director", "total_value": 5_000,
         "groups": [], "transaction_date": "2026-09-23", "company": "Z Corp"}
    feed = {"name": "SEC Insider Buys", "kind": "edgar_form4", "public": True, "min_value_usd": 1_000_000}
    assert edgar.form4_item(d, feed, "https://sec/x", NOW, universe=["ZZZ"]) is not None
    assert edgar.form4_item(d, feed, "https://sec/x", NOW, universe=[]) is None
    d["total_value"] = 2_000_000
    assert edgar.form4_item(d, feed, "https://sec/x", NOW, universe=[])["tickers"] == ["ZZZ"]


def test_form4_headline_reads_like_the_reference_feed():
    d = {"symbol": "LEN", "insider": "BERKSHIRE HATHAWAY INC", "relationship": "10% Owner",
         "total_value": 136_382_789.45, "groups": [{"shares": 1_659_025}], "company": "Lennar",
         "transaction_date": "2026-09-23"}
    feed = {"name": "SEC Insider Buys", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    it = edgar.form4_item(d, feed, "https://sec/x", NOW, universe=["LEN"])
    assert it["title"] == "LEN — BERKSHIRE HATHAWAY INC (10% Owner) bought $136.4M"
    assert it["topics"] == ["SEC Filing", "Insider Transaction"]


def test_cik_map_resolves_tickers():
    m = edgar.cik_map(json.loads((FIX / "company_tickers.json").read_text()))
    assert m["320193"] == "AAPL"


def test_filing_item_names_company_and_form():
    e = {"form": "S-3", "company": "CytoDyn Inc.", "cik": "1175680", "accession": "a",
         "index_url": "https://sec/i", "updated": "2026-09-25T21:08:19-04:00"}
    feed = {"name": "SEC Offerings", "kind": "edgar_filings", "public": True, "forms": ["S-3"]}
    it = edgar.filing_item(e, feed, NOW, cik_to_ticker={"1175680": "CYDY"})
    assert it["title"] == "CytoDyn Inc. files S-3 (shelf registration)"
    assert it["tickers"] == ["CYDY"] and it["topics"] == ["SEC Filing", "Offering"]


# --- beyond the plan: hardening -------------------------------------------

def test_current_filing_cik_has_no_leading_zeros_so_it_matches_cik_map():
    # The Atom title zero-pads the CIK ("(0001175680)"); company_tickers.json does not.
    entries = edgar.parse_current((FIX / "edgar_current_s3.xml").read_bytes())
    cyto = [e for e in entries if e["company"] == "CytoDyn Inc."]
    assert cyto and cyto[0]["cik"] == "1175680"
    m = edgar.cik_map(json.loads((FIX / "company_tickers.json").read_text()))
    assert edgar.filing_item(cyto[0], {"name": "SEC Offerings"}, NOW,
                             cik_to_ticker=m)["tickers"] == ["CYDY"]


def test_current_filings_issuer_row_wins():
    entries = edgar.parse_current((FIX / "edgar_current_4.xml").read_bytes())
    by_acc = {e["accession"]: e for e in entries}
    assert by_acc["0002045034-26-000009"]["role"] == "Issuer"
    assert by_acc["0002045034-26-000009"]["company"] == "GigaCloud Technology Inc"


def test_parse_current_never_raises_on_garbage():
    for body in (b"", b"not xml at all", b"<feed><entry><title>x</title></entry></feed>",
                 b"\xff\xfe\x00junk", None):
        assert edgar.parse_current(body) == []


def test_parse_form4_never_raises_on_garbage():
    for body in (b"", b"<<<", b"<ownershipDocument/>", None):
        assert edgar.parse_form4(body) is None


def test_form4_namespaced_document_still_parses():
    body = (FIX / "form4_buy.xml").read_bytes().replace(
        b"<ownershipDocument>",
        b'<ownershipDocument xmlns="http://www.sec.gov/edgar/ownership">', 1)
    d = edgar.parse_form4(body)
    assert d is not None and d["symbol"] == "LEN" and d["total_value"] > 1_000_000


def _one_purchase(shares, price):
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerName>Z Corp</issuerName><issuerTradingSymbol>zzz</issuerTradingSymbol></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerName>A B</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common</value></securityTitle>
      <transactionDate><value>2026-09-23</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common</value></securityTitle>
      <transactionDate><value>2026-09-24</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>10</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>""".encode()


def test_form4_nan_or_non_numeric_group_is_skipped_and_never_reaches_the_total():
    for shares, price in (("NaN", "10"), ("100", "nan"), ("inf", "10"),
                          ("lots", "10"), ("100", "see footnote")):
        d = edgar.parse_form4(_one_purchase(shares, price))
        assert d is not None, (shares, price)
        assert math.isfinite(d["total_value"]) and d["total_value"] == 1000.0
        assert len(d["groups"]) == 1 and d["groups"][0]["date"] == "2026-09-24"
        assert d["symbol"] == "ZZZ" and d["relationship"] == "Director"


def test_form4_all_groups_unusable_is_none():
    body = _one_purchase("NaN", "10").replace(
        b"<transactionShares><value>100</value>", b"<transactionShares><value>nan</value>")
    assert edgar.parse_form4(body) is None


def test_money_handles_zero_and_negatives():
    assert edgar._money(0) == "$0"
    assert edgar._money(-1_500_000) == "-$1.5M"
    assert edgar._money(12_000) == "$12K"
    assert edgar._money(999) == "$999"
    assert edgar._money(1_200_000_000) == "$1.2B"
    assert edgar._money(float("nan")) == "$0"


def test_form4_item_skips_without_a_url_and_survives_missing_fields():
    feed = {"name": "SEC Insider Buys"}
    assert edgar.form4_item({"symbol": "ZZZ", "total_value": 5e6}, feed, "", NOW,
                            universe=[]) is None
    assert edgar.form4_item({"symbol": "ZZZ", "total_value": 5e6}, feed, None, NOW,
                            universe=[]) is None
    it = edgar.form4_item({}, feed, "https://sec/x", NOW, universe=None)
    assert it is not None and it["tickers"] == [] and "bought $0" in it["title"]


def test_filing_item_skips_without_a_url_and_survives_missing_fields():
    feed = {"name": "SEC Offerings"}
    assert edgar.filing_item({"form": "S-3", "company": "X"}, feed, NOW,
                             cik_to_ticker={}) is None
    it = edgar.filing_item({"index_url": "https://sec/i"}, feed, NOW, cik_to_ticker=None)
    assert it is not None and it["tickers"] == [] and it["published_at"] == NOW


def test_xml_url_picks_the_ownership_xml():
    idx = {"directory": {"item": [{"name": "0001193125-26-403089-index.html"},
                                  {"name": "ownership.xml"}]}}
    assert (edgar.xml_url("https://www.sec.gov/Archives/edgar/data/1067983/"
                          "000119312526403089/0001193125-26-403089-index.htm", idx)
            == "https://www.sec.gov/Archives/edgar/data/1067983/000119312526403089/ownership.xml")
    assert edgar.xml_url("https://sec/a/b-index.htm", {}) is None
    assert edgar.xml_url("https://sec/a/b-index.htm", None) is None


# --- review fixes (7453a8b) -----------------------------------------------

def _entry(href, title="S-3 - Evil Co (0000000001) (Filer)"):
    return f"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>{title}</title>
<link rel="alternate" type="text/html" href="{href}"/>
<updated>2026-09-25T17:08:19-04:00</updated></entry>
</feed>""".encode()


def test_filing_item_published_at_is_utc_like_every_other_adapter():
    entries = edgar.parse_current((FIX / "edgar_current_s3.xml").read_bytes())
    cyto = [e for e in entries if e["company"] == "CytoDyn Inc."][0]
    assert cyto["updated"] == "2026-09-25T17:08:19-04:00"      # the SEC's own offset
    it = edgar.filing_item(cyto, {"name": "SEC Offerings"}, NOW, cik_to_ticker={})
    assert it["published_at"] == "2026-09-25T21:08:19+00:00"


def test_filing_item_bad_or_naive_time_falls_back_to_now():
    for updated in ("garbage", "2026-09-25T17:08:19", "", None, 12345):
        e = {"form": "S-3", "company": "X", "cik": "1", "accession": "a",
             "index_url": "https://sec/i", "updated": updated}
        assert edgar.filing_item(e, {}, NOW, cik_to_ticker={})["published_at"] == NOW, updated


def test_parse_current_accepts_only_sec_archive_links():
    good = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/0000000001-26-000001-index.htm"
    assert len(edgar.parse_current(_entry(good))) == 1
    for bad in ("https://evil.example/x/0000000001-26-000001-index.htm",
                "https://www.sec.gov.evil.example/Archives/edgar/data/1/0000000001-26-000001-index.htm",
                "https://evil.example/https://www.sec.gov/Archives/edgar/data/0000000001-26-000001-index.htm",
                # plain http is REFUSED, not upgraded - the SEC only ever sends https
                "http://www.sec.gov/Archives/edgar/data/1/000000000126000001/0000000001-26-000001-index.htm",
                "/Archives/edgar/data/1/000000000126000001/0000000001-26-000001-index.htm"):
        assert edgar.parse_current(_entry(bad)) == [], bad


_IDX_URL = ("https://www.sec.gov/Archives/edgar/data/1067983/000119312526403089/"
            "0001193125-26-403089-index.htm")
_FOLDER = _IDX_URL.rsplit("/", 1)[0]


def test_xml_url_refuses_path_like_names():
    for name in ("../x.xml", "a/b.xml", "a\\b.xml", "https:x.xml", "..x.xml"):
        assert edgar.xml_url(_IDX_URL, {"directory": {"item": [{"name": name}]}}) is None, name


def test_xml_url_refuses_a_non_sec_index_url():
    idx = {"directory": {"item": [{"name": "form4.xml"}]}}
    assert edgar.xml_url("https://evil.example/a/b-index.htm", idx) is None


def test_xml_url_never_raises_on_malformed_index_json():
    for idx in ({"directory": "x"}, {"directory": {"item": 5}}, {"item": [{"name": None}]},
                {"directory": {"item": [{"name": None}, {"name": 7}, "x", None]}},
                {"directory": None}, {"directory": {"item": None}}, [], "x", 5):
        assert edgar.xml_url(_IDX_URL, idx) is None, idx
    assert edgar.xml_url(None, {"directory": {"item": [{"name": "a.xml"}]}}) is None
    assert edgar.xml_url(5, {"directory": {"item": [{"name": "a.xml"}]}}) is None


def test_xml_url_prefers_the_ownership_document_and_ignores_case():
    idx = {"directory": {"item": [{"name": "exhibit.XML"}, {"name": "FilingSummary.xml"},
                                  {"name": "wk-FORM4_17.XML"}]}}
    assert edgar.xml_url(_IDX_URL, idx) == f"{_FOLDER}/wk-FORM4_17.XML"
    idx = {"directory": {"item": [{"name": "other.xml"}, {"name": "primary_doc.xml"}]}}
    assert edgar.xml_url(_IDX_URL, idx) == f"{_FOLDER}/primary_doc.xml"
    idx = {"directory": {"item": [{"name": "readme.txt"}, {"name": "Doc.XML"}]}}
    assert edgar.xml_url(_IDX_URL, idx) == f"{_FOLDER}/Doc.XML"


def test_parse_form4_requires_an_ownership_document_root():
    body = _one_purchase("100", "10").replace(b"ownershipDocument", b"somethingElse")
    assert edgar.parse_form4(body) is None


def test_form4_single_quoted_default_namespace_still_parses():
    body = (FIX / "form4_buy.xml").read_bytes().replace(
        b"<ownershipDocument>",
        b"<ownershipDocument xmlns='http://www.sec.gov/edgar/ownership'>", 1)
    d = edgar.parse_form4(body)
    assert d is not None and d["symbol"] == "LEN"


def test_doctype_or_entity_bodies_are_refused():
    form4 = (FIX / "form4_buy.xml").read_bytes()
    evil = form4.replace(b"<ownershipDocument>",
                         b'<!DOCTYPE x [<!ENTITY a "aaaa">]>\n<ownershipDocument>', 1)
    assert edgar.parse_form4(evil) is None
    atom = (FIX / "edgar_current_4.xml").read_bytes()
    assert edgar.parse_current(atom.replace(b"<feed", b"<!doctype feed>\n<feed", 1)) == []
    assert edgar.parse_current(b'<!ENTITY x "y"><feed/>') == []


def test_form4_junk_issuer_symbol_is_no_symbol():
    for junk in ("NONE", "none", "NA", "N/A", "n/a", "", "bad sym", "$$$$$$$$$$"):
        d = edgar.parse_form4(_one_purchase("100", "10").replace(
            b"<issuerTradingSymbol>zzz</issuerTradingSymbol>",
            f"<issuerTradingSymbol>{junk}</issuerTradingSymbol>".encode()))
        assert d is not None and d["symbol"] == "", junk
        it = edgar.form4_item(d, {"min_value_usd": 0}, "https://sec/x", NOW, universe=[])
        assert it["tickers"] == [] and it["title"].startswith("Z Corp — "), junk


def test_form4_item_refuses_a_junk_symbol_in_detail():
    feed = {"min_value_usd": 1_000_000}
    for junk in ("NONE", "N/A", "NA"):
        it = edgar.form4_item({"symbol": junk, "company": "Z Corp", "total_value": 5},
                              feed, "https://sec/x", NOW, universe=[junk])
        # a junk symbol is not a ticker, so universe membership cannot rescue it
        assert it is None, junk
    it = edgar.form4_item({"symbol": "NONE", "company": "Z Corp", "total_value": 5e6},
                          feed, "https://sec/x", NOW, universe=[])
    assert it["tickers"] == [] and it["title"].startswith("Z Corp — ")


def test_form4_names_every_reporting_owner():
    d = edgar.parse_form4((FIX / "form4_buy.xml").read_bytes())
    assert d["insider"] == "BERKSHIRE HATHAWAY INC"
    assert d["insiders"] == ["BERKSHIRE HATHAWAY INC", "BUFFETT WARREN E"]
    assert d["relationship"] == "10% Owner"                 # the FIRST owner's
    it = edgar.form4_item(d, {"min_value_usd": 0}, "https://sec/x", NOW, universe=["LEN"])
    assert it["title"].startswith("LEN — BERKSHIRE HATHAWAY INC +1 (10% Owner) bought $")
    assert it["detail"]["insiders"] == ["BERKSHIRE HATHAWAY INC", "BUFFETT WARREN E"]


def test_form4_single_owner_has_no_suffix_and_non_str_insider_is_stringified():
    d = edgar.parse_form4(_one_purchase("100", "10"))
    assert d["insiders"] == ["A B"]
    it = edgar.form4_item(d, {"min_value_usd": 0}, "https://sec/x", NOW, universe=["ZZZ"])
    assert it["title"] == "ZZZ — A B (Director) bought $2K"
    it = edgar.form4_item({"symbol": "ZZZ", "insider": 42, "insiders": [42, None, "x"],
                           "total_value": 5}, {"min_value_usd": 0}, "https://sec/x", NOW,
                          universe=["ZZZ"])
    assert it["title"].startswith("ZZZ — 42 +1 (")


def test_cik_map_ignores_a_bool_cik():
    m = edgar.cik_map({"0": {"cik_str": True, "ticker": "BAD"},
                       "1": {"cik_str": 320193, "ticker": "aapl"}})
    assert m == {"320193": "AAPL"}


def test_form_labels_cover_automatic_shelves_and_amendments():
    def title(form):
        return edgar.filing_item({"form": form, "company": "X", "index_url": "https://sec/i"},
                                 {}, NOW, cik_to_ticker={})["title"]
    assert title("S-3ASR") == "X files S-3ASR (automatic shelf registration)"
    assert title("S-3/A") == "X files S-3/A (amended shelf registration)"
    assert title("S-1/A") == "X files S-1/A (amended IPO / new registration)"
    assert title("F-9/A") == "X files F-9/A (amended registration)"


def test_a_feed_without_public_yields_a_private_item():
    d = {"symbol": "ZZZ", "total_value": 5e6}
    assert edgar.form4_item(d, {"min_value_usd": 0}, "https://sec/x", NOW,
                            universe=["ZZZ"])["public"] is False
    assert edgar.filing_item({"form": "S-3", "index_url": "https://sec/i"}, {}, NOW,
                             cik_to_ticker={})["public"] is False
    assert edgar.form4_item(d, {"public": True}, "https://sec/x", NOW,
                            universe=["ZZZ"])["public"] is True
