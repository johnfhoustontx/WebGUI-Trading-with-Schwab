"""Named numeric guards: one definition of "is this a usable number".

There were 35 private one-argument ``_num`` / ``_finite`` helpers across
``services/``, ``shared/`` and ``webgui/pages`` with 15 different behaviours
(measured 2026-10-04, audit CQ-07). Nine let NaN through. Eleven raised
``OverflowError`` on an int too large for a float. A caller could not tell
which one a module's ``_num`` was without reading it.

The behaviours live here by NAME, so a caller picks one on purpose:

* :func:`finite` - the value already IS a number (an int or a float, never a
  bool, never text) and is finite.
* :func:`parsed_finite` - the same, and text or another number type that
  spells a finite number is read.

Both return a ``float`` or ``None`` and neither raises. ``None`` means "no
usable reading"; what a missing reading implies (neutral, zero, drop the row)
is the caller's decision and is not made here.

Standard library only, so every tier may import it. (The web tier's own shared
helper is ``pages.fmt``; this module is not on its import allow-list yet.)

A module moves onto these one behaviour group at a time, after its members
were shown to behave alike. ``shared/tests/test_numeric.py`` holds the list of
private copies that remain; it can only get shorter.
"""
from __future__ import annotations

import math


def finite(value) -> float | None:
    """``value`` as a float when it is a real, finite number; else None.

    An int or a float only. A bool is refused (``True`` is an int, so an
    unguarded flag would read as 1.0), and so is text, however numeric it
    looks: use :func:`parsed_finite` where text is expected. NaN, the
    infinities and an int too large for a float are None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except OverflowError:
        return None
    return out if math.isfinite(out) else None


def parsed_finite(value) -> float | None:
    """``value`` as a finite float, reading text and other number types; else
    None.

    Anything ``float()`` accepts except a bool: ``"3"``, ``" 2.5 "``, a
    ``Decimal``. Text that spells NaN or an infinity parses to one and is
    refused like one. Whatever ``float()`` raises is None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except Exception:  # noqa: BLE001 - a guard that raises is not a guard.
        return None
    return out if math.isfinite(out) else None
