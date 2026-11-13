"""A one-line note about where an exception came from, never what it said.

``str(exc)`` can quote a handle, a group name or a URL carrying one, so the
degrade notes in xfeed_svc pass ``exc_info=False`` and a ``detail`` built here.
Same shape as ``blog_svc._trace.where``.
"""
import os
import traceback


def where(doing, exc) -> str:
    frames = traceback.extract_tb(exc.__traceback__, limit=-4)
    trail = " < ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in reversed(frames))
    return f"{doing}: {type(exc).__name__} at {trail or 'no frame'}"
