"""The scanner's TRACKED structures are read by exactly one module.

``signals.db`` holds two kinds of row since 2026-10-07: the credit spreads the
paper Account can open, and the structures the Market Scanner records only to
measure (``shared.structures.TRACKED_SCANNER_TYPES``). The second kind has a
different shape - a signed ``entry_credit``, a ``legs_json`` blob, no strike
columns - and more than a dozen readers were written for the first.

Two things keep them apart, and this file pins both at source level:

* every ``signal_db`` reader hides tracked rows unless the caller passes
  ``tracked=True`` (the behaviour is tested in
  ``options-scanner/tests/test_signal_db_tracked.py``), so the only way to see
  one is to ask;
* a reader that writes its OWN query over the two tables carries the shared
  exclusion clause.

A new caller of either kind fails here until it is named.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
CODE_DIRS = ("services", "options-scanner", "webgui", "shared", "tools",
             "schwab-proxy", "sentiment-dashboard", "trade-analyzer",
             "portfolio-analyzer")

# The only code that may ask for tracked rows, and why.
MAY_ASK = {
    "services/options_svc/tracked.py",       # the manage loop and the view
    "options-scanner/signal_recorder.py",    # the tracked rows' own per-symbol cap
    "options-scanner/signal_db.py",          # the readers pass their own flag on
}

# SQL over the ``signals`` table itself - not the words "from signals.db".
_SIGNALS_SQL = re.compile(r"\b(from|join)\s+signals\b(?!\.)", re.IGNORECASE)

# Modules that query the two tables themselves, and must exclude tracked rows.
OWN_QUERIES = {
    "tools/signal_calibration.py": "is_tracked_type",
    "tools/measure_commission_convention.py": "not_tracked_sql",
    "tools/replay_debate.py": "not_tracked_sql",
}


def _sources():
    for top in CODE_DIRS:
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "/tests/" in rel or "/.venv/" in rel or "/node_modules/" in rel:
                continue
            yield rel, path.read_text(encoding="utf-8", errors="replace")


def _asks_for_tracked(source):
    """Line numbers of calls passing ``tracked=`` anything but a literal False."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "tracked":
                continue
            if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                continue
            hits.append(node.lineno)
    return hits


def test_only_the_named_modules_ask_for_tracked_rows():
    askers = {rel: lines for rel, src in _sources()
              if "tracked=" in src and (lines := _asks_for_tracked(src))}
    assert set(askers) <= MAY_ASK, {k: v for k, v in askers.items()
                                    if k not in MAY_ASK}
    # The two that must ask actually do, so this cannot pass on a rename.
    assert {"services/options_svc/tracked.py",
            "options-scanner/signal_recorder.py"} <= set(askers)


def test_every_module_with_its_own_query_carries_the_exclusion():
    seen = set()
    for rel, src in _sources():
        if rel in ("options-scanner/signal_db.py", "shared/structures.py"):
            continue
        if not _SIGNALS_SQL.search(src):
            continue
        seen.add(rel)
        assert rel in OWN_QUERIES, (
            f"{rel} queries signals.db itself: exclude tracked rows "
            "(shared.structures.not_tracked_sql) and name it in OWN_QUERIES")
        assert OWN_QUERIES[rel] in src, rel
    assert seen == set(OWN_QUERIES)


def test_the_tracked_types_have_one_definition():
    """A second literal is a second list to keep in step."""
    holders = [rel for rel, src in _sources()
               if '"0DTE_STRUCT", "SWING_STRUCT"' in src
               or "'0DTE_STRUCT', 'SWING_STRUCT'" in src]
    assert holders == ["shared/structures.py"]


def test_the_service_reads_the_store_through_signal_db_only():
    """``compute`` and ``handlers`` hold no SQL over the signal tables, so the
    default exclusion in ``signal_db`` covers every reader in the service."""
    for rel in ("services/options_svc/compute.py", "services/options_svc/handlers.py",
                "services/options_svc/captured_score.py",
                "services/options_svc/rescue.py"):
        assert not _SIGNALS_SQL.search((ROOT / rel).read_text(encoding="utf-8")), rel
