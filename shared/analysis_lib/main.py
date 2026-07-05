"""
Blueprint Analyzer - CLI Entry Point
Version: 1.0.0
Last Updated: 2025-01-01

Command-line interface for Blueprint trading analysis.

Version 1.0.0 Changes:
- Initial implementation
- Stock analysis command
- Sector ranking command
- Macro score command
- Position sizing command
"""

import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from config import COLORS
from schwab_client import MarketDataClient
from sector_analysis import SectorRanker, get_sector_info
from macro_score import MacroScoreCalculator
from blueprint_scorer import BlueprintScorer, TradingStyle, Recommendation
from position_sizer import PositionSizer, SizingStyle
from technical import calculate_atr_percent

#############################################
# LOGGING SETUP
#############################################

def setup_logging(verbose: bool = False):
    """Configure logging"""
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Reduce noise from requests
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)


#############################################
# CLI COMMANDS
#############################################

class BlueprintCLI:
    """Blueprint Analyzer CLI"""
    
    def __init__(self):
        self.client = None
        self.macro = None
        self.sectors = None
        self.scorer = None
        self.sizer = None
        self._initialized = False
    
    def _init_components(self):
        """Initialize components (lazy loading)"""
        if self._initialized:
            return
        
        print("Initializing Blueprint Analyzer...")
        print("-" * 50)
        
        # Initialize market data client
        print("• Connecting to Schwab API...")
        self.client = MarketDataClient()
        
        # Initialize macro calculator
        print("• Initializing macro calculator...")
        self.macro = MacroScoreCalculator(self.client)
        
        # Initialize sector ranker
        print("• Initializing sector ranker...")
        self.sectors = SectorRanker(self.client)
        
        # Initialize blueprint scorer
        print("• Initializing blueprint scorer...")
        self.scorer = BlueprintScorer(self.client, self.macro, self.sectors)
        
        # Initialize position sizer
        print("• Initializing position sizer...")
        self.sizer = PositionSizer()
        
        print("-" * 50)
        print("Initialization complete!")
        print()
        
        self._initialized = True
    
    def cmd_analyze(
        self, 
        symbol: str, 
        style: str = 'swing',
        update_sectors: bool = True,
        update_macro: bool = True
    ):
        """Analyze a stock using Blueprint scoring
        
        Args:
            symbol: Stock ticker
            style: Trading style (swing, momentum, buy_hold, speculative)
            update_sectors: Update sector rankings first
            update_macro: Update macro score first
        """
        self._init_components()
        
        symbol = symbol.upper()
        print(f"\n{'='*75}")
        print(f"ANALYZING: {symbol}")
        print(f"{'='*75}\n")
        
        # Parse style
        try:
            trading_style = TradingStyle(style.lower())
        except ValueError:
            print(f"Invalid style: {style}")
            print("Valid styles: swing, momentum, buy_hold, speculative")
            return
        
        # Update macro score
        if update_macro:
            print("Updating macro score...")
            spy_df = self.client.get_daily('SPY', months=12)
            vix_quote = self.client.get_quote('VIX')
            vix_price = vix_quote['last'] if vix_quote else 20.0
            macro_score = self.macro.calculate(spy_df, vix_price)
            print(f"  Macro Score: {macro_score.total_score:.2f}")
            print(f"  Deployment: {macro_score.deployment_pct:.0f}%")
            print(f"  Regime: {macro_score.regime}")
            print()
        
        # Update sector rankings
        if update_sectors:
            print("Updating sector rankings...")
            spy_df = self.client.get_daily('SPY', months=12)
            rankings = self.sectors.update_rankings(spy_df)
            print(f"  Top 3: {', '.join(self.sectors.get_top_sectors(3))}")
            print()
        
        # Run analysis
        print(f"Analyzing {symbol}...")
        score = self.scorer.score_stock(symbol, style=trading_style)
        
        # Print report
        report = self.scorer.format_score_report(score)
        print(report)
        
        # Calculate position size if we have a setup
        if score.setup:
            print("\nPOSITION SIZE CALCULATION:")
            print("-" * 50)
            
            # Get sector info for correlation check
            sector_info = get_sector_info(symbol)
            
            # Calculate ATR%
            daily_df = self.client.get_daily(symbol, months=3)
            atr_pct = calculate_atr_percent(daily_df) if daily_df is not None else None
            
            sizing_style = SizingStyle(style.lower())
            deployment = self.macro.get_last_score().deployment_pct if self.macro.get_last_score() else 100.0
            
            size_result = self.sizer.calculate(
                style=sizing_style,
                entry_price=score.setup.entry_price,
                stop_price=score.setup.stop_price,
                atr_percent=atr_pct,
                deployment_pct=deployment
            )
            
            size_report = self.sizer.format_calculation(
                symbol, sizing_style,
                score.setup.entry_price,
                score.setup.stop_price,
                size_result
            )
            print(size_report)
        
        # Return score for programmatic use
        return score
    
    def cmd_sectors(self, force_update: bool = True):
        """Show sector rankings"""
        self._init_components()
        
        print("\n" + "="*80)
        print("SECTOR ANALYSIS")
        print("="*80 + "\n")
        
        if force_update or not self.sectors.get_rankings():
            print("Fetching sector data...")
            spy_df = self.client.get_daily('SPY', months=12)
            self.sectors.update_rankings(spy_df)
        
        print(self.sectors.format_rankings_table())
    
    def cmd_macro(self, fed_policy: str = None, event_risk: bool = None):
        """Show macro score"""
        self._init_components()
        
        print("\n" + "="*70)
        print("MACRO ANALYSIS")
        print("="*70 + "\n")
        
        # Update settings if provided
        if fed_policy:
            self.macro.set_fed_policy(fed_policy)
        if event_risk is not None:
            self.macro.set_event_risk(event_risk)
        
        print("Fetching market data...")
        spy_df = self.client.get_daily('SPY', months=12)
        vix_quote = self.client.get_quote('VIX')
        vix_price = vix_quote['last'] if vix_quote else 20.0
        
        score = self.macro.calculate(spy_df, vix_price)
        print(self.macro.format_report(score))
    
    def cmd_size(
        self,
        style: str,
        entry: float,
        stop: float,
        symbol: str = None
    ):
        """Calculate position size"""
        self._init_components()
        
        print("\n" + "="*60)
        print("POSITION SIZE CALCULATOR")
        print("="*60 + "\n")
        
        try:
            sizing_style = SizingStyle(style.lower())
        except ValueError:
            print(f"Invalid style: {style}")
            print("Valid styles: swing, momentum, speculative, buy_hold")
            return
        
        # Get ATR% if symbol provided
        atr_pct = None
        if symbol:
            print(f"Fetching {symbol} data for ATR calculation...")
            daily_df = self.client.get_daily(symbol, months=3)
            if daily_df is not None:
                atr_pct = calculate_atr_percent(daily_df)
                print(f"  ATR%: {atr_pct*100:.1f}%")
        
        # Get deployment level from macro
        deployment = 100.0
        if self.macro.get_last_score():
            deployment = self.macro.get_last_score().deployment_pct
            print(f"  Deployment: {deployment:.0f}%")
        
        result = self.sizer.calculate(
            style=sizing_style,
            entry_price=entry,
            stop_price=stop,
            atr_percent=atr_pct,
            deployment_pct=deployment
        )
        
        display_symbol = symbol or "STOCK"
        report = self.sizer.format_calculation(
            display_symbol, sizing_style, entry, stop, result
        )
        print(report)
    
    def cmd_watchlist(self, symbols: list, style: str = 'swing'):
        """Analyze multiple symbols and rank them"""
        self._init_components()
        
        print("\n" + "="*80)
        print("WATCHLIST ANALYSIS")
        print("="*80 + "\n")
        
        try:
            trading_style = TradingStyle(style.lower())
        except ValueError:
            print(f"Invalid style: {style}")
            return
        
        # Update macro and sectors first
        print("Updating market context...")
        spy_df = self.client.get_daily('SPY', months=12)
        vix_quote = self.client.get_quote('VIX')
        vix_price = vix_quote['last'] if vix_quote else 20.0
        self.macro.calculate(spy_df, vix_price)
        self.sectors.update_rankings(spy_df)
        
        macro_score = self.macro.get_last_score()
        print(f"  Macro Score: {macro_score.total_score:.2f} | Deploy: {macro_score.deployment_pct:.0f}%")
        print(f"  Top Sectors: {', '.join(self.sectors.get_top_sectors(3))}")
        print()
        
        # Analyze each symbol
        results = []
        print(f"Analyzing {len(symbols)} symbols...")
        
        for i, symbol in enumerate(symbols, 1):
            symbol = symbol.upper()
            print(f"  [{i}/{len(symbols)}] {symbol}...", end=" ")
            
            try:
                score = self.scorer.score_stock(symbol, style=trading_style)
                results.append(score)
                print(f"Score: {score.total_score:.2f} - {score.recommendation.value}")
            except Exception as e:
                print(f"Error: {e}")
        
        # Sort by score
        results.sort(key=lambda x: x.total_score, reverse=True)
        
        # Print ranked table
        print("\n" + "-"*80)
        print("RANKED RESULTS")
        print("-"*80)
        print(f"{'Rank':<5} {'Symbol':<8} {'Score':<8} {'Sector':<15} {'Setup':<15} {'Recommendation':<15}")
        print("-"*80)
        
        for i, score in enumerate(results, 1):
            setup = score.setup.setup_type if score.setup else "None"
            print(
                f"{i:<5} {score.symbol:<8} {score.total_score:<8.2f} "
                f"{score.sector_name[:14]:<15} {setup:<15} {score.recommendation.value:<15}"
            )
        
        print("-"*80)
        
        # Highlight top pick
        if results and results[0].recommendation != Recommendation.AVOID:
            top = results[0]
            print(f"\n🎯 TOP PICK: {top.symbol}")
            if top.setup:
                print(f"   Entry: ${top.setup.entry_price:.2f} | Stop: ${top.setup.stop_price:.2f} | R:R {top.setup.risk_reward:.1f}:1")
        
        print("="*80 + "\n")
        
        return results
    
    def cmd_quick(self, symbol: str):
        """Quick pass/fail scan"""
        self._init_components()
        
        symbol = symbol.upper()
        print(f"\n{'='*60}")
        print(f"QUICK SCAN: {symbol}")
        print(f"{'='*60}\n")
        
        # Quick checks without full update
        quote = self.client.get_quote(symbol)
        if not quote:
            print("❌ FAIL: Unable to fetch quote")
            return
        
        price = quote['last']
        sector_info = get_sector_info(symbol)
        
        checks = []
        
        # Check 1: Sector rank
        sector_etf = sector_info.sector_etf if sector_info else 'SPY'
        sector_rank = self.sectors.get_sector_rank(sector_etf)
        sector_pass = sector_rank <= 5
        checks.append(('Sector Top 5', sector_pass, f"{sector_info.sector_name if sector_info else 'Unknown'} (#{sector_rank})"))
        
        # Check 2: Above 200 DMA
        daily_df = self.client.get_daily(symbol, months=12)
        if daily_df is not None and len(daily_df) >= 200:
            sma_200 = daily_df['close'].rolling(200).mean().iloc[-1]
            above_200 = price > sma_200
            pct_vs_200 = (price / sma_200 - 1) * 100
            checks.append(('Above 200 DMA', above_200, f"{pct_vs_200:+.1f}%"))
        else:
            checks.append(('Above 200 DMA', False, "Insufficient data"))
        
        # Check 3: RSI not extreme
        from technical import calculate_rsi
        if daily_df is not None:
            rsi = calculate_rsi(daily_df)
            rsi_ok = 30 <= rsi <= 70
            checks.append(('RSI Normal', rsi_ok, f"RSI: {rsi:.0f}"))
        
        # Check 4: Volume
        if daily_df is not None and 'volume' in daily_df.columns:
            avg_vol = daily_df['volume'].mean()
            vol_ok = avg_vol > 500000
            checks.append(('Volume > 500K', vol_ok, f"{avg_vol/1e6:.1f}M avg"))
        
        # Print results
        all_pass = all(c[1] for c in checks)
        
        for name, passed, detail in checks:
            icon = "✓" if passed else "✗"
            color = COLORS['green'] if passed else COLORS['red']
            print(f"  {color}{icon}{COLORS['reset']} {name}: {detail}")
        
        print()
        if all_pass:
            print(f"{COLORS['green']}✓ PASS - Meets basic criteria{COLORS['reset']}")
            print("  Run full analysis: analyze <symbol>")
        else:
            print(f"{COLORS['red']}✗ FAIL - Does not meet criteria{COLORS['reset']}")
        
        print(f"{'='*60}\n")


#############################################
# MAIN
#############################################

def main():
    parser = argparse.ArgumentParser(
        description='Blueprint Trading Analyzer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py analyze NVDA               # Full analysis of NVDA
  python main.py analyze AAPL -s momentum   # Momentum analysis
  python main.py sectors                    # Show sector rankings
  python main.py macro                      # Show macro score
  python main.py size swing 150.00 145.00   # Calculate position size
  python main.py quick MSFT                 # Quick pass/fail scan
  python main.py watchlist NVDA AAPL MSFT   # Analyze multiple stocks
        """
    )
    
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Analyze command
    analyze_p = subparsers.add_parser('analyze', help='Analyze a stock')
    analyze_p.add_argument('symbol', help='Stock ticker')
    analyze_p.add_argument('-s', '--style', default='swing',
                          choices=['swing', 'momentum', 'buy_hold', 'speculative'],
                          help='Trading style')
    analyze_p.add_argument('--no-sectors', action='store_true',
                          help='Skip sector ranking update')
    analyze_p.add_argument('--no-macro', action='store_true',
                          help='Skip macro score update')
    
    # Quick scan command
    quick_p = subparsers.add_parser('quick', help='Quick pass/fail scan')
    quick_p.add_argument('symbol', help='Stock ticker')
    
    # Sectors command
    sectors_p = subparsers.add_parser('sectors', help='Show sector rankings')
    sectors_p.add_argument('--no-update', action='store_true',
                          help='Use cached rankings')
    
    # Macro command
    macro_p = subparsers.add_parser('macro', help='Show macro score')
    macro_p.add_argument('--fed', choices=['CUTTING', 'HOLDING_DOVISH', 'NEUTRAL',
                                           'HOLDING_HAWKISH', 'HIKING'],
                        help='Set Fed policy stance')
    macro_p.add_argument('--event-risk', action='store_true',
                        help='Flag high event risk')
    
    # Size command
    size_p = subparsers.add_parser('size', help='Calculate position size')
    size_p.add_argument('style', choices=['swing', 'momentum', 'speculative', 'buy_hold'],
                       help='Trading style')
    size_p.add_argument('entry', type=float, help='Entry price')
    size_p.add_argument('stop', type=float, help='Stop price')
    size_p.add_argument('-t', '--ticker', help='Ticker for ATR calculation')
    
    # Watchlist command
    watchlist_p = subparsers.add_parser('watchlist', help='Analyze multiple stocks')
    watchlist_p.add_argument('symbols', nargs='+', help='Stock tickers')
    watchlist_p.add_argument('-s', '--style', default='swing',
                            choices=['swing', 'momentum', 'buy_hold', 'speculative'],
                            help='Trading style')
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    
    if not args.command:
        parser.print_help()
        return
    
    cli = BlueprintCLI()
    
    try:
        if args.command == 'analyze':
            cli.cmd_analyze(
                args.symbol,
                style=args.style,
                update_sectors=not args.no_sectors,
                update_macro=not args.no_macro
            )
        elif args.command == 'quick':
            cli.cmd_quick(args.symbol)
        elif args.command == 'sectors':
            cli.cmd_sectors(force_update=not args.no_update)
        elif args.command == 'macro':
            cli.cmd_macro(fed_policy=args.fed, event_risk=args.event_risk)
        elif args.command == 'size':
            cli.cmd_size(args.style, args.entry, args.stop, symbol=args.ticker)
        elif args.command == 'watchlist':
            cli.cmd_watchlist(args.symbols, style=args.style)
    
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        logging.error(f"Error: {e}", exc_info=args.verbose)
        print(f"\nError: {e}")
        if args.verbose:
            raise


if __name__ == '__main__':
    main()
