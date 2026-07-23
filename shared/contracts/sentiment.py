from numbers import Real

from pydantic import field_validator

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
