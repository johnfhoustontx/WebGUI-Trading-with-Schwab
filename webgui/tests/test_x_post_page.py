"""The /x page's pure helpers: the live preview, the log table and the command."""
from pages import x_post as page


def test_preview_uses_the_service_rules():
    text, n = page.preview("Hello", "https://neuralstrike.co", ["options", "#trading"], 4)
    assert text == "Hello\n\nhttps://neuralstrike.co\n#options #trading"
    assert n == 5 + 2 + 23 + 1 + len("#options #trading")


def test_over_limit_is_flagged_on_the_unfitted_text():
    assert page.over_limit("z" * 400, "", [])
    assert not page.over_limit("short", "https://neuralstrike.co", ["#a"])
    _, n = page.preview("z" * 400, "", [], 4)
    assert n <= 280


def test_over_limit_counts_the_tags_the_post_would_carry():
    # 270 characters of body fit alone; the tags push the UNFITTED post over.
    body = "z" * 270
    assert not page.over_limit(body, "", [])
    assert page.over_limit(body, "", ["#options", "#trading"])


def test_parse_tags_splits_on_spaces_and_commas():
    assert page.parse_tags("#options, trading  $spy") == ["#options", "trading", "$spy"]
    assert page.parse_tags("") == [] and page.parse_tags(None) == []


def test_log_rows_are_newest_first_with_a_link_or_reason():
    rows = page.log_rows({"posts": [
        {"at": "2026-09-22T10:00:00-05:00", "kind": "report", "status": "posted",
         "url": "https://x.com/i/web/status/1", "text": "a", "reason": None},
        {"at": "2026-09-22T09:00:00-05:00", "kind": "trade_idea", "status": "refused",
         "url": None, "text": "b", "reason": "daily cap (15) reached"}]})
    assert rows[0]["kind"] == "Market report" and rows[0]["detail"].endswith("/1")
    assert rows[1]["kind"] == "Trade idea" and rows[1]["detail"] == "daily cap (15) reached"
    assert page.log_rows(None) == [] and page.log_rows({"posts": 5}) == []


def test_log_rows_format_time_status_and_long_text():
    rows = page.log_rows({"posts": [
        {"at": "2026-09-22T10:00:00-05:00", "kind": "marketing", "status": "dry_run",
         "text": "y" * 200, "url": None, "reason": None},
        {"at": "garbage", "kind": "???", "status": None, "text": None},
        "not a dict"]})
    assert len(rows) == 2
    assert rows[0]["when"] == "Sep 22 10:00"
    assert rows[0]["kind"] == "Marketing" and rows[0]["status"] == "Dry run"
    assert len(rows[0]["text"]) <= 80 and rows[0]["text"].endswith("…")
    assert rows[0]["detail"] == ""
    assert rows[1]["when"] == "" and rows[1]["status"] == "Unknown"
    assert rows[0]["id"] != rows[1]["id"]


def test_command_carries_base64_only_with_an_image():
    cmd = page.command("hi", ["#a"], "", None)
    assert cmd == {"type": "x_post", "args": {"text": "hi", "tags": ["#a"], "link": ""}}
    assert "image_b64" in page.command("hi", [], "", b"PNG")["args"]
    import base64
    assert base64.b64decode(page.command("hi", [], "", b"PNG")["args"]["image_b64"]) == b"PNG"


def test_the_page_imports_nothing_from_services_or_the_notify_package():
    import ast, pathlib
    src = pathlib.Path(page.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and m.startswith(("services", "shared.notify", "requests")) for m in mods)


# ── render: driven the way the browser does (the test_config_editor recipe) ──
def _render(monkeypatch, payload=None):
    from nicegui import ui
    import bus_client
    sent = []
    monkeypatch.setattr(bus_client, "read", lambda view: payload)
    monkeypatch.setattr(bus_client, "read_version", lambda view: 1)
    monkeypatch.setattr(bus_client, "request", lambda domain, cmd: sent.append((domain, cmd)))
    with ui.card() as host:
        page.render()
    return host, sent


def _one(host, cls):
    found = [e for e in host.descendants() if isinstance(e, cls)]
    assert found, f"no {cls.__name__} on the page"
    return found


def _button(host, text):
    from nicegui import ui
    (btn,) = [b for b in _one(host, ui.button) if b.text == text]
    return btn


def _fire_click(btn):
    from nicegui import helpers
    from nicegui.events import GenericEventArguments
    e = GenericEventArguments(sender=btn, client=btn.client, args=None)
    for li in list(btn._event_listeners.values()):
        if li.type == "click" and li.handler is not None:
            li.handler(e) if helpers.expects_arguments(li.handler) else li.handler()


def _confirm_post():
    import asyncio
    from nicegui import context, ui
    dlgs = [d for d in context.client.layout.descendants() if isinstance(d, ui.dialog)
            and any(isinstance(b, ui.button) and b.text == "Post" for b in d.descendants())]
    dlg = dlgs[-1]
    (run_,) = [li.handler for li in dlg._event_listeners.values()
               if li.type == "keydown.enter"]

    async def _drive(slot):
        with slot:
            await run_(None)

    asyncio.run(_drive(dlg.parent_slot))
    return dlg


def test_post_is_held_until_there_is_something_to_post(monkeypatch):
    from nicegui import ui
    host, _sent = _render(monkeypatch)
    post = _button(host, "Post")
    assert not post.enabled
    (body,) = _one(host, ui.textarea)
    body.value = "Hello"
    assert post.enabled
    labels = [e.text for e in _one(host, ui.label)]
    _, n = page.preview("Hello", page.DEFAULT_LINK, page.parse_tags(page.DEFAULT_TAGS))
    assert f"{n} / 280" in labels


def test_the_counter_turns_red_over_the_limit(monkeypatch):
    from nicegui import ui
    host, _sent = _render(monkeypatch)
    (body,) = _one(host, ui.textarea)
    counter = [e for e in _one(host, ui.label) if e.text.endswith("/ 280")][0]
    assert page.COUNT_OVER not in counter.classes
    body.value = "z" * 400
    assert page.COUNT_OVER in counter.classes
    body.value = "short"
    assert page.COUNT_OVER not in counter.classes


def test_confirming_sends_the_command_and_holds_the_button(monkeypatch):
    from nicegui import ui
    host, sent = _render(monkeypatch)
    (body,) = _one(host, ui.textarea)
    body.value = "Hello"
    post = _button(host, "Post")
    _fire_click(post)
    _confirm_post()
    assert sent == [("options", page.command(
        "Hello", page.parse_tags(page.DEFAULT_TAGS), page.DEFAULT_LINK, None))]
    assert not post.enabled          # held until the log answers


def test_the_log_renders_as_a_table_or_an_empty_line(monkeypatch):
    from nicegui import ui
    host, _ = _render(monkeypatch, {"posts": [
        {"at": "2026-09-22T10:00:00-05:00", "kind": "marketing", "status": "dry_run",
         "text": "hi", "url": None, "reason": None}]})
    (table,) = _one(host, ui.table)
    assert table.rows[0]["status"] == "Dry run"
    host2, _ = _render(monkeypatch, None)
    assert "Nothing posted yet." in [e.text for e in _one(host2, ui.label)]
