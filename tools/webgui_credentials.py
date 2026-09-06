"""Set the webgui's password, enrol an authenticator, and revoke every device.

Run this ONCE, over SSH, on the box that serves the app::

    .venv/bin/python tools/webgui_credentials.py set-password
    .venv/bin/python tools/webgui_credentials.py enroll-totp
    .venv/bin/python tools/webgui_credentials.py show
    .venv/bin/python tools/webgui_credentials.py revoke-devices

It writes ``shared/webgui_auth.json`` -- the file ``webgui/auth_store.py`` reads
and ``webgui/auth_middleware.py`` gates every route against.

THERE ARE NO RECOVERY CODES IN THIS DESIGN. If the enrolled secret and the
authenticator on the phone ever disagree, the only way back into the app is
another SSH session and another run of this tool. Two consequences shape
everything below:

* ``enroll-totp`` VERIFIES A CODE BEFORE IT SAVES ANYTHING. A tool that wrote
  first and asked afterwards could replace a working authenticator with one that
  was never successfully scanned, between one command and the next, with no way
  back through the UI.
* The ``otpauth://`` URI and the grouped base32 secret are printed in full,
  every time, BEFORE the confirmation prompt. That URI is effectively the only
  recovery code that exists -- put it in a password manager. The QR square is a
  convenience on top; it is never the only path (see :func:`qr_lines`).

The password is read with ``getpass`` and there is deliberately NO flag that
accepts one. An argv password lands in the shell history and stays readable in
``pgrep -af`` for the life of the process; this repo has a documented incident
where a live secret was readable there on this very box.
"""
import argparse
import dataclasses
import datetime
import getpass
import io
import pathlib
import secrets
import sys

import pyotp

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
# APPENDED, not inserted: ``webgui/`` contains a ``tools/`` directory of its own,
# and putting it ahead of the repo root would shadow this package for anything
# that later does a fresh ``import tools``. Nothing else on the path defines
# ``auth`` or ``auth_store``, so the tail of the path resolves them fine.
sys.path.append(str(_REPO_ROOT / "webgui"))

import auth              # noqa: E402  (webgui/auth.py)
import auth_store        # noqa: E402  (webgui/auth_store.py)

# What the authenticator app shows in its list. Stable on purpose: an authenticator
# keys its entry on these, so changing them makes an existing enrolment look like a
# stranger's. The account name is the host the owner types into a browser.
ISSUER = "NeuralStrike"
ACCOUNT = "app.neuralstrike.co"

# 32 URL-safe characters of entropy, signing every session and remember-device
# token. Not derived from the password: rotating one must not silently rotate
# the other, and ``revoke-devices`` is this design's single revocation lever.
SESSION_SECRET_BYTES = 32


# ---------------------------------------------------------------------------
# Pure helpers.

def resolve_path(path=None):
    """The credentials file this run acts on.

    ``None`` looks up ``auth_store.DEFAULT_PATH`` **now**, never a value captured
    at import: that default-argument trap is what let a test fixture write into
    a live database for six weeks (see the repo-root conftest).
    """
    return pathlib.Path(path) if path is not None else auth_store.DEFAULT_PATH


def new_session_secret():
    return secrets.token_urlsafe(SESSION_SECRET_BYTES)


def format_secret(secret):
    """Base32 in groups of four, for typing off a phone into a laptop."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def format_counter(counter):
    """The moment a TOTP time step covers, for ``show``.

    ``last_totp_counter`` is a step index -- 59623991 -- which tells a reader
    nothing. UTC rather than local time because this is read over SSH on a box
    whose timezone is not necessarily the owner's, and a bare unlabelled time
    would be worse than the counter it replaced.
    """
    try:
        step = int(counter)
        if step <= 0:       # 0 is the never-used default; "1970" is not that.
            return "none yet"
        return datetime.datetime.fromtimestamp(
            step * auth.TOTP_PERIOD_SEC,
            datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OverflowError, OSError, TypeError):
        return "none yet"


def provisioning_uri(secret):
    return pyotp.TOTP(secret, interval=auth.TOTP_PERIOD_SEC).provisioning_uri(
        name=ACCOUNT, issuer_name=ISSUER)


def qr_lines(uri, *, encoding=None):
    """The QR square as terminal lines, or ``None`` when it cannot be drawn.

    Mirrors ``webgui/voice.py``'s ``edge_tts`` precedent: the import is lazy, so
    the tool stays usable on a box where ``qrcode`` never got installed, and
    every failure degrades to ``None`` rather than raising. Losing the square
    costs the owner a scan; raising here would cost them the enrolment.

    ``invert=True`` because these terminals are dark. An un-inverted square
    renders its quiet zone in the terminal's background colour, and a phone
    camera will not read it.

    THE ENCODING CHECK IS NOT DEFENSIVE PADDING -- it was found live.
    ``print_ascii`` emits half-block characters, and printing those to a stdout
    encoded cp1252, or ANSI_X3.4-1968 under ``LANG=C`` over SSH, raises
    ``UnicodeEncodeError``. That happens AFTER the "scan this" banner and BEFORE
    the URI, so the owner would be left with a traceback and no recovery code at
    all -- the one outcome this whole module is arranged to prevent. Checking
    against the stream's OWN codec is exact, because it is the codec ``print``
    is about to use. An unknown or unstated encoding is treated as "cannot", on
    the grounds that a garbled square looks scannable and is not.
    """
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(uri)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
    except Exception:       # noqa: BLE001 - the caller prints why there is no square.
        return None
    text = buf.getvalue().rstrip("\n")
    if not text:
        return None
    enc = encoding or getattr(sys.stdout, "encoding", None)
    try:
        text.encode(enc)
    except (UnicodeEncodeError, LookupError, TypeError):
        return None
    return text.split("\n")


def _blank():
    """The shape a brand-new credentials file starts from.

    Empty strings, NOT plausible defaults. ``verify_password`` fails closed on an
    unparseable hash and ``verify_totp`` refuses a secret shorter than
    ``MIN_TOTP_SECRET_LEN`` rather than treating it as "no second factor", so a
    half-configured store admits nobody -- which is what makes it safe for the
    two subcommands to be run in either order.
    """
    return auth_store.Credentials(password_hash="", totp_secret="",
                                  session_secret=new_session_secret())


def _repaired(creds):
    """Mint a session key if the store has none.

    An empty ``session_secret`` is not a configured app with a quirk -- the gate
    default-denies on a falsy key and the login form cannot mint a token either,
    so every route refuses. Without this, the tool that exists to fix a locked
    out owner could not fix it, and the remaining route in would be editing JSON
    by hand. Minting invalidates nothing, because an empty key never signed
    anything: this is a repair, not the rotation ``revoke-devices`` performs.
    """
    if creds.session_secret:
        return creds
    return dataclasses.replace(creds, session_secret=new_session_secret())


def _load(path):
    """``(creds_or_blank, existed)``, or ``None`` when the file is unreadable.

    A corrupt file must NEVER be silently replaced: overwriting it would take the
    TOTP secret and the session key down with whatever was actually wrong, and
    the fix would be indistinguishable from the fault.
    """
    try:
        creds = auth_store.load(path)
    except auth_store.CredentialsError as exc:
        print("%s cannot be read, so nothing was changed: %s" % (path, exc), file=sys.stderr)
        print("Inspect or move that file by hand, then run this again.", file=sys.stderr)
        return None
    return (creds, True) if creds is not None else (_blank(), False)


def _ask(prompt, *, hidden):
    """One prompt, or ``None`` if the owner backed out.

    Ctrl-C and Ctrl-D at a prompt are the SAFE outcome -- nothing has been
    written yet -- so they end the run quietly instead of tracebacking.
    """
    try:
        return getpass.getpass(prompt) if hidden else input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written.", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Subcommands.

def set_password(path):
    loaded = _load(path)
    if loaded is None:
        return 1
    creds, existed = loaded

    first = _ask("New webgui password: ", hidden=True)
    if first is None:
        return 1
    if not first:
        print("An empty password would admit anyone who finds the hostname. "
              "Nothing was written.", file=sys.stderr)
        return 1
    again = _ask("Repeat it: ", hidden=True)
    if again is None:
        return 1
    if first != again:
        print("The two passwords did not match. Nothing was written.", file=sys.stderr)
        return 1

    # Everything but the hash is preserved, deliberately. Changing the password
    # is not "sign out everywhere" -- ``revoke-devices`` is, and it is the only
    # thing in this design that is. Rewinding last_totp_counter here would also
    # re-open the TOTP replay window for one 30 s step.
    auth_store.save(dataclasses.replace(_repaired(creds),
                                        password_hash=auth.hash_password(first)), path)
    print("Password set in %s." % path)
    if len(creds.totp_secret) < auth.MIN_TOTP_SECRET_LEN:
        print("No authenticator is enrolled yet, so sign-in is still REFUSED. "
              "Run:  enroll-totp")
    elif existed:
        print("Sessions already signed in stay signed in. To end them all, run:  "
              "revoke-devices")
    return 0


def enroll_totp(path):
    loaded = _load(path)
    if loaded is None:
        return 1
    creds, existed = loaded

    secret = pyotp.random_base32()
    uri = provisioning_uri(secret)

    print()
    if len(creds.totp_secret) >= auth.MIN_TOTP_SECRET_LEN:
        print("An authenticator is already enrolled. Confirming a code below "
              "REPLACES it; the current one keeps working until then.")
        print()
    print("Scan this with your authenticator app:")
    print()
    square = qr_lines(uri)
    if square:
        for line in square:
            print(line)
    else:
        print("  (no QR square here: either the `qrcode` package is missing, or "
              "this terminal")
        print("   cannot render block characters. Use the URI or the secret below "
              "instead.)")
    print()
    print("  URI:    %s" % uri)
    print("  Secret: %s" % format_secret(secret))
    print()
    print("SAVE THE URI SOMEWHERE SAFE NOW -- a password manager. There are no")
    print("recovery codes: without it, a lost phone means another SSH session.")
    print()

    code = _ask("Enter the 6-digit code the app is showing, to confirm: ", hidden=False)
    if code is None:
        return 1
    # The SAME call the login route makes, floor included -- so a code this
    # accepts is one that will sign in, rather than one that merely looks right.
    ok, counter = auth.verify_totp(secret, code, last_counter=creds.last_totp_counter)
    if not ok:
        print("That code was not accepted, so NOTHING was saved.", file=sys.stderr)
        if existed and creds.totp_secret:
            print("The authenticator you are already using is untouched.", file=sys.stderr)
        # Three real causes, and the owner cannot tell them apart from here: a
        # mistyped code, a phone clock that has drifted, or -- because the same
        # replay floor the login route uses applies here -- a code from the 30 s
        # window a sign-in already spent.
        print("Check the phone's clock, wait for the app to show the NEXT code, "
              "then run enroll-totp again -- it issues a fresh secret to scan.",
              file=sys.stderr)
        return 1

    # ``counter`` is the time step that code occupied. Persisting it spends the
    # confirmation code, so it cannot be replayed into the login form inside its
    # own 30-second window.
    auth_store.save(dataclasses.replace(_repaired(creds), totp_secret=secret,
                                        last_totp_counter=counter), path)
    print()
    print("Enrolled in %s." % path)
    if not creds.password_hash:
        print("No password is set yet, so sign-in is still REFUSED. Run:  set-password")
    else:
        print("Sign in with your password and a code from this app.")
    return 0


def revoke_devices(path):
    loaded = _load(path)
    if loaded is None:
        return 1
    creds, existed = loaded
    if not existed:
        print("There is no credentials file at %s, so there is nothing to "
              "revoke." % path, file=sys.stderr)
        return 1

    auth_store.save(dataclasses.replace(creds, epoch=creds.epoch + 1), path)
    print("Epoch %d -> %d. Every session and remembered device is now signed "
          "out, on every machine." % (creds.epoch, creds.epoch + 1))
    print("Your password and authenticator are unchanged -- sign in again as usual.")
    return 0


def show(path):
    """Report the state. Never prints the hash or either secret.

    Exit 1 when the app cannot actually admit anyone, so a scripted check can
    ask "is this box configured?" and read the answer from the status.
    """
    loaded = _load(path)
    if loaded is None:
        return 1
    creds, existed = loaded

    print("credentials file    %s" % path)
    if not existed:
        print("status              NOT CONFIGURED -- every sign-in is refused")
        print("                    Run:  set-password   then:  enroll-totp")
        return 1

    has_password = bool(creds.password_hash)
    has_totp = len(creds.totp_secret) >= auth.MIN_TOTP_SECRET_LEN
    has_key = bool(creds.session_secret)
    print("password set        %s" % ("yes" if has_password else "NO"))
    print("authenticator       %s" % ("enrolled" if has_totp else "NOT ENROLLED"))
    print("session key         %s" % ("present" if has_key else "MISSING"))
    print("revocation epoch    %d" % creds.epoch)
    print("last code accepted   %s" % format_counter(creds.last_totp_counter))

    if has_password and has_totp and has_key:
        return 0
    print()
    print("Sign-in is REFUSED until all three are in place.")
    if not has_password or not has_key:
        # set-password mints a session key on the way past, so it is the answer
        # to both -- naming a command the owner cannot run would be worse.
        print("  Run:  set-password")
    if not has_totp:
        print("  Run:  enroll-totp")
    return 1


# ---------------------------------------------------------------------------
# CLI.

_COMMANDS = {
    "set-password": (set_password, "set the password, read from a hidden prompt"),
    "enroll-totp": (enroll_totp, "enrol an authenticator app (confirms a code before saving)"),
    "revoke-devices": (revoke_devices, "sign out every session and remembered device"),
    "show": (show, "report what is configured, without printing any secret"),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="webgui_credentials",
        description=__doc__.split("\n\n")[0],
        epilog="No subcommand takes an argument. The password is never read "
               "from argv -- it would be in your shell history and in `pgrep -af`.")
    subs = parser.add_subparsers(dest="command", required=True)
    for name, (_fn, help_text) in _COMMANDS.items():
        subs.add_parser(name, help=help_text)
    return parser


def main(argv=None, *, path=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    return _COMMANDS[args.command][0](resolve_path(path))


if __name__ == "__main__":
    raise SystemExit(main())
