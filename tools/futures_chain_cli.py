"""The /ES and /NQ chain feed's two commands on vps2, run over ssh by the capture.

The capture on the PC reads the chains out of ThinkorSwim through Excel and has
no Redis of its own. Once per interval it does two things here:

    futures_chain_cli.py plan     prints what to capture, as JSON: the expiry, the
                                  series to try, the numbers to try them with.
                                  The server builds it, so a change of config or
                                  of session reaches the PC without editing a
                                  file there.
    futures_chain_cli.py ingest   reads {"snapshots": [...], "status": {...}} from
                                  stdin and puts one ``snapshot`` command per
                                  instrument, then one ``pc_status``, on
                                  ``cmd:futures_chain``. options_svc takes it from
                                  there (``futures_chain_feed``).

It validates the shape and the size and nothing else: the contract, the age, the
terms and the checks against the last capture are the service's. Input it cannot
read at all sends nothing. A snapshot over the limit is dropped and named on
stderr, and the others still go (one instrument failing does not block the
other); the exit status is 3 so the capture knows.

Exit codes: 0 written, 2 input or usage the command cannot use, 3 a snapshot over
``max_snapshot_kb`` was dropped (the rest were written) or stdin was larger than a
capture can be, 4 Redis refused a write. The reason is on stderr.

Run it through ``tools/futures_chain_remote.sh``, which loads the stack's ``.env``
(Redis needs a password) and the venv. Design:
docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, section 5.6.
"""
import json
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared import futures_chain, futures_chain_config  # noqa: E402
from shared.bus import Bus  # noqa: E402

USAGE = "usage: futures_chain_cli.py plan | ingest   (ingest reads its JSON from stdin)"

EXIT_OK, EXIT_BAD_INPUT, EXIT_TOO_BIG, EXIT_BUS = 0, 2, 3, 4

# Room beyond the snapshots themselves in what stdin may hold: the status, the
# wrapping, and whitespace.
_SLACK_BYTES = 64 * 1024

# A capture is a document holding snapshots holding contracts. Deeper input parses and
# then fails on its way to Redis, where it would read as Redis refusing a write.
_MAX_DEPTH = 8
_TOO_DEEP = "the input is nested too deeply to be a capture"


class _Refused(Exception):
    """Input the command will not send; ``code`` is the exit status."""

    def __init__(self, message: str, code: int = EXIT_BAD_INPUT):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    """The current instant. A function so tests can pin the clock."""
    return datetime.now(timezone.utc)


def _read_limit() -> int:
    """The most stdin can hold: every instrument's snapshot at the size limit, and
    the slack."""
    per_snapshot = futures_chain_config.capture()["max_snapshot_kb"] * 1024
    return per_snapshot * len(futures_chain_config.instruments()) + _SLACK_BYTES


def _read(stdin) -> str:
    """The input as text, without a byte order mark. PowerShell puts one on what
    it pipes, and ``json.loads`` refuses it."""
    limit = _read_limit()
    raw = stdin.read(limit + 1)
    if len(raw) > limit:
        raise _Refused(f"the input is too large: more than {limit // 1024} KB is "
                       "not a capture", EXIT_TOO_BIG)
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise _Refused(f"the input is not UTF-8 text: {exc}") from None
    return raw.lstrip("﻿")


def _too_deep(value) -> bool:
    """Whether ``value`` nests past ``_MAX_DEPTH``. A loop, so it cannot recurse."""
    level = [value]
    for _ in range(_MAX_DEPTH):
        level = [child for item in level if isinstance(item, (dict, list))
                 for child in (item.values() if isinstance(item, dict) else item)]
        if not level:
            return False
    return True


def _parse(text: str):
    """``(snapshots, status)`` from the input, or ``_Refused``."""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise _Refused(f"the input is not JSON: {exc}") from None
    except RecursionError:
        raise _Refused(_TOO_DEEP) from None
    if _too_deep(payload):
        raise _Refused(_TOO_DEEP)
    if not isinstance(payload, dict):
        raise _Refused('the input must be a JSON object: {"snapshots": [...], "status": {...}}')
    snapshots = payload.get("snapshots", [])
    status = payload.get("status")
    if not isinstance(snapshots, list):
        raise _Refused('"snapshots" must be a list')
    if status is not None and not isinstance(status, dict):
        raise _Refused('"status" must be an object')
    roots = futures_chain_config.instruments()
    for index, snapshot in enumerate(snapshots):
        instrument = snapshot.get("instrument") if isinstance(snapshot, dict) else None
        if not isinstance(instrument, str) or instrument not in roots:
            raise _Refused(f"snapshot {index + 1} is not a snapshot of {' or '.join(roots)}")
    if not snapshots and status is None:
        raise _Refused("the input holds nothing to send: no snapshots and no status")
    return snapshots, status


def _split_by_size(snapshots: list):
    """``(kept, dropped)``: the snapshots within ``max_snapshot_kb`` and a sentence
    for each that is not. One instrument's snapshot never blocks the other's."""
    limit_kb = futures_chain_config.capture()["max_snapshot_kb"]
    kept, dropped = [], []
    for snapshot in snapshots:
        size = len(json.dumps(snapshot, separators=(",", ":")).encode("utf-8"))
        if size > limit_kb * 1024:
            dropped.append(f"the {snapshot['instrument']} snapshot is {size // 1024} KB, "
                           f"over the {limit_kb} KB limit; it was not sent")
        else:
            kept.append(snapshot)
    return kept, dropped


def _enqueue(bus, commands: list) -> list:
    """Put each command on the stream. On a failure part way, say how many got
    through: the ones before it are already there."""
    queued = []
    for kind, args in commands:
        try:
            bus.enqueue_command(futures_chain.STREAM, {"type": kind, "args": args})
        except Exception as exc:  # noqa: BLE001 - the message IS the feature
            hint = ""
            if "authenticat" in str(exc).lower():
                hint = ("\nRedis needs a password. Run this through "
                        "tools/futures_chain_remote.sh, or load the stack's "
                        "environment first, from the repo root:"
                        "\n    set -a && . ./.env && set +a")
            raise _Refused(f"could not write to Redis: {exc} ({len(queued)} of "
                           f"{len(commands)} commands were queued){hint}", EXIT_BUS) from None
        queued.append(kind)
    return queued


def _plan(stdout) -> int:
    stdout.write(json.dumps(futures_chain.build_plan(_now())) + "\n")
    return EXIT_OK


def _ingest(stdin, stdout) -> int:
    snapshots, status = _parse(_read(stdin))
    snapshots, dropped = _split_by_size(snapshots)
    commands = [("snapshot", snapshot) for snapshot in snapshots]
    if status is not None:
        commands.append(("pc_status", status))
    queued = _enqueue(Bus(), commands) if commands else []
    stdout.write(json.dumps({"queued": queued}) + "\n")
    for sentence in dropped:
        sys.stderr.write(f"futures_chain_cli ingest: {sentence}\n")
    return EXIT_TOO_BIG if dropped else EXIT_OK


def main(argv=None, stdin=None, stdout=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if len(argv) != 1 or argv[0] not in ("plan", "ingest"):
        sys.stderr.write(USAGE + "\n")
        return EXIT_BAD_INPUT
    try:
        return _plan(stdout) if argv[0] == "plan" else _ingest(stdin, stdout)
    except _Refused as refusal:
        sys.stderr.write(f"futures_chain_cli {argv[0]}: {refusal}\n")
        return refusal.code


if __name__ == "__main__":
    raise SystemExit(main())
