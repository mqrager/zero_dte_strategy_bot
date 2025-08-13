# zero_dte_strategy_bot_patched.py
import os
import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime, time
import time as t

# Optional: load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ==============================
# CONFIG (env-first, with sane defaults)
# ==============================
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TICKERS = [s.strip().upper() for s in os.getenv("TICKERS", "SPY,QQQ,AAPL,TSLA,NVDA,AMD,META").split(",") if s.strip()]
MIN_VOLUME = int(os.getenv("MIN_VOLUME", "5000"))
OI_RATIO_THRESHOLD = float(os.getenv("OI_RATIO_THRESHOLD", "0.5"))  # volume / (openInterest + 1)
SPREAD_PCT_MAX = float(os.getenv("SPREAD_PCT_MAX", "0.25"))  # 25% cap
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "300"))  # 5 min

def _parse_time(val, fallback):
    try:
        hh, mm = [int(x) for x in val.split(":")]
        return time(hh, mm)
    except Exception:
        return fallback

MARKET_OPEN  = _parse_time(os.getenv("MARKET_OPEN",  "06:30"), time(6, 30))
MARKET_CLOSE = _parse_time(os.getenv("MARKET_CLOSE", "13:00"), time(13, 0))

# ==============================
# Helpers
# ==============================
def post_to_discord(message: str):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ DISCORD_WEBHOOK_URL not set; printing instead:\n", message[:500])
        return
    payload = {"content": message}
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        if r.status_code >= 300:
            print(f"❌ Discord post failed [{r.status_code}]: {r.text[:200]}")
    except Exception as e:
        print("❌ Failed to post to Discord:", e)

def get_same_day_options(ticker: str) -> pd.DataFrame | None:
    print(f"📥 Downloading options for {ticker}...")
    tk = yf.Ticker(ticker)
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        options = tk.options
    except Exception as e:
        print(f"⚠️ Options lookup error for {ticker}: {e}")
        return None
    if not options or today not in options:
        print(f"⚠️ No options data for today: {ticker}")
        return None
    try:
        chain = tk.option_chain(today)
    except Exception as e:
        print(f"⚠️ option_chain fetch failed for {ticker}: {e}")
        return None
    calls, puts = chain.calls.copy(), chain.puts.copy()
    frames = []
    for df, tside in [(calls, "CALL"), (puts, "PUT")]:
        if df is None or df.empty:
            continue
        df = df.copy()
        df["type"] = tside
        for col, fill in [("openInterest", 0), ("volume", 0), ("bid", 0.0), ("ask", 0.0), ("lastPrice", 0.0)]:
            if col in df.columns:
                df[col] = df[col].fillna(fill)
        df["oi_ratio"] = df["volume"] / (df["openInterest"] + 1)
        df["mid"] = (df["bid"] + df["ask"]) / 2
        df["spread_pct"] = (df["ask"] - df["bid"]) / (df["mid"] + 1e-5)
        frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)

def _rsi_fallback(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(period).mean()
    roll_down = down.rolling(period).mean().replace(0, np.nan)
    rs = roll_up / roll_down
    return (100 - 100 / (1 + rs))

def compute_technicals(ticker: str):
    print(f"📊 Downloading historical price data for {ticker}...")
    try:
        hist = yf.download(ticker, period="5d", interval="1m", progress=False, auto_adjust=True)
    except Exception as e:
        print(f"⚠️ yfinance error for {ticker}: {e}")
        return None

    if hist is None or hist.empty or len(hist) < 30:
        print(f"⚠️ Insufficient historical data for {ticker}")
        return None

    vol_cum = hist["Volume"].replace(0, np.nan).cumsum()
    hist["vwap"] = (hist["Close"] * hist["Volume"]).cumsum() / vol_cum

    # RSI: try 'ta' library if available, else fallback
    rsi_series = None
    try:
        import ta
        rsi_series = ta.momentum.RSIIndicator(close=hist["Close"].squeeze()).rsi()
    except Exception:
        rsi_series = _rsi_fallback(hist["Close"])

    hist["rsi"] = rsi_series
    hist["roc"] = hist["Close"].pct_change(periods=14)

    close = float(hist["Close"].iloc[-1])
    vwap = float(hist["vwap"].iloc[-1])
    rsi = float(hist["rsi"].iloc[-1])
    roc = float(hist["roc"].iloc[-1])

    return {"price": close, "vwap": vwap, "rsi": rsi, "roc": roc, "above_vwap": bool(close > vwap)}

def build_strategy(row, bias, rsi):
    trade = ""
    reason = ""
    risk = []

    if bias == "Bullish":
        trade = "Buy CALL or debit spread"
        reason = "Above VWAP & positive momentum"
    elif bias == "Bearish":
        trade = "Buy PUT or debit spread"
        reason = "Below VWAP & negative momentum"
    else:
        trade = "Iron condor or butterfly"
        reason = "Neutral RSI & minimal ROC"

    try:
        if float(row.get("impliedVolatility", 0)) > 0.5:
            risk.append("⚠️ IV risk: potential crush")
    except Exception:
        pass
    try:
        if float(row.get("spread_pct", 0)) > 0.1:
            risk.append("⚠️ Liquidity: wide spread")
    except Exception:
        pass
    if rsi > 70:
        risk.append("⚠️ RSI overbought")
    elif rsi < 30:
        risk.append("⚠️ RSI oversold")

    return trade, reason, " | ".join(risk)

def format_contract(row, bias, rsi, roc):
    iv = round(float(row.get('impliedVolatility', 0.0)) * 100, 2)
    spread = round(float(row.get('spread_pct', 0.0)) * 100, 2)
    oi_ratio = round(float(row.get('oi_ratio', 0.0)), 2)
    roc_pct = round(float(roc) * 100, 2)
    rsi = round(float(rsi), 2)

    trade, reason, risk = build_strategy(row, bias, rsi)

    last_price = float(row.get('lastPrice', 0.0)) or float(row.get('mid', 0.0)) or 0.0
    if row.get('type') == 'CALL':
        target_price = round(last_price * 1.25, 2)
    elif row.get('type') == 'PUT':
        target_price = round(last_price * 1.35, 2)
    else:
        target_price = round(last_price * 1.30, 2)

    flow_spike = "🔥 Flow Spike" if oi_ratio > 10 or float(row.get('volume', 0)) > 100000 else ""
    trailing_stop = round(last_price * 0.85, 2) if last_price > 0 else 0.0

    return (
        f"**{row.get('contractSymbol','')}** ({row.get('type','?')})  {flow_spike}\n"
        f"Strike: `{row.get('strike')}` | Price: `{last_price}` → 🎯 Target: `${target_price}` | 🛑 Stop: `${trailing_stop}`\n"
        f"Vol: `{row.get('volume',0)}` | OI: `{row.get('openInterest',0)}` | IV: `{iv}%`\n"
        f"Spread: `{spread}%` | OI Ratio: `{oi_ratio}`\n"
        f"RSI: `{rsi}` | ROC: `{roc_pct}%`\n"
        f"💡 Strategy: *{trade}*\n"
        f"🎯 Reason: {reason}\n"
        f"{risk if risk else ''}\n"
    )

def run():
    banner = (
        f"📈 **0DTE Options Strategy Scanner** ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n"
        "This scanner identifies the most active and efficient 0DTE options based on:\n"
        "- High volume and strong open interest flow\n"
        "- Low bid/ask spread for optimal entry\n"
        "- Momentum bias using VWAP, RSI, and ROC\n"
        "- Suggested strategy based on trend strength\n"
    )

    full_report = [banner]

    for symbol in TICKERS:
        try:
            print(f"\n🔍 Analyzing {symbol}...")
            tech = compute_technicals(symbol)
            if tech is None:
                continue

            opt_data = get_same_day_options(symbol)
            if opt_data is None or opt_data.empty:
                continue

            filtered = opt_data[
                (opt_data["volume"] > MIN_VOLUME) &
                (opt_data["oi_ratio"] > OI_RATIO_THRESHOLD) &
                (opt_data["spread_pct"] < SPREAD_PCT_MAX)
            ].sort_values(by=["spread_pct", "volume"], ascending=[True, False])

            if filtered.empty:
                continue

            bias = (
                "Bullish" if tech["above_vwap"] and tech["roc"] > 0
                else "Bearish" if (not tech["above_vwap"]) and tech["roc"] < 0
                else "Neutral"
            )
            full_report.append(f"🔹 **{symbol}** — Bias: {bias}")

            top_pick = filtered.head(1).iloc[0].to_dict()
            full_report.append(f"⭐ **Top Pick:** {top_pick.get('contractSymbol','')} — Tightest spread & high volume")

            for _, row in filtered.head(3).iterrows():
                summary = format_contract(row.to_dict(), bias, tech["rsi"], tech["roc"])
                full_report.append(summary)
        except Exception as e:
            print(f"{symbol}: error {e}")

    if len(full_report) > 1:
        msg = ""
        for line in full_report:
            if len(msg) + len(line) > 1800:
                post_to_discord(msg)
                msg = line + "\n"
            else:
                msg += line + "\n"
        post_to_discord(msg)
    else:
        post_to_discord("No valid 0DTE trade setups found today.")

if __name__ == "__main__":
    while True:
        now = datetime.now().time()
        if MARKET_OPEN <= now <= MARKET_CLOSE:
            print("\n🟢 Running 0DTE scanner...")
            run()
        else:
            print("🔒 Outside market hours. Sleeping...")
        t.sleep(REFRESH_SECONDS)
