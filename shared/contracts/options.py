from .envelope import _Base


class ScanResult(_Base):
    """Options-scan payload produced by ``scanner_engine.run_full_scan``.

    Signals are heterogeneous, sparse dicts that vary by trade type
    (PCS / CCS / IC), so the lists are modelled loosely as ``list[dict]``.
    This contract validates the *envelope* shape (the lists/dicts exist and
    have the right container types) as a gate against gross drift — it does
    not over-specify each signal (the GUI's ``signal_rows`` already tolerates
    sparse fields).
    """

    signals_0dte: list[dict] = []
    signals_swing: list[dict] = []
    vix_term_structure: dict = {}
    timestamp: str | None = None
    errors: list = []
    warnings: list = []
