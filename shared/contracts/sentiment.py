from .envelope import _Base


class CompositeSnapshot(_Base):
    total: float
    bias: str = ""
    components: dict = {}


class IntradayHistory(_Base):
    points: list = []   # [{"ts": int, "sentiment": float, "trend": float}, ...]
