
# SK BOT PRO FINAL - main.py
# 50 strategies + 5 meta-filters voting system
# 3/5 meta-filters agreement = SIGNAL, otherwise AVOID

import os
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from enum import Enum

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
from pydantic import BaseModel

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('sk_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# ==================== CONFIGURATION ====================
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")

# 15 liquid real FX pairs
MARKETS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
    "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP",
    "AUDJPY", "EURAUD", "EURCAD", "GBPAUD", "GBPCHF",
]

TIMEFRAMES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}
BANGLADESH_TZ = timezone(timedelta(hours=6))
UTC_TZ = timezone.utc

# ==================== VOTING SYSTEM ====================
STRATEGY_COUNT = 50
CONSENSUS_REQUIRED = 35  # 70% of 50 strategies

# Meta-filters configuration
META_FILTERS = {
    "Trend & Structure": list(range(0, 10)),
    "Momentum & Oscillator": list(range(10, 20)),
    "Volatility & Breakout": list(range(20, 30)),
    "Price Action & Flow": list(range(30, 40)),
    "Multi-Timeframe & Confluence": list(range(40, 50)),
}

# Strategy names and weights
STRATEGIES = [
    # Meta 1 — Trend & Structure (10)
    ("EMA 9/21 Trend", 5), ("EMA 21/50 Trend", 5), ("SMA 20/50 Trend", 4),
    ("ADX + DI Direction", 5), ("Trend Alignment", 5), ("Price vs EMA21", 4),
    ("Price vs SMA50", 4), ("EMA Slope", 4), ("Higher-High/Lows", 4), ("Trend Persistence", 4),
    # Meta 2 — Momentum & Oscillator (10)
    ("RSI Regime", 5), ("MACD Direction", 5), ("MACD Histogram", 4), ("Stochastic", 4),
    ("Stochastic RSI", 4), ("CCI", 4), ("Williams %R", 4), ("ROC", 4),
    ("Momentum", 4), ("RSI-MACD Confluence", 5),
    # Meta 3 — Volatility & Breakout (10)
    ("ATR Regime", 4), ("Bollinger Position", 4), ("Bollinger Breakout", 5),
    ("Donchian Breakout", 5), ("Range Expansion", 4), ("ATR Direction", 4),
    ("Volatility Compression", 4), ("Breakout Retest", 5), ("Channel Bias", 4), ("Volatility Trend", 4),
    # Meta 4 — Price Action & Flow (10)
    ("Candle Momentum", 3), ("Bull/Bear Body", 3), ("5-Bar Trend", 3), ("10-Bar Trend", 3),
    ("20-Bar Trend", 4), ("Support/Resistance", 4), ("Flow Proxy", 3), ("Price Impulse", 4),
    ("Wick Rejection", 4), ("Close Location", 3),
    # Meta 5 — Multi-Timeframe & Confluence (10)
    ("Fast-Mid-Slow EMA", 5), ("EMA + ADX", 5), ("RSI + Stoch", 4), ("MACD + ROC", 4),
    ("BB + RSI", 4), ("Donchian + ADX", 5), ("S/R + Momentum", 4), ("Trend + Volatility", 4),
    ("Price + Momentum Stack", 5), ("Master Confluence", 6),
]

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="SK BOT PRO",
    version="3.0",
    description="50 Strategies + 5 Meta-Filters Voting System | 3/5 Meta Agreement = SIGNAL"
)

# ==================== HTML DASHBOARD ====================
HTML = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SK BOT PRO</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e1a;color:#e8edf5;font-family:'Segoe UI',Arial,sans-serif}
.sidebar{position:fixed;left:0;top:0;height:100%;width:220px;background:#111827;border-right:1px solid #1f2937;padding:20px 0}
.logo{padding:0 20px 30px;border-bottom:1px solid #1f2937;margin-bottom:20px}
.logo h2{font-size:18px;color:#6d5dfc}
.logo span{font-size:11px;color:#6b7280}
.nav-links{list-style:none;padding:0 12px}
.nav-links li{margin:4px 0}
.nav-links li a{display:block;padding:10px 16px;border-radius:10px;color:#9ca3af;text-decoration:none;font-size:14px;transition:all 0.3s}
.nav-links li a:hover,.nav-links li.active a{background:#1f2937;color:#fff}
.nav-links li.active a{background:#6d5dfc;color:#fff}
.main{margin-left:220px;padding:24px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:30px}
.header h1{font-size:24px}
.status-badge{background:#065f46;color:#34d399;padding:6px 16px;border-radius:20px;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:30px}
.card{background:#111827;border:1px solid #1f2937;border-radius:16px;padding:20px}
.card .label{color:#6b7280;font-size:12px}
.card .value{font-size:28px;font-weight:700;margin-top:4px}
.card .value.green{color:#34d399}
.card .value.red{color:#f87171}
.card .value.yellow{color:#fbbf24}
.signal-box{background:#111827;border:1px solid #1f2937;border-radius:16px;padding:24px;margin-bottom:30px}
.signal-box .action{font-size:48px;font-weight:900;text-align:center;padding:20px}
.signal-box .action.buy{color:#34d399}
.signal-box .action.sell{color:#f87171}
.signal-box .action.avoid{color:#fbbf24}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:500px;margin:auto}
.grid-2 .stat{text-align:center;background:#0a0e1a;border-radius:12px;padding:12px}
.grid-2 .stat .num{font-size:22px;font-weight:700}
.meta-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
.meta-item{background:#0a0e1a;border-radius:10px;padding:12px;text-align:center}
.meta-item .dir{font-weight:700}
.meta-item .dir.buy{color:#34d399}
.meta-item .dir.sell{color:#f87171}
.meta-item .dir.neutral{color:#fbbf24}
select,button{width:100%;padding:12px;border-radius:10px;border:1px solid #1f2937;background:#0a0e1a;color:#fff;font-size:14px;margin:6px 0}
button{background:#6d5dfc;border:0;font-weight:700;cursor:pointer}
button:hover{background:#5b4be0}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1f2937}
.small{color:#6b7280;font-size:12px}
.hidden{display:none}
@media(max-width:768px){.sidebar{width:60px;padding:10px 0}.sidebar .logo h2,.sidebar .logo span,.sidebar .nav-links li a span{display:none}.sidebar .logo{padding:0 0 20px;text-align:center}.main{margin-left:60px;padding:12px}.header h1{font-size:18px}.cards{grid-template-columns:1fr 1fr}.grid-2{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>

<div class="sidebar">
    <div class="logo">
        <h2>SK BOT</h2>
        <span>PRO FINAL</span>
    </div>
    <ul class="nav-links">
        <li class="active"><a href="/"><span>📊 Dashboard</span></a></li>
        <li><a href="/"><span>📈 Signals</span></a></li>
        <li><a href="/"><span>⚙️ Settings</span></a></li>
    </ul>
</div>

<div class="main">
    <div class="header">
        <h1>📊 SK BOT PRO</h1>
        <span class="status-badge" id="marketStatus">● LOADING</span>
    </div>

    <!-- Stats -->
    <div class="cards">
        <div class="card"><div class="label">Total Strategies</div><div class="value">50</div></div>
        <div class="card"><div class="label">Meta Filters</div><div class="value">5</div></div>
        <div class="card"><div class="label">Meta Required</div><div class="value" style="color:#fbbf24">3/5</div></div>
        <div class="card"><div class="label">Status</div><div class="value green" id="statusText">● ONLINE</div></div>
    </div>

    <!-- Signal Generator -->
    <div class="signal-box">
        <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:10px;margin-bottom:16px">
            <select id="market">
                <option value="EURUSD">EURUSD</option>
                <option value="GBPUSD">GBPUSD</option>
                <option value="USDJPY">USDJPY</option>
                <option value="AUDUSD">AUDUSD</option>
                <option value="USDCAD">USDCAD</option>
                <option value="USDCHF">USDCHF</option>
                <option value="NZDUSD">NZDUSD</option>
                <option value="EURJPY">EURJPY</option>
                <option value="GBPJPY">GBPJPY</option>
                <option value="EURGBP">EURGBP</option>
                <option value="AUDJPY">AUDJPY</option>
                <option value="EURAUD">EURAUD</option>
                <option value="EURCAD">EURCAD</option>
                <option value="GBPAUD">GBPAUD</option>
                <option value="GBPCHF">GBPCHF</option>
            </select>
            <select id="timeframe">
                <option value="1min">1 MIN</option>
                <option value="5min">5 MIN</option>
                <option value="15min">15 MIN</option>
                <option value="30min">30 MIN</option>
                <option value="60min">1 HOUR</option>
            </select>
            <select id="mode">
                <option value="REAL">REAL FX</option>
                <option value="OTC">OTC</option>
            </select>
            <button onclick="getSignal()">🔍 SIGNAL</button>
        </div>

        <div id="result" class="hidden">
            <div class="action" id="actionText">AVOID</div>
            <div class="grid-2">
                <div class="stat"><div class="small">BUY</div><div class="num green" id="buyVotes">0</div></div>
                <div class="stat"><div class="small">SELL</div><div class="num red" id="sellVotes">0</div></div>
                <div class="stat"><div class="small">NEUTRAL</div><div class="num yellow" id="neutralVotes">0</div></div>
                <div class="stat"><div class="small">CONSENSUS</div><div class="num" id="consensusPct">0%</div></div>
            </div>

            <div id="metaResults" class="meta-grid"></div>

            <div class="row"><span>Entry Price</span><b id="priceText">-</b></div>
            <div class="row"><span>Entry UTC+6</span><b id="entryTime">-</b></div>
            <div class="row"><span>Expiry UTC+6</span><b id="expiryTime">-</b></div>
            <div class="row"><span>News Sentiment</span><b id="newsText">-</b></div>
            <div class="row"><span>Meta Agreement</span><b id="metaAgree">0/5</b></div>
            <div style="text-align:center;margin-top:12px;font-size:11px;color:#6b7280">3/5 Meta Agreement = SIGNAL | 35/50 Consensus = STRONG</div>
        </div>
    </div>

    <div class="card" style="background:#111827;border:1px solid #1f2937;border-radius:16px;padding:16px">
        <div class="small">⚠️ OTC requires genuine connected OTC feed. No fake signals generated.</div>
        <div class="small" style="margin-top:6px">📊 50 Strategies | 5 Meta-Filters | 3/5 Meta Agreement</div>
    </div>
</div>

<script>
async function getSignal() {
    const market = document.getElementById('market').value;
    const tf = document.getElementById('timeframe').value;
    const mode = document.getElementById('mode').value;
    const result = document.getElementById('result');
    const action = document.getElementById('actionText');

    try {
        const res = await fetch(`/signal?symbol=${market}&timeframe=${tf}&mode=${mode}`);
        const d = await res.json();

        if (d.detail) { alert(d.detail); return; }

        result.classList.remove('hidden');
        action.textContent = d.action;
        action.className = 'action ' + (d.action === 'BUY' ? 'buy' : d.action === 'SELL' ? 'sell' : 'avoid');

        document.getElementById('buyVotes').textContent = d.buy_votes;
        document.getElementById('sellVotes').textContent = d.sell_votes;
        document.getElementById('neutralVotes').textContent = d.neutral_votes;
        document.getElementById('consensusPct').textContent = d.consensus_percent + '%';
        document.getElementById('priceText').textContent = d.entry_price;
        document.getElementById('entryTime').textContent = d.entry_time_utc6;
        document.getElementById('expiryTime').textContent = d.expiry_time_utc6;
        document.getElementById('newsText').textContent = d.news.sentiment;
        document.getElementById('metaAgree').textContent = d.meta_agreement + '/5';

        // Meta results
        const metaContainer = document.getElementById('metaResults');
        metaContainer.innerHTML = '';
        for (const [name, data] of Object.entries(d.meta_filters)) {
            const div = document.createElement('div');
            div.className = 'meta-item';
            const dir = data.direction;
            const color = dir === 'BUY' ? 'buy' : dir === 'SELL' ? 'sell' : 'neutral';
            div.innerHTML = `
                <div style="font-size:11px;color:#6b7280">${name}</div>
                <div class="dir ${color}">${dir}</div>
                <div style="font-size:11px;color:#6b7280">${data.consensus_percent}%</div>
            `;
            metaContainer.appendChild(div);
        }

    } catch(e) {
        alert('Error: ' + e.message);
    }
}

// Market status
async function updateStatus() {
    try {
        const res = await fetch('/health');
        const d = await res.json();
        document.getElementById('marketStatus').textContent = '● ' + d.status;
        document.getElementById('statusText').textContent = '● ONLINE';
    } catch(e) {
        document.getElementById('marketStatus').textContent = '● OFFLINE';
    }
}
updateStatus();
setInterval(updateStatus, 60000);
</script>

</body>
</html>
"""

# ==================== CORE FUNCTIONS ====================
def market_status_utc(now=None):
    now = now or datetime.now(UTC_TZ)
    wd = now.weekday()
    mins = now.hour * 60 + now.minute
    if wd == 5:
        return "CLOSED"
    if wd == 6:
        return "OPEN" if mins >= 22 * 60 else "CLOSED"
    if wd == 4:
        return "OPEN" if mins < 22 * 60 else "CLOSED"
    return "OPEN"

def utc_clock_payload(now=None):
    now = now or datetime.now(UTC_TZ)
    bd = now.astimezone(BANGLADESH_TZ)
    return {
        "utc": now.strftime("%Y-%m-%d %H:%M:%S"),
        "utc_offset": "UTC+00:00",
        "bangladesh": bd.strftime("%Y-%m-%d %H:%M:%S"),
        "bangladesh_offset": "UTC+06:00",
        "market_status": market_status_utc(now),
    }

async def get_fx_data(symbol: str, timeframe: str = "1min") -> pd.DataFrame:
    symbol = symbol.replace("/", "").upper()
    if len(symbol) != 6:
        raise HTTPException(400, "Use a market such as EURUSD.")
    if timeframe not in TIMEFRAMES:
        raise HTTPException(400, "Choose 1min, 5min, 15min, 30min or 60min.")

    if TWELVE_DATA_API_KEY:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                "https://api.twelvedata.com/time_series",
                params={
                    "symbol": f"{symbol[:3]}/{symbol[3:]}",
                    "interval": timeframe,
                    "outputsize": 250,
                    "apikey": TWELVE_DATA_API_KEY,
                },
            )
        data = response.json()
        if "values" in data:
            return pd.DataFrame([
                {"time": x["datetime"], "open": float(x["open"]),
                 "high": float(x["high"]), "low": float(x["low"]),
                 "close": float(x["close"])}
                for x in data["values"]
            ]).sort_values("time").reset_index(drop=True)

    if not ALPHA_VANTAGE_API_KEY:
        raise HTTPException(500, "Add TWELVE_DATA_API_KEY or ALPHA_VANTAGE_API_KEY first.")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "FX_INTRADAY",
                "from_symbol": symbol[:3],
                "to_symbol": symbol[3:],
                "interval": timeframe,
                "outputsize": "compact",
                "apikey": ALPHA_VANTAGE_API_KEY,
            },
        )
    data = response.json()
    if "Note" in data:
        raise HTTPException(429, data["Note"])
    if "Information" in data:
        raise HTTPException(429, data["Information"])
    key = next((k for k in data if "Time Series FX" in k), None)
    if not key:
        raise HTTPException(502, "No intraday FX data returned.")

    return pd.DataFrame([
        {"time": t, "open": float(x["1. open"]), "high": float(x["2. high"]),
         "low": float(x["3. low"]), "close": float(x["4. close"])}
        for t, x in data[key].items()
    ]).sort_values("time").reset_index(drop=True)

def indicators(df):
    df = df.copy()
    c, h, l = df.close, df.high, df.low

    for n in [9, 12, 21, 26, 50]:
        df[f"ema{n}"] = c.ewm(span=n, adjust=False).mean()
    for n in [20, 50]:
        df[f"sma{n}"] = c.rolling(n).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-12))

    df["macd"] = df.ema12 - df.ema26
    df["macd_signal"] = df.macd.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df.macd - df.macd_signal

    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + 2 * sd
    df["bb_lower"] = mid - 2 * sd
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].abs().replace(0, 1e-12)

    lo, hi = l.rolling(14).min(), h.rolling(14).max()
    df["stoch"] = 100 * (c - lo) / (hi - lo).replace(0, 1e-12)
    df["stoch_d"] = df.stoch.rolling(3).mean()

    sr_lo, sr_hi = df.stoch.rolling(14).min(), df.stoch.rolling(14).max()
    df["stoch_rsi"] = 100 * (df.stoch - sr_lo) / (sr_hi - sr_lo).replace(0, 1e-12)

    tp = (h + l + c) / 3
    df["cci"] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).std()).replace(0, 1e-12)

    df["williams"] = -100 * (hi - c) / (hi - lo).replace(0, 1e-12)
    df["roc"] = c.pct_change(12) * 100
    df["momentum"] = c - c.shift(10)

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    up, down = h.diff(), -l.diff()
    plus = up.where((up > down) & (up > 0), 0.0)
    minus = down.where((down > up) & (down > 0), 0.0)
    atr = df.atr.replace(0, 1e-12)

    pdi = 100 * plus.rolling(14).mean() / atr
    mdi = 100 * minus.rolling(14).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-12)

    df["adx"], df["pdi"], df["mdi"] = dx.rolling(14).mean(), pdi, mdi
    df["don_hi"] = h.shift(1).rolling(20).max()
    df["don_lo"] = l.shift(1).rolling(20).min()
    df["tenkan"] = (h.rolling(9).max() + l.rolling(9).min()) / 2
    df["kijun"] = (h.rolling(26).max() + l.rolling(26).min()) / 2
    df["sar_proxy"] = df.ema21 - 2 * df.atr
    df["flow_proxy"] = c.diff().abs().rolling(10).mean()

    return df

def calculate_votes(frame):
    df = indicators(frame).dropna().reset_index(drop=True)
    if len(df) < 60:
        raise HTTPException(502, "Not enough candles.")

    b, p = df.iloc[-1], df.iloc[-2]
    out = []

    def add(i, vote, reason):
        out.append({"strategy": STRATEGIES[i][0], "weight": STRATEGIES[i][1], "vote": vote, "reason": reason})

    # Meta 1: Trend & Structure
    add(0, "BUY" if b.ema9 > b.ema21 else "SELL", "EMA 9/21")
    add(1, "BUY" if b.ema21 > b.ema50 else "SELL", "EMA 21/50")
    add(2, "BUY" if b.sma20 > b.sma50 else "SELL", "SMA 20/50")
    add(3, "BUY" if b.adx >= 20 and b.pdi > b.mdi else "SELL" if b.adx >= 20 else "NEUTRAL", f"ADX {b.adx:.1f}")
    add(4, "BUY" if b.ema9 > b.ema21 > b.ema50 and b.close > b.ema9 else "SELL" if b.ema9 < b.ema21 < b.ema50 and b.close < b.ema9 else "NEUTRAL", "Alignment")
    add(5, "BUY" if b.close > b.ema21 else "SELL", "Price/EMA21")
    add(6, "BUY" if b.close > b.sma50 else "SELL", "Price/SMA50")
    ema_slope = b.ema21 - df.ema21.iloc[-6]
    add(7, "BUY" if ema_slope > 0 else "SELL" if ema_slope < 0 else "NEUTRAL", "EMA21 slope")
    hh = b.high > df.high.iloc[-6:-1].max() and b.low >= df.low.iloc[-6:-1].min()
    ll = b.low < df.low.iloc[-6:-1].min() and b.high <= df.high.iloc[-6:-1].max()
    add(8, "BUY" if hh else "SELL" if ll else "NEUTRAL", "Swing structure")
    up_count = sum(df.close.iloc[-8:].diff().dropna() > 0)
    add(9, "BUY" if up_count >= 5 else "SELL" if up_count <= 2 else "NEUTRAL", "Trend persistence")

    # Meta 2: Momentum & Oscillator
    add(10, "BUY" if b.rsi < 30 else "SELL" if b.rsi > 70 else "BUY" if b.rsi > 50 else "SELL", f"RSI {b.rsi:.1f}")
    add(11, "BUY" if b.macd > b.macd_signal else "SELL", "MACD")
    add(12, "BUY" if b.macd_hist > 0 else "SELL", "MACD histogram")
    add(13, "BUY" if b.stoch < 20 else "SELL" if b.stoch > 80 else "BUY" if b.stoch > b.stoch_d else "SELL", "Stochastic")
    add(14, "BUY" if b.stoch_rsi < 20 else "SELL" if b.stoch_rsi > 80 else "BUY" if b.stoch_rsi > 50 else "SELL", "Stoch RSI")
    add(15, "BUY" if b.cci > 0 else "SELL", "CCI")
    add(16, "BUY" if b.williams > -50 else "SELL", "Williams %R")
    add(17, "BUY" if b.roc > 0 else "SELL", "ROC")
    add(18, "BUY" if b.momentum > 0 else "SELL", "Momentum")
    add(19, "BUY" if b.rsi > 50 and b.macd > b.macd_signal else "SELL" if b.rsi < 50 and b.macd < b.macd_signal else "NEUTRAL", "RSI + MACD")

    # Meta 3: Volatility & Breakout
    atr_avg = df.atr.rolling(30).mean().iloc[-1]
    bb_width = (b.bb_upper - b.bb_lower) / max(abs(b.bb_mid), 1e-12)
    prev_bb_width = (p.bb_upper - p.bb_lower) / max(abs(p.bb_mid), 1e-12)
    add(20, "NEUTRAL" if b.atr > atr_avg * 1.8 else "BUY" if b.close > b.ema21 else "SELL", "ATR regime")
    add(21, "BUY" if b.close <= b.bb_lower else "SELL" if b.close >= b.bb_upper else "BUY" if b.close > b.bb_mid else "SELL", "Bollinger position")
    add(22, "BUY" if b.close > b.bb_upper else "SELL" if b.close < b.bb_lower else "NEUTRAL", "Bollinger breakout")
    add(23, "BUY" if b.close > b.don_hi else "SELL" if b.close < b.don_lo else "NEUTRAL", "Donchian")
    recent_range = df.high.iloc[-1] - df.low.iloc[-1]
    avg_range = (df.high - df.low).rolling(20).mean().iloc[-1]
    add(24, "BUY" if recent_range > avg_range * 1.25 and b.close > b.open else "SELL" if recent_range > avg_range * 1.25 and b.close < b.open else "NEUTRAL", "Range expansion")
    add(25, "BUY" if b.atr > p.atr and b.close > p.close else "SELL" if b.atr > p.atr and b.close < p.close else "NEUTRAL", "ATR direction")
    add(26, "BUY" if bb_width < df.bb_width.rolling(30).mean().iloc[-1] * 0.8 and b.close > b.bb_mid else "SELL" if bb_width < df.bb_width.rolling(30).mean().iloc[-1] * 0.8 and b.close < b.bb_mid else "NEUTRAL", "Volatility compression")
    retest_buy = p.close > p.don_hi and b.close > b.don_hi * 0.999
    retest_sell = p.close < p.don_lo and b.close < b.don_lo * 1.001
    add(27, "BUY" if retest_buy else "SELL" if retest_sell else "NEUTRAL", "Breakout retest")
    mid = (df.high.iloc[-21:-1].max() + df.low.iloc[-21:-1].min()) / 2
    add(28, "BUY" if b.close > mid else "SELL", "Channel bias")
    add(29, "BUY" if bb_width > prev_bb_width and b.close > b.bb_mid else "SELL" if bb_width > prev_bb_width and b.close < b.bb_mid else "NEUTRAL", "Volatility trend")

    # Meta 4: Price Action & Flow
    body = b.close - b.open
    rng = max(b.high - b.low, 1e-12)
    add(30, "BUY" if body > 0 else "SELL" if body < 0 else "NEUTRAL", "Candle momentum")
    add(31, "BUY" if body > rng * 0.55 else "SELL" if body < -rng * 0.55 else "NEUTRAL", "Body strength")
    add(32, "BUY" if b.close > df.close.iloc[-6] else "SELL", "5-bar")
    add(33, "BUY" if b.close > df.close.iloc[-11] else "SELL", "10-bar")
    add(34, "BUY" if b.close > df.close.iloc[-21] else "SELL", "20-bar")
    support = df.low.iloc[-21:-1].min()
    resistance = df.high.iloc[-21:-1].max()
    add(35, "BUY" if b.close <= support * 1.001 else "SELL" if b.close >= resistance * 0.999 else "BUY" if b.close > (support + resistance) / 2 else "SELL", "S/R")
    flow_mean = df.flow_proxy.rolling(10).mean().iloc[-1]
    add(36, "BUY" if b.close > p.close and b.flow_proxy > flow_mean else "SELL" if b.close < p.close and b.flow_proxy > flow_mean else "NEUTRAL", "Flow proxy")
    impulse = b.close - df.close.iloc[-4]
    add(37, "BUY" if impulse > b.atr * 0.5 else "SELL" if impulse < -b.atr * 0.5 else "NEUTRAL", "Price impulse")
    upper_wick = b.high - max(b.open, b.close)
    lower_wick = min(b.open, b.close) - b.low
    add(38, "BUY" if lower_wick > upper_wick * 1.5 else "SELL" if upper_wick > lower_wick * 1.5 else "NEUTRAL", "Wick rejection")
    add(39, "BUY" if (b.close - b.low) / rng > 0.65 else "SELL" if (b.close - b.low) / rng < 0.35 else "NEUTRAL", "Close location")

    # Meta 5: Multi-Timeframe & Confluence
    add(40, "BUY" if b.ema9 > b.ema21 > b.ema50 else "SELL" if b.ema9 < b.ema21 < b.ema50 else "NEUTRAL", "Fast/Mid/Slow EMA")
    add(41, "BUY" if b.ema21 > b.ema50 and b.adx >= 20 else "SELL" if b.ema21 < b.ema50 and b.adx >= 20 else "NEUTRAL", "EMA + ADX")
    add(42, "BUY" if b.rsi > 50 and b.stoch > b.stoch_d else "SELL" if b.rsi < 50 and b.stoch < b.stoch_d else "NEUTRAL", "RSI + Stoch")
    add(43, "BUY" if b.macd_hist > 0 and b.roc > 0 else "SELL" if b.macd_hist < 0 and b.roc < 0 else "NEUTRAL", "MACD + ROC")
    add(44, "BUY" if b.close > b.bb_mid and b.rsi > 50 else "SELL" if b.close < b.bb_mid and b.rsi < 50 else "NEUTRAL", "BB + RSI")
    add(45, "BUY" if b.close > b.don_hi and b.adx >= 20 else "SELL" if b.close < b.don_lo and b.adx >= 20 else "NEUTRAL", "Donchian + ADX")
    add(46, "BUY" if b.close > (support + resistance) / 2 and b.momentum > 0 else "SELL" if b.close < (support + resistance) / 2 and b.momentum < 0 else "NEUTRAL", "S/R + Momentum")
    add(47, "BUY" if b.ema21 > b.ema50 and b.atr <= atr_avg * 1.8 else "SELL" if b.ema21 < b.ema50 and b.atr <= atr_avg * 1.8 else "NEUTRAL", "Trend + Volatility")
    add(48, "BUY" if b.close > b.ema21 and b.rsi > 50 and b.macd_hist > 0 and b.momentum > 0 else "SELL" if b.close < b.ema21 and b.rsi < 50 and b.macd_hist < 0 and b.momentum < 0 else "NEUTRAL", "Price + Momentum")
    bullish = sum(x == "BUY" for x in [out[i]["vote"] for i in [0,1,3,10,11,18,32,33,40,41,48]])
    bearish = sum(x == "SELL" for x in [out[i]["vote"] for i in [0,1,3,10,11,18,32,33,40,41,48]])
    add(49, "BUY" if bullish >= 8 else "SELL" if bearish >= 8 else "NEUTRAL", "Master confluence")

    return out

async def get_news(symbol):
    if not ALPHA_VANTAGE_API_KEY:
        return {"status": "UNAVAILABLE", "sentiment": "NEUTRAL", "score": 0, "count": 0}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "NEWS_SENTIMENT",
                    "tickers": symbol,
                    "limit": 20,
                    "apikey": ALPHA_VANTAGE_API_KEY,
                },
            )

        feed = r.json().get("feed", [])
        scores = [float(x.get("overall_sentiment_score", 0)) for x in feed]
        avg = sum(scores) / len(scores) if scores else 0

        return {
            "status": "AVAILABLE",
            "sentiment": "BULLISH" if avg >= 0.15 else "BEARISH" if avg <= -0.15 else "NEUTRAL",
            "score": round(avg, 3),
            "count": len(feed),
        }
    except Exception as exc:
        return {"status": "ERROR", "sentiment": "NEUTRAL", "score": 0, "count": 0, "error": str(exc)}

async def make_signal(symbol, timeframe="1min", mode="REAL"):
    symbol = symbol.replace("/", "").upper()
    mode = mode.upper()

    if mode == "OTC":
        raise HTTPException(503, "OTC DATA UNAVAILABLE: connected OTC price feed is required.")
    if symbol not in MARKETS:
        raise HTTPException(400, f"Unsupported market: {', '.join(MARKETS)}")
    if timeframe not in TIMEFRAMES:
        raise HTTPException(400, "Choose 1min, 5min, 15min, 30min or 60min.")

    frame = await get_fx_data(symbol, timeframe)
    votes = calculate_votes(frame)

    buy = sum(x["vote"] == "BUY" for x in votes)
    sell = sum(x["vote"] == "SELL" for x in votes)
    neutral = STRATEGY_COUNT - buy - sell
    buy_weight = sum(x["weight"] for x in votes if x["vote"] == "BUY")
    sell_weight = sum(x["weight"] for x in votes if x["vote"] == "SELL")
    
    # ==================== META VOTING SYSTEM ====================
    meta_results = {}
    meta_directions = []
    
    for meta_name, idxs in META_FILTERS.items():
        mb = sum(votes[i]["vote"] == "BUY" for i in idxs)
        ms = sum(votes[i]["vote"] == "SELL" for i in idxs)
        mn = 10 - mb - ms
        
        if mb > ms:
            direction = "BUY"
        elif ms > mb:
            direction = "SELL"
        else:
            direction = "NEUTRAL"
        
        meta_directions.append(direction)
        
        meta_results[meta_name] = {
            "buy": mb,
            "sell": ms,
            "neutral": mn,
            "consensus_percent": round(max(mb, ms) / 10 * 100, 1),
            "direction": direction,
            "strength": "STRONG" if max(mb, ms) >= 8 else "MODERATE" if max(mb, ms) >= 6 else "WEAK"
        }
    
    # ==================== SIGNAL DECISION ====================
    buy_count = sum(1 for d in meta_directions if d == "BUY")
    sell_count = sum(1 for d in meta_directions if d == "SELL")
    
    # 3/5 meta agreement = SIGNAL
    if buy_count >= 3:
        leading_direction = "BUY"
        meta_agreement = buy_count
    elif sell_count >= 3:
        leading_direction = "SELL"
        meta_agreement = sell_count
    else:
        leading_direction = "NEUTRAL"
        meta_agreement = 0
    
    news = await get_news(symbol)
    
    conflict = (
        buy >= CONSENSUS_REQUIRED and news["sentiment"] == "BEARISH"
    ) or (
        sell >= CONSENSUS_REQUIRED and news["sentiment"] == "BULLISH"
    )

    if leading_direction == "BUY" and not conflict and meta_agreement >= 3:
        action = "BUY"
    elif leading_direction == "SELL" and not conflict and meta_agreement >= 3:
        action = "SELL"
    else:
        action = "AVOID"

    now = datetime.now(BANGLADESH_TZ)
    now_utc = now.astimezone(UTC_TZ)
    entry = now.replace(second=0, microsecond=0)
    entry_utc = entry.astimezone(UTC_TZ)
    expiry = entry + timedelta(minutes=TIMEFRAMES[timeframe])
    expiry_utc = expiry.astimezone(UTC_TZ)
    price = round(float(frame.close.iloc[-1]), 6)

    return {
        "symbol": symbol,
        "mode": mode,
        "timeframe": timeframe,
        "action": action,
        "buy_votes": buy,
        "sell_votes": sell,
        "neutral_votes": neutral,
        "buy_weight": buy_weight,
        "sell_weight": sell_weight,
        "weighted_consensus": max(buy_weight, sell_weight),
        "consensus_percent": round(max(buy, sell) / STRATEGY_COUNT * 100, 1),
        "required_votes": CONSENSUS_REQUIRED,
        "required_consensus_percent": 70,
        "meta_filters": meta_results,
        "meta_agreement": meta_agreement,
        "meta_filters_required": 3,
        "news": news,
        "price": price,
        "entry_price": price,
        "entry_time_utc": entry_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "expiry_time_utc": expiry_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "entry_time_utc6": entry.strftime("%Y-%m-%d %H:%M:%S"),
        "expiry_time_utc6": expiry.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "UTC+06:00",
        "updated_at": now.isoformat(),
        "utc_clock": utc_clock_payload(now_utc),
        "strategies": votes,
    }

# ==================== TELEGRAM BOT ====================
async def telegram_send(chat_id, message):
    if not TELEGRAM_TOKEN or not chat_id:
        return False
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message},
        )
        return r.is_success

def telegram_signal_message(x):
    return (
        f"🤖 SK BOT SIGNAL\n\n"
        f"📊 {x['symbol']} | {x['timeframe']} | {x['mode']}\n"
        f"🎯 SIGNAL: {x['action']}\n\n"
        f"🟢 BUY {x['buy_votes']}/50\n"
        f"🔴 SELL {x['sell_votes']}/50\n"
        f"⚪ NEUTRAL {x['neutral_votes']}/50\n"
        f"📈 Consensus {x['consensus_percent']}%\n"
        f"📰 News {x['news']['sentiment']}\n"
        f"💰 Entry Price: {x['entry_price']}\n"
        f"🕐 Entry UTC+6: {x['entry_time_utc6']}\n"
        f"⏳ Expiry UTC+6: {x['expiry_time_utc6']}\n"
        f"📊 Meta Agreement: {x['meta_agreement']}/5\n"
        f"⚠️ Meta filters required: 3/5\n\n"
        "⚠️ Consensus is strategy agreement, not guaranteed accuracy or profit."
    )

async def telegram_polling():
    if not TELEGRAM_TOKEN:
        return

    offset = 0
    async with httpx.AsyncClient(timeout=40) as client:
        while True:
            try:
                r = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                    params={"timeout": 30, "offset": offset},
                )
                data = r.json()

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    chat_id = message.get("chat", {}).get("id")
                    text = (message.get("text") or "").strip()
                    if not chat_id:
                        continue

                    if text.startswith("/start"):
                        await telegram_send(
                            chat_id,
                            "🤖 SK BOT ONLINE\n\n"
                            "50 strategies enabled.\n"
                            "3/5 meta agreement required for SIGNAL.\n\n"
                            "/signal EURUSD 1min\n"
                            "/signal EURUSD 5min\n"
                            "/signal EURUSD 15min\n"
                            "/signal EURUSD 30min\n"
                            "/signal EURUSD 60min\n"
                            "/markets\n"
                            "/chatid"
                        )
                    elif text.startswith("/markets"):
                        await telegram_send(
                            chat_id,
                            "📊 " + ", ".join(MARKETS) +
                            "\n\nTimeframes: 1min, 5min, 15min, 30min, 60min"
                        )
                    elif text.startswith("/chatid"):
                        await telegram_send(chat_id, f"🆔 Your Telegram Chat ID: {chat_id}\n\nAdd this as TELEGRAM_CHAT_ID in your environment.")
                    elif text.startswith("/signal"):
                        parts = text.split()
                        symbol = parts[1] if len(parts) > 1 else "EURUSD"
                        timeframe = parts[2] if len(parts) > 2 else "1min"
                        try:
                            x = await make_signal(symbol, timeframe, "REAL")
                            await telegram_send(chat_id, telegram_signal_message(x))
                        except Exception as exc:
                            await telegram_send(chat_id, f"❌ {exc}")
                    else:
                        await telegram_send(chat_id, "Use /signal EURUSD 1min or /markets")

            except Exception as exc:
                logger.error(f"Telegram polling error: {exc}")
                await asyncio.sleep(5)

# ==================== ROUTES ====================
@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML

@app.get("/health")
async def health():
    return {
        "ok": True,
        "bot": "SK BOT PRO",
        "version": "3.0",
        "strategies": STRATEGY_COUNT,
        "meta_filters": len(META_FILTERS),
        "meta_required": 3,
        "status": market_status_utc(),
        "timestamp": datetime.now(UTC_TZ).isoformat()
    }

@app.get("/markets")
async def markets():
    return {
        "count": len(MARKETS),
        "markets": MARKETS,
        "market_type": "REAL_FX",
        "utc_time": datetime.now(UTC_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "status": market_status_utc()
    }

@app.get("/signal")
async def signal_endpoint(symbol: str = "EURUSD", timeframe: str = "1min", mode: str = "REAL"):
    x = await make_signal(symbol, timeframe, mode)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and x["action"] in {"BUY", "SELL"}:
        try:
            await telegram_send(TELEGRAM_CHAT_ID, telegram_signal_message(x))
        except Exception as exc:
            logger.error(f"Telegram send error: {exc}")
    return x

@app.on_event("startup")
async def startup():
    logger.info("SK BOT PRO v3.0 started | 50 strategies | 5 meta-filters | 3/5 meta agreement = SIGNAL | 15 real FX pairs | UTC clock | 1m-1h timeframes")
    if TELEGRAM_TOKEN:
        asyncio.create_task(telegram_polling())

# ==================== RUN (for local testing) ====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
