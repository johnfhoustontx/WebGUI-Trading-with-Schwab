from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Base(BaseModel):
    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str):
        return cls.model_validate_json(raw)


class CacheEnvelope(_Base):
    version: int
    ts: str
    payload: dict


class Command(_Base):
    type: str
    args: dict = {}
    # Enqueue timestamp (ISO-8601, UTC). Additive + back-compatible: the
    # default_factory fires ONLY when ``ts`` is absent, so it stamps at
    # enqueue-side construction (``Bus.enqueue_command`` does ``Command(**dict)``)
    # and is carried verbatim through ``to_json``/``from_json`` — a decoded NEW
    # command keeps its original enqueue time (the factory does NOT re-stamp when
    # the key is present). An OLDER command serialized before this field existed
    # has no ``ts`` key, so on decode the factory stamps it fresh → its age reads
    # ~0 → the R5 staleness gate treats it as NOT stale (never rejects a legacy
    # command). Consumers wanting an explicit "unknown" can pass ``ts=None``.
    ts: str | None = Field(default_factory=_now_iso)
