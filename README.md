# 0DTE Options Strategy Bot

Real-time scanner for **same-day (0DTE)** options across selected tickers using Yahoo Finance.
Filters by **volume**, **volume>OI**, and **tight spreads**, then adds **VWAP, RSI, and ROC**
context to post concise trade ideas to Discord.

## Features
- 0DTE chain scan (calls & puts) for today's expiry
- Liquidity guards: min volume, volume/OI ratio, spread % of mid
- Momentum context: VWAP, RSI (with fallback), 14-period ROC
- Discord posting with smart chunking; prints to console if webhook is missing
- Configurable entirely via `.env`

## Quick Start
```bash
# 1) Create & activate a virtual env
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 2) Install deps
pip install -r requirements.txt

# 3) Configure
cp .env.example .env
# Edit .env and set DISCORD_WEBHOOK_URL, optional tickers & thresholds

# 4) Run
python zero_dte_strategy_bot.py
```

## Environment Variables
| Var | Default | Notes |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | *(required for Discord posting)* | If empty, bot prints output instead |
| `TICKERS` | `SPY,QQQ,AAPL,TSLA,NVDA,AMD,META` | Comma-separated tickers |
| `MIN_VOLUME` | `5000` | Minimum contract volume |
| `OI_RATIO_THRESHOLD` | `0.5` | volume / (openInterest + 1) |
| `SPREAD_PCT_MAX` | `0.25` | Max bid/ask spread as % of mid |
| `REFRESH_SECONDS` | `300` | Loop delay |
| `MARKET_OPEN` | `06:30` | HH:MM (server local time) |
| `MARKET_CLOSE` | `13:00` | HH:MM (server local time) |

## Notes
- Tested on Python 3.10–3.12
- This is educational code; not financial advice. Use at your own risk.

## License
MIT — see `LICENSE`.