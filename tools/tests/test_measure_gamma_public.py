"""The public-Gamma measurement tool: its pure arithmetic, and that it writes nothing."""
import ast
import json
import pathlib

from tools import measure_gamma_public as m

_SRC = pathlib.Path(m.__file__).read_text(encoding="utf-8")


def _snap():
    return {"symbol": "NVDA", "spot": 180.0,
            "views": {v: {"data": {"180.0": 1.0}, "history": [[1, 2], [3, 4]]}
                      for v in m.HISTORY_VIEWS},
            "term": {}, "flow": []}


def test_payload_sizes_splits_history_out_like_the_publisher():
    snap = _snap()
    sizes = m.payload_sizes(snap)
    assert set(sizes["history"]) == set(m.HISTORY_VIEWS)
    assert all(h["rows"] == 2 for h in sizes["history"].values())
    assert sizes["total_bytes"] == sizes["main_bytes"] + sum(
        h["bytes"] for h in sizes["history"].values())
    # the main payload is measured WITHOUT the rows
    slim = json.loads(json.dumps(snap))
    for v in m.HISTORY_VIEWS:
        slim["views"][v].pop("history")
    assert sizes["main_bytes"] == len(json.dumps(slim))


def test_payload_sizes_leaves_the_callers_snapshot_alone():
    snap = _snap()
    m.payload_sizes(snap)
    assert all("history" in snap["views"][v] for v in m.HISTORY_VIEWS)


def test_expirations_counts_distinct_dates_across_both_sides():
    chain = {"callExpDateMap": {"2026-09-22:1": {}, "2026-09-24:3": {}},
             "putExpDateMap": {"2026-09-22:1": {}, "2026-09-26:5": {}}}
    assert m.expirations(chain) == 3
    assert m.expirations(None) == 0


def test_pair_ticks_matches_each_start_to_the_next_end():
    starts = [100.0, 160.0, 220.0]
    ends = [95.0, 140.0, 215.0]          # 95 is the previous tick's end
    assert m.pair_ticks(starts, ends) == [40.0, 55.0]   # 220 has no end: dropped


def test_pair_ticks_drops_an_end_too_far_away():
    assert m.pair_ticks([0.0], [500.0], max_s=180) == []


def test_p95_is_nearest_rank():
    assert m.p95([]) is None
    assert m.p95([10]) == 10
    assert m.p95(list(range(1, 101))) == 95


def test_hot_cap():
    assert m.hot_cap(40.0, 2.5, 50.0) == 4
    assert m.hot_cap(55.0, 2.5, 50.0) == 0       # already over: no headroom
    assert m.hot_cap(None, 2.5, 50.0) is None
    assert m.hot_cap(40.0, 0, 50.0) is None


def test_totals_ignores_errored_rows_for_the_means():
    ok = m.summarize("A", fetch_s=1, fetch_calls=1, cold_s=4, cold_calls=2,
                     warm_s=2, exp_count=5, sizes={"main_bytes": 10,
                                                   "history": {}, "total_bytes": 10})
    bad = m.summarize("B", fetch_s=0, fetch_calls=0, cold_s=0, cold_calls=0,
                      warm_s=0, exp_count=0, sizes=None, error="no chain")
    t = m.totals([ok, bad], [30.0, 40.0], 50.0)
    assert t["errors"] == ["B"] and t["warm_mean_s"] == 2
    assert t["hot_cap"] == m.hot_cap(40.0, 2, 50.0)


def test_it_never_calls_the_handlers_or_writes():
    """compute is proxy-only and writes nothing; handlers publish. The tool
    must stay on the compute side, and must never write Redis or SQLite."""
    tree = ast.parse(_SRC)
    names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(mod and mod.endswith("handlers") for mod in names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "services.options_svc":
            assert [a.name for a in node.names] == ["compute"]
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("cache_set", "enqueue_command", "set",
                                     "xadd", "insert_snapshot", "publish",
                                     "collect_gex_snapshots"), node.attr


def test_builds_skip_term_by_default(monkeypatch):
    """The public page has no Term view, so the measured build must not make
    Term's wider chain fetch -- and must put _term_chain back afterwards."""
    from services.options_svc import compute
    seen = {}
    orig = compute._term_chain

    def fake_measure(symbol):
        seen["term"] = compute._term_chain(symbol, {"x": 1})
        return {"symbol": symbol}

    monkeypatch.setattr(m, "_measure", fake_measure)
    m.measure("NVDA")
    assert seen["term"] is None
    assert compute._term_chain is orig
    m.measure("NVDA", with_term=True)
    assert compute._term_chain is orig
