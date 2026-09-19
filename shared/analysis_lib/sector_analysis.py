"""
Sector Analysis - stock -> sector classification and stock-vs-sector strength.

What survives of the Blueprint sector module: ``get_sector_info`` (local map,
then FinViz) and ``calculate_stock_vs_sector_rs``, both consumed by
portfolio-analyzer. The SectorRanker (sector RS ranking 1-11 + rotation
quadrants) had no production caller and was removed 2026-09-19.
"""

import logging
import re
from typing import Optional, Dict, List
from dataclasses import dataclass

import requests
import pandas as pd

logger = logging.getLogger(__name__)

#############################################
# DATA CLASSES
#############################################


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


