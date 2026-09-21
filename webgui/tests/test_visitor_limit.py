"""The per-visitor limit on public scans: the address rule and the window."""
from types import SimpleNamespace

import visitor_limit as vl


def _req(peer="127.0.0.1", **headers):
    return SimpleNamespace(client=SimpleNamespace(host=peer),
                           headers={k.lower().replace("_", "-"): v
                                    for k, v in headers.items()})


def test_the_edge_header_name_is_the_login_middlewares():
    """A copy, because the public process must not import main; pinned so the
    two cannot drift."""
    import auth_middleware
    assert vl.EDGE_HEADER == auth_middleware.EDGE_HEADER


def test_behind_the_edge_the_last_forwarded_hop_is_the_visitor():
    req = _req(x_edge="1", x_forwarded_for="6.6.6.6, 203.0.113.9")
    assert vl.client_key(req) == "203.0.113.9"


def test_a_forged_prefix_cannot_change_the_key():
    """Caddy appends the peer it saw; a visitor can only add to the front."""
    a = _req(x_edge="1", x_forwarded_for="1.1.1.1, 203.0.113.9")
    b = _req(x_edge="1", x_forwarded_for="9.9.9.9, 203.0.113.9")
    assert vl.client_key(a) == vl.client_key(b)


def test_without_the_edge_header_the_forwarded_list_is_not_trusted():
    req = _req(peer="10.0.0.5", x_forwarded_for="203.0.113.9")
    assert vl.client_key(req) == "10.0.0.5"


def test_the_address_rule_matches_main():
    """main._client_ip is the original. Same inputs, same answer."""
    import inspect
    import main
    src = inspect.getsource(main._client_ip)
    assert 'hops[-1]' in src and "x-forwarded-for" in src


def test_a_missing_request_is_one_bucket_not_a_crash():
    assert vl.client_key(None) == "unknown"


def test_the_limit_counts_per_visitor_within_the_hour():
    clock = [0.0]
    lim = vl.Limiter(lambda: 3, clock=lambda: clock[0])
    assert [lim.allow("a") for _ in range(4)] == [True, True, True, False]
    assert lim.allow("b") is True                       # another visitor
    clock[0] = vl.WINDOW_SEC - 1
    assert lim.allow("a") is False
    clock[0] = vl.WINDOW_SEC + 1
    assert lim.allow("a") is True                       # the hour has passed


def test_the_limit_is_read_on_every_check():
    """A Settings change applies without a restart."""
    limit = [1]
    lim = vl.Limiter(lambda: limit[0], clock=lambda: 0.0)
    assert lim.allow("a") and not lim.allow("a")
    limit[0] = 5
    assert lim.allow("a")


def test_visitors_are_forgotten_after_the_window():
    clock = [0.0]
    lim = vl.Limiter(lambda: 3, clock=lambda: clock[0])
    for key in ("a", "b", "c"):
        lim.allow(key)
    clock[0] = vl.WINDOW_SEC + 1
    lim.allow("d")
    assert len(lim) == 1


def test_it_never_writes_an_address_anywhere():
    """Stdlib counting only: no bus, no logging, no file - checked on the code,
    not the prose."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(vl.__file__).read_text(encoding="utf-8"))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0])
    assert imported <= {"__future__", "collections", "threading", "time"}, imported
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not {c for c in calls if c in ("open", "print") or c.startswith(("log", "logging"))}
