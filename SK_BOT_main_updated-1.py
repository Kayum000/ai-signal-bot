# SK BOT main.py
# 25 strategies + 14/25 consensus + 8 default FX markets + Telegram + mobile UI

import os
import asyncio
from datetime import datetime, timezone, timedelta

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

load_dotenv()

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")

MARKETS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
           "USDCAD", "USDCHF", "NZDUSD", "EURJPY"]

TIMEFRAMES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}
BANGLADESH_TZ = timezone(timedelta(hours=6))

STRATEGY_COUNT = 25
CONSENSUS_REQUIRED = 14

# Importance weights; these are NOT guaranteed win/accuracy percentages.
STRATEGIES = [
    ("RSI", 6), ("MACD", 6), ("EMA 9/21", 6), ("EMA 21/50", 6),
    ("ADX Trend", 6), ("SMA 20/50", 4), ("Bollinger Bands", 4),
    ("Stochastic", 4), ("Stochastic RSI", 4), ("CCI", 4),
    ("Williams %R", 4), ("ROC", 4), ("Momentum", 4), ("ATR Regime", 4),
    ("Price vs EMA", 4), ("MACD Histogram", 4), ("Donchian Breakout", 4),
    ("Parabolic SAR proxy", 4), ("Ichimoku proxy", 4), ("Volume proxy", 3),
    ("Candle Momentum", 3), ("5-bar Trend", 3), ("10-bar Trend", 3),
    ("Support/Resistance", 3), ("Trend Alignment", 3),
]

app = FastAPI(title="SK BOT", version="2.0")


async def get_fx_data(symbol: str, timeframe: str = "1min") -> pd.DataFrame:
    symbol = symbol.replace("/", "").upper()
    if len(symbol) != 6:
        raise HTTPException(400, "Use a market such as EURUSD.")
    if timeframe not in TIMEFRAMES:
        raise HTTPException(400, "Unsupported timeframe.")
    interval = timeframe
    if TWELVE_DATA_API_KEY:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get("https://api.twelvedata.com/time_series", params={
                "symbol": f"{symbol[:3]}/{symbol[3:]}", "interval": interval,
                "outputsize": 250, "apikey": TWELVE_DATA_API_KEY})
        data = response.json()
        if "values" in data:
            return pd.DataFrame([{"time":x["datetime"],"open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),"close":float(x["close"])} for x in data["values"]]).sort_values("time").reset_index(drop=True)
    if not ALPHA_VANTAGE_API_KEY:
        raise HTTPException(500, "Add TWELVE_DATA_API_KEY or ALPHA_VANTAGE_API_KEY first.")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get("https://www.alphavantage.co/query", params={
            "function":"FX_INTRADAY","from_symbol":symbol[:3],"to_symbol":symbol[3:],
            "interval":interval,"outputsize":"compact","apikey":ALPHA_VANTAGE_API_KEY})
    data=response.json()
    if "Note" in data: raise HTTPException(429,data["Note"])
    if "Information" in data: raise HTTPException(429,data["Information"])
    key=next((k for k in data if "Time Series FX" in k),None)
    if not key: raise HTTPException(502,"No intraday FX data returned.")
    return pd.DataFrame([{"time":x,"open":float(v["1. open"]),"high":float(v["2. high"]),"low":float(v["3. low"]),"close":float(v["4. close"])} for x,v in data[key].items()]).sort_values("time").reset_index(drop=True)


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

    lo, hi = l.rolling(14).min(), h.rolling(14).max()
    df["stoch"] = 100 * (c - lo) / (hi - lo).replace(0, 1e-12)
    df["stoch_d"] = df.stoch.rolling(3).mean()

    sr_lo, sr_hi = df.stoch.rolling(14).min(), df.stoch.rolling(14).max()
    df["stoch_rsi"] = 100 * (df.stoch - sr_lo) / (sr_hi - sr_lo).replace(0, 1e-12)

    tp = (h + l + c) / 3
    df["cci"] = (tp - tp.rolling(20).mean()) / (
        0.015 * tp.rolling(20).std()
    ).replace(0, 1e-12)

    df["williams"] = -100 * (hi - c) / (hi - lo).replace(0, 1e-12)
    df["roc"] = c.pct_change(12) * 100
    df["momentum"] = c - c.shift(10)

    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
        axis=1,
    ).max(axis=1)
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
        out.append({
            "strategy": STRATEGIES[i][0],
            "weight": STRATEGIES[i][1],
            "vote": vote,
            "reason": reason,
        })

    add(0, "BUY" if b.rsi < 30 else "SELL" if b.rsi > 70 else "NEUTRAL", f"RSI {b.rsi:.1f}")
    add(1, "BUY" if b.macd > b.macd_signal else "SELL", "MACD")
    add(2, "BUY" if b.ema9 > b.ema21 else "SELL", "EMA 9/21")
    add(3, "BUY" if b.ema21 > b.ema50 else "SELL", "EMA 21/50")
    add(4, "BUY" if b.adx >= 20 and b.pdi > b.mdi else "SELL" if b.adx >= 20 else "NEUTRAL", f"ADX {b.adx:.1f}")
    add(5, "BUY" if b.sma20 > b.sma50 else "SELL", "SMA")
    add(6, "BUY" if b.close <= b.bb_lower else "SELL" if b.close >= b.bb_upper else "BUY" if b.close > b.bb_mid else "SELL", "Bollinger")
    add(7, "BUY" if b.stoch < 20 else "SELL" if b.stoch > 80 else "BUY" if b.stoch > b.stoch_d else "SELL", "Stochastic")
    add(8, "BUY" if b.stoch_rsi < 20 else "SELL" if b.stoch_rsi > 80 else "BUY" if b.stoch_rsi > 50 else "SELL", "Stoch RSI")
    add(9, "BUY" if b.cci > 0 else "SELL", "CCI")
    add(10, "BUY" if b.williams > -50 else "SELL", "Williams %R")
    add(11, "BUY" if b.roc > 0 else "SELL", "ROC")
    add(12, "BUY" if b.momentum > 0 else "SELL", "Momentum")

    atr_avg = df.atr.rolling(30).mean().iloc[-1]
    add(13, "NEUTRAL" if b.atr > atr_avg * 1.8 else "BUY" if b.close > b.ema21 else "SELL", "ATR regime")
    add(14, "BUY" if b.close > b.ema21 else "SELL", "Price vs EMA")
    add(15, "BUY" if b.macd_hist > 0 else "SELL", "Histogram")
    add(16, "BUY" if b.close > b.don_hi else "SELL" if b.close < b.don_lo else "BUY" if b.close > df.close.iloc[-21:-1].mean() else "SELL", "Donchian")
    add(17, "BUY" if b.close > b.sar_proxy else "SELL", "SAR proxy")
    add(18, "BUY" if b.tenkan > b.kijun else "SELL", "Ichimoku proxy")
    add(19, "BUY" if b.close > p.close and b.flow_proxy > df.flow_proxy.rolling(10).mean().iloc[-1] else "SELL", "Flow proxy")
    add(20, "BUY" if b.close > b.open else "SELL", "Candle")
    add(21, "BUY" if b.close > df.close.iloc[-6] else "SELL", "5-bar")
    add(22, "BUY" if b.close > df.close.iloc[-11] else "SELL", "10-bar")

    support = df.low.iloc[-21:-1].min()
    resistance = df.high.iloc[-21:-1].max()
    add(23, "BUY" if b.close <= support * 1.001 else "SELL" if b.close >= resistance * 0.999 else "BUY" if b.close > (support + resistance) / 2 else "SELL", "S/R")
    add(24, "BUY" if b.ema9 > b.ema21 > b.ema50 and b.close > b.ema9 else "SELL" if b.ema9 < b.ema21 < b.ema50 and b.close < b.ema9 else "NEUTRAL", "Alignment")

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
    symbol=symbol.replace("/","").upper(); mode=mode.upper()
    if mode=="OTC":
        raise HTTPException(503,"OTC DATA UNAVAILABLE: Quotex OTC price feed is not connected. Real FX signals remain available.")
    if symbol not in MARKETS: raise HTTPException(400,f"Unsupported market: {', '.join(MARKETS)}")
    if timeframe not in TIMEFRAMES: raise HTTPException(400,"Choose 1m, 5m, 15m, 30m or 1h.")
    frame=await get_fx_data(symbol,timeframe); votes=calculate_votes(frame)
    buy=sum(x["vote"]=="BUY" for x in votes); sell=sum(x["vote"]=="SELL" for x in votes); neutral=STRATEGY_COUNT-buy-sell
    buy_weight=sum(x["weight"] for x in votes if x["vote"]=="BUY"); sell_weight=sum(x["weight"] for x in votes if x["vote"]=="SELL")
    news=await get_news(symbol)
    conflict=(buy>=CONSENSUS_REQUIRED and news["sentiment"]=="BEARISH") or (sell>=CONSENSUS_REQUIRED and news["sentiment"]=="BULLISH")
    action="BUY" if buy>=CONSENSUS_REQUIRED and buy>sell and not conflict else "SELL" if sell>=CONSENSUS_REQUIRED and sell>buy and not conflict else "WAIT"
    now=datetime.now(BANGLADESH_TZ); entry=now.replace(second=0,microsecond=0); expiry=entry+timedelta(minutes=TIMEFRAMES[timeframe]); price=round(float(frame.close.iloc[-1]),6)
    return {"symbol":symbol,"mode":mode,"timeframe":timeframe,"action":action,"buy_votes":buy,"sell_votes":sell,"neutral_votes":neutral,"buy_weight":buy_weight,"sell_weight":sell_weight,"weighted_consensus":max(buy_weight,sell_weight),"required_votes":CONSENSUS_REQUIRED,"news":news,"price":price,"entry_price":price,"entry_time_utc6":entry.strftime("%Y-%m-%d %H:%M:%S"),"expiry_time_utc6":expiry.strftime("%Y-%m-%d %H:%M:%S"),"timezone":"UTC+6","updated_at":now.isoformat(),"strategies":votes}


async def telegram_send(chat_id, message):
    if not TELEGRAM_TOKEN:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message},
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
                            "🤖 SK BOT ONLINE\n\n25 strategies enabled.\n"
                            "Signal requires 14/25 consensus.\n\n"
                            "/signal EURUSD\n/markets"
                        )

                    elif text.startswith("/markets"):
                        await telegram_send(chat_id, "📊 " + ", ".join(MARKETS))

                    elif text.startswith("/signal"):
                        parts = text.split()
                        symbol = parts[1] if len(parts) > 1 else "EURUSD"
                        try:
                            x = await make_signal(symbol, "1min", "REAL")
                            await telegram_send(
                                chat_id,
                                f"🤖 SK BOT\n\n"
                                f"📊 {x['symbol']} | {x['timeframe']}\n"
                                f"🎯 {x['action']}\n\n"
                                f"🟢 BUY {x['buy_votes']}/25\n"
                                f"🔴 SELL {x['sell_votes']}/25\n"
                                f"⚪ WAIT {x['neutral_votes']}/25\n"
                                f"⚖️ Weight {x['weighted_consensus']}%\n"
                                f"📰 News {x['news']['sentiment']}\n"
                                f"💰 Entry {x['entry_price']}\n"
                                 f"🕐 Entry UTC+6 {x['entry_time_utc6']}\n"
                                 f"⏳ Expiry UTC+6 {x['expiry_time_utc6']}\n\n"
                                "⚠️ Weight is not a guaranteed win rate."
                            )
                        except Exception as exc:
                            await telegram_send(chat_id, f"❌ {exc}")

                    else:
                        await telegram_send(chat_id, "Use /signal EURUSD or /markets")

            except Exception as exc:
                print("Telegram error:", exc)
                await asyncio.sleep(5)


HTML = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>SK BOT</title><style>body{margin:0;background:#070b16;color:#eef2ff;font-family:Arial}.wrap{max-width:560px;margin:auto;padding:18px}.header{display:flex;align-items:center;gap:14px}.logo{width:60px;height:60px;border-radius:18px;background:linear-gradient(135deg,#5b5cf6,#00cfff);display:flex;align-items:center;justify-content:center;font-weight:900;font-size:20px}h1{margin:0;font-size:30px}.muted,.small{color:#98a4c0;font-size:12px}.card{background:#11192c;border:1px solid #273352;border-radius:20px;padding:16px;margin-top:15px}label{display:block;color:#aab6d2;font-size:13px;margin:8px 0}select,button{width:100%;box-sizing:border-box;padding:14px;border-radius:13px;margin-bottom:10px;font-size:15px}select{background:#080f20;color:#fff;border:1px solid #2b395d}button{background:linear-gradient(90deg,#6657ff,#7a5cff);color:#fff;border:0;font-weight:800}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.stat{background:#080f20;border-radius:14px;padding:12px;text-align:center}.big{font-size:25px;font-weight:900;margin:5px 0}.buy{color:#4ade80}.sell{color:#fb7185}.wait{color:#facc15}.row{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #222c45}.hidden{display:none}.entry{background:#0b1325;border:1px solid #29385c;border-radius:14px;padding:12px;margin-top:12px}.badge{display:inline-block;padding:5px 9px;border-radius:999px;background:#1b2b49;color:#9fd7ff;font-size:11px;font-weight:700}.otc{color:#facc15}</style></head><body><div class="wrap"><div class="header"><div class="logo">SK</div><div><h1>SK BOT</h1><div class="muted">25-Strategy AI Market Scanner</div></div></div><div class="card"><label>Market Mode</label><select id="mode"><option value="REAL">REAL FX</option><option value="OTC">OTC</option></select><label>Market</label><select id="market"></select><label>Timeframe</label><select id="timeframe"><option value="1min">1 MIN</option><option value="5min">5 MIN</option><option value="15min">15 MIN</option><option value="30min">30 MIN</option><option value="60min">1 HOUR</option></select><button onclick="getSignal()">GET SIGNAL</button></div><div class="card hidden" id="result"><div class="muted">CURRENT SIGNAL <span id="modeBadge" class="badge">REAL FX</span></div><div id="action" class="big wait">WAIT</div><div class="grid"><div class="stat"><div class="small">BUY</div><div id="buy" class="big buy">0/25</div></div><div class="stat"><div class="small">SELL</div><div id="sell" class="big sell">0/25</div></div></div><div class="grid" style="margin-top:10px"><div class="stat"><div class="small">NEUTRAL</div><div id="neutral" class="big wait">0/25</div></div><div class="stat"><div class="small">WEIGHT</div><div id="weight" class="big">0%</div></div></div><div class="row"><span>News</span><b id="news">NEUTRAL</b></div><div class="row"><span>Entry Price</span><b id="price">-</b></div><div class="row"><span>Timeframe</span><b id="tf">-</b></div><div class="entry"><div class="small">ENTRY TIME — UTC+6:00</div><b id="entryTime">-</b></div><div class="entry"><div class="small">EXPIRY TIME — UTC+6:00</div><b id="expiryTime">-</b></div><p class="small">Weight is model importance, not guaranteed accuracy or profit.</p></div><div class="card"><div class="small"><b>14 / 25 CONSENSUS</b></div><p class="small">BUY/SELL is issued only when at least 14 strategies agree and news does not conflict.</p><p class="small otc">OTC requires a connected OTC price feed. No fake OTC price/signal is generated.</p></div></div><script>async function loadMarkets(){const r=await fetch("/markets"),d=await r.json();d.markets.forEach(s=>market.add(new Option(s,s)))}async function getSignal(){const r=await fetch("/signal?symbol="+encodeURIComponent(market.value)+"&timeframe="+encodeURIComponent(timeframe.value)+"&mode="+encodeURIComponent(mode.value)),d=await r.json();if(d.detail){alert(d.detail);return}result.classList.remove("hidden");action.textContent=d.action;action.className="big "+(d.action==="BUY"?"buy":d.action==="SELL"?"sell":"wait");buy.textContent=d.buy_votes+"/25";sell.textContent=d.sell_votes+"/25";neutral.textContent=d.neutral_votes+"/25";weight.textContent=d.weighted_consensus+"%";news.textContent=d.news.sentiment;price.textContent=d.entry_price;tf.textContent=timeframe.value==="60min"?"1 HOUR":timeframe.value.replace("min"," MIN");entryTime.textContent=d.entry_time_utc6;expiryTime.textContent=d.expiry_time_utc6;modeBadge.textContent=mode.value==="OTC"?"OTC":"REAL FX"}loadMarkets();</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/app", response_class=HTMLResponse)
def app_page():
    return HTML


@app.get("/health")
def health():
    return {
        "ok": True,
        "bot": "SK BOT",
        "strategies": STRATEGY_COUNT,
        "required_consensus": CONSENSUS_REQUIRED,
    }


@app.get("/markets")
def markets():
    return {"count": len(MARKETS), "markets": MARKETS}


@app.get("/signal")
async def signal(symbol: str = "EURUSD", timeframe: str = "1min", mode: str = "REAL"):
    return await make_signal(symbol, timeframe, mode)


@app.on_event("startup")
async def startup():
    print("SK BOT started | 25 strategies | 14/25 consensus")
    if TELEGRAM_TOKEN:
        asyncio.create_task(telegram_polling())
