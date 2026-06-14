"""
AI Agents for Autonomous Idea Generation
=========================================
Version: 1.0.0

This module provides autonomous screening agents that monitor markets
and feed qualified candidates into the Blueprint Analyzer.

Agents:
- NewsEarningsAgent: Monitors news and earnings for trading opportunities
- (Future) SectorRotationAgent
- (Future) TechnicalPatternAgent
- (Future) OptionsFlowAgent
- (Future) VolumeAnomalyAgent
"""

from .base_agent import BaseAgent, AgentCandidate, CandidatePriority, CandidateSource, TradingStyleSuggestion
from .news_earnings_agent import NewsEarningsAgent
from .agent_dashboard import AgentDashboardPopup

__all__ = [
    'BaseAgent',
    'AgentCandidate', 
    'CandidatePriority',
    'CandidateSource',
    'TradingStyleSuggestion',
    'NewsEarningsAgent',
    'AgentDashboardPopup',
]
