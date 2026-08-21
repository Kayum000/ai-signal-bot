# SK BOT PRO ULTRA — SINGLE FILE
# 35 strategies + 20/35 consensus + 70% entry filter
# Credit saver + cache + duplicate alert protection + UTC dashboard

import os
import asyncio
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd
import numpy as np
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

# ---------------- CONFIG ----------------
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MIN_ENTRY_CONSENSUS = 70
MIN_ENTRY_VOTES = 20
TOTAL_STRATEGIES = 35

TIMEFRAME = "5min"
CANDLE_LIMIT = 120

# 60 sec cache = fewer API credits than the old 30 sec build.
CACHE_DURATION_SECONDS = 60
ALERT_COOLDOWN_SECONDS = 300

DB_FILE = "sk_bot.db"

MARKET_CATALOG = {
    "EURUSD": {"name": "EUR/USD", "symbol": "EUR/USD", "type": "FX"},
    "GBPUSD": {"name": "GBP/USD", "symbol": "GBP/USD", "type": "FX"},
    "USDJPY": {"name": "USD/JPY", "symbol": "USD/JPY", "type": "FX"},
    "AUDUSD": {"name": "AUD/USD", "symbol": "AUD/USD", "type": "FX"},
    "BTCUSD": {"name": "BTC/USD", "symbol": "BTC/USD", "type": "CRYPTO"},
    "ETHUSD": {"name": "ETH/USD", "symbol": "ETH/USD", "type": "CRYPTO"},
    "XAUUSD": {"name": "Gold / USD", "symbol": "XAU/USD", "type": "COMMODITY"},
}
MARKETS = list(MARKET_CATALOG)

CANDLE_CACHE = {}
LAST_ALERT = {}
SIGNAL_LOCK = asyncio.Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(levelname)s | %(message)s",
)
logger = logging.getLogger("SK_BOT")

app = FastAPI(title="SK BOT PRO ULTRA", version="Final-Checked")


# ---------------- DB ----------------
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings "
            "(key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market TEXT NOT NULL,
                signal TEXT NOT NULL,
                score INTEGER NOT NULL,
                buy_votes INTEGER NOT NULL,
                sell_votes INTEGER NOT NULL,
                price REAL
            )"""
        )
        conn.commit()


init_db()


# ---------------- HELPERS ----------------
def utc_now():
    return datetime.now(timezone.utc)


def indicator_ok(*values):
    return all(pd.notna(v) and np.isfinite(v) for v in values)


def sma(s, n):
    return s.rolling(n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / (loss + 1e-12)
    return 100 - (100 / (1 + rs))


def macd(s):
    line = ema(s, 12) - ema(s, 26)
    signal = ema(line, 9)
    return line, signal


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - pc).abs(),
            (df["low"] - pc).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n).mean()


def bollinger(s, n=20, k=2):
    mid = sma(s, n)
    sd = s.rolling(n).std()
    return mid + k*sd, mid, mid - k*sd


def stochastic(df, n=14):
    lo = df["low"].rolling(n).min()
    hi = df["high"].rolling(n).max()
    return 100 * (df["close"] - lo) / (hi - lo + 1e-12)


# ---------------- DATA ----------------
async def fetch_real_candles(market: str) -> Optional[pd.DataFrame]:
    now = utc_now()

    cached = CANDLE_CACHE.get(market)
    if cached:
        cached_at, cached_df = cached
        if (now - cached_at).total_seconds() < CACHE_DURATION_SECONDS:
            return cached_df

    if not TWELVE_DATA_API_KEY:
        logger.error("TWELVE_DATA_API_KEY is missing")
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.twelvedata.com/time_series",
                params={
                    "symbol": MARKET_CATALOG[market]["symbol"],
                    "interval": TIMEFRAME,
                    "outputsize": CANDLE_LIMIT,
                    "apikey": TWELVE_DATA_API_KEY,
                },
            )
            r.raise_for_status()
            data = r.json()

        if "values" not in data:
            logger.error("%s | Twelve Data response: %s", market, data)
            return None

        df = pd.DataFrame(data["values"])
        required = ["datetime", "open", "high", "low", "close"]
        if any(c not in df.columns for c in required):
            logger.error("%s | malformed candle response", market)
            return None

        for c in ["open", "high", "low", "close"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df.iloc[::-1].reset_index(drop=True)

        if len(df) < 60:
            logger.error("%s | only %d candles", market, len(df))
            return None

        CANDLE_CACHE[market] = (now, df)
        return df

    except Exception:
        logger.exception("%s | candle fetch failed", market)
        return None


# ---------------- 35 STRATEGIES ----------------
def evaluate_35_strategies(df: pd.DataFrame) -> dict:
    votes = {f"S{i}": 0 for i in range(1, 36)}
    if df is None or len(df) < 60:
        return votes

    c = df["close"]
    h = df["high"]
    l = df["low"]
    o = df["open"]

    s10, s20, s50 = sma(c,10), sma(c,20), sma(c,50)
    e9, e21, e30, e50 = ema(c,9), ema(c,21), ema(c,30), ema(c,50)
    rr = rsi(c,14)
    ml, ms = macd(c)
    aa = atr(df,14)
    bu, bm, bl = bollinger(c)
    st = stochastic(df)

    x = {
        "c": c.iloc[-1], "r": rr.iloc[-1],
        "s10": s10.iloc[-1], "s20": s20.iloc[-1], "s50": s50.iloc[-1],
        "e9": e9.iloc[-1], "e21": e21.iloc[-1], "e30": e30.iloc[-1], "e50": e50.iloc[-1],
        "ml": ml.iloc[-1], "ms": ms.iloc[-1], "aa": aa.iloc[-1],
        "bu": bu.iloc[-1], "bm": bm.iloc[-1], "bl": bl.iloc[-1],
        "st": st.iloc[-1],
    }
    if not indicator_ok(*x.values()):
        return votes

    # 1-8 trend / moving-average rules
    votes["S1"] = 1 if x["c"] > x["s10"] else -1
    votes["S2"] = 1 if x["c"] > x["s20"] else -1
    votes["S3"] = 1 if x["c"] > x["s50"] else -1
    votes["S4"] = 1 if x["c"] > x["e9"] else -1
    votes["S5"] = 1 if x["c"] > x["e21"] else -1
    votes["S6"] = 1 if x["c"] > x["e50"] else -1
    votes["S7"] = 1 if x["e9"] > x["e21"] else -1
    votes["S8"] = 1 if x["e21"] > x["e50"] else -1

    # 9-17 oscillators / momentum
    votes["S9"] = 1 if x["r"] < 35 else -1 if x["r"] > 65 else 0
    votes["S10"] = 1 if x["r"] > 50 else -1
    votes["S11"] = 1 if x["ml"] > x["ms"] else -1
    votes["S12"] = 1 if x["ml"] > 0 else -1
    votes["S13"] = 1 if x["c"] > x["bm"] else -1
    votes["S14"] = -1 if x["c"] > x["bu"] else 1 if x["c"] < x["bl"] else 0
    votes["S15"] = 1 if x["st"] < 20 else -1 if x["st"] > 80 else 0

    roc10 = (c.iloc[-1] / c.iloc[-11] - 1) * 100
    mom10 = c.iloc[-1] - c.iloc[-11]
    votes["S16"] = 1 if roc10 > 0 else -1 if roc10 < 0 else 0
    votes["S17"] = 1 if mom10 > 0 else -1 if mom10 < 0 else 0

    # 18-22 price action / breakouts
    votes["S18"] = 1 if c.iloc[-1] > o.iloc[-1] else -1
    votes["S19"] = 1 if c.iloc[-1] > h.iloc[-2] else -1 if c.iloc[-1] < l.iloc[-2] else 0
    h10, l10 = h.iloc[-11:-1].max(), l.iloc[-11:-1].min()
    votes["S20"] = 1 if c.iloc[-1] > h10 else -1 if c.iloc[-1] < l10 else 0
    h20, l20 = h.iloc[-21:-1].max(), l.iloc[-21:-1].min()
    votes["S21"] = 1 if c.iloc[-1] > h20 else -1 if c.iloc[-1] < l20 else 0
    votes["S22"] = (
        1 if c.iloc[-1] > c.iloc[-2] + x["aa"]*.25
        else -1 if c.iloc[-1] < c.iloc[-2] - x["aa"]*.25
        else 0
    )

    # 23-30 confluence / trend
    votes["S23"] = 1 if x["c"] > x["e21"] and x["r"] > 50 else -1 if x["c"] < x["e21"] and x["r"] < 50 else 0
    votes["S24"] = 1 if x["c"] > x["s20"] and x["r"] < 45 else -1 if x["c"] < x["s20"] and x["r"] > 55 else 0
    votes["S25"] = 1 if x["ml"] > x["ms"] and x["r"] > 50 else -1 if x["ml"] < x["ms"] and x["r"] < 50 else 0
    votes["S26"] = 1 if x["e9"] > x["e21"] > x["e50"] else -1 if x["e9"] < x["e21"] < x["e50"] else 0
    votes["S27"] = 1 if x["s10"] > x["s20"] > x["s50"] else -1 if x["s10"] < x["s20"] < x["s50"] else 0
    votes["S28"] = 1 if c.iloc[-1] > c.iloc[-6] else -1 if c.iloc[-1] < c.iloc[-6] else 0
    votes["S29"] = 1 if c.iloc[-1] > c.iloc[-16] else -1 if c.iloc[-1] < c.iloc[-16] else 0
    votes["S30"] = 1 if x["c"] > x["e30"] else -1

    # 31-35 candle / master filters
    rng = h.iloc[-1] - l.iloc[-1]
    body = c.iloc[-1] - o.iloc[-1]
    votes["S31"] = 1 if rng > 0 and body/rng > .55 else -1 if rng > 0 and body/rng < -.55 else 0

    d3 = c.diff().iloc[-3:]
    votes["S32"] = 1 if (d3 > 0).sum() >= 2 else -1 if (d3 < 0).sum() >= 2 else 0
    votes["S33"] = 1 if c.iloc[-1] > c.iloc[-6] else -1 if c.iloc[-1] < c.iloc[-6] else 0

    rh, rl = h.iloc[-20:].max(), l.iloc[-20:].min()
    loc = (c.iloc[-1] - rl) / (rh - rl + 1e-12)
    votes["S34"] = 1 if loc > .65 else -1 if loc < .35 else 0

    bull = sum([
        x["c"] > x["e21"],
        x["e9"] > x["e21"],
        x["ml"] > x["ms"],
        x["r"] > 50,
        x["c"] > x["s50"],
    ])
    bear = 5 - bull
    votes["S35"] = 1 if bull >= 4 else -1 if bear >= 4 else 0

    return votes


# ---------------- CONSENSUS ----------------
def calculate_consensus(votes):
    buy = sum(v == 1 for v in votes.values())
    sell = sum(v == -1 for v in votes.values())
    neutral = TOTAL_STRATEGIES - buy - sell
    strongest = max(buy, sell)
    score = round(strongest / TOTAL_STRATEGIES * 100)

    # Hard rule: at least 20/35 AND at least 70%.
    if buy >= MIN_ENTRY_VOTES and buy >= sell:
        signal = "BUY"
    elif sell >= MIN_ENTRY_VOTES and sell > buy:
        signal = "SELL"
    else:
        signal = "NEUTRAL"

    entry_allowed = signal != "NEUTRAL" and score >= MIN_ENTRY_CONSENSUS
    return {
        "signal": signal,
        "score": score,
        "buy_votes": buy,
        "sell_votes": sell,
        "neutral_votes": neutral,
        "entry_allowed": entry_allowed,
        "entry_status": "ENTRY_ALLOWED" if entry_allowed else "AVOID",
    }


# ---------------- ALERT ----------------
async def send_telegram_alert(market, signal, score, price):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials missing; alert skipped")
        return

    now = utc_now()
    async with SIGNAL_LOCK:
        old = LAST_ALERT.get(market)
        if old:
            old_time, old_signal, old_score = old
            age = (now - old_time).total_seconds()
            if age < ALERT_COOLDOWN_SECONDS and old_signal == signal:
                logger.info("%s | duplicate alert blocked", market)
                return

    message = (
        "🚀 *SK BOT PRO ULTRA*\\n\\n"
        f"*Market:* {market}\\n"
        f"*Signal:* {signal}\\n"
        f"*Strength:* {score}%\\n"
        f"*Price:* {price}\\n"
        f"*UTC:* {now.strftime('%Y-%m-%d %H:%M:%S')}\\n"
        "✅ *ENTRY FILTER: PASSED*"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            )
        if r.is_success:
            async with SIGNAL_LOCK:
                LAST_ALERT[market] = (now, signal, score)
        else:
            logger.error("Telegram %s: %s", r.status_code, r.text)
    except Exception:
        logger.exception("Telegram request failed")


# ---------------- ANALYSIS ----------------
def save_signal(market, result, price):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """INSERT INTO signal_history
            (timestamp, market, signal, score, buy_votes, sell_votes, price)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                utc_now().isoformat(),
                market,
                result["signal"],
                result["score"],
                result["buy_votes"],
                result["sell_votes"],
                price,
            ),
        )
        conn.commit()


async def analyze_market(market):
    df = await fetch_real_candles(market)
    base = {
        "market": market,
        "name": MARKET_CATALOG[market]["name"],
        "type": MARKET_CATALOG[market]["type"],
    }

    if df is None:
        return {**base, "status": "DATA_ERROR", "signal": "N/A",
                "score": 0, "buy_votes": 0, "sell_votes": 0,
                "neutral_votes": 0, "entry_allowed": False,
                "entry_status": "AVOID", "price": None}

    votes = evaluate_35_strategies(df)
    result = calculate_consensus(votes)
    price = float(df["close"].iloc[-1])

    output = {
        **base,
        **result,
        "status": "LIVE",
        "price": round(price, 8),
    }
    save_signal(market, result, price)

    if result["entry_allowed"]:
        asyncio.create_task(
            send_telegram_alert(market, result["signal"], result["score"], round(price, 8))
        )
    return output


# ---------------- API ----------------
@app.get("/api/status")
async def get_status():
    started = utc_now()
    results = await asyncio.gather(
        *(analyze_market(m) for m in MARKETS),
        return_exceptions=True,
    )
    data = []
    for market, item in zip(MARKETS, results):
        if isinstance(item, Exception):
            logger.exception("%s | analysis failed: %s", market, item)
            data.append({
                "market": market,
                "name": MARKET_CATALOG[market]["name"],
                "type": MARKET_CATALOG[market]["type"],
                "status": "ERROR",
                "signal": "N/A",
                "score": 0,
                "entry_allowed": False,
                "entry_status": "AVOID",
                "price": None,
            })
        else:
            data.append(item)

    finished = utc_now()
    return JSONResponse({
        "timestamp_utc": finished.isoformat(),
        "timeframe": TIMEFRAME,
        "strategies": TOTAL_STRATEGIES,
        "consensus_required": f"{MIN_ENTRY_VOTES}/{TOTAL_STRATEGIES}",
        "minimum_entry_score": MIN_ENTRY_CONSENSUS,
        "cache_seconds": CACHE_DURATION_SECONDS,
        "processing_ms": round((finished - started).total_seconds()*1000),
        "data": data,
    })


@app.get("/api/health")
async def health():
    return {
        "status": "ONLINE",
        "timestamp_utc": utc_now().isoformat(),
        "twelve_data_configured": bool(TWELVE_DATA_API_KEY),
        "telegram_configured": bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID),
        "markets": len(MARKETS),
        "strategies": TOTAL_STRATEGIES,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """<!doctype html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SK BOT PRO ULTRA</title>
<style>
body{margin:0;padding:18px;background:#070b12;color:#fff;font-family:Arial}
h1{margin:0 0 5px}.sub,#status{color:#94a3b8;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.card{background:#111827;border:1px solid #263244;border-radius:14px;padding:16px}
.market{font-size:20px;font-weight:700}.type{color:#94a3b8;font-size:12px}
.signal{font-size:26px;font-weight:700;margin:14px 0}
.buy,.allowed{color:#22c55e}.sell{color:#ef4444}.neutral,.avoid{color:#f59e0b}
.row{display:flex;justify-content:space-between;margin:7px 0}.price{font-size:18px}
</style></head><body>
<h1>SK BOT PRO ULTRA</h1>
<div class="sub">35 Strategies • 20/35 Consensus • 70% Entry Filter</div>
<div id="status">Loading...</div><div id="grid" class="grid"></div>
<script>
const cls=s=>s==="BUY"?"buy":s==="SELL"?"sell":"neutral";
async function update(){
 try{
  const r=await fetch("/api/status"); const d=await r.json();
  document.getElementById("status").textContent =
    "LIVE • UTC: "+new Date(d.timestamp_utc).toISOString()+
    " • "+d.processing_ms+"ms";
  document.getElementById("grid").innerHTML=d.data.map(x=>`
   <div class="card">
    <div class="market">${x.name}</div><div class="type">${x.type} • ${x.status}</div>
    <div class="signal ${cls(x.signal)}">${x.signal}</div>
    <div class="row"><span>Consensus</span><b>${x.score}%</b></div>
    <div class="row"><span>BUY votes</span><b>${x.buy_votes??0}</b></div>
    <div class="row"><span>SELL votes</span><b>${x.sell_votes??0}</b></div>
    <div class="row"><span>Neutral</span><b>${x.neutral_votes??0}</b></div>
    <div class="row"><span>Entry</span><b class="${x.entry_allowed?'allowed':'avoid'}">${x.entry_status}</b></div>
    <hr><div class="price">Price: <b>${x.price??"N/A"}</b></div>
   </div>`).join("");
 }catch(e){document.getElementById("status").textContent="Connection error: "+e.message}
}
update(); setInterval(update,30000);
</script></body></html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
