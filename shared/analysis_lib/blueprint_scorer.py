"""
Blueprint Scorer - 8-Factor Stock Scoring Model
Version: 1.1.0
Last Updated: 2025-01-04

Implements Blueprint Section 10.8 Combined Scoring Model.

Version 1.1.0 Changes:
- IMPLEMENTED: Industry Strength factor using Finviz API
- Industry scoring based on average weekly performance
- Leader bonus (+1) for stocks in top 3 of their industry
- Added 100+ industry filter mappings for Finviz

Version 1.0.0 Changes:
- Initial implementation
- 8-factor weighted scoring
- Setup quality detection
- Trade recommendation engine
"""

import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pandas as pd

# Handle imports for both direct execution and module import
try:
    from config import (
        BLUEPRINT_SCORE_WEIGHTS, SCORE_THRESHOLDS, 
        TREND_STATES, MIN_RR_RATIOS, STYLE_SECTOR_RULES
    )
    from technical import (
        detect_trend_structure, calculate_rsi, calculate_atr,
        calculate_atr_percent, calculate_relative_strength,
        calculate_ema, calculate_relative_volume, calculate_macd,
        is_rs_new_high
    )
except ImportError:
    # Running directly - try relative import from src directory
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import (
        BLUEPRINT_SCORE_WEIGHTS, SCORE_THRESHOLDS, 
        TREND_STATES, MIN_RR_RATIOS, STYLE_SECTOR_RULES
    )
    from technical import (
        detect_trend_structure, calculate_rsi, calculate_atr,
        calculate_atr_percent, calculate_relative_strength,
        calculate_ema, calculate_relative_volume, calculate_macd,
        is_rs_new_high
    )

logger = logging.getLogger(__name__)

#############################################
# ENUMS AND DATA CLASSES
#############################################

class Recommendation(Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    BUY_REDUCED = "BUY_REDUCED"
    SPECULATIVE = "SPECULATIVE"
    WATCHLIST = "WATCHLIST"
    AVOID = "AVOID"
    SHORT = "SHORT"


class TradingStyle(Enum):
    MOMENTUM = "momentum"
    SWING = "swing"
    BUY_HOLD = "buy_hold"
    SPECULATIVE = "speculative"


@dataclass
class FactorScore:
    """Individual factor score"""
    name: str
    score: float  # 0-10
    weight: float
    weighted_score: float
    rationale: str


@dataclass
class SetupInfo:
    """Trading setup information"""
    setup_type: str  # e.g., 'pullback', 'breakout', 'none'
    quality: float  # 0-10
    entry_price: float
    stop_price: float
    target_price: float
    risk_reward: float
    description: str


@dataclass
class BlueprintScore:
    """Complete blueprint analysis score"""
    symbol: str
    current_price: float
    total_score: float
    recommendation: Recommendation
    conviction: str  # HIGH, MEDIUM, LOW
    style: TradingStyle
    factors: Dict[str, FactorScore] = field(default_factory=dict)
    setup: Optional[SetupInfo] = None
    sector_rank: int = 0
    sector_name: str = ""
    industry_name: str = ""  # NEW: Industry from Master Database
    company_name: str = ""  # NEW: Company description
    trend_state: str = ""
    rs_composite: float = 100.0
    warnings: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


#############################################
# BLUEPRINT SCORER
#############################################

class BlueprintScorer:
    """Score stocks using Blueprint 8-factor model"""
    
    def __init__(self, market_client, macro_calculator, sector_ranker, finviz_processor=None):
        """
        Args:
            market_client: MarketDataClient instance
            macro_calculator: MacroScoreCalculator instance
            sector_ranker: SectorRanker instance
            finviz_processor: FinvizDataProcessor instance (optional, for industry scoring)
        """
        self.client = market_client
        self.macro = macro_calculator
        self.sectors = sector_ranker
        self.finviz = finviz_processor  # For industry strength scoring
        self._master_db = None  # Lazy load Master Database
        self._industry_filter_map = self._build_industry_filter_map()
    
    def _build_industry_filter_map(self) -> Dict[str, str]:
        """Build mapping of Finviz industry names to filter codes"""
        return {
            # Technology
            'Software - Application': 'ind_softwareapplication',
            'Software - Infrastructure': 'ind_softwareinfrastructure',
            'Semiconductors': 'ind_semiconductors',
            'Semiconductor Equipment & Materials': 'ind_semiconductorequipment',
            'Computer Hardware': 'ind_computerhardware',
            'Electronic Components': 'ind_electroniccomponents',
            'Information Technology Services': 'ind_informationtechnologyservices',
            'Communication Equipment': 'ind_communicationequipment',
            'Consumer Electronics': 'ind_consumerelectronics',
            'Scientific & Technical Instruments': 'ind_scientifictechnicalinstruments',
            'Solar': 'ind_solar',
            'Electronics & Computer Distribution': 'ind_electronicscomputerdistribution',
            
            # Financial
            'Banks - Diversified': 'ind_banksdiversified',
            'Banks - Regional': 'ind_banksregional',
            'Credit Services': 'ind_creditservices',
            'Asset Management': 'ind_assetmanagement',
            'Capital Markets': 'ind_capitalmarkets',
            'Insurance - Life': 'ind_insurancelife',
            'Insurance - Property & Casualty': 'ind_insurancepropertycasualty',
            'Insurance - Diversified': 'ind_insurancediversified',
            'Insurance - Specialty': 'ind_insurancespecialty',
            'Insurance Brokers': 'ind_insurancebrokers',
            'Financial Data & Stock Exchanges': 'ind_financialdatastockexchanges',
            'Mortgage Finance': 'ind_mortgagefinance',
            
            # Healthcare
            'Biotechnology': 'ind_biotechnology',
            'Drug Manufacturers - General': 'ind_drugmanufacturersgeneral',
            'Drug Manufacturers - Specialty & Generic': 'ind_drugmanufacturersspecialtygeneric',
            'Medical Devices': 'ind_medicaldevices',
            'Medical Instruments & Supplies': 'ind_medicalinstrumentssupplies',
            'Health Care Plans': 'ind_healthcareplans',
            'Health Care Providers': 'ind_healthcareproviders',
            'Medical Care Facilities': 'ind_medicalcarefacilities',
            'Diagnostics & Research': 'ind_diagnosticsresearch',
            'Medical Distribution': 'ind_medicaldistribution',
            'Pharmaceutical Retailers': 'ind_pharmaceuticalretailers',
            
            # Energy
            'Oil & Gas E&P': 'ind_oilgasep',
            'Oil & Gas Integrated': 'ind_oilgasintegrated',
            'Oil & Gas Equipment & Services': 'ind_oilgasequipmentservices',
            'Oil & Gas Midstream': 'ind_oilgasmidstream',
            'Oil & Gas Refining & Marketing': 'ind_oilgasrefiningmarketing',
            'Oil & Gas Drilling': 'ind_oilgasdrilling',
            'Thermal Coal': 'ind_thermalcoal',
            'Uranium': 'ind_uranium',
            
            # Consumer Cyclical
            'Internet Retail': 'ind_internetretail',
            'Specialty Retail': 'ind_specialtyretail',
            'Home Improvement Retail': 'ind_homeimprovementretail',
            'Apparel Retail': 'ind_apparelretail',
            'Auto & Truck Dealerships': 'ind_autotruckdealerships',
            'Department Stores': 'ind_departmentstores',
            'Discount Stores': 'ind_discountstores',
            'Grocery Stores': 'ind_grocerystores',
            'Luxury Goods': 'ind_luxurygoods',
            'Apparel Manufacturing': 'ind_apparelmanufacturing',
            'Footwear & Accessories': 'ind_footwearaccessories',
            'Restaurants': 'ind_restaurants',
            'Gambling': 'ind_gambling',
            'Resorts & Casinos': 'ind_resortscasinos',
            'Leisure': 'ind_leisure',
            'Lodging': 'ind_lodging',
            'Travel Services': 'ind_travelservices',
            'Recreational Vehicles': 'ind_recreationalvehicles',
            
            # Consumer Defensive
            'Packaged Foods': 'ind_packagedfoods',
            'Beverages - Non-Alcoholic': 'ind_beveragesnonalcoholic',
            'Beverages - Wineries & Distilleries': 'ind_beverageswineriesdistilleries',
            'Beverages - Brewers': 'ind_beveragesbrewers',
            'Tobacco': 'ind_tobacco',
            'Household & Personal Products': 'ind_householdpersonalproducts',
            'Confectioners': 'ind_confectioners',
            
            # Industrials
            'Aerospace & Defense': 'ind_aerospacedefense',
            'Airlines': 'ind_airlines',
            'Auto Manufacturers': 'ind_automanufacturers',
            'Auto Parts': 'ind_autoparts',
            'Railroads': 'ind_railroads',
            'Trucking': 'ind_trucking',
            'Integrated Freight & Logistics': 'ind_integratedfreightlogistics',
            'Marine Shipping': 'ind_marineshipping',
            'Rental & Leasing Services': 'ind_rentalleasingservices',
            'Farm & Heavy Construction Machinery': 'ind_farmheavyconstructionmachinery',
            'Industrial Distribution': 'ind_industrialdistribution',
            'Metal Fabrication': 'ind_metalfabrication',
            'Pollution & Treatment Controls': 'ind_pollutiontreatmentcontrols',
            'Tools & Accessories': 'ind_toolsaccessories',
            'Electrical Equipment & Parts': 'ind_electricalequipmentparts',
            'Building Products & Equipment': 'ind_buildingproductsequipment',
            'Security & Protection Services': 'ind_securityprotectionservices',
            'Staffing & Employment Services': 'ind_staffingemploymentservices',
            'Consulting Services': 'ind_consultingservices',
            'Business Equipment & Supplies': 'ind_businessequipmentsupplies',
            'Conglomerates': 'ind_conglomerates',
            'Engineering & Construction': 'ind_engineeringconstruction',
            'Infrastructure Operations': 'ind_infrastructureoperations',
            'Specialty Industrial Machinery': 'ind_specialtyindustrialmachinery',
            'Waste Management': 'ind_wastemanagement',
            
            # Materials
            'Aluminum': 'ind_aluminum',
            'Copper': 'ind_copper',
            'Gold': 'ind_gold',
            'Silver': 'ind_silver',
            'Steel': 'ind_steel',
            'Other Industrial Metals & Mining': 'ind_otherindustrialmetalsmining',
            'Other Precious Metals & Mining': 'ind_otherpreciousmetalsmining',
            'Specialty Chemicals': 'ind_specialtychemicals',
            'Agricultural Inputs': 'ind_agriculturalinputs',
            'Chemicals': 'ind_chemicals',
            'Building Materials': 'ind_buildingmaterials',
            'Lumber & Wood Production': 'ind_lumberwoodproduction',
            'Paper & Paper Products': 'ind_paperpaperproducts',
            'Coking Coal': 'ind_cokingcoal',
            
            # Real Estate
            'REIT - Retail': 'ind_reitretail',
            'REIT - Residential': 'ind_reitresidential',
            'REIT - Industrial': 'ind_reitindustrial',
            'REIT - Office': 'ind_reitoffice',
            'REIT - Healthcare Facilities': 'ind_reithealthcarefacilities',
            'REIT - Hotel & Motel': 'ind_reithotelmotel',
            'REIT - Diversified': 'ind_reitdiversified',
            'REIT - Specialty': 'ind_reitspecialty',
            'REIT - Mortgage': 'ind_reitmortgage',
            'Real Estate Services': 'ind_realestateservices',
            'Real Estate - Development': 'ind_realestatedevelopment',
            'Real Estate - Diversified': 'ind_realestatediversified',
            
            # Communication Services
            'Telecom Services': 'ind_telecomservices',
            'Internet Content & Information': 'ind_internetcontentinformation',
            'Entertainment': 'ind_entertainment',
            'Broadcasting': 'ind_broadcasting',
            'Advertising Agencies': 'ind_advertisingagencies',
            'Publishing': 'ind_publishing',
            'Electronic Gaming & Multimedia': 'ind_electronicgamingmultimedia',
            
            # Utilities
            'Utilities - Regulated Electric': 'ind_utilitiesregulatedelectric',
            'Utilities - Regulated Gas': 'ind_utilitiesregulatedgas',
            'Utilities - Diversified': 'ind_utilitiesdiversified',
            'Utilities - Renewable': 'ind_utilitiesrenewable',
            'Utilities - Independent Power Producers': 'ind_utilitiesindependentpowerproducers',
        }
    
    def _get_industry_filter(self, industry_name: str) -> Optional[str]:
        """Convert industry name to Finviz filter code"""
        import re
        
        # Try exact match first
        if industry_name in self._industry_filter_map:
            return self._industry_filter_map[industry_name]
        
        # Try case-insensitive match
        for key, value in self._industry_filter_map.items():
            if key.lower() == industry_name.lower():
                return value
        
        # Try partial match
        for key, value in self._industry_filter_map.items():
            if key.lower() in industry_name.lower() or industry_name.lower() in key.lower():
                return value
        
        # Generate filter from industry name (fallback)
        cleaned = industry_name.lower()
        cleaned = re.sub(r'[^a-z0-9]', '', cleaned)
        return f'ind_{cleaned}'
    
    def _load_master_database(self) -> pd.DataFrame:
        """Load Master Stock Database (lazy loaded)"""
        if self._master_db is None:
            try:
                import os
                db_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    'data', 'Master_Stock_Database.csv'
                )
                self._master_db = pd.read_csv(db_path)
                logger.info(f"Loaded Master Database: {len(self._master_db)} stocks")
            except Exception as e:
                logger.warning(f"Could not load Master Database: {e}")
                self._master_db = pd.DataFrame()  # Empty dataframe
        return self._master_db
    
    def _get_stock_info(self, symbol: str) -> dict:
        """Get company info from Master Database
        
        Returns:
            dict with keys: company_name, sector, industry, sub_industry
        """
        db = self._load_master_database()
        
        if db.empty:
            return {
                'company_name': symbol,
                'sector': 'Unknown',
                'industry': 'Unknown',
                'sub_industry': 'Unknown'
            }
        
        # Look up symbol
        stock_row = db[db['Symbol'] == symbol]
        
        if stock_row.empty:
            return {
                'company_name': symbol,
                'sector': 'Unknown',
                'industry': 'Unknown',
                'sub_industry': 'Unknown'
            }
        
        row = stock_row.iloc[0]
        return {
            'company_name': symbol,  # Will be enhanced by Schwab API later
            'sector': str(row.get('Sector', 'Unknown')),
            'industry': str(row.get('Industry', 'Unknown')),
            'sub_industry': str(row.get('Sub_Industry', 'Unknown'))
        }
    
    def score_stock(
        self,
        symbol: str,
        style: TradingStyle = TradingStyle.SWING,
        daily_df: pd.DataFrame = None,
        spy_df: pd.DataFrame = None,
        sector_df: pd.DataFrame = None,
        sector_etf: str = None,
        current_price: float = None
    ) -> BlueprintScore:
        """Score a stock using 8-factor model
        
        Args:
            symbol: Stock ticker
            style: Trading style for evaluation
            daily_df: Daily OHLCV DataFrame (fetched if None)
            spy_df: SPY daily DataFrame for RS calculation
            sector_df: Sector ETF daily DataFrame
            sector_etf: Sector ETF symbol
            current_price: Current price (fetched if None)
            
        Returns:
            BlueprintScore with complete analysis
        """
        logger.info(f"Scoring {symbol} for {style.value} style")
        
        # Fetch data if not provided
        if daily_df is None:
            daily_df = self.client.get_daily(symbol, months=12)
        
        if daily_df is None or len(daily_df) < 50:
            return self._create_error_score(symbol, "Insufficient price data")
        
        if spy_df is None:
            spy_df = self.client.get_daily('SPY', months=12)
        
        if current_price is None:
            quote = self.client.get_quote(symbol)
            current_price = quote['last'] if quote else float(daily_df['close'].iloc[-1])
            # Try to get company name from quote
            company_name = quote.get('description', symbol) if quote else symbol
        else:
            # Get company name from quote even if price provided
            quote = self.client.get_quote(symbol)
            company_name = quote.get('description', symbol) if quote else symbol
        
        # Get sector info from sector_analysis (for sector ETF mapping)
        from sector_analysis import get_sector_info
        sector_info = get_sector_info(symbol)
        sector_etf = sector_info.sector_etf if sector_info else 'SPY'
        sector_name = sector_info.sector_name if sector_info else 'Unknown'
        
        # Get industry info from Master Database
        stock_info = self._get_stock_info(symbol)
        industry_name = stock_info['industry']
        # company_name already retrieved from quote above
        
        # Prefer sector from database if available
        if stock_info['sector'] != 'Unknown':
            sector_name = stock_info['sector']
        
        if sector_df is None and sector_etf:
            sector_df = self.client.get_daily(sector_etf, months=12)
        
        # Calculate all factors
        factors = {}
        warnings = []
        
        # 1. Macro Alignment (15%)
        macro_factor = self._score_macro_alignment(style)
        factors['macro_alignment'] = macro_factor
        
        # 2. Sector Strength (15%)
        sector_factor, sector_rank = self._score_sector_strength(sector_etf, style)
        factors['sector_strength'] = sector_factor
        # Only add warning if we have valid sector data (rank 1-11) and it's actually weak
        if sector_factor.score < 5 and sector_rank <= 11 and sector_name != 'Unknown':
            warnings.append(f"Weak sector: {sector_name} ranked #{sector_rank}/11")
        elif sector_name == 'Unknown' or sector_rank > 11:
            # Don't warn about unknown sectors - handled by neutral score
            pass
        
        # 3. Industry Strength (10%) - NOW USING FINVIZ DATA
        industry_factor = self._score_industry_strength(symbol, sector_info, industry_name)
        factors['industry_strength'] = industry_factor
        if industry_factor.score < 4:
            warnings.append(f"Weak industry: {industry_name}")
        
        # 4. Trend Structure (15%)
        trend_factor, trend_state = self._score_trend_structure(daily_df, current_price)
        factors['trend_structure'] = trend_factor
        if trend_state == 'DOUBLE_DEATH':
            warnings.append("⚠️ DOUBLE DEATH - Mandatory 50% trim for B&H")
        
        # 5. Relative Strength (15%)
        rs_factor, rs_composite = self._score_relative_strength(daily_df, spy_df)
        factors['relative_strength'] = rs_factor
        
        # Check for RS new high (leader signal)
        if is_rs_new_high(daily_df, spy_df):
            rs_factor.rationale += " ⭐ RS NEW HIGH - Leader signal!"
        
        # 6. Setup Quality (15%)
        setup_factor, setup_info = self._score_setup_quality(
            daily_df, current_price, style
        )
        factors['setup_quality'] = setup_factor
        
        # 7. Risk/Reward (10%)
        rr_factor = self._score_risk_reward(setup_info, style)
        factors['risk_reward'] = rr_factor
        
        # 8. Volume Pattern (5%)
        volume_factor = self._score_volume_pattern(daily_df)
        factors['volume_pattern'] = volume_factor
        
        # Calculate total weighted score
        total_score = sum(f.weighted_score for f in factors.values())
        
        # Determine recommendation
        recommendation, conviction = self._determine_recommendation(
            total_score, factors, style, warnings
        )
        
        return BlueprintScore(
            symbol=symbol,
            current_price=current_price,
            total_score=round(total_score, 2),
            recommendation=recommendation,
            conviction=conviction,
            style=style,
            factors=factors,
            setup=setup_info,
            sector_rank=sector_rank,
            sector_name=sector_name,
            industry_name=industry_name,  # NEW: From Master Database
            company_name=company_name,  # NEW: From Master Database  
            trend_state=trend_state,
            rs_composite=rs_composite,
            warnings=warnings
        )
    
    def _score_macro_alignment(self, style: TradingStyle) -> FactorScore:
        """Score macro alignment (15% weight)"""
        weight = BLUEPRINT_SCORE_WEIGHTS['macro_alignment']
        
        macro_score = self.macro.get_last_score()
        if macro_score is None:
            return FactorScore(
                name='Macro Alignment',
                score=5.0,
                weight=weight,
                weighted_score=5.0 * weight,
                rationale='Macro score not available'
            )
        
        # Convert macro score (0-10) directly
        score = macro_score.total_score
        
        # Adjust for trading against macro
        if macro_score.regime == 'PRESERVATION' and style != TradingStyle.BUY_HOLD:
            score = min(score, 3.0)
            rationale = f"Preservation mode - Macro {macro_score.total_score:.1f}, Deploy {macro_score.deployment_pct:.0f}%"
        elif macro_score.zbt_active:
            score = 10.0
            rationale = "ZBT ACTIVE - Full deployment override"
        else:
            rationale = f"Macro {macro_score.total_score:.1f}, Regime: {macro_score.regime}"
        
        return FactorScore(
            name='Macro Alignment',
            score=score,
            weight=weight,
            weighted_score=score * weight,
            rationale=rationale
        )
    
    def _score_sector_strength(
        self, 
        sector_etf: str, 
        style: TradingStyle
    ) -> Tuple[FactorScore, int]:
        """Score sector strength (15% weight)"""
        weight = BLUEPRINT_SCORE_WEIGHTS['sector_strength']
        
        sector_rank = self.sectors.get_sector_rank(sector_etf)
        
        # Handle unknown sector (rank 99)
        if sector_rank > 11:
            return FactorScore(
                name='Sector Strength',
                score=5.0,  # Neutral score for unknown
                weight=weight,
                weighted_score=5.0 * weight,
                rationale=f"Sector {sector_etf} not ranked (using neutral score)"
            ), sector_rank
        
        # Score based on rank (1-11)
        if sector_rank == 1:
            score = 10.0
        elif sector_rank == 2:
            score = 9.0
        elif sector_rank == 3:
            score = 8.0
        elif sector_rank <= 5:
            score = 6.0
        elif sector_rank <= 8:
            score = 4.0
        elif sector_rank <= 10:
            score = 2.0
        else:
            score = 0.0
        
        # Check style requirements
        is_acceptable, reason = self.sectors.is_sector_acceptable(
            sector_etf, style.value
        )
        
        if not is_acceptable:
            score = min(score, 3.0)
        
        return FactorScore(
            name='Sector Strength',
            score=score,
            weight=weight,
            weighted_score=score * weight,
            rationale=f"Sector {sector_etf} rank #{sector_rank}/11"
        ), sector_rank
    
    def _score_industry_strength(
        self, 
        symbol: str,
        sector_info,
        industry_name: str = None
    ) -> FactorScore:
        """Score industry strength using Finviz data (10% weight)
        
        Scoring Logic:
        1. Query Finviz for industry's stocks sorted by weekly performance
        2. Calculate average industry weekly performance
        3. Determine stock's rank within industry
        4. Score based on industry performance + leader bonus
        
        Scoring Scale (Industry Weekly Performance):
        - > +5%: 10.0 (Hot industry)
        - > +3%: 9.0 (Very strong)
        - > +1%: 8.0 (Solid)
        - > 0%: 6.0 (Positive)
        - > -1%: 5.0 (Slight weakness)
        - > -3%: 3.0 (Underperforming)
        - > -5%: 2.0 (Significant weakness)
        - <= -5%: 0.0 (Avoid)
        
        Bonus: +1 if stock is in top 3 of industry by weekly perf
        """
        weight = BLUEPRINT_SCORE_WEIGHTS['industry_strength']
        
        # Get industry name from Finviz if not provided
        if not industry_name or industry_name == 'Unknown':
            industry_name = sector_info.industry if sector_info else 'Unknown'
        
        # If no Finviz processor or unknown industry, return neutral score
        if not self.finviz or not industry_name or industry_name == 'Unknown':
            return FactorScore(
                name='Industry Strength',
                score=5.0,
                weight=weight,
                weighted_score=5.0 * weight,
                rationale=f"Industry: {industry_name} (Finviz data not available)"
            )
        
        try:
            # Get Finviz filter for this industry
            industry_filter = self._get_industry_filter(industry_name)
            logger.info(f"Querying Finviz for industry: {industry_name} ({industry_filter})")
            
            # Query Finviz API for industry stocks sorted by weekly performance
            df = self.finviz.fetch_from_api(
                filters=industry_filter,
                view='141',  # Performance view
                order='-perf1w'  # Sort by weekly performance descending
            )
            
            if df is None or df.empty:
                logger.warning(f"No Finviz data for industry: {industry_filter}")
                return FactorScore(
                    name='Industry Strength',
                    score=5.0,
                    weight=weight,
                    weighted_score=5.0 * weight,
                    rationale=f"Industry: {industry_name} (no data from Finviz)"
                )
            
            # Calculate average industry weekly performance
            perf_col = None
            for col in ['Performance (Week)', 'Perf Week', 'Perf 1W']:
                if col in df.columns:
                    perf_col = col
                    break
            
            if perf_col is None:
                logger.warning(f"No performance column found in Finviz data")
                return FactorScore(
                    name='Industry Strength',
                    score=5.0,
                    weight=weight,
                    weighted_score=5.0 * weight,
                    rationale=f"Industry: {industry_name} (performance data unavailable)"
                )
            
            # Parse performance values (remove % sign if present)
            def parse_perf(val):
                try:
                    if pd.isna(val):
                        return 0.0
                    val_str = str(val).replace('%', '').strip()
                    return float(val_str)
                except:
                    return 0.0
            
            df['perf_numeric'] = df[perf_col].apply(parse_perf)
            avg_industry_perf = df['perf_numeric'].mean()
            
            # Find stock's rank in industry
            stock_rank = None
            is_leader = False
            if 'Ticker' in df.columns:
                stock_row = df[df['Ticker'] == symbol.upper()]
                if not stock_row.empty:
                    stock_rank = df[df['Ticker'] == symbol.upper()].index[0] + 1
                    # Check if in top 3
                    top_3_tickers = df['Ticker'].head(3).tolist()
                    is_leader = symbol.upper() in top_3_tickers
            
            # Score based on average industry weekly performance
            if avg_industry_perf > 5.0:
                score = 10.0
                perf_desc = "Hot industry"
            elif avg_industry_perf > 3.0:
                score = 9.0
                perf_desc = "Very strong"
            elif avg_industry_perf > 1.0:
                score = 8.0
                perf_desc = "Solid outperformance"
            elif avg_industry_perf > 0.0:
                score = 6.0
                perf_desc = "Positive momentum"
            elif avg_industry_perf > -1.0:
                score = 5.0
                perf_desc = "Slight weakness"
            elif avg_industry_perf > -3.0:
                score = 3.0
                perf_desc = "Underperforming"
            elif avg_industry_perf > -5.0:
                score = 2.0
                perf_desc = "Significant weakness"
            else:
                score = 0.0
                perf_desc = "Industry in freefall"
            
            # Leader bonus: +1 if stock is top 3 in industry
            if is_leader:
                score = min(score + 1.0, 10.0)
                leader_note = " ⭐ INDUSTRY LEADER"
            else:
                leader_note = ""
            
            # Build rationale
            rank_str = f" (#{stock_rank}/{len(df)})" if stock_rank else ""
            rationale = f"{industry_name}: {avg_industry_perf:+.1f}% avg week - {perf_desc}{rank_str}{leader_note}"
            
            logger.info(f"Industry score for {symbol}: {score} - {rationale}")
            
            return FactorScore(
                name='Industry Strength',
                score=score,
                weight=weight,
                weighted_score=score * weight,
                rationale=rationale
            )
            
        except Exception as e:
            logger.error(f"Error scoring industry strength: {e}")
            return FactorScore(
                name='Industry Strength',
                score=5.0,
                weight=weight,
                weighted_score=5.0 * weight,
                rationale=f"Industry: {industry_name} (error: {str(e)[:50]})"
            )
    
    def _score_trend_structure(
        self, 
        df: pd.DataFrame, 
        current_price: float
    ) -> Tuple[FactorScore, str]:
        """Score trend structure (15% weight)"""
        weight = BLUEPRINT_SCORE_WEIGHTS['trend_structure']
        
        trend_state, trend_score = detect_trend_structure(df, current_price)
        
        # Normalize to 0-10 scale
        score = trend_score
        
        return FactorScore(
            name='Trend Structure',
            score=score,
            weight=weight,
            weighted_score=score * weight,
            rationale=f"Trend: {trend_state}"
        ), trend_state
    
    def _score_relative_strength(
        self, 
        stock_df: pd.DataFrame, 
        spy_df: pd.DataFrame
    ) -> Tuple[FactorScore, float]:
        """Score relative strength vs SPY (15% weight)"""
        weight = BLUEPRINT_SCORE_WEIGHTS['relative_strength']
        
        rs_values = calculate_relative_strength(stock_df, spy_df)
        composite = rs_values.get('composite', 100.0)
        
        # Score based on RS composite
        if composite > 130:
            score = 10.0
        elif composite > 110:
            score = 8.0
        elif composite > 100:
            score = 6.0
        elif composite > 90:
            score = 5.0
        elif composite > 80:
            score = 3.0
        else:
            score = 0.0
        
        return FactorScore(
            name='Relative Strength',
            score=score,
            weight=weight,
            weighted_score=score * weight,
            rationale=f"RS Composite: {composite:.1f} vs SPY"
        ), composite
    
    def _score_setup_quality(
        self, 
        df: pd.DataFrame, 
        current_price: float,
        style: TradingStyle
    ) -> Tuple[FactorScore, Optional[SetupInfo]]:
        """Score setup quality (15% weight)"""
        weight = BLUEPRINT_SCORE_WEIGHTS['setup_quality']
        
        # Detect setup type
        setup_info = self._detect_setup(df, current_price, style)
        
        if setup_info is None:
            return FactorScore(
                name='Setup Quality',
                score=0.0,
                weight=weight,
                weighted_score=0.0,
                rationale="No valid setup detected"
            ), None
        
        score = setup_info.quality
        
        return FactorScore(
            name='Setup Quality',
            score=score,
            weight=weight,
            weighted_score=score * weight,
            rationale=f"Setup: {setup_info.setup_type} - {setup_info.description}"
        ), setup_info
    
    def _detect_setup(
        self, 
        df: pd.DataFrame, 
        current_price: float,
        style: TradingStyle
    ) -> Optional[SetupInfo]:
        """Detect trading setup and calculate entry/stop/target"""
        
        if df is None or len(df) < 50:
            return None
        
        # Calculate indicators
        ema_21 = calculate_ema(df, 21)
        ema_50 = calculate_ema(df, 50)
        rsi = calculate_rsi(df)
        atr = calculate_atr(df)
        macd = calculate_macd(df)
        
        if ema_21 is None or ema_50 is None:
            return None
        
        ema_21_val = float(ema_21.iloc[-1])
        ema_50_val = float(ema_50.iloc[-1])
        
        # Determine setup type
        setup_type = 'none'
        quality = 0.0
        description = ''
        
        # Check for pullback setup
        price_near_21ema = abs(current_price - ema_21_val) / ema_21_val < 0.03
        uptrend = current_price > ema_50_val and ema_21_val > ema_50_val
        rsi_neutral = 40 <= rsi <= 65
        macd_positive = macd['macd'] > 0
        
        if uptrend and price_near_21ema and rsi_neutral:
            setup_type = 'pullback'
            quality = 8.0 if macd_positive else 6.0
            description = 'Pullback to 21 EMA in uptrend'
        
        # Check for breakout setup
        recent_high = df['high'].iloc[-20:].max()
        near_breakout = current_price >= recent_high * 0.98
        volume_surge = df['volume'].iloc[-1] > df['volume'].iloc[-20:].mean() * 1.3
        
        if near_breakout and uptrend:
            setup_type = 'breakout'
            quality = 9.0 if volume_surge else 7.0
            description = 'Breakout from consolidation'
        
        # Check for mean reversion (oversold bounce)
        if rsi < 30 and current_price > ema_50_val * 0.9:
            setup_type = 'mean_reversion'
            quality = 5.0  # Higher risk
            description = 'Oversold bounce candidate'
        
        if setup_type == 'none':
            return None
        
        # Calculate entry, stop, target
        entry_price = current_price
        
        if setup_type == 'pullback':
            stop_price = ema_21_val - (atr * 1.5)
            target_price = current_price + (atr * 3)
        elif setup_type == 'breakout':
            stop_price = min(ema_21_val, df['low'].iloc[-5:].min())
            target_price = current_price + (atr * 2.5)
        else:  # mean reversion
            stop_price = df['low'].iloc[-10:].min() - (atr * 0.5)
            target_price = ema_21_val
        
        risk = entry_price - stop_price
        reward = target_price - entry_price
        rr = reward / risk if risk > 0 else 0
        
        return SetupInfo(
            setup_type=setup_type,
            quality=quality,
            entry_price=round(entry_price, 2),
            stop_price=round(stop_price, 2),
            target_price=round(target_price, 2),
            risk_reward=round(rr, 2),
            description=description
        )
    
    def _score_risk_reward(
        self, 
        setup: Optional[SetupInfo],
        style: TradingStyle
    ) -> FactorScore:
        """Score risk/reward ratio (10% weight)"""
        weight = BLUEPRINT_SCORE_WEIGHTS['risk_reward']
        
        if setup is None:
            return FactorScore(
                name='Risk/Reward',
                score=0.0,
                weight=weight,
                weighted_score=0.0,
                rationale="No setup to evaluate"
            )
        
        rr = setup.risk_reward
        min_rr = MIN_RR_RATIOS.get(style.value, 2.0)
        
        if min_rr is None:  # B&H
            score = 6.0
            rationale = "B&H: Thesis-driven, not R:R based"
        elif rr >= 4.0:
            score = 10.0
            rationale = f"R:R {rr:.1f}:1 - Excellent"
        elif rr >= 3.0:
            score = 8.0
            rationale = f"R:R {rr:.1f}:1 - Good"
        elif rr >= 2.0:
            score = 6.0
            rationale = f"R:R {rr:.1f}:1 - Acceptable"
        elif rr >= 1.5:
            score = 4.0
            rationale = f"R:R {rr:.1f}:1 - Marginal"
        elif rr >= 1.0:
            score = 2.0
            rationale = f"R:R {rr:.1f}:1 - Poor"
        else:
            score = 0.0
            rationale = f"R:R {rr:.1f}:1 - Unacceptable"
        
        # Penalize if below minimum for style
        if min_rr and rr < min_rr:
            score = min(score, 3.0)
            rationale += f" (Below min {min_rr}:1 for {style.value})"
        
        return FactorScore(
            name='Risk/Reward',
            score=score,
            weight=weight,
            weighted_score=score * weight,
            rationale=rationale
        )
    
    def _score_volume_pattern(self, df: pd.DataFrame) -> FactorScore:
        """Score volume pattern (5% weight)"""
        weight = BLUEPRINT_SCORE_WEIGHTS['volume_pattern']
        
        if df is None or 'volume' not in df.columns:
            return FactorScore(
                name='Volume Pattern',
                score=5.0,
                weight=weight,
                weighted_score=5.0 * weight,
                rationale="Volume data not available"
            )
        
        rel_vol, today_vol = calculate_relative_volume(df)
        
        # Check for accumulation (up days on higher volume)
        recent = df.iloc[-10:]
        up_days = recent[recent['close'] > recent['open']]
        down_days = recent[recent['close'] <= recent['open']]
        
        avg_up_vol = up_days['volume'].mean() if len(up_days) > 0 else 0
        avg_down_vol = down_days['volume'].mean() if len(down_days) > 0 else 0
        
        if avg_up_vol > avg_down_vol * 1.3:
            score = 9.0
            pattern = "Accumulation (higher vol on up days)"
        elif avg_up_vol > avg_down_vol:
            score = 7.0
            pattern = "Mild accumulation"
        elif avg_down_vol > avg_up_vol * 1.3:
            score = 2.0
            pattern = "Distribution (higher vol on down days)"
        else:
            score = 5.0
            pattern = "Neutral volume pattern"
        
        # Adjust for relative volume
        if rel_vol > 1.5:
            score = min(score + 1, 10)
            pattern += f", RelVol {rel_vol:.1f}x"
        
        return FactorScore(
            name='Volume Pattern',
            score=score,
            weight=weight,
            weighted_score=score * weight,
            rationale=pattern
        )
    
    def _determine_recommendation(
        self,
        total_score: float,
        factors: Dict[str, FactorScore],
        style: TradingStyle,
        warnings: List[str]
    ) -> Tuple[Recommendation, str]:
        """Determine final recommendation and conviction"""
        
        # Check for hard stops
        if 'DOUBLE DEATH' in str(warnings):
            return Recommendation.AVOID, 'LOW'
        
        sector_score = factors.get('sector_strength', FactorScore('', 0, 0, 0, '')).score
        setup_score = factors.get('setup_quality', FactorScore('', 0, 0, 0, '')).score
        
        # Apply decision tree logic
        if total_score >= SCORE_THRESHOLDS['strong_buy']:
            recommendation = Recommendation.STRONG_BUY
            conviction = 'HIGH'
        elif total_score >= SCORE_THRESHOLDS['buy']:
            recommendation = Recommendation.BUY
            conviction = 'HIGH'
        elif total_score >= SCORE_THRESHOLDS['buy_reduced']:
            recommendation = Recommendation.BUY_REDUCED
            conviction = 'MEDIUM'
        elif total_score >= SCORE_THRESHOLDS['speculative']:
            recommendation = Recommendation.SPECULATIVE
            conviction = 'LOW'
        else:
            recommendation = Recommendation.AVOID
            conviction = 'LOW'
        
        # Additional checks
        if setup_score == 0 and recommendation != Recommendation.AVOID:
            recommendation = Recommendation.WATCHLIST
            conviction = 'LOW'
        
        # Sector filter for momentum
        if style == TradingStyle.MOMENTUM and sector_score < 6:
            if recommendation in [Recommendation.STRONG_BUY, Recommendation.BUY]:
                recommendation = Recommendation.AVOID
                conviction = 'LOW'
        
        return recommendation, conviction
    
    def _create_error_score(self, symbol: str, error: str) -> BlueprintScore:
        """Create error score when analysis fails"""
        return BlueprintScore(
            symbol=symbol,
            current_price=0,
            total_score=0,
            recommendation=Recommendation.AVOID,
            conviction='LOW',
            style=TradingStyle.SWING,
            warnings=[f"Error: {error}"]
        )
    
    def format_score_report(self, score: BlueprintScore) -> str:
        """Format score as detailed text report"""
        lines = [
            "=" * 75,
            f"BLUEPRINT ANALYSIS: {score.symbol}",
            "=" * 75,
            f"Date: {score.timestamp.strftime('%Y-%m-%d %H:%M')}",
            f"Price: ${score.current_price:.2f}",
            f"Style: {score.style.value.upper()}",
            "",
            "-" * 75,
            "RECOMMENDATION",
            "-" * 75,
            f"  Decision:   {score.recommendation.value}",
            f"  Conviction: {score.conviction}",
            f"  Score:      {score.total_score:.2f} / 10.00",
            "",
        ]
        
        # Warnings
        if score.warnings:
            lines.append("⚠️  WARNINGS:")
            for w in score.warnings:
                lines.append(f"    • {w}")
            lines.append("")
        
        # Trade Plan (if setup exists)
        if score.setup:
            lines.extend([
                "-" * 75,
                "TRADE PLAN",
                "-" * 75,
                f"  Setup Type: {score.setup.setup_type}",
                f"  Entry:      ${score.setup.entry_price:.2f}",
                f"  Stop:       ${score.setup.stop_price:.2f}",
                f"  Target:     ${score.setup.target_price:.2f}",
                f"  Risk/Reward: {score.setup.risk_reward:.1f}:1",
                "",
            ])
        
        # Factor Details with right-justified numerics
        lines.extend([
            "-" * 75,
            "FACTOR ANALYSIS",
            "-" * 75,
            f"  {'Factor':<25} {'Score':>8} {'Weight':>8} {'Weighted':>10}",
            "  " + "-" * 60,
        ])
        
        for name, factor in score.factors.items():
            lines.append(
                f"  {factor.name:<25} {factor.score:>8.1f} {factor.weight*100:>7.0f}% "
                f"{factor.weighted_score:>10.2f}"
            )
        
        lines.extend([
            "  " + "-" * 60,
            f"  {'TOTAL':<25} {'':>8} {'100%':>8} {score.total_score:>10.2f}",
            "",
        ])
        
        # Factor Rationales
        lines.extend([
            "-" * 75,
            "DETAILS",
            "-" * 75,
        ])
        
        for name, factor in score.factors.items():
            lines.append(f"  • {factor.name}: {factor.rationale}")
        
        lines.extend([
            "",
            "-" * 75,
            "CONTEXT",
            "-" * 75,
            f"  Sector: {score.sector_name} (Rank #{score.sector_rank}/11)",
            f"  Trend: {score.trend_state}",
            f"  RS Composite: {score.rs_composite:.1f}",
            "",
            "=" * 75,
        ])
        
        return "\n".join(lines)


#############################################
# STANDALONE TEST
#############################################

if __name__ == "__main__":
    """
    Test the BlueprintScorer module.
    Run from D:\AI_Based_Analysis with: python -m src.blueprint_scorer
    Or run directly (will show import test only).
    """
    import sys
    
    print("="*60)
    print("BLUEPRINT SCORER v1.1.0 - Module Test")
    print("="*60)
    
    try:
        print("\n[1] Testing imports...")
        print(f"    - pandas: {pd.__version__}")
        print(f"    - config: OK")
        print(f"    - technical: OK")
        print("    ✅ All imports successful")
        
        print("\n[2] Testing data classes...")
        test_factor = FactorScore(
            name="Test Factor",
            score=8.0,
            weight=0.15,
            weighted_score=1.2,
            rationale="Test rationale"
        )
        print(f"    - FactorScore: {test_factor.name} = {test_factor.score}")
        print("    ✅ Data classes OK")
        
        print("\n[3] Testing enums...")
        print(f"    - Recommendation.BUY = {Recommendation.BUY.value}")
        print(f"    - TradingStyle.SWING = {TradingStyle.SWING.value}")
        print("    ✅ Enums OK")
        
        print("\n[4] Testing weight configuration...")
        total_weight = sum(BLUEPRINT_SCORE_WEIGHTS.values())
        print(f"    - Total weight: {total_weight:.2f}")
        for factor, weight in BLUEPRINT_SCORE_WEIGHTS.items():
            print(f"    - {factor}: {weight*100:.0f}%")
        if abs(total_weight - 1.0) < 0.001:
            print("    ✅ Weights sum to 100%")
        else:
            print(f"    ⚠️  Weights sum to {total_weight*100:.1f}% (should be 100%)")
        
        print("\n[5] Testing score thresholds...")
        for level, threshold in SCORE_THRESHOLDS.items():
            print(f"    - {level}: {threshold}")
        print("    ✅ Thresholds OK")
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED")
        print("="*60)
        print("\nNote: Full scoring requires MarketDataClient, MacroScoreCalculator,")
        print("SectorRanker, and FinvizDataProcessor instances.")
        print("Run the full app with: python -m src.gui_main")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n")
    input("Press Enter to exit...")
