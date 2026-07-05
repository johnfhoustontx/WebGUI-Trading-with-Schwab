"""
Alerts System - Price and condition-based alerts
Version: 1.0.0

Provides alert management for price levels, technical conditions, and events.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass
from enum import Enum

from data_cache import get_cache

logger = logging.getLogger(__name__)


class AlertType(Enum):
    """Types of alerts"""
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PRICE_CROSS_ABOVE = "price_cross_above"
    PRICE_CROSS_BELOW = "price_cross_below"
    RSI_ABOVE = "rsi_above"
    RSI_BELOW = "rsi_below"
    VOLUME_SPIKE = "volume_spike"
    NEW_HIGH = "new_high"
    NEW_LOW = "new_low"
    EARNINGS_SOON = "earnings_soon"
    SECTOR_CHANGE = "sector_change"
    SCORE_CHANGE = "score_change"
    STOP_HIT = "stop_hit"
    TARGET_HIT = "target_hit"


@dataclass
class Alert:
    """Alert definition"""
    id: int
    symbol: str
    alert_type: AlertType
    condition: str
    target_value: float
    current_value: Optional[float] = None
    triggered: bool = False
    triggered_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    notes: Optional[str] = None


@dataclass
class AlertResult:
    """Result of alert check"""
    alert: Alert
    triggered: bool
    message: str
    current_value: float


class AlertManager:
    """Manage alerts and check conditions"""

    def __init__(self):
        """Initialize alert manager"""
        self.cache = get_cache()
        self._callbacks: List[Callable[[AlertResult], None]] = []

    def add_callback(self, callback: Callable[[AlertResult], None]):
        """Add callback for triggered alerts"""
        self._callbacks.append(callback)

    def create_alert(
        self,
        symbol: str,
        alert_type: AlertType,
        target_value: float,
        notes: str = None
    ) -> int:
        """Create a new alert

        Args:
            symbol: Stock symbol
            alert_type: Type of alert
            target_value: Target value to trigger
            notes: Optional notes

        Returns:
            Alert ID
        """
        condition = self._format_condition(alert_type, symbol, target_value)

        alert_id = self.cache.add_alert(
            symbol=symbol,
            alert_type=alert_type.value,
            condition=condition,
            target_value=target_value,
            notes=notes
        )

        logger.info(f"Created alert {alert_id}: {condition}")
        return alert_id

    def _format_condition(self, alert_type: AlertType, symbol: str, target: float) -> str:
        """Format human-readable condition"""
        formats = {
            AlertType.PRICE_ABOVE: f"{symbol} price crosses above ${target:.2f}",
            AlertType.PRICE_BELOW: f"{symbol} price crosses below ${target:.2f}",
            AlertType.PRICE_CROSS_ABOVE: f"{symbol} price crosses above ${target:.2f}",
            AlertType.PRICE_CROSS_BELOW: f"{symbol} price crosses below ${target:.2f}",
            AlertType.RSI_ABOVE: f"{symbol} RSI goes above {target:.0f}",
            AlertType.RSI_BELOW: f"{symbol} RSI goes below {target:.0f}",
            AlertType.VOLUME_SPIKE: f"{symbol} volume exceeds {target:.0f}x average",
            AlertType.NEW_HIGH: f"{symbol} makes new {int(target)}-day high",
            AlertType.NEW_LOW: f"{symbol} makes new {int(target)}-day low",
            AlertType.EARNINGS_SOON: f"{symbol} earnings within {int(target)} days",
            AlertType.STOP_HIT: f"{symbol} stop loss hit at ${target:.2f}",
            AlertType.TARGET_HIT: f"{symbol} target hit at ${target:.2f}",
        }
        return formats.get(alert_type, f"{symbol} {alert_type.value} at {target}")

    def get_alerts(self, symbol: str = None, active_only: bool = True) -> List[Alert]:
        """Get alerts

        Args:
            symbol: Filter by symbol (optional)
            active_only: Only return non-triggered alerts

        Returns:
            List of Alert objects
        """
        triggered = False if active_only else None
        raw_alerts = self.cache.get_alerts(symbol=symbol, triggered=triggered)

        alerts = []
        for row in raw_alerts:
            try:
                alerts.append(Alert(
                    id=row['id'],
                    symbol=row['symbol'],
                    alert_type=AlertType(row['alert_type']),
                    condition=row['condition'],
                    target_value=row['target_value'],
                    current_value=row['current_value'],
                    triggered=bool(row['triggered']),
                    triggered_at=datetime.fromisoformat(row['triggered_at']) if row['triggered_at'] else None,
                    created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
                    notes=row['notes']
                ))
            except Exception as e:
                logger.warning(f"Error parsing alert: {e}")
                continue

        return alerts

    def delete_alert(self, alert_id: int):
        """Delete an alert"""
        self.cache.delete_alert(alert_id)
        logger.info(f"Deleted alert {alert_id}")

    def check_alerts(self, market_data: Dict[str, Dict]) -> List[AlertResult]:
        """Check all active alerts against current market data

        Args:
            market_data: Dict mapping symbol to quote data
                         Each quote should have: last, high, low, volume, avg_volume

        Returns:
            List of AlertResult for triggered alerts
        """
        results = []
        alerts = self.get_alerts(active_only=True)

        for alert in alerts:
            quote = market_data.get(alert.symbol.upper())
            if not quote:
                continue

            result = self._check_alert(alert, quote)
            if result:
                results.append(result)

                # Update in database
                self.cache.update_alert(
                    alert.id,
                    current_value=result.current_value,
                    triggered=result.triggered
                )

                # Notify callbacks
                if result.triggered:
                    for callback in self._callbacks:
                        try:
                            callback(result)
                        except Exception as e:
                            logger.error(f"Alert callback error: {e}")

        return results

    def _check_alert(self, alert: Alert, quote: Dict) -> Optional[AlertResult]:
        """Check a single alert against quote data"""
        current = quote.get('last', 0)
        high = quote.get('high', current)
        low = quote.get('low', current)
        volume = quote.get('volume', 0)
        avg_volume = quote.get('avg_volume', 1)

        triggered = False
        message = ""

        if alert.alert_type == AlertType.PRICE_ABOVE:
            triggered = current >= alert.target_value
            message = f"{alert.symbol} at ${current:.2f} (target: ${alert.target_value:.2f})"

        elif alert.alert_type == AlertType.PRICE_BELOW:
            triggered = current <= alert.target_value
            message = f"{alert.symbol} at ${current:.2f} (target: ${alert.target_value:.2f})"

        elif alert.alert_type == AlertType.PRICE_CROSS_ABOVE:
            triggered = high >= alert.target_value and (alert.current_value or 0) < alert.target_value
            message = f"{alert.symbol} crossed above ${alert.target_value:.2f}"

        elif alert.alert_type == AlertType.PRICE_CROSS_BELOW:
            triggered = low <= alert.target_value and (alert.current_value or float('inf')) > alert.target_value
            message = f"{alert.symbol} crossed below ${alert.target_value:.2f}"

        elif alert.alert_type == AlertType.VOLUME_SPIKE:
            volume_ratio = volume / max(avg_volume, 1)
            triggered = volume_ratio >= alert.target_value
            message = f"{alert.symbol} volume {volume_ratio:.1f}x average"
            current = volume_ratio

        elif alert.alert_type == AlertType.STOP_HIT:
            triggered = low <= alert.target_value
            message = f"{alert.symbol} STOP HIT at ${alert.target_value:.2f} (low: ${low:.2f})"

        elif alert.alert_type == AlertType.TARGET_HIT:
            triggered = high >= alert.target_value
            message = f"{alert.symbol} TARGET HIT at ${alert.target_value:.2f} (high: ${high:.2f})"

        if triggered or alert.current_value != current:
            return AlertResult(
                alert=alert,
                triggered=triggered,
                message=message,
                current_value=current
            )

        return None

    def check_rsi_alerts(self, symbol: str, rsi: float) -> List[AlertResult]:
        """Check RSI-based alerts for a symbol

        Args:
            symbol: Stock symbol
            rsi: Current RSI value

        Returns:
            List of triggered AlertResults
        """
        results = []
        alerts = self.get_alerts(symbol=symbol, active_only=True)

        for alert in alerts:
            if alert.alert_type == AlertType.RSI_ABOVE:
                if rsi >= alert.target_value:
                    result = AlertResult(
                        alert=alert,
                        triggered=True,
                        message=f"{symbol} RSI at {rsi:.1f} (above {alert.target_value:.0f})",
                        current_value=rsi
                    )
                    results.append(result)
                    self.cache.update_alert(alert.id, current_value=rsi, triggered=True)

            elif alert.alert_type == AlertType.RSI_BELOW:
                if rsi <= alert.target_value:
                    result = AlertResult(
                        alert=alert,
                        triggered=True,
                        message=f"{symbol} RSI at {rsi:.1f} (below {alert.target_value:.0f})",
                        current_value=rsi
                    )
                    results.append(result)
                    self.cache.update_alert(alert.id, current_value=rsi, triggered=True)

        return results

    def create_trade_alerts(
        self,
        symbol: str,
        entry: float,
        stop: float,
        target: float
    ) -> Dict[str, int]:
        """Create entry, stop, and target alerts for a trade

        Args:
            symbol: Stock symbol
            entry: Entry price
            stop: Stop loss price
            target: Target price

        Returns:
            Dict mapping alert type to alert ID
        """
        alerts = {}

        # Entry alert (price crosses to entry level)
        if entry > stop:  # Long trade
            alerts['entry'] = self.create_alert(
                symbol, AlertType.PRICE_CROSS_BELOW, entry,
                notes="Entry zone for long trade"
            )
        else:  # Short trade
            alerts['entry'] = self.create_alert(
                symbol, AlertType.PRICE_CROSS_ABOVE, entry,
                notes="Entry zone for short trade"
            )

        # Stop alert
        alerts['stop'] = self.create_alert(
            symbol, AlertType.STOP_HIT, stop,
            notes=f"Stop loss for trade entered at ${entry:.2f}"
        )

        # Target alert
        alerts['target'] = self.create_alert(
            symbol, AlertType.TARGET_HIT, target,
            notes=f"Target for trade entered at ${entry:.2f}"
        )

        logger.info(f"Created trade alerts for {symbol}: entry=${entry:.2f}, stop=${stop:.2f}, target=${target:.2f}")
        return alerts


# Global instance
_alert_manager = None

def get_alert_manager() -> AlertManager:
    """Get global AlertManager instance"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager
