"""Click-through on the published screens: it must land somewhere that exists.

The pages navigate with the PRIVATE app's route table, and ``live_screens``
re-maps most of those paths — ``/options/matrix`` is served at ``/opportunity``,
``/options/flow`` at ``/flow``, ``/sentiment/bullbear`` at ``/bullbear``. Only
``/sentiment`` survives by coincidence of naming, so on the Desk and the Flow
Alerts tape every internal link was a 404 and the whole feature read as broken.

Two sub-problems, two answers:

* a published page that lives at a DIFFERENT path is navigated to at that path.
  The map is DERIVED from ``live_screens.SCREENS`` — each Screen names both the
  route the private app serves it at and the route this origin publishes it at,
  so the two cannot disagree and there is no second table to maintain.
* a route that is deliberately NOT published — ``/options/paper``, ``/driver``,
  ``/options/captured`` — has nowhere to go, so the control is not drawn as
  clickable and the handler refuses. No public stand-in is invented for it, and
  nothing links to ``app.neuralstrike.co``: the site must never advertise the
  private app.
"""
import ast
import pathlib

import pytest

import live_screens

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "main.py"
_PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


# --- the map is derived, and its halves are checked against reality ---------

def test_public_routes_maps_each_private_route_to_the_screen_that_publishes_it():
    """Derived from the table, never written out a second time."""
    expected = {}
    for s in live_screens.SCREENS:
        expected.setdefault(s.private_route, s.route)
    assert live_screens.PUBLIC_ROUTES == expected
    assert live_screens.PUBLIC_ROUTES, "an empty map would make every test vacuous"


def test_a_dealer_positioning_click_lands_on_the_plain_gamma_screen():
    """FOUR screens render ``options.gamma`` — Gamma, Net Prem and the two
    Premium Divergence boards — so ``/options/gamma`` has four candidates and
    the table's ORDER decides. That is the one place order is load-bearing, so
    it is asserted rather than left to be discovered."""
    assert live_screens.PUBLIC_ROUTES["/options/gamma"] == "/gamma"


def _page_routes_to_modules():
    """``{route: {dotted page module}}`` read off ``main.py``'s own source.

    ``@_page("/x")`` decorates a function whose body does
    ``from pages... import <module>``, which is the only statement of what the
    private app serves where. Reading it here is what stops ``private_route``
    becoming a field someone typed once and nobody ever checked."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = [d.args[0].value for d in node.decorator_list
                  if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                  and d.func.id == "_page" and d.args
                  and isinstance(d.args[0], ast.Constant)]
        if not routes:
            continue
        mods = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and (sub.module or "") == "pages":
                mods |= {a.name for a in sub.names}
            elif isinstance(sub, ast.ImportFrom) \
                    and (sub.module or "").startswith("pages."):
                pkg = sub.module[len("pages."):]
                mods |= {f"{pkg}.{a.name}" for a in sub.names}
        for route in routes:
            out.setdefault(route, set()).update(mods)
    return out


def test_every_screen_names_the_route_the_private_app_really_serves_it_at():
    """THE NON-DRIFT GUARD.

    ``private_route`` is the one field with no runtime consequence on the
    private app, so a typo in it would show up only as a 404 on the public
    site. This reads main.py and asserts that the route each screen names is
    (a) a route main registers and (b) the one that renders that screen's own
    page module."""
    served = _page_routes_to_modules()
    assert served, "no @_page routes found — the check would be vacuous"
    for s in live_screens.SCREENS:
        assert s.private_route in served, (
            f"{s.slug} names private_route {s.private_route!r}, which main.py "
            "does not register")
        assert s.module in served[s.private_route], (
            f"{s.slug} names private_route {s.private_route!r}, but main.py "
            f"renders pages.{sorted(served[s.private_route])} there — not "
            f"pages.{s.module}")


# --- the shell resolves a route to wherever it lives in THIS process --------

@pytest.fixture
def published():
    import shell
    shell.publish(live_screens.PUBLIC_ROUTES)
    yield
    shell.unpublish()


def test_the_private_process_resolves_every_route_to_itself():
    """⚠ THE ONE THAT PROTECTS THE APP. With no ``publish()`` the seam is a
    pass-through, so ``navigate_to`` is ``ui.navigate.to`` and every route —
    published or not — stays navigable."""
    import shell
    for route in ("/options/matrix", "/options/flow", "/sentiment/bullbear",
                  "/options/paper", "/driver", "/options/captured",
                  "/options/gamma", "/sentiment"):
        assert shell.route_for(route) == route
        assert shell.can_navigate(route) is True


def test_the_published_process_remaps_what_it_serves(published):
    import shell
    assert shell.route_for("/options/matrix") == "/opportunity"
    assert shell.route_for("/options/flow") == "/flow"
    assert shell.route_for("/sentiment/bullbear") == "/bullbear"
    assert shell.route_for("/options/gamma") == "/gamma"
    assert shell.route_for("/sentiment") == "/sentiment"


def test_the_published_process_refuses_a_route_it_does_not_serve(published):
    """Deliberately unpublished — a paper ledger, the autonomous driver's book
    and the captured-signal tape are the owner's positions."""
    import shell
    for route in ("/options/paper", "/driver", "/options/captured",
                  "/settings", "/terminate"):
        assert shell.route_for(route) is None
        assert shell.can_navigate(route) is False


def test_navigate_to_an_unserved_route_is_a_no_op(published, monkeypatch):
    import shell
    went = []
    monkeypatch.setattr(shell.ui.navigate, "to",
                        lambda *a, **k: went.append(a))
    shell.navigate_to("/options/paper")
    assert went == []
    shell.navigate_to("/options/matrix")
    assert went == [("/opportunity",)]


# --- no page reaches ui.navigate.to behind the seam's back ------------------
# Enumerated over the page modules a published screen can reach, so a page
# added next year is covered without anyone remembering to add it here.

def _module_path(dotted):
    return _PAGES.joinpath(*dotted.split(".")).with_suffix(".py")


def _reachable_modules():
    """Every ``pages.*`` module transitively imported by a published screen."""
    seen, todo = set(), [s.module for s in live_screens.SCREENS]
    while todo:
        dotted = todo.pop()
        if dotted in seen:
            continue
        seen.add(dotted)
        path = _module_path(dotted)
        if not path.is_file():
            continue
        pkg = dotted.rsplit(".", 1)[0] if "." in dotted else ""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                mod = n.module or ""
                if n.level:                    # from .handoff import send_to_gamma
                    mod = f"{pkg}.{mod}" if mod else pkg
                    found |= {f"{mod}.{a.name}" if mod else a.name
                              for a in n.names}
                elif mod == "pages":
                    found |= {a.name for a in n.names}
                elif mod.startswith("pages."):
                    sub = mod[len("pages."):]
                    found.add(sub)
                    found |= {f"{sub}.{a.name}" for a in n.names}
            elif isinstance(n, ast.Import):
                found |= {a.name[len("pages."):] for a in n.names
                          if a.name.startswith("pages.")}
        todo += [m for m in found if _module_path(m).is_file()]
    return sorted(seen)


def _navigate_calls(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "to"
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "navigate"]


def test_the_walk_reaches_the_pages_that_actually_navigate():
    """Non-vacuity: the closure must really contain the navigating modules."""
    reachable = set(_reachable_modules())
    assert {"desk", "options.gamma", "options.handoff"} <= reachable
    assert len(reachable) > 20, reachable


def test_no_reachable_page_navigates_to_an_internal_route_behind_the_seam():
    """THE ENUMERATION.

    A bare ``ui.navigate.to("/options/matrix")`` is correct in the private app
    and a 404 on the public origin, and nothing about reading the line says
    which process it will run in. So the rule is ABSOLUTE for every page a
    published screen can reach — no "unless it is behind a gate", which would
    make the reviewer of the next page re-derive whether that gate really
    closes. ``shell.navigate_to`` is the identity in the private app, so
    uniformity is free.

    ⚠ Scoped by the IMPORT CLOSURE of the published screens, which
    over-approximates what a published render can actually reach (the Desk
    imports ``options.paper`` for one pure helper). Over-approximating is the
    safe direction here: the cost is routing a private-only navigation through
    a no-op resolver."""
    offenders = []
    for dotted in _reachable_modules():
        path = _module_path(dotted)
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"pages/{dotted.replace('.', '/')}.py:{call.lineno}"
                      for call in _navigate_calls(tree)]
    assert offenders == [], (
        f"{offenders} call ui.navigate.to directly. On live.neuralstrike.co "
        "the private route table does not exist, so that is a 404. Use "
        "shell.navigate_to(<private route>), which resolves it — or None.")


# --- what the Desk actually wires, in both processes ------------------------
# The Desk carries every one of the five click-throughs, so it is driven here
# rather than asserted from source: a handler that navigates correctly and is
# never wired to anything looks identical in a file.

def _desk_clicks(monkeypatch):
    """``([targets], [row elements with a click listener])`` for one render.

    Every click listener the Desk just wired is DRIVEN with ``ui.navigate.to``
    captured, which is the only way to see where a lambda actually goes."""
    from nicegui import ui
    from pages import desk

    # ONE patch covers every module: ``from nicegui import ui`` binds the same
    # module object everywhere, so ``shell.ui.navigate`` and ``handoff.ui.
    # navigate`` ARE this object. Patching each separately would only look
    # thorough.
    went = []
    monkeypatch.setattr(ui.navigate, "to", lambda *a, **k: went.append(a[0]))

    before = set(ui.context.client.elements)
    desk.render()
    clickable = []
    for key, el in ui.context.client.elements.items():
        if key in before:
            continue
        for listener in el._event_listeners.values():
            if listener.type == "click":
                clickable.append(el)
                listener.handler(None)
    return went, clickable


def _seed_desk(monkeypatch):
    """Enough published data that every clickable panel really draws a row.

    A panel with a cold cache renders a placeholder and NO row, so without this
    the navigation assertions below would pass by drawing nothing at all."""
    import bus_client
    from pages import desk

    def _pos(pid, source):
        return {"position_id": pid, "source": source, "symbol": "SPY",
                "structure": "PUT_CREDIT", "quantity": 1, "status": "OPEN"}

    data = {
        "options:paper_account": {"positions": [_pos("p1", desk.PAPER_SOURCE)]},
        "options:driver_paper_account": {
            "positions": [_pos("d1", desk.CLAUDE_SOURCE)]},
        "options:captured": {"signals": [{"signal_id": "c1",
                                          "source": desk.CAPTURED_SOURCE,
                                          "symbol": "QQQ", "status": "OPEN"}]},
        "options:matrix": {"rows": [{"symbol": "$SPX", "hotness": 50,
                                     "signal": "buy", "spot": 5000.0}]},
        "options:flow_alerts": {"alerts": [{"id": "f1", "symbol": "SPY",
                                            "type": "crossover", "side": "call",
                                            "text": "x", "ts": 1}]},
        "sentiment:bullbear": {"levels": {"sector": [
            {"symbol": "XLK", "name": "Technology", "trend": 1.0, "rs": 0.4,
             "day_move": 0.5}]}},
    }
    monkeypatch.setattr(bus_client, "read", lambda v: data.get(v))
    monkeypatch.setattr(bus_client, "read_full",
                        lambda v: (data.get(v), 1 if v in data else None))
    monkeypatch.setattr(bus_client, "read_versions",
                        lambda vs: {v: (1 if v in data else None) for v in vs})


def _position_rows():
    """The position panel's DATA rows, and not its column head — which carries
    the same ``POS_GRID`` and is never a link in either process."""
    from nicegui import ui
    from pages import desk

    before = set(ui.context.client.elements)
    desk.render()
    return [" ".join(e._classes)
            for key, e in ui.context.client.elements.items() if key not in before
            if desk.POS_GRID in " ".join(e._classes)
            and "py-[11px]" in " ".join(e._classes)]


def test_the_private_desk_navigates_exactly_where_it_always_did(monkeypatch):
    """⚠ THE ONE THAT PROTECTS THE APP. Every one of these click-throughs is
    how the Desk stays as terse as it is — a row you cannot open is a row you
    have to go and find."""
    _seed_desk(monkeypatch)
    went, _ = _desk_clicks(monkeypatch)
    assert "/sentiment" in went                     # the regime card
    assert "/options/matrix" in went                # an Opportunity row
    assert "/options/flow" in went                  # a Flow row
    assert "/sentiment/bullbear" in went            # the Bull/Bear strip
    assert "/options/gamma" in went                 # a dealer row
    assert {"/options/paper", "/driver", "/options/captured"} <= set(went)


def test_the_published_desk_navigates_only_where_this_origin_serves(
        monkeypatch, published):
    """Nothing 404s, and nothing points at the private app."""
    _seed_desk(monkeypatch)
    went, _ = _desk_clicks(monkeypatch)
    served = {s.route for s in live_screens.SCREENS}
    assert set(went) <= served, f"404 on the live origin: {set(went) - served}"
    # And the remap really happened rather than the page drawing nothing.
    assert {"/opportunity", "/flow", "/bullbear", "/sentiment", "/gamma"} \
        <= set(went)


def test_the_published_desk_draws_no_clickable_position_row(monkeypatch,
                                                            published):
    """The paper ledger, the driver's book and the captured tape are the
    OWNER'S positions and are deliberately unpublished, so those rows have
    nowhere to go. They are drawn — the panel is the point — but they are not
    dressed as a link and they wire no handler."""
    from nicegui import ui
    from pages import desk

    _seed_desk(monkeypatch)
    _, clickable = _desk_clicks(monkeypatch)
    assert not any(desk.POS_GRID in " ".join(el._classes) for el in clickable), \
        "a position row is still clickable on the public origin"

    # ...and the cursor does not promise a click that will not happen.
    before = set(ui.context.client.elements)
    desk.render()
    rows = [" ".join(e._classes) for key, e in ui.context.client.elements.items()
            if key not in before and desk.POS_GRID in " ".join(e._classes)]
    assert rows, "no position row was drawn — the assertion would be vacuous"
    assert not any("cursor-pointer" in r for r in rows)


def test_the_private_desk_still_dresses_a_position_row_as_a_link(monkeypatch):
    """Non-vacuity for the test above, and the private-app guarantee."""
    _seed_desk(monkeypatch)
    rows = _position_rows()
    assert rows and all("cursor-pointer" in r for r in rows)


def test_the_flow_tape_hands_off_to_the_published_gamma_screen(monkeypatch,
                                                               published):
    """``/options/flow``'s row click is ``handoff.send_to_gamma``, which both
    stashes the symbol and navigates. ⚠ The published Gamma screen PINS its
    symbol, so the stash cannot be honoured there and the visitor lands on the
    pinned board — the page says which symbol it is showing, and a 404 is the
    worse of the two. Recorded here so the trade-off cannot change silently."""
    from pages.options import handoff

    went = []
    monkeypatch.setattr(handoff.ui.navigate, "to", lambda *a, **k: went.append(a[0]))
    monkeypatch.setattr(handoff.ui, "notify", lambda *a, **k: None)
    handoff.send_to_gamma("SPY")
    assert went == ["/gamma"]
