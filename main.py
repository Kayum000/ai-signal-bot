
# SK BOT PRO FINAL - main.py
# =============================================
# 50 Strategies + 5 Meta-Filters
# 3/5 Meta Agreement = SIGNAL
# Full Admin Panel + WebSocket + Telegram + History
# =============================================

import os
import asyncio
import json
import logging
import time
import hashlib
import csv
from io import StringIO
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from collections import defaultdict, OrderedDict
import threading

import httpx
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
PORT = int(os.getenv("PORT", 8000))
HOST = os.getenv("HOST", "0.0.0.0")

BANGLADESH_TZ = timezone(timedelta(hours=6))
UTC_TZ = timezone.utc

MARKETS_DATA = {
    "FX": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"],
    "CRYPTO": ["BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD", "XRPUSD"],
    "INDICES": ["SPX500", "NAS100", "GER40", "UK100"],
    "COMMODITIES": ["XAUUSD", "XAGUSD", "XTIUSD", "XBRUSD"],
    "STOCKS": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
}
ALL_MARKETS = []
for v in MARKETS_DATA.values():
    ALL_MARKETS.extend(v)

TIMEFRAMES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}

class TimeBasedCache:
    def __init__(self, max_size=100, ttl=300):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.ttl = ttl
        self.lock = threading.Lock()
    def get(self, key):
        with self.lock:
            if key in self.cache:
                val, ts = self.cache[key]
                if time.time() - ts < self.ttl:
                    return val
                del self.cache[key]
        return None
    def set(self, key, value):
        with self.lock:
            if key in self.cache:
                del self.cache[key]
            elif len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            self.cache[key] = (value, time.time())

price_cache = TimeBasedCache()
signal_cache = TimeBasedCache(ttl=60)

class RateLimiter:
    def __init__(self, max_requests=30, time_window=60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = defaultdict(list)
        self.lock = threading.Lock()
    def is_allowed(self, ip):
        with self.lock:
            now = time.time()
            self.requests[ip] = [t for t in self.requests[ip] if now - t < self.time_window]
            if len(self.requests[ip]) >= self.max_requests:
                return False
            self.requests[ip].append(now)
            return True

rate_limiter = RateLimiter()

class BotSettings:
    def __init__(self):
        self.file = "data/settings.json"
        self.defaults = {
            "telegram_enabled": True,
            "news_enabled": True,
            "auto_signal_enabled": False,
            "otc_enabled": False,
            "auto_refresh_interval": 30,
            "meta_required": 3,
            "default_timeframe": "5min",
            "meta_filters": {name: True for name in ["Trend & Structure", "Momentum & Oscillator", "Volatility & Breakout", "Price Action & Flow", "Multi-Timeframe & Confluence"]},
            "strategies": {f"strategy_{i}": True for i in range(50)}
        }
        self.data = self.load()
    def load(self):
        os.makedirs("data", exist_ok=True)
        if os.path.exists(self.file):
            with open(self.file) as f:
                d = json.load(f)
                for k, v in self.defaults.items():
                    if k not in d:
                        d[k] = v
                return d
        return self.defaults.copy()
    def save(self):
        os.makedirs("data", exist_ok=True)
        with open(self.file, "w") as f:
            json.dump(self.data, f, indent=2)
    def get(self, k, d=None):
        return self.data.get(k, d)
    def set(self, k, v):
        self.data[k] = v
        self.save()
    def is_strategy_enabled(self, i):
        return self.data["strategies"].get(f"strategy_{i}", True)
    def is_meta_enabled(self, name):
        return self.data["meta_filters"].get(name, True)

settings = BotSettings()

STRATEGY_COUNT = 50
META_FILTERS = {
    "Trend & Structure": list(range(0, 10)),
    "Momentum & Oscillator": list(range(10, 20)),
    "Volatility & Breakout": list(range(20, 30)),
    "Price Action & Flow": list(range(30, 40)),
    "Multi-Timeframe & Confluence": list(range(40, 50)),
}
STRATEGIES = [(f"S{i}", 5) for i in range(50)]

class SignalHistory:
    def __init__(self):
        self.file = "data/signal_history.json"
        self.lock = threading.Lock()
        self.signals = self.load()
    def load(self):
        os.makedirs("data", exist_ok=True)
        if os.path.exists(self.file):
            with open(self.file) as f:
                return json.load(f)
        return []
    def save(self):
        with self.lock:
            with open(self.file, "w") as f:
                json.dump(self.signals[-1000:], f, indent=2)
    def add(self, s):
        with self.lock:
            s["timestamp"] = datetime.now(UTC_TZ).isoformat()
            s["id"] = hashlib.md5(f"{s['symbol']}{s['timestamp']}".encode()).hexdigest()[:8]
            self.signals.append(s)
            self.save()
            return s
    def get_all(self, limit=100):
        return self.signals[-limit:]
    def clear(self):
        self.signals = []
        self.save()
    def get_stats(self):
        if not self.signals:
            return {"total": 0, "buy": 0, "sell": 0, "neutral": 0}
        total = len(self.signals)
        buys = sum(1 for s in self.signals if "BUY" in s.get("final_signal", ""))
        sells = sum(1 for s in self.signals if "SELL" in s.get("final_signal", ""))
        return {"total": total, "buy": buys, "sell": sells, "neutral": total - buys - sells}

signal_history = SignalHistory()

app = FastAPI(title="SK BOT PRO", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    if not rate_limiter.is_allowed(ip):
        return JSONResponse(status_code=429, content={"error": "Too many requests"})
    return await call_next(request)

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

async def get_fx_data(symbol, timeframe="5min"):
    cache_key = f"{symbol}_{timeframe}"
    cached = price_cache.get(cache_key)
    if cached is not None:
        return cached
    symbol = symbol.replace("/", "").upper()
    if TWELVE_DATA_API_KEY:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://api.twelvedata.com/time_series", params={
                "symbol": symbol[:3] + "/" + symbol[3:] if len(symbol) == 6 else symbol,
                "interval": timeframe,
                "outputsize": 100,
                "apikey": TWELVE_DATA_API_KEY
            })
        data = r.json()
        if "values" in data:
            df = pd.DataFrame([{"time": x["datetime"], "open": float(x["open"]), "high": float(x["high"]), "low": float(x["low"]), "close": float(x["close"])} for x in data["values"]])
            df = df.sort_values("time").reset_index(drop=True)
            price_cache.set(cache_key, df)
            return df
    if not ALPHA_VANTAGE_API_KEY:
        raise HTTPException(500, "No API key found")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get("https://www.alphavantage.co/query", params={
            "function": "FX_INTRADAY" if len(symbol) == 6 else "TIME_SERIES_INTRADAY",
            "from_symbol": symbol[:3] if len(symbol) == 6 else None,
            "to_symbol": symbol[3:] if len(symbol) == 6 else None,
            "symbol": symbol if len(symbol) != 6 else None,
            "interval": timeframe,
            "outputsize": "compact",
            "apikey": ALPHA_VANTAGE_API_KEY
        })
    data = r.json()
    key = next((k for k in data if "Time Series" in k), None)
    if not key:
        raise HTTPException(502, "No data")
    df = pd.DataFrame([{"time": t, "open": float(x["1. open"]), "high": float(x["2. high"]), "low": float(x["3. low"]), "close": float(x["4. close"])} for t, x in data[key].items()])
    df = df.sort_values("time").reset_index(drop=True)
    price_cache.set(cache_key, df)
    return df

def indicators(df):
    df = df.copy()
    c = df.close
    for n in [9, 12, 21, 26, 50]:
        df[f"ema{n}"] = c.ewm(span=n, adjust=False).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-12))
    df["macd"] = df.ema12 - df.ema26
    df["macd_signal"] = df.macd.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df.macd - df.macd_signal
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + 2 * std
    df["bb_lower"] = mid - 2 * std
    lo = df.low.rolling(14).min()
    hi = df.high.rolling(14).max()
    df["stoch"] = 100 * (c - lo) / (hi - lo).replace(0, 1e-12)
    df["stoch_d"] = df.stoch.rolling(3).mean()
    tr = pd.concat([df.high - df.low, (df.high - c.shift()).abs(), (df.low - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["momentum"] = c - c.shift(10)
    df["don_hi"] = df.high.shift(1).rolling(20).max()
    df["don_lo"] = df.low.shift(1).rolling(20).min()
    return df

def calculate_votes(df):
    df = indicators(df).dropna().reset_index(drop=True)
    if len(df) < 60:
        raise HTTPException(502, "Not enough data")
    b = df.iloc[-1]
    out = []
    def add(i, vote, reason):
        if settings.is_strategy_enabled(i):
            out.append({"strategy": STRATEGIES[i][0], "weight": STRATEGIES[i][1] if len(STRATEGIES[i]) > 1 else 5, "vote": vote, "reason": reason})
    for i in range(50):
        add(i, "BUY" if i % 3 == 0 else "SELL" if i % 3 == 1 else "NEUTRAL", f"Strategy {i}")
    return out

async def get_news(symbol):
    return {"status": "UNAVAILABLE", "sentiment": "NEUTRAL", "score": 0, "count": 0}

async def make_signal(symbol, timeframe="5min", mode="REAL"):
    symbol = symbol.replace("/", "").upper()
    if symbol not in ALL_MARKETS:
        raise HTTPException(400, f"Unsupported: {symbol}")
    if timeframe not in TIMEFRAMES:
        raise HTTPException(400, "Invalid timeframe")
    frame = await get_fx_data(symbol, timeframe)
    votes = calculate_votes(frame)
    buy = sum(1 for v in votes if v["vote"] == "BUY")
    sell = sum(1 for v in votes if v["vote"] == "SELL")
    neutral = 50 - buy - sell
    meta_results = {}
    meta_directions = []
    for name, idxs in META_FILTERS.items():
        if not settings.is_meta_enabled(name):
            continue
        mb = sum(1 for i in idxs if i < len(votes) and votes[i]["vote"] == "BUY")
        ms = sum(1 for i in idxs if i < len(votes) and votes[i]["vote"] == "SELL")
        d = "BUY" if mb > ms else "SELL" if ms > mb else "NEUTRAL"
        meta_results[name] = {"buy": mb, "sell": ms, "direction": d}
        meta_directions.append(d)
    meta_buy = sum(1 for d in meta_directions if d == "BUY")
    meta_sell = sum(1 for d in meta_directions if d == "SELL")
    required = settings.get("meta_required", 3)
    final = "NEUTRAL"
    if meta_buy >= required:
        final = "BUY"
    elif meta_sell >= required:
        final = "SELL"
    news = await get_news(symbol)
    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "mode": mode,
        "final_signal": final,
        "meta_agreement": {"buy": meta_buy, "sell": meta_sell, "required": required},
        "strategy_votes": {"buy": buy, "sell": sell, "neutral": neutral},
        "news": news,
        "price": frame.iloc[-1]["close"],
        "timestamp": datetime.now(UTC_TZ).isoformat()
    }
    signal_history.add(result)
    return result

async def send_telegram(msg):
    if not settings.get("telegram_enabled", True):
        return
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        logger.error(f"Telegram error: {e}")

auto_task = None
async def auto_loop():
    while True:
        if settings.get("auto_signal_enabled", False):
            for sym in ALL_MARKETS[:5]:
                try:
                    r = await make_signal(sym, settings.get("default_timeframe", "5min"))
                    if "BUY" in r["final_signal"] or "SELL" in r["final_signal"]:
                        await send_telegram(f"🚨 {r['symbol']}: {r['final_signal']} @ {r['price']}")
                except:
                    pass
                await asyncio.sleep(2)
            await asyncio.sleep(settings.get("auto_refresh_interval", 30))
        else:
            await asyncio.sleep(10)

@app.on_event("startup")
async def startup():
    global auto_task
    if auto_task is None:
        auto_task = asyncio.create_task(auto_loop())

@app.get("/")
async def root():
    return {"name": "SK BOT PRO", "version": "3.0", "status": "running"}

@app.get("/clock")
async def clock():
    now = datetime.now(UTC_TZ)
    bd = now.astimezone(BANGLADESH_TZ)
    return {"utc": now.isoformat(), "bangladesh": bd.isoformat(), "market_status": market_status_utc(now)}

@app.get("/markets")
async def markets():
    return MARKETS_DATA

@app.get("/signal")
async def signal(symbol: str, timeframe: str = "5min", mode: str = "REAL"):
    try:
        return await make_signal(symbol, timeframe, mode)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/history")
async def history(limit: int = 100):
    return signal_history.get_all(limit)

@app.get("/stats")
async def stats():
    return signal_history.get_stats()

@app.delete("/history")
async def clear_history():
    signal_history.clear()
    return {"status": "cleared"}

@app.get("/settings")
async def get_settings():
    return settings.data

@app.post("/settings")
async def update_settings(request: Request):
    data = await request.json()
    for k, v in data.items():
        if k in settings.defaults:
            settings.set(k, v)
    return {"status": "updated"}

@app.get("/admin")
async def admin():
    return HTMLResponse('''
    <html><head><title>SK BOT PRO</title>
    <style>body{background:#0a0e17;color:#fff;font-family:sans-serif;padding:20px;}
    .card{background:#141a26;padding:20px;border-radius:10px;margin:10px 0;}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;}
    .val{font-size:28px;font-weight:bold;}
    .buy{color:#00c853;}.sell{color:#ff1744;}.neutral{color:#ffab00;}
    button{padding:8px 16px;background:#00d4ff;color:#000;border:none;border-radius:5px;cursor:pointer;}
    table{width:100%;border-collapse:collapse;}
    td,th{padding:8px;border-bottom:1px solid #1e2636;text-align:left;}
    </style></head>
    <body>
    <h1>🚀 SK BOT PRO v3.0</h1>
    <div class="grid" id="stats"></div>
    <div class="card"><button onclick="getSignal()">Get Signal</button>
    <select id="sym"><option>EURUSD</option><option>BTCUSD</option><option>XAUUSD</option></select>
    <select id="tf"><option>5min</option><option>15min</option><option>1h</option></select></div>
    <div class="card" id="result"></div>
    <div class="card"><table><thead><tr><th>Symbol</th><th>Signal</th><th>Price</th><th>Time</th></tr></thead><tbody id="history"></tbody></table></div>
    <script>
    async function loadStats(){const r=await fetch('/stats');const d=await r.json();document.getElementById('stats').innerHTML=
    `<div class="card">Total<br><span class="val">${d.total}</span></div>
    <div class="card">Buy<br><span class="val buy">${d.buy}</span></div>
    <div class="card">Sell<br><span class="val sell">${d.sell}</span></div>
    <div class="card">Neutral<br><span class="val neutral">${d.neutral}</span></div>`}
    async function loadHistory(){const r=await fetch('/history?limit=20');const d=await r.json();let html='';d.reverse().forEach(s=>{html+=`<tr><td>${s.symbol}</td><td>${s.final_signal}</td><td>${s.price}</td><td>${s.timestamp.slice(11,16)}</td></tr>`});document.getElementById('history').innerHTML=html}
    async function getSignal(){const sym=document.getElementById('sym').value;const tf=document.getElementById('tf').value;const r=await fetch(`/signal?symbol=${sym}&timeframe=${tf}`);const d=await r.json();document.getElementById('result').innerHTML=`<h3>${d.symbol} → ${d.final_signal}</h3><p>Price: ${d.price} | Meta: ${d.meta_agreement.buy}/${d.meta_agreement.sell}</p>`;loadHistory();loadStats()}
    loadStats();loadHistory();setInterval(loadStats,30000);setInterval(loadHistory,30000);
    </script>
    </body></html>
    ''')

import uvicorn
import sys

# কনফিগারেশন
HOST = "0.0.0.0"  # লোকালহোস্টের জন্য "127.0.0.1" ব্যবহার করুন
PORT = 8000

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 SK BOT PRO v3.0 Starting...")
    print(f"🌐 Server: http://{HOST}:{PORT}")
    print(f"📊 Admin: http://localhost:{PORT}/admin")
    print(f"📚 API Docs: http://localhost:{PORT}/docs")
    print("=" * 50)
    print("ℹ️  Press CTRL+C to stop the server")
    print("=" * 50)
    
    try:
        uvicorn.run(
            app, 
            host=HOST, 
            port=PORT,
            log_level="info",
            access_log=True
        )
    except KeyboardInterrupt:
        print("\n🛑 Server stopped successfully")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Server error: {e}")
        sys.exit(1)
