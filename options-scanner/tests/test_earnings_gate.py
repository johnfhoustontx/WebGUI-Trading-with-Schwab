"""Which candidates the earnings gate applies to.

The gate used to be a bare membership test, ``trade_type in
EARNINGS_GATED_TRADE_TYPES``, whose comment justified exempting "0-DTE" on the
grounds that such a position "is flat by the close" and so cannot be held
through a report.

That premise is false for most of the bucket. ``scanner_engine`` line 1398 sets
``zerodte_max_dte = 4`` and the bucket is documented as covering DTE 0..4, so a
"0-DTE" candidate is routinely a multi-session hold. Measured on 2026-09-08: all
sixteen ORCL captures were ``scanner_type=0DTE`` with ``dte_at_entry=3``,
expiring 2026-09-11 -- straddling the report scheduled for 2026-09-10. The
exemption is sound only for a genuinely same-day expiry.
"""
import scanner_engine as se


class TestHeldAcrossSessions:
    def test_swing_is_gated(self):
        assert se.earnings_gate_applies("SWING", 9) is True

    def test_income_is_gated(self):
        assert se.earnings_gate_applies("INCOME", 38) is True

    def test_swing_is_gated_even_at_the_bottom_of_its_range(self):
        assert se.earnings_gate_applies("SWING", 5) is True


class TestTheZeroDteBucket:
    def test_a_genuinely_same_day_expiry_keeps_its_exemption(self):
        """Flat by the close is true here, and only here."""
        assert se.earnings_gate_applies("0-DTE", 0) is False

    def test_a_three_day_hold_in_the_zero_dte_bucket_is_gated(self):
        """The September ORCL case: scanner_type 0DTE, dte_at_entry 3."""
        assert se.earnings_gate_applies("0-DTE", 3) is True

    def test_every_overnight_dte_in_the_bucket_is_gated(self):
        assert [se.earnings_gate_applies("0-DTE", d) for d in (1, 2, 3, 4)] \
            == [True, True, True, True]

    def test_an_unreadable_dte_does_not_earn_the_exemption(self):
        """The exemption rests on KNOWING the expiry is same-day. Absent that,
        the candidate is treated as held -- this fails closed on the exemption,
        not on the gate: nothing is dropped unless a report actually straddles."""
        assert se.earnings_gate_applies("0-DTE", None) is True
        assert se.earnings_gate_applies("0-DTE", "nonsense") is True

    def test_a_numeric_string_dte_is_read_not_refused(self):
        assert se.earnings_gate_applies("0-DTE", "0") is False
        assert se.earnings_gate_applies("0-DTE", "3") is True


class TestTheFailOpenDefault:
    """Other callers pass trade types this gate has never covered, and a blanket
    fail-closed would silently empty them -- the reasoning
    TestScannedTradeTypesAreAllGated already pins for the liquidity gate."""

    def test_an_unknown_trade_type_is_not_gated(self):
        assert se.earnings_gate_applies("NOT-A-WINDOW", 3) is False

    def test_directional_is_not_gated(self):
        assert se.earnings_gate_applies("DIRECTIONAL", 3) is False


class TestEveryScannedTradeTypeIsDecided:
    def test_the_gated_tuple_is_still_the_underlying_data(self):
        """The predicate must stay driven by the shared tuple, so the scanner
        and options_svc cannot drift -- the reason that tuple was exported."""
        for tt in se.EARNINGS_GATED_TRADE_TYPES:
            assert se.earnings_gate_applies(tt, 7) is True

    def test_the_zero_dte_bucket_is_the_only_dte_sensitive_window(self):
        """A gated window is gated regardless of DTE; only the exempt bucket
        looks at it. Pins that a future window added to the tuple cannot
        accidentally inherit the same-day carve-out."""
        for tt in se.EARNINGS_GATED_TRADE_TYPES:
            assert se.earnings_gate_applies(tt, 0) is True
