"""Offline research harness for the swing factor model (Phase 4).

Everything here is RUN MANUALLY and **never imported by a service** — the same
rule ``fit_swing_model.py`` follows. Its job is to make competing methodology
variants comparable: fetch the panel once, cache it, and score every variant
against those identical bytes.
"""
