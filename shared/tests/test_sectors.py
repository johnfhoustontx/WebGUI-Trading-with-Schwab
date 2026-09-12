"""The symbol → sector map — gap assessment B4.

Design: docs/plans/2026-09-12-sector-cap-design.md.

Two jobs here. The first is the accessor's contract, chiefly what it does with a
symbol it has never heard of: **`None`, so the cap can give that symbol its own
bucket** rather than pooling it with unrelated names or letting it borrow another
sector's allowance. The second is the DATA, because this map's whole reason to
exist is that the map it replaces had a 40% hole in exactly the names the cap is
for — so a typo'd sector name creating a 13th "sector" of one member has to be a
test failure, not a silently uncapped symbol.
"""
import tomllib

import pytest

from repo_paths import SECTORS_TOML
from shared import sectors


@pytest.fixture(autouse=True)
def _fresh():
    sectors.reset_cache()
    yield
    sectors.reset_cache()


# ── the accessor ─────────────────────────────────────────────────────────────

def test_a_mapped_symbol_returns_its_sector():
    assert sectors.sector_of("MU") == "Information Technology"
    assert sectors.sector_of("XOM") == "Energy"


def test_an_unmapped_symbol_returns_None():
    """NOT a placeholder string: the caller decides what "unknown" means, and
    here it means "its own bucket", which a shared sentinel could not express."""
    assert sectors.sector_of("ZZQQ_NOT_A_TICKER") is None


def test_lookup_is_case_and_whitespace_insensitive():
    """A stored position row that ever drifted in case must group with the
    signal - the same reason paper_concentration normalises its symbol key."""
    assert sectors.sector_of("mu") == "Information Technology"
    assert sectors.sector_of("  MU  ") == "Information Technology"


@pytest.mark.parametrize("bad", [None, "", "   ", 0, 3.4, [], {}])
def test_a_non_symbol_returns_None_rather_than_raising(bad):
    assert sectors.sector_of(bad) is None


def test_group_key_gives_an_unmapped_symbol_its_OWN_bucket():
    """The load-bearing rule. Two unmapped symbols must not share a bucket (that
    would cap unrelated names together) and neither may collide with a real
    sector name (that would let one borrow the other's allowance)."""
    a = sectors.group_key("ZZQQ_ONE")
    b = sectors.group_key("ZZQQ_TWO")
    assert a != b
    assert a not in sectors.SECTORS and b not in sectors.SECTORS


def test_group_key_of_a_mapped_symbol_IS_its_sector():
    assert sectors.group_key("MU") == "Information Technology"
    assert sectors.group_key("INTC") == sectors.group_key("AMAT")


def test_group_key_normalises_case_so_the_bucket_cannot_split():
    assert sectors.group_key("zzqq_one") == sectors.group_key("ZZQQ_ONE")


def test_indices_are_a_bucket_of_their_own_and_not_exempt():
    """Measured: mean pairwise correlation of SPY/QQQ/DIA/IWM daily returns is
    0.799 over six months - the second-tightest group in the universe. Nine
    simultaneous index positions is one market bet, so they are capped, and they
    are capped TOGETHER."""
    keys = {sectors.group_key(s) for s in ("SPY", "QQQ", "DIA", "IWM", "$SPX", "$NDX")}
    assert keys == {"INDEX"}
    assert "INDEX" in sectors.SECTORS


# ── the data ─────────────────────────────────────────────────────────────────

def _raw():
    return tomllib.loads(SECTORS_TOML.read_text(encoding="utf-8"))["sectors"]


def test_every_shipped_value_is_a_known_sector():
    """A typo would otherwise create a sector of one member — which does not
    refuse anything, and looks exactly like a mapped symbol."""
    unknown = {s: v for s, v in _raw().items() if v not in sectors.SECTORS}
    assert not unknown, unknown


def test_the_eleven_GICS_sectors_plus_INDEX_are_the_closed_set():
    assert len(sectors.SECTORS) == 12
    assert "INDEX" in sectors.SECTORS


def test_the_vocabulary_matches_the_sentiment_services_own_sector_names():
    """The two halves of the app must not disagree about what a sector is called.
    ``sectors_ref`` carries the cyclical/defensive split over the same 11 names,
    so any drift here would silently un-map a whole sector.
    """
    import pathlib
    import sys

    from repo_paths import SENTIMENT as SENTIMENT_DASHBOARD
    sys.path.insert(0, str(SENTIMENT_DASHBOARD))
    try:
        import sectors_ref
    finally:
        sys.path.remove(str(SENTIMENT_DASHBOARD))
    known = set(sectors_ref.CYCLICAL_SECTORS) | set(sectors_ref.DEFENSIVE_SECTORS)
    assert known, "sectors_ref exposes no sector names — check the import"
    assert known <= sectors.SECTORS, sorted(known - sectors.SECTORS)
    assert pathlib.Path(SECTORS_TOML).exists()


def test_no_symbol_is_mapped_twice_under_different_sectors():
    """TOML would silently keep the last one. The workbook genuinely lists 20
    symbols under two sectors (NVDA, TSLA, COIN, ...), which is why the seed
    applied an explicit tie-break rather than whichever row came last."""
    raw = SECTORS_TOML.read_text(encoding="utf-8")
    seen, dupes = set(), []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith('"') or "=" not in line:
            continue
        sym = line.split("=", 1)[0].strip().strip('"')
        if sym in seen:
            dupes.append(sym)
        seen.add(sym)
    assert not dupes, dupes


def test_the_semiconductor_names_the_cap_EXISTS_for_are_mapped():
    """The workbook this map replaces was missing every one of these, and they
    are 25 of the 273 historical paper positions plus the bulk of the watchlist's
    tilt. A regression that dropped them would re-open the exact hole B4 closed.
    """
    for sym in ("MU", "AMAT", "MRVL", "INTC", "TXN", "ALAB", "SMCI", "DELL",
                "SKHY"):
        assert sectors.sector_of(sym) == "Information Technology", sym


def test_the_most_traded_unmapped_name_is_mapped_now():
    """SPCX was 121 of 273 historical paper positions — the single largest name
    in the book — and the workbook had never heard of it. Schwab describes it as
    "SPACE EX TECH SPACEX A", an aerospace equity."""
    assert sectors.sector_of("SPCX") == "Industrials"


def test_a_missing_file_degrades_to_an_empty_map_rather_than_raising(monkeypatch,
                                                                    tmp_path):
    """House contract for every config file here: missing / malformed -> the
    built-in default, never a raise. The default is EMPTY, so the sector cap
    simply stops binding — the conservative direction, since the four rungs
    around it are untouched."""
    from shared import config_toml
    load, _ = config_toml.toml_loader(tmp_path / "nope.toml",
                                      {"sectors": {}}, label="test")
    monkeypatch.setattr(sectors, "load", load)
    sectors.reset_cache()
    assert sectors.sector_of("MU") is None
    assert sectors.group_key("MU") != "Information Technology"
