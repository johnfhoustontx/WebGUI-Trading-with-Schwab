"""
SchwabClient - Schwab API Client with OAuth 2.0
Version: 1.0.0
Last Updated: 2025-01-01

Adapted from TradeAnalyzer v3.2 for Blueprint Analysis System.

Version 1.0.0 Changes:
- Initial implementation
- OAuth 2.0 authentication
- Quote, price history, options chain endpoints
"""

import json
import time
import base64
import webbrowser
import urllib.parse
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import requests
import pandas as pd

from config import SCHWAB_CONFIG_PATHS

logger = logging.getLogger(__name__)

#############################################
# SCHWAB API CLIENT
#############################################

class SchwabClient:
    """Schwab API client with OAuth 2.0 authentication"""
    
    AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
    TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
    BASE_URL = "https://api.schwabapi.com/marketdata/v1"
    
    FUTURES_MAP = {
        'ES': '/ES', 'NQ': '/NQ', 'MES': '/MES', 'MNQ': '/MNQ',
        'RTY': '/RTY', 'YM': '/YM', 'CL': '/CL', 'GC': '/GC',
        '/ES': '/ES', '/NQ': '/NQ', '/MES': '/MES', '/MNQ': '/MNQ',
        '/RTY': '/RTY', '/YM': '/YM', '/CL': '/CL', '/GC': '/GC'
    }
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize Schwab client
        
        Args:
            config_path: Path to appsettings.json. If None, searches default locations.
        """
        self.config_path = config_path or self._find_config_file()
        self.config = self._load_config()
        self.tokens = self._load_tokens()
        self.session = requests.Session()
        self._rate_limit_delay = 0.2  # Delay between API calls
    
    def _find_config_file(self) -> Optional[Path]:
        """Find appsettings.json in common locations"""
        for path in SCHWAB_CONFIG_PATHS:
            if path.exists():
                logger.info(f"Found config at {path}")
                return path
        return None
    
    def _load_config(self) -> Dict:
        """Load configuration from appsettings.json"""
        if not self.config_path or not self.config_path.exists():
            raise FileNotFoundError(
                f"appsettings.json not found. Searched: {SCHWAB_CONFIG_PATHS}"
            )
        
        with open(self.config_path, 'r') as f:
            config = json.load(f)
        
        schwab = config.get('Schwab', {})
        self.app_key = schwab.get('AppKey')
        self.app_secret = schwab.get('AppSecret')
        self.callback_url = schwab.get('CallbackUrl', 'https://127.0.0.1:8182')
        self.token_file = self.config_path.parent / schwab.get('TokenFilePath', 'tokens.json')
        
        if not self.app_key or not self.app_secret:
            raise ValueError("AppKey and AppSecret required in appsettings.json")
        
        logger.info(f"Loaded config from {self.config_path}")
        return config
    
    def _load_tokens(self) -> Dict:
        """Load tokens from file"""
        if self.token_file.exists():
            with open(self.token_file, 'r') as f:
                tokens = json.load(f)
                logger.debug(f"Loaded tokens from {self.token_file}")
                return tokens
        return {}
    
    def _save_tokens(self, tokens: Dict):
        """Save tokens to file"""
        self.tokens = tokens
        with open(self.token_file, 'w') as f:
            json.dump(tokens, f, indent=2)
        logger.debug(f"Saved tokens to {self.token_file}")
    
    def _is_token_expired(self) -> bool:
        """Check if access token is expired (with 5 min buffer)"""
        expires_at = self.tokens.get('ExpiresAt')
        if not expires_at:
            return True
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            exp_dt = exp_dt.replace(tzinfo=None)
            now = datetime.utcnow()
            buffer = timedelta(minutes=5)
            return now >= (exp_dt - buffer)
        except Exception as e:
            logger.warning(f"Error parsing expiry: {e}")
            return True
    
    def _is_refresh_token_expired(self) -> bool:
        """Check if refresh token is expired"""
        expires_at = self.tokens.get('RefreshTokenExpiresAt')
        if not expires_at:
            return True
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            exp_dt = exp_dt.replace(tzinfo=None)
            return datetime.utcnow() >= exp_dt
        except:
            return True
    
    def authorize(self):
        """Perform OAuth 2.0 authorization flow"""
        auth_url = (
            f"{self.AUTH_URL}?response_type=code"
            f"&client_id={self.app_key}"
            f"&redirect_uri={urllib.parse.quote(self.callback_url)}"
        )
        
        print("\n" + "="*60)
        print("SCHWAB API AUTHORIZATION REQUIRED")
        print("="*60)
        print("\n1. Opening browser to authorize...")
        print("2. Log in to your Schwab account")
        print("3. After authorization, copy the FULL redirect URL")
        print(f"   (It will start with: {self.callback_url})")
        print("="*60 + "\n")
        
        webbrowser.open(auth_url)
        
        redirect_url = input("Paste the full redirect URL here: ").strip()
        
        parsed = urllib.parse.urlparse(redirect_url)
        params = urllib.parse.parse_qs(parsed.query)
        
        if 'code' not in params:
            raise ValueError("No authorization code found in URL")
        
        auth_code = params['code'][0]
        logger.info("Got authorization code")
        self._exchange_code(auth_code)
    
    def _exchange_code(self, code: str):
        """Exchange authorization code for tokens"""
        code = urllib.parse.unquote(code)
        credentials = base64.b64encode(
            f"{self.app_key}:{self.app_secret}".encode()
        ).decode()
        
        headers = {
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.callback_url
        }
        
        response = requests.post(self.TOKEN_URL, headers=headers, data=data)
        
        if response.status_code != 200:
            logger.error(f"Token exchange failed: {response.text}")
            raise Exception(f"Token exchange failed: {response.status_code}")
        
        token_data = response.json()
        now = datetime.utcnow()
        expires_in = token_data.get('expires_in', 1800)
        refresh_expires_in = token_data.get('refresh_token_expires_in', 604800)
        
        tokens = {
            'AccessToken': token_data['access_token'],
            'RefreshToken': token_data['refresh_token'],
            'ExpiresIn': expires_in,
            'TokenType': token_data.get('token_type', 'Bearer'),
            'ExpiresAt': (now + timedelta(seconds=expires_in)).isoformat() + 'Z',
            'RefreshTokenExpiresAt': (now + timedelta(seconds=refresh_expires_in)).isoformat() + 'Z'
        }
        
        self._save_tokens(tokens)
        logger.info("Authorization successful")
    
    def _refresh_token(self):
        """Refresh the access token"""
        if not self.tokens.get('RefreshToken'):
            raise Exception("No refresh token available")
        
        credentials = base64.b64encode(
            f"{self.app_key}:{self.app_secret}".encode()
        ).decode()
        
        headers = {
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.tokens['RefreshToken']
        }
        
        response = requests.post(self.TOKEN_URL, headers=headers, data=data)
        
        if response.status_code != 200:
            logger.error(f"Token refresh failed: {response.text}")
            raise Exception("Token refresh failed")
        
        token_data = response.json()
        now = datetime.utcnow()
        expires_in = token_data.get('expires_in', 1800)
        
        self.tokens['AccessToken'] = token_data['access_token']
        self.tokens['ExpiresIn'] = expires_in
        self.tokens['ExpiresAt'] = (now + timedelta(seconds=expires_in)).isoformat() + 'Z'
        
        if 'refresh_token' in token_data:
            self.tokens['RefreshToken'] = token_data['refresh_token']
            refresh_expires_in = token_data.get('refresh_token_expires_in', 604800)
            self.tokens['RefreshTokenExpiresAt'] = (
                now + timedelta(seconds=refresh_expires_in)
            ).isoformat() + 'Z'
        
        self._save_tokens(self.tokens)
        logger.info("Token refreshed successfully")
    
    def _ensure_token(self):
        """Ensure we have a valid access token"""
        if not self.tokens.get('AccessToken'):
            logger.info("No access token, need authorization")
            self.authorize()
            return
        
        if self._is_token_expired():
            if self._is_refresh_token_expired():
                logger.info("Refresh token expired, need re-authorization")
                self.authorize()
            else:
                logger.info("Access token expired, refreshing")
                try:
                    self._refresh_token()
                except Exception as e:
                    logger.warning(f"Refresh failed: {e}, re-authorizing")
                    self.authorize()
    
    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make authenticated API request"""
        self._ensure_token()
        
        headers = {
            'Authorization': f'Bearer {self.tokens["AccessToken"]}',
            'Accept': 'application/json'
        }
        
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            response = self.session.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 401:
                logger.warning("Got 401, refreshing token and retrying")
                self._refresh_token()
                headers['Authorization'] = f'Bearer {self.tokens["AccessToken"]}'
                response = self.session.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"API error {response.status_code}: {response.text}")
                return None
            
            # Rate limiting
            time.sleep(self._rate_limit_delay)
            
            return response.json()
            
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout for {endpoint}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {e}")
            return None
    
    def _map_symbol(self, symbol: str) -> str:
        """Map symbol to Schwab format"""
        sym = symbol.upper().strip()
        return self.FUTURES_MAP.get(sym, sym)
    
    #############################################
    # PUBLIC API METHODS
    #############################################
    
    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get current quote for symbol
        
        Args:
            symbol: Stock or futures symbol
            
        Returns:
            Dict with 'last', 'bid', 'ask', 'high', 'low', 'open', 'close', 
            'volume', 'description' or None if error
        """
        schwab_symbol = self._map_symbol(symbol)
        data = self._request("/quotes", params={'symbols': schwab_symbol})
        
        if not data:
            return None
        
        quote_data = data.get(schwab_symbol, {}).get('quote', {})
        
        if not quote_data:
            # Try alternate key format
            for key in data:
                if key.replace('/', '') == schwab_symbol.replace('/', ''):
                    quote_data = data[key].get('quote', {})
                    break
        
        if not quote_data:
            logger.warning(f"No quote data for {schwab_symbol}")
            return None
        
        # DEBUG: Log raw quote data to diagnose price issues
        logger.debug(f"Raw quote data for {schwab_symbol}: lastPrice={quote_data.get('lastPrice')}, "
                    f"mark={quote_data.get('mark')}, closePrice={quote_data.get('closePrice')}, "
                    f"regularMarketLastPrice={quote_data.get('regularMarketLastPrice')}")
        
        price = (
            quote_data.get('lastPrice') or 
            quote_data.get('regularMarketLastPrice') or
            quote_data.get('mark') or 
            quote_data.get('closePrice', 0)
        )
        description = (
            quote_data.get('description') or 
            quote_data.get('shortName') or 
            quote_data.get('longName') or 
            symbol.upper()
        )
        
        return {
            'last': float(price) if price else 0,
            'bid': float(quote_data.get('bidPrice', 0)),
            'ask': float(quote_data.get('askPrice', 0)),
            'high': float(quote_data.get('highPrice', 0)),
            'low': float(quote_data.get('lowPrice', 0)),
            'open': float(quote_data.get('openPrice', 0)),
            'close': float(quote_data.get('closePrice', 0)),
            'volume': int(quote_data.get('totalVolume', 0)),
            'description': description,
            '52_week_high': float(quote_data.get('52WkHigh', 0)),
            '52_week_low': float(quote_data.get('52WkLow', 0)),
        }
    
    def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple symbols
        
        Args:
            symbols: List of stock symbols
            
        Returns:
            Dict mapping symbol to quote data
        """
        if not symbols:
            return {}
        
        # Schwab allows up to 500 symbols per request
        schwab_symbols = [self._map_symbol(s) for s in symbols]
        symbols_str = ','.join(schwab_symbols)
        
        data = self._request("/quotes", params={'symbols': symbols_str})
        
        if not data:
            return {}
        
        results = {}
        for orig_symbol, schwab_symbol in zip(symbols, schwab_symbols):
            quote_data = data.get(schwab_symbol, {}).get('quote', {})
            if quote_data:
                price = (
                    quote_data.get('lastPrice') or 
                    quote_data.get('mark') or 
                    quote_data.get('closePrice', 0)
                )
                results[orig_symbol] = {
                    'last': float(price) if price else 0,
                    'volume': int(quote_data.get('totalVolume', 0)),
                    'change_pct': float(quote_data.get('netPercentChangeInDouble', 0)),
                }
        
        return results
    
    def get_price_history(
        self, 
        symbol: str, 
        period_type: str = 'day', 
        period: int = 5, 
        frequency_type: str = 'minute', 
        frequency: int = 5,
        need_extended: bool = False
    ) -> Optional[pd.DataFrame]:
        """Get price history for symbol
        
        Args:
            symbol: Stock or futures symbol
            period_type: 'day', 'month', 'year'
            period: Number of periods
            frequency_type: 'minute', 'daily', 'weekly', 'monthly'
            frequency: Frequency value
            need_extended: Include extended hours data
            
        Returns:
            DataFrame with columns: datetime, open, high, low, close, volume
        """
        schwab_symbol = self._map_symbol(symbol)
        
        params = {
            'symbol': schwab_symbol,
            'periodType': period_type,
            'period': period,
            'frequencyType': frequency_type,
            'frequency': frequency,
            'needExtendedHoursData': str(need_extended).lower()
        }
        
        data = self._request("/pricehistory", params=params)
        
        if not data or 'candles' not in data:
            logger.warning(f"No price history for {schwab_symbol}")
            return None
        
        candles = data['candles']
        if not candles:
            return None
        
        df = pd.DataFrame(candles)
        df['datetime'] = pd.to_datetime(df['datetime'], unit='ms')
        df.columns = [c.lower() for c in df.columns]
        df = df.sort_values('datetime').reset_index(drop=True)
        
        logger.debug(f"Got {len(df)} candles for {schwab_symbol}")
        return df
    
    def get_daily_history(
        self, 
        symbol: str, 
        months: int = 12
    ) -> Optional[pd.DataFrame]:
        """Get daily price history
        
        Args:
            symbol: Stock symbol
            months: Number of months of history
            
        Returns:
            DataFrame with daily OHLCV data
        """
        period_type = 'month' if months <= 6 else 'year'
        period = months if months <= 6 else max(1, months // 12)
        
        return self.get_price_history(
            symbol, 
            period_type=period_type,
            period=period,
            frequency_type='daily',
            frequency=1,
            need_extended=False
        )
    
    def get_weekly_history(
        self, 
        symbol: str, 
        years: int = 1
    ) -> Optional[pd.DataFrame]:
        """Get weekly price history
        
        Args:
            symbol: Stock symbol
            years: Number of years of history
            
        Returns:
            DataFrame with weekly OHLCV data
        """
        return self.get_price_history(
            symbol,
            period_type='year',
            period=years,
            frequency_type='weekly',
            frequency=1,
            need_extended=False
        )
    
    def get_intraday_history(
        self,
        symbol: str,
        frequency: int = 5,
        days: int = 5
    ) -> Optional[pd.DataFrame]:
        """Get intraday price history
        
        Args:
            symbol: Stock symbol
            frequency: Minutes per bar (1, 5, 15, 30)
            days: Number of days
            
        Returns:
            DataFrame with intraday OHLCV data
        """
        return self.get_price_history(
            symbol,
            period_type='day',
            period=days,
            frequency_type='minute',
            frequency=frequency,
            need_extended=False
        )


#############################################
# MARKET DATA CLIENT WRAPPER
#############################################

class MarketDataClient:
    """High-level market data client wrapping SchwabClient"""
    
    def __init__(self, config_path: Optional[Path] = None):
        self.schwab = SchwabClient(config_path)
    
    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get current quote"""
        return self.schwab.get_quote(symbol)
    
    def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple symbols"""
        return self.schwab.get_quotes(symbols)
    
    def get_daily(self, symbol: str, months: int = 12) -> Optional[pd.DataFrame]:
        """Get daily OHLCV data"""
        return self.schwab.get_daily_history(symbol, months)
    
    def get_weekly(self, symbol: str, years: int = 1) -> Optional[pd.DataFrame]:
        """Get weekly OHLCV data"""
        return self.schwab.get_weekly_history(symbol, years)
    
    def get_intraday(
        self, 
        symbol: str, 
        frequency: int = 5, 
        days: int = 5
    ) -> Optional[pd.DataFrame]:
        """Get intraday OHLCV data"""
        return self.schwab.get_intraday_history(symbol, frequency, days)
    
    def get_multi_timeframe(
        self, 
        symbol: str
    ) -> Dict[str, Optional[pd.DataFrame]]:
        """Get data for all standard timeframes
        
        Returns:
            Dict mapping timeframe name to DataFrame
        """
        data = {}
        
        # Daily
        data['daily'] = self.get_daily(symbol, months=12)
        time.sleep(0.2)
        
        # 60min (use 30min and aggregate)
        df_30m = self.schwab.get_price_history(
            symbol, 'day', 10, 'minute', 30
        )
        if df_30m is not None and len(df_30m) >= 2:
            data['60min'] = self._aggregate_bars(df_30m, 2)
        else:
            data['60min'] = None
        time.sleep(0.2)
        
        # 15min
        data['15min'] = self.schwab.get_price_history(
            symbol, 'day', 10, 'minute', 15
        )
        time.sleep(0.2)
        
        # 5min
        data['5min'] = self.schwab.get_price_history(
            symbol, 'day', 10, 'minute', 5
        )
        time.sleep(0.2)
        
        # 1min
        data['1min'] = self.schwab.get_price_history(
            symbol, 'day', 2, 'minute', 1
        )
        
        return data
    
    def _aggregate_bars(self, df: pd.DataFrame, factor: int) -> pd.DataFrame:
        """Aggregate bars by a factor"""
        if df is None or len(df) < factor:
            return df
        
        df = df.copy()
        df['group'] = df.index // factor
        
        agg = df.groupby('group').agg({
            'datetime': 'first',
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).reset_index(drop=True)
        
        return agg
