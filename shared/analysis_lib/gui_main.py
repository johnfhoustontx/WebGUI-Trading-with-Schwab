"""
Blueprint Analyzer GUI - Feature Enhancements (v1.7.0)
Version: 1.7.0
Changes:
- ADDED: AI Agents for autonomous idea generation (News & Earnings POC)
- ADDED: Agent Dashboard popup with candidate management
- ADDED: News scanning with catalyst detection
- ADDED: Earnings surprise monitoring
- ADDED: Top gainers/losers scanning
- ADDED: Ticker-specific news lookup
- ADDED: Priority-based candidate scoring
- ADDED: Direct integration to send candidates to analysis

Previous (v1.6.1):
- Position Sizer popup with tk widgets
- Dynamic scrollbar (auto show/hide)
- Chart buttons open Finviz in browser
- Scrolling news ticker (updates every 5 minutes)
- Finviz integration for stock info
- Top 10 industry stocks from Finviz
- 8-factor stock scoring system
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import logging
from typing import Optional
import threading
import requests
from io import BytesIO
import time

# Try to import PIL for chart display
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL not available - Chart display disabled. Install with: pip install Pillow")

from market_data import MarketDataProvider
from macro_score import MacroScoreCalculator
from sector_analysis import SectorRanker, get_sector_info
from blueprint_scorer import BlueprintScorer, TradingStyle, Recommendation
from position_sizer import PositionSizer
from config import PORTFOLIO

# Agent Dashboard
try:
    from agents.agent_dashboard import AgentDashboardPopup
    AGENTS_AVAILABLE = True
except ImportError:
    AGENTS_AVAILABLE = False
    print("⚠️  Agent Dashboard not available - run from project root")

# Finviz integration
import sys
import pandas as pd
import re
# finviz_processor is an optional external integration that was not migrated into
# the monorepo; the try/except below disables Finviz features gracefully if absent.
try:
    from finviz_processor import FinvizDataProcessor
    FINVIZ_AVAILABLE = True
except ImportError:
    FINVIZ_AVAILABLE = False
    print("⚠️  Finviz processor not found - Finviz features disabled")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

#############################################
# GUI CONFIGURATION
#############################################

# Window dimensions
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 850
LEFT_PANEL_WIDTH = 340

# Color scheme - Windows-style
COLORS = {
    'bg_main': '#f0f0f0',
    'bg_panel': '#ffffff',
    'bg_button': '#e1e1e1',
    'bg_button_hover': '#d0d0d0',
    'bg_entry': '#ffffff',
    'fg_text': '#000000',
    'border': '#a0a0a0',
    'accent': '#0078d4',
    'ticker_bg': '#1a1a2e',
    'ticker_fg': '#00ff88',
}

# Dark output theme
OUTPUT_COLORS = {
    'bg': '#1e1e1e',
    'fg': '#d4d4d4',
    'header': '#4ec9b0',
    'strong_buy': '#00ff00',
    'buy': '#90ee90',
    'speculative': '#ffff00',
    'avoid': '#ff4500',
    'warning': '#ff6347',
    'section': '#569cd6',
    'factor': '#9cdcfe',
    'value': '#ce9178',
}

# Trading styles
STYLES = {
    'Swing': TradingStyle.SWING,
    'Momentum': TradingStyle.MOMENTUM,
    'Buy & Hold': TradingStyle.BUY_HOLD,
    'Speculative': TradingStyle.SPECULATIVE,
}

# Portfolio allocations
ALLOCATIONS = {
    'swing': PORTFOLIO.swing_capital,
    'momentum': PORTFOLIO.momentum_capital,
    'speculative': PORTFOLIO.speculative_capital,
    'buy_hold': PORTFOLIO.buy_hold_capital,
}

# News update interval (5 minutes in milliseconds)
NEWS_UPDATE_INTERVAL = 5 * 60 * 1000

#############################################
# MAIN APPLICATION
#############################################

class BlueprintAnalyzerGUI:
    """Main GUI application for Blueprint Analyzer"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Blueprint Analyzer v1.7.0")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.configure(bg=COLORS['bg_main'])
        
        # Initialize components
        self.client = None
        self.macro = None
        self.sectors = None
        self.scorer = None
        self.sizer = None
        self.finviz = None
        
        self.processing = False
        self.current_symbol = ""  # Track current symbol for chart buttons
        self.news_items = []  # Store news for ticker
        self.news_position = 0  # Current position in news ticker
        self.ticker_after_id = None  # For cancelling ticker animation
        self.news_update_after_id = None  # For cancelling news updates
        
        # Position sizer popup window reference
        self.position_sizer_window = None
        
        # Agent dashboard popup window reference
        self.agent_dashboard = None
        
        # Position sizer variables (used by popup)
        self.entry_price_var = tk.StringVar()
        self.stop_price_var = tk.StringVar()
        self.atr_var = tk.StringVar()
        self.sizer_style_var = tk.StringVar(value='swing')
        
        # Create UI
        self._setup_styles()
        self._create_menu()
        self._create_layout()
        self._create_news_ticker()
        self._create_status_bar()
        
        # Initialize backend in background
        self.root.after(100, self._initialize_backend)
        
        # Start news ticker updates
        self.root.after(2000, self._start_news_ticker)
    
    def _setup_styles(self):
        """Configure ttk styles"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # Button style
        style.configure(
            'TButton',
            background=COLORS['bg_button'],
            foreground=COLORS['fg_text'],
            bordercolor=COLORS['border'],
            focuscolor='none',
            lightcolor=COLORS['bg_button'],
            darkcolor=COLORS['bg_button'],
            padding=6
        )
        style.map('TButton',
            background=[('active', COLORS['bg_button_hover'])])
        
        # Primary button (accent)
        style.configure(
            'Accent.TButton',
            background=COLORS['accent'],
            foreground='white',
            bordercolor=COLORS['accent'],
            padding=8
        )
        style.map('Accent.TButton',
            background=[('active', '#005a9e')])
        
        # Chart button style
        style.configure(
            'Chart.TButton',
            background='#2d5a27',
            foreground='white',
            bordercolor='#1e3d1a',
            padding=4
        )
        style.map('Chart.TButton',
            background=[('active', '#3d7a37')])
        
        # Entry style
        style.configure(
            'TEntry',
            fieldbackground=COLORS['bg_entry'],
            background=COLORS['bg_entry'],
            foreground=COLORS['fg_text'],
            bordercolor=COLORS['border'],
            lightcolor=COLORS['bg_entry'],
            darkcolor=COLORS['bg_entry'],
            insertcolor=COLORS['fg_text']
        )
        
        # Combobox style
        style.configure(
            'TCombobox',
            fieldbackground=COLORS['bg_entry'],
            background=COLORS['bg_entry'],
            foreground=COLORS['fg_text'],
            bordercolor=COLORS['border'],
            arrowcolor=COLORS['fg_text'],
            selectbackground=COLORS['accent'],
            selectforeground='white'
        )
        
        # Label style
        style.configure(
            'TLabel',
            background=COLORS['bg_panel'],
            foreground=COLORS['fg_text']
        )
        
        # Frame style
        style.configure(
            'TFrame',
            background=COLORS['bg_panel']
        )
    
    def _create_menu(self):
        """Create menu bar"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Refresh Data", command=self._refresh_data)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        if AGENTS_AVAILABLE:
            tools_menu.add_command(label="🤖 AI Agents", command=self._open_agent_dashboard)
            tools_menu.add_separator()
        tools_menu.add_command(label="Position Sizer", command=self._open_position_sizer)
        tools_menu.add_separator()
        tools_menu.add_command(label="Update Macro Score", command=self._update_macro)
        tools_menu.add_command(label="Rank Sectors", command=self._rank_sectors)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self._show_about)
    
    def _create_layout(self):
        """Create main layout with dynamic scrollbar on left panel"""
        # Main container
        self.main_container = ttk.Frame(self.root)
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # LEFT PANEL - Fixed width with dynamic scrollbar
        self.left_frame = ttk.Frame(self.main_container, width=LEFT_PANEL_WIDTH)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        self.left_frame.pack_propagate(False)
        
        # Create canvas and scrollbar for left panel
        self.left_canvas = tk.Canvas(self.left_frame, bg=COLORS['bg_panel'], highlightthickness=0)
        self.left_scrollbar = ttk.Scrollbar(self.left_frame, orient=tk.VERTICAL, command=self.left_canvas.yview)
        self.scrollable_frame = ttk.Frame(self.left_canvas)
        
        # Configure canvas scrolling
        self.scrollable_frame.bind("<Configure>", self._on_frame_configure)
        self.left_canvas.bind("<Configure>", self._on_canvas_configure)
        
        self.canvas_window = self.left_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.left_canvas.configure(yscrollcommand=self.left_scrollbar.set)
        
        # Pack canvas (scrollbar packed dynamically)
        self.left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Enable mouse wheel scrolling only when scrollbar is visible
        def _on_mousewheel(event):
            if self.left_scrollbar.winfo_ismapped():
                self.left_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.left_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        # SECTIONS IN LEFT PANEL
        # 1. Market Context
        self._create_market_context_section(self.scrollable_frame)
        
        # 2. Stock Analysis (with chart buttons)
        self._create_stock_analysis_section(self.scrollable_frame)
        
        # 3. Quick Actions
        self._create_quick_actions_section(self.scrollable_frame)
        
        # RIGHT PANEL - Output area
        right_frame = ttk.Frame(self.main_container)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self._create_output_section(right_frame)
    
    def _on_frame_configure(self, event=None):
        """Update scroll region and check if scrollbar is needed"""
        self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
        self._update_scrollbar_visibility()
    
    def _on_canvas_configure(self, event=None):
        """Update scrollable frame width to match canvas"""
        self.left_canvas.itemconfig(self.canvas_window, width=event.width)
        self._update_scrollbar_visibility()
    
    def _update_scrollbar_visibility(self):
        """Show scrollbar only if content exceeds visible area"""
        self.root.update_idletasks()
        
        # Get canvas and content heights
        canvas_height = self.left_canvas.winfo_height()
        content_height = self.scrollable_frame.winfo_reqheight()
        
        if content_height > canvas_height:
            # Content exceeds canvas - show scrollbar
            if not self.left_scrollbar.winfo_ismapped():
                self.left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        else:
            # Content fits - hide scrollbar
            if self.left_scrollbar.winfo_ismapped():
                self.left_scrollbar.pack_forget()
    
    def _create_market_context_section(self, parent):
        """Create market context section"""
        frame = self._create_section_frame(parent, "Market Context")
        
        # Macro score display
        self.macro_score_label = ttk.Label(
            frame,
            text="Macro Score: --/10",
            font=('Segoe UI', 10, 'bold')
        )
        self.macro_score_label.pack(pady=5)
        
        self.deployment_label = ttk.Label(
            frame,
            text="Deployment: --%"
        )
        self.deployment_label.pack()
        
        self.regime_label = ttk.Label(
            frame,
            text="Regime: Unknown"
        )
        self.regime_label.pack(pady=(0, 10))
        
        # Top sectors display
        ttk.Label(frame, text="Top 3 Sectors:", font=('Segoe UI', 9, 'bold')).pack(anchor=tk.W, padx=5)
        self.sectors_text = tk.Text(
            frame,
            height=4,
            width=35,
            bg=COLORS['bg_entry'],
            fg=COLORS['fg_text'],
            font=('Consolas', 8),
            relief=tk.FLAT,
            borderwidth=1
        )
        self.sectors_text.pack(padx=5, pady=5, fill=tk.X)
        self.sectors_text.config(state=tk.DISABLED)
        
        # Update button
        ttk.Button(
            frame,
            text="Update Market Data",
            command=self._update_market_context,
            style='Accent.TButton'
        ).pack(pady=5, padx=5, fill=tk.X)
    
    def _create_stock_analysis_section(self, parent):
        """Create stock analysis section with chart buttons"""
        frame = self._create_section_frame(parent, "Stock Analysis")
        
        # Symbol input
        input_frame = ttk.Frame(frame)
        input_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(input_frame, text="Symbol:").pack(side=tk.LEFT)
        self.symbol_entry = ttk.Entry(input_frame, width=12, font=('Segoe UI', 10))
        self.symbol_entry.pack(side=tk.LEFT, padx=5)
        self.symbol_entry.bind('<Return>', lambda e: self._analyze_stock())
        self.symbol_entry.bind('<KeyRelease>', self._auto_uppercase_symbol)
        self.symbol_entry.bind('<FocusIn>', lambda e: self.symbol_entry.select_range(0, tk.END))
        
        # Style selection
        style_frame = ttk.Frame(frame)
        style_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(style_frame, text="Style:").pack(side=tk.LEFT)
        self.style_var = tk.StringVar(value='Swing')
        self.style_combo = ttk.Combobox(
            style_frame,
            textvariable=self.style_var,
            values=list(STYLES.keys()),
            state='readonly',
            width=15,
            font=('Segoe UI', 9)
        )
        self.style_combo.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Analyze button
        ttk.Button(
            frame,
            text="Analyze Stock",
            command=self._analyze_stock,
            style='Accent.TButton'
        ).pack(pady=5, padx=5, fill=tk.X)
        
        # Chart buttons frame
        chart_frame = ttk.Frame(frame)
        chart_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Daily chart button
        self.daily_chart_btn = ttk.Button(
            chart_frame,
            text="Daily Chart",
            command=lambda: self._show_chart('d'),
            style='Chart.TButton'
        )
        self.daily_chart_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        
        # 15-min chart button (opens in browser for Finviz Elite)
        self.intraday_chart_btn = ttk.Button(
            chart_frame,
            text="15 min Chart",
            command=lambda: self._show_chart('i15'),
            style='Chart.TButton'
        )
        self.intraday_chart_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
    
    def _create_quick_actions_section(self, parent):
        """Create quick actions section"""
        frame = self._create_section_frame(parent, "Quick Actions")
        
        # AI Agents button (prominent)
        if AGENTS_AVAILABLE:
            agents_btn = ttk.Button(
                frame,
                text="🤖 AI Agents",
                command=self._open_agent_dashboard,
                style='Accent.TButton'
            )
            agents_btn.pack(pady=2, padx=5, fill=tk.X)
        
        ttk.Button(
            frame,
            text="Position Sizer",
            command=self._open_position_sizer
        ).pack(pady=2, padx=5, fill=tk.X)
        
        ttk.Button(
            frame,
            text="View Macro Dashboard",
            command=self._show_macro_dashboard
        ).pack(pady=2, padx=5, fill=tk.X)
        
        ttk.Button(
            frame,
            text="View Sector Rankings",
            command=self._show_sector_rankings
        ).pack(pady=2, padx=5, fill=tk.X)
        
        ttk.Button(
            frame,
            text="Clear Output",
            command=self._clear_output
        ).pack(pady=2, padx=5, fill=tk.X)
    
    def _create_output_section(self, parent):
        """Create output text area"""
        output_frame = ttk.Frame(parent)
        output_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(output_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.output_text = tk.Text(
            output_frame,
            bg=OUTPUT_COLORS['bg'],
            fg=OUTPUT_COLORS['fg'],
            font=('Consolas', 10),
            wrap=tk.WORD,
            yscrollcommand=scrollbar.set
        )
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.output_text.yview)
        
        # Configure text tags
        self.output_text.tag_config('header', foreground=OUTPUT_COLORS['header'], font=('Consolas', 11, 'bold'))
        self.output_text.tag_config('strong_buy', foreground=OUTPUT_COLORS['strong_buy'], font=('Consolas', 11, 'bold'))
        self.output_text.tag_config('buy', foreground=OUTPUT_COLORS['buy'], font=('Consolas', 11, 'bold'))
        self.output_text.tag_config('speculative', foreground=OUTPUT_COLORS['speculative'], font=('Consolas', 11, 'bold'))
        self.output_text.tag_config('avoid', foreground=OUTPUT_COLORS['avoid'], font=('Consolas', 11, 'bold'))
        self.output_text.tag_config('warning', foreground=OUTPUT_COLORS['warning'], font=('Consolas', 10, 'bold'))
        self.output_text.tag_config('section', foreground=OUTPUT_COLORS['section'], font=('Consolas', 10, 'bold'))
        self.output_text.tag_config('factor', foreground=OUTPUT_COLORS['factor'])
        self.output_text.tag_config('value', foreground=OUTPUT_COLORS['value'])
        
        # Welcome message
        self._print_output("Blueprint Analyzer v1.6.0", 'header')
        self._print_output("Initializing...\n")
    
    def _create_section_frame(self, parent, title):
        """Create a labeled section frame"""
        container = ttk.Frame(parent, relief=tk.RIDGE, borderwidth=1)
        container.pack(fill=tk.X, padx=5, pady=5)
        
        title_label = ttk.Label(
            container,
            text=title,
            font=('Segoe UI', 10, 'bold'),
            background=COLORS['bg_panel']
        )
        title_label.pack(anchor=tk.W, padx=5, pady=(5, 2))
        
        content_frame = ttk.Frame(container)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))
        
        return content_frame
    
    def _create_news_ticker(self):
        """Create scrolling news ticker at bottom"""
        self.ticker_frame = tk.Frame(self.root, bg=COLORS['ticker_bg'], height=25)
        self.ticker_frame.pack(side=tk.BOTTOM, fill=tk.X, before=self.main_container)
        self.ticker_frame.pack_propagate(False)
        
        # News label icon
        news_icon = tk.Label(
            self.ticker_frame,
            text="📰 NEWS",
            bg=COLORS['ticker_bg'],
            fg='#ffaa00',
            font=('Segoe UI', 9, 'bold')
        )
        news_icon.pack(side=tk.LEFT, padx=5)
        
        # Ticker canvas for scrolling text
        self.ticker_canvas = tk.Canvas(
            self.ticker_frame,
            bg=COLORS['ticker_bg'],
            height=25,
            highlightthickness=0
        )
        self.ticker_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Create ticker text
        self.ticker_text_id = self.ticker_canvas.create_text(
            0, 12,
            text="Loading market news...",
            fill=COLORS['ticker_fg'],
            font=('Consolas', 9),
            anchor='w'
        )
    
    def _start_news_ticker(self):
        """Start the news ticker animation and updates"""
        # Initial news fetch
        self._fetch_news()
        
        # Start ticker animation
        self._animate_ticker()
        
        # Schedule periodic news updates
        self._schedule_news_update()
    
    def _fetch_news(self):
        """Fetch news from Finviz"""
        def fetch():
            try:
                news_text = []
                
                # Try to get news from Finviz
                url = "https://finviz.com/news.ashx"
                headers = {'User-Agent': 'Mozilla/5.0'}
                
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    # Parse simple news headlines from page
                    from html.parser import HTMLParser
                    
                    class NewsParser(HTMLParser):
                        def __init__(self):
                            super().__init__()
                            self.in_link = False
                            self.headlines = []
                            self.current_headline = ""
                        
                        def handle_starttag(self, tag, attrs):
                            if tag == 'a':
                                for attr in attrs:
                                    if attr[0] == 'class' and 'nn-tab-link' in attr[1]:
                                        self.in_link = True
                                        break
                        
                        def handle_endtag(self, tag):
                            if tag == 'a' and self.in_link:
                                if self.current_headline.strip():
                                    self.headlines.append(self.current_headline.strip())
                                self.current_headline = ""
                                self.in_link = False
                        
                        def handle_data(self, data):
                            if self.in_link:
                                self.current_headline += data
                    
                    parser = NewsParser()
                    parser.feed(response.text)
                    
                    if parser.headlines:
                        news_text = parser.headlines[:15]  # Get top 15 headlines
                
                if not news_text:
                    # Fallback - generic market news placeholder
                    news_text = [
                        "Market news loading...",
                        "Visit finviz.com for latest market updates",
                        "Blueprint Analyzer v1.6.0 - Real-time stock analysis"
                    ]
                
                self.news_items = news_text
                
                # Build ticker string with separators
                separator = "  ●  "
                self.ticker_full_text = separator.join(self.news_items) + separator
                
                # Update ticker display
                self.root.after(0, self._update_ticker_text)
                
            except Exception as e:
                print(f"⚠️  News fetch error: {e}")
                self.news_items = ["Market news unavailable - check connection"]
                self.ticker_full_text = "  ●  ".join(self.news_items) + "  ●  "
        
        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()
    
    def _update_ticker_text(self):
        """Update the ticker text on canvas"""
        self.ticker_canvas.itemconfig(self.ticker_text_id, text=self.ticker_full_text)
        self.news_position = self.ticker_canvas.winfo_width()  # Start from right edge
    
    def _animate_ticker(self):
        """Animate the news ticker scrolling"""
        if not hasattr(self, 'ticker_full_text'):
            self.ticker_after_id = self.root.after(100, self._animate_ticker)
            return
        
        # Move text left
        self.news_position -= 2
        
        # Get text width
        bbox = self.ticker_canvas.bbox(self.ticker_text_id)
        if bbox:
            text_width = bbox[2] - bbox[0]
            
            # Reset position when text scrolls off screen
            if self.news_position < -text_width:
                self.news_position = self.ticker_canvas.winfo_width()
        
        # Update position
        self.ticker_canvas.coords(self.ticker_text_id, self.news_position, 12)
        
        # Schedule next animation frame (50ms = 20fps)
        self.ticker_after_id = self.root.after(50, self._animate_ticker)
    
    def _schedule_news_update(self):
        """Schedule next news update"""
        self.news_update_after_id = self.root.after(NEWS_UPDATE_INTERVAL, self._periodic_news_update)
    
    def _periodic_news_update(self):
        """Periodically update news"""
        print("📰 Updating news ticker...")
        self._fetch_news()
        self._schedule_news_update()
    
    def _create_status_bar(self):
        """Create status bar"""
        self.status_frame = ttk.Frame(self.root, relief=tk.SUNKEN, borderwidth=1)
        self.status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_label = ttk.Label(
            self.status_frame,
            text="Ready",
            background=COLORS['bg_panel'],
            foreground=COLORS['fg_text']
        )
        self.status_label.pack(side=tk.LEFT, padx=5)
        
        self.processing_label = ttk.Label(
            self.status_frame,
            text="",
            background=COLORS['bg_panel'],
            foreground=COLORS['accent']
        )
        self.processing_label.pack(side=tk.RIGHT, padx=5)
    
    #############################################
    # CHART (OPEN IN BROWSER)
    #############################################
    
    def _show_chart(self, period='d'):
        """Open Finviz chart in browser"""
        import webbrowser
        
        symbol = self.symbol_entry.get().strip().upper()
        if not symbol:
            symbol = self.current_symbol
        
        if not symbol:
            messagebox.showwarning("No Symbol", "Please enter a symbol first")
            return
        
        # Build Finviz URL
        url = f"https://finviz.com/quote.ashx?t={symbol}&ty=c&ta=1&p={period}"
        
        # Determine chart type for status
        if period == 'd':
            chart_type = "Daily"
        elif period == 'i15':
            chart_type = "15 min"
        elif period == 'w':
            chart_type = "Weekly"
        else:
            chart_type = period
        
        self._set_status(f"Opening {symbol} {chart_type} chart in browser...")
        webbrowser.open(url)
        self._set_status("Ready")
    
    def _create_chart_popup(self, image, title):
        """Create popup window with chart image"""
        # Create toplevel window
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.configure(bg='#1e1e1e')
        
        # Size window to image
        img_width, img_height = image.size
        popup.geometry(f"{img_width + 20}x{img_height + 50}")
        popup.resizable(True, True)
        
        # Title label
        title_label = tk.Label(
            popup,
            text=title,
            bg='#1e1e1e',
            fg='#4ec9b0',
            font=('Segoe UI', 12, 'bold')
        )
        title_label.pack(pady=5)
        
        # Convert to PhotoImage
        photo = ImageTk.PhotoImage(image)
        
        # Image label
        img_label = tk.Label(popup, image=photo, bg='#1e1e1e')
        img_label.image = photo  # Keep reference
        img_label.pack(padx=10, pady=5)
        
        # Close button
        close_btn = ttk.Button(popup, text="Close", command=popup.destroy)
        close_btn.pack(pady=5)
        
        # Focus and bring to front
        popup.focus_force()
        popup.lift()
        
        self._set_status("Ready")
    
    def _update_chart_button_text(self, symbol):
        """Update chart button text with current symbol"""
        self.current_symbol = symbol
        if symbol:
            self.daily_chart_btn.config(text=f"{symbol} Daily")
            self.intraday_chart_btn.config(text=f"{symbol} 15 min")
        else:
            self.daily_chart_btn.config(text="Daily Chart")
            self.intraday_chart_btn.config(text="15 min Chart")
    
    #############################################
    # AI AGENT DASHBOARD POPUP
    #############################################
    
    def _open_agent_dashboard(self):
        """Open AI Agent Dashboard popup"""
        if not AGENTS_AVAILABLE:
            messagebox.showwarning("Not Available", "Agent Dashboard module not loaded")
            return
        
        # Check if window already exists
        if self.agent_dashboard is not None:
            try:
                self.agent_dashboard.show()
                return
            except:
                pass  # Window was closed, create new one
        
        # Create new dashboard with callback for analysis
        self.agent_dashboard = AgentDashboardPopup(
            parent=self.root,
            finviz_processor=self.finviz,
            on_analyze_callback=self._analyze_from_agent
        )
    
    def _analyze_from_agent(self, symbol: str, style: str):
        """Callback from Agent Dashboard to analyze a candidate"""
        # Map style string to our style names
        style_map = {
            'Swing': 'Swing',
            'Momentum': 'Momentum',
            'Buy & Hold': 'Buy & Hold',
            'Speculative': 'Speculative',
        }
        
        # Set the symbol and style in the main UI
        self.symbol_entry.delete(0, tk.END)
        self.symbol_entry.insert(0, symbol.upper())
        
        if style in style_map:
            self.style_var.set(style_map[style])
        
        # Update chart buttons
        self._update_chart_button_text(symbol.upper())
        
        # Run analysis
        self._analyze_stock()
    
    #############################################
    # POSITION SIZER POPUP
    #############################################
    
    def _open_position_sizer(self):
        """Open position sizer in separate popup window using tk widgets"""
        # Check if window already exists
        if self.position_sizer_window is not None and self.position_sizer_window.winfo_exists():
            self.position_sizer_window.lift()
            self.position_sizer_window.focus_force()
            return
        
        # Create new window
        self.position_sizer_window = tk.Toplevel(self.root)
        self.position_sizer_window.title("Position Sizer")
        self.position_sizer_window.geometry("420x520")
        self.position_sizer_window.configure(bg='#f0f0f0')
        self.position_sizer_window.resizable(False, False)
        
        # Main frame using tk.Frame
        main_frame = tk.Frame(self.position_sizer_window, bg='#f0f0f0')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=15)
        
        # Title
        tk.Label(
            main_frame,
            text="Position Size Calculator",
            font=('Segoe UI', 14, 'bold'),
            bg='#f0f0f0',
            fg='#000000'
        ).pack(pady=(0, 20))
        
        # Input fields frame
        inputs_frame = tk.Frame(main_frame, bg='#f0f0f0')
        inputs_frame.pack(fill=tk.X, pady=5)
        
        # Entry price row
        entry_frame = tk.Frame(inputs_frame, bg='#f0f0f0')
        entry_frame.pack(fill=tk.X, pady=8)
        tk.Label(entry_frame, text="Entry Price:", width=15, anchor='e', 
                 bg='#f0f0f0', font=('Segoe UI', 10)).pack(side=tk.LEFT)
        self.popup_entry_var = tk.StringVar(value=self.entry_price_var.get())
        tk.Entry(entry_frame, textvariable=self.popup_entry_var, width=18, 
                 font=('Segoe UI', 10), relief=tk.SOLID, bd=1).pack(side=tk.LEFT, padx=10)
        
        # Stop price row
        stop_frame = tk.Frame(inputs_frame, bg='#f0f0f0')
        stop_frame.pack(fill=tk.X, pady=8)
        tk.Label(stop_frame, text="Stop Price:", width=15, anchor='e',
                 bg='#f0f0f0', font=('Segoe UI', 10)).pack(side=tk.LEFT)
        self.popup_stop_var = tk.StringVar(value=self.stop_price_var.get())
        tk.Entry(stop_frame, textvariable=self.popup_stop_var, width=18,
                 font=('Segoe UI', 10), relief=tk.SOLID, bd=1).pack(side=tk.LEFT, padx=10)
        
        # ATR row
        atr_frame = tk.Frame(inputs_frame, bg='#f0f0f0')
        atr_frame.pack(fill=tk.X, pady=8)
        tk.Label(atr_frame, text="ATR:", width=15, anchor='e',
                 bg='#f0f0f0', font=('Segoe UI', 10)).pack(side=tk.LEFT)
        self.popup_atr_var = tk.StringVar(value=self.atr_var.get())
        tk.Entry(atr_frame, textvariable=self.popup_atr_var, width=18,
                 font=('Segoe UI', 10), relief=tk.SOLID, bd=1).pack(side=tk.LEFT, padx=10)
        
        # Style row
        style_frame = tk.Frame(inputs_frame, bg='#f0f0f0')
        style_frame.pack(fill=tk.X, pady=8)
        tk.Label(style_frame, text="Trading Style:", width=15, anchor='e',
                 bg='#f0f0f0', font=('Segoe UI', 10)).pack(side=tk.LEFT)
        self.popup_style_var = tk.StringVar(value=self.sizer_style_var.get())
        style_menu = tk.OptionMenu(
            style_frame,
            self.popup_style_var,
            'swing', 'momentum', 'speculative', 'buy_hold'
        )
        style_menu.config(width=14, font=('Segoe UI', 9))
        style_menu.pack(side=tk.LEFT, padx=10)
        
        # Calculate button
        calc_btn = tk.Button(
            main_frame,
            text="Calculate Position Size",
            command=self._calculate_position_popup,
            bg='#0078d4',
            fg='white',
            font=('Segoe UI', 10, 'bold'),
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor='hand2'
        )
        calc_btn.pack(pady=15, fill=tk.X)
        
        # Hover effects for button
        def on_enter(e):
            calc_btn.config(bg='#005a9e')
        def on_leave(e):
            calc_btn.config(bg='#0078d4')
        calc_btn.bind('<Enter>', on_enter)
        calc_btn.bind('<Leave>', on_leave)
        
        # Results frame
        results_frame = tk.Frame(main_frame, bg='#f0f0f0')
        results_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        # Results text
        self.popup_results_text = tk.Text(
            results_frame,
            height=14,
            bg='#1e1e1e',
            fg='#d4d4d4',
            font=('Consolas', 10),
            relief=tk.SOLID,
            bd=1,
            padx=10,
            pady=10
        )
        self.popup_results_text.pack(fill=tk.BOTH, expand=True)
        
        # Tags for results
        self.popup_results_text.tag_config('header', foreground='#4ec9b0', font=('Consolas', 11, 'bold'))
        self.popup_results_text.tag_config('value', foreground='#ce9178')
        self.popup_results_text.tag_config('warning', foreground='#ff6347')
        
        # Focus the window
        self.position_sizer_window.focus_force()
        self.position_sizer_window.lift()
    
    def _calculate_position_popup(self):
        """Calculate position size in popup window"""
        if not self.sizer:
            messagebox.showerror("Not Ready", "System still initializing")
            return
        
        try:
            entry = float(self.popup_entry_var.get())
            stop = float(self.popup_stop_var.get())
            atr = float(self.popup_atr_var.get())
            style = self.popup_style_var.get()
            
            result = self.sizer.calculate_position(
                entry_price=entry,
                stop_price=stop,
                atr=atr,
                style=style
            )
            
            # Clear and display results
            self.popup_results_text.delete('1.0', tk.END)
            
            self.popup_results_text.insert(tk.END, "POSITION SIZE RESULTS\n", 'header')
            self.popup_results_text.insert(tk.END, "=" * 35 + "\n\n")
            
            self.popup_results_text.insert(tk.END, f"Style:           {style.upper()}\n")
            self.popup_results_text.insert(tk.END, f"Entry:           ${entry:>10.2f}\n")
            self.popup_results_text.insert(tk.END, f"Stop:            ${stop:>10.2f}\n")
            self.popup_results_text.insert(tk.END, f"ATR:             ${atr:>10.2f}\n\n")
            
            self.popup_results_text.insert(tk.END, "-" * 35 + "\n")
            self.popup_results_text.insert(tk.END, f"Base Shares:     {result['raw_shares']:>10,}\n")
            self.popup_results_text.insert(tk.END, f"ATR Adjustment:  {result['atr_multiplier']:>10.2f}x\n")
            self.popup_results_text.insert(tk.END, f"Final Shares:    {result['final_shares']:>10,}\n", 'value')
            self.popup_results_text.insert(tk.END, f"Position Value:  ${result['position_value']:>9,.2f}\n", 'value')
            self.popup_results_text.insert(tk.END, f"Dollar Risk:     ${result['dollar_risk']:>9,.2f}\n")
            self.popup_results_text.insert(tk.END, f"Risk %:          {result['risk_pct']:>10.2f}%\n")
            
            if result.get('warnings'):
                self.popup_results_text.insert(tk.END, "\n")
                for warning in result['warnings']:
                    self.popup_results_text.insert(tk.END, f"⚠️  {warning}\n", 'warning')
            
            # Sync back to main variables
            self.entry_price_var.set(self.popup_entry_var.get())
            self.stop_price_var.set(self.popup_stop_var.get())
            self.atr_var.set(self.popup_atr_var.get())
            self.sizer_style_var.set(self.popup_style_var.get())
            
        except ValueError:
            messagebox.showwarning("Invalid Input", "Please enter valid numbers")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    #############################################
    # BACKEND INITIALIZATION
    #############################################
    
    def _initialize_backend(self):
        """Initialize backend components"""
        def init():
            try:
                print("\n" + "="*60)
                print("🔍 DIAGNOSTIC: Initializing backend components")
                print("="*60)
                
                # Initialize Schwab API client
                print("\n📡 DIAGNOSTIC: Initializing Schwab API client...")
                schwab_client = None
                try:
                    from schwab_client import SchwabClient
                    schwab_client = SchwabClient()
                    print("✅ DIAGNOSTIC: Schwab API client initialized")
                except Exception as e:
                    print(f"⚠️  DIAGNOSTIC: Schwab API initialization failed: {e}")
                
                # Initialize market data client
                self._set_status("Initializing market data client...")
                self.client = MarketDataProvider(schwab_client=schwab_client)
                
                from market_data import YFINANCE_AVAILABLE
                print(f"✅ DIAGNOSTIC: MarketDataProvider created")
                
                self._set_status("Initializing macro calculator...")
                self.macro = MacroScoreCalculator(self.client)
                
                self._set_status("Initializing sector ranker...")
                self.sectors = SectorRanker(self.client)
                
                # Initialize Finviz processor
                if FINVIZ_AVAILABLE:
                    self._set_status("Initializing Finviz processor...")
                    self.finviz = FinvizDataProcessor(api_token="77fceb14-2783-4612-a992-58985f14505c")
                    print("✅ DIAGNOSTIC: FinvizDataProcessor created")
                
                self._set_status("Initializing blueprint scorer...")
                self.scorer = BlueprintScorer(self.client, self.macro, self.sectors, self.finviz)
                
                self._set_status("Initializing position sizer...")
                self.sizer = PositionSizer()
                
                self._set_status("Ready")
                print("\n✅ DIAGNOSTIC: All components initialized")
                print("="*60 + "\n")
                
                self._print_output("\nSystem initialized successfully!", 'header')
                self._print_output("\n✅ Ready to analyze stocks.", 'value')
                self._print_output("Enter a symbol and click 'Analyze Stock'\n")
                
                # Auto-update market context
                self.root.after(500, self._update_market_context)
                
            except Exception as e:
                print(f"\n❌ DIAGNOSTIC ERROR in initialization: {str(e)}")
                import traceback
                traceback.print_exc()
                self._set_status("Initialization failed - check console")
                self._print_output(f"\n⚠️  Initialization Error: {str(e)}\n", 'warning')
        
        thread = threading.Thread(target=init, daemon=True)
        thread.start()
    
    #############################################
    # CORE FUNCTIONS
    #############################################
    
    def _auto_uppercase_symbol(self, event=None):
        """Auto-uppercase symbol as user types"""
        current = self.symbol_entry.get()
        if current and not current.isupper():
            pos = self.symbol_entry.index(tk.INSERT)
            self.symbol_entry.delete(0, tk.END)
            self.symbol_entry.insert(0, current.upper())
            self.symbol_entry.icursor(pos)
        
        # Update chart button text
        self._update_chart_button_text(current.upper() if current else "")
    
    def _clear_and_focus_symbol(self):
        """Clear symbol entry and refocus"""
        self.symbol_entry.delete(0, tk.END)
        self.symbol_entry.focus()
        self._update_chart_button_text("")
    
    def _analyze_stock(self):
        """Analyze stock using blueprint scorer"""
        if self.processing:
            messagebox.showwarning("Busy", "Please wait for current operation to complete")
            return
        
        symbol = self.symbol_entry.get().strip().upper()
        if not symbol:
            messagebox.showwarning("Input Required", "Please enter a symbol")
            return
        
        if not self.scorer:
            messagebox.showerror("Not Ready", "System still initializing")
            return
        
        # Update chart button text
        self._update_chart_button_text(symbol)
        
        style_name = self.style_var.get()
        style = STYLES[style_name]
        
        def analyze():
            try:
                self.processing = True
                self._set_status(f"Analyzing {symbol}...")
                self._show_processing()
                
                score = self.scorer.score_stock(symbol=symbol, style=style)
                
                self.root.after(0, lambda: self._display_score(score))
                
                if score.setup:
                    self.root.after(0, lambda: self._populate_sizer_from_score(score))
                
            except Exception as e:
                logger.error(f"Analysis error: {e}", exc_info=True)
                self.root.after(0, lambda: self._print_output(
                    f"\n⚠️  Analysis Error: {str(e)}\n", 'warning'))
            finally:
                self.processing = False
                self.root.after(0, lambda: self._hide_processing())
                self.root.after(0, lambda: self._set_status("Ready"))
        
        thread = threading.Thread(target=analyze, daemon=True)
        thread.start()
    
    def _update_market_context(self):
        """Update market context display"""
        if not self.macro or not self.sectors:
            return
        
        def update():
            try:
                self._set_status("Updating market data...")
                
                self.macro.calculate()
                macro_score = self.macro.get_last_score()
                
                rankings_result = self.sectors.update_rankings()
                rankings = self.sectors.get_rankings()
                
                if len(rankings) > 0:
                    top_sectors = [(r.symbol, {'name': r.name, 'rank': r.rank, 'rs_composite': r.composite_rs}) 
                                   for r in rankings[:3]]
                else:
                    top_sectors = []
                
                self.root.after(0, lambda: self._update_macro_display(macro_score))
                self.root.after(0, lambda: self._update_sectors_display(top_sectors))
                self.root.after(0, lambda: self._set_status("Market data updated"))
                
            except Exception as e:
                logger.error(f"Update error: {e}", exc_info=True)
                self.root.after(0, lambda: self._set_status(f"Update failed"))
        
        thread = threading.Thread(target=update, daemon=True)
        thread.start()
    
    def _update_macro_display(self, macro_score):
        """Update macro score labels"""
        if macro_score:
            self.macro_score_label.config(text=f"Macro Score: {macro_score.total_score:.1f}/10")
            self.deployment_label.config(text=f"Deployment: {macro_score.deployment_pct:.0f}%")
            self.regime_label.config(text=f"Regime: {macro_score.regime}")
    
    def _update_sectors_display(self, top_sectors):
        """Update top sectors display"""
        self.sectors_text.config(state=tk.NORMAL)
        self.sectors_text.delete('1.0', tk.END)
        for i, (etf, data) in enumerate(top_sectors, 1):
            self.sectors_text.insert(tk.END, f"{i}. {etf}: {data['name']}\n")
        self.sectors_text.config(state=tk.DISABLED)
    
    def _populate_sizer_from_score(self, score):
        """Auto-populate position sizer from score"""
        if score.setup:
            self.entry_price_var.set(str(score.setup.entry_price))
            self.stop_price_var.set(str(score.setup.stop_price))
    
    def _get_finviz_stock_info(self, symbol):
        """Get stock info from Finviz API"""
        if not self.finviz:
            return None
        
        symbol = symbol.upper().strip()
        
        try:
            df = self.finviz.fetch_from_api(filters='', view='111', order='')
            
            if df is None or df.empty:
                return None
            
            symbol_data = df[df['Ticker'] == symbol]
            
            if symbol_data.empty:
                return None
            
            row = symbol_data.iloc[0]
            return {
                'company_name': row.get('Company', symbol),
                'sector': row.get('Sector', 'Unknown'),
                'industry': row.get('Industry', 'Unknown'),
            }
            
        except Exception as e:
            print(f"⚠️  Finviz API error: {e}")
            return None
    
    def _get_finviz_industry_filter(self, industry_name):
        """Convert Finviz industry name to filter code"""
        industry_map = {
            'Software - Application': 'ind_softwareapplication',
            'Software - Infrastructure': 'ind_softwareinfrastructure',
            'Semiconductors': 'ind_semiconductors',
            'Banks - Diversified': 'ind_banksdiversified',
            'Banks - Regional': 'ind_banksregional',
            'Biotechnology': 'ind_biotechnology',
            'Oil & Gas E&P': 'ind_oilgasep',
            'Internet Retail': 'ind_internetretail',
            'Aerospace & Defense': 'ind_aerospacedefense',
            'Airlines': 'ind_airlines',
            'Auto Manufacturers': 'ind_automanufacturers',
        }
        
        if industry_name in industry_map:
            return industry_map[industry_name]
        
        for key, value in industry_map.items():
            if key.lower() in industry_name.lower():
                return value
        
        cleaned = re.sub(r'[^a-z0-9]', '', industry_name.lower())
        return f'ind_{cleaned}'
    
    def _get_finviz_top_industry_stocks(self, industry_name, exclude_symbol=None, limit=10):
        """Get top stocks in an industry from Finviz"""
        if not self.finviz:
            return []
        
        try:
            industry_filter = self._get_finviz_industry_filter(industry_name)
            
            df = self.finviz.fetch_from_api(
                filters=industry_filter,
                view='141',
                order='-perf1w'
            )
            
            if df is None or df.empty:
                return []
            
            if exclude_symbol:
                df = df[df['Ticker'] != exclude_symbol.upper()]
            
            top_stocks = df.head(limit)
            
            results = []
            for idx, row in top_stocks.iterrows():
                results.append({
                    'rank': len(results) + 1,
                    'symbol': row.get('Ticker', 'N/A'),
                    'company_name': row.get('Company', ''),
                    'price': row.get('Price', 0),
                    'perf_week': row.get('Performance (Week)', row.get('Perf Week', 'N/A')),
                    'volume': row.get('Volume', 0)
                })
            
            return results
            
        except Exception as e:
            print(f"❌ Finviz industry lookup failed: {e}")
            return []
    
    def _display_finviz_header(self, finviz_info, symbol, current_price):
        """Display header section with Finviz data"""
        self._print_styled("=" * 95, 'header')
        
        company_name = finviz_info.get('company_name', symbol) if finviz_info else symbol
        self._print_styled(f"BLUEPRINT ANALYSIS: {symbol} - {company_name}", 'header')
        
        sector = finviz_info.get('sector', 'Unknown') if finviz_info else 'Unknown'
        industry = finviz_info.get('industry', 'Unknown') if finviz_info else 'Unknown'
        
        self._print_styled(f"Sector:   {sector}", 'value')
        self._print_styled(f"Industry: {industry}", 'value')
        
        self._print_styled("=" * 95, 'header')
        self._print_styled(f"Price: ${current_price:.2f}")
        
        return sector, industry
    
    def _display_top_industry_stocks(self, industry_name, exclude_symbol):
        """Display top 10 industry stocks from Finviz"""
        top_stocks = self._get_finviz_top_industry_stocks(industry_name, exclude_symbol, limit=10)
        
        if not top_stocks:
            return
        
        self._print_styled("\n" + "=" * 95, 'section')
        self._print_styled(f"TOP 10 INDUSTRY STOCKS: {industry_name.upper()}", 'header')
        self._print_styled("Sorted by Weekly Performance (Source: Finviz)", 'value')
        self._print_styled("=" * 95, 'section')
        
        self._print_styled(f"{'#':>4} {'SYMBOL':<8} {'COMPANY NAME':<40} {'WEEK %':>12} {'PRICE':>12} {'VOLUME':>15}")
        self._print_styled("-" * 95, 'section')
        
        for stock in top_stocks:
            company = str(stock['company_name'])[:38] if len(str(stock['company_name'])) > 38 else stock['company_name']
            price_str = f"${stock['price']:.2f}" if stock['price'] > 0 else 'N/A'
            vol_str = f"{stock['volume']:,}" if stock['volume'] > 0 else 'N/A'
            week_str = str(stock['perf_week']) if stock['perf_week'] != 'N/A' else 'N/A'
            
            try:
                week_val = float(str(stock['perf_week']).replace('%', '')) if stock['perf_week'] != 'N/A' else 0
                color = 'value' if week_val >= 0 else 'warning'
            except:
                color = 'value'
            
            line = f"{stock['rank']:>4} {stock['symbol']:<8} {company:<40} {week_str:>12} {price_str:>12} {vol_str:>15}"
            self._print_styled(line, color)
        
        self._print_styled("=" * 95, 'section')
    
    def _display_score(self, score):
        """Display blueprint score"""
        self._clear_output()
        self._format_score_report_gui(score)
    
    def _format_score_report_gui(self, score):
        """Format score for GUI display"""
        finviz_info = self._get_finviz_stock_info(score.symbol)
        
        finviz_sector, finviz_industry = self._display_finviz_header(
            finviz_info, score.symbol, score.current_price
        )
        
        self._print_styled(f"Style: {score.style.value.upper()}\n")
        
        # Recommendation
        self._print_styled("-" * 95, 'section')
        self._print_styled("RECOMMENDATION", 'section')
        self._print_styled("-" * 95, 'section')
        
        rec_tag = {
            'STRONG_BUY': 'strong_buy',
            'BUY': 'buy',
            'BUY_REDUCED': 'buy',
            'SPECULATIVE': 'speculative',
            'WATCHLIST': 'value',
            'AVOID': 'avoid',
        }.get(score.recommendation.value, 'value')
        
        self._print_styled(f"Decision:   {score.recommendation.value}", rec_tag)
        self._print_styled(f"Conviction: {score.conviction}")
        self._print_styled(f"Score:      {score.total_score:.2f} / 10.00\n")
        
        if score.warnings:
            self._print_styled("⚠️  WARNINGS:", 'warning')
            for w in score.warnings:
                self._print_styled(f"  • {w}", 'warning')
            self._print_styled("")
        
        if score.setup:
            self._print_styled("-" * 95, 'section')
            self._print_styled("TRADE PLAN", 'section')
            self._print_styled("-" * 95, 'section')
            self._print_styled(f"Setup Type: {score.setup.setup_type}")
            self._print_styled(f"Entry:      ${score.setup.entry_price:.2f}", 'value')
            self._print_styled(f"Stop:       ${score.setup.stop_price:.2f}", 'value')
            self._print_styled(f"Target:     ${score.setup.target_price:.2f}", 'value')
            self._print_styled(f"Risk/Reward: {score.setup.risk_reward:.1f}:1\n", 'value')
        
        # Factor breakdown
        self._print_styled("-" * 95, 'section')
        self._print_styled("FACTOR ANALYSIS", 'section')
        self._print_styled("-" * 95, 'section')
        self._print_styled(f"{'Factor':<30} {'Score':>8} {'Weight':>8} {'Weighted':>10}")
        self._print_styled("  " + "-" * 85, 'section')
        
        for name, factor in score.factors.items():
            self._print_styled(
                f"{factor.name:<30} {factor.score:>8.1f} {factor.weight*100:>7.0f}% {factor.weighted_score:>10.2f}",
                'factor'
            )
        
        self._print_styled("  " + "-" * 85, 'section')
        self._print_styled(f"{'TOTAL':<30} {'':<8} {'100%':>8} {score.total_score:>10.2f}\n", 'value')
        
        # Factor details
        self._print_styled("-" * 95, 'section')
        self._print_styled("FACTOR DETAILS", 'section')
        self._print_styled("-" * 95, 'section')
        
        for name, factor in score.factors.items():
            self._print_styled(f"• {factor.name}: {factor.rationale}")
        
        self._print_styled("")
        
        # Context
        self._print_styled("-" * 95, 'section')
        self._print_styled("CONTEXT", 'section')
        self._print_styled("-" * 95, 'section')
        self._print_styled(f"Sector: {score.sector_name} (Rank #{score.sector_rank}/11)")
        self._print_styled(f"Trend: {score.trend_state}")
        self._print_styled(f"RS Composite: {score.rs_composite:.1f}\n")
        
        # Top industry stocks
        industry_to_use = finviz_industry if finviz_industry and finviz_industry != 'Unknown' else score.industry_name
        
        if industry_to_use and industry_to_use != 'Unknown':
            def get_and_display_industry():
                self.root.after(0, lambda: self._display_top_industry_stocks(industry_to_use, score.symbol))
            
            thread = threading.Thread(target=get_and_display_industry, daemon=True)
            thread.start()
    
    #############################################
    # MENU ACTIONS
    #############################################
    
    def _refresh_data(self):
        """Refresh all market data"""
        self._update_market_context()
    
    def _update_macro(self):
        """Update macro score"""
        if not self.macro:
            messagebox.showwarning("Not Ready", "System still initializing")
            return
        
        def update():
            try:
                self._set_status("Calculating macro score...")
                self.macro.calculate()
                self.root.after(0, lambda: self._show_macro_dashboard())
                self.root.after(0, lambda: self._set_status("Macro updated"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
        
        thread = threading.Thread(target=update, daemon=True)
        thread.start()
    
    def _rank_sectors(self):
        """Rank sectors"""
        if not self.sectors:
            messagebox.showwarning("Not Ready", "System still initializing")
            return
        
        def rank():
            try:
                self._set_status("Ranking sectors...")
                self.sectors.update_rankings()
                self.root.after(0, lambda: self._show_sector_rankings())
                self.root.after(0, lambda: self._set_status("Sectors ranked"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
        
        thread = threading.Thread(target=rank, daemon=True)
        thread.start()
    
    def _show_macro_dashboard(self):
        """Show macro dashboard"""
        if not self.macro:
            return
        
        macro_score = self.macro.get_last_score()
        
        if not macro_score:
            messagebox.showinfo("No Data", "No macro score available. Click 'Update Macro Score' first.")
            return
        
        self._clear_output()
        self._print_styled("=" * 75, 'header')
        self._print_styled("MACRO DASHBOARD", 'header')
        self._print_styled("=" * 75, 'header')
        self._print_styled(f"\nTotal Score: {macro_score.total_score:.1f}/10", 'value')
        self._print_styled(f"Regime: {macro_score.regime}")
        self._print_styled(f"Deployment: {macro_score.deployment_pct:.0f}%")
        self._print_styled(f"ZBT Active: {'YES 🚀' if macro_score.zbt_active else 'No'}\n")
        
        self._print_styled("-" * 75, 'section')
        self._print_styled("FACTOR BREAKDOWN", 'section')
        self._print_styled("-" * 75, 'section')
        
        if hasattr(macro_score, 'indicators') and macro_score.indicators:
            for indicator_name, indicator in macro_score.indicators.items():
                self._print_styled(f"\n{indicator.name.upper()}")
                self._print_styled(f"  Score: {indicator.score:.1f}/10")
                self._print_styled(f"  Weight: {indicator.weight*100:.0f}%")
                self._print_styled(f"  Status: {indicator.status}")
                self._print_styled(f"  {indicator.description}")
    
    def _show_sector_rankings(self):
        """Show sector rankings"""
        if not self.sectors:
            return
        
        rankings = self.sectors.get_rankings()
        
        if not rankings:
            messagebox.showinfo("No Data", "No sector rankings available. Click 'Rank Sectors' first.")
            return
        
        self._clear_output()
        self._print_styled("=" * 95, 'header')
        self._print_styled("SECTOR RANKINGS", 'header')
        self._print_styled("=" * 95, 'header')
        
        self._print_styled("\nRS Score: Relative Strength vs SPY (S&P 500)")
        self._print_styled("  Score > 100: Outperforming the market")
        self._print_styled("  Score = 100: Matching the market")
        self._print_styled("  Score < 100: Underperforming the market\n")
        
        self._print_styled(f"{'Rank':>6} {'ETF':<6} {'Sector':<25} {'RS Score':>10} {'Rotation':<12}")
        self._print_styled("-" * 95, 'section')
        
        for rank, ranking in enumerate(rankings, 1):
            if ranking.rotation_status == 'LEADING':
                color = 'value'
            elif ranking.rotation_status == 'IMPROVING':
                color = 'factor'
            elif ranking.rotation_status == 'WEAKENING':
                color = 'warning'
            else:
                color = None
            
            self._print_styled(
                f"{rank:>6} {ranking.symbol:<6} {ranking.name:<25} {ranking.composite_rs:>10.1f} {ranking.rotation_status:<12}",
                color
            )
    
    def _show_about(self):
        """Show about dialog"""
        messagebox.showinfo(
            "About",
            "Blueprint Analyzer v1.7.0\n\n"
            "Stock Trading Blueprint Implementation\n"
            "Based on Blueprint v2.0.0\n\n"
            "Features:\n"
            "- AI Agents for autonomous idea generation\n"
            "- News & Earnings monitoring agent\n"
            "- Dynamic scrollbar (auto show/hide)\n"
            "- Chart buttons open Finviz in browser\n"
            "- Position Sizer popup window\n"
            "- Scrolling news ticker\n"
            "- Schwab API integration\n"
            "- Finviz Elite integration\n"
            "- 8-factor stock scoring system\n"
            "- Right-justified numeric columns"
        )
    
    #############################################
    # UTILITY FUNCTIONS
    #############################################
    
    def _print_output(self, text, tag=None):
        """Print to output with optional tag"""
        self.output_text.insert(tk.END, text + "\n", tag)
        self.output_text.see(tk.END)
    
    def _print_styled(self, text, tag=None):
        """Print styled text to output"""
        self._print_output(text, tag)
    
    def _clear_output(self):
        """Clear output text"""
        self.output_text.delete('1.0', tk.END)
    
    def _set_status(self, text):
        """Set status bar text"""
        self.status_label.config(text=text)
    
    def _show_processing(self):
        """Show processing indicator"""
        self.processing_label.config(text="Processing...")
    
    def _hide_processing(self):
        """Hide processing indicator"""
        self.processing_label.config(text="")


#############################################
# MAIN ENTRY POINT
#############################################

def main():
    """Main entry point"""
    root = tk.Tk()
    app = BlueprintAnalyzerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
