from .envelope import _Base


class CompositeSnapshot(_Base):
    total: float
    bias: str = ""
    components: dict = {}
