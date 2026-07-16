def test_config_holidays_is_the_shared_set():
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
    from shared.market_calendar import HOLIDAYS
    import scanner
    assert scanner.Config.HOLIDAYS is HOLIDAYS
