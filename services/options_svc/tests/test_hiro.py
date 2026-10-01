import math

import pytest

from services.options_svc import hiro


@pytest.mark.parametrize("last,bid,ask,want", [
    (1.10, 1.00, 1.10, 1),     # at the ask -> customer bought
    (1.20, 1.00, 1.10, 1),     # through the ask
    (1.00, 1.00, 1.10, -1),    # at the bid -> customer sold
    (0.95, 1.00, 1.10, -1),
    (1.08, 1.00, 1.10, 1),     # above mid
    (1.02, 1.00, 1.10, -1),    # below mid
    (1.05, 1.00, 1.10, 0),     # exactly mid -> unclassified, never guessed
])
def test_classify_side_quote_rule(last, bid, ask, want):
    assert hiro.classify_side(last, bid, ask) == want


@pytest.mark.parametrize("last,bid,ask", [
    (None, 1.0, 1.1), (1.0, None, 1.1), (1.0, 1.0, None),
    (math.nan, 1.0, 1.1), (1.0, math.inf, 1.1), (True, 1.0, 1.1),
    (0.0, 1.0, 1.1),           # no print
    (1.0, 1.2, 1.1),           # crossed quote
    (1.0, -999.0, 1.1),        # sentinel
])
def test_classify_side_unusable_quote_is_unclassified(last, bid, ask):
    assert hiro.classify_side(last, bid, ask) == 0
