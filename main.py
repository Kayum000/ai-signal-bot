import os
import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("TWELVE_DATA_API_KEY")
app = FastAPI(title="AI Signal Bot API")

def indicators(df):
    close = df["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-12)
    df["rsi"] = 100 - (100 / (1 + rs))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()
    return df

@app.get("/")
def root():
    return {"status":"online","service":"AI Signal Bot"}

@app.get("/health")
def health():
    return {"ok":True}

@app.get("/signal")
async def signal(symbol: str="EUR/USD", interval: str="1min"):
    if not API_KEY:
        raise HTTPException(500, "TWELVE_DATA_API_KEY is missing")
    async with httpx.AsyncClient(timeout=15) as client:
        r=await client.get("https://api.twelvedata.com/time_series",
            params={"symbol":symbol,"interval":interval,"outputsize":100,"apikey":API_KEY})
    data=r.json()
    if "values" not in data:
        raise HTTPException(502, data.get("message","Market data unavailable"))
    df=pd.DataFrame(data["values"])
    for c in ["open","high","low","close"]: df[c]=pd.to_numeric(df[c])
    df=indicators(df.sort_values("datetime")).dropna()
    if len(df)<3: raise HTTPException(502,"Not enough candles")
    a,b=df.iloc[-2],df.iloc[-1]
    bullish=a.macd<=a.macd_signal and b.macd>b.macd_signal
    bearish=a.macd>=a.macd_signal and b.macd<b.macd_signal
    buy=sell=0
    if b.rsi<35: buy+=2
    elif b.rsi<45: buy+=1
    if b.rsi>65: sell+=2
    elif b.rsi>55: sell+=1
    if bullish: buy+=2
    if bearish: sell+=2
    if b.ema9>b.ema21: buy+=1
    if b.ema9<b.ema21: sell+=1
    if buy>=4 and buy>sell: action,confidence="BUY / CALL",min(95,60+buy*6)
    elif sell>=4 and sell>buy: action,confidence="SELL / PUT",min(95,60+sell*6)
    else: action,confidence="WAIT",50
    return {"symbol":symbol,"interval":interval,"action":action,"confidence":confidence,
            "rsi":round(float(b.rsi),2),"macd":round(float(b.macd),6),
            "macd_signal":round(float(b.macd_signal),6),"ema9":round(float(b.ema9),6),
            "ema21":round(float(b.ema21),6),"candle_time":str(b["datetime"])}
