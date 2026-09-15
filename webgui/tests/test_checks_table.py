"""The shared Checks-column helpers (``checks_table.py``) - PURE parts.

The Market Scanner and the Strategy Finder both draw the checklist's one-chip
verdict and the "Only clear" filter. The helpers live in one widget-free module
so the Finder never imports the scanner PAGE for them, and the two tables
cannot word or filter the same verdict differently.
"""
import ast
import pathlib

from pages.options import checks_table as ct


def _imports(module):
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {node.module or ""} | {a.name for a in node.names}
    return names


def test_module_imports_no_widget_library_and_no_page():
    names = _imports(ct)
    assert names, "parsed no imports at all - the check is vacuous"
    assert not {n for n in names if n.split(".")[0] == "nicegui"}
    assert not {n for n in names if n.split(".")[-1] in ("scanner", "swing",
                                                          "strategy_table")}


def test_the_scanner_re_exports_the_shared_helpers_under_their_old_names():
    from pages.options import scanner
    assert scanner.stamp_checks is ct.stamp_checks
    assert scanner.only_clear is ct.only_clear
    assert scanner.restamp is ct.restamp
    assert scanner.only_clear_empty_label is ct.only_clear_empty_label
    assert scanner.filtered_tab_label is ct.filtered_tab_label
    assert scanner._CHECKS_SLOT is ct.CHECKS_SLOT
    assert scanner._ONLY_CLEAR_TIP is ct.ONLY_CLEAR_TIP


def test_filtered_tab_label_needs_no_scanner_helper():
    assert ct.filtered_tab_label("Swing", 40, 3, have=True, filtering=False) == "Swing (40)"
    assert ct.filtered_tab_label("Swing", 40, 3, have=True, filtering=True) == "Swing (3 of 40)"
    assert ct.filtered_tab_label("Swing", 40, 3, have=False, filtering=True) == "Swing"


# ── a memo spares a list that re-filters from re-stamping every row ──────────
def _counting_build(calls):
    def build(row, ctx):
        calls.append(row["id"])
        return [{"key": "cost", "tone": "pos", "text": "fine"}]
    return build


def test_stamp_checks_with_a_memo_stamps_each_id_once():
    calls, memo = [], {}
    sigs = [{"id": "a"}, {"id": "b"}]
    ct.stamp_checks([{"id": "a"}, {"id": "b"}], sigs, None,
                    build=_counting_build(calls), memo=memo)
    again = [{"id": "b", "_allow_paper": True}]
    ct.stamp_checks(again, sigs, None, build=_counting_build(calls), memo=memo)
    assert calls == ["a", "b"]
    assert set(memo) == {"a", "b"}
    assert again[0]["_checks_state"] == "pos" and again[0]["_checks_clear"] is True
    assert set(memo["b"]) == set(ct.CHECK_FIELDS)


def test_a_memo_entry_is_a_copy_the_row_cannot_change():
    memo = {}
    rows = [{"id": "a"}]
    ct.stamp_checks(rows, [{"id": "a"}], None, build=_counting_build([]), memo=memo)
    rows[0]["checks"] = "edited"
    assert memo["a"]["checks"] != "edited"


def test_without_a_memo_every_call_stamps_again():
    calls = []
    sigs = [{"id": "a"}]
    for _ in range(2):
        ct.stamp_checks([{"id": "a"}], sigs, None, build=_counting_build(calls))
    assert calls == ["a", "a"]


def test_read_and_restamp_reads_the_context_off_the_loop_and_returns_copies(monkeypatch):
    from pages.options import checks_feed
    monkeypatch.setattr(checks_feed, "read_context", lambda: "CTX")
    seen = []

    def build(row, ctx):
        seen.append(ctx)
        return [{"key": "cost", "tone": "warn", "text": "wide"}]

    monkeypatch.setattr(checks_feed, "checks_for", build)
    rows = [{"id": "a", "_checks_state": "pos", "_new": True}]
    ctx, fresh, memo = ct.read_and_restamp(rows, [{"id": "a"}])
    assert ctx == "CTX" and seen == ["CTX"]
    assert rows[0]["_checks_state"] == "pos"            # the painted row is untouched
    assert fresh[0] is not rows[0] and fresh[0]["_checks_state"] == "warn"
    assert fresh[0]["_new"] is True
    assert memo["a"]["_checks_state"] == "warn"


def test_the_empty_label_before_any_row_was_checked_says_the_checks_are_loading():
    rows = [{"id": "a"}, {"id": "b"}]
    assert ct.only_clear_empty_label(rows, [], filtering=True) == (
        "The checks haven't loaded yet — turn off Only clear to see all 2.")
    assert ct.only_clear(rows) == []                     # still fails closed


def test_the_scanner_restamps_through_the_shared_reader(monkeypatch):
    from pages.options import scanner
    seen = {}

    def shared(rows_by_key, sigs_by_key):
        seen["args"] = (rows_by_key, sigs_by_key)
        return "CTX", {"signals_swing": ["copy"]}, {}

    monkeypatch.setattr(ct, "read_and_restamp_tables", shared)
    assert scanner._read_and_restamp({"signals_swing": ["row"]}, {"signals_swing": []}) == {
        "signals_swing": ["copy"]}
    assert seen["args"] == ({"signals_swing": ["row"]}, {"signals_swing": []})
