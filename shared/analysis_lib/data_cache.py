"""
Data Cache System - SQLite-based caching for market data
Version: 1.0.0

Provides caching layer for API calls to reduce requests and enable offline mode.
Supports configurable TTL (time-to-live) for different data types.
"""

import sqlite3
import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Cache configuration - TTL in minutes
CACHE_TTL = {
    'quote': 5,              # Real-time quotes: 5 minutes
    'daily': 60 * 12,        # Daily price data: 12 hours
    'weekly': 60 * 24,       # Weekly data: 24 hours
    'monthly': 60 * 24 * 7,  # Monthly data: 1 week
    'breadth': 60 * 4,       # Breadth data: 4 hours (expensive to calculate)
    'yield_curve': 60 * 4,   # Yield curve: 4 hours
    'earnings': 60 * 24,     # Earnings calendar: 24 hours
    'industry': 60 * 6,      # Industry data: 6 hours
    'sector': 60 * 2,        # Sector data: 2 hours
    'fundamentals': 60 * 24, # Fundamentals: 24 hours
}


class DataCache:
    """SQLite-based data cache with TTL support"""

    def __init__(self, db_path: str = None):
        """Initialize cache database

        Args:
            db_path: Path to SQLite database file
        """
        if db_path is None:
            cache_dir = Path(__file__).parent.parent / 'cache'
            cache_dir.mkdir(exist_ok=True)
            db_path = cache_dir / 'market_data.db'

        self.db_path = str(db_path)
        self._init_database()

    def _init_database(self):
        """Create database tables if they don't exist"""
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires_at)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_type ON cache(data_type)
            ''')

            # Alerts table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    target_value REAL,
                    current_value REAL,
                    triggered BOOLEAN DEFAULT 0,
                    triggered_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol)
            ''')

            # Analysis history table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS analysis_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    analysis_date DATE NOT NULL,
                    total_score REAL,
                    recommendation TEXT,
                    factors_json TEXT,
                    setup_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_history_symbol ON analysis_history(symbol, analysis_date)
            ''')

            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _make_key(self, data_type: str, identifier: str, **kwargs) -> str:
        """Create a unique cache key"""
        key_parts = [data_type, identifier]
        if kwargs:
            key_parts.append(hashlib.md5(json.dumps(kwargs, sort_keys=True).encode()).hexdigest()[:8])
        return ':'.join(key_parts)

    def get(self, data_type: str, identifier: str, **kwargs) -> Optional[Any]:
        """Get data from cache if not expired

        Args:
            data_type: Type of data (quote, daily, breadth, etc.)
            identifier: Unique identifier (symbol, date, etc.)
            **kwargs: Additional parameters for key generation

        Returns:
            Cached data or None if not found/expired
        """
        key = self._make_key(data_type, identifier, **kwargs)

        with self._get_connection() as conn:
            cursor = conn.execute(
                'SELECT data FROM cache WHERE key = ? AND expires_at > ?',
                (key, datetime.now())
            )
            row = cursor.fetchone()

            if row:
                try:
                    return json.loads(row['data'])
                except json.JSONDecodeError:
                    return row['data']

        return None

    def set(self, data_type: str, identifier: str, data: Any, ttl_minutes: int = None, **kwargs):
        """Store data in cache

        Args:
            data_type: Type of data
            identifier: Unique identifier
            data: Data to cache (will be JSON serialized)
            ttl_minutes: Time-to-live in minutes (uses default if None)
            **kwargs: Additional parameters for key generation
        """
        key = self._make_key(data_type, identifier, **kwargs)

        if ttl_minutes is None:
            ttl_minutes = CACHE_TTL.get(data_type, 60)

        expires_at = datetime.now() + timedelta(minutes=ttl_minutes)

        # Serialize data
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data)
        else:
            data_str = str(data)

        with self._get_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO cache (key, data, data_type, expires_at)
                VALUES (?, ?, ?, ?)
            ''', (key, data_str, data_type, expires_at))
            conn.commit()

    def invalidate(self, data_type: str = None, identifier: str = None):
        """Invalidate cache entries

        Args:
            data_type: Invalidate all entries of this type (optional)
            identifier: Invalidate specific identifier (optional)
        """
        with self._get_connection() as conn:
            if data_type and identifier:
                key = self._make_key(data_type, identifier)
                conn.execute('DELETE FROM cache WHERE key LIKE ?', (f'{key}%',))
            elif data_type:
                conn.execute('DELETE FROM cache WHERE data_type = ?', (data_type,))
            else:
                conn.execute('DELETE FROM cache WHERE expires_at < ?', (datetime.now(),))
            conn.commit()

    def clear_all(self):
        """Clear entire cache"""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM cache')
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT data_type, COUNT(*) as count,
                       SUM(CASE WHEN expires_at > datetime('now') THEN 1 ELSE 0 END) as valid
                FROM cache GROUP BY data_type
            ''')

            stats = {'types': {}}
            for row in cursor:
                stats['types'][row['data_type']] = {
                    'total': row['count'],
                    'valid': row['valid']
                }

            cursor = conn.execute('SELECT COUNT(*) as total FROM cache')
            stats['total_entries'] = cursor.fetchone()['total']

            return stats

    # Alert methods
    def add_alert(self, symbol: str, alert_type: str, condition: str,
                  target_value: float, notes: str = None) -> int:
        """Add a new alert

        Args:
            symbol: Stock symbol
            alert_type: Type of alert (price_above, price_below, rsi_above, etc.)
            condition: Human-readable condition description
            target_value: Target value to trigger alert
            notes: Optional notes

        Returns:
            Alert ID
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                INSERT INTO alerts (symbol, alert_type, condition, target_value, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (symbol.upper(), alert_type, condition, target_value, notes))
            conn.commit()
            return cursor.lastrowid

    def get_alerts(self, symbol: str = None, triggered: bool = None) -> List[Dict]:
        """Get alerts, optionally filtered

        Args:
            symbol: Filter by symbol
            triggered: Filter by triggered status

        Returns:
            List of alert dictionaries
        """
        query = 'SELECT * FROM alerts WHERE 1=1'
        params = []

        if symbol:
            query += ' AND symbol = ?'
            params.append(symbol.upper())

        if triggered is not None:
            query += ' AND triggered = ?'
            params.append(1 if triggered else 0)

        query += ' ORDER BY created_at DESC'

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_alert(self, alert_id: int, current_value: float = None,
                     triggered: bool = None):
        """Update an alert"""
        with self._get_connection() as conn:
            if triggered:
                conn.execute('''
                    UPDATE alerts SET triggered = 1, triggered_at = ?, current_value = ?
                    WHERE id = ?
                ''', (datetime.now(), current_value, alert_id))
            elif current_value is not None:
                conn.execute('''
                    UPDATE alerts SET current_value = ? WHERE id = ?
                ''', (current_value, alert_id))
            conn.commit()

    def delete_alert(self, alert_id: int):
        """Delete an alert"""
        with self._get_connection() as conn:
            conn.execute('DELETE FROM alerts WHERE id = ?', (alert_id,))
            conn.commit()

    # Analysis history methods
    def save_analysis(self, symbol: str, total_score: float, recommendation: str,
                      factors: Dict, setup: Dict = None):
        """Save analysis to history"""
        with self._get_connection() as conn:
            conn.execute('''
                INSERT INTO analysis_history
                (symbol, analysis_date, total_score, recommendation, factors_json, setup_json)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                symbol.upper(),
                datetime.now().date(),
                total_score,
                recommendation,
                json.dumps(factors),
                json.dumps(setup) if setup else None
            ))
            conn.commit()

    def get_analysis_history(self, symbol: str, days: int = 30) -> List[Dict]:
        """Get analysis history for a symbol"""
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT * FROM analysis_history
                WHERE symbol = ? AND analysis_date >= date('now', ?)
                ORDER BY analysis_date DESC
            ''', (symbol.upper(), f'-{days} days'))
            return [dict(row) for row in cursor.fetchall()]


# Global cache instance
_cache_instance = None

def get_cache() -> DataCache:
    """Get global cache instance"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = DataCache()
    return _cache_instance
