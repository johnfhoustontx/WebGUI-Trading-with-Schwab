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
