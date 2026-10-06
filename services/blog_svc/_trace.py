"""A one-line, document-free note about where an exception came from.

A ``_degrade.degraded`` call with the default ``exc_info=True`` logs the whole
traceback, and a traceback can quote a fragment of the document being cleaned -
an attribute name inside a ``KeyError``, a snippet in a parser message. The blog
never logs the submitter's document. So the guards here pass ``exc_info=False``
and a ``detail`` built by ``where``: the exception's TYPE and the last few
frames it came through, and nothing of its ``str()``.

``blog_svc/fonts.py`` grew its own copy of this first (``_where``); this is the
shared home its owner can adopt.
"""
import os
import traceback


def where(doing, exc) -> str:
    """``"<doing>: <ExcType> at file:line func < …"`` - enough to find a fault,
    never ``str(exc)`` (which can carry document text)."""
    # ``limit=-4`` is the LAST four frames, and reads only those: slicing the
    # whole extracted list would format every frame of a deep traceback (a
    # RecursionError's is a thousand long) to keep four.
    frames = traceback.extract_tb(exc.__traceback__, limit=-4)
    trail = " < ".join(f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}"
                       for frame in reversed(frames))
    return f"{doing}: {type(exc).__name__} at {trail or 'no frame'}"
