"""No NEW silent guard may swallow a whole computation.

The repo's most expensive bug class is `try/except Exception -> return a
plausible default` with nothing logged: the bug becomes a confident number and
stays invisible. A 2026-08-21 census found 289 such handlers in services/, and
the useful split was by SIZE:

* **41 guarded >= 15 lines** - these swallow an entire computation (the worst
  wrapped 294 lines and returned `_neutral_trend()`). All now call
  `_degrade.degraded(area)`, which logs with a traceback and counts for /health.
* **248 guarded < 15 lines** - one-statement parse guards
  (`try: return float(x) except: return None`). Those are LEFT ALONE on purpose:
  a WARNING per row per tick is spam, not observability, and there the
  missing-value contract is the point rather than a failure.

So this guard is deliberately scoped to the big ones. Enabling ruff's BLE001
instead was considered and rejected: it flags every `except Exception` (542 of
them here), which would need 542 grandfathered noqa comments and would dilute the
signal to nothing - and it conflicts with the documented rule that a new ruff
rule class is only added once the tree is already clean under it.

Tier 1 is not covered: `webgui/` cannot import `services.*`, and its guards are
all small.
"""
import ast
import pathlib

import pytest

SERVICES = pathlib.Path(__file__).resolve().parents[1]
MIN_BODY = 15
SPEAKS = ("log.", "logger.", "logging.", "notify", "print(", "_degrade.")


def _silent_big_guards():
    out = []
    for path in sorted(SERVICES.rglob("*.py")):
        parts = path.parts
        if "tests" in parts or "__pycache__" in parts or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                      # not ours to police
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                if getattr(h.type, "id", None) != "Exception":
                    continue
                body = (max(getattr(n, "end_lineno", n.lineno) for n in node.body)
                        - min(n.lineno for n in node.body) + 1)
                if body < MIN_BODY:
                    continue
                mod = ast.Module(body=h.body, type_ignores=[])
                src = ast.unparse(mod)
                if any(t in src for t in SPEAKS):
                    continue
                if any(isinstance(n, ast.Raise) for n in ast.walk(mod)):
                    continue                     # re-raises: the caller will see it
                rel = path.relative_to(SERVICES.parent)
                out.append(f"{str(rel).replace(chr(92), '/')}:{h.lineno} "
                           f"({body} lines guarded)")
    return out


def test_no_silent_guard_swallows_a_whole_computation():
    offenders = _silent_big_guards()
    assert not offenders, (
        "These handlers swallow >= {} lines and say nothing. Add "
        "`_degrade.degraded(\"<domain>.<func>\")` as the first line of the "
        "handler (it logs with a traceback and counts for /health), or log "
        "there yourself:\n  {}".format(MIN_BODY, "\n  ".join(offenders)))


def test_the_scan_actually_reaches_the_code():
    """A guard that silently scans nothing passes vacuously forever."""
    seen = 0
    for path in SERVICES.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        seen += 1
    assert seen > 30, f"only walked {seen} service modules - is the root wrong?"


@pytest.mark.parametrize("domain", ["options_svc", "sentiment_svc", "driver_svc"])
def test_the_domains_that_were_fixed_import_the_helper(domain):
    """Cheap canary: these three carried the worst offenders."""
    src = (SERVICES / domain / "compute.py").read_text(encoding="utf-8")
    assert "from services import _degrade" in src
