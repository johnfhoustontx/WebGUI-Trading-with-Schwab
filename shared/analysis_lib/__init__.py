"""Shared analysis library: technical indicators, sector reference data, config.

Three modules, imported by sentiment_svc, trade_svc, scoring.regime_evidence and
portfolio-analyzer. NOTHING here is an application.

This package used to BE one -- the abandoned "Blueprint Analyzer" Tk app, ~9,600
of 11,406 lines with no callers outside each other -- and this file eagerly
imported all of it, including a `schwab_client` documented in-repo as broken. That
made `from shared.analysis_lib import technical` raise, which is why every live
consumer carried a sys.path bootstrap to import the module standalone and dodge
this init. The app was deleted on 2026-08-20; the bootstraps can go whenever their
files are next touched. Keep this init import-light -- `shared/tests/
test_analysis_lib_surface.py` fails if the package grows an application again.
"""
from . import config, sector_analysis, technical

__all__ = ["config", "sector_analysis", "technical"]
