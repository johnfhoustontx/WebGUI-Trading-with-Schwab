from pydantic import BaseModel


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
