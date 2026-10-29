"""shared.paper_limits.daily_condor and the [slots.daily_condor] time."""
import datetime as dt

import pytest

from shared import market_calendar as mc
from shared import paper_limits as pl

SHIPPED = {"enabled": True, "symbol": "SPY", "short_delta": 0.15, "width": 5.0,
           "stop_mult": 2.0, "quantity": 1}


@pytest.fixture
def toml(tmp_path, monkeypatch):
    """Point the loader at a scratch file; returns a writer."""
    path = tmp_path / "paper.toml"
    from shared.config_toml import toml_loader

    load, reset = toml_loader(path, pl.DEFAULTS, label="paper.toml")
    monkeypatch.setattr(pl, "load", load)

    def write(text):
        path.write_text(text, encoding="utf-8")
        reset()
    return write


def test_shipped_values():
    assert pl.daily_condor() == SHIPPED


def test_missing_file_reads_the_defaults(toml):
    toml("")
    assert pl.daily_condor() == SHIPPED


def test_the_file_overrides_each_key(toml):
    toml("[daily_condor]\nenabled = false\nsymbol = \"qqq\"\nshort_delta = 0.2\n"
         "width = 10\nstop_mult = 1.5\nquantity = 3\n")
    assert pl.daily_condor() == {"enabled": False, "symbol": "QQQ", "short_delta": 0.2,
                                 "width": 10.0, "stop_mult": 1.5, "quantity": 3}


def test_a_bad_key_reverts_alone(toml):
    toml("[daily_condor]\nshort_delta = 0.7\nwidth = 10\n")
    got = pl.daily_condor()
    assert got["short_delta"] == 0.15 and got["width"] == 10.0


@pytest.mark.parametrize("line, key", [
    ("enabled = \"yes\"", "enabled"),
    ("symbol = \"not a ticker!\"", "symbol"),
    ("short_delta = 0", "short_delta"),
    ("width = 0", "width"),
    ("width = 101", "width"),
    ("stop_mult = 0", "stop_mult"),
    ("stop_mult = 21", "stop_mult"),
    ("quantity = 0", "quantity"),
    ("quantity = 1.5", "quantity"),
])
def test_unusable_values_read_as_the_default(toml, line, key):
    toml(f"[daily_condor]\n{line}\n")
    assert pl.daily_condor()[key] == SHIPPED[key]


def test_the_slot_is_0845_with_a_ten_minute_grace():
    assert mc.slot_times("daily_condor") == {"at": dt.time(8, 45)}
    assert mc.slot_grace_min("daily_condor") == 10
