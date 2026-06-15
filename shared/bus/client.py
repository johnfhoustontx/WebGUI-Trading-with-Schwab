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


class Bus:
    def __init__(self, fake: bool = False, url: str | None = None):
        if fake or os.environ.get("PYTEST_CURRENT_TEST"):
            import fakeredis

            self._r = fakeredis.FakeStrictRedis(decode_responses=True)
        else:
            import redis

            self._r = redis.Redis.from_url(url or MEMURAI_URL, decode_responses=True)

    # --- versioned cache -------------------------------------------------
    def cache_set(self, key: str, payload: dict) -> int:
        version = self._r.incr(f"{key}:ver")
        env = CacheEnvelope(
            version=version,
            ts=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )
        self._r.set(key, env.to_json())
        return version

    def cache_get(self, key: str) -> CacheEnvelope | None:
        raw = self._r.get(key)
        if raw is None:
            return None
        return CacheEnvelope.from_json(raw)

    # --- pub/sub ---------------------------------------------------------
    def publish(self, channel: str, message: dict) -> None:
        self._r.publish(channel, json.dumps(message))

    def subscribe(self, channel: str) -> _Subscription:
        pubsub = self._r.pubsub()
        pubsub.subscribe(channel)
        return _Subscription(pubsub)

    # --- command stream --------------------------------------------------
    def enqueue_command(self, stream: str, command: dict) -> str:
        cmd = Command(**command)
        return self._r.xadd(stream, {"data": cmd.to_json()})

    def consume_commands(
        self,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 50,
        count: int = 10,
    ) -> list[tuple[str, Command]]:
        try:
            self._r.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP — group already exists.
            if "BUSYGROUP" not in str(exc):
                raise
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
        for _stream_name, messages in resp:
            for msg_id, fields in messages:
                out.append((msg_id, Command.from_json(fields["data"])))
        return out

    def ack(self, stream: str, group: str, msg_id: str) -> None:
        self._r.xack(stream, group, msg_id)
