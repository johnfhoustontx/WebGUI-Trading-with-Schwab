"""The webgui's credentials file: one dataclass, load, save.

Deliberately NOT built on ``shared.config_toml.toml_loader``. That factory's
contract is "never raises -- fall back to built-in defaults", which is right for
a config file and catastrophic for a credentials file: the default would be
"no password", so a corrupt file would silently open the app to the internet.
This module raises instead.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import tempfile

DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[1] / "shared" / "webgui_auth.json"


class CredentialsError(RuntimeError):
    """The credentials file exists but cannot be understood."""


@dataclasses.dataclass(frozen=True)
class Credentials:
    password_hash: str
    totp_secret: str
    session_secret: str
    epoch: int = 1
    last_totp_counter: int = 0


def load(path: pathlib.Path | None = None) -> Credentials | None:
    """Return the stored credentials, or ``None`` when none have been set yet."""
    p = pathlib.Path(path) if path is not None else DEFAULT_PATH
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return Credentials(
            password_hash=str(raw["password_hash"]),
            totp_secret=str(raw["totp_secret"]),
            session_secret=str(raw["session_secret"]),
            epoch=int(raw.get("epoch", 1)),
            last_totp_counter=int(raw.get("last_totp_counter", 0)),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise CredentialsError(f"{p} is not a readable credentials file: {exc}") from exc


def save(creds: Credentials, path: pathlib.Path | None = None) -> None:
    """Write atomically, owner-read-write only."""
    p = pathlib.Path(path) if path is not None else DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".webgui_auth-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(creds), fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise
