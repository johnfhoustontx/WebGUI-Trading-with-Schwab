"""Thin wrapper over redis-py — the storage/communication backbone.

``Bus`` provides three things over a single Redis (or Memurai) connection:

* a **versioned cache** (``cache_set`` / ``cache_get``) — each write bumps a
  version counter (``INCR``) then stores a :class:`CacheEnvelope`. The ``INCR``
  is atomic; the INCR+SET pair is best-effort (not a single transaction) —
  acceptable for the single-user app;
* **pub/sub** (``publish`` / ``subscribe``) for fan-out notifications;
* a **Redis Streams command queue** (``enqueue_command`` / ``consume_commands``
  / ``ack``) with consumer groups.

Under pytest (or with ``fake=True``) it auto-selects an in-memory
``fakeredis`` backend so tests need no live server.
"""
import json
from typing import cast
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

from shared.contracts.envelope import CacheEnvelope, Command

# Repo root on sys.path -> repo_paths is importable (same pattern as webgui/proxy.py).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import MEMURAI_URL  # noqa: E402

# Cap the command streams (``cmd:*``) so they cannot grow without bound. XADD
# trims (approximately, for speed) to roughly this many entries. A single-user
# stack issues at most a few commands/second, so ~1000 is a generous window for
# inspection/replay while guaranteeing bounded memory.
_XADD_MAXLEN = 1000


class _Subscription:
    """Wraps a redis pubsub so callers get decoded dict payloads back.

    The first messages off a fresh pubsub are the subscribe confirmation, so we
    ignore those and poll within the timeout budget until a real message lands.
    """

    def __init__(self, pubsub):
        self._pubsub = pubsub

    def get_message(self, timeout: float):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            raw = self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=remaining
            )
            if raw is None:
                continue
            if raw.get("type") == "message":
                return json.loads(raw["data"])
            # subscribe/unsubscribe confirmation — keep waiting.

    def close(self) -> None:
        """Unsubscribe and release the underlying pubsub connection."""
        self._pubsub.unsubscribe()
        self._pubsub.close()

    def __enter__(self) -> "_Subscription":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# --- fake-bus backing store (tests only) ------------------------------------
# Prod has ONE Memurai, so every Bus in the process sees the same data. A fake
# that gave each Bus its own store would lie about exactly the thing several
# modules depend on: options_svc.compute._BRIEFING_BUS, trade_svc.compute._BUS,
# webgui/bus_client._bus and _scaffold's ``the_bus or Bus()`` all construct their
# OWN bus rather than receiving one, so a test handing a handler its own fake bus
# would have them reading an empty cache and passing down the degrade path.
#
# One FakeServer per RUNNING TEST gives both halves at once: shared within a
# test, clean between tests - and it needs no conftest wiring in any of the ~15
# suites that use the fake bus, because pytest rewrites PYTEST_CURRENT_TEST for
# every test (and phase). The phase suffix is stripped so a fixture's writes are
# visible in the test body.
_fake_servers: dict = {}


def _fake_server():
    import fakeredis

    key = os.environ.get("PYTEST_CURRENT_TEST", "").split(" ")[0]
    srv = _fake_servers.get(key)
    if srv is None:
        srv = fakeredis.FakeServer()
        _fake_servers.clear()      # only the current test's server is ever live
        _fake_servers[key] = srv
    return srv


def reset_fake_bus() -> None:
    """Drop the fake backing store (test hook; the per-test key usually suffices)."""
    _fake_servers.clear()


class Bus:
    def __init__(self, fake: bool = False, url: str | None = None):
        if fake or os.environ.get("PYTEST_CURRENT_TEST"):
            import fakeredis

            self._r = fakeredis.FakeStrictRedis(server=_fake_server(),
                                                decode_responses=True)
        else:
            import redis

            # Optional Memurai/Redis auth (backward-compatible): when MEMURAI_PASSWORD is
            # set AND Memurai is configured with `requirepass`, every service authenticates
            # with it. Unset → password=None → no AUTH, exactly as before.
            self._r = redis.Redis.from_url(
                url or MEMURAI_URL, decode_responses=True,
                password=os.environ.get("MEMURAI_PASSWORD") or None)
        self._groups: set = set()  # (stream, group) consumer groups already ensured

    # --- versioned cache -------------------------------------------------
    def cache_set(
        self,
        key: str,
        payload: dict,
        event: str | None = None,
        skip_unchanged: bool = False,
        ttl: int | None = None,
    ) -> int:
        """Write ``payload`` under ``key`` and return its version.

        ``skip_unchanged`` — for periodic republishers (e.g. the options
        header / GEX-status ticks). When set, if the currently-stored payload is
        byte-identical to ``payload`` the payload write is skipped: no ``INCR``,
        no envelope ``SET``, and no event publish, returning the existing version.
        This stops unchanged data from bumping the version and waking every GUI
        version-poller into a needless repaint. The ``{key}:ts`` freshness stamp
        IS still refreshed — see the comment at the short-circuit for why a
        publisher whose data legitimately stops moving must not look dead.

        ``ttl`` — seconds after which the key expires, applied to the payload AND
        its ``:ver``/``:ts`` side keys (an un-expired counter would outlive its
        payload as an orphan). Refreshed on every write, so a view that is still
        being republished never expires under a reader. For per-entity keys that
        accumulate one-per-thing forever — the per-position rescue boards write
        one key per rescued trade and the bus has no delete API, so 37 had piled
        up in prod including boards for long-closed trades (2026-08-20).

        ``event`` — when given, the change event ``{"version": …}`` is published
        on that channel **as part of the same write** (pipelined with the
        ``SET``, one round trip), and is likewise skipped when ``skip_unchanged``
        short-circuits. (The ``INCR`` stays a separate call because its result is
        embedded in the stored envelope, so the ``SET`` value depends on it.)
        Callers that publish themselves can omit ``event`` — behaviour is then
        identical to the original two-line set+publish.
        """
        if skip_unchanged:
            current = self.cache_get(key)
            if current is not None and current.payload == payload:
                # Refresh the freshness side key even though the payload write is
                # skipped. This is the whole difference between the two stamps:
                # ``{key}:ts`` answers "when did the publisher last CONFIRM this is
                # current", which is what a freshness/health probe needs, while the
                # envelope's own ``ts`` still answers "when did it last CHANGE".
                #
                # Without it a healthy publisher looks dead whenever its data
                # legitimately stops moving: market_svc polls round the clock, but
                # over a weekend the quotes are frozen, every poll produces a
                # byte-identical payload, and cache:market:dashboard measured 18h
                # "stale" on the System Status board while the service was fine.
                # The bug is latent in EVERY skip_unchanged view, not just that one.
                #
                # Costs one SET per skipped publish and — deliberately — does NOT
                # touch ``:ver``, so the version-pollers this flag exists to protect
                # still see nothing and still do not repaint.
                self._r.set(f"{key}:ts", datetime.now(timezone.utc).isoformat())
                if ttl is not None:
                    # A skipped publish still means "this is current" — renew the
                    # whole family or a static-but-live view expires mid-session.
                    for k in (key, f"{key}:ver", f"{key}:ts"):
                        self._r.expire(k, ttl)
                return current.version
        version = self._r.incr(f"{key}:ver")
        env = CacheEnvelope(
            version=version,
            ts=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )
        # ``{key}:ts`` is a tiny side key (like ``:ver``) so freshness probes
        # (cache_metas) never deserialize the payload. Written in the same pipeline
        # as the SET. NOTE it is NOT simply a mirror of the envelope ts: a
        # skip_unchanged short-circuit refreshes it too (see above), so ``:ts`` is
        # "last confirmed current" while the envelope's ts is "last changed".
        pipe = self._r.pipeline()
        pipe.set(key, env.to_json())
        pipe.set(f"{key}:ts", env.ts)
        if ttl is not None:
            for k in (key, f"{key}:ver", f"{key}:ts"):
                pipe.expire(k, ttl)
        if event is not None:
            pipe.publish(event, json.dumps({"version": version}))
        pipe.execute()
        return version

    def cache_get(self, key: str) -> CacheEnvelope | None:
        raw = self._r.get(key)
        if raw is None:
            return None
        # Every client here is built with decode_responses=True (both branches of
        # __init__), so redis returns str; the stubs' bytes|str is the general
        # case, not ours. cast rather than ignore, so the invariant is stated.
        return CacheEnvelope.from_json(cast(str, raw))

    def cache_version(self, key: str) -> int | None:
        """Read just the version counter (``{key}:ver``) — NO payload deserialize.

        ``cache_set`` keeps this counter in lockstep with the stored envelope's
        ``version`` (INCR on every write, untouched when ``skip_unchanged`` skips).
        A cheap change-probe for version-poll timers: ``GET`` a tiny int instead
        of fetching + JSON-deserializing the whole payload envelope."""
        v = self._r.get(f"{key}:ver")
        return int(v) if v is not None else None

    def cache_versions(self, keys) -> dict:
        """Pipelined :meth:`cache_version` for many keys → ``{key: int|None}``.

        One round-trip for the whole batch (e.g. the Gamma page's four views)."""
        keys = list(keys)
        if not keys:
            return {}
        pipe = self._r.pipeline()
        for k in keys:
            pipe.get(f"{k}:ver")
        vals = pipe.execute()
        return {k: (int(v) if v is not None else None) for k, v in zip(keys, vals)}

    def cache_metas(self, keys) -> dict:
        """Pipelined ``(version, ts)`` probe for many keys → ``{key: (int|None, str|None)}``.

        Reads only the tiny ``:ver`` + ``:ts`` side keys — NO payload deserialize.
        One round-trip for the whole batch (e.g. the watcher's freshness views).
        ``ts`` may be None for a key written before the ``:ts`` side key existed —
        callers needing back-compat can fall back to ``cache_get`` for those."""
        keys = list(keys)
        if not keys:
            return {}
        pipe = self._r.pipeline()
        for k in keys:
            pipe.get(f"{k}:ver")
            pipe.get(f"{k}:ts")
        vals = pipe.execute()
        out = {}
        for i, k in enumerate(keys):
            ver, ts = vals[2 * i], vals[2 * i + 1]
            out[k] = (int(ver) if ver is not None else None, ts)
        return out

    # --- pub/sub ---------------------------------------------------------
    def publish(self, channel: str, message: dict) -> None:
        self._r.publish(channel, json.dumps(message))

    def subscribe(self, channel: str) -> _Subscription:
        pubsub = self._r.pubsub()
        pubsub.subscribe(channel)
        return _Subscription(pubsub)

    # --- command stream --------------------------------------------------
    @staticmethod
    def dead_letter_key(stream: str) -> str:
        """The dead-letter list name for a command ``stream`` (``cmd:{domain}:dead``)."""
        return f"{stream}:dead"

    def enqueue_command(self, stream: str, command: dict) -> str:
        cmd = Command(**command)
        # maxlen caps the stream so cmd:* can't grow forever; approximate=True lets
        # Redis trim in efficient ~macro-node batches (a small overshoot is fine).
        return cast(str, self._r.xadd(          # decode_responses=True -> str
            stream, {"data": cmd.to_json()}, maxlen=_XADD_MAXLEN, approximate=True
        ))

    def dead_letter(self, stream: str, raw_fields: dict, reason: str) -> None:
        """Record an un-processable command on the ``{stream}:dead`` list.

        A command handler that raises, or a stream entry that fails to decode,
        is routed here (rather than silently lost) so a human can inspect/replay
        it. We deliberately do NOT auto-re-execute — a stranded trade-opening
        command (e.g. ``driver_paper_create`` / ``rescue_apply``) re-run could
        double-open a position. Stores the raw XADD fields verbatim + why.
        """
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "fields": raw_fields,
        }
        try:
            self._r.rpush(self.dead_letter_key(stream), json.dumps(record, default=str))
        except Exception:  # never let dead-lettering itself take down the loop.
            pass

    def _ensure_group(self, stream: str, group: str) -> None:
        # Ensure the consumer group exists ONCE per (stream, group) for this Bus,
        # not on every poll. consume_commands runs in a tight ~50ms loop in each
        # of the 5 services; the old per-poll xgroup_create was an extra round-trip
        # + a swallowed BUSYGROUP exception on every single poll.
        gk = (stream, group)
        if gk in self._groups:
            return
        try:
            self._r.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP — group already exists.
            if "BUSYGROUP" not in str(exc):
                raise
        self._groups.add(gk)

    def drain_pending(self, stream: str, group: str, consumer: str) -> int:
        """Move any entries stranded in the group's PEL to the dead-letter list.

        Called once at consumer startup: a previous consumer that crashed leaves
        its read-but-un-acked entries PENDING forever. We claim them (XAUTOCLAIM,
        min-idle 0) and dead-letter + ack each — surfacing stranded commands for
        human review instead of losing them, and WITHOUT auto-re-executing (a
        re-run trade-opening command could double-open a position). Returns the
        number of entries drained. Defensive — never raises.
        """
        self._ensure_group(stream, group)
        moved = 0
        start = "0-0"
        try:
            while True:
                res = self._r.xautoclaim(
                    stream, group, consumer, min_idle_time=0, start_id=start, count=100
                )
                # redis-py: (next_cursor, [(id, fields), ...], [deleted_ids])
                next_cursor = res[0]
                claimed = res[1] if len(res) > 1 else []
                for msg_id, fields in claimed:
                    self.dead_letter(stream, fields, f"stranded in PEL ({msg_id})")
                    self._r.xack(stream, group, msg_id)
                    moved += 1
                if not claimed or next_cursor in ("0-0", "0", b"0-0", b"0"):
                    break
                start = next_cursor
        except Exception:  # noqa: BLE001 — draining must never crash startup.
            log_exc = getattr(self, "_log_drain_exc", None)
            if log_exc:
                log_exc()
        return moved

    def consume_commands(
        self,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 50,
        count: int = 10,
    ) -> list[tuple[str, Command]]:
        """Read up to ``count`` new commands, decoding each defensively.

        A stream entry that fails to decode (poison / corrupt) is dead-lettered
        and ack'd in place and skipped — it never fails the whole batch nor sticks
        un-acked in the PEL. Only successfully-decoded ``(msg_id, Command)`` pairs
        are returned (the return shape is unchanged for callers).
        """
        self._ensure_group(stream, group)
        resp = self._r.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )
        if not resp:
            return []
        out: list[tuple[str, Command]] = []
        # xreadgroup's stub covers every response shape (including the int of an
        # async/raw client); ours is always [(stream, [(id, fields), ...])].
        for _stream_name, messages in cast(list, resp):
            for msg_id, fields in messages:
                try:
                    out.append((msg_id, Command.from_json(fields["data"])))
                except Exception as exc:  # noqa: BLE001 — poison entry, don't sink the batch.
                    self.dead_letter(stream, fields, f"decode failed: {exc!r}")
                    self._r.xack(stream, group, msg_id)
        return out

    def ack(self, stream: str, group: str, msg_id: str) -> None:
        self._r.xack(stream, group, msg_id)
