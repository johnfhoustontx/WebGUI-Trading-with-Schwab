"""Options engine import helpers.

The options-scanner ships a top-level ``scoring.py`` module while the
sentiment-dashboard ships a ``scoring/`` **package**. Both folders end up on
``sys.path``, so whichever is imported first wins ``sys.modules['scoring']``
for the whole process. The options engines (``scanner_engine.run_full_scan``,
``scoring.score_all_signals``) import ``scoring`` *lazily*, so we pin the
options module for the duration of an options engine call and restore the prior
binding afterward — correct regardless of which page loaded first.
"""
import importlib.util
import sys
from contextlib import contextmanager

from repo_paths import OPTIONS_SCANNER


@contextmanager
def options_scoring():
    """Temporarily bind the top-level name ``scoring`` to options-scanner."""
    prev = sys.modules.get("scoring")
    prev_sub = {k: v for k, v in list(sys.modules.items()) if k.startswith("scoring.")}
    for k in prev_sub:
        sys.modules.pop(k, None)
    spec = importlib.util.spec_from_file_location("scoring", str(OPTIONS_SCANNER / "scoring.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scoring"] = mod
    spec.loader.exec_module(mod)
    try:
        yield
    finally:
        if prev is not None:
            sys.modules["scoring"] = prev
        else:
            sys.modules.pop("scoring", None)
        sys.modules.update(prev_sub)
