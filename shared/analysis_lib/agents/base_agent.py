"""
Base Agent Class for AI-Powered Stock Screening
================================================
Version: 1.0.0

Provides the foundation for all screening agents with:
- Standardized candidate output format
- Priority scoring system
- Integration with Blueprint Analyzer
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum
import threading
import queue


class CandidatePriority(Enum):
    """Priority levels for candidates"""
    CRITICAL = 1    # Immediate attention required
    HIGH = 2        # Strong opportunity
    MEDIUM = 3      # Worth reviewing
    LOW = 4         # Monitor only


class CandidateSource(Enum):
    """Source agent that generated the candidate"""
    NEWS_CATALYST = "News Catalyst"
    EARNINGS_SURPRISE = "Earnings Surprise"
    EARNINGS_CALENDAR = "Earnings Calendar"
    SECTOR_ROTATION = "Sector Rotation"
    TECHNICAL_PATTERN = "Technical Pattern"
    OPTIONS_FLOW = "Options Flow"
    VOLUME_ANOMALY = "Volume Anomaly"
    INSIDER_ACTIVITY = "Insider Activity"


class TradingStyleSuggestion(Enum):
    """Suggested trading style for the candidate"""
    SWING = "Swing"
    MOMENTUM = "Momentum"
    BUY_HOLD = "Buy & Hold"
    SPECULATIVE = "Speculative"


@dataclass
class AgentCandidate:
    """
    Standardized candidate output from any agent.
    This is the data structure that flows into the analysis framework.
    """
    # Core identification
    symbol: str
    timestamp: datetime
    source: CandidateSource
    priority: CandidatePriority
    
    # Scoring
    score: float = 0.0  # 0-100 composite score
    
    # Context
    headline: str = ""
    summary: str = ""
    catalyst_type: str = ""
    
    # Market data (populated by agent)
    current_price: float = 0.0
    change_pct: float = 0.0
    volume_ratio: float = 1.0  # vs 20-day average
    
    # Sector/Industry context
    sector: str = ""
    sector_rank: int = 0
    industry: str = ""
    industry_rank: int = 0
    
    # Suggested actions
    suggested_style: TradingStyleSuggestion = TradingStyleSuggestion.SWING
    action_notes: str = ""
    
    # Agent-specific data
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Status tracking
    status: str = "pending"  # pending, analyzed, traded, dismissed
    analyzed_at: Optional[datetime] = None
    
    def __post_init__(self):
        """Ensure timestamp is datetime"""
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)
    
    @property
    def priority_label(self) -> str:
        """Human-readable priority"""
        labels = {
            CandidatePriority.CRITICAL: "🔴 CRITICAL",
            CandidatePriority.HIGH: "🟠 HIGH",
            CandidatePriority.MEDIUM: "🟡 MEDIUM",
            CandidatePriority.LOW: "🟢 LOW"
        }
        return labels.get(self.priority, "UNKNOWN")
    
    @property
    def age_minutes(self) -> float:
        """Minutes since candidate was generated"""
        return (datetime.now() - self.timestamp).total_seconds() / 60
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/display"""
        return {
            'symbol': self.symbol,
            'timestamp': self.timestamp.isoformat(),
            'source': self.source.value,
            'priority': self.priority.name,
            'score': self.score,
            'headline': self.headline,
            'summary': self.summary,
            'catalyst_type': self.catalyst_type,
            'current_price': self.current_price,
            'change_pct': self.change_pct,
            'volume_ratio': self.volume_ratio,
            'sector': self.sector,
            'sector_rank': self.sector_rank,
            'industry': self.industry,
            'suggested_style': self.suggested_style.value,
            'action_notes': self.action_notes,
            'status': self.status,
            'metadata': self.metadata,
        }


class BaseAgent:
    """
    Base class for all screening agents.
    
    Each agent runs autonomously and generates AgentCandidate objects
    that are fed into the candidate queue for analysis.
    """
    
    def __init__(self, name: str, finviz_processor=None):
        """
        Initialize agent.
        
        Args:
            name: Agent identifier
            finviz_processor: Optional FinvizDataProcessor instance
        """
        self.name = name
        self.finviz = finviz_processor
        self.is_running = False
        self.last_run: Optional[datetime] = None
        self.candidates: List[AgentCandidate] = []
        self.errors: List[str] = []
        
        # Threading support
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._candidate_queue = queue.Queue()
        
        # Callbacks
        self._on_candidate_callback = None
        self._on_error_callback = None
    
    def set_on_candidate(self, callback):
        """Set callback for when new candidate is found"""
        self._on_candidate_callback = callback
    
    def set_on_error(self, callback):
        """Set callback for errors"""
        self._on_error_callback = callback
    
    def run_once(self) -> List[AgentCandidate]:
        """
        Execute single scan cycle.
        Override in subclass.
        
        Returns:
            List of AgentCandidate objects
        """
        raise NotImplementedError("Subclasses must implement run_once()")
    
    def start(self, interval_seconds: int = 300):
        """
        Start agent in background thread with periodic scanning.
        
        Args:
            interval_seconds: Seconds between scans (default 5 minutes)
        """
        if self.is_running:
            return
        
        self._stop_event.clear()
        self.is_running = True
        
        def run_loop():
            while not self._stop_event.is_set():
                try:
                    new_candidates = self.run_once()
                    self.last_run = datetime.now()
                    
                    for candidate in new_candidates:
                        self.candidates.append(candidate)
                        self._candidate_queue.put(candidate)
                        
                        if self._on_candidate_callback:
                            self._on_candidate_callback(candidate)
                    
                except Exception as e:
                    error_msg = f"[{self.name}] Error: {str(e)}"
                    self.errors.append(error_msg)
                    if self._on_error_callback:
                        self._on_error_callback(error_msg)
                
                # Wait for next cycle or stop signal
                self._stop_event.wait(timeout=interval_seconds)
            
            self.is_running = False
        
        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the background agent"""
        self._stop_event.set()
        self.is_running = False
    
    def get_pending_candidates(self) -> List[AgentCandidate]:
        """Get all pending candidates from queue"""
        candidates = []
        while not self._candidate_queue.empty():
            try:
                candidates.append(self._candidate_queue.get_nowait())
            except queue.Empty:
                break
        return candidates
    
    def get_recent_candidates(self, minutes: int = 60) -> List[AgentCandidate]:
        """Get candidates from the last N minutes"""
        cutoff = datetime.now()
        return [c for c in self.candidates if c.age_minutes <= minutes]
    
    def clear_candidates(self):
        """Clear all stored candidates"""
        self.candidates = []
        while not self._candidate_queue.empty():
            try:
                self._candidate_queue.get_nowait()
            except queue.Empty:
                break
    
    def get_status(self) -> Dict[str, Any]:
        """Get agent status for display"""
        return {
            'name': self.name,
            'is_running': self.is_running,
            'last_run': self.last_run.isoformat() if self.last_run else None,
            'candidate_count': len(self.candidates),
            'pending_count': self._candidate_queue.qsize(),
            'error_count': len(self.errors),
            'last_error': self.errors[-1] if self.errors else None,
        }
