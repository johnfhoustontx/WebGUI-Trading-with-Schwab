"""The public-Finder measurement tool: its pure summary, and that it writes nothing."""
import ast
import pathlib

from tools import measure_finder_public as m

_SRC = pathlib.Path(m.__file__).read_text(encoding="utf-8")


def _row(**kw):
    base = {"type": "PCS", "group": "VERTICAL", "score": 60, "dte": 30,
            "expiration": "2026-10-16", "unbounded": False, "max_profit": 100.0}
    return {**base, **kw}


def test_loss_unbounded_reads_the_explicit_flag_first():
    assert m.loss_unbounded(_row(unbounded_loss=True))
    assert not m.loss_unbounded(_row(unbounded_loss=False))


def test_a_legacy_row_is_partitioned_by_max_profit():
    # Unbounded PROFIT (a long call) is not an undefined-risk row.
    assert not m.loss_unbounded(_row(unbounded=True, max_profit=None))
    assert m.loss_unbounded(_row(unbounded=True, max_profit=250.0))


def test_a_short_put_is_counted_apart_from_unbounded_loss():
    """Its loss is bounded (strike to zero) - D4 has to decide it separately."""
    sp = _row(type="SHORT_PUT", unbounded_loss=False)
    assert m.is_naked_short_put(sp) and not m.loss_unbounded(sp)


def test_summarize_counts_and_ranks():
    result = {"signals": [_row(score=50),
                          _row(type="SHORT_STRADDLE", group="STRADDLE",
                               score=90, unbounded_loss=True),
                          _row(type="SHORT_PUT", group="DIRECTIONAL", score=70)],
              "filtered_out": 12, "expirations_scanned": 9}
    s = m.summarize("SPY", result, 12.345, {"/chains": 3, "/quotes": 1})
    assert s["rows"] == 3 and s["http_calls"] == 4 and s["wall_s"] == 12.35
    assert s["rows_unbounded_loss"] == 1 and s["rows_naked_short_put"] == 1
    assert s["rows_by_group"] == {"DIRECTIONAL": 1, "STRADDLE": 1, "VERTICAL": 1}
    assert [t["score"] for t in s["top"]] == [90, 70, 50]
    assert s["top"][0]["unbounded_loss"] is True
    assert s["filtered_out"] == 12 and s["error"] is None


def test_an_empty_or_failed_result_summarizes_without_raising():
    s = m.summarize("X", {"signals": [], "error": "ConnectionError"}, 0.5, {})
    assert s["rows"] == 0 and s["error"] == "ConnectionError"
    assert m.totals([s])["errors"] == ["X"]


def test_endpoint_drops_the_query():
    assert m.endpoint("http://127.0.0.1:8100/chains?symbol=SPY") == "/chains"


def test_the_pin_is_the_roadmaps_and_the_pages_defaults():
    assert (m.PIN["dte_min"], m.PIN["dte_max"]) == (0, 90)
    assert (m.PIN["put_d_min"], m.PIN["put_d_max"]) == (-0.20, -0.10)
    assert (m.PIN["call_d_min"], m.PIN["call_d_max"]) == (0.10, 0.20)
    assert m.PIN["min_cr_fraction"] == 0.10


def test_the_call_counter_restores_requests():
    import requests
    orig = requests.Session.request
    with m.CallCounter():
        assert requests.Session.request is not orig
    assert requests.Session.request is orig


def test_it_calls_compute_never_the_handler_and_writes_nothing():
    """THE SAFETY ARGUMENT, at source level: compute.swing_scan is proxy-only;
    handlers.swing_scan would write cache:options:swing (the owner's slot)."""
    tree = ast.parse(_SRC)
    attrs = {(getattr(n.value, "id", None), n.attr) for n in ast.walk(tree)
             if isinstance(n, ast.Attribute)}
    assert ("compute", "swing_scan") in attrs
    assert ("handlers", "swing_scan") not in attrs
    for forbidden in ("cache_set", "enqueue_command", "publish(", "xadd"):
        assert forbidden not in _SRC, forbidden
