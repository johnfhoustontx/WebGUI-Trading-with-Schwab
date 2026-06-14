# Claude is the Driver

Automated virtual trading system. $10,000 virtual book across three strategy buckets.

## Architecture

| Module | Role |
|---|---|
| `config.py` | All parameters — capital, risk limits, endpoints, thresholds |
| `morning_agent.py` | Orchestrator — runs at 9:28am ET, pulls all data, grades the day |
| `trade_selector.py` | Decision engine — structure + strike selection |
| `approval_server.py` | FastAPI server — opens browser, waits for your APPROVE/SKIP |
| `order_executor.py` | Places bracket orders via Schwab API |
| `intraday_monitor.py` | Polls positions, closes at 50% target or EOD |

## Your only daily action

Check your browser at 9:30am. Click **APPROVE** or **SKIP**. Everything else is automated.  
Auto-skips after 15 minutes if no response.

## Setup

```
cd D:\Claude_is_the_Driver
pip install -r requirements.txt
```

## Run

```
start_all.bat
```

Or manually:
```
# Terminal 1
python approval_server.py

# Terminal 2
python morning_agent.py          # waits for 9:28am
python morning_agent.py --now    # runs immediately (testing)
```

## Buckets

| Bucket | Strategy | Allocation | Max risk/trade |
|---|---|---|---|
| A | SPX 0-DTE options (IC / PCS / CCS) | $4,000 | $300 |
| B | Equity day trades (QQQ/SPY/NVDA/TSLA) | $3,500 | $150 |
| C | MES micro futures (ORB) | $1,500 | $75 |
| Reserve | Drawdown buffer | $1,000 | — |

## Risk limits

| Limit | Amount |
|---|---|
| Daily max loss | $250 |
| Weekly max loss | $600 |
| Monthly drawdown | $1,500 |

## Data files

- `data/trade_log.json` — all trades with P&L
- `data/pending_trade.json` — current pending trade awaiting approval
- `logs/` — per-day log files for each module

## Dependencies

Requires existing services running:
- Schwab proxy on port 8100 (`D:\AI_Based_Analysis\SchwabProxy\schwab_proxy.py`)
- OptionsAnalytics on port 8200 (`D:\Schwab Test Project\OptionsAnalytics\main.py`)
- ML prediction servers on ports 8000–8052

## Schwab re-auth

If the proxy token expires (every 7 days):
```
http://127.0.0.1:8100/auth
```
