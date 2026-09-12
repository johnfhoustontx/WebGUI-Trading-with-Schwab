"""Recording Income Window candidates as captured signals (gap assessment C1).

The 30-45 DTE window has produced **no outcome data at all**: nothing recorded
it, so the nightly calibration has never been able to test the playbook's
30-45 DTE claim against the app's short-dated core. C1 records each day's board
under ``scanner_type = "INCOME"``, which ``shared.calibration.family_key``
already buckets on its own ("an unrecognised family should show up as its own
bucket, not be folded into an existing one").

⚠ **Two things made the one-line version of this a silent no-op or worse**, and
both are what these tests pin:

1. **UNITS.** ``signals.entry_credit`` and ``entry_max_loss`` are PER SHARE —
   verified against prod rows, where ``entry_max_loss == width - entry_credit``
   exactly. The income board carries per-CONTRACT dollars (``net_credit`` 340.0,
   ``max_loss`` 15661.3) and the per-share ``credit`` only on the adapted
   spreads, never on a single leg. Recording a per-contract credit as per-share
   would make every income outcome 100x wrong — in the one dataset C1 exists to
   create.
2. **SHAPE.** An adapted spread carries BOTH the flat ``short_strike`` and the
   normalized ``legs``; a ``SHORT_PUT`` carries only ``legs``. So the strike has
   to come through ``compute.income_open_strike``, the reader that already
   exists for the Open button.
"""
import pytest

from services.options_svc import compute


def _spread_row(**kw):
    """Shaped exactly like a live board row (prod, 2026-09-11): the flat fields
    AND the normalized legs, money per contract in ``net_credit``/``max_loss``
    and per share in ``credit``."""
    base = {
        "id": "IREN_PCS_2026-10-16_38.0_37.0", "symbol": "IREN", "type": "PCS",
        "expiration": "2026-10-16", "dte": 35, "width": 1.0,
        "short_strike": 38.0, "long_strike": 37.0,
        "credit": 0.28, "net_credit": 28.0, "max_loss": 74.6, "max_profit": 25.4,
        "capital": 74.6, "composite_score": 57.0, "grade": "Marginal",
        "short_delta": -0.246, "iv_rank": 0.1, "underlying_price": 43.73,
        "legs": [
            {"kind": "put", "side": "short", "strike": 38.0, "mark": 1.83,
             "delta": -0.246, "theta": -0.05, "bid": 1.77, "ask": 1.88},
            {"kind": "put", "side": "long", "strike": 37.0, "mark": 1.52,
             "delta": 0.0, "theta": 0.0}],
    }
    base.update(kw)
    return base


def _single_leg_row(**kw):
    """A live ``SHORT_PUT`` row: normalized ONLY. No ``credit``, no
    ``short_strike``, no ``long_strike``, no ``width``."""
    base = {
        "id": "XOM_SHORT_PUT_2026-10-16_160.0", "symbol": "XOM",
        "type": "SHORT_PUT", "expiration": "2026-10-16", "dte": 35,
        "net_credit": 340.0, "max_loss": 15661.3, "max_profit": 338.7,
        "capital": 15661.3, "composite_score": 53.5, "grade": "Marginal",
        "iv_rank": 69.2, "underlying_price": 165.56,
        "legs": [
            {"kind": "put", "side": "short", "strike": 160.0, "mark": 3.4,
             "delta": -0.334, "theta": -0.076, "vega": 0.187, "gamma": 0.025,
             "iv": 28.064, "bid": 3.3, "ask": 3.5, "volume": 10, "oi": 2311}],
    }
    base.update(kw)
    return base


# --- units: the part that would corrupt the dataset silently ---------------

def test_a_spread_records_its_per_share_credit_and_max_loss():
    row = compute.income_capture_row(_spread_row())
    assert row["credit"] == 0.28
    assert row["max_loss"] == pytest.approx(0.746)


def test_the_recorded_max_loss_is_the_boards_own_number_which_nets_commission():
    """⚠ A convention difference, recorded deliberately rather than papered over.

    Measured on prod's own scanner rows, ``entry_max_loss == width -
    entry_credit`` EXACTLY — a gross figure. The income board instead nets the
    round-trip commission into ``max_profit``/``max_loss``: $1 wide at a 0.28
    credit gives 74.6, not 72.0, because max_profit is 25.4 rather than 28.0.

    So an income row's risk denominator is ~1.7% larger than a scanner row's on
    a $1-wide spread, which makes an income R-multiple slightly CONSERVATIVE
    against a 0DTE or SWING one. That is accepted here for two reasons: it lands
    in its own calibration bucket, where it is compared against itself; and
    re-deriving a gross figure would need a branch per structure (``width -
    credit`` for a spread, ``strike - credit`` for a cash-secured put, the lot
    basis for a covered call) — three chances to be wrong against one documented
    bias. Gap assessment A7 is the change that flips every column at once.
    """
    row = compute.income_capture_row(_spread_row())
    board = _spread_row()
    assert row["max_loss"] == pytest.approx(board["max_loss"] / 100.0)
    gross = board["width"] - board["credit"]
    assert row["max_loss"] > gross                      # commission-inclusive
    assert row["max_loss"] == pytest.approx(gross, rel=0.05)   # and only just


def test_a_single_leg_derives_its_per_share_credit_from_the_contract_total():
    """``credit`` is absent on a single-leg row, so it comes from
    ``net_credit / 100`` — and must agree with the short leg's own mark, which is
    the independent check that the divisor is right."""
    row = compute.income_capture_row(_single_leg_row())
    assert row["credit"] == pytest.approx(3.40)
    assert row["credit"] == pytest.approx(_single_leg_row()["legs"][0]["mark"])


def test_a_single_legs_max_loss_is_the_strike_less_the_credit():
    """A cash-secured put risks the stock to zero: the strike, less what it was
    paid. The board says 15661.3 for the contract, and getting the divisor wrong
    here is a 100x error in the one number the calibration measures R against —
    so the check is that the per-share figure lands within a commission of
    ``strike - credit`` and NOT anywhere near 15661 or 1.57."""
    row = compute.income_capture_row(_single_leg_row())
    assert row["max_loss"] == pytest.approx(156.613)
    assert row["max_loss"] == pytest.approx(row["short_strike"] - row["credit"], abs=0.02)


# --- shape ------------------------------------------------------------------

def test_a_single_legs_strike_comes_from_the_normalized_legs():
    row = compute.income_capture_row(_single_leg_row())
    assert row["short_strike"] == 160.0
    assert row["long_strike"] is None


def test_a_single_legs_short_delta_comes_from_the_short_leg():
    """The recorder stores ``entry_short_delta``, and B6 made that load-bearing:
    the drift stop measures against it. A single-leg row carries no flat
    ``short_delta`` at all, so 0 would be recorded — and 0 makes the drift rule
    fire at 0.12 on a position that has not moved."""
    row = compute.income_capture_row(_single_leg_row())
    assert row["short_delta"] == pytest.approx(-0.334)


def test_a_spread_keeps_its_flat_fields():
    row = compute.income_capture_row(_spread_row())
    assert (row["short_strike"], row["long_strike"], row["width"]) == (38.0, 37.0, 1.0)
    assert row["short_delta"] == pytest.approx(-0.246)


def test_the_score_and_grade_ride_through_under_the_recorders_own_names():
    row = compute.income_capture_row(_spread_row())
    assert row["composite_score"] == 57.0
    assert row["grade"] == "Marginal"
    assert row["type"] == "PCS" and row["symbol"] == "IREN"
    assert row["dte"] == 35 and row["expiration"] == "2026-10-16"


def test_a_row_with_no_strike_at_all_is_refused_rather_than_recorded_as_zero():
    """``_dedup_key`` indexes ``short_strike`` directly and the key is globally
    UNIQUE with no date component, so one None-striked row would claim a slot
    permanently and silently discard every later capture that hashes to it."""
    assert compute.income_capture_row(_single_leg_row(legs=[])) is None


def test_a_row_with_no_credit_at_all_is_refused():
    assert compute.income_capture_row(_single_leg_row(net_credit=None)) is None


@pytest.mark.parametrize("bad", [None, {}, {"symbol": "X"}, "junk"])
def test_junk_is_refused_rather_than_raising(bad):
    """It runs inside the daily publish; one malformed row must not cost the
    board."""
    assert compute.income_capture_row(bad) is None


# --- the daily publish records the board -----------------------------------

def test_the_publish_records_the_board_it_published(monkeypatch):
    """The wiring. Without it the mapping above is dead code, and the audit's
    own C1 recipe ("record Income Window candidates") is the whole point."""
    from shared.bus import Bus

    from services.options_svc import handlers

    rows = [_spread_row(), _single_leg_row()]
    monkeypatch.setattr(handlers, "_income_symbols", lambda: ["IREN"])
    monkeypatch.setattr(handlers, "_income_lots", lambda: [])
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda symbol, **kw: {"signals": rows})
    captured = {}
    monkeypatch.setattr(handlers, "record_income_signals",
                        lambda cands: captured.setdefault("rows", list(cands)))

    handlers.publish_income(Bus(fake=True))

    assert [r["type"] for r in captured["rows"]] == ["PCS", "SHORT_PUT"]


def test_the_captured_cycle_acts_on_the_21_dte_rule():
    """⚠ Without this, C1 records entries whose only exit is EXPIRY.

    The captured cycle closes on ``_CAPTURED_CLOSE_CODES`` and B1's ``MANAGE_DTE``
    was not in it, so a tracked income signal would have ridden to expiration
    while the app's own policy closes it at 21 DTE in profit. The calibration
    would then have measured a hold-to-expiry policy the manage cycle never
    executes — which is the exact distortion the 2026-08-25 calibration warns
    about for ``rr_pct``.

    ``TARGET_HIT`` stays OUT on purpose: this path is the lifecycle, where +50%
    ARMS break-even and holds rather than taking profit, so that code cannot
    appear here.
    """
    assert "MANAGE_DTE" in compute._CAPTURED_CLOSE_CODES
    assert "TARGET_HIT" not in compute._CAPTURED_CLOSE_CODES


def test_the_captured_cycle_arms_at_the_structures_own_target():
    """Same desync B6 fixed in ``paper_engine``: the cycle decides when to ARM
    from its own threshold read, so once a structure can move its ``tp_frac``
    both reads have to come from the one accessor."""
    import inspect

    src = inspect.getsource(compute.run_captured_manage_cycle)
    assert "tp_frac_for" in src, (
        "the captured cycle reads the GLOBAL TP_FRAC, so a per-structure target "
        "would arm break-even at a level recommend() has not reached")


def test_a_recording_failure_does_not_cost_the_board(monkeypatch):
    """The board is what the reader came for; the capture is bookkeeping behind
    it. A signals-DB failure must not empty the page."""
    from shared.bus import Bus

    from services.options_svc import handlers

    def _boom(_cands):
        raise RuntimeError("signals.db is locked")

    monkeypatch.setattr(handlers, "_income_symbols", lambda: ["IREN"])
    monkeypatch.setattr(handlers, "_income_lots", lambda: [])
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda symbol, **kw: {"signals": [_spread_row()]})
    monkeypatch.setattr(handlers, "record_income_signals", _boom)
    bus = Bus(fake=True)

    handlers.publish_income(bus)

    published = bus.cache_get(handlers.CACHE_INCOME).payload
    assert len(published["candidates"]) == 1
