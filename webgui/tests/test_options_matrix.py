from pages.options import matrix


def test_matrix_columns_are_sortable():
    cols = matrix.matrix_columns()
    fields = {c["field"] for c in cols}
    for f in ("symbol", "spot", "day_pct", "n_signals", "n_alerts", "signal_label", "hotness"):
        assert f in fields
    assert all(c["sortable"] for c in cols)


def test_matrix_rows_formats_and_stamps_classes():
    payload = {"rows": [{
        "symbol": "SPY", "spot": 101.0, "day_pct": 1.2, "trend_state": "up",
        "trend_dir": 0.6, "call_accel": "hot", "put_accel": "cool", "pc_ratio": 0.5,
        "net_prem_m": 2.0, "flip": 100.0, "gex_regime": "above", "n_signals": 3,
        "n_alerts": 2, "signal": "buy", "signal_strength": 2, "hotness": 12}]}
    rows = matrix.matrix_rows(payload)
    r = rows[0]
    assert r["symbol"] == "SPY"
    assert r["_signal_class"]
    assert r["_daypct_class"]
    assert r["_regime_class"]
    assert r["signal_label"] == "Buy"
    assert r["trend"]           # an arrow glyph
    assert r["_trend_class"]


def test_matrix_rows_empty_and_none_payload():
    assert matrix.matrix_rows({}) == []
    assert matrix.matrix_rows(None) == []
    assert matrix.matrix_rows({"rows": []}) == []


def test_signal_class_maps_all_states():
    for s in ("buy", "neutral", "sell", "bogus"):
        assert isinstance(matrix.signal_class(s), str) and matrix.signal_class(s)
    assert matrix.signal_class("bogus") == matrix.signal_class("neutral")


def test_daypct_class_sign():
    assert matrix.daypct_class(1.0) != matrix.daypct_class(-1.0)
    assert matrix.daypct_class(None) == matrix.daypct_class(0.0)


def test_matrix_rows_degraded_row_has_defaults():
    # a row with mostly-missing fields must not crash + get sane defaults
    rows = matrix.matrix_rows({"rows": [{"symbol": "AAPL"}]})
    r = rows[0]
    assert r["symbol"] == "AAPL"
    assert r["signal_label"] == "Neutral"
    assert r["gex_regime"] == "na"
    assert r["n_signals"] == 0


def test_short_ts_converts_utc_to_central():
    # 22:03 UTC on 2026-07-20 is CDT (UTC-5) → 5:03 PM Central, not 10:03 PM.
    assert matrix._short_ts("2026-07-20T22:03:00+00:00") == "5:03 PM"
    # a naive timestamp (no offset) is treated as UTC and still converted.
    assert matrix._short_ts("2026-07-20T22:03:00") == "5:03 PM"
    # winter date is CST (UTC-6): 22:03 UTC → 4:03 PM.
    assert matrix._short_ts("2026-01-15T22:03:00+00:00") == "4:03 PM"
    assert matrix._short_ts(None) == ""
    assert matrix._short_ts("garbage") == ""


def test_signal_summary_counts_by_signal():
    payload = {"rows": [
        {"signal": "buy"}, {"signal": "buy"}, {"signal": "sell"},
        {"signal": "neutral"}, {"signal": "neutral"}, {"signal": "neutral"}]}
    assert matrix.signal_summary(payload) == {"buy": 2, "neutral": 3, "sell": 1}


def test_signal_summary_empty_and_unknown_falls_to_neutral():
    assert matrix.signal_summary({}) == {"buy": 0, "neutral": 0, "sell": 0}
    assert matrix.signal_summary(None) == {"buy": 0, "neutral": 0, "sell": 0}
    # unknown/missing signal is bucketed as neutral so the counts sum to the row total
    assert matrix.signal_summary({"rows": [{"signal": "bogus"}, {}]}) \
        == {"buy": 0, "neutral": 2, "sell": 0}


def test_status_text_updated_clock_is_central():
    text = matrix.status_text({"rows": [{}], "session_date": "2026-07-20",
                               "ts": "2026-07-20T22:03:00+00:00"})
    assert "updated 5:03 PM" in text          # Central, not UTC 10:03 PM
    assert "session 2026-07-20" in text


# --- ETH-eligible badge (E2) -------------------------------------------------
# The badge's job is to keep the ~38 non-eligible symbols from reading as
# stale/broken at 07:00 CT, when only the ~7 eligible names have live rows.
def test_matrix_rows_stamps_the_eth_flag_and_class():
    rows = matrix.matrix_rows({"rows": [
        {"symbol": "NVDA", "eth_eligible": True},
        {"symbol": "KO", "eth_eligible": False},
    ]})
    by = {r["symbol"]: r for r in rows}
    assert by["NVDA"]["_eth"] is True
    assert by["NVDA"]["_eth_class"] == matrix.ETH_BADGE_CLASS
    assert by["KO"]["_eth"] is False


def test_matrix_rows_eth_absent_is_false():
    """A payload cached before the field existed (Redis persists the view across a
    service restart) must simply show no badge."""
    rows = matrix.matrix_rows({"rows": [{"symbol": "SPY"}]})
    assert rows[0]["_eth"] is False


def test_eth_badge_class_is_a_muted_static_tailwind_class():
    """Finite-set → fixed Tailwind class (the page's data-driven-color rule); and
    it stays visually quiet, since most rows carry it on a post-activation day."""
    cls = matrix.ETH_BADGE_CLASS
    # No runtime-built colour: named palette classes only, never a hex/rgba/var
    # arbitrary (the documented Tailwind JIT trap). A `text-[10px]` SIZE is fine.
    assert "#" not in cls and "rgba(" not in cls and "var(" not in cls
    assert "slate" in cls              # muted, not a loud accent colour


def test_symbol_slot_renders_the_badge_conditionally():
    slot = matrix._SYMBOL_SLOT
    assert "props.row._eth" in slot            # v-if gate
    assert "props.row._eth_class" in slot      # stamped :class, no inline style
    assert ":style=" not in slot
