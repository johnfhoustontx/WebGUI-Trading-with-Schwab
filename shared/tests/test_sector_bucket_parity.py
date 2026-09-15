"""The page's sector bucket and the service's are ONE rule.

The Paper dialog previews the sector rungs with ``book_caps.sector_bucket``
over the published table; the service enforces with ``sectors.group_key``. If
the two ever disagreed on any symbol, the preview would promise a fit the click
then refuses (or the reverse). ``group_key`` also feeds the Paper ACCOUNT's
sector cap, so its answers must stay exactly what they were before it began
delegating.

``_frozen_group_key`` is ``shared.sectors.group_key`` as it stood on 2026-09-15
BEFORE the delegation, copied verbatim with its helpers, reading the same live
table. Both current implementations are compared to it over every mapped symbol
plus the edge inputs.
"""
from shared import book_caps, sectors


def _frozen_key(symbol):
    if not isinstance(symbol, str):
        return None
    s = symbol.strip().upper()
    return s or None


def _frozen_sector_of(symbol):
    key = _frozen_key(symbol)
    if key is None:
        return None
    table = sectors.load().get("sectors")
    if not isinstance(table, dict):
        return None
    value = table.get(key)
    return value if isinstance(value, str) and value else None


def _frozen_group_key(symbol):
    key = _frozen_key(symbol)
    if key is None:
        return None
    return _frozen_sector_of(key) or ("?" + key)


EDGE = ["zzzq", " orcl ", "", "   ", None, 123, "?X", "xom", "Spy"]


def _inputs():
    table = sectors.load().get("sectors") or {}
    assert len(table) > 50, "the real sectors.toml must be loaded, or parity is vacuous"
    return list(table) + [s.lower() for s in table] + EDGE


def test_group_key_answers_exactly_as_before():
    for s in _inputs():
        assert sectors.group_key(s) == _frozen_group_key(s), repr(s)


def test_the_page_rule_over_the_published_table_matches_the_service():
    table = sectors.load().get("sectors")
    for s in _inputs():
        assert book_caps.sector_bucket(table, s) == _frozen_group_key(s), repr(s)
