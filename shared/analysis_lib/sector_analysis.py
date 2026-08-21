"""
Sector Analysis - Sector/Industry Ranking and Rotation Analysis
Version: 1.0.0
Last Updated: 2025-01-01

Sector and industry strength ranking per Blueprint Section 10.1-10.3.

Version 1.0.0 Changes:
- Initial implementation
- Sector RS ranking (1-11)
- Industry identification via FinViz
- Rotation detection
"""

import logging
import time
import re
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from datetime import datetime

import requests
import pandas as pd

# `config` is imported RELATIVELY when this module is loaded as part of the
# package, and by bare name when it is loaded standalone (several consumers put
# shared/analysis_lib directly on sys.path and `import sector_analysis`). The branch is on
# __package__ rather than a try/except so a genuine error inside config.py is NOT
# swallowed into a fallback that would silently bind ANOTHER app's `config`
# module -- exactly the cross-app collision documented in CLAUDE.md.
if __package__:
    from .config import (SECTORS, SECTOR_ETFS, STYLE_SECTOR_RULES)
else:
    from config import (SECTORS, SECTOR_ETFS, STYLE_SECTOR_RULES)

logger = logging.getLogger(__name__)

#############################################
# DATA CLASSES
#############################################

@dataclass
class SectorRanking:
    """Sector ranking result"""
    symbol: str
    name: str
    rank: int
    rs_1w: float  # Relative strength vs SPY (100 = parity)
    rs_1m: float
    rs_3m: float
    rs_6m: float
    composite_rs: float
    pct_1w: float  # Actual 1-week % return
    pct_1m: float  # Actual 1-month % return
    pct_3m: float  # Actual 3-month % return
    pct_above_50dma: float
    cycle_position: str
    trend: str  # RISING, FALLING, NEUTRAL
    rotation_status: str = 'NEUTRAL'  # LEADING, WEAKENING, LAGGING, IMPROVING


@dataclass 
class IndustryInfo:
    """Industry information for a stock"""
    sector_etf: str
    sector_name: str
    industry: str
    industry_code: str
    company_name: str


#############################################
# STOCK-SECTOR MAPPING
#############################################

# Common stock to sector/industry mapping (fallback when FinViz unavailable)
STOCK_SECTOR_MAP = {
    # Technology - Semiconductors
    'NVDA': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'NVIDIA Corporation'),
    'AMD': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Advanced Micro Devices'),
    'AVGO': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Broadcom Inc'),
    'INTC': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Intel Corporation'),
    'MU': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Micron Technology'),
    'QCOM': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Qualcomm Inc'),
    'TSM': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Taiwan Semiconductor'),
    'MRVL': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Marvell Technology'),
    'AMAT': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Applied Materials'),
    'LRCX': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'Lam Research'),
    'KLAC': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'KLA Corporation'),
    'ASML': ('XLK', 'Technology', 'Semiconductors', 'semicon', 'ASML Holding'),
    
    # Technology - Software
    'MSFT': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'Microsoft Corporation'),
    'ORCL': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'Oracle Corporation'),
    'CRM': ('XLK', 'Technology', 'Software - Application', 'softwareapp', 'Salesforce Inc'),
    'ADBE': ('XLK', 'Technology', 'Software - Application', 'softwareapp', 'Adobe Inc'),
    'NOW': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'ServiceNow Inc'),
    'PANW': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'Palo Alto Networks'),
    'PLTR': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'Palantir Technologies'),
    'MDB': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'MongoDB Inc'),
    'SNOW': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'Snowflake Inc'),
    'DDOG': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'Datadog Inc'),
    'NET': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'Cloudflare Inc'),
    'ZS': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'Zscaler Inc'),
    'CRWD': ('XLK', 'Technology', 'Software - Infrastructure', 'softwareinfra', 'CrowdStrike Holdings'),
    'WDAY': ('XLK', 'Technology', 'Software - Application', 'softwareapp', 'Workday Inc'),
    'TEAM': ('XLK', 'Technology', 'Software - Application', 'softwareapp', 'Atlassian Corporation'),
    
    # Technology - Hardware/Internet
    'AAPL': ('XLK', 'Technology', 'Consumer Electronics', 'conselec', 'Apple Inc'),
    'GOOGL': ('XLC', 'Communication Services', 'Internet Content', 'internetcontent', 'Alphabet Inc'),
    'GOOG': ('XLC', 'Communication Services', 'Internet Content', 'internetcontent', 'Alphabet Inc'),
    'META': ('XLC', 'Communication Services', 'Internet Content', 'internetcontent', 'Meta Platforms'),
    'AMZN': ('XLY', 'Consumer Discretionary', 'Internet Retail', 'internetretail', 'Amazon.com Inc'),
    'NFLX': ('XLC', 'Communication Services', 'Entertainment', 'entertainment', 'Netflix Inc'),
    'UBER': ('XLY', 'Consumer Discretionary', 'Software - Application', 'softwareapp', 'Uber Technologies'),
    'ABNB': ('XLY', 'Consumer Discretionary', 'Travel Services', 'travelservices', 'Airbnb Inc'),
    'COIN': ('XLF', 'Financials', 'Capital Markets', 'capitalmarkets', 'Coinbase Global'),
    'SHOP': ('XLY', 'Consumer Discretionary', 'Software - Application', 'softwareapp', 'Shopify Inc'),
    'SQ': ('XLF', 'Financials', 'Software - Infrastructure', 'softwareinfra', 'Block Inc'),
    
    # Financials
    'JPM': ('XLF', 'Financials', 'Banks - Diversified', 'banksdiversified', 'JPMorgan Chase'),
    'BAC': ('XLF', 'Financials', 'Banks - Diversified', 'banksdiversified', 'Bank of America'),
    'WFC': ('XLF', 'Financials', 'Banks - Diversified', 'banksdiversified', 'Wells Fargo'),
    'GS': ('XLF', 'Financials', 'Capital Markets', 'capitalmarkets', 'Goldman Sachs'),
    'MS': ('XLF', 'Financials', 'Capital Markets', 'capitalmarkets', 'Morgan Stanley'),
    'V': ('XLF', 'Financials', 'Credit Services', 'creditservices', 'Visa Inc'),
    'MA': ('XLF', 'Financials', 'Credit Services', 'creditservices', 'Mastercard Inc'),
    'BRK.B': ('XLF', 'Financials', 'Insurance - Diversified', 'insurancediversified', 'Berkshire Hathaway'),
    
    # Healthcare
    'UNH': ('XLV', 'Healthcare', 'Healthcare Plans', 'healthcareplans', 'UnitedHealth Group'),
    'JNJ': ('XLV', 'Healthcare', 'Drug Manufacturers', 'drugmanufacturers', 'Johnson & Johnson'),
    'LLY': ('XLV', 'Healthcare', 'Drug Manufacturers', 'drugmanufacturers', 'Eli Lilly'),
    'PFE': ('XLV', 'Healthcare', 'Drug Manufacturers', 'drugmanufacturers', 'Pfizer Inc'),
    'ABBV': ('XLV', 'Healthcare', 'Drug Manufacturers', 'drugmanufacturers', 'AbbVie Inc'),
    'MRK': ('XLV', 'Healthcare', 'Drug Manufacturers', 'drugmanufacturers', 'Merck & Co'),
    'TMO': ('XLV', 'Healthcare', 'Diagnostics & Research', 'diagnosticsresearch', 'Thermo Fisher'),
    
    # Consumer Discretionary
    'TSLA': ('XLY', 'Consumer Discretionary', 'Auto Manufacturers', 'automanufacturers', 'Tesla Inc'),
    'HD': ('XLY', 'Consumer Discretionary', 'Home Improvement', 'homeimprovement', 'Home Depot'),
    'MCD': ('XLY', 'Consumer Discretionary', 'Restaurants', 'restaurants', 'McDonald\'s Corp'),
    'NKE': ('XLY', 'Consumer Discretionary', 'Footwear & Accessories', 'footwear', 'Nike Inc'),
    'SBUX': ('XLY', 'Consumer Discretionary', 'Restaurants', 'restaurants', 'Starbucks Corp'),
    'LOW': ('XLY', 'Consumer Discretionary', 'Home Improvement', 'homeimprovement', 'Lowe\'s Companies'),
    
    # Consumer Staples
    'PG': ('XLP', 'Consumer Staples', 'Household Products', 'householdproducts', 'Procter & Gamble'),
    'KO': ('XLP', 'Consumer Staples', 'Beverages - Non-Alcoholic', 'beveragesnonalcoholic', 'Coca-Cola Co'),
    'PEP': ('XLP', 'Consumer Staples', 'Beverages - Non-Alcoholic', 'beveragesnonalcoholic', 'PepsiCo Inc'),
    'COST': ('XLP', 'Consumer Staples', 'Discount Stores', 'discountstores', 'Costco Wholesale'),
    'WMT': ('XLP', 'Consumer Staples', 'Discount Stores', 'discountstores', 'Walmart Inc'),
    
    # Energy
    'XOM': ('XLE', 'Energy', 'Oil & Gas Integrated', 'oilgasintegrated', 'Exxon Mobil'),
    'CVX': ('XLE', 'Energy', 'Oil & Gas Integrated', 'oilgasintegrated', 'Chevron Corp'),
    'COP': ('XLE', 'Energy', 'Oil & Gas E&P', 'oilgasep', 'ConocoPhillips'),
    'SLB': ('XLE', 'Energy', 'Oil & Gas Equipment', 'oilgasequipment', 'Schlumberger'),
    'EOG': ('XLE', 'Energy', 'Oil & Gas E&P', 'oilgasep', 'EOG Resources'),
    
    # Industrials
    'CAT': ('XLI', 'Industrials', 'Farm & Heavy Equipment', 'farmheavyequipment', 'Caterpillar Inc'),
    'DE': ('XLI', 'Industrials', 'Farm & Heavy Equipment', 'farmheavyequipment', 'Deere & Company'),
    'BA': ('XLI', 'Industrials', 'Aerospace & Defense', 'aerospacedefense', 'Boeing Co'),
    'HON': ('XLI', 'Industrials', 'Conglomerates', 'conglomerates', 'Honeywell International'),
    'UPS': ('XLI', 'Industrials', 'Integrated Freight', 'integratedfreight', 'United Parcel Service'),
    'RTX': ('XLI', 'Industrials', 'Aerospace & Defense', 'aerospacedefense', 'RTX Corporation'),
    'GE': ('XLI', 'Industrials', 'Aerospace & Defense', 'aerospacedefense', 'GE Aerospace'),
    
    # Materials
    'LIN': ('XLB', 'Materials', 'Specialty Chemicals', 'specialtychemicals', 'Linde PLC'),
    'APD': ('XLB', 'Materials', 'Specialty Chemicals', 'specialtychemicals', 'Air Products'),
    'FCX': ('XLB', 'Materials', 'Copper', 'copper', 'Freeport-McMoRan'),
    'NEM': ('XLB', 'Materials', 'Gold', 'gold', 'Newmont Corporation'),
    
    # Utilities
    'NEE': ('XLU', 'Utilities', 'Utilities - Regulated Electric', 'utilitiesregulated', 'NextEra Energy'),
    'DUK': ('XLU', 'Utilities', 'Utilities - Regulated Electric', 'utilitiesregulated', 'Duke Energy'),
    'SO': ('XLU', 'Utilities', 'Utilities - Regulated Electric', 'utilitiesregulated', 'Southern Company'),
    
    # Real Estate
    'AMT': ('XLRE', 'Real Estate', 'REIT - Specialty', 'reitspecialty', 'American Tower'),
    'PLD': ('XLRE', 'Real Estate', 'REIT - Industrial', 'reitindustrial', 'Prologis Inc'),
    'EQIX': ('XLRE', 'Real Estate', 'REIT - Specialty', 'reitspecialty', 'Equinix Inc'),
    
    # Major Indices/ETFs
    'SPY': ('SPY', 'Index', 'S&P 500 ETF', 'index', 'SPDR S&P 500 ETF'),
    'QQQ': ('QQQ', 'Index', 'Nasdaq 100 ETF', 'index', 'Invesco QQQ Trust'),
    'IWM': ('IWM', 'Index', 'Russell 2000 ETF', 'index', 'iShares Russell 2000'),
    'DIA': ('DIA', 'Index', 'Dow Jones ETF', 'index', 'SPDR Dow Jones'),
}


#############################################
# FINVIZ SCRAPER
#############################################

class FinVizScraper:
    """Scrape sector/industry data from FinViz"""
    
    BASE_URL = "https://finviz.com"
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    @staticmethod
    def get_stock_info(symbol: str) -> Optional[IndustryInfo]:
        """Get sector/industry info for a stock from FinViz
        
        Args:
            symbol: Stock ticker
            
        Returns:
            IndustryInfo or None if not found
        """
        # Check local mapping first
        if symbol.upper() in STOCK_SECTOR_MAP:
            data = STOCK_SECTOR_MAP[symbol.upper()]
            return IndustryInfo(
                sector_etf=data[0],
                sector_name=data[1],
                industry=data[2],
                industry_code=data[3],
                company_name=data[4]
            )
        
        # Try FinViz
        try:
            url = f"{FinVizScraper.BASE_URL}/quote.ashx?t={symbol.upper()}"
            response = requests.get(url, headers=FinVizScraper.HEADERS, timeout=10)
            
            if response.status_code != 200:
                logger.warning(f"FinViz returned {response.status_code} for {symbol}")
                return None
            
            html = response.text
            
            # Parse sector
            sector_match = re.search(r'Sector</td><td[^>]*><a[^>]*>([^<]+)</a>', html)
            sector = sector_match.group(1) if sector_match else 'Unknown'
            
            # Parse industry
            industry_match = re.search(r'Industry</td><td[^>]*><a[^>]*href="[^"]*ind=([^"&]+)[^"]*">([^<]+)</a>', html)
            if industry_match:
                industry_code = industry_match.group(1)
                industry = industry_match.group(2)
            else:
                industry_code = 'unknown'
                industry = 'Unknown'
            
            # Parse company name
            name_match = re.search(r'<title>([^|]+)', html)
            company_name = name_match.group(1).strip() if name_match else symbol
            
            # Map sector to ETF
            sector_etf = FinVizScraper._sector_to_etf(sector)
            
            return IndustryInfo(
                sector_etf=sector_etf,
                sector_name=sector,
                industry=industry,
                industry_code=industry_code,
                company_name=company_name
            )
            
        except Exception as e:
            logger.warning(f"Error fetching FinViz data for {symbol}: {e}")
            return None
    
    @staticmethod
    def _sector_to_etf(sector: str) -> str:
        """Map sector name to ETF symbol"""
        mapping = {
            'Technology': 'XLK',
            'Communication Services': 'XLC',
            'Consumer Cyclical': 'XLY',
            'Consumer Discretionary': 'XLY',
            'Financial': 'XLF',
            'Financial Services': 'XLF',
            'Financials': 'XLF',
            'Healthcare': 'XLV',
            'Industrials': 'XLI',
            'Basic Materials': 'XLB',
            'Materials': 'XLB',
            'Energy': 'XLE',
            'Real Estate': 'XLRE',
            'Consumer Defensive': 'XLP',
            'Consumer Staples': 'XLP',
            'Utilities': 'XLU',
        }
        return mapping.get(sector, 'SPY')
    
    @staticmethod
    def get_industry_stocks(industry_code: str, limit: int = 20) -> List[str]:
        """Get top stocks in an industry from FinViz
        
        Args:
            industry_code: FinViz industry code
            limit: Max stocks to return
            
        Returns:
            List of stock symbols
        """
        try:
            url = f"{FinVizScraper.BASE_URL}/screener.ashx?v=111&f=ind_{industry_code}&o=-marketcap"
            response = requests.get(url, headers=FinVizScraper.HEADERS, timeout=10)
            
            if response.status_code != 200:
                return []
            
            # Parse ticker symbols from screener
            tickers = re.findall(r'<a href="quote\.ashx\?t=([A-Z]+)"', response.text)
            return list(dict.fromkeys(tickers))[:limit]  # Remove duplicates, limit
            
        except Exception as e:
            logger.warning(f"Error fetching industry stocks: {e}")
            return []


#############################################
# SECTOR RANKER
#############################################

class SectorRanker:
    """Rank sectors by relative strength (Blueprint Section 10.1)"""
    
    def __init__(self, market_client):
        """
        Args:
            market_client: MarketDataClient instance for fetching data
        """
        self.client = market_client
        self._rankings: List[SectorRanking] = []
        self._last_update: Optional[datetime] = None
    
    def update_rankings(self, spy_df: pd.DataFrame = None) -> List[SectorRanking]:
        """Update sector rankings
        
        Args:
            spy_df: SPY daily DataFrame for RS calculation (optional, will fetch if None)
            
        Returns:
            List of SectorRanking sorted by composite RS (best first)
        """
        logger.info("Updating sector rankings...")
        
        # Fetch SPY data if not provided
        if spy_df is None:
            spy_df = self.client.get_daily('SPY', months=12)
        
        if spy_df is None or len(spy_df) < 126:
            logger.error("Insufficient SPY data for sector ranking")
            return []
        
        rankings = []
        
        for etf in SECTOR_ETFS:
            try:
                df = self.client.get_daily(etf, months=12)
                time.sleep(0.2)  # Rate limiting
                
                if df is None or len(df) < 126:
                    logger.warning(f"Insufficient data for {etf}")
                    continue
                
                # Calculate period returns
                rs_1w = self._calc_return(df, 5)
                rs_1m = self._calc_return(df, 21)
                rs_3m = self._calc_return(df, 63)
                rs_6m = self._calc_return(df, 126)
                
                # Calculate RS vs SPY
                spy_1w = self._calc_return(spy_df, 5)
                spy_1m = self._calc_return(spy_df, 21)
                spy_3m = self._calc_return(spy_df, 63)
                spy_6m = self._calc_return(spy_df, 126)
                
                # Sector RS vs SPY: parity-preserving growth-factor ratio
                # 100 * (1 + sector) / (1 + spy) (100 == parity). A raw
                # return/return ratio is unstable near spy=0 and SIGN-INVERTS
                # when SPY is negative (a sector that fell less than SPY would
                # wrongly rank weak). Returns are percents here, so /100 first.
                rs_1w_vs = _rs_parity(rs_1w / 100.0, spy_1w / 100.0)
                rs_1m_vs = _rs_parity(rs_1m / 100.0, spy_1m / 100.0)
                rs_3m_vs = _rs_parity(rs_3m / 100.0, spy_3m / 100.0)
                rs_6m_vs = _rs_parity(rs_6m / 100.0, spy_6m / 100.0)
                
                # Composite RS (weighted per Blueprint)
                # 10% 1W, 25% 1M, 35% 3M, 30% 6M
                composite = (
                    rs_1w_vs * 0.10 +
                    rs_1m_vs * 0.25 +
                    rs_3m_vs * 0.35 +
                    rs_6m_vs * 0.30
                )
                
                # Calculate RS momentum for rotation status
                # Momentum = change in RS over last month (1M vs 3M)
                rs_momentum = rs_1m_vs - rs_3m_vs
                rotation_status = determine_rrg_quadrant(composite, rs_momentum)
                
                # Determine trend
                ema_20 = df['close'].ewm(span=20).mean()
                trend = 'RISING' if ema_20.iloc[-1] > ema_20.iloc[-5] else 'FALLING'
                if abs(ema_20.iloc[-1] - ema_20.iloc[-5]) / ema_20.iloc[-5] < 0.01:
                    trend = 'NEUTRAL'
                
                sector_info = SECTORS.get(etf, {'name': etf, 'cycle': 'unknown'})
                
                rankings.append(SectorRanking(
                    symbol=etf,
                    name=sector_info['name'],
                    rank=0,  # Will set after sorting
                    rs_1w=round(rs_1w_vs, 1),
                    rs_1m=round(rs_1m_vs, 1),
                    rs_3m=round(rs_3m_vs, 1),
                    rs_6m=round(rs_6m_vs, 1),
                    composite_rs=round(composite, 1),
                    pct_1w=round(rs_1w, 2),  # Actual % return
                    pct_1m=round(rs_1m, 2),
                    pct_3m=round(rs_3m, 2),
                    pct_above_50dma=0,  # Would need breadth data
                    cycle_position=sector_info['cycle'],
                    trend=trend,
                    rotation_status=rotation_status
                ))
                
            except Exception as e:
                logger.warning(f"Error processing {etf}: {e}")
                continue
        
        # Sort by composite RS and assign ranks
        rankings.sort(key=lambda x: x.composite_rs, reverse=True)
        for i, r in enumerate(rankings):
            r.rank = i + 1
        
        self._rankings = rankings
        self._last_update = datetime.now()
        
        logger.info(f"Sector rankings updated: {len(rankings)} sectors")
        return rankings
    
    def _calc_return(self, df: pd.DataFrame, periods: int) -> float:
        """Calculate percent return over periods"""
        if len(df) <= periods:
            return 0.0
        return (df['close'].iloc[-1] / df['close'].iloc[-periods-1] - 1) * 100
    
    def get_rankings(self) -> List[SectorRanking]:
        """Get current rankings"""
        return self._rankings
    
    def get_sector_rank(self, etf: str) -> int:
        """Get rank for a specific sector ETF (1=best, 11=worst)"""
        for r in self._rankings:
            if r.symbol == etf:
                return r.rank
        return 99  # Unknown
    
    def get_top_sectors(self, n: int = 3) -> List[str]:
        """Get top N sector ETF symbols"""
        return [r.symbol for r in self._rankings[:n]]
    
    def get_bottom_sectors(self, n: int = 3) -> List[str]:
        """Get bottom N sector ETF symbols"""
        return [r.symbol for r in self._rankings[-n:]]
    
    def is_sector_acceptable(self, etf: str, style: str) -> Tuple[bool, str]:
        """Check if sector meets style requirements
        
        Args:
            etf: Sector ETF symbol
            style: Trading style ('momentum', 'swing', 'buy_hold', 'speculative')
            
        Returns:
            Tuple of (is_acceptable, reason)
        """
        rank = self.get_sector_rank(etf)
        rules = STYLE_SECTOR_RULES.get(style, {})
        
        max_rank = rules.get('max_sector_rank')
        avoid_bottom = rules.get('avoid_bottom', 0)
        
        if max_rank is not None and rank > max_rank:
            return False, f"Sector rank {rank} exceeds max {max_rank} for {style}"
        
        if avoid_bottom > 0 and rank > (11 - avoid_bottom):
            return False, f"Sector in bottom {avoid_bottom} (rank {rank}), avoid for {style}"
        
        return True, f"Sector rank {rank} acceptable for {style}"
    
    def format_rankings_table(self) -> str:
        """Format rankings as text table"""
        if not self._rankings:
            return "No sector rankings available. Run update_rankings() first."
        
        lines = [
            "=" * 80,
            "SECTOR RANKINGS (By Relative Strength vs SPY)",
            "=" * 80,
            f"{'Rank':<5} {'ETF':<6} {'Sector':<25} {'1W RS':<8} {'1M RS':<8} {'3M RS':<8} {'Comp':<8} {'Trend':<8}",
            "-" * 80
        ]
        
        for r in self._rankings:
            lines.append(
                f"{r.rank:<5} {r.symbol:<6} {r.name:<25} {r.rs_1w:<8.1f} {r.rs_1m:<8.1f} "
                f"{r.rs_3m:<8.1f} {r.composite_rs:<8.1f} {r.trend:<8}"
            )
        
        lines.append("-" * 80)
        lines.append(f"Top 3: {', '.join(self.get_top_sectors(3))}")
        lines.append(f"Bottom 3: {', '.join(self.get_bottom_sectors(3))}")
        lines.append("=" * 80)
        
        return "\n".join(lines)


#############################################
# SECTOR ANALYSIS FUNCTIONS
#############################################

def get_sector_info(symbol: str) -> Optional[IndustryInfo]:
    """Get sector/industry info for a stock
    
    Args:
        symbol: Stock ticker
        
    Returns:
        IndustryInfo or None
    """
    return FinVizScraper.get_stock_info(symbol)


def calculate_stock_vs_sector_rs(
    stock_df: pd.DataFrame,
    sector_df: pd.DataFrame,
    periods: List[int] = None
) -> Dict[str, float]:
    """Calculate stock's relative strength vs its sector
    
    Args:
        stock_df: Stock daily DataFrame
        sector_df: Sector ETF daily DataFrame
        periods: Lookback periods in days
        
    Returns:
        Dict mapping period label to RS value
    """
    if periods is None:
        periods = [5, 21, 63]  # 1W, 1M, 3M
    
    labels = ['1D', '1W', '1M', '3M', '6M']
    results = {}
    
    if stock_df is None or sector_df is None:
        return {}
    
    for i, period in enumerate(periods):
        label = labels[min(i, len(labels)-1)]
        
        if len(stock_df) <= period or len(sector_df) <= period:
            results[label] = 100.0
            continue
        
        stock_ret = (stock_df['close'].iloc[-1] / stock_df['close'].iloc[-period-1] - 1) * 100
        sector_ret = (sector_df['close'].iloc[-1] / sector_df['close'].iloc[-period-1] - 1) * 100

        # Parity-preserving relative strength: 100 * (1 + stock) / (1 + sector),
        # with returns as fractions (100 == parity, >100 == outperformance).
        # A plain return/return ratio is unstable as the denominator -> 0 and
        # SIGN-INVERTS in down markets (e.g. stock -1% vs sector -2% must read
        # as OUTperformance, not weakness). The growth-factor ratio is stable
        # and sign-correct in both up and down markets.
        results[label] = _rs_parity(stock_ret / 100.0, sector_ret / 100.0)

    return results


def _rs_parity(stock_ret: float, sector_ret: float) -> float:
    """Parity-preserving relative strength from two *fractional* returns.

    Returns ``100 * (1 + stock_ret) / (1 + sector_ret)`` (100 == parity,
    >100 == the stock outperformed). Stable and sign-correct in down markets,
    unlike a raw return/return ratio. Falls back to parity (100.0) only when
    the growth factor ``1 + sector_ret`` is non-positive (a -100%+ benchmark
    move, which can't happen for a real ETF) to avoid a divide-by-zero/sign flip.
    """
    denom = 1.0 + sector_ret
    if denom <= 0:
        return 100.0
    return 100.0 * (1.0 + stock_ret) / denom


def determine_rrg_quadrant(rs: float, rs_momentum: float) -> str:
    """Determine Relative Rotation Graph quadrant
    
    Args:
        rs: Relative strength value (100 = parity)
        rs_momentum: Change in RS (positive = improving)
        
    Returns:
        Quadrant: 'LEADING', 'WEAKENING', 'LAGGING', 'IMPROVING'
    """
    if rs >= 100 and rs_momentum >= 0:
        return 'LEADING'
    elif rs >= 100 and rs_momentum < 0:
        return 'WEAKENING'
    elif rs < 100 and rs_momentum < 0:
        return 'LAGGING'
    else:  # rs < 100 and rs_momentum >= 0
        return 'IMPROVING'
