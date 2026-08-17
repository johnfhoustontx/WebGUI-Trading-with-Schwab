"""
test_gex_collector_live_spot.py — the chain's ``underlyingPrice`` is STALE
outside the regular session, so the collector re-anchors it on the live quote.

Measured on 2026-08-17, the first Cboe extended-hours session, at 06:44 CT:
NVDA's chain reported ``underlyingPrice: 225.16`` — Friday's close to the cent —
while ``/quotes`` reported ``lastPrice: 226.58``. Every one of the first 13 GTH
snapshots for all 17 ETH-eligible symbols stored the stale value.

That one field freezes EVERYTHING downstream: GEX is computed as a function of
spot, so a pinned spot pins net_total, the flip and the walls for the whole
session. The rest of the chain is genuinely fresh (post-expiry open interest,
a moved flip) — the staleness is confined to this single field.

**Pre-existing, not an extended-hours regression.** The same freeze is visible
in the 08:00–08:30 CT pre-open stretch of earlier sessions (NVDA: 2 distinct
spots across that half hour, against 13–15 in a comparable 15-minute regular
stretch). It was invisible because 30 minutes of pre-open drift is small. It is
not invisible across 90 minutes of GTH.

Scoped to NON-regular hours on purpose: during the regular session the two
agree, so overriding there would perturb an established series to no benefit.
"""
from unittest.mock import MagicMock

import datetime as dt
from zoneinfo import ZoneInfo

import gex_collector as gc
import gex_history_db as db

CT = ZoneInfo("America/Chicago")

# 2026-08-17 is the ETH activation date (a Monday).
GTH_NOW = dt.datetime(2026, 8, 17, 6, 44, tzinfo=CT)      # extended hours
RTH_NOW = dt.datetime(2026, 8, 17, 10, 0, tzinfo=CT)      # regular session


#############################################
# FIXTURES
#############################################

def _quotes_payload(prices):
    """The raw Schwab /quotes shape: {SYM: {quote: {lastPrice: ...}}}."""
    return {sym: {"quote": {"lastPrice": px}} for sym, px in prices.items()}


def _make_client(chain_by_symbol, quotes=None, quotes_raise=False):
    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"

    def get_chain(symbol, **kwargs):
        val = chain_by_symbol.get(symbol)
        resp = MagicMock()
        resp.status_code = 200 if val is not None else 500
        resp.json.return_value = val
        return resp

    client.get_option_chain.side_effect = get_chain

    def get_quotes(symbols, **kwargs):
        if quotes_raise:
            raise RuntimeError("proxy down")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _quotes_payload(quotes or {})
        return resp

    client.get_quotes.side_effect = get_quotes
    return client


def _make_engine():
    engine = MagicMock()
    engine._last_dte = 0
    result = {
        "gex": {"225.0": {"call": 1.0, "put": -0.5, "net": 0.5}},
        "spot": 225.16, "flip": 223.71,
        "top_pos_strike": 230.0, "top_neg_strike": 220.0,
        "net_total": 5.9e7, "ts": 1_700_000_000,
    }
    engine.calc_all_from_chain.return_value = (result, None, None, None)
    return engine


def _stale_chain():
    """A GTH chain: fresh contracts, but underlyingPrice pinned to Friday."""
    return {
        "underlyingPrice": 225.16,          # Friday's close
        "ethOptionEligible": True,
        "callExpDateMap": {"2026-08-21:4": {
            "225.0": [{"strike": 225.0, "totalVolume": 10, "mark": 2.0}],
        }},
        "putExpDateMap": {"2026-08-21:4": {
            "225.0": [{"strike": 225.0, "totalVolume": 6, "mark": 3.0}],
        }},
    }


def _conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    return conn


def _seen_chain(client, engine, conn, now):
    """Run one poll and return the chain as downstream consumers saw it."""
    seen = {}
    gc.poll_once(client, engine, conn, symbols=["NVDA"],
                 on_chain=lambda s, c: seen.update({s: c}), now=now)
    return seen.get("NVDA")


#############################################
# live_spots — pure extraction
#############################################

def test_live_spots_extracts_last_price():
    payload = _quotes_payload({"NVDA": 226.58, "TSLA": 341.60})
    assert gc.live_spots(payload) == {"NVDA": 226.58, "TSLA": 341.60}


def test_live_spots_skips_zero_and_missing():
    """A zero or absent lastPrice is NOT a price — it must not overwrite a
    real (if stale) chain value with 0 and collapse every downstream number."""
    payload = {
        "NVDA": {"quote": {"lastPrice": 0}},
        "TSLA": {"quote": {}},
        "AAPL": {"quote": {"lastPrice": 307.38}},
    }
    assert gc.live_spots(payload) == {"AAPL": 307.38}


def test_live_spots_skips_non_numeric():
    payload = {"NVDA": {"quote": {"lastPrice": "n/a"}}}
    assert gc.live_spots(payload) == {}


def test_live_spots_degrades_on_malformed_payload():
    for bad in (None, [], "oops", {"NVDA": None}, {"NVDA": "x"}):
        assert gc.live_spots(bad) == {}


#############################################
# poll_once — the re-anchoring, and its blast radius
#############################################

def test_extended_hours_chain_is_reanchored_on_the_live_quote(tmp_path,
                                                              monkeypatch):
    """THE bug: at 06:44 CT the chain says 225.16 but the tape says 226.58."""
    client = _make_client({"NVDA": _stale_chain()}, quotes={"NVDA": 226.58})
    chain = _seen_chain(client, _make_engine(), _conn(tmp_path, monkeypatch),
                        GTH_NOW)
    assert chain["underlyingPrice"] == 226.58


def test_regular_hours_chain_is_left_alone(tmp_path, monkeypatch):
    """DELIBERATE scope limit: during the regular session the chain's own
    underlyingPrice is live and authoritative. Overriding it there would
    perturb an established series for no gain — see the module docstring."""
    client = _make_client({"NVDA": _stale_chain()}, quotes={"NVDA": 999.0})
    chain = _seen_chain(client, _make_engine(), _conn(tmp_path, monkeypatch),
                        RTH_NOW)
    assert chain["underlyingPrice"] == 225.16


def test_regular_hours_fetches_no_quotes_at_all(tmp_path, monkeypatch):
    """The batched /quotes call is pure cost during RTH — don't make it."""
    client = _make_client({"NVDA": _stale_chain()}, quotes={"NVDA": 999.0})
    _seen_chain(client, _make_engine(), _conn(tmp_path, monkeypatch), RTH_NOW)
    client.get_quotes.assert_not_called()


def test_quote_failure_falls_back_to_the_chain_value(tmp_path, monkeypatch):
    """A stale spot is wrong; no spot is fatal. Degrade to today's behavior."""
    client = _make_client({"NVDA": _stale_chain()}, quotes_raise=True)
    chain = _seen_chain(client, _make_engine(), _conn(tmp_path, monkeypatch),
                        GTH_NOW)
    assert chain["underlyingPrice"] == 225.16


def test_missing_quote_for_one_symbol_leaves_that_chain_alone(tmp_path,
                                                              monkeypatch):
    client = _make_client({"NVDA": _stale_chain()}, quotes={"TSLA": 341.60})
    chain = _seen_chain(client, _make_engine(), _conn(tmp_path, monkeypatch),
                        GTH_NOW)
    assert chain["underlyingPrice"] == 225.16


def test_quotes_are_fetched_once_per_poll_not_once_per_symbol(tmp_path,
                                                              monkeypatch):
    """One batched call for the whole poll. Per-symbol would be ~17x the cost
    across the 90-minute GTH window."""
    chains = {s: _stale_chain() for s in ("NVDA", "TSLA", "AAPL")}
    client = _make_client(chains, quotes={"NVDA": 226.58, "TSLA": 341.60,
                                          "AAPL": 307.38})
    gc.poll_once(client, _make_engine(), _conn(tmp_path, monkeypatch),
                 symbols=["NVDA", "TSLA", "AAPL"], now=GTH_NOW)
    assert client.get_quotes.call_count == 1


def test_engine_receives_the_reanchored_chain(tmp_path, monkeypatch):
    """The whole point: the ENGINE must compute off the live spot, not just
    the on_chain observers. GEX is a function of spot."""
    engine = _make_engine()
    client = _make_client({"NVDA": _stale_chain()}, quotes={"NVDA": 226.58})
    gc.poll_once(client, engine, _conn(tmp_path, monkeypatch),
                 symbols=["NVDA"], now=GTH_NOW)
    passed = engine.calc_all_from_chain.call_args[0][0]
    assert passed["underlyingPrice"] == 226.58
