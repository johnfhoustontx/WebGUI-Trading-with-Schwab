"""
Market Data Provider - Unified data access with multiple sources
Version: 1.0.0

Integrates yfinance, FRED API, FinViz, and other free data sources.
Includes caching for efficient data access and offline mode support.
"""

import logging
import time
import re
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    yf = None

from data_cache import get_cache

# Import FRED API key from config if available
try:
    from config import FRED_API_KEY as CONFIG_FRED_API_KEY
except ImportError:
    CONFIG_FRED_API_KEY = None

logger = logging.getLogger(__name__)

# FRED API configuration
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_API_KEY = CONFIG_FRED_API_KEY  # Loaded from config.py

# FRED series IDs
FRED_SERIES = {
    'DGS2': '2-Year Treasury',
    'DGS10': '10-Year Treasury',
    'DGS30': '30-Year Treasury',
    'DFEDTARU': 'Fed Funds Upper Target',
    'DFEDTARL': 'Fed Funds Lower Target',
    'T10Y2Y': '10Y-2Y Spread',
    'T10Y3M': '10Y-3M Spread',
    'VIXCLS': 'VIX Close',
    'BAMLH0A0HYM2': 'High Yield Spread',
    'ICSA': 'Initial Jobless Claims',
    'UNRATE': 'Unemployment Rate',
}

# S&P 500 sector ETFs
SECTOR_ETFS = ['XLK', 'XLF', 'XLV', 'XLC', 'XLY', 'XLP', 'XLE', 'XLI', 'XLB', 'XLU', 'XLRE']

# Major indices for breadth calculation
INDICES = {
    'SPY': 'S&P 500',
    'QQQ': 'Nasdaq 100',
    'IWM': 'Russell 2000',
    'DIA': 'Dow 30',
}


@dataclass
class YieldCurveData:
    """Treasury yield curve data"""
    date: datetime
    yield_2y: float
    yield_10y: float
    yield_30y: float
    spread_10y_2y: float
    spread_10y_3m: float
    is_inverted: bool
    fed_funds_rate: float
    status: str  # NORMAL, FLAT, INVERTED, RE-STEEPENING


@dataclass
class BreadthData:
    """Market breadth data"""
    date: datetime
    pct_above_50dma: float
    pct_above_200dma: float
    advance_decline_ratio: float
    new_highs: int
    new_lows: int
    mcclellan_oscillator: float
    status: str  # STRONG, NEUTRAL, WEAK, EXTREME


@dataclass
class EarningsInfo:
    """Earnings calendar information"""
    symbol: str
    earnings_date: datetime
    days_until: int
    time_of_day: str  # BMO, AMC, Unknown
    eps_estimate: Optional[float]
    revenue_estimate: Optional[float]


@dataclass
class IndustryRanking:
    """Industry group ranking"""
    industry: str
    sector: str
    performance_1w: float
    performance_1m: float
    performance_3m: float
    avg_volume: float
    top_stocks: List[str]
    rank: int


def set_fred_api_key(api_key: str):
    """Set FRED API key"""
    global FRED_API_KEY
    FRED_API_KEY = api_key


class MarketDataProvider:
    """Unified market data provider with caching"""

    def __init__(self, fred_api_key: str = None, use_cache: bool = True, schwab_client=None):
        """Initialize provider

        Args:
            fred_api_key: FRED API key (get free at https://fred.stlouisfed.org/docs/api/api_key.html)
            use_cache: Enable/disable caching
            schwab_client: Optional Schwab MarketDataClient for breadth data
        """
        self.cache = get_cache() if use_cache else None
        self.fred_api_key = fred_api_key or FRED_API_KEY
        self.schwab_client = schwab_client

        if not YFINANCE_AVAILABLE:
            logger.warning("yfinance not installed. Install with: pip install yfinance")

    # =========================================================================
    # YIELD CURVE DATA (FRED)
    # =========================================================================

    def get_yield_curve(self, use_cache: bool = True) -> Optional[YieldCurveData]:
        """Get current yield curve data from FRED

        Returns:
            YieldCurveData or None if unavailable
        """
        cache_key = 'yield_curve'

        # Check cache
        if use_cache and self.cache:
            cached = self.cache.get('yield_curve', 'current')
            if cached:
                # Convert date string back to datetime
                if isinstance(cached.get('date'), str):
                    cached['date'] = datetime.fromisoformat(cached['date'])
                return YieldCurveData(**cached)

        if not self.fred_api_key:
            logger.warning("FRED API key not set. Using fallback data.")
            return self._get_yield_curve_fallback()

        try:
            # Fetch multiple series
            yields = {}
            for series_id in ['DGS2', 'DGS10', 'DGS30', 'T10Y2Y', 'DFEDTARU']:
                value = self._fetch_fred_series(series_id)
                if value is not None:
                    yields[series_id] = value

            if not yields:
                return self._get_yield_curve_fallback()

            yield_2y = yields.get('DGS2', 4.5)
            yield_10y = yields.get('DGS10', 4.5)
            yield_30y = yields.get('DGS30', 4.7)
            spread = yields.get('T10Y2Y', yield_10y - yield_2y)
            fed_rate = yields.get('DFEDTARU', 5.5)

            # Determine status
            if spread > 0.5:
                status = 'NORMAL'
            elif spread > 0:
                status = 'FLAT'
            elif spread > -0.5:
                status = 'INVERTED'
            else:
                status = 'DEEPLY_INVERTED'

            data = YieldCurveData(
                date=datetime.now(),
                yield_2y=yield_2y,
                yield_10y=yield_10y,
                yield_30y=yield_30y,
                spread_10y_2y=spread,
                spread_10y_3m=0,  # Would need T10Y3M
                is_inverted=spread < 0,
                fed_funds_rate=fed_rate,
                status=status
            )

            # Cache result
            if self.cache:
                cache_data = data.__dict__.copy()
                cache_data['date'] = data.date.isoformat()  # Convert datetime to string
                self.cache.set('yield_curve', 'current', cache_data)

            return data

        except Exception as e:
            logger.error(f"Error fetching yield curve: {e}")
            return self._get_yield_curve_fallback()

    def _fetch_fred_series(self, series_id: str, observations: int = 1) -> Optional[float]:
        """Fetch latest value from FRED series"""
        try:
            params = {
                'series_id': series_id,
                'api_key': self.fred_api_key,
                'file_type': 'json',
                'limit': observations,
                'sort_order': 'desc'
            }
            response = requests.get(FRED_API_URL, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get('observations'):
                value = data['observations'][0]['value']
                if value != '.':
                    return float(value)
        except Exception as e:
            logger.warning(f"Error fetching FRED series {series_id}: {e}")
        return None

    def _get_yield_curve_fallback(self) -> YieldCurveData:
        """Fallback yield curve data when FRED unavailable"""
        return YieldCurveData(
            date=datetime.now(),
            yield_2y=4.5,
            yield_10y=4.5,
            yield_30y=4.7,
            spread_10y_2y=0.0,
            spread_10y_3m=-0.5,
            is_inverted=False,
            fed_funds_rate=5.5,
            status='FLAT'
        )

    # =========================================================================
    # BREADTH DATA (yfinance + calculations)
    # =========================================================================

    def get_breadth_data(self, use_cache: bool = True) -> Optional[BreadthData]:
        """Get market breadth data

        Tries Schwab API first (fast), then falls back to yfinance (slow).

        Returns:
            BreadthData or None if unavailable
        """
        # Check cache first
        if use_cache and self.cache:
            cached = self.cache.get('breadth', 'current')
            if cached:
                try:
                    date_val = datetime.fromisoformat(cached['date']) if isinstance(cached.get('date'), str) else datetime.now()
                    return BreadthData(
                        date=date_val,
                        pct_above_50dma=cached['pct_above_50dma'],
                        pct_above_200dma=cached['pct_above_200dma'],
                        advance_decline_ratio=cached['advance_decline_ratio'],
                        new_highs=cached['new_highs'],
                        new_lows=cached['new_lows'],
                        mcclellan_oscillator=cached['mcclellan_oscillator'],
                        status=cached['status']
                    )
                except Exception as e:
                    logger.warning(f"Cache parse error for breadth: {e}")

        # Try Schwab API first (much faster)
        if self.schwab_client:
            breadth = self._get_breadth_from_schwab()
            if breadth:
                return breadth

        # Fall back to yfinance
        if not YFINANCE_AVAILABLE:
            return self._get_breadth_fallback()

        try:
            # Quick connectivity check - try to fetch SPY first (2s timeout)
            try:
                test_data = yf.download('SPY', period='1d', progress=False, timeout=2)
                if test_data is None or test_data.empty:
                    logger.info("yfinance unavailable, using breadth fallback")
                    return self._get_breadth_fallback()
            except Exception:
                logger.info("yfinance connectivity check failed, using fallback")
                return self._get_breadth_fallback()

            # Get S&P 500 components (using a subset for speed)
            sp500_sample = self._get_sp500_sample()

            above_50 = 0
            above_200 = 0
            advances = 0
            declines = 0
            total = 0

            # Download all stocks in one batch (sample is small enough)
            tickers_str = ' '.join(sp500_sample)

            try:
                # Use shorter timeout to fail fast if yfinance is having issues
                data = yf.download(tickers_str, period='1y', progress=False,
                                   group_by='ticker', threads=True, timeout=15)

                if data is None or data.empty:
                    logger.warning("yfinance returned empty data, using fallback")
                    return self._get_breadth_fallback()

                for symbol in sp500_sample:
                    try:
                        df = data[symbol] if symbol in data.columns.get_level_values(0) else None

                        if df is None or df.empty:
                            continue

                        close = df['Close'].dropna()
                        if len(close) < 200:
                            continue

                        current = close.iloc[-1]
                        prev = close.iloc[-2] if len(close) > 1 else current
                        sma_50 = close.rolling(50).mean().iloc[-1]
                        sma_200 = close.rolling(200).mean().iloc[-1]

                        total += 1
                        if current > sma_50:
                            above_50 += 1
                        if current > sma_200:
                            above_200 += 1
                        if current > prev:
                            advances += 1
                        else:
                            declines += 1

                    except Exception:
                        continue

            except Exception as e:
                logger.warning(f"Error downloading breadth data: {e}")
                return self._get_breadth_fallback()

            if total == 0:
                return self._get_breadth_fallback()

            pct_above_50 = (above_50 / total) * 100
            pct_above_200 = (above_200 / total) * 100
            ad_ratio = advances / max(declines, 1)

            # Determine status
            if pct_above_50 > 70 and pct_above_200 > 60:
                status = 'STRONG'
            elif pct_above_50 > 50 and pct_above_200 > 50:
                status = 'NEUTRAL'
            elif pct_above_50 < 30 or pct_above_200 < 30:
                status = 'EXTREME'
            else:
                status = 'WEAK'

            data = BreadthData(
                date=datetime.now(),
                pct_above_50dma=round(pct_above_50, 1),
                pct_above_200dma=round(pct_above_200, 1),
                advance_decline_ratio=round(ad_ratio, 2),
                new_highs=0,  # Would need separate data source
                new_lows=0,
                mcclellan_oscillator=0,  # Would need A/D line history
                status=status
            )

            # Cache result
            if self.cache:
                cache_dict = data.__dict__.copy()
                cache_dict['date'] = data.date.isoformat()
                self.cache.set('breadth', 'current', cache_dict)

            return data

        except Exception as e:
            logger.error(f"Error calculating breadth: {e}")
            return self._get_breadth_fallback()

    def _get_breadth_from_schwab(self) -> Optional[BreadthData]:
        """Get breadth data from Schwab API (fast)

        Uses $ADVN, $DECN, etc. symbols available through Schwab.
        """
        try:
            # Fetch breadth symbols from Schwab
            breadth_symbols = ['$ADVN', '$DECN', '$ADVN/Q', '$DECN/Q']
            quotes = {}

            for symbol in breadth_symbols:
                quote = self.schwab_client.get_quote(symbol)
                if quote and quote.get('last') is not None:
                    quotes[symbol] = quote.get('last', 0)

            if not quotes.get('$ADVN') or not quotes.get('$DECN'):
                logger.debug("Schwab breadth data incomplete, falling back")
                return None

            # Calculate metrics
            nyse_adv = quotes.get('$ADVN', 0)
            nyse_dec = quotes.get('$DECN', 0)
            nasd_adv = quotes.get('$ADVN/Q', 0)
            nasd_dec = quotes.get('$DECN/Q', 0)

            total_adv = nyse_adv + nasd_adv
            total_dec = nyse_dec + nasd_dec

            # A/D Ratio
            ad_ratio = total_adv / max(total_dec, 1)

            # Estimate % above 50 DMA from A/D ratio
            # A/D ratio of 1.5+ typically means 60%+ above 50 DMA
            # A/D ratio of 0.67 typically means 40% or less above 50 DMA
            if ad_ratio >= 2.0:
                pct_above_50 = 75.0
                pct_above_200 = 65.0
            elif ad_ratio >= 1.5:
                pct_above_50 = 65.0
                pct_above_200 = 55.0
            elif ad_ratio >= 1.0:
                pct_above_50 = 55.0
                pct_above_200 = 50.0
            elif ad_ratio >= 0.67:
                pct_above_50 = 45.0
                pct_above_200 = 40.0
            else:
                pct_above_50 = 35.0
                pct_above_200 = 30.0

            # Determine status
            if ad_ratio >= 1.5 and pct_above_50 > 60:
                status = 'STRONG'
            elif ad_ratio >= 1.0 and pct_above_50 > 50:
                status = 'NEUTRAL'
            elif ad_ratio < 0.5 or pct_above_50 < 30:
                status = 'EXTREME'
            else:
                status = 'WEAK'

            data = BreadthData(
                date=datetime.now(),
                pct_above_50dma=round(pct_above_50, 1),
                pct_above_200dma=round(pct_above_200, 1),
                advance_decline_ratio=round(ad_ratio, 2),
                new_highs=0,  # Not available via Schwab API
                new_lows=0,
                mcclellan_oscillator=0,  # Would need historical data
                status=status
            )

            # Cache the result
            if self.cache:
                cache_dict = data.__dict__.copy()
                cache_dict['date'] = data.date.isoformat()
                self.cache.set('breadth', 'current', cache_dict)

            logger.info(f"Schwab breadth: A/D={ad_ratio:.2f}, Adv={total_adv}, Dec={total_dec}")
            return data

        except Exception as e:
            logger.warning(f"Error getting Schwab breadth: {e}")
            return None

    def _get_sp500_sample(self) -> List[str]:
        """Get sample of S&P 500 stocks for breadth calculation

        Uses a smaller representative sample (33 stocks) for faster calculation.
        """
        # Representative sample across sectors (3 per sector for speed)
        return [
            # Technology (3)
            'AAPL', 'MSFT', 'NVDA',
            # Financials (3)
            'JPM', 'BAC', 'GS',
            # Healthcare (3)
            'UNH', 'JNJ', 'LLY',
            # Consumer Discretionary (3)
            'AMZN', 'TSLA', 'HD',
            # Consumer Staples (3)
            'PG', 'KO', 'COST',
            # Industrials (3)
            'CAT', 'HON', 'UPS',
            # Energy (3)
            'XOM', 'CVX', 'COP',
            # Materials (3)
            'LIN', 'APD', 'FCX',
            # Utilities (3)
            'NEE', 'DUK', 'SO',
            # Real Estate (3)
            'AMT', 'PLD', 'CCI',
            # Communication (3)
            'GOOGL', 'META', 'NFLX',
        ]

    def _get_breadth_fallback(self, cache_result: bool = True) -> BreadthData:
        """Fallback breadth data

        Args:
            cache_result: If True, cache the fallback for 1 hour to avoid repeated slow attempts
        """
        data = BreadthData(
            date=datetime.now(),
            pct_above_50dma=55.0,
            pct_above_200dma=50.0,
            advance_decline_ratio=1.0,
            new_highs=0,
            new_lows=0,
            mcclellan_oscillator=0,
            status='NEUTRAL'
        )

        # Cache fallback for 1 hour to avoid repeated slow API attempts
        if cache_result and self.cache:
            cache_dict = data.__dict__.copy()
            cache_dict['date'] = data.date.isoformat()
            self.cache.set('breadth', 'current', cache_dict, ttl_minutes=60)

        return data

    # =========================================================================
    # EARNINGS CALENDAR (yfinance)
    # =========================================================================

    def get_earnings_calendar(self, symbols: List[str], days_ahead: int = 14) -> List[EarningsInfo]:
        """Get upcoming earnings dates for symbols

        Args:
            symbols: List of stock symbols
            days_ahead: Number of days to look ahead

        Returns:
            List of EarningsInfo sorted by date
        """
        if not YFINANCE_AVAILABLE:
            return []

        earnings = []
        cutoff_date = datetime.now() + timedelta(days=days_ahead)

        for symbol in symbols:
            # Check cache
            if self.cache:
                cached = self.cache.get('earnings', symbol)
                if cached:
                    try:
                        info = EarningsInfo(**{k: v for k, v in cached.items() if k != 'earnings_date'},
                                           earnings_date=datetime.fromisoformat(cached['earnings_date']))
                        if info.earnings_date <= cutoff_date:
                            earnings.append(info)
                        continue
                    except Exception:
                        pass

            try:
                ticker = yf.Ticker(symbol)
                calendar = ticker.calendar

                if calendar is not None and not calendar.empty:
                    # Handle different calendar formats
                    if isinstance(calendar, pd.DataFrame):
                        if 'Earnings Date' in calendar.index:
                            earnings_date = calendar.loc['Earnings Date'].iloc[0]
                        elif len(calendar.columns) > 0:
                            earnings_date = calendar.iloc[0, 0]
                        else:
                            continue
                    else:
                        continue

                    if pd.isna(earnings_date):
                        continue

                    if isinstance(earnings_date, str):
                        earnings_date = pd.to_datetime(earnings_date)

                    if hasattr(earnings_date, 'to_pydatetime'):
                        earnings_date = earnings_date.to_pydatetime()

                    if earnings_date.tzinfo:
                        earnings_date = earnings_date.replace(tzinfo=None)

                    days_until = (earnings_date - datetime.now()).days

                    if 0 <= days_until <= days_ahead:
                        info = EarningsInfo(
                            symbol=symbol,
                            earnings_date=earnings_date,
                            days_until=days_until,
                            time_of_day='Unknown',
                            eps_estimate=None,
                            revenue_estimate=None
                        )
                        earnings.append(info)

                        # Cache it
                        if self.cache:
                            cache_dict = info.__dict__.copy()
                            cache_dict['earnings_date'] = info.earnings_date.isoformat()
                            self.cache.set('earnings', symbol, cache_dict)

            except Exception as e:
                logger.debug(f"Error getting earnings for {symbol}: {e}")
                continue

            time.sleep(0.1)  # Rate limiting

        # Sort by date
        earnings.sort(key=lambda x: x.earnings_date)
        return earnings

    # =========================================================================
    # INDUSTRY RANKINGS (FinViz)
    # =========================================================================

    def get_industry_rankings(self, use_cache: bool = True) -> List[IndustryRanking]:
        """Get industry group rankings from FinViz

        Returns:
            List of IndustryRanking sorted by performance
        """
        # Check cache
        if use_cache and self.cache:
            cached = self.cache.get('industry', 'rankings')
            if cached:
                return [IndustryRanking(**r) for r in cached]

        try:
            url = "https://finviz.com/groups.ashx?g=industry&v=140&o=-perf1w"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()

            rankings = self._parse_finviz_industries(response.text)

            # Cache result
            if self.cache and rankings:
                self.cache.set('industry', 'rankings', [r.__dict__ for r in rankings])

            return rankings

        except Exception as e:
            logger.error(f"Error fetching industry rankings: {e}")
            return []

    def _parse_finviz_industries(self, html: str) -> List[IndustryRanking]:
        """Parse FinViz industry performance table"""
        rankings = []

        try:
            # Find performance table rows
            # Pattern: industry name, 1D, 1W, 1M, 3M, 6M, 1Y, etc.
            table_pattern = r'<td[^>]*class="tab-link"[^>]*><a[^>]*>([^<]+)</a></td>'
            perf_pattern = r'<td[^>]*class="snapshot-td2[^"]*"[^>]*>([^<]+)</td>'

            # Simple extraction - in production use BeautifulSoup
            industries = re.findall(table_pattern, html)
            performances = re.findall(perf_pattern, html)

            # Each industry has multiple performance values
            values_per_row = 7  # Approx columns

            for i, industry in enumerate(industries[:50]):  # Top 50
                try:
                    start_idx = i * values_per_row
                    if start_idx + 3 <= len(performances):
                        perf_1w = self._parse_percent(performances[start_idx + 1]) if start_idx + 1 < len(performances) else 0
                        perf_1m = self._parse_percent(performances[start_idx + 2]) if start_idx + 2 < len(performances) else 0
                        perf_3m = self._parse_percent(performances[start_idx + 3]) if start_idx + 3 < len(performances) else 0

                        rankings.append(IndustryRanking(
                            industry=industry.strip(),
                            sector=self._industry_to_sector(industry),
                            performance_1w=perf_1w,
                            performance_1m=perf_1m,
                            performance_3m=perf_3m,
                            avg_volume=0,
                            top_stocks=[],
                            rank=i + 1
                        ))
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"Error parsing FinViz industries: {e}")

        return rankings

    def _parse_percent(self, value: str) -> float:
        """Parse percentage string to float"""
        try:
            return float(value.strip().replace('%', '').replace(',', ''))
        except Exception:
            return 0.0

    def _industry_to_sector(self, industry: str) -> str:
        """Map industry to sector"""
        industry_lower = industry.lower()

        sector_keywords = {
            'XLK': ['software', 'semiconductor', 'computer', 'electronic', 'internet', 'tech'],
            'XLF': ['bank', 'insurance', 'asset', 'capital', 'financial', 'credit'],
            'XLV': ['biotech', 'pharma', 'health', 'medical', 'drug', 'diagnostic'],
            'XLY': ['retail', 'restaurant', 'auto', 'travel', 'leisure', 'apparel'],
            'XLP': ['food', 'beverage', 'household', 'tobacco', 'consumer'],
            'XLE': ['oil', 'gas', 'energy', 'coal', 'solar', 'uranium'],
            'XLI': ['aerospace', 'defense', 'machinery', 'industrial', 'transport'],
            'XLB': ['chemical', 'metal', 'mining', 'steel', 'gold', 'material'],
            'XLU': ['utility', 'electric', 'water', 'gas utility'],
            'XLRE': ['reit', 'real estate', 'property'],
            'XLC': ['media', 'entertainment', 'telecom', 'communication'],
        }

        for sector, keywords in sector_keywords.items():
            if any(kw in industry_lower for kw in keywords):
                return sector

        return 'OTHER'

    # =========================================================================
    # MULTI-TIMEFRAME DATA (yfinance)
    # =========================================================================

    def get_multi_timeframe_data(self, symbol: str, use_cache: bool = True) -> Dict[str, pd.DataFrame]:
        """Get daily, weekly, and monthly data for a symbol

        Args:
            symbol: Stock symbol

        Returns:
            Dict with 'daily', 'weekly', 'monthly' DataFrames
        """
        if not YFINANCE_AVAILABLE:
            return {}

        result = {}

        for timeframe, period in [('daily', '2y'), ('weekly', '5y'), ('monthly', '10y')]:
            # Check cache
            cache_key = f"{symbol}_{timeframe}"
            if use_cache and self.cache:
                cached = self.cache.get(timeframe, symbol)
                if cached:
                    result[timeframe] = pd.DataFrame(cached)
                    continue

            try:
                ticker = yf.Ticker(symbol)

                if timeframe == 'daily':
                    df = ticker.history(period=period, interval='1d')
                elif timeframe == 'weekly':
                    df = ticker.history(period=period, interval='1wk')
                else:
                    df = ticker.history(period=period, interval='1mo')

                if df is not None and not df.empty:
                    # Standardize column names
                    df.columns = [c.lower() for c in df.columns]
                    df = df.reset_index()
                    df.columns = [c.lower().replace(' ', '_') for c in df.columns]

                    result[timeframe] = df

                    # Cache it
                    if self.cache:
                        self.cache.set(timeframe, symbol, df.to_dict('records'))

            except Exception as e:
                logger.error(f"Error getting {timeframe} data for {symbol}: {e}")
                continue

            time.sleep(0.2)  # Rate limiting

        return result

    # =========================================================================
    # STOCK QUOTE (yfinance with caching)
    # =========================================================================

    def get_quote(self, symbol: str, use_cache: bool = True) -> Optional[Dict]:
        """Get current quote for a symbol (Schwab API primary, yfinance backup)

        Args:
            symbol: Stock symbol
            use_cache: Use cached data if available

        Returns:
            Dict with quote data or None
        """
        # Check cache
        if use_cache and self.cache:
            cached = self.cache.get('quote', symbol)
            if cached:
                logger.debug(f"Returning cached quote for {symbol}")
                return cached

        # Try Schwab API first (primary source)
        if self.schwab_client:
            try:
                logger.debug(f"Fetching quote for {symbol} from Schwab API...")
                schwab_quote = self.schwab_client.get_quote(symbol)
                
                if schwab_quote:
                    # Schwab client already returns standardized format
                    # Just use it directly
                    price = schwab_quote.get('last', 0)
                    
                    # If quote price is $0, try to get from historical data
                    if price == 0:
                        logger.warning(f"Quote price is $0 for {symbol}, trying historical data...")
                        df = self.get_daily(symbol, months=1, use_cache=True)
                        if df is not None and not df.empty:
                            price = df['close'].iloc[-1] if 'close' in df.columns else 0
                            logger.info(f"Using last close price for {symbol}: ${price:.2f}")
                    
                    quote = {
                        'symbol': symbol,
                        'last': float(price),
                        'change': 0,  # Schwab quote doesn't have change calc'd
                        'change_pct': 0,
                        'volume': schwab_quote.get('volume', 0),
                        'high': schwab_quote.get('high', 0),
                        'low': schwab_quote.get('low', 0),
                        'open': schwab_quote.get('open', 0),
                        'prev_close': schwab_quote.get('close', 0),
                        'bid': schwab_quote.get('bid', 0),
                        'ask': schwab_quote.get('ask', 0),
                        '52w_high': schwab_quote.get('52_week_high', 0),
                        '52w_low': schwab_quote.get('52_week_low', 0),
                    }
                    
                    logger.info(f"Got quote for {symbol} from Schwab API: ${quote['last']:.2f}")
                    
                    # Cache it
                    if self.cache:
                        self.cache.set('quote', symbol, quote)
                    
                    return quote
                else:
                    logger.warning(f"Schwab API returned no quote for {symbol}, trying yfinance...")
            except Exception as e:
                logger.warning(f"Schwab API error for {symbol}: {e}, trying yfinance...")

        # Fall back to yfinance if Schwab unavailable or failed
        if not YFINANCE_AVAILABLE:
            logger.error(f"Cannot get quote for {symbol}: Schwab API failed and yfinance not available")
            return None

        try:
            logger.debug(f"Fetching quote for {symbol} from yfinance (fallback)...")
            ticker = yf.Ticker(symbol)
            info = ticker.info

            quote = {
                'symbol': symbol,
                'last': info.get('currentPrice') or info.get('regularMarketPrice', 0),
                'change': info.get('regularMarketChange', 0),
                'change_pct': info.get('regularMarketChangePercent', 0),
                'volume': info.get('regularMarketVolume', 0),
                'avg_volume': info.get('averageVolume', 0),
                'high': info.get('dayHigh', 0),
                'low': info.get('dayLow', 0),
                'open': info.get('regularMarketOpen', 0),
                'prev_close': info.get('previousClose', 0),
                'market_cap': info.get('marketCap', 0),
                'pe_ratio': info.get('trailingPE', 0),
                'forward_pe': info.get('forwardPE', 0),
                '52w_high': info.get('fiftyTwoWeekHigh', 0),
                '52w_low': info.get('fiftyTwoWeekLow', 0),
            }
            
            logger.info(f"Got quote for {symbol} from yfinance: ${quote['last']:.2f}")

            # Cache it
            if self.cache:
                self.cache.set('quote', symbol, quote)

            return quote

        except Exception as e:
            logger.error(f"Error getting quote for {symbol} from yfinance: {e}")
            return None

    def get_daily(self, symbol: str, months: int = 12, use_cache: bool = True) -> Optional[pd.DataFrame]:
        """Get daily price data (Schwab API primary, yfinance backup)

        Args:
            symbol: Stock symbol
            months: Number of months of history
            use_cache: Use cached data if available

        Returns:
            DataFrame with OHLCV data
        """
        cache_key = f"{symbol}_{months}m"

        # Check cache first
        if use_cache and self.cache:
            cached = self.cache.get('daily', cache_key)
            if cached:
                df = pd.DataFrame(cached)
                if 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date'])
                logger.debug(f"Returning cached data for {symbol}")
                return df

        # Try Schwab API first (primary source)
        if self.schwab_client:
            try:
                logger.debug(f"Fetching {symbol} from Schwab API...")
                df = self.schwab_client.get_daily_history(symbol, months)
                
                if df is not None and not df.empty:
                    logger.info(f"Got {len(df)} bars for {symbol} from Schwab API")
                    
                    # Standardize column names
                    df.columns = [c.lower() for c in df.columns]
                    if 'date' in df.columns:
                        df['datetime'] = df['date']
                    elif 'datetime' in df.columns:
                        df['date'] = df['datetime']
                    
                    # Cache it
                    if self.cache:
                        cache_df = df.copy()
                        for col in cache_df.columns:
                            if cache_df[col].dtype == 'datetime64[ns]':
                                cache_df[col] = cache_df[col].astype(str)
                        self.cache.set('daily', cache_key, cache_df.to_dict('records'))
                    
                    return df
                else:
                    logger.warning(f"Schwab API returned no data for {symbol}, trying yfinance...")
            except Exception as e:
                logger.warning(f"Schwab API error for {symbol}: {e}, trying yfinance...")
        
        # Fall back to yfinance if Schwab unavailable or failed
        if not YFINANCE_AVAILABLE:
            logger.error(f"Cannot fetch {symbol}: Schwab API failed and yfinance not available")
            return None

        try:
            logger.debug(f"Fetching {symbol} from yfinance (fallback)...")
            ticker = yf.Ticker(symbol)
            period = f"{months}mo" if months <= 24 else f"{months // 12}y"
            df = ticker.history(period=period, interval='1d')

            if df is not None and not df.empty:
                logger.info(f"Got {len(df)} bars for {symbol} from yfinance")
                df.columns = [c.lower() for c in df.columns]
                df = df.reset_index()
                df.columns = [c.lower().replace(' ', '_') for c in df.columns]

                if 'date' in df.columns:
                    df['datetime'] = df['date']
                elif 'datetime' not in df.columns and df.index.name:
                    df['datetime'] = df.index

                # Cache it
                if self.cache:
                    cache_df = df.copy()
                    for col in cache_df.columns:
                        if cache_df[col].dtype == 'datetime64[ns]':
                            cache_df[col] = cache_df[col].astype(str)
                    self.cache.set('daily', cache_key, cache_df.to_dict('records'))

                return df
            else:
                logger.error(f"yfinance returned no data for {symbol}")
                return None

        except Exception as e:
            logger.error(f"Error getting daily data for {symbol} from yfinance: {e}")
            return None

    # =========================================================================
    # PREFETCH UTILITIES
    # =========================================================================

    def prefetch_market_data(self, symbols: List[str] = None, callback=None):
        """Prefetch and cache market data for faster access

        Args:
            symbols: List of symbols to prefetch (uses defaults if None)
            callback: Optional callback function(progress, message)
        """
        if symbols is None:
            symbols = list(INDICES.keys()) + SECTOR_ETFS + self._get_sp500_sample()[:50]

        total = len(symbols) + 3  # +3 for yield, breadth, industries
        completed = 0

        def update_progress(msg):
            nonlocal completed
            completed += 1
            if callback:
                callback(completed / total, msg)

        # Fetch yield curve
        update_progress("Fetching yield curve...")
        self.get_yield_curve(use_cache=False)

        # Fetch breadth
        update_progress("Calculating breadth...")
        self.get_breadth_data(use_cache=False)

        # Fetch industry rankings
        update_progress("Fetching industry rankings...")
        self.get_industry_rankings(use_cache=False)

        # Fetch symbols in parallel
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(self.get_daily, sym, 12, False): sym for sym in symbols}

            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    future.result()
                    update_progress(f"Fetched {symbol}")
                except Exception as e:
                    update_progress(f"Error fetching {symbol}: {e}")

        logger.info(f"Prefetch complete: {completed} items cached")


# Convenience function
def get_provider(fred_api_key: str = None) -> MarketDataProvider:
    """Get a MarketDataProvider instance"""
    return MarketDataProvider(fred_api_key=fred_api_key)
