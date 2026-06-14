import pathlib

import pytest

from src.trades_import import parse_trades_csv

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "trades_sample.csv"


def _write_csv(tmp_path, body: str) -> str:
    path = tmp_path / "trades.csv"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_parse_trades_csv_maps_headers():
    rows = parse_trades_csv(str(FIXTURE))
    assert len(rows) == 3
    aapl = [r for r in rows if r["symbol"] == "AAPL"]
    assert aapl[0]["instruction"] == "BUY"
    assert aapl[0]["quantity"] == 10.0
    assert aapl[0]["price"] == 150.0
    assert aapl[0]["trade_date"] == "2026-05-20"
    assert all("trade_id" in r for r in rows)


def test_sell_action_maps_to_sell(tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "Date,Action,Symbol,Quantity,Price\n"
        "05/20/2026,Sold,AAPL,10,150.00\n",
    )
    rows = parse_trades_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["instruction"] == "SELL"


def test_iso_date_parses(tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "Date,Action,Symbol,Quantity,Price\n"
        "2026-05-20,Buy,AAPL,10,150.00\n",
    )
    rows = parse_trades_csv(csv_path)
    assert rows[0]["trade_date"] == "2026-05-20"


def test_negative_quantity_stored_as_positive(tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "Date,Action,Symbol,Quantity,Price\n"
        "05/20/2026,Sell,AAPL,-10,150.00\n",
    )
    rows = parse_trades_csv(csv_path)
    assert rows[0]["quantity"] == 10.0


def test_blank_trailing_line_skipped(tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "Date,Action,Symbol,Quantity,Price\n"
        "05/20/2026,Buy,AAPL,10,150.00\n"
        "\n",
    )
    rows = parse_trades_csv(csv_path)
    assert len(rows) == 1


def test_missing_required_column_raises(tmp_path):
    # No price column -> should fail loudly.
    csv_path = _write_csv(
        tmp_path,
        "Date,Action,Symbol,Quantity\n"
        "05/20/2026,Buy,AAPL,10\n",
    )
    with pytest.raises(ValueError):
        parse_trades_csv(csv_path)


def test_nonsense_action_raises(tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "Date,Action,Symbol,Quantity,Price\n"
        "05/20/2026,Split,AAPL,10,150.00\n",
    )
    with pytest.raises(ValueError):
        parse_trades_csv(csv_path)
