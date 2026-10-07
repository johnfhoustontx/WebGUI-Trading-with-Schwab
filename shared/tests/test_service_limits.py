"""How old a command may be before a service refuses it (audit AR-05)."""
import datetime as dt

import pytest

from shared import service_limits as cl


def _cmd(seconds=None, ts="auto"):
    class C:
        type = "x"
    c = C()
    if ts == "auto":
        c.ts = (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(seconds=seconds)).isoformat()
    else:
        c.ts = ts
    return c


def test_the_shipped_limits():
    assert cl.side_effect_max_sec() == 180
    assert cl.replay_max_sec() == 900
    assert cl.side_effect_max_sec() < cl.replay_max_sec()


@pytest.mark.parametrize("bad", [0, -5, True, "180", float("nan"), None, 1.5e12])
def test_an_unusable_limit_reads_as_the_shipped_one(monkeypatch, bad):
    monkeypatch.setattr(cl, "load", lambda: {"age": {"side_effect_max_sec": bad,
                                                     "replay_max_sec": bad}})
    assert cl.side_effect_max_sec() == 180
    assert cl.replay_max_sec() == 900


def test_age_is_seconds_since_the_enqueue_stamp():
    assert cl.age_seconds(_cmd(120)) == pytest.approx(120, abs=2)


@pytest.mark.parametrize("ts", [None, "", "not a date", 12345])
def test_no_readable_stamp_is_no_age(ts):
    assert cl.age_seconds(_cmd(ts=ts)) is None


def test_a_naive_stamp_is_read_as_utc():
    naive = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=60)
             ).replace(tzinfo=None).isoformat()
    assert cl.age_seconds(_cmd(ts=naive)) == pytest.approx(60, abs=2)


def test_older_than_judges_only_a_known_age():
    assert cl.older_than(_cmd(1000), 900) is True
    assert cl.older_than(_cmd(100), 900) is False
    assert cl.older_than(_cmd(ts=None), 900) is False


# ---- the size of each service's shared thread pool (audit PF-03) -------------

def test_the_shipped_pool_size():
    assert cl.pool_workers() == 16


@pytest.mark.parametrize("bad", [0, 1, -3, True, "16", 2.5, float("nan"), None, 5000])
def test_an_unusable_pool_size_reads_as_the_shipped_one(monkeypatch, bad):
    monkeypatch.setattr(cl, "load", lambda: {"pool": {"workers": bad}})
    assert cl.pool_workers() == 16


def test_a_usable_pool_size_is_used(monkeypatch):
    monkeypatch.setattr(cl, "load", lambda: {"pool": {"workers": 24}})
    assert cl.pool_workers() == 24


# ---- how much of one un-run command a dead letter keeps ----------------------
# A dead letter is for a person to read, and it stored the command whole. A blog
# command carries a document, so 200 dead letters were up to 200 documents.

def test_a_dead_letter_keeps_sixty_four_kb_of_a_field():
    assert cl.dead_letter_field_kb() == 64
    assert (cl.DEAD_FIELD_KB_MIN, cl.DEAD_FIELD_KB_MAX) == (1, 4096)


def test_the_shipped_dead_letter_table_says_what_the_defaults_say():
    import tomllib
    from repo_paths import SERVICES_TOML
    shipped = tomllib.loads(SERVICES_TOML.read_text(encoding="utf-8"))
    assert shipped["dead_letters"] == cl.DEFAULTS["dead_letters"]


@pytest.mark.parametrize("ok", [1, 8, 4096])
def test_a_usable_dead_letter_field_limit_is_used(monkeypatch, ok):
    monkeypatch.setattr(cl, "load", lambda: {"dead_letters": {"max_field_kb": ok}})
    assert cl.dead_letter_field_kb() == ok


@pytest.mark.parametrize("bad", [0, -1, 4097, True, False, "64", 64.0, 2.5,
                                 float("nan"), float("inf"), None, [64]])
def test_an_unusable_dead_letter_field_limit_reads_as_the_shipped_one(
        monkeypatch, bad):
    monkeypatch.setattr(cl, "load", lambda: {"dead_letters": {"max_field_kb": bad}})
    assert cl.dead_letter_field_kb() == 64


@pytest.mark.parametrize("table", [None, 5, "x", [1], {}])
def test_a_dead_letter_table_that_is_not_one_reads_as_shipped(monkeypatch, table):
    monkeypatch.setattr(cl, "load", lambda: {"dead_letters": table})
    assert cl.dead_letter_field_kb() == 64
    assert cl.dead_letter_keep() == 200


# ---- how many entries each command stream keeps ------------------------------
# Every stream was trimmed to 1000. A blog command carries a whole document (up
# to config/blog.toml [limits] max_html_kb, shipped 512 KB), so at 1000 entries
# cmd:blog could hold about 500 MB of Redis memory.

EVERY_OTHER_STREAM = ("cmd:options", "cmd:sentiment", "cmd:portfolio", "cmd:trade",
                      "cmd:market", "cmd:news", "cmd:finder_public",
                      "cmd:rescue_public", "cmd:tools_public",
                      "cmd:tools_public_math", "cmd:gamma_public", "cmd:anything")


def test_every_existing_stream_keeps_the_thousand_it_always_kept():
    for stream in EVERY_OTHER_STREAM:
        assert cl.stream_keep(stream) == 1000, stream


def test_the_stream_that_carries_a_document_keeps_fifty():
    assert cl.stream_keep("cmd:blog") == 50


def test_the_document_stream_is_named_as_the_blog_names_it():
    """The cap is keyed by stream NAME, so a renamed stream would silently go
    back to 1000 whole documents. This is the join between the two modules.

    ONE stream, the one in use. ``blog_inbox.INBOX_STREAM`` (``cmd:blog_inbox``)
    belongs to the connector, which is parked: nothing writes it, so it has no
    cap of its own and no row under Settings. The test below is what says so."""
    from shared import blog_inbox
    small = {s for s, n in cl.DEFAULTS["stream_keep"].items() if s != "default"}
    assert small == {f"cmd:{blog_inbox.OWNER_DOMAIN}"}


def test_the_parked_inbox_stream_has_no_cap_because_nothing_writes_it():
    """⚠ A tripwire, not a description. ``cmd:blog_inbox`` would carry whole
    documents exactly as ``cmd:blog`` does, and today it is trimmed like any
    other stream (1000 entries: about 500 MB of documents). That is safe only
    while NOTHING writes it.

    The day a module starts using ``INBOX_STREAM`` this fails. Do not relax it:
    give the stream its cap back first - ``config/services.toml [stream_keep]``,
    ``DEFAULTS["stream_keep"]`` and ``STREAM_KEEP_CEILINGS`` here, and a row in
    ``webgui/config_schema.py`` - then update this test to say it has one."""
    import ast
    import pathlib
    from shared import blog_inbox
    assert blog_inbox.INBOX_STREAM == "cmd:blog_inbox"
    assert blog_inbox.INBOX_STREAM not in cl.DEFAULTS["stream_keep"]
    assert blog_inbox.INBOX_STREAM not in cl.STREAM_KEEP_CEILINGS
    assert cl.stream_keep(blog_inbox.INBOX_STREAM) == 1000
    assert cl.stream_keep_bounds(blog_inbox.INBOX_STREAM) == (10, 100000)

    def names_the_stream(tree) -> bool:
        """Whether CODE names the stream: the constant by name, or its value as
        a string of its own. A docstring or comment that mentions it does not."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "INBOX_STREAM":
                return True
            if isinstance(node, ast.Attribute) and node.attr == "INBOX_STREAM":
                return True
            if isinstance(node, ast.alias) and node.name == "INBOX_STREAM":
                return True
            if isinstance(node, ast.Constant) and node.value == blog_inbox.INBOX_STREAM:
                return True
        return False

    repo = pathlib.Path(__file__).resolve().parents[2]
    users, read = [], 0
    for top in ("services", "webgui", "shared", "deploy", "tools", "schwab-proxy"):
        for path in sorted((repo / top).rglob("*.py")):
            parts = path.relative_to(repo).parts
            if "tests" in parts or ".venv" in parts or "node_modules" in parts:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if "INBOX_STREAM" not in text and blog_inbox.INBOX_STREAM not in text:
                continue
            read += 1
            if names_the_stream(ast.parse(text)):
                users.append("/".join(parts))
    assert read >= 1, "nothing was read: the scan is looking in the wrong place"
    assert users == ["shared/blog_inbox.py"], (
        "code outside shared/blog_inbox.py names the inbox stream; if it "
        f"writes it, the stream needs its cap back first: {users}")


def test_the_shipped_file_says_what_the_defaults_say():
    """The TOML only overrides; shipped equal, so a missing file - or a missing
    table in it - changes nothing."""
    import tomllib
    from repo_paths import SERVICES_TOML
    shipped = tomllib.loads(SERVICES_TOML.read_text(encoding="utf-8"))
    assert shipped["stream_keep"] == cl.DEFAULTS["stream_keep"]


def test_a_configured_cap_is_used_and_the_rest_follow_the_default(monkeypatch):
    monkeypatch.setattr(cl, "load", lambda: {"stream_keep": {
        "default": 400, "cmd:options": 250, "cmd:blog": 10}})
    assert cl.stream_keep("cmd:options") == 250
    assert cl.stream_keep("cmd:blog") == 10
    assert cl.stream_keep("cmd:market") == 400
    # Named in neither the file nor the shipped table: the file's default.
    assert cl.stream_keep("cmd:never_heard_of_it") == 400


def test_the_bounds_are_ten_to_a_hundred_thousand(monkeypatch):
    """The floor is 10, not 1. The cap is not only a memory limit: a stream
    drops its OLDEST entries past it whether or not a service has read them, so
    a cap of 1 means a second click loses the first one still waiting."""
    assert (cl.STREAM_KEEP_MIN, cl.STREAM_KEEP_MAX) == (10, 100000)
    for ok in (10, 100000):
        monkeypatch.setattr(cl, "load", lambda v=ok: {"stream_keep": {
            "default": v, "cmd:options": v}})
        assert cl.stream_keep("cmd:market") == ok
        assert cl.stream_keep("cmd:options") == ok


def test_a_document_stream_has_a_ceiling_of_its_own(monkeypatch):
    """100000 commands of a few hundred bytes is tens of megabytes; 100000
    documents is not a number to offer. The stream whose entries carry a
    document stops at 500, and past it a value reads as its shipped 50."""
    assert cl.STREAM_KEEP_CEILINGS == {"cmd:blog": 500}
    # every stream that ships with a number of its own has a ceiling of its own
    named = {s for s in cl.DEFAULTS["stream_keep"] if s != "default"}
    assert set(cl.STREAM_KEEP_CEILINGS) == named
    for stream, ceiling in cl.STREAM_KEEP_CEILINGS.items():
        assert cl.STREAM_KEEP_MIN <= cl.DEFAULTS["stream_keep"][stream] <= ceiling
        for ok in (cl.STREAM_KEEP_MIN, ceiling):
            monkeypatch.setattr(cl, "load", lambda s=stream, v=ok: {
                "stream_keep": {"default": 1000, s: v}})
            assert cl.stream_keep(stream) == ok
        for over in (ceiling + 1, 1000, 100000):
            monkeypatch.setattr(cl, "load", lambda s=stream, v=over: {
                "stream_keep": {"default": 1000, s: v}})
            assert cl.stream_keep(stream) == 50, (stream, over)
    # the ceiling is theirs alone: any other stream may still go that high
    monkeypatch.setattr(cl, "load", lambda: {"stream_keep": {"cmd:options": 501}})
    assert cl.stream_keep("cmd:options") == 501


def test_the_bounds_of_a_stream_are_one_call(monkeypatch):
    """What the Settings catalogue mirrors, per stream."""
    assert cl.stream_keep_bounds("cmd:options") == (10, 100000)
    assert cl.stream_keep_bounds("default") == (10, 100000)
    assert cl.stream_keep_bounds("cmd:blog") == (10, 500)
    assert cl.stream_keep_bounds(None) == (10, 100000)


BAD_CAPS = [0, -1, 1, 9, 100001, True, False, "50", 50.0, 2.5, float("nan"),
            float("inf"), None, [50], {"n": 50}]


@pytest.mark.parametrize("bad", BAD_CAPS)
def test_an_unusable_default_reads_as_the_shipped_thousand(monkeypatch, bad):
    monkeypatch.setattr(cl, "load", lambda: {"stream_keep": {"default": bad}})
    assert cl.stream_keep("cmd:options") == 1000


@pytest.mark.parametrize("bad", BAD_CAPS)
def test_an_unusable_cap_on_a_document_stream_reads_as_its_shipped_fifty(
        monkeypatch, bad):
    """NOT as the default. A typo in the one line that keeps documents out of
    Redis must not be read as "keep a thousand of them"."""
    monkeypatch.setattr(cl, "load", lambda: {"stream_keep": {
        "default": 1000, "cmd:blog": bad}})
    assert cl.stream_keep("cmd:blog") == 50


@pytest.mark.parametrize("bad", BAD_CAPS)
def test_an_unusable_cap_on_any_other_stream_reads_as_the_default(monkeypatch, bad):
    monkeypatch.setattr(cl, "load", lambda: {"stream_keep": {
        "default": 300, "cmd:options": bad}})
    assert cl.stream_keep("cmd:options") == 300


@pytest.mark.parametrize("table", [None, 5, "x", [1], True])
def test_a_table_that_is_not_a_table_reads_as_the_shipped_caps(monkeypatch, table):
    monkeypatch.setattr(cl, "load", lambda: {"stream_keep": table})
    assert cl.stream_keep("cmd:options") == 1000
    assert cl.stream_keep("cmd:blog") == 50


def test_a_missing_table_reads_as_the_shipped_caps(monkeypatch):
    monkeypatch.setattr(cl, "load", lambda: {})
    assert cl.stream_keep("cmd:options") == 1000
    assert cl.stream_keep("cmd:blog") == 50


@pytest.mark.parametrize("stream", [None, 7, "", ["cmd:blog"], {"a": 1}, b"cmd:blog",
                                    "default"])
def test_a_stream_name_that_is_not_one_never_raises(stream):
    """``default`` is the table's own key, not a stream: asking for a stream
    called that gets the default like any other unknown name."""
    assert cl.stream_keep(stream) == 1000


def test_the_cap_is_read_through_the_real_loader_from_a_file(tmp_path, monkeypatch):
    from shared.config_toml import toml_loader
    path = tmp_path / "services.toml"
    path.write_text('[stream_keep]\ndefault = 700\n"cmd:blog" = 20\n'
                    '"cmd:market" = 5\n',
                    encoding="utf-8")
    load, _reset = toml_loader(path, cl.DEFAULTS, label="services.toml")
    monkeypatch.setattr(cl, "load", load)
    assert cl.stream_keep("cmd:options") == 700
    assert cl.stream_keep("cmd:blog") == 20
    assert cl.stream_keep("cmd:market") == 700       # 5 is under the floor
    # A cap on the document stream that cannot be read is its shipped 50, never
    # the file's default. (This was shown on cmd:blog_inbox, the parked
    # connector's stream, which no longer has a cap of its own.)
    bad = tmp_path / "services_bad.toml"
    bad.write_text('[stream_keep]\ndefault = 700\n"cmd:blog" = "many"\n', encoding="utf-8")
    load_bad, _reset = toml_loader(bad, cl.DEFAULTS, label="services.toml")
    monkeypatch.setattr(cl, "load", load_bad)
    assert cl.stream_keep("cmd:blog") == 50
    assert cl.stream_keep("cmd:options") == 700
