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

**"Speaks" is judged on CALL NODES** (2026-10-04, audit CQ-06). It used to be a
substring test on the handler's source - `"log." in src` - and two handlers
around PAID Claude calls passed it because the error page they return tells the
reader to check "the service log.". The engine folder and `shared/` are covered
too: the same bug class lives there and nothing was looking.
"""
import ast
import pathlib

import pytest

SERVICES = pathlib.Path(__file__).resolve().parents[1]
REPO = SERVICES.parent
ROOTS = (SERVICES, REPO / "options-scanner", REPO / "shared")
MIN_BODY = 15
# A call ON one of these names is the handler saying something.
LOGGERS = {"log", "logger", "logging", "_log", "LOG", "_degrade"}
LOG_METHODS = {"exception", "warning", "error", "critical", "info", "degraded"}


def speaks(handler_body) -> bool:
    """True when the handler CALLS a logger, the degrade counter, a notifier or
    ``print``, or re-raises. Text that merely mentions a log does not count."""
    for n in ast.walk(ast.Module(body=list(handler_body), type_ignores=[])):
        if isinstance(n, ast.Raise):
            return True                          # the caller will see it
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            if f.id == "print" or f.id.startswith("notify"):
                return True
        elif isinstance(f, ast.Attribute):
            base = f.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in LOGGERS:
                return True
            if f.attr in LOG_METHODS or f.attr.startswith("notify"):
                return True
    return False


def _silent_big_guards(roots=ROOTS):
    out = []
    paths = sorted(p for root in roots for p in root.rglob("*.py"))
    for path in paths:
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
                if speaks(h.body):
                    continue
                rel = path.relative_to(REPO)
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


def _handler(src):
    return ast.parse(src).body[0].handlers[0].body


def test_text_that_mentions_a_log_is_not_speaking():
    """The two handlers this guard let through, in miniature."""
    quiet = _handler(
        "try:\n    x()\nexcept Exception as exc:\n"
        "    return {'html': f'<p>failed: {exc}</p><p>Check the service log.</p>'}\n")
    assert speaks(quiet) is False


@pytest.mark.parametrize("line", [
    "log.exception('x')", "logger.warning('x')", "_degrade.degraded('a.b')",
    "self.log.error('x')", "print('x')", "notify_failure(exc)", "raise",
    "ui.notify('x')",
])
def test_a_real_call_is_speaking(line):
    assert speaks(_handler(f"try:\n    x()\nexcept Exception as exc:\n    {line}\n"))


def test_the_engine_folder_and_shared_are_covered_too():
    walked = {p.parts[len(REPO.parts)] for root in ROOTS for p in root.rglob("*.py")}
    assert {"services", "options-scanner", "shared"} <= walked


def test_the_scan_actually_reaches_the_code():
    """A guard that silently scans nothing passes vacuously forever."""
    seen = 0
    for path in SERVICES.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        seen += 1
    assert seen > 30, f"only walked {seen} service modules - is the root wrong?"


@pytest.mark.parametrize("domain", ["options_svc", "sentiment_svc"])
def test_the_domains_that_were_fixed_import_the_helper(domain):
    """Cheap canary: these carried the worst offenders (driver_svc, the third,
    was removed 2026-09-22)."""
    src = (SERVICES / domain / "compute.py").read_text(encoding="utf-8")
    assert "from services import _degrade" in src
