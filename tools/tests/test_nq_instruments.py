"""Tests for tools/nq_instruments.py — the per-instrument HUD specs.

The specs are pure data, so most of what is worth asserting is CONSISTENCY
with the two places the rest of the stack keeps the same facts:

  * services/market_svc/symbols.py — the tile display name and quote symbol the
    HUD reads the tape from. These drift at every quarterly futures roll and the
    symptom is silent: the basis gets measured against a contract nobody trades.
  * options-scanner/gex_collector.py — the cash symbols whose gamma grids the
    HUD reads. A cash symbol that is not collected leaves that pane blank.

Both are real defects that unit tests over the HUD's own logic cannot see.
"""

import pathlib
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import nq_instruments as ni  # noqa: E402


#############################################
# SHAPE
#############################################

def test_both_instruments_are_registered():
    assert [s.key for s in ni.INSTRUMENTS] == ["nq", "es"]


def test_lookup_by_key():
    assert ni.by_key("es") is ni.ES
    assert ni.by_key("nq") is ni.NQ
    assert ni.by_key("nope") is None


def test_keys_are_lowercase_and_unique():
    """The key is the JSON prefix in nq_state.json (``nq_cash_flip``). The
    NinjaScript reader anchors its regex on the opening quote, so a prefix that
    is a suffix of another would still be safe — but a DUPLICATE key would have
    one instrument silently overwrite the other."""
    keys = [s.key for s in ni.INSTRUMENTS]
    assert keys == [k.lower() for k in keys]
    assert len(keys) == len(set(keys))


def test_labels_are_unique():
    labels = [s.label for s in ni.INSTRUMENTS]
    assert len(labels) == len(set(labels))


#############################################
# CROSS-MODULE CONSISTENCY (the drift guards)
#############################################

def test_tape_tiles_exist_in_market_svc():
    """Every tile name the HUD reads must actually be published.

    This is the guard for the quarterly roll: when /NQU26 becomes /NQZ26,
    market_svc/symbols.py changes and this fails, instead of the HUD quietly
    reporting a basis measured against a dead contract.
    """
    from services.market_svc import symbols as ms

    displays = {t["display"] for t in ms.SYMBOL_MAP}
    for spec in ni.INSTRUMENTS:
        assert spec.future_tile in displays, spec.future_tile
        assert spec.cash_tile in displays, spec.cash_tile


def test_future_tile_maps_to_the_declared_contract():
    """The tile DISPLAY name and the quote SYMBOL are different strings and both
    are duplicated here. Assert they still describe the same contract."""
    from services.market_svc import symbols as ms

    by_display = {t["display"]: t for t in ms.SYMBOL_MAP}
    for spec in ni.INSTRUMENTS:
        assert by_display[spec.future_tile]["quote_symbol"] == spec.contract


def test_cash_symbols_are_actually_collected():
    """A cash symbol absent from the collection universe means no gamma grid,
    so that instrument's pane can never populate."""
    from repo_paths import OPTIONS_SCANNER

    if str(OPTIONS_SCANNER) not in sys.path:
        sys.path.insert(0, str(OPTIONS_SCANNER))
    import gex_collector as gc

    for spec in ni.INSTRUMENTS:
        assert spec.sources[0] in gc.SYMBOLS, spec.sources[0]


def test_source_preference_leads_with_the_cash_index():
    """The ETF proxy carries structural call-overwriting flow that can invert
    the apparent gamma sign, so it is strictly a fallback."""
    assert ni.NQ.sources == ("$NDX", "QQQ")
    assert ni.ES.sources == ("$SPX", "SPY")
    for spec in ni.INSTRUMENTS:
        assert spec.sources[0].startswith("$")


#############################################
# CONTRACT ECONOMICS
#############################################

@pytest.mark.parametrize("spec,full,micro", [
    (ni.NQ, 20.0, 2.0),    # NQ $20/pt, MNQ $2/pt
    (ni.ES, 50.0, 5.0),    # ES $50/pt, MES $5/pt
])
def test_cme_point_values(spec, full, micro):
    assert spec.point_value == full
    assert spec.micro_point_value == micro


def test_micro_is_a_tenth_of_the_full_contract():
    for spec in ni.INSTRUMENTS:
        assert spec.micro_point_value * 10 == spec.point_value


#############################################
# STOP SIZING
#############################################

def test_es_stops_are_tighter_than_nq_in_points():
    """ES trades at roughly a quarter of NDX's index value, so the same
    percentage move is a quarter of the points. A shared point-denominated stop
    band would be ~4x too wide on ES."""
    assert ni.ES.min_stop < ni.NQ.min_stop
    assert ni.ES.max_stop < ni.NQ.max_stop


def test_stop_bands_keep_the_same_ratio():
    """The floor-to-ceiling ratio is the structural property — it is how much
    room the ATR scaling is allowed to move within. Scaling both ends by the
    index ratio preserves it."""
    for spec in ni.INSTRUMENTS:
        assert spec.max_stop / spec.min_stop == pytest.approx(3.0)


def test_stop_bands_are_proportional_to_index_value():
    """NDX/SPX is ~4, so ES's band should be ~1/4 of NQ's. Loose bound: this is
    a sizing judgement, not a computed constant — the test exists to catch a
    band that is wrong by an order of magnitude, not to pin the exact number."""
    ratio = ni.NQ.min_stop / ni.ES.min_stop
    assert 3.0 <= ratio <= 5.0


def test_dollar_risk_at_the_floor_is_the_same_order():
    """Sanity in the unit that actually matters — a floor stop should cost a
    comparable amount on either contract, or the HUD is silently sizing one of
    them very differently."""
    risks = [s.min_stop * s.point_value for s in ni.INSTRUMENTS]
    assert max(risks) / min(risks) <= 2.0
