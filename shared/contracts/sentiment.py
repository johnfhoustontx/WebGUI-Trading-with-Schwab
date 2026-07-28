from numbers import Real

from pydantic import ConfigDict, Field, field_validator

from .envelope import _Base

# The 5 market-regime names. Hardcoded here (not imported from
# sentiment-dashboard) — the contracts package must not import app code.
_REGIME_KEYS = frozenset(
    {"mean_reversion", "trending", "breakout", "choppy", "crisis"}
)


class CompositeSnapshot(_Base):
    total: float
    bias: str = ""
    components: dict = {}


class IntradayHistory(_Base):
    points: list = []   # [{"ts": int, "sentiment": float, "trend": float}, ...]


class RegimeState(_Base):
    ts: str                          # ISO-8601 of this sample
    as_of: str = ""                  # human/display stamp (may equal ts)
    memberships: dict                # exactly the 5 regime keys -> float
    raw: dict                        # exactly the 5 regime keys -> float
    confidence: float
    unclear: bool = False
    label: str = ""                  # display label (e.g. "Mean Reversion")
    committed_label: str = ""        # hysteresis-committed label key
    transition: dict | None = None   # {"from","to","progress"} or None
    evidence: list = []              # list[str] for the UI "why" popup
    version_info: dict = {}          # optional provenance (model version etc.)

    @field_validator("memberships", "raw")
    @classmethod
    def _exactly_five_numeric_regimes(cls, v: dict) -> dict:
        keys = set(v)
        if keys != set(_REGIME_KEYS):
            raise ValueError(
                f"expected exactly the regime keys {sorted(_REGIME_KEYS)}, got {sorted(keys)}"
            )
        for k, val in v.items():
            if isinstance(val, bool) or not isinstance(val, Real):
                raise ValueError(f"regime value for {k!r} must be a number, got {val!r}")
        return v

    @field_validator("transition")
    @classmethod
    def _transition_has_keys(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        missing = {"from", "to", "progress"} - set(v)
        if missing:
            raise ValueError(f"transition missing keys {sorted(missing)}")
        return v


class MomentumSnapshot(_Base):
    """The nightly momentum cascade — its OWN cache view.

    Deliberately NOT part of the sentiment composite: scoring/__init__.WEIGHTS
    is untouched and the bridge never sees these numbers. Momentum is context,
    published on its own key. Fields are additive across minor versions, same
    rule as the bridge.
    """
    model_config = ConfigDict(populate_by_name=True)

    # The wire key is "schema" (the design's payload), but a field named
    # `schema` shadows pydantic's BaseModel.schema and warns at class creation.
    schema_version: int = Field(1, alias="schema")
    computed_at: str = ""
    session_date: str
    regime: dict = {}
    levels: dict                     # exactly sector / industry / stock
    rank_history: dict = {}          # level -> {symbol: [(date, rank)]} (ribbon)
    excluded: list = []              # [{"symbol", "reason"}] -> the page footer

    @field_validator("levels")
    @classmethod
    def _exactly_three_levels(cls, v: dict) -> dict:
        expected = {"sector", "industry", "stock"}
        if set(v) != expected:
            raise ValueError(
                f"expected exactly the levels {sorted(expected)}, got {sorted(v)}")
        for name, rows in v.items():
            if not isinstance(rows, list):
                raise ValueError(f"level {name!r} must be a list, got {type(rows)}")
        return v
