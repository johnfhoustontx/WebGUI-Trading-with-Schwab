"""The proxy is the ONLY Schwab gateway - no second token holder in a service.

``deepdive/engine.py`` inherited a bypass mode from its source-repo CLI
heritage: it read ``tokens.json`` itself and called Schwab's API host,
sidestepping schwab-proxy. In-service it was never reachable (compute.py builds
``SchwabClient()`` with no args = proxy mode, and the token path was None), but
a credential-reading path living inside ``services/`` contradicts the stated
invariant, and the Schwab refresh token is a single rotating credential - two
holders can invalidate each other's session.
"""
import ast
import pathlib

ENGINE = pathlib.Path(__file__).resolve().parents[1] / "deepdive" / "engine.py"

# Checked against CODE, not prose: the docstring explaining the removal names the
# very things being banned, and a comment mentioning them is not a regression.
BANNED = ("api.schwabapi.com", "DIRECT_BASE", "--direct", "_load_token",
          "MARKETDATA_PREFIX")


def _code_without_docstrings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
            if not body:                       # never leave an empty body
                body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def test_engine_has_no_direct_to_schwab_path():
    code = _code_without_docstrings(ENGINE)
    for banned in BANNED:
        assert banned not in code, (
            f"deepdive/engine.py regrew a direct-to-Schwab path: {banned!r}. "
            "Every Schwab call in a service goes through schwab-proxy.")


def test_the_guard_can_actually_see_code():
    """A guard that reads an empty string passes vacuously."""
    code = _code_without_docstrings(ENGINE)
    assert "class SchwabClient" in code and "PASSTHROUGH_ROUTE" in code
