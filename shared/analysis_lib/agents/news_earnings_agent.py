"""
News & Earnings Agent
=====================
Version: 1.0.0

Monitors news and earnings for trading opportunities:
- Pre-market and post-market news scanning
- Earnings calendar monitoring
- Earnings surprise detection
- News catalyst identification

Data Sources:
- Finviz Elite API for news and stock data
- Yahoo Finance for earnings calendar
"""

import re
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from html.parser import HTMLParser

from .base_agent import (
    BaseAgent, 
    AgentCandidate, 
    CandidatePriority,
    CandidateSource,
    TradingStyleSuggestion
)


class FinvizNewsParser(HTMLParser):
    """Parse news headlines from Finviz news page"""
    
    def __init__(self):
        super().__init__()
        self.in_news_link = False
        self.in_source = False
        self.headlines = []
        self.current_headline = ""
        self.current_source = ""
        self.current_ticker = ""
    
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        # News headlines
        if tag == 'a':
            href = attrs_dict.get('href', '')
            classes = attrs_dict.get('class', '')
            
            if 'nn-tab-link' in classes:
                self.in_news_link = True
                self.current_headline = ""
            
            # Check for ticker links
            if '/quote.ashx?t=' in href:
                match = re.search(r't=([A-Z]+)', href)
                if match:
                    self.current_ticker = match.group(1)
        
        # News source
        if tag == 'span' and 'nn-tab-source' in attrs_dict.get('class', ''):
            self.in_source = True
    
    def handle_endtag(self, tag):
        if tag == 'a' and self.in_news_link:
            if self.current_headline.strip():
                self.headlines.append({
                    'headline': self.current_headline.strip(),
                    'source': self.current_source.strip(),
                    'ticker': self.current_ticker,
                    'timestamp': datetime.now()
                })
            self.current_headline = ""
            self.current_ticker = ""
            self.in_news_link = False
        
        if tag == 'span' and self.in_source:
            self.in_source = False
    
    def handle_data(self, data):
        if self.in_news_link:
            self.current_headline += data
        if self.in_source:
            self.current_source = data


class NewsEarningsAgent(BaseAgent):
    """
    Agent that monitors news and earnings for trading opportunities.
    
    Features:
    - Real-time news scanning from Finviz
    - Earnings calendar tracking
    - Earnings surprise detection
    - Catalyst classification and scoring
    """
    
    # Keywords for catalyst detection
    BULLISH_KEYWORDS = [
        'beats', 'beat', 'exceeds', 'surpasses', 'raises', 'upgrades', 'upgrade',
        'strong', 'record', 'growth', 'surge', 'soars', 'jumps', 'rallies',
        'acquisition', 'buyback', 'dividend', 'approval', 'fda approved',
        'breakthrough', 'partnership', 'contract', 'deal', 'wins',
        'outperform', 'buy rating', 'price target raised', 'bullish'
    ]
    
    BEARISH_KEYWORDS = [
        'misses', 'miss', 'disappoints', 'cuts', 'lowers', 'downgrades', 'downgrade',
        'weak', 'decline', 'drops', 'falls', 'plunges', 'tumbles', 'crashes',
        'lawsuit', 'investigation', 'recall', 'warning', 'guidance cut',
        'underperform', 'sell rating', 'price target cut', 'bearish'
    ]
    
    EARNINGS_KEYWORDS = [
        'earnings', 'eps', 'revenue', 'profit', 'quarter', 'q1', 'q2', 'q3', 'q4',
        'results', 'guidance', 'outlook', 'forecast', 'fiscal'
    ]
    
    HIGH_IMPACT_KEYWORDS = [
        'fda', 'approval', 'merger', 'acquisition', 'buyout', 'takeover',
        'bankruptcy', 'ceo', 'sec', 'investigation', 'fraud',
        'breakthrough', 'blockbuster', 'record'
    ]
    
    def __init__(self, finviz_processor=None, api_token: str = None):
        """
        Initialize News/Earnings Agent.
        
        Args:
            finviz_processor: FinvizDataProcessor instance
            api_token: Finviz Elite API token (optional, uses processor's token if not provided)
        """
        super().__init__("News & Earnings Agent", finviz_processor)
        
        self.api_token = api_token
        if not self.api_token and finviz_processor:
            self.api_token = finviz_processor.api_token
        
        # Cache for avoiding duplicate alerts
        self._seen_headlines: set = set()
        self._seen_earnings: set = set()
        
        # Configuration
        self.config = {
            'min_price_move_pct': 3.0,      # Min % move to trigger alert
            'min_volume_ratio': 1.5,         # Min volume vs average
            'earnings_lookback_days': 7,     # Days back to check for earnings
            'earnings_lookahead_days': 3,    # Days ahead for upcoming earnings
            'max_headlines_per_scan': 50,    # Limit news items per scan
        }
    
    def run_once(self) -> List[AgentCandidate]:
        """
        Execute single scan cycle.
        
        Returns:
            List of AgentCandidate objects
        """
        candidates = []
        
        # 1. Scan news for catalysts
        news_candidates = self._scan_news()
        candidates.extend(news_candidates)
        
        # 2. Check recent earnings surprises
        earnings_candidates = self._scan_earnings_surprises()
        candidates.extend(earnings_candidates)
        
        # 3. Check upcoming earnings calendar
        upcoming_candidates = self._scan_upcoming_earnings()
        candidates.extend(upcoming_candidates)
        
        return candidates
    
    def _scan_news(self) -> List[AgentCandidate]:
        """Scan Finviz news for trading catalysts"""
        candidates = []
        
        try:
            # Fetch news from Finviz
            news_items = self._fetch_finviz_news()
            
            for item in news_items[:self.config['max_headlines_per_scan']]:
                headline = item.get('headline', '')
                ticker = item.get('ticker', '')
                
                # Skip if already seen
                headline_hash = hash(f"{ticker}:{headline[:50]}")
                if headline_hash in self._seen_headlines:
                    continue
                self._seen_headlines.add(headline_hash)
                
                # Analyze the headline
                sentiment, catalyst_type, impact_score = self._analyze_headline(headline)
                
                # Only create candidate for significant news
                if impact_score >= 50 and ticker:
                    # Get stock data
                    stock_data = self._get_stock_data(ticker)
                    
                    # Determine priority
                    priority = self._calculate_priority(impact_score, stock_data)
                    
                    # Determine suggested style
                    suggested_style = self._suggest_trading_style(
                        catalyst_type, sentiment, impact_score, stock_data
                    )
                    
                    candidate = AgentCandidate(
                        symbol=ticker,
                        timestamp=datetime.now(),
                        source=CandidateSource.NEWS_CATALYST,
                        priority=priority,
                        score=impact_score,
                        headline=headline[:200],
                        summary=f"{catalyst_type} - {sentiment.upper()} sentiment",
                        catalyst_type=catalyst_type,
                        current_price=stock_data.get('price', 0),
                        change_pct=stock_data.get('change_pct', 0),
                        volume_ratio=stock_data.get('volume_ratio', 1.0),
                        sector=stock_data.get('sector', ''),
                        industry=stock_data.get('industry', ''),
                        suggested_style=suggested_style,
                        action_notes=self._generate_action_notes(
                            catalyst_type, sentiment, stock_data
                        ),
                        metadata={
                            'sentiment': sentiment,
                            'source': item.get('source', 'Finviz'),
                            'raw_headline': headline,
                        }
                    )
                    candidates.append(candidate)
        
        except Exception as e:
            self.errors.append(f"News scan error: {str(e)}")
        
        return candidates
    
    def _scan_earnings_surprises(self) -> List[AgentCandidate]:
        """Scan for recent earnings surprises using Finviz"""
        candidates = []
        
        try:
            if not self.finviz or not self.api_token:
                return candidates
            
            # Use Finviz to find stocks with recent earnings surprises
            # Filter: Earnings date in past week, sorted by change
            df = self.finviz.fetch_from_api(
                filters='earningsdate_prevweek',
                view='141',
                order='-change'
            )
            
            if df is None or df.empty:
                return candidates
            
            for _, row in df.head(10).iterrows():
                ticker = row.get('Ticker', '')
                change = self._parse_percentage(row.get('Change', '0'))
                
                # Skip small moves
                if abs(change) < self.config['min_price_move_pct']:
                    continue
                
                # Skip if already seen
                earnings_hash = hash(f"earnings:{ticker}:{datetime.now().date()}")
                if earnings_hash in self._seen_earnings:
                    continue
                self._seen_earnings.add(earnings_hash)
                
                # Determine sentiment from price move
                sentiment = 'bullish' if change > 0 else 'bearish'
                
                # Calculate priority based on move size
                if abs(change) >= 10:
                    priority = CandidatePriority.HIGH
                    impact_score = 85
                elif abs(change) >= 5:
                    priority = CandidatePriority.MEDIUM
                    impact_score = 70
                else:
                    priority = CandidatePriority.LOW
                    impact_score = 55
                
                candidate = AgentCandidate(
                    symbol=ticker,
                    timestamp=datetime.now(),
                    source=CandidateSource.EARNINGS_SURPRISE,
                    priority=priority,
                    score=impact_score,
                    headline=f"Earnings {'Beat' if change > 0 else 'Miss'}: {ticker} {change:+.1f}%",
                    summary=f"Post-earnings move of {change:+.1f}%",
                    catalyst_type='Earnings Reaction',
                    current_price=self._parse_float(row.get('Price', 0)),
                    change_pct=change,
                    volume_ratio=self._parse_float(row.get('Rel Volume', 1.0)),
                    sector=row.get('Sector', ''),
                    industry=row.get('Industry', ''),
                    suggested_style=(
                        TradingStyleSuggestion.MOMENTUM if change > 0 
                        else TradingStyleSuggestion.SWING
                    ),
                    action_notes=self._earnings_action_notes(change, row),
                    metadata={
                        'sentiment': sentiment,
                        'eps_surprise': row.get('EPS this Y', ''),
                        'sales_surprise': row.get('Sales Q/Q', ''),
                    }
                )
                candidates.append(candidate)
        
        except Exception as e:
            self.errors.append(f"Earnings surprise scan error: {str(e)}")
        
        return candidates
    
    def _scan_upcoming_earnings(self) -> List[AgentCandidate]:
        """Scan for upcoming earnings using Finviz"""
        candidates = []
        
        try:
            if not self.finviz or not self.api_token:
                return candidates
            
            # Filter: Earnings this week, sorted by market cap
            df = self.finviz.fetch_from_api(
                filters='earningsdate_thisweek',
                view='111',
                order='-marketcap'
            )
            
            if df is None or df.empty:
                return candidates
            
            for _, row in df.head(15).iterrows():
                ticker = row.get('Ticker', '')
                
                # Skip if already seen
                upcoming_hash = hash(f"upcoming:{ticker}:{datetime.now().date()}")
                if upcoming_hash in self._seen_earnings:
                    continue
                self._seen_earnings.add(upcoming_hash)
                
                # Upcoming earnings are informational (lower priority)
                candidate = AgentCandidate(
                    symbol=ticker,
                    timestamp=datetime.now(),
                    source=CandidateSource.EARNINGS_CALENDAR,
                    priority=CandidatePriority.LOW,
                    score=40,
                    headline=f"Upcoming Earnings: {ticker}",
                    summary=f"Earnings scheduled this week - {row.get('Company', ticker)}",
                    catalyst_type='Upcoming Earnings',
                    current_price=self._parse_float(row.get('Price', 0)),
                    change_pct=self._parse_percentage(row.get('Change', '0')),
                    sector=row.get('Sector', ''),
                    industry=row.get('Industry', ''),
                    suggested_style=TradingStyleSuggestion.SPECULATIVE,
                    action_notes="Monitor for earnings play. Consider options strategies.",
                    metadata={
                        'market_cap': row.get('Market Cap', ''),
                        'company': row.get('Company', ''),
                    }
                )
                candidates.append(candidate)
        
        except Exception as e:
            self.errors.append(f"Upcoming earnings scan error: {str(e)}")
        
        return candidates
    
    def _fetch_finviz_news(self) -> List[Dict[str, Any]]:
        """Fetch news headlines from Finviz"""
        news_items = []
        
        try:
            url = "https://finviz.com/news.ashx"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            # Parse the HTML
            parser = FinvizNewsParser()
            parser.feed(response.text)
            news_items = parser.headlines
            
        except Exception as e:
            self.errors.append(f"Finviz news fetch error: {str(e)}")
        
        return news_items
    
    def _analyze_headline(self, headline: str) -> Tuple[str, str, float]:
        """
        Analyze a news headline for trading relevance.
        
        Returns:
            Tuple of (sentiment, catalyst_type, impact_score)
        """
        headline_lower = headline.lower()
        
        # Count keyword matches
        bullish_count = sum(1 for kw in self.BULLISH_KEYWORDS if kw in headline_lower)
        bearish_count = sum(1 for kw in self.BEARISH_KEYWORDS if kw in headline_lower)
        earnings_match = any(kw in headline_lower for kw in self.EARNINGS_KEYWORDS)
        high_impact = any(kw in headline_lower for kw in self.HIGH_IMPACT_KEYWORDS)
        
        # Determine sentiment
        if bullish_count > bearish_count:
            sentiment = 'bullish'
        elif bearish_count > bullish_count:
            sentiment = 'bearish'
        else:
            sentiment = 'neutral'
        
        # Determine catalyst type
        if earnings_match:
            catalyst_type = 'Earnings News'
        elif high_impact:
            if 'fda' in headline_lower:
                catalyst_type = 'FDA/Regulatory'
            elif 'merger' in headline_lower or 'acquisition' in headline_lower:
                catalyst_type = 'M&A Activity'
            elif 'upgrade' in headline_lower or 'downgrade' in headline_lower:
                catalyst_type = 'Analyst Action'
            else:
                catalyst_type = 'High Impact News'
        else:
            catalyst_type = 'General News'
        
        # Calculate impact score (0-100)
        base_score = 30
        
        # Sentiment strength
        sentiment_strength = abs(bullish_count - bearish_count)
        base_score += min(sentiment_strength * 10, 30)
        
        # High impact bonus
        if high_impact:
            base_score += 25
        
        # Earnings relevance bonus
        if earnings_match:
            base_score += 15
        
        impact_score = min(base_score, 100)
        
        return sentiment, catalyst_type, impact_score
    
    def _get_stock_data(self, ticker: str) -> Dict[str, Any]:
        """Get current stock data from Finviz"""
        stock_data = {
            'price': 0,
            'change_pct': 0,
            'volume_ratio': 1.0,
            'sector': '',
            'industry': ''
        }
        
        try:
            if self.finviz and self.api_token:
                # Quick lookup using API
                df = self.finviz.fetch_from_api(
                    filters=f'ticker_{ticker.lower()}',
                    view='111',
                    order=''
                )
                
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    stock_data['price'] = self._parse_float(row.get('Price', 0))
                    stock_data['change_pct'] = self._parse_percentage(row.get('Change', '0'))
                    stock_data['volume_ratio'] = self._parse_float(row.get('Rel Volume', 1.0))
                    stock_data['sector'] = row.get('Sector', '')
                    stock_data['industry'] = row.get('Industry', '')
        
        except Exception as e:
            pass  # Silently fail, return defaults
        
        return stock_data
    
    def _calculate_priority(self, impact_score: float, stock_data: Dict) -> CandidatePriority:
        """Calculate candidate priority based on multiple factors"""
        
        # High volume + high impact = critical
        if impact_score >= 80 and stock_data.get('volume_ratio', 1) >= 2.0:
            return CandidatePriority.CRITICAL
        
        # High impact or large price move
        if impact_score >= 70 or abs(stock_data.get('change_pct', 0)) >= 5:
            return CandidatePriority.HIGH
        
        # Medium impact
        if impact_score >= 50:
            return CandidatePriority.MEDIUM
        
        return CandidatePriority.LOW
    
    def _suggest_trading_style(
        self, 
        catalyst_type: str, 
        sentiment: str, 
        impact_score: float,
        stock_data: Dict
    ) -> TradingStyleSuggestion:
        """Suggest trading style based on catalyst characteristics"""
        
        # FDA/M&A are speculative plays
        if catalyst_type in ['FDA/Regulatory', 'M&A Activity']:
            return TradingStyleSuggestion.SPECULATIVE
        
        # High impact with volume = momentum
        if impact_score >= 70 and stock_data.get('volume_ratio', 1) >= 2.0:
            return TradingStyleSuggestion.MOMENTUM
        
        # Earnings with large move = momentum
        if 'Earnings' in catalyst_type and abs(stock_data.get('change_pct', 0)) >= 5:
            return TradingStyleSuggestion.MOMENTUM
        
        # Default to swing for most news
        return TradingStyleSuggestion.SWING
    
    def _generate_action_notes(
        self, 
        catalyst_type: str, 
        sentiment: str, 
        stock_data: Dict
    ) -> str:
        """Generate actionable notes for the candidate"""
        notes = []
        
        if sentiment == 'bullish':
            notes.append("Consider long entry on pullback")
        elif sentiment == 'bearish':
            notes.append("Watch for bounce or continued weakness")
        
        if stock_data.get('volume_ratio', 1) >= 2.0:
            notes.append(f"High volume ({stock_data['volume_ratio']:.1f}x avg)")
        
        if catalyst_type == 'Analyst Action':
            notes.append("Check price target changes")
        
        if catalyst_type == 'Earnings News':
            notes.append("Review full earnings report")
        
        return ". ".join(notes) if notes else "Monitor for entry opportunity"
    
    def _earnings_action_notes(self, change_pct: float, row: Dict) -> str:
        """Generate action notes for earnings plays"""
        notes = []
        
        if change_pct > 0:
            notes.append("Post-earnings momentum candidate")
            if change_pct >= 10:
                notes.append("Large gap - wait for consolidation")
            else:
                notes.append("Consider gap continuation trade")
        else:
            notes.append("Potential bounce play if oversold")
            notes.append("Wait for stabilization before entry")
        
        return ". ".join(notes)
    
    def _parse_percentage(self, value: Any) -> float:
        """Parse percentage string to float"""
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).replace('%', '').replace(',', ''))
        except (ValueError, TypeError):
            return 0.0
    
    def _parse_float(self, value: Any) -> float:
        """Parse string to float"""
        if isinstance(value, (int, float)):
            return float(value)
        try:
            # Handle suffixes like K, M, B
            value_str = str(value).replace(',', '').upper()
            multipliers = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
            
            for suffix, mult in multipliers.items():
                if suffix in value_str:
                    return float(value_str.replace(suffix, '')) * mult
            
            return float(value_str)
        except (ValueError, TypeError):
            return 0.0
    
    # === Public API for manual triggers ===
    
    def scan_ticker_news(self, ticker: str) -> List[AgentCandidate]:
        """
        Manually scan news for a specific ticker.
        
        Args:
            ticker: Stock symbol
            
        Returns:
            List of candidates related to this ticker
        """
        candidates = []
        
        try:
            # Fetch ticker-specific news from Finviz quote page
            url = f"https://finviz.com/quote.ashx?t={ticker.upper()}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            # Basic parsing for news table
            # Note: Full implementation would parse the news table specifically
            parser = FinvizNewsParser()
            parser.feed(response.text)
            
            for item in parser.headlines:
                sentiment, catalyst_type, impact_score = self._analyze_headline(
                    item.get('headline', '')
                )
                
                if impact_score >= 40:
                    stock_data = self._get_stock_data(ticker)
                    
                    candidate = AgentCandidate(
                        symbol=ticker.upper(),
                        timestamp=datetime.now(),
                        source=CandidateSource.NEWS_CATALYST,
                        priority=self._calculate_priority(impact_score, stock_data),
                        score=impact_score,
                        headline=item.get('headline', '')[:200],
                        summary=f"{catalyst_type} - {sentiment.upper()} sentiment",
                        catalyst_type=catalyst_type,
                        current_price=stock_data.get('price', 0),
                        change_pct=stock_data.get('change_pct', 0),
                        sector=stock_data.get('sector', ''),
                        industry=stock_data.get('industry', ''),
                        suggested_style=self._suggest_trading_style(
                            catalyst_type, sentiment, impact_score, stock_data
                        ),
                        metadata={'sentiment': sentiment}
                    )
                    candidates.append(candidate)
        
        except Exception as e:
            self.errors.append(f"Ticker news scan error for {ticker}: {str(e)}")
        
        return candidates
    
    def get_todays_movers(self, direction: str = 'up', limit: int = 10) -> List[AgentCandidate]:
        """
        Get today's top movers as candidates.
        
        Args:
            direction: 'up' for gainers, 'down' for losers
            limit: Number of results
            
        Returns:
            List of candidates
        """
        candidates = []
        
        try:
            if not self.finviz or not self.api_token:
                return candidates
            
            # Fetch top gainers or losers
            order = '-change' if direction == 'up' else 'change'
            df = self.finviz.fetch_from_api(
                filters='',
                view='141',
                order=order
            )
            
            if df is None or df.empty:
                return candidates
            
            for _, row in df.head(limit).iterrows():
                ticker = row.get('Ticker', '')
                change = self._parse_percentage(row.get('Change', '0'))
                
                # Skip small moves
                if abs(change) < 3:
                    continue
                
                candidate = AgentCandidate(
                    symbol=ticker,
                    timestamp=datetime.now(),
                    source=CandidateSource.NEWS_CATALYST,
                    priority=CandidatePriority.MEDIUM if abs(change) >= 5 else CandidatePriority.LOW,
                    score=min(50 + abs(change) * 3, 90),
                    headline=f"Top {'Gainer' if change > 0 else 'Loser'}: {ticker} {change:+.1f}%",
                    summary=f"Large move of {change:+.1f}% - investigate catalyst",
                    catalyst_type='Price Movement',
                    current_price=self._parse_float(row.get('Price', 0)),
                    change_pct=change,
                    volume_ratio=self._parse_float(row.get('Rel Volume', 1.0)),
                    sector=row.get('Sector', ''),
                    industry=row.get('Industry', ''),
                    suggested_style=(
                        TradingStyleSuggestion.MOMENTUM if change > 0 
                        else TradingStyleSuggestion.SWING
                    ),
                    action_notes="Investigate cause of move before entry"
                )
                candidates.append(candidate)
        
        except Exception as e:
            self.errors.append(f"Movers scan error: {str(e)}")
        
        return candidates
