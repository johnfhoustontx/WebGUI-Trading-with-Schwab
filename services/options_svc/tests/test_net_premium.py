"""Dealer Positioning "Net Prem" view: the PURE group model + series builder
(``net_premium``) and the DB-only orchestration that feeds it
(``compute.build_net_premium``)."""
import datetime

import pytest

from services.market_svc import symbols as market_symbols
from services.options_svc import compute
from services.options_svc import net_premium as np_mod


def test_three_groups_in_display_order():
    keys = [g["key"] for g in np_mod.GROUPS]
    assert keys == ["indices", "sectors", "megacaps"]


def test_group_membership_matches_the_spec():
    by_key = {g["key"]: list(g["symbols"]) for g in np_mod.GROUPS}
    assert by_key["indices"] == ["$SPX", "$NDX", "BIG10", "SPY", "QQQ", "IWM", "DIA"]
    assert by_key["sectors"] == ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                                 "XLP", "XLRE", "XLU", "XLV", "XLY"]
    assert by_key["megacaps"] == ["NVDA", "AVGO", "AAPL", "META", "MSFT",
                                  "TSLA", "PLTR", "AMZN", "GOOGL", "AMD"]


def test_big10_basket_matches_market_dashboard_membership():
    # Must stay identical to market_svc/symbols.py's BIG10 basket, or "BIG10"
    # would mean two different things on two pages. Read from market_svc rather
    # than hardcoding the ten, so this fails on drift from EITHER side — a
    # hardcoded copy would stay green while the two pages silently diverged.
    # The cross-service import is TEST-ONLY (symbols.py is pure data, no
    # imports); net_premium.py itself stays free of it.
    #
    # Compared SORTED, not as sets: order differs by design (display vs
    # market_svc-mirroring) so the comparison must stay order-insensitive, but a
    # set() would also swallow a DUPLICATE ticker — and a duplicate is not
    # cosmetic here, it double-counts that member into the BIG10 sum.
    mkt = next(e["basket"] for e in market_symbols.SYMBOL_MAP
               if e["kind"] == "basket" and e["display"] == "BIG10")
    members = np_mod.BASKETS["BIG10"]
    assert sorted(members) == sorted(mkt)
    assert len(members) == len(set(members)), f"duplicate in BIG10: {members}"


def test_big10_basket_covers_the_megacap_group():
    # The two lists are the same ten tickers in different orders (display vs
    # market_svc-mirroring). Nothing structural ties them, so pin it: adding an
    # 11th mega-cap to the group without the basket would make BIG10 quietly
    # under-sum while the new line rendered right beside it.
    # sorted(), not set(), for the same duplicate-hiding reason as above.
    megacaps = next(g["symbols"] for g in np_mod.GROUPS if g["key"] == "megacaps")
    assert sorted(megacaps) == sorted(np_mod.BASKETS["BIG10"])


def test_display_symbols_are_every_group_member_deduped_in_order():
    out = np_mod.display_symbols()
    assert out[0] == "$SPX"
    assert "BIG10" in out
    assert len(out) == len(set(out)) == 7 + 11 + 10


def test_source_symbols_drop_baskets_and_add_their_members():
    out = np_mod.source_symbols()
    assert "BIG10" not in out          # not a real ticker — nothing to read
    # Every real ticker survives and BIG10's members are all already mega-cap
    # group entries, so the source set is exactly the display set less BIG10.
    assert set(out) == set(np_mod.display_symbols()) - {"BIG10"}
    assert len(out) == len(set(out))   # deduped


def test_every_source_symbol_is_in_the_static_collection_base():
    """Drift guard: a group symbol that isn't collected has no premium history,
    so its line would be permanently empty.

    Asserts against the STATIC ``SYMBOLS`` base, deliberately NOT against
    ``collection_symbols()`` (= base ∪ ``Top 20.xlsx``). That union is the weaker
    claim and it fails open on exactly the machine this guard protects: the
    workbook is GITIGNORED, and it already happens to carry IWM, DIA and all ten
    mega-caps. So a union-based assertion stays GREEN on the dev box while a
    fresh clone silently loses those twelve lines — the regression would surface
    only for whoever clones next. ``SYMBOLS ⊆ collection_symbols()`` always, so
    this is strictly stronger, and it pins the actual design intent: every symbol
    a named UI group renders must be collected independently of that workbook."""
    import sys

    from repo_paths import OPTIONS_SCANNER
    if str(OPTIONS_SCANNER) not in sys.path:
        sys.path.insert(0, str(OPTIONS_SCANNER))
    import gex_collector

    missing = [s for s in np_mod.source_symbols()
               if s not in set(gex_collector.SYMBOLS)]
    assert not missing, f"not in gex_collector.SYMBOLS: {missing}"


def _row(ts, call, put):
    """A gex_history flow row: (ts, spot, call_vol, put_vol, call_prem, put_prem).

    The volume slots carry DISTINCT non-zero sentinels (7, 8) on purpose. They are
    never read, but they sit immediately before the premium columns, so if the
    parser ever read 2/3 instead of 4/5 these values make every test in this file
    fail on a wrong VALUE. Zeroes there would make the tests pass-by-luck: the row
    would look premium-less and get dropped, so the suite would go red for the
    wrong reason and stay silent about the real hazard (contract counts read as
    dollars)."""
    return (ts, 100.0, 7, 8, call, put)


def test_build_series_projects_ts_call_put():
    out = np_mod.build_series({"SPY": [_row(1, 10.0, 4.0), _row(2, 20.0, 5.0)]})
    assert out["SPY"] == [[1, 10.0, 4.0], [2, 20.0, 5.0]]


def test_build_series_skips_rows_with_no_premium_at_all():
    out = np_mod.build_series({"SPY": [_row(1, None, None), _row(2, 20.0, 5.0)]})
    assert out["SPY"] == [[2, 20.0, 5.0]]


def test_build_series_treats_one_missing_side_as_zero():
    out = np_mod.build_series({"SPY": [_row(1, 10.0, None)]})
    assert out["SPY"] == [[1, 10.0, 0.0]]


def test_build_series_sums_basket_members_by_timestamp():
    out = np_mod.build_series({
        "NVDA": [_row(1, 10.0, 1.0), _row(2, 30.0, 2.0)],
        "MSFT": [_row(1, 5.0, 3.0), _row(2, 7.0, 4.0)],
    })
    assert out["BIG10"] == [[1, 15.0, 4.0], [2, 37.0, 6.0]]


def test_basket_tolerates_partially_reported_timestamps():
    # A member missing a snapshot contributes nothing at that ts rather than
    # dropping the whole column.
    out = np_mod.build_series({
        "NVDA": [_row(1, 10.0, 1.0), _row(2, 30.0, 2.0)],
        "MSFT": [_row(1, 5.0, 3.0)],
    })
    assert out["BIG10"] == [[1, 15.0, 4.0], [2, 30.0, 2.0]]


def test_basket_absent_when_no_member_has_data():
    out = np_mod.build_series({"NVDA": []})
    assert "BIG10" not in out


def test_symbols_with_no_rows_are_omitted():
    out = np_mod.build_series({"XLK": [], "SPY": [_row(1, 1.0, 1.0)]})
    assert "XLK" not in out and "SPY" in out


def test_build_series_never_raises_on_junk():
    out = np_mod.build_series({"SPY": [("x",), None, _row(1, 1.0, 1.0)]})
    assert out["SPY"] == [[1, 1.0, 1.0]]


def test_dict_shaped_rows_are_skipped_not_raised_on():
    """A row_factory set anywhere upstream (sqlite3.Row / dict) hands us mappings
    instead of tuples. Indexing one by position raises KeyError, which would
    break the stated "junk rows are skipped" contract from a change made in a
    different file."""
    out = np_mod.build_series({"SPY": [{"ts": 1, "call_prem": 9.0},
                                       _row(2, 1.0, 1.0)]})
    assert out["SPY"] == [[2, 1.0, 1.0]]


def test_sqlite3_row_shaped_rows_still_parse():
    """``sqlite3.Row`` is the most ordinary row_factory there is, and it is NOT a
    tuple or list — but it has ``len()`` and positional indexing, and every other
    parser of these rows handles it. An isinstance-based gate would make this
    module the sole silent casualty of someone setting that factory upstream,
    failing in the worst mode: an empty series looks exactly like a symbol that
    isn't collected yet."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT 1 AS ts, 100.0 AS spot, 7 AS call_vol, 8 AS put_vol,"
        "       10.0 AS call_prem, 4.0 AS put_prem"
    ).fetchone()
    conn.close()

    assert not isinstance(row, (tuple, list))   # the exact trap being guarded
    assert np_mod.build_series({"SPY": [row]})["SPY"] == [[1, 10.0, 4.0]]


def test_truncated_rows_are_skipped():
    # Fewer than 6 fields: the premium columns aren't there to read.
    out = np_mod.build_series({"SPY": [(1, 100.0, 0, 0), _row(2, 1.0, 1.0)]})
    assert out["SPY"] == [[2, 1.0, 1.0]]


def test_bools_are_not_numbers():
    # True == 1 in Python, so an unguarded bool would smuggle in ts=1.
    out = np_mod.build_series({"SPY": [_row(True, 1.0, 1.0)]})
    assert "SPY" not in out


def test_nan_and_inf_are_rejected():
    """One nan would otherwise propagate through the basket sum and blank the
    entire BIG10 column, and json.dumps would emit invalid JSON into Redis."""
    out = np_mod.build_series({
        "NVDA": [_row(1, float("nan"), 1.0), _row(2, float("inf"), 2.0),
                 _row(3, 10.0, 3.0)],
    })
    assert out["NVDA"] == [[1, 0.0, 1.0], [2, 0.0, 2.0], [3, 10.0, 3.0]]
    assert out["BIG10"] == [[1, 0.0, 1.0], [2, 0.0, 2.0], [3, 10.0, 3.0]]


def test_zero_premium_row_is_kept_as_a_real_observation():
    # 0.0 means "nothing traded that side" (real); None means "column absent".
    out = np_mod.build_series({"SPY": [_row(1, 0.0, 0.0)]})
    assert out["SPY"] == [[1, 0.0, 0.0]]


def test_int_premiums_are_normalised_to_float():
    out = np_mod.build_series({"SPY": [_row(1, 10, 4)]})
    assert [type(v) for v in out["SPY"][0][1:]] == [float, float]


def test_series_is_sorted_by_timestamp():
    """Highcharts line series require x-ascending points; out-of-order data is a
    documented error condition, not a cosmetic one. The basket path sorts
    separately, so this covers the per-symbol path."""
    out = np_mod.build_series({"SPY": [_row(3, 3.0, 0.0), _row(1, 1.0, 0.0),
                                       _row(2, 2.0, 0.0)]})
    assert [pt[0] for pt in out["SPY"]] == [1, 2, 3]


def test_basket_key_in_the_input_is_ignored_not_passed_through():
    """Baskets are always derived, never forwarded, so the result can't depend on
    whether the caller happened to include one."""
    out = np_mod.build_series({"BIG10": [_row(1, 999.0, 999.0)],
                               "NVDA": [_row(1, 10.0, 1.0)]})
    assert out["BIG10"] == [[1, 10.0, 1.0]]


def test_basket_key_alone_in_the_input_yields_no_basket():
    out = np_mod.build_series({"BIG10": [_row(1, 999.0, 999.0)]})
    assert out == {}


# --- compute.build_net_premium (DB-only orchestration) ------------------------


class _FakeGH:
    """Stands in for gex_history_db: records the symbols asked for."""

    def __init__(self, data, fail_for=()):
        self.data = data
        self.fail_for = set(fail_for)
        self.asked = []
        self.asked_dates = []
        self.closed = False

    def connect(self, read_only=False):
        gh = self

        class _Conn:
            def close(self):
                gh.closed = True

        return _Conn()

    def load_flow_series(self, conn, symbol, d=None):
        self.asked.append(symbol)
        self.asked_dates.append(d)   # pins WHICH session was read, not just which symbol
        if symbol in self.fail_for:
            raise RuntimeError("boom")
        return self.data.get(symbol, [])


@pytest.fixture
def _pin_session(monkeypatch):
    """Pin the display session date so RTH bounds are deterministic."""
    monkeypatch.setattr(compute, "_display_session_date",
                        lambda now, sd: datetime.date(2026, 8, 5))
    return datetime.date(2026, 8, 5)


def _rth_ts(hour, minute):
    """Unix ts for a CT wall-clock time on the pinned session date."""
    return datetime.datetime(2026, 8, 5, hour, minute,
                             tzinfo=compute._PROJ_CT_TZ).timestamp()


def test_build_net_premium_reads_every_source_symbol(monkeypatch, _pin_session):
    gh = _FakeGH({"SPY": [(_rth_ts(10, 0), 1.0, 0, 0, 10.0, 4.0)]})
    monkeypatch.setattr(compute, "_matrix_gh", lambda: gh)

    out = compute.build_net_premium(_pin_session)

    assert set(gh.asked) == set(np_mod.source_symbols())
    # Asked ONCE each, not merely "at least once": this runs on the 1-min GEX
    # branch, and this codebase has twice paid for a duplicated read.
    assert len(gh.asked) == len(np_mod.source_symbols())
    assert out["series"]["SPY"] == [[_rth_ts(10, 0), 10.0, 4.0]]
    assert out["session_date"] == "2026-08-05"
    assert out["error"] is None
    assert gh.closed is True


def _ct_ts(year, month, day, hour, minute):
    return datetime.datetime(year, month, day, hour, minute,
                             tzinfo=compute._PROJ_CT_TZ).timestamp()


def test_premarket_reads_and_crops_the_PRIOR_session(monkeypatch):
    """08:00-08:30 CT: the display date must drive BOTH the read and the crop.

    ``scheduler.active_session_date`` flips to today at the 08:00 COLLECTION start,
    but these charts render RTH only — so for that half hour today has rows and none
    of them displayable. Using ``session_date`` for either the read or the bounds
    would fetch today's near-empty rows and crop them to yesterday's window (or vice
    versa): an empty chart, no error, thirty minutes every trading morning, in the
    one window nobody is watching.

    The REAL ``_display_session_date`` runs here (deliberately NOT monkeypatched —
    the other tests in this file pin it to the date they pass, so in those
    ``display_date == session_date`` and this wiring is invisible). Two rows exactly
    24h apart discriminate the bounds: only the PRIOR day's survives.

    Passing ``now`` explicitly also exercises the tz-aware default's contract — a
    naive ``now`` would blow up on the comparison inside ``_display_session_date``.
    """
    today = datetime.date(2026, 8, 5)     # a Wednesday, not a holiday
    prior = datetime.date(2026, 8, 4)     # → the Tuesday
    assert today.weekday() == 2 and prior == today - datetime.timedelta(days=1)

    gh = _FakeGH({"SPY": [
        (_ct_ts(2026, 8, 4, 10, 0), 1.0, 0, 0, 10.0, 4.0),   # prior RTH — kept
        (_ct_ts(2026, 8, 5, 10, 0), 1.0, 0, 0, 99.0, 99.0),  # today's RTH — cropped out
    ]})
    monkeypatch.setattr(compute, "_matrix_gh", lambda: gh)

    out = compute.build_net_premium(
        today, now=datetime.datetime(2026, 8, 5, 8, 5, tzinfo=compute._PROJ_CT_TZ))

    assert out["session_date"] == "2026-08-04"          # reported as the prior session
    assert set(gh.asked_dates) == {prior}               # …and READ for it
    assert out["series"]["SPY"] == [[_ct_ts(2026, 8, 4, 10, 0), 10.0, 4.0]]  # …and CROPPED to it


def test_build_net_premium_crops_to_rth(monkeypatch, _pin_session):
    gh = _FakeGH({"SPY": [
        (_rth_ts(8, 5), 1.0, 0, 0, 1.0, 1.0),    # pre-open — dropped
        (_rth_ts(10, 0), 1.0, 0, 0, 10.0, 4.0),  # RTH — kept
        (_rth_ts(15, 10), 1.0, 0, 0, 9.0, 9.0),  # post-close — dropped
    ]})
    monkeypatch.setattr(compute, "_matrix_gh", lambda: gh)

    out = compute.build_net_premium(_pin_session)
    assert [r[0] for r in out["series"]["SPY"]] == [_rth_ts(10, 0)]


def test_one_symbol_read_failure_does_not_sink_the_build(monkeypatch, _pin_session):
    gh = _FakeGH({"SPY": [(_rth_ts(10, 0), 1.0, 0, 0, 10.0, 4.0)]},
                 fail_for=["XLK"])
    monkeypatch.setattr(compute, "_matrix_gh", lambda: gh)

    out = compute.build_net_premium(_pin_session)
    assert "SPY" in out["series"] and "XLK" not in out["series"]
    assert out["error"] is None


def test_db_unavailable_degrades_to_empty_series(monkeypatch, _pin_session):
    def _boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(compute, "_matrix_gh", _boom)

    out = compute.build_net_premium(_pin_session)
    assert out["series"] == {} and out["error"]


def test_premium_lands_where_build_series_expects_it():
    """Pins the positional contract with load_flow_series against a REAL DB.

    ``net_premium._project`` reads premium at tuple indices 4/5. A reordered
    SELECT in ``gex_history_db.load_flow_series`` would land call_vol/put_vol
    there instead — contract counts in the thousands where dollars in the
    millions belong. Both pass the numeric guard, so the chart would render a
    plausible-looking wrong answer and skew mode a plausible-looking percentage:
    completely silent. This cannot be pinned in the pure module without
    circularity (a pure test would only re-read the same index constants), so it
    lives here, where sqlite3 is already in scope.

    The stored values are deliberately far apart in MAGNITUDE (thousands of
    contracts vs millions of dollars) so a mis-index fails on VALUE rather than
    passing by coincidence.

    The second half pins ``sqlite3.Row``: the most ordinary row_factory there is,
    not a tuple or list, but supported by the positional read. An earlier
    iteration gated on ``isinstance(row, (tuple, list))`` and silently dropped
    it — an empty series is indistinguishable from a symbol nobody collects yet.

    Uses an in-memory connection built here, so ``gh.connect()`` (which opens the
    real ``DB_PATH``) is never called and the live 1.5 GB store is never touched.
    """
    import sqlite3
    import sys

    from repo_paths import OPTIONS_SCANNER
    if str(OPTIONS_SCANNER) not in sys.path:
        sys.path.insert(0, str(OPTIONS_SCANNER))
    import gex_history_db as gh

    session = datetime.date(2026, 8, 5)
    # A LOCAL-naive 10:00 → the machine's local tz, the same basis
    # ``gex_history_db._local_unix_range`` uses. Mid-morning by design: that
    # helper uses the CURRENT fixed UTC offset, so only rows near midnight could
    # ever land in the wrong day.
    ts = int(datetime.datetime(2026, 8, 5, 10, 0).timestamp())

    conn = sqlite3.connect(":memory:")
    try:
        gh.init_schema(conn)
        gh.insert_snapshot(conn, "SPY", "gex", {
            "ts": ts,
            "spot": 500.0,
            "call_vol": 7_000,          # contracts — the wrong-index values
            "put_vol": 8_000,
            "call_prem": 10_000_000.0,  # dollars — what must be plotted
            "put_prem": 4_000_000.0,
        }, {}, 0)

        rows = gh.load_flow_series(conn, "SPY", session)
        assert rows, "fixture row not readable — check the view/date filter"
        assert (np_mod.build_series({"SPY": rows})["SPY"]
                == [[ts, 10_000_000.0, 4_000_000.0]])

        conn.row_factory = sqlite3.Row
        row_rows = gh.load_flow_series(conn, "SPY", session)
        assert not isinstance(row_rows[0], (tuple, list))  # the exact trap
        assert (np_mod.build_series({"SPY": row_rows})["SPY"]
                == [[ts, 10_000_000.0, 4_000_000.0]])
    finally:
        conn.close()
