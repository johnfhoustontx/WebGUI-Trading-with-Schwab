"""Sector reference data — workbook loader + S&P cap weights.

Extracted from the (uncopied) tk dashboard so the webgui Sentiment page and
the headless snapshot can build the ``sector_data`` rows that ``scoring``
and ``history_backfill`` consume, without importing tkinter.
"""
from pathlib import Path

SECTORS_XLSX = Path(__file__).parent / "Sectors_Industries_ETFs.xlsx"

# S&P 500 sector weights (percent, ~sum 100) — drives cap-weighted sector_perf.
SP500_SECTOR_WEIGHTS = {
    "Information Technology": 32.53,
    "Financials":             13.42,
    "Communication Services": 10.16,
    "Consumer Discretionary":  9.94,
    "Industrials":             8.86,
    "Health Care":             8.63,
    "Energy":                  4.89,
    "Consumer Staples":        4.61,
    "Materials":               2.74,
    "Real Estate":             2.12,
    "Utilities":               2.09,
}

CYCLICAL_SECTORS = {
    "Consumer Discretionary", "Financials", "Industrials",
    "Information Technology", "Communication Services",
    "Materials", "Energy",
}
DEFENSIVE_SECTORS = {
    "Consumer Staples", "Utilities", "Health Care", "Real Estate",
}


def load_sectors_data(xlsx_path=SECTORS_XLSX):
    """Load sector / industry / ETF rows from the reference workbook.

    Returns a list of dicts in display order:
        {kind: 'sector'|'industry', sector, label, etf, name, notes, sp_weight}
    Returns [] if the workbook or openpyxl is unavailable.
    """
    try:
        import openpyxl
    except ImportError:
        return []
    try:
        wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
    except Exception:
        return []

    sector_primary = {}
    sector_descriptions = {}
    sector_order = []
    for i, row in enumerate(wb["Sectors"].iter_rows(values_only=True)):
        if i == 0 or not row or not row[1]:
            continue
        sector_name, spdr = row[1], row[2]
        description = row[5] if len(row) > 5 else None
        sector_primary[sector_name] = spdr
        sector_descriptions[sector_name] = description or ''
        sector_order.append(sector_name)

    industries_by_sector = {}
    seen_industry = set()
    seen_etf = set()
    for i, row in enumerate(wb["Industries"].iter_rows(values_only=True)):
        if i == 0 or not row or not row[0]:
            continue
        sector = row[0]
        industry = row[1] if len(row) > 1 else None
        etf = row[2] if len(row) > 2 else None
        etf_name = row[3] if len(row) > 3 else None
        notes = row[5] if len(row) > 5 else None
        if not etf:
            continue
        key = (sector, industry)
        if key in seen_industry or etf in seen_etf:
            continue
        seen_industry.add(key)
        seen_etf.add(etf)
        industries_by_sector.setdefault(sector, []).append(
            {'industry': industry, 'etf': etf,
             'name': etf_name or '', 'notes': notes or ''})

    rows = []
    for sector_name in sector_order:
        rows.append({
            'kind': 'sector', 'sector': sector_name,
            'label': sector_name, 'etf': sector_primary.get(sector_name),
            'name': sector_descriptions.get(sector_name, ''), 'notes': '',
            'sp_weight': SP500_SECTOR_WEIGHTS.get(sector_name, 0.0),
        })
        for ind in industries_by_sector.get(sector_name, []):
            rows.append({
                'kind': 'industry', 'sector': sector_name,
                'label': ind['industry'] or ind['etf'], 'etf': ind['etf'],
                'name': ind['name'], 'notes': ind['notes'],
                'sp_weight': 0.0,
            })
    return rows
