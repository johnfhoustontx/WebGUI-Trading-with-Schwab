"""
TOS Data Import - Import market data exported from ThinkOrSwim
Version: 1.0.0

Provides functionality to import:
- Watchlist CSV exports
- Breadth data from custom studies
- Scan results
"""

import logging
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TOSBreadthData:
    """Breadth data imported from TOS"""
    timestamp: datetime
    nyse_advances: int
    nyse_declines: int
    nyse_unchanged: int
    nasdaq_advances: int
    nasdaq_declines: int
    new_highs: int
    new_lows: int
    ad_ratio: float
    mcclellan: float
    breadth_thrust: float
    source: str = "TOS"


@dataclass
class TOSScanResult:
    """Stock from TOS scan results"""
    symbol: str
    last_price: float
    change_pct: float
    volume: int
    avg_volume: int
    sector: str
    industry: str
    market_cap: float
    pe_ratio: float
    notes: str = ""


class TOSImporter:
    """Import data from ThinkOrSwim exports"""

    def __init__(self, data_dir: str = None):
        """Initialize importer

        Args:
            data_dir: Directory for TOS export files (default: project/data/tos)
        """
        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            self.data_dir = Path(__file__).parent.parent / 'data' / 'tos'

        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"TOS import directory: {self.data_dir}")

    def import_breadth_csv(self, filepath: str = None) -> Optional[TOSBreadthData]:
        """Import breadth data from TOS watchlist export

        Expected CSV format (from TOS Watchlist export):
        Symbol,Last,Bid,Ask,Volume,...

        Or custom breadth export:
        Metric,Value
        NYSE_ADV,1500
        NYSE_DEC,1200
        ...

        Args:
            filepath: Path to CSV file (or latest in data_dir)

        Returns:
            TOSBreadthData or None
        """
        if filepath is None:
            # Find most recent breadth CSV
            filepath = self._find_latest_file('breadth*.csv')
            if not filepath:
                logger.warning("No breadth CSV files found")
                return None

        try:
            with open(filepath, 'r', newline='', encoding='utf-8') as f:
                # Detect format
                first_line = f.readline().strip()
                f.seek(0)

                if 'Symbol' in first_line:
                    return self._parse_watchlist_breadth(f)
                elif 'Metric' in first_line or 'Name' in first_line:
                    return self._parse_custom_breadth(f)
                else:
                    logger.warning(f"Unknown CSV format: {first_line[:50]}")
                    return None

        except Exception as e:
            logger.error(f"Error importing breadth CSV: {e}")
            return None

    def _parse_watchlist_breadth(self, file) -> Optional[TOSBreadthData]:
        """Parse breadth from TOS watchlist export format"""
        reader = csv.DictReader(file)

        data = {}
        for row in reader:
            symbol = row.get('Symbol', '').strip()
            last = self._parse_number(row.get('Last', '0'))

            # Map TOS symbols to our data
            symbol_map = {
                '$ADVN': 'nyse_advances',
                '$DECN': 'nyse_declines',
                '$UNCN': 'nyse_unchanged',
                '$ADVN/Q': 'nasdaq_advances',
                '$DECN/Q': 'nasdaq_declines',
                '$NYHGH': 'new_highs',
                '$NYLOW': 'new_lows',
            }

            if symbol in symbol_map:
                data[symbol_map[symbol]] = int(last)

        if not data.get('nyse_advances') or not data.get('nyse_declines'):
            logger.warning("Incomplete breadth data in watchlist")
            return None

        # Calculate derived metrics
        total_adv = data.get('nyse_advances', 0) + data.get('nasdaq_advances', 0)
        total_dec = data.get('nyse_declines', 0) + data.get('nasdaq_declines', 0)
        ad_ratio = total_adv / max(total_dec, 1)

        return TOSBreadthData(
            timestamp=datetime.now(),
            nyse_advances=data.get('nyse_advances', 0),
            nyse_declines=data.get('nyse_declines', 0),
            nyse_unchanged=data.get('nyse_unchanged', 0),
            nasdaq_advances=data.get('nasdaq_advances', 0),
            nasdaq_declines=data.get('nasdaq_declines', 0),
            new_highs=data.get('new_highs', 0),
            new_lows=data.get('new_lows', 0),
            ad_ratio=round(ad_ratio, 2),
            mcclellan=0,
            breadth_thrust=0,
            source="TOS_Watchlist"
        )

    def _parse_custom_breadth(self, file) -> Optional[TOSBreadthData]:
        """Parse custom breadth export format

        Format:
        Metric,Value
        NYSE_ADV,1500
        NYSE_DEC,1200
        NASDAQ_ADV,800
        NASDAQ_DEC,600
        NEW_HIGHS,100
        NEW_LOWS,50
        MCCLELLAN,45
        BREADTH_THRUST,55.5
        """
        reader = csv.DictReader(file)

        data = {}
        for row in reader:
            metric = row.get('Metric', row.get('Name', '')).strip().upper()
            value = self._parse_number(row.get('Value', row.get('Last', '0')))

            metric_map = {
                'NYSE_ADV': 'nyse_advances',
                'NYSE_ADVANCES': 'nyse_advances',
                'ADVN': 'nyse_advances',
                '$ADVN': 'nyse_advances',
                'NYSE_DEC': 'nyse_declines',
                'NYSE_DECLINES': 'nyse_declines',
                'DECN': 'nyse_declines',
                '$DECN': 'nyse_declines',
                'NYSE_UNCH': 'nyse_unchanged',
                'NASDAQ_ADV': 'nasdaq_advances',
                'NASDAQ_DEC': 'nasdaq_declines',
                'NEW_HIGHS': 'new_highs',
                'NYHGH': 'new_highs',
                '$NYHGH': 'new_highs',
                'NEW_LOWS': 'new_lows',
                'NYLOW': 'new_lows',
                '$NYLOW': 'new_lows',
                'MCCLELLAN': 'mcclellan',
                'MCCELLAN_OSC': 'mcclellan',
                'BREADTH_THRUST': 'breadth_thrust',
                'AD_RATIO': 'ad_ratio',
            }

            if metric in metric_map:
                data[metric_map[metric]] = value

        # Calculate A/D ratio if not provided
        if 'ad_ratio' not in data:
            total_adv = data.get('nyse_advances', 0) + data.get('nasdaq_advances', 0)
            total_dec = data.get('nyse_declines', 0) + data.get('nasdaq_declines', 0)
            data['ad_ratio'] = total_adv / max(total_dec, 1)

        return TOSBreadthData(
            timestamp=datetime.now(),
            nyse_advances=int(data.get('nyse_advances', 0)),
            nyse_declines=int(data.get('nyse_declines', 0)),
            nyse_unchanged=int(data.get('nyse_unchanged', 0)),
            nasdaq_advances=int(data.get('nasdaq_advances', 0)),
            nasdaq_declines=int(data.get('nasdaq_declines', 0)),
            new_highs=int(data.get('new_highs', 0)),
            new_lows=int(data.get('new_lows', 0)),
            ad_ratio=round(data.get('ad_ratio', 1.0), 2),
            mcclellan=round(data.get('mcclellan', 0), 1),
            breadth_thrust=round(data.get('breadth_thrust', 0), 1),
            source="TOS_Custom"
        )

    def import_scan_results(self, filepath: str = None) -> List[TOSScanResult]:
        """Import scan results from TOS

        Expected format (TOS scan export):
        Symbol,Last,Net Chng,% Chng,Volume,Avg Volume,...

        Args:
            filepath: Path to CSV file

        Returns:
            List of TOSScanResult
        """
        if filepath is None:
            filepath = self._find_latest_file('scan*.csv')
            if not filepath:
                return []

        results = []
        try:
            with open(filepath, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)

                for row in reader:
                    try:
                        result = TOSScanResult(
                            symbol=row.get('Symbol', '').strip(),
                            last_price=self._parse_number(row.get('Last', '0')),
                            change_pct=self._parse_number(row.get('% Chng', row.get('Chng %', '0'))),
                            volume=int(self._parse_number(row.get('Volume', '0'))),
                            avg_volume=int(self._parse_number(row.get('Avg Volume', '0'))),
                            sector=row.get('Sector', ''),
                            industry=row.get('Industry', ''),
                            market_cap=self._parse_number(row.get('Market Cap', '0')),
                            pe_ratio=self._parse_number(row.get('P/E', row.get('PE Ratio', '0'))),
                        )
                        if result.symbol:
                            results.append(result)
                    except Exception as e:
                        logger.debug(f"Error parsing row: {e}")
                        continue

            logger.info(f"Imported {len(results)} scan results from {filepath}")
            return results

        except Exception as e:
            logger.error(f"Error importing scan results: {e}")
            return []

    def import_watchlist(self, filepath: str = None) -> List[str]:
        """Import watchlist symbols from TOS export

        Args:
            filepath: Path to CSV file

        Returns:
            List of symbols
        """
        if filepath is None:
            filepath = self._find_latest_file('watchlist*.csv')
            if not filepath:
                return []

        symbols = []
        try:
            with open(filepath, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)

                for row in reader:
                    symbol = row.get('Symbol', '').strip().upper()
                    # Skip index symbols and empty rows
                    if symbol and not symbol.startswith('$') and not symbol.startswith('.'):
                        symbols.append(symbol)

            logger.info(f"Imported {len(symbols)} symbols from {filepath}")
            return symbols

        except Exception as e:
            logger.error(f"Error importing watchlist: {e}")
            return []

    def watch_for_updates(self, callback, interval_seconds: int = 60):
        """Watch data directory for new files and call callback

        Args:
            callback: Function to call with new data
            interval_seconds: Check interval
        """
        import threading
        import time

        last_check = {}

        def checker():
            while True:
                for pattern in ['breadth*.csv', 'scan*.csv']:
                    latest = self._find_latest_file(pattern)
                    if latest:
                        mtime = Path(latest).stat().st_mtime
                        if pattern not in last_check or mtime > last_check[pattern]:
                            last_check[pattern] = mtime
                            try:
                                if 'breadth' in pattern:
                                    data = self.import_breadth_csv(latest)
                                else:
                                    data = self.import_scan_results(latest)
                                if data:
                                    callback(pattern, data)
                            except Exception as e:
                                logger.error(f"Error processing {pattern}: {e}")

                time.sleep(interval_seconds)

        thread = threading.Thread(target=checker, daemon=True)
        thread.start()
        logger.info(f"Watching {self.data_dir} for TOS exports")

    def _find_latest_file(self, pattern: str) -> Optional[str]:
        """Find most recent file matching pattern"""
        files = list(self.data_dir.glob(pattern))
        if not files:
            return None
        return str(max(files, key=lambda p: p.stat().st_mtime))

    def _parse_number(self, value: str) -> float:
        """Parse number from TOS format (handles K, M, B suffixes)"""
        if not value:
            return 0.0

        value = str(value).strip().replace(',', '').replace('$', '')

        if not value or value == '--' or value == 'N/A':
            return 0.0

        multipliers = {'K': 1000, 'M': 1000000, 'B': 1000000000, 'T': 1000000000000}

        try:
            suffix = value[-1].upper()
            if suffix in multipliers:
                return float(value[:-1]) * multipliers[suffix]
            return float(value)
        except (ValueError, IndexError):
            return 0.0

    def create_sample_breadth_csv(self) -> str:
        """Create a sample breadth CSV template

        Returns:
            Path to created file
        """
        sample_file = self.data_dir / 'breadth_template.csv'
        content = """Metric,Value
NYSE_ADV,1500
NYSE_DEC,1200
NYSE_UNCH,100
NASDAQ_ADV,1800
NASDAQ_DEC,1400
NEW_HIGHS,85
NEW_LOWS,45
MCCLELLAN,35
BREADTH_THRUST,52.5
"""
        with open(sample_file, 'w') as f:
            f.write(content)

        logger.info(f"Created sample template: {sample_file}")
        return str(sample_file)


def convert_tos_breadth_to_market_data(tos_data: TOSBreadthData) -> Dict:
    """Convert TOS breadth data to MarketDataProvider format"""
    from market_data import BreadthData

    # Calculate percentages from A/D ratio
    ad_ratio = tos_data.ad_ratio
    if ad_ratio >= 2.0:
        pct_above_50 = 75.0
        pct_above_200 = 65.0
        status = 'STRONG'
    elif ad_ratio >= 1.5:
        pct_above_50 = 65.0
        pct_above_200 = 55.0
        status = 'STRONG'
    elif ad_ratio >= 1.0:
        pct_above_50 = 55.0
        pct_above_200 = 50.0
        status = 'NEUTRAL'
    elif ad_ratio >= 0.67:
        pct_above_50 = 45.0
        pct_above_200 = 40.0
        status = 'WEAK'
    else:
        pct_above_50 = 35.0
        pct_above_200 = 30.0
        status = 'EXTREME'

    return BreadthData(
        date=tos_data.timestamp,
        pct_above_50dma=pct_above_50,
        pct_above_200dma=pct_above_200,
        advance_decline_ratio=tos_data.ad_ratio,
        new_highs=tos_data.new_highs,
        new_lows=tos_data.new_lows,
        mcclellan_oscillator=tos_data.mcclellan,
        status=status
    )


# Convenience function
def import_tos_breadth(filepath: str = None) -> Optional[TOSBreadthData]:
    """Import breadth data from TOS export"""
    importer = TOSImporter()
    return importer.import_breadth_csv(filepath)


if __name__ == '__main__':
    # Test the importer

    logging.basicConfig(level=logging.INFO)

    importer = TOSImporter()

    # Create sample template
    template = importer.create_sample_breadth_csv()
    print(f"Created template: {template}")

    # Try to import it
    data = importer.import_breadth_csv(template)
    if data:
        print("\nImported data:")
        print(f"  NYSE A/D: {data.nyse_advances}/{data.nyse_declines}")
        print(f"  NASDAQ A/D: {data.nasdaq_advances}/{data.nasdaq_declines}")
        print(f"  A/D Ratio: {data.ad_ratio}")
        print(f"  New Highs/Lows: {data.new_highs}/{data.new_lows}")
        print(f"  McClellan: {data.mcclellan}")
