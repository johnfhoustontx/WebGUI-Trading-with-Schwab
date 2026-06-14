"""
Agent Dashboard Popup
=====================
Version: 1.0.0

Provides a GUI popup window for managing AI screening agents:
- View agent status
- Display candidates in sortable table
- Run manual scans
- Send candidates to Blueprint Analyzer
"""

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import Optional, Callable, List
import threading

# Import agent classes
from agents import NewsEarningsAgent, AgentCandidate, CandidatePriority


class AgentDashboardPopup:
    """
    Popup window for AI Agent management and candidate viewing.
    """
    
    # Color scheme matching main app
    COLORS = {
        'bg_main': '#f0f0f0',
        'bg_panel': '#ffffff',
        'bg_dark': '#1e1e1e',
        'fg_text': '#000000',
        'fg_light': '#d4d4d4',
        'accent': '#0078d4',
        'success': '#00aa00',
        'warning': '#ff8800',
        'error': '#ff4444',
        'critical': '#ff0000',
        'high': '#ff8800',
        'medium': '#ffcc00',
        'low': '#00aa00',
        # Scan button colors
        'btn_news': '#1e88e5',           # Blue for news
        'btn_news_active': '#0d47a1',    # Darker blue when active
        'btn_earnings': '#7b1fa2',       # Purple for earnings
        'btn_earnings_active': '#4a0072',
        'btn_gainers': '#2e7d32',        # Green for gainers
        'btn_gainers_active': '#1b5e20',
        'btn_losers': '#c62828',         # Red for losers
        'btn_losers_active': '#8e0000',
        'btn_default': '#546e7a',        # Gray default
        'btn_default_active': '#37474f',
    }
    
    def __init__(self, parent, finviz_processor=None, on_analyze_callback: Callable = None):
        """
        Initialize Agent Dashboard popup.
        
        Args:
            parent: Parent tkinter window
            finviz_processor: FinvizDataProcessor instance for API access
            on_analyze_callback: Callback when user wants to analyze a candidate
        """
        self.parent = parent
        self.finviz = finviz_processor
        self.on_analyze = on_analyze_callback
        
        # Initialize agents
        self.news_agent: Optional[NewsEarningsAgent] = None
        self._init_agents()
        
        # Candidate storage
        self.all_candidates: List[AgentCandidate] = []
        
        # Button references for styling
        self.scan_buttons: dict = {}
        self.active_scan: Optional[str] = None
        
        # Create window
        self.window: Optional[tk.Toplevel] = None
        self._create_window()
    
    def _init_agents(self):
        """Initialize all agents"""
        try:
            self.news_agent = NewsEarningsAgent(
                finviz_processor=self.finviz,
                api_token=self.finviz.api_token if self.finviz else None
            )
            
            # Set callback for new candidates
            self.news_agent.set_on_candidate(self._on_new_candidate)
            self.news_agent.set_on_error(self._on_agent_error)
            
        except Exception as e:
            print(f"⚠️  Agent initialization error: {e}")
    
    def _create_window(self):
        """Create the popup window"""
        self.window = tk.Toplevel(self.parent)
        self.window.title("AI Agent Dashboard - Idea Generation")
        self.window.geometry("1100x700")
        self.window.configure(bg=self.COLORS['bg_main'])
        self.window.minsize(900, 500)
        
        # Main container
        main_frame = ttk.Frame(self.window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # === TOP: Agent Controls ===
        self._create_agent_controls(main_frame)
        
        # === MIDDLE: Candidate Table ===
        self._create_candidate_table(main_frame)
        
        # === BOTTOM: Candidate Details ===
        self._create_details_panel(main_frame)
        
        # === STATUS BAR ===
        self._create_status_bar()
        
        # Initial population
        self._refresh_display()
        
        # Focus window
        self.window.focus_force()
        self.window.lift()
    
    def _create_agent_controls(self, parent):
        """Create agent control panel"""
        control_frame = ttk.LabelFrame(parent, text="Agent Controls", padding=10)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        # === News/Earnings Agent Row ===
        agent_row = ttk.Frame(control_frame)
        agent_row.pack(fill=tk.X, pady=5)
        
        # Agent name and status
        ttk.Label(
            agent_row, 
            text="📰 News & Earnings Agent",
            font=('Segoe UI', 10, 'bold')
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        self.news_status_label = ttk.Label(
            agent_row,
            text="● Stopped",
            foreground='gray'
        )
        self.news_status_label.pack(side=tk.LEFT, padx=10)
        
        # Control buttons
        self.news_start_btn = ttk.Button(
            agent_row,
            text="▶ Start",
            command=self._start_news_agent,
            width=10
        )
        self.news_start_btn.pack(side=tk.LEFT, padx=2)
        
        self.news_stop_btn = ttk.Button(
            agent_row,
            text="⏹ Stop",
            command=self._stop_news_agent,
            width=10,
            state=tk.DISABLED
        )
        self.news_stop_btn.pack(side=tk.LEFT, padx=2)
        
        ttk.Separator(agent_row, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        
        # Manual scan buttons with distinct colors
        self.scan_buttons['news'] = self._create_scan_button(
            agent_row,
            text="🔍 Scan News",
            command=lambda: self._manual_news_scan(),
            color_key='btn_news'
        )
        
        self.scan_buttons['earnings'] = self._create_scan_button(
            agent_row,
            text="📊 Earnings",
            command=lambda: self._manual_earnings_scan(),
            color_key='btn_earnings'
        )
        
        self.scan_buttons['gainers'] = self._create_scan_button(
            agent_row,
            text="📈 Top Gainers",
            command=lambda: self._manual_movers_scan('up'),
            color_key='btn_gainers'
        )
        
        self.scan_buttons['losers'] = self._create_scan_button(
            agent_row,
            text="📉 Top Losers",
            command=lambda: self._manual_movers_scan('down'),
            color_key='btn_losers'
        )
        
        # === Second Row: Quick Actions ===
        action_row = ttk.Frame(control_frame)
        action_row.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Label(action_row, text="Quick Actions:").pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Button(
            action_row,
            text="🔄 Refresh",
            command=self._refresh_display,
            width=10
        ).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(
            action_row,
            text="🗑️ Clear All",
            command=self._clear_candidates,
            width=10
        ).pack(side=tk.LEFT, padx=2)
        
        # Ticker search
        ttk.Separator(action_row, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(action_row, text="Scan Ticker:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.ticker_entry = ttk.Entry(action_row, width=10)
        self.ticker_entry.pack(side=tk.LEFT, padx=2)
        self.ticker_entry.bind('<Return>', lambda e: self._scan_specific_ticker())
        
        self.scan_buttons['ticker'] = self._create_scan_button(
            action_row,
            text="🔎 Scan",
            command=self._scan_specific_ticker,
            color_key='btn_default'
        )
        
        # Filter by priority
        ttk.Separator(action_row, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(action_row, text="Filter:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.filter_var = tk.StringVar(value='All')
        filter_combo = ttk.Combobox(
            action_row,
            textvariable=self.filter_var,
            values=['All', 'Critical', 'High', 'Medium', 'Low'],
            state='readonly',
            width=10
        )
        filter_combo.pack(side=tk.LEFT, padx=2)
        filter_combo.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
    
    def _create_scan_button(self, parent, text: str, command, color_key: str) -> tk.Button:
        """
        Create a styled scan button with distinct colors.
        
        Args:
            parent: Parent widget
            text: Button text
            command: Button command
            color_key: Key for color in COLORS dict (e.g., 'btn_news')
            
        Returns:
            tk.Button instance
        """
        bg_color = self.COLORS.get(color_key, self.COLORS['btn_default'])
        active_color = self.COLORS.get(f"{color_key}_active", self.COLORS['btn_default_active'])
        
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg_color,
            fg='white',
            activebackground=active_color,
            activeforeground='white',
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor='hand2',
            borderwidth=0
        )
        btn.pack(side=tk.LEFT, padx=3)
        
        # Store original color for reset
        btn._original_bg = bg_color
        btn._active_bg = active_color
        
        # Hover effects
        def on_enter(e):
            if btn['state'] != tk.DISABLED:
                btn.config(bg=active_color)
        
        def on_leave(e):
            if btn['state'] != tk.DISABLED and not getattr(btn, '_is_scanning', False):
                btn.config(bg=bg_color)
        
        btn.bind('<Enter>', on_enter)
        btn.bind('<Leave>', on_leave)
        
        return btn
    
    def _set_button_scanning(self, button_key: str, is_scanning: bool):
        """
        Set button to scanning state with visual feedback.
        
        Args:
            button_key: Key in scan_buttons dict
            is_scanning: True if currently scanning
        """
        if button_key not in self.scan_buttons:
            return
        
        btn = self.scan_buttons[button_key]
        
        # Store original texts for reset
        original_texts = {
            'news': '🔍 Scan News',
            'earnings': '📊 Earnings',
            'gainers': '📈 Top Gainers',
            'losers': '📉 Top Losers',
            'ticker': '🔎 Scan'
        }
        
        if is_scanning:
            btn._is_scanning = True
            # Get emoji from current text and add spinner
            current_text = btn.cget('text')
            emoji = current_text.split()[0] if current_text else '🔍'
            btn.config(
                bg=btn._active_bg,
                text=f"{emoji} ⏳...",
                state=tk.DISABLED
            )
            self.active_scan = button_key
        else:
            btn._is_scanning = False
            btn.config(
                bg=btn._original_bg,
                text=original_texts.get(button_key, btn.cget('text').replace(' ⏳...', '')),
                state=tk.NORMAL
            )
            self.active_scan = None
    
    def _create_candidate_table(self, parent):
        """Create the candidate table"""
        table_frame = ttk.LabelFrame(parent, text="Candidates", padding=5)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Treeview with scrollbars
        tree_container = ttk.Frame(table_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)
        
        # Scrollbars
        y_scroll = ttk.Scrollbar(tree_container, orient=tk.VERTICAL)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        x_scroll = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL)
        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Treeview
        columns = (
            'priority', 'symbol', 'source', 'headline', 
            'change', 'volume', 'style', 'score', 'time'
        )
        
        self.tree = ttk.Treeview(
            tree_container,
            columns=columns,
            show='headings',
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set,
            selectmode='browse'
        )
        
        y_scroll.config(command=self.tree.yview)
        x_scroll.config(command=self.tree.xview)
        
        # Column configuration
        column_config = {
            'priority': ('Priority', 80, tk.CENTER),
            'symbol': ('Symbol', 70, tk.CENTER),
            'source': ('Source', 120, tk.W),
            'headline': ('Headline', 350, tk.W),
            'change': ('Change %', 80, tk.E),
            'volume': ('Vol Ratio', 80, tk.E),
            'style': ('Style', 90, tk.CENTER),
            'score': ('Score', 60, tk.CENTER),
            'time': ('Time', 80, tk.CENTER),
        }
        
        for col, (heading, width, anchor) in column_config.items():
            self.tree.heading(col, text=heading, command=lambda c=col: self._sort_column(c))
            self.tree.column(col, width=width, anchor=anchor, minwidth=50)
        
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        # Bind events
        self.tree.bind('<<TreeviewSelect>>', self._on_select)
        self.tree.bind('<Double-1>', self._on_double_click)
        
        # Context menu
        self._create_context_menu()
        self.tree.bind('<Button-3>', self._show_context_menu)
        
        # Configure row tags for priority colors
        self.tree.tag_configure('critical', background='#ffcccc')
        self.tree.tag_configure('high', background='#fff0cc')
        self.tree.tag_configure('medium', background='#ffffcc')
        self.tree.tag_configure('low', background='#ccffcc')
    
    def _create_context_menu(self):
        """Create right-click context menu"""
        self.context_menu = tk.Menu(self.window, tearoff=0)
        self.context_menu.add_command(label="📊 Analyze in Blueprint", command=self._analyze_selected)
        self.context_menu.add_command(label="📰 View News", command=self._view_news)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="✓ Mark as Reviewed", command=self._mark_reviewed)
        self.context_menu.add_command(label="✗ Dismiss", command=self._dismiss_selected)
    
    def _create_details_panel(self, parent):
        """Create the details panel"""
        details_frame = ttk.LabelFrame(parent, text="Candidate Details", padding=10)
        details_frame.pack(fill=tk.X)
        
        # Left side: Key info
        left_frame = ttk.Frame(details_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.details_text = tk.Text(
            left_frame,
            height=6,
            bg=self.COLORS['bg_dark'],
            fg=self.COLORS['fg_light'],
            font=('Consolas', 9),
            wrap=tk.WORD,
            state=tk.DISABLED
        )
        self.details_text.pack(fill=tk.BOTH, expand=True)
        
        # Configure tags
        self.details_text.tag_configure('header', foreground='#4ec9b0', font=('Consolas', 10, 'bold'))
        self.details_text.tag_configure('value', foreground='#ce9178')
        self.details_text.tag_configure('bullish', foreground='#00ff00')
        self.details_text.tag_configure('bearish', foreground='#ff4444')
        
        # Right side: Actions
        action_frame = ttk.Frame(details_frame)
        action_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        
        ttk.Button(
            action_frame,
            text="📊 Analyze",
            command=self._analyze_selected,
            width=15
        ).pack(pady=2)
        
        ttk.Button(
            action_frame,
            text="📰 View News",
            command=self._view_news,
            width=15
        ).pack(pady=2)
        
        ttk.Button(
            action_frame,
            text="📈 Open Chart",
            command=self._open_chart,
            width=15
        ).pack(pady=2)
        
        ttk.Button(
            action_frame,
            text="✗ Dismiss",
            command=self._dismiss_selected,
            width=15
        ).pack(pady=2)
    
    def _create_status_bar(self):
        """Create status bar"""
        status_frame = ttk.Frame(self.window)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_label = ttk.Label(
            status_frame,
            text="Ready",
            background=self.COLORS['bg_panel']
        )
        self.status_label.pack(side=tk.LEFT, padx=5)
        
        self.count_label = ttk.Label(
            status_frame,
            text="Candidates: 0",
            background=self.COLORS['bg_panel']
        )
        self.count_label.pack(side=tk.RIGHT, padx=5)
    
    # === Agent Control Methods ===
    
    def _start_news_agent(self):
        """Start the news agent"""
        if self.news_agent:
            self.news_agent.start(interval_seconds=900)  # 15 minute interval
            self._update_agent_status()
            self._set_status("News agent started - scanning every 15 minutes")
    
    def _stop_news_agent(self):
        """Stop the news agent"""
        if self.news_agent:
            self.news_agent.stop()
            self._update_agent_status()
            self._set_status("News agent stopped")
    
    def _update_agent_status(self):
        """Update agent status display"""
        if self.news_agent and self.news_agent.is_running:
            self.news_status_label.config(text="● Running", foreground='green')
            self.news_start_btn.config(state=tk.DISABLED)
            self.news_stop_btn.config(state=tk.NORMAL)
        else:
            self.news_status_label.config(text="● Stopped", foreground='gray')
            self.news_start_btn.config(state=tk.NORMAL)
            self.news_stop_btn.config(state=tk.DISABLED)
    
    def _manual_news_scan(self):
        """Run manual news scan"""
        self._set_status("Scanning news...")
        self._set_button_scanning('news', True)
        
        def scan():
            try:
                if self.news_agent:
                    candidates = self.news_agent._scan_news()
                    self._add_candidates(candidates)
                    self.window.after(0, lambda: self._set_status(f"Found {len(candidates)} news items"))
            except Exception as e:
                self.window.after(0, lambda: self._set_status(f"Error: {str(e)}"))
            finally:
                self.window.after(0, lambda: self._set_button_scanning('news', False))
        
        threading.Thread(target=scan, daemon=True).start()
    
    def _manual_earnings_scan(self):
        """Run manual earnings scan"""
        self._set_status("Scanning earnings...")
        self._set_button_scanning('earnings', True)
        
        def scan():
            try:
                if self.news_agent:
                    # Scan both recent and upcoming
                    candidates = []
                    candidates.extend(self.news_agent._scan_earnings_surprises())
                    candidates.extend(self.news_agent._scan_upcoming_earnings())
                    self._add_candidates(candidates)
                    self.window.after(0, lambda: self._set_status(f"Found {len(candidates)} earnings items"))
            except Exception as e:
                self.window.after(0, lambda: self._set_status(f"Error: {str(e)}"))
            finally:
                self.window.after(0, lambda: self._set_button_scanning('earnings', False))
        
        threading.Thread(target=scan, daemon=True).start()
    
    def _manual_movers_scan(self, direction: str):
        """Run manual movers scan"""
        label = "gainers" if direction == 'up' else "losers"
        button_key = label  # 'gainers' or 'losers'
        self._set_status(f"Scanning top {label}...")
        self._set_button_scanning(button_key, True)
        
        def scan():
            try:
                if self.news_agent:
                    candidates = self.news_agent.get_todays_movers(direction, limit=15)
                    self._add_candidates(candidates)
                    self.window.after(0, lambda: self._set_status(f"Found {len(candidates)} {label}"))
            except Exception as e:
                self.window.after(0, lambda: self._set_status(f"Error: {str(e)}"))
            finally:
                self.window.after(0, lambda: self._set_button_scanning(button_key, False))
        
        threading.Thread(target=scan, daemon=True).start()
    
    def _scan_specific_ticker(self):
        """Scan news for specific ticker"""
        ticker = self.ticker_entry.get().strip().upper()
        if not ticker:
            messagebox.showwarning("Input Required", "Please enter a ticker symbol")
            return
        
        self._set_status(f"Scanning {ticker}...")
        self._set_button_scanning('ticker', True)
        
        def scan():
            try:
                if self.news_agent:
                    candidates = self.news_agent.scan_ticker_news(ticker)
                    self._add_candidates(candidates)
                    self.window.after(0, lambda: self._set_status(f"Found {len(candidates)} items for {ticker}"))
            except Exception as e:
                self.window.after(0, lambda: self._set_status(f"Error: {str(e)}"))
            finally:
                self.window.after(0, lambda: self._set_button_scanning('ticker', False))
        
        threading.Thread(target=scan, daemon=True).start()
    
    # === Candidate Management ===
    
    def _add_candidates(self, candidates: List[AgentCandidate]):
        """Add candidates to the list (avoiding duplicates)"""
        existing_hashes = {hash(f"{c.symbol}:{c.headline[:30]}") for c in self.all_candidates}
        
        for candidate in candidates:
            candidate_hash = hash(f"{candidate.symbol}:{candidate.headline[:30]}")
            if candidate_hash not in existing_hashes:
                self.all_candidates.append(candidate)
                existing_hashes.add(candidate_hash)
        
        # Sort by priority then score
        self.all_candidates.sort(key=lambda c: (c.priority.value, -c.score))
        
        # Update display
        self.window.after(0, self._refresh_display)
    
    def _on_new_candidate(self, candidate: AgentCandidate):
        """Callback when agent finds new candidate"""
        self._add_candidates([candidate])
    
    def _on_agent_error(self, error_msg: str):
        """Callback when agent encounters error"""
        self.window.after(0, lambda: self._set_status(f"Agent error: {error_msg}"))
    
    def _refresh_display(self):
        """Refresh the candidate table"""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Apply filter
        filter_val = self.filter_var.get()
        candidates = self.all_candidates
        
        if filter_val != 'All':
            priority_map = {
                'Critical': CandidatePriority.CRITICAL,
                'High': CandidatePriority.HIGH,
                'Medium': CandidatePriority.MEDIUM,
                'Low': CandidatePriority.LOW,
            }
            if filter_val in priority_map:
                candidates = [c for c in candidates if c.priority == priority_map[filter_val]]
        
        # Populate table
        for candidate in candidates:
            priority_text = {
                CandidatePriority.CRITICAL: "🔴 CRITICAL",
                CandidatePriority.HIGH: "🟠 HIGH",
                CandidatePriority.MEDIUM: "🟡 MEDIUM",
                CandidatePriority.LOW: "🟢 LOW",
            }.get(candidate.priority, "UNKNOWN")
            
            tag = candidate.priority.name.lower()
            
            values = (
                priority_text,
                candidate.symbol,
                candidate.source.value,
                candidate.headline[:60] + ('...' if len(candidate.headline) > 60 else ''),
                f"{candidate.change_pct:+.1f}%" if candidate.change_pct else "N/A",
                f"{candidate.volume_ratio:.1f}x" if candidate.volume_ratio > 0 else "N/A",
                candidate.suggested_style.value,
                f"{candidate.score:.0f}",
                candidate.timestamp.strftime("%H:%M")
            )
            
            self.tree.insert('', tk.END, values=values, tags=(tag,))
        
        # Update count
        self.count_label.config(text=f"Candidates: {len(candidates)}")
    
    def _apply_filter(self):
        """Apply priority filter"""
        self._refresh_display()
    
    def _clear_candidates(self):
        """Clear all candidates"""
        if messagebox.askyesno("Confirm", "Clear all candidates?"):
            self.all_candidates = []
            if self.news_agent:
                self.news_agent.clear_candidates()
            self._refresh_display()
    
    def _sort_column(self, col):
        """Sort table by column"""
        # Get all items
        items = [(self.tree.set(item, col), item) for item in self.tree.get_children('')]
        
        # Sort
        items.sort(reverse=True)
        
        # Rearrange
        for index, (_, item) in enumerate(items):
            self.tree.move(item, '', index)
    
    # === Selection and Actions ===
    
    def _get_selected_candidate(self) -> Optional[AgentCandidate]:
        """Get currently selected candidate"""
        selection = self.tree.selection()
        if not selection:
            return None
        
        item = selection[0]
        values = self.tree.item(item, 'values')
        symbol = values[1]  # Symbol is second column
        
        # Find matching candidate
        for candidate in self.all_candidates:
            if candidate.symbol == symbol:
                return candidate
        
        return None
    
    def _on_select(self, event):
        """Handle row selection"""
        candidate = self._get_selected_candidate()
        if candidate:
            self._show_details(candidate)
    
    def _on_double_click(self, event):
        """Handle double-click to analyze"""
        self._analyze_selected()
    
    def _show_context_menu(self, event):
        """Show right-click context menu"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.tk_popup(event.x_root, event.y_root)
    
    def _show_details(self, candidate: AgentCandidate):
        """Show candidate details in the details panel"""
        self.details_text.config(state=tk.NORMAL)
        self.details_text.delete('1.0', tk.END)
        
        # Header
        self.details_text.insert(tk.END, f"{candidate.symbol} - {candidate.headline}\n", 'header')
        self.details_text.insert(tk.END, "=" * 80 + "\n\n")
        
        # Key info
        self.details_text.insert(tk.END, f"Source:    {candidate.source.value}\n")
        self.details_text.insert(tk.END, f"Priority:  {candidate.priority_label}\n")
        self.details_text.insert(tk.END, f"Score:     {candidate.score:.0f}/100\n")
        self.details_text.insert(tk.END, f"Style:     {candidate.suggested_style.value}\n\n")
        
        # Price info
        if candidate.current_price > 0:
            self.details_text.insert(tk.END, f"Price:     ${candidate.current_price:.2f}\n")
        
        change_tag = 'bullish' if candidate.change_pct > 0 else 'bearish'
        self.details_text.insert(tk.END, f"Change:    ", 'value')
        self.details_text.insert(tk.END, f"{candidate.change_pct:+.1f}%\n", change_tag)
        
        if candidate.volume_ratio > 1:
            self.details_text.insert(tk.END, f"Volume:    {candidate.volume_ratio:.1f}x average\n")
        
        # Sector/Industry
        if candidate.sector:
            self.details_text.insert(tk.END, f"\nSector:    {candidate.sector}\n")
        if candidate.industry:
            self.details_text.insert(tk.END, f"Industry:  {candidate.industry}\n")
        
        # Action notes
        if candidate.action_notes:
            self.details_text.insert(tk.END, f"\nNotes: {candidate.action_notes}\n", 'value')
        
        self.details_text.config(state=tk.DISABLED)
    
    def _analyze_selected(self):
        """Send selected candidate to Blueprint Analyzer"""
        candidate = self._get_selected_candidate()
        if not candidate:
            messagebox.showinfo("No Selection", "Please select a candidate first")
            return
        
        if self.on_analyze:
            self.on_analyze(candidate.symbol, candidate.suggested_style.value)
            self._set_status(f"Analyzing {candidate.symbol}...")
    
    def _view_news(self):
        """Open Finviz news page for selected stock"""
        import webbrowser
        
        candidate = self._get_selected_candidate()
        if not candidate:
            return
        
        url = f"https://finviz.com/quote.ashx?t={candidate.symbol}&ty=n"
        webbrowser.open(url)
    
    def _open_chart(self):
        """Open Finviz chart for selected stock"""
        import webbrowser
        
        candidate = self._get_selected_candidate()
        if not candidate:
            return
        
        url = f"https://finviz.com/quote.ashx?t={candidate.symbol}&ty=c&ta=1&p=d"
        webbrowser.open(url)
    
    def _mark_reviewed(self):
        """Mark candidate as reviewed"""
        candidate = self._get_selected_candidate()
        if candidate:
            candidate.status = 'reviewed'
            self._set_status(f"{candidate.symbol} marked as reviewed")
    
    def _dismiss_selected(self):
        """Dismiss selected candidate"""
        candidate = self._get_selected_candidate()
        if candidate:
            self.all_candidates.remove(candidate)
            self._refresh_display()
            self._set_status(f"{candidate.symbol} dismissed")
    
    # === Utility Methods ===
    
    def _set_status(self, text: str):
        """Update status bar"""
        self.status_label.config(text=text)
    
    def show(self):
        """Show or focus the window"""
        if self.window and self.window.winfo_exists():
            self.window.lift()
            self.window.focus_force()
        else:
            self._create_window()
    
    def destroy(self):
        """Clean up and destroy window"""
        # Stop agents
        if self.news_agent:
            self.news_agent.stop()
        
        if self.window:
            self.window.destroy()
            self.window = None
