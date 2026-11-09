"""The Social Cards page (/social): the card list, the image route, and the
buttons that hand a card to the absorbed Post to X composer."""
import asyncio
import json

import pytest

import bus_client
import repo_paths
from pages import social_cards as page
from pages import x_post
from shared import social_cards as cards

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 40
JPG = b"\xff\xd8\xff" + b"j" * 40
AT_NEW = "2026-11-10T09:00:00-06:00"
AT_OLD = "2026-11-09T09:00:00-06:00"
AT_BEFORE = "2026-10-01T09:00:00-05:00"        # before [cards] listed_from


SCRIPTS = []          # what the page asked the browser to run


def _row(key, kind, at, title, caption="cap", link="https://example.test/x"):
    stem = key.replace(":", "-")
    return cards.row(key, kind, at, title, caption, link,
                     f"social/{stem}.png", f"social/{stem}.jpg")


BLOG = {"cards": [_row("blog:alpha", "blog", AT_OLD, "Alpha entry",
                       caption="Alpha entry\n\nRead it at https://neuralstrike.co/blog/alpha/"),
                  _row("blog:old", "blog", AT_BEFORE, "Too old to list")]}
IDEAS = {"site_enabled": True,
         "cards": [_row("idea:2026-11-10-spy", "idea", AT_NEW, "SPY iron condor",
                        caption="SPY iron condor idea #0DTE")]}


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "social").mkdir()
    for key in ("blog-alpha", "blog-old", "idea-2026-11-10-spy"):
        (tmp_path / "social" / f"{key}.png").write_bytes(PNG)
        (tmp_path / "social" / f"{key}.jpg").write_bytes(JPG)
    monkeypatch.setattr(repo_paths, "SITE_ROOT", tmp_path)
    return tmp_path


def _serve(site, key, fmt, download=False, blog=BLOG, ideas=IDEAS):
    return page.serve(key, fmt, download, blog, ideas, site)


# ── the list ────────────────────────────────────────────────────────────────
def test_rows_merge_and_hide_unlisted():
    rows = page.rows_for(BLOG, IDEAS)
    assert [r["key"] for r in rows] == ["idea:2026-11-10-spy", "blog:alpha"]   # newest first
    assert page.rows_for(None, None) == []


# ── the image route ─────────────────────────────────────────────────────────
def test_route_serves_listed_png_and_jpg(site):
    assert _serve(site, "blog:alpha", "png") == (PNG, "image/png", None)
    assert _serve(site, "idea:2026-11-10-spy", "jpg") == (JPG, "image/jpeg", None)


def test_route_404s_unknown_key_and_traversal(site):
    (site.parent / "secret.png").write_bytes(b"secret")
    for key, fmt in [("blog:nope", "png"),                      # not in either view
                     ("blog:old", "png"),                       # in a view but unlisted
                     ("blog:../secret", "png"),                 # a traversal in the key
                     ("../secret", "png"), ("blog:alpha/../x", "png"),
                     ("blog:alpha", "gif"), ("blog:alpha", "../x"),
                     ("", "png"), ("blog:", "png")]:
        assert _serve(site, key, fmt) is None, (key, fmt)
    # A row whose own path climbs out of the site folder is not served either.
    evil = {"cards": [cards.row("blog:evil", "blog", AT_NEW, "t", "c", "l",
                                "../secret.png", "../secret.jpg")]}
    assert _serve(site, "blog:evil", "png", blog=evil) is None
    # A listed row whose file is gone is a 404, not an error.
    (site / "social" / "blog-alpha.png").unlink()
    assert _serve(site, "blog:alpha", "png") is None


def test_download_sets_attachment_name(site):
    _data, media, disposition = _serve(site, "idea:2026-11-10-spy", "jpg", True)
    assert media == "image/jpeg"
    assert disposition == 'attachment; filename="idea-2026-11-10-spy.jpg"'
    assert _serve(site, "blog:alpha", "png", True)[2] == 'attachment; filename="blog-alpha.png"'


def test_the_main_route_is_registered_and_answers(site, monkeypatch):
    import main
    from nicegui import app

    monkeypatch.setattr(bus_client, "read", lambda view: BLOG if view == cards.VIEW_BLOG else IDEAS)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/social/card/{key}/{fmt}" in paths
    resp = main._serve_social_card("blog:alpha", "png", _Req(""))
    assert resp.status_code == 200 and resp.body == PNG
    assert resp.headers["content-type"] == "image/png"
    assert "content-disposition" not in resp.headers
    resp = main._serve_social_card("blog:alpha", "jpg", _Req("download=1"))
    assert resp.headers["content-disposition"] == 'attachment; filename="blog-alpha.jpg"'
    for key in ("blog:nope", "blog:../x"):
        assert main._serve_social_card(key, "png", _Req("")).status_code == 404
    assert main._serve_social_card("blog:alpha", "gif", _Req("")).status_code == 404


class _Req:
    def __init__(self, query):
        from starlette.datastructures import QueryParams
        self.query_params = QueryParams(query)


def test_x_redirects_to_social():
    import main
    resp = main._x_to_social()
    assert resp.status_code == 308 and resp.headers["location"] == "/social"
    from nicegui import Client
    assert "/x" not in set(Client.page_routes.values())
    assert "/social" in set(Client.page_routes.values())


# ── the page ────────────────────────────────────────────────────────────────
def _render(monkeypatch, blog=BLOG, ideas=IDEAS):
    from nicegui import run, ui

    sent = []

    def read(view):
        return {cards.VIEW_BLOG: blog, cards.VIEW_IDEAS: ideas}.get(view)

    async def inline(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(bus_client, "read", read)
    monkeypatch.setattr(bus_client, "read_version", lambda view: 1)
    monkeypatch.setattr(bus_client, "request", lambda domain, cmd: sent.append((domain, cmd)))
    monkeypatch.setattr(run, "io_bound", inline)
    SCRIPTS.clear()
    monkeypatch.setattr(ui, "run_javascript", lambda js, **k: SCRIPTS.append(js))
    with ui.card() as host:
        page.render()
    return host, sent


def _all(host, cls):
    return [e for e in host.descendants() if isinstance(e, cls)]


def _buttons(host, text):
    from nicegui import ui
    return [b for b in _all(host, ui.button) if b.text == text]


def _click(btn):
    """Fire a button's click the way the browser does. NiceGUI schedules an async
    handler as a background task, so the task is caught and run to its end."""
    from nicegui import background_tasks
    from nicegui.events import GenericEventArguments
    e = GenericEventArguments(sender=btn, client=btn.client, args=None)
    tasks = []
    real = background_tasks.create_or_defer
    background_tasks.create_or_defer = lambda coro, **k: tasks.append(coro)
    try:
        for li in list(btn._event_listeners.values()):
            if li.type == "click" and li.handler is not None:
                li.handler(e)
    finally:
        background_tasks.create_or_defer = real

    async def _drive():
        for t in tasks:
            await t

    asyncio.run(_drive())


def test_each_card_has_the_three_buttons_in_row_order(site, monkeypatch):
    host, _ = _render(monkeypatch)
    for text in ("Download", "Copy caption", "Post to X"):
        assert len(_buttons(host, text)) == 2, text
    labels = [e.text for e in _all(host, __import__("nicegui").ui.label)]
    assert labels.index("SPY iron condor") < labels.index("Alpha entry")      # newest first
    assert "Too old to list" not in labels
    assert "Trade idea" in labels and "Blog" in labels


def test_post_to_x_fills_composer_without_sending(site, monkeypatch):
    from nicegui import ui
    host, sent = _render(monkeypatch)
    # The first button is the newest row's (the trade idea).
    first = _buttons(host, "Post to X")[0]
    _click(first)
    (body,) = _all(host, ui.textarea)
    assert body.value == "SPY iron condor idea #0DTE"
    values = [i.value for i in _all(host, ui.input)]
    assert values == ["SPY iron condor idea #0DTE", "https://example.test/x", ""]   # post, Link, Hashtags (emptied)
    assert sent == []                                   # nothing sent, no request at all
    assert len(SCRIPTS) == 1 and "scrollIntoView" in SCRIPTS[0]
    # Only the composer's own confirm sends - with the card attached.
    _click(_buttons(host, "Post")[0])
    assert sent == []                                   # opening the confirm sends nothing
    from nicegui import context
    dlg = [d for d in context.client.layout.descendants() if isinstance(d, ui.dialog)
           and any(isinstance(b, ui.button) and b.text == "Post" for b in d.descendants())][-1]
    body_lbl = [e for e in dlg.descendants() if isinstance(e, ui.label)
                and "[with image]" in (e.text or "")]
    assert body_lbl, "the confirm should say the post carries the image"
    _confirm(dlg)
    assert len(sent) == 1
    domain, cmd = sent[0]
    assert domain == "options" and cmd["type"] == "x_post"
    import base64
    assert base64.b64decode(cmd["args"]["image_b64"]) == PNG
    assert cmd["args"]["text"] == "SPY iron condor idea #0DTE"


def _confirm(dlg):
    (run_,) = [li.handler for li in dlg._event_listeners.values()
               if li.type == "keydown.enter"]

    async def _drive(slot):
        with slot:
            await run_(None)

    asyncio.run(_drive(dlg.parent_slot))


def test_composer_fill_refuses_an_oversize_image(monkeypatch):
    from nicegui import ui
    toasts = []
    monkeypatch.setattr(x_post.kit, "toast", lambda kind, text: toasts.append((kind, text)))
    monkeypatch.setattr(bus_client, "read", lambda view: None)
    monkeypatch.setattr(bus_client, "read_version", lambda view: 1)
    with ui.card() as host:
        composer = x_post.build()
    composer.fill("t", "l", "", b"x" * (x_post.IMAGE_MAX_BYTES + 1), "big.png")
    assert toasts and toasts[0][0] == "warn" and "5 MB" in toasts[0][1]
    assert composer.body.value == "t"
    ok = b"x" * 10
    composer.fill("t2", "l", "", ok, "small.png")
    names = [e.text for e in _all(host, ui.label)]
    assert "small.png" in names


def test_copy_caption_writes_the_caption_in_the_browser(site, monkeypatch):
    host, _ = _render(monkeypatch)
    btn = _buttons(host, "Copy caption")[1]            # the blog row, second
    js = [li.js_handler for li in btn._event_listeners.values()
          if li.type == "click" and li.js_handler]
    assert js and json.dumps(BLOG["cards"][0]["caption"]) in js[0]
    assert "navigator.clipboard" in js[0]


def test_download_button_points_at_the_jpeg(site, monkeypatch):
    from nicegui import ui
    host, _ = _render(monkeypatch)
    calls = []
    monkeypatch.setattr(ui, "download", lambda *a, **k: calls.append(a))
    _click(_buttons(host, "Download")[1])
    assert calls == [("/social/card/blog:alpha/jpg?download=1",)]


def test_site_switch_notice_shows_while_blog_cards_are_listed(site, monkeypatch):
    from nicegui import ui
    host, _ = _render(monkeypatch, ideas={"site_enabled": False, "cards": []})
    labels = [e.text for e in _all(host, ui.label)]
    assert "No cards yet." not in labels
    assert ("Trade idea cards appear here only while trade ideas are published "
            "to the site (Settings → General).") in labels


def test_empty_state_explains_site_switch(site, monkeypatch):
    from nicegui import ui
    host, _ = _render(monkeypatch, blog={"cards": []},
                      ideas={"site_enabled": False, "cards": []})
    labels = [e.text for e in _all(host, ui.label)]
    assert "No cards yet." in labels
    assert ("Trade idea cards appear here only while trade ideas are published "
            "to the site (Settings → General).") in labels
    host2, _ = _render(monkeypatch, blog=None, ideas={"site_enabled": True, "cards": []})
    labels2 = [e.text for e in _all(host2, ui.label)]
    assert "No cards yet." in labels2
    assert not any("published to the site" in t for t in labels2)
    host3, _ = _render(monkeypatch, blog=None, ideas=None)    # nothing published at all
    assert not any("published to the site" in t
                   for t in [e.text for e in _all(host3, ui.label)])
