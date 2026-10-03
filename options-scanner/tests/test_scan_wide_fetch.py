"""The autoscan can fetch one wide chain and cut its three windows locally."""
import datetime as dt

import scanner_engine as se

D = dt.date(2026, 10, 5)


def _chain():
    days = [0, 2, 4, 7, 14, 21, 30, 44]
    exp = {f"{(D + dt.timedelta(days=n)).isoformat()}:{n}": {"100.0": [{"x": n}]}
           for n in days}
    return {"symbol": "AAPL", "underlyingPrice": 100.0,
            "callExpDateMap": dict(exp), "putExpDateMap": dict(exp)}


def _dtes(chain, side="callExpDateMap"):
    return sorted(int(k.split(":")[1]) for k in chain[side])


def test_a_window_keeps_only_its_expirations_on_both_sides():
    out = se.slice_chain(_chain(), D, D + dt.timedelta(days=4))
    assert _dtes(out) == [0, 2, 4] and _dtes(out, "putExpDateMap") == [0, 2, 4]


def test_the_three_scan_windows_partition_as_three_fetches_would():
    wide = _chain()
    assert _dtes(se.slice_chain(wide, D + dt.timedelta(days=5),
                                D + dt.timedelta(days=15))) == [7, 14]
    assert _dtes(se.slice_chain(wide, D + dt.timedelta(days=20),
                                D + dt.timedelta(days=45))) == [21, 30, 44]


def test_everything_else_on_the_chain_is_kept_and_the_source_untouched():
    wide = _chain()
    out = se.slice_chain(wide, D, D)
    assert out["underlyingPrice"] == 100.0 and out["symbol"] == "AAPL"
    assert len(wide["callExpDateMap"]) == 8


def test_a_window_with_no_expirations_has_empty_maps_not_missing_ones():
    out = se.slice_chain(_chain(), D + dt.timedelta(days=50), D + dt.timedelta(days=60))
    assert out["callExpDateMap"] == {} and out["putExpDateMap"] == {}


def test_no_chain_stays_no_chain():
    assert se.slice_chain(None, D, D) is None


# ── the header a cut carries ────────────────────────────────────────────────
# Measured against Schwab: a window holding no expiration is answered HTTP 200,
# status SUCCESS, both maps empty, numberOfContracts 0 and underlyingPrice 0.0.
# The scan's funnel reads that price (``chain_has_underlying``), so a cut has to
# carry the header a fetch of the same window would have.

def _schwab_chain():
    """``_chain()`` with the header fields a real Schwab chain carries."""
    return dict(_chain(), status="SUCCESS", numberOfContracts=16)


def _schwab_empty_window():
    """What Schwab itself answers for a window with no expirations."""
    return {"symbol": "AAPL", "status": "SUCCESS", "underlyingPrice": 0.0,
            "numberOfContracts": 0, "callExpDateMap": {}, "putExpDateMap": {}}


def test_a_cut_recounts_the_contracts_it_kept():
    wide = _schwab_chain()
    out = se.slice_chain(wide, D, D + dt.timedelta(days=4))
    assert out["numberOfContracts"] == 6          # 3 expirations x 2 sides
    assert out["underlyingPrice"] == 100.0        # a window with contracts keeps the price
    assert wide["numberOfContracts"] == 16        # the source header is untouched


def test_the_recount_counts_contracts_not_strikes():
    wide = _schwab_chain()
    key = f"{D.isoformat()}:0"
    wide["callExpDateMap"] = {key: {"100.0": [{"x": 0}, {"x": 1}], "105.0": [{"x": 2}]}}
    wide["putExpDateMap"] = {key: {"100.0": [{"x": 3}]}}
    assert se.slice_chain(wide, D, D)["numberOfContracts"] == 4


def test_an_empty_window_carries_the_header_schwab_answers_one_with():
    wide = _schwab_chain()
    out = se.slice_chain(wide, D + dt.timedelta(days=50), D + dt.timedelta(days=60))
    assert out == _schwab_empty_window()
    assert wide["underlyingPrice"] == 100.0 and wide["numberOfContracts"] == 16


def test_an_empty_cut_reads_to_the_scan_exactly_as_schwabs_empty_answer_does():
    """The funnel's ``underlying_zero`` and ``screen_spreads``' early return both
    ask ``chain_has_underlying``; a cut that kept the wide chain's real price
    would answer it differently from the fetch it stands in for."""
    cut = se.slice_chain(_schwab_chain(), D + dt.timedelta(days=50),
                         D + dt.timedelta(days=60))
    assert se.chain_has_underlying(cut) is se.chain_has_underlying(_schwab_empty_window())
    assert se.chain_has_underlying(cut) is False


def test_a_one_sided_window_is_not_an_empty_one():
    """Only a window with NO expiration on either side loses its price."""
    wide = _schwab_chain()
    wide["putExpDateMap"] = {}
    out = se.slice_chain(wide, D, D + dt.timedelta(days=4))
    assert out["underlyingPrice"] == 100.0 and out["numberOfContracts"] == 3
