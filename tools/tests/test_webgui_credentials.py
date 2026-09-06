"""``tools/webgui_credentials.py`` -- the once-over-SSH credentials CLI.

Weighted towards the ways this tool could LOCK THE OWNER OUT rather than towards
coverage, because there are no recovery codes in this design: if the enrolment
that gets written does not match the authenticator that got scanned, the only
way back is another SSH session and another run of this tool.

Two invariants get their own tests because nothing else in the repo checks them:

1. **No test may write the real ``shared/webgui_auth.json``.** Every call passes
   an explicit ``path=``, and ``_never_the_live_store`` below is the belt-and-
   braces: it repoints ``auth_store.DEFAULT_PATH`` at a tmp file, so even a
   forgotten ``path=`` cannot reach the live credentials of the checkout the
   suite happens to be running in. The repo-root conftest guards ``sqlite3``;
   there is no equivalent for this file.
2. **The password can never be an argv flag.** It would land in shell history
   and in ``pgrep -af`` output -- this repo has a documented incident where a
   secret was readable there on this very box.

``tools/`` has no ``__init__.py``; the sys.path insert below mirrors
tools/tests/test_snapshot_from_prod.py.
"""
import io
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import webgui_credentials as cli  # noqa: E402

auth = cli.auth
auth_store = cli.auth_store


# ---------------------------------------------------------------------------
# Fixtures.

@pytest.fixture(autouse=True)
def _never_the_live_store(tmp_path, monkeypatch):
    """No test may reach the checkout's real credentials file.

    Every test below passes ``path=`` explicitly -- this only catches the one
    that forgets. Redirecting the module attribute works because ``auth_store``
    resolves ``DEFAULT_PATH`` at CALL time (``path=None`` -> look it up), which
    is the shape CLAUDE.md recommends and the opposite of the default-argument
    trap that once had a fixture writing into the live database for six weeks.
    """
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", tmp_path / "unused_auth.json")
    yield
    assert not (tmp_path / "unused_auth.json").exists(), \
        "a test fell through to the default credentials path"


@pytest.fixture
def store(tmp_path):
    return tmp_path / "webgui_auth.json"


def _passwords(monkeypatch, *answers):
    """Monkeypatch getpass so a test can never block on a real prompt."""
    seen = iter(answers)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(seen))


def _codes(monkeypatch, *answers):
    seen = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(seen))


def _seed(path, **over):
    """A complete, working credentials file."""
    creds = auth_store.Credentials(
        password_hash=auth.hash_password("old-password"),
        totp_secret="JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
        session_secret="seeded-session-secret",
        epoch=4,
        last_totp_counter=0,
    )
    auth_store.save(cli.dataclasses.replace(creds, **over), path)
    return auth_store.load(path)


# ---------------------------------------------------------------------------
# The module itself.

def test_the_module_name_does_not_shadow_a_stdlib_module():
    """A service module called ``secrets.py`` once shadowed the stdlib and
    crashed a service on launch while the suite stayed green -- because pytest
    runs from a different sys.path than the script does."""
    assert "webgui_credentials" not in sys.stdlib_module_names


def test_the_default_path_is_resolved_at_call_time():
    """``None`` must mean "look up ``auth_store.DEFAULT_PATH`` NOW", not the
    value it happened to hold at import. The fixture above repoints that
    attribute, so identity here proves the lookup is live."""
    assert cli.resolve_path(None) == auth_store.DEFAULT_PATH
    assert cli.resolve_path(pathlib.Path("/tmp/x.json")) == pathlib.Path("/tmp/x.json")


# ---------------------------------------------------------------------------
# The password can never come from argv.

def test_set_password_takes_no_arguments_at_all():
    """Not "it is optional" -- there is no such flag to pass. A password on the
    command line is in the shell history and in ``pgrep -af`` for the life of
    the process."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["set-password", "hunter2"])
    assert exc.value.code != 0

    with pytest.raises(SystemExit):
        parser.parse_args(["set-password", "--password", "hunter2"])


def test_no_subcommand_declares_a_secret_bearing_option():
    """Structural, so a future flag cannot re-open this by accident."""
    for name, sub in cli.build_parser()._subparsers._group_actions[0].choices.items():
        flags = [o for a in sub._actions for o in a.option_strings if o != "-h" and o != "--help"]
        assert flags == [], f"{name} grew the option(s) {flags}"
        assert [a for a in sub._actions if not a.option_strings] == [], \
            f"{name} grew a positional argument"


# ---------------------------------------------------------------------------
# set-password.

def test_set_password_on_a_fresh_box_writes_a_usable_half(store, monkeypatch, capsys):
    """A fresh box has to start somewhere, and a store with a password but no
    TOTP secret is SAFE: ``verify_totp`` refuses an empty secret rather than
    treating it as "no second factor". The session secret is minted here so the
    gate has a key at all -- but the tool must SAY that sign-in is still
    refused, or the owner will try to log in and read the failure as a bug."""
    _passwords(monkeypatch, "correct horse", "correct horse")
    assert cli.main(["set-password"], path=store) == 0

    creds = auth_store.load(store)
    assert auth.verify_password(creds.password_hash, "correct horse")
    assert creds.totp_secret == ""
    assert len(creds.session_secret) >= 32
    assert creds.epoch == 1

    out = capsys.readouterr().out
    assert "enroll-totp" in out


def test_set_password_keeps_every_other_field(store, monkeypatch):
    """Changing the password must not silently sign every device out, mint a new
    session key, or rewind the TOTP replay counter."""
    before = _seed(store, last_totp_counter=987654)
    _passwords(monkeypatch, "new-password", "new-password")
    assert cli.main(["set-password"], path=store) == 0

    after = auth_store.load(store)
    assert auth.verify_password(after.password_hash, "new-password")
    assert not auth.verify_password(after.password_hash, "old-password")
    assert (after.totp_secret, after.session_secret, after.epoch, after.last_totp_counter) == \
        (before.totp_secret, before.session_secret, before.epoch, before.last_totp_counter)


def test_a_mistyped_repeat_writes_nothing(store, monkeypatch, capsys):
    _passwords(monkeypatch, "one thing", "another thing")
    assert cli.main(["set-password"], path=store) == 1
    assert not store.exists()
    assert "did not match" in capsys.readouterr().err


def test_an_empty_password_is_refused(store, monkeypatch, capsys):
    """Not a strength meter -- an empty password admits anyone who finds the
    hostname, and the hostname reaches Certificate Transparency within the hour."""
    _passwords(monkeypatch, "", "")
    assert cli.main(["set-password"], path=store) == 1
    assert not store.exists()
    assert "empty" in capsys.readouterr().err.lower()


def test_set_password_refuses_a_corrupt_store_rather_than_overwriting_it(store, monkeypatch, capsys):
    """Overwriting would destroy the TOTP secret and the session key along with
    whatever was wrong -- a lockout produced by the recovery tool."""
    store.write_text("{ this is not json", encoding="utf-8")
    _passwords(monkeypatch, "x", "x")
    assert cli.main(["set-password"], path=store) == 1
    assert store.read_text(encoding="utf-8") == "{ this is not json"
    assert "cannot be read" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# enroll-totp -- the subcommand that can lock the owner out permanently.

def test_enroll_saves_only_after_the_code_is_confirmed(store, monkeypatch, capsys):
    _seed(store)
    monkeypatch.setattr(cli.pyotp, "random_base32", lambda: "NEWSECRETNEWSECRETNEWSECRETNEWSE")
    _codes(monkeypatch, cli.pyotp.TOTP("NEWSECRETNEWSECRETNEWSECRETNEWSE").now())

    assert cli.main(["enroll-totp"], path=store) == 0
    after = auth_store.load(store)
    assert after.totp_secret == "NEWSECRETNEWSECRETNEWSECRETNEWSE"
    # The confirmation code was SPENT: persisting the accepted counter is what
    # stops it being replayed into the login form inside its own 30 s window.
    assert after.last_totp_counter > 0
    assert auth.verify_totp(after.totp_secret, cli.pyotp.TOTP(after.totp_secret).now(),
                            last_counter=after.last_totp_counter)[0] is False


def test_a_wrong_confirmation_code_leaves_the_old_secret_in_place(store, monkeypatch, capsys):
    """THE test of this task. A tool that saved first and asked later would
    replace a working authenticator with one that was never scanned."""
    before = _seed(store)
    monkeypatch.setattr(cli.pyotp, "random_base32", lambda: "NEWSECRETNEWSECRETNEWSECRETNEWSE")
    _codes(monkeypatch, "000000")

    assert cli.main(["enroll-totp"], path=store) == 1
    assert auth_store.load(store) == before
    err = capsys.readouterr().err
    assert "not" in err.lower() and "saved" in err.lower()


def test_a_wrong_code_on_a_fresh_box_creates_no_file(store, monkeypatch):
    _codes(monkeypatch, "000000")
    assert cli.main(["enroll-totp"], path=store) == 1
    assert not store.exists()


def test_enroll_on_a_fresh_box_works_and_warns_that_the_password_is_missing(store, monkeypatch, capsys):
    """The owner may reasonably run these two subcommands in either order."""
    monkeypatch.setattr(cli.pyotp, "random_base32", lambda: "FRESHSECRETFRESHSECRETFRESHSECRE")
    _codes(monkeypatch, cli.pyotp.TOTP("FRESHSECRETFRESHSECRETFRESHSECRE").now())

    assert cli.main(["enroll-totp"], path=store) == 0
    creds = auth_store.load(store)
    assert creds.totp_secret == "FRESHSECRETFRESHSECRETFRESHSECRE"
    assert creds.password_hash == ""
    assert len(creds.session_secret) >= 32
    assert "set-password" in capsys.readouterr().out


def test_enroll_prints_the_secret_in_a_form_that_can_be_saved(store, monkeypatch, capsys):
    """That URI is effectively the only recovery code this design has. It must
    be on screen in BOTH forms -- scannable and typable -- before the tool asks
    for a code, or a terminal without a working QR renderer is a dead end."""
    monkeypatch.setattr(cli.pyotp, "random_base32", lambda: "NEWSECRETNEWSECRETNEWSECRETNEWSE")
    _codes(monkeypatch, cli.pyotp.TOTP("NEWSECRETNEWSECRETNEWSECRETNEWSE").now())
    cli.main(["enroll-totp"], path=store)

    out = capsys.readouterr().out
    assert "otpauth://totp/" in out
    assert "secret=NEWSECRETNEWSECRETNEWSECRETNEWSE" in out.replace("&", "&").lower() or \
        "NEWSECRETNEWSECRETNEWSECRETNEWSE" in out
    # Grouped for typing by hand off a phone screen.
    assert "NEWS ECRE TNEW SECR ETNE WSEC RETN EWSE" in out


def test_enroll_refuses_a_corrupt_store(store, monkeypatch, capsys):
    store.write_text("{ nope", encoding="utf-8")
    _codes(monkeypatch, "000000")
    assert cli.main(["enroll-totp"], path=store) == 1
    assert store.read_text(encoding="utf-8") == "{ nope"
    assert "cannot be read" in capsys.readouterr().err


def test_a_non_numeric_confirmation_is_a_refusal_not_a_traceback(store, monkeypatch):
    _seed(store)
    _codes(monkeypatch, "not a code")
    assert cli.main(["enroll-totp"], path=store) == 1


def test_an_abandoned_enrolment_writes_nothing(store, monkeypatch, capsys):
    """Ctrl-D at the confirmation prompt. The owner backing out is the SAFE
    outcome and must not traceback -- nor half-write."""
    before = _seed(store)

    def _eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert cli.main(["enroll-totp"], path=store) == 1
    assert auth_store.load(store) == before


# ---------------------------------------------------------------------------
# The QR code is a convenience, never the only path.

def test_the_qr_is_skipped_cleanly_when_the_package_is_absent(store, monkeypatch, capsys):
    """Mirrors webgui/voice.py's edge_tts precedent: lazy import, degrade to
    None, and say so. ``sys.modules[x] = None`` makes ``import x`` raise."""
    monkeypatch.setitem(sys.modules, "qrcode", None)
    assert cli.qr_lines("otpauth://totp/x") is None

    monkeypatch.setattr(cli.pyotp, "random_base32", lambda: "NEWSECRETNEWSECRETNEWSECRETNEWSE")
    _codes(monkeypatch, cli.pyotp.TOTP("NEWSECRETNEWSECRETNEWSECRETNEWSE").now())
    assert cli.main(["enroll-totp"], path=store) == 0

    out = capsys.readouterr().out
    assert "otpauth://totp/" in out          # still enrollable by hand
    assert "qrcode" in out                   # and it says why there is no square


def test_the_qr_is_rendered_when_the_package_is_there(monkeypatch):
    """Injected rather than assumed installed, so this is deterministic on a box
    that does not have it -- including prod, if the lock entry ever goes missing."""
    class _FakeQR:
        def __init__(self, *a, **kw): self.data = None

        def add_data(self, data): self.data = data

        def print_ascii(self, out=None, invert=False):
            assert invert, "a dark terminal renders an un-inverted QR unscannable"
            out.write("##%s##\n##\n" % self.data)

    monkeypatch.setitem(sys.modules, "qrcode", type(sys)("qrcode"))
    sys.modules["qrcode"].QRCode = _FakeQR
    assert cli.qr_lines("otpauth://totp/x") == ["##otpauth://totp/x##", "##"]


def test_a_broken_qr_renderer_does_not_take_the_enrolment_down(monkeypatch):
    """The square is decoration; the URI is the payload."""
    mod = type(sys)("qrcode")

    class _Boom:
        def __init__(self, *a, **kw): raise RuntimeError("no")

    mod.QRCode = _Boom
    monkeypatch.setitem(sys.modules, "qrcode", mod)
    assert cli.qr_lines("otpauth://totp/x") is None


# ---------------------------------------------------------------------------
# revoke-devices.

def test_revoke_bumps_the_epoch_and_touches_nothing_else(store, capsys):
    before = _seed(store)
    assert cli.main(["revoke-devices"], path=store) == 0

    after = auth_store.load(store)
    assert after.epoch == before.epoch + 1
    assert (after.password_hash, after.totp_secret, after.session_secret) == \
        (before.password_hash, before.totp_secret, before.session_secret)
    assert "4" in capsys.readouterr().out       # names the epoch it moved from


def test_revoke_on_a_box_with_no_credentials_says_so(store, capsys):
    assert cli.main(["revoke-devices"], path=store) == 1
    assert not store.exists()
    assert str(store) in capsys.readouterr().err


def test_revoke_refuses_a_corrupt_store(store, capsys):
    store.write_text("{", encoding="utf-8")
    assert cli.main(["revoke-devices"], path=store) == 1
    assert "cannot be read" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# show.

def test_show_never_prints_secret_material(store, capsys):
    creds = _seed(store)
    assert cli.main(["show"], path=store) == 0

    printed = capsys.readouterr().out
    for secret in (creds.password_hash, creds.totp_secret, creds.session_secret):
        assert secret not in printed
    # Not even a fragment: a hash prefix names the Argon2 parameters, and a
    # secret prefix is a real head start on one 32-character string.
    assert creds.password_hash[:12] not in printed
    assert creds.totp_secret[:8] not in printed
    assert creds.session_secret[:8] not in printed


def test_show_answers_the_questions_the_owner_actually_has(store, capsys):
    _seed(store, epoch=7)
    assert cli.main(["show"], path=store) == 0
    out = capsys.readouterr().out
    assert str(store) in out
    assert "7" in out
    assert "yes" in out.lower()


def test_show_names_the_half_configured_case(store, capsys):
    """A password with no authenticator refuses every sign-in. Reporting that as
    a bare "TOTP: no" invites the owner to read it as optional."""
    _seed(store, totp_secret="")
    assert cli.main(["show"], path=store) == 1
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "enroll-totp" in out


def test_show_on_an_unconfigured_box_exits_non_zero(store, capsys):
    assert cli.main(["show"], path=store) == 1
    assert str(store) in capsys.readouterr().out


def test_show_on_a_corrupt_file_reports_the_file_not_a_traceback(store, capsys):
    store.write_text('{"password_hash": 1', encoding="utf-8")
    assert cli.main(["show"], path=store) == 1
    err = capsys.readouterr().err
    assert "cannot be read" in err
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# The file the tool writes.

def test_the_written_file_is_json_the_app_can_load(store, monkeypatch):
    """Round-trip through the store the running app reads, not through a shape
    this test invented -- the difference is exactly what the repo's
    consumer-side-guard lesson is about."""
    _passwords(monkeypatch, "pw", "pw")
    assert cli.main(["set-password"], path=store) == 0
    monkeypatch.setattr(cli.pyotp, "random_base32", lambda: "ROUNDTRIPROUNDTRIPROUNDTRIPROUND")
    _codes(monkeypatch, cli.pyotp.TOTP("ROUNDTRIPROUNDTRIPROUNDTRIPROUND").now())
    assert cli.main(["enroll-totp"], path=store) == 0

    raw = json.loads(store.read_text(encoding="utf-8"))
    assert set(raw) == {"password_hash", "totp_secret", "session_secret",
                        "epoch", "last_totp_counter"}
    creds = auth_store.load(store)
    assert auth.verify_password(creds.password_hash, "pw")
    assert len(creds.session_secret) >= 32


def test_two_boxes_do_not_get_the_same_session_secret(tmp_path, monkeypatch):
    _passwords(monkeypatch, "pw", "pw", "pw", "pw")
    cli.main(["set-password"], path=tmp_path / "a.json")
    cli.main(["set-password"], path=tmp_path / "b.json")
    assert auth_store.load(tmp_path / "a.json").session_secret != \
        auth_store.load(tmp_path / "b.json").session_secret


def test_provisioning_uri_identifies_this_app(store):
    uri = cli.provisioning_uri("NEWSECRETNEWSECRETNEWSECRETNEWSE")
    assert uri.startswith("otpauth://totp/")
    assert cli.ISSUER.replace(" ", "%20") in uri or cli.ISSUER in uri
    assert "NEWSECRETNEWSECRETNEWSECRETNEWSE" in uri


def test_format_secret_groups_for_manual_entry():
    assert cli.format_secret("ABCDEFGH") == "ABCD EFGH"
    assert cli.format_secret("ABCDE") == "ABCD E"
    assert cli.format_secret("") == ""


def test_qr_lines_uses_a_string_buffer_not_the_terminal(monkeypatch):
    """Regression shape: ``print_ascii`` defaults to sys.stdout, so a builder
    that forgot ``out=`` would return nothing and print the square where the
    caller could not place it."""
    assert isinstance(io.StringIO(), io.StringIO)   # keeps the import honest
    mod = type(sys)("qrcode")

    class _QR:
        def __init__(self, *a, **kw): pass

        def add_data(self, data): pass

        def print_ascii(self, out=None, invert=False):
            assert out is not None and out is not sys.stdout
            out.write("x\n")

    mod.QRCode = _QR
    monkeypatch.setitem(sys.modules, "qrcode", mod)
    assert cli.qr_lines("u") == ["x"]


def test_a_terminal_that_cannot_render_blocks_gets_no_square_rather_than_a_crash(monkeypatch):
    """FOUND LIVE, and it is the exact failure this design cannot afford.

    ``qrcode.print_ascii`` emits half-block characters. Printing those to a
    stdout encoded cp1252 (Windows) or ANSI_X3.4-1968 (``LANG=C`` over SSH, a
    very ordinary minimal server) raises ``UnicodeEncodeError`` -- and it raises
    AFTER the "scan this" banner and BEFORE the URI, so the owner is left with a
    traceback and no recovery code at all.

    "The prod box is UTF-8" is not an answer: the whole point of the fallback is
    the terminal nobody predicted.
    """
    mod = type(sys)("qrcode")

    class _Blocks:
        def __init__(self, *a, **kw): pass

        def add_data(self, data): pass

        def print_ascii(self, out=None, invert=False):
            out.write("\u2588\u2580\u2584\n")

    mod.QRCode = _Blocks
    monkeypatch.setitem(sys.modules, "qrcode", mod)

    assert cli.qr_lines("otpauth://totp/x", encoding="utf-8") == ["\u2588\u2580\u2584"]
    assert cli.qr_lines("otpauth://totp/x", encoding="cp1252") is None
    assert cli.qr_lines("otpauth://totp/x", encoding="ascii") is None
    # A stream that will not say what it encodes is not a reason to gamble.
    assert cli.qr_lines("otpauth://totp/x", encoding="not-a-real-codec") is None


def test_show_reports_the_last_code_as_a_time_not_a_counter():
    """``last_totp_counter`` is a TOTP time step -- 59623991 means nothing to a
    reader. The repo's copy rule is that a label names what the number is FOR,
    so print the moment that code was accepted."""
    assert cli.format_counter(0) == "none yet"
    # Independently: time.gmtime(59623991 * 30) -> 2026-09-06 18:35:30 UTC.
    assert cli.format_counter(59623991) == "2026-09-06 18:35 UTC"
    # A counter that predates the epoch, or is otherwise nonsense, must not
    # traceback in a status report.
    assert cli.format_counter(-1) == "none yet"
    assert cli.format_counter(10 ** 18) == "none yet"


def test_a_missing_session_key_is_repaired_rather_than_preserved(store, monkeypatch):
    """A store with an empty ``session_secret`` is a DEAD app, not a configured
    one: the gate default-denies on a falsy key, so every route refuses and the
    login form cannot mint a token either. Preserving that faithfully would make
    this tool unable to fix the one thing it exists to fix -- and the owner's
    only other route in is editing JSON by hand.

    Minting one here invalidates nothing, because an empty key never signed
    anything. It is a repair, not the rotation that ``revoke-devices`` is.
    """
    _seed(store, session_secret="")
    _passwords(monkeypatch, "pw", "pw")
    assert cli.main(["set-password"], path=store) == 0
    assert len(auth_store.load(store).session_secret) >= 32

    _seed(store, session_secret="")
    monkeypatch.setattr(cli.pyotp, "random_base32", lambda: "REPAIRREPAIRREPAIRREPAIRREPAIRRE")
    _codes(monkeypatch, cli.pyotp.TOTP("REPAIRREPAIRREPAIRREPAIRREPAIRRE").now())
    assert cli.main(["enroll-totp"], path=store) == 0
    assert len(auth_store.load(store).session_secret) >= 32


def test_show_calls_a_missing_session_key_what_it_is(store, capsys):
    _seed(store, session_secret="")
    assert cli.main(["show"], path=store) == 1
    assert "set-password" in capsys.readouterr().out


def test_a_failed_first_enrolment_does_not_claim_an_authenticator_you_lack(store, monkeypatch, capsys):
    """"Your existing authenticator is untouched" is reassuring and, on a fresh
    box, false -- and this is the screen where the owner is deciding whether
    they have just locked themselves out."""
    _codes(monkeypatch, "000000")
    assert cli.main(["enroll-totp"], path=store) == 1
    err = capsys.readouterr().err
    assert "NOTHING was saved" in err
    assert "already using" not in err
