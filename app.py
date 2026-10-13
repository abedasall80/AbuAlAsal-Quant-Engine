import os
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_NAME = "AbuAlAsal Quant Engine"
APP_VERSION = "1.0.0"
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "https://abedasasl.com,https://www.abedasasl.com").split(",") if x.strip()]
app = FastAPI(title=APP_NAME, version=APP_VERSION, description="Market-data and quantitative-analysis API. Informational only; does not execute trades.")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False, allow_methods=["GET","POST","OPTIONS"], allow_headers=["*"])
INTERVALS = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w"}

class AnalyzeRequest(BaseModel):
    symbol: str = Field(default="BTCUSDT", min_length=5, max_length=20)
    interval: str = Field(default="4h", min_length=2, max_length=5)
    limit: int = Field(default=200, ge=50, le=500)

@app.get("/")
async def root():
    return {"service": APP_NAME, "version": APP_VERSION, "status": "running", "docs": "/docs", "health": "/health", "note": "Analysis only. No order execution."}

@app.get("/health")
async def health():
    return {"status":"ok","service":APP_NAME,"version":APP_VERSION,"timestamp":datetime.now(timezone.utc).isoformat()}

@app.get("/api/v1/market/klines")
async def market_klines(symbol: str = Query("BTCUSDT", min_length=5, max_length=20), interval: str = Query("4h"), limit: int = Query(200, ge=1, le=1000)):
    symbol = symbol.upper().strip()
    if interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval. Allowed: {sorted(INTERVALS)}")
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(f"{BINANCE_BASE_URL.rstrip('/')}/api/v3/klines", params={"symbol":symbol,"interval":interval,"limit":limit})
        if response.status_code != 200:
            try:
                detail = response.json().get("msg", "Market data provider rejected the request")
            except Exception:
                detail = "Market data provider rejected the request"
            raise HTTPException(status_code=502, detail=detail)
        raw = response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Market data request timed out")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Unable to reach market data provider")
    candles = [{"open_time":r[0],"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4]),"volume":float(r[5]),"close_time":r[6],"quote_volume":float(r[7]),"trades":int(r[8])} for r in raw]
    return {"symbol":symbol,"interval":interval,"count":len(candles),"source":"Binance Spot API","fetched_at":datetime.now(timezone.utc).isoformat(),"candles":candles}

@app.get("/api/v1/market/ticker")
async def market_ticker(symbol: str = Query("BTCUSDT", min_length=5, max_length=20)):
    symbol = symbol.upper().strip()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(f"{BINANCE_BASE_URL.rstrip('/')}/api/v3/ticker/24hr", params={"symbol":symbol})
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Market data provider rejected the symbol")
        data = response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Market data request timed out")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Unable to reach market data provider")
    return {"symbol":symbol,"price":float(data["lastPrice"]),"price_change_percent_24h":float(data["priceChangePercent"]),"high_24h":float(data["highPrice"]),"low_24h":float(data["lowPrice"]),"volume_24h":float(data["volume"]),"source":"Binance Spot API","fetched_at":datetime.now(timezone.utc).isoformat()}

@app.post("/api/v1/analyze")
async def analyze(req: AnalyzeRequest):
    if req.interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval. Allowed: {sorted(INTERVALS)}")
    market = await market_klines(symbol=req.symbol, interval=req.interval, limit=req.limit)
    candles = market["candles"]
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    def sma(values, period):
        return sum(values[-period:])/period if len(values) >= period else None
    def rsi(values, period=14):
        if len(values) < period + 1: return None
        changes = [values[i]-values[i-1] for i in range(len(values)-period, len(values))]
        gains = sum(max(x,0) for x in changes)/period
        losses = sum(max(-x,0) for x in changes)/period
        if losses == 0: return 100.0 if gains > 0 else 50.0
        return 100-(100/(1+gains/losses))
    current = closes[-1]
    ma20, ma50, rsi14 = sma(closes,20), sma(closes,50), rsi(closes)
    true_ranges = []
    for i in range(1,len(candles)):
        prev = closes[i-1]; c = candles[i]
        true_ranges.append(max(c["high"]-c["low"], abs(c["high"]-prev), abs(c["low"]-prev)))
    checks = [
      {"name":"السعر فوق المتوسط 20","passed":ma20 is not None and current>ma20},
      {"name":"المتوسط 20 فوق المتوسط 50","passed":ma20 is not None and ma50 is not None and ma20>ma50},
      {"name":"RSI أعلى من 50","passed":rsi14 is not None and rsi14>50},
      {"name":"حجم التداول فوق متوسط 20","passed":sma(volumes,20) is not None and volumes[-1]>sma(volumes,20)},
      {"name":"إغلاق الشمعة الأخيرة صاعد","passed":candles[-1]["close"]>candles[-1]["open"]},
      {"name":"السعر أعلى من إغلاق الشمعة السابقة","passed":len(closes)>1 and closes[-1]>closes[-2]},
      {"name":"موجات إليوت","passed":None,"status":"غير محسوب حتى إضافة قواعد موجية قابلة للاختبار"},
      {"name":"جان مربع 9","passed":None,"status":"غير محسوب حتى إضافة قواعد جان قابلة للاختبار"},
      {"name":"فيبوناتشي","passed":None,"status":"غير محسوب حتى تحديد موجة مرجعية"},
      {"name":"الدورات الزمنية","passed":None,"status":"غير محسوب حتى إضافة إعداد دورة موثّق"},
      {"name":"تأكيد إطار زمني أعلى","passed":None,"status":"غير محسوب حتى جلب إطار أعلى"},
      {"name":"اختبار تاريخي ومعايرة الاحتمالات","passed":None,"status":"غير محسوب حتى بناء اختبار تاريخي"},
    ]
    passed = sum(1 for x in checks if x["passed"] is True)
    evaluated = sum(1 for x in checks if x["passed"] is not None)
    signal = "شراء مبدئي" if passed >= 5 else ("مراقبة" if passed >= 3 else "لا توجد إشارة")
    return {"symbol":req.symbol.upper(),"interval":req.interval,"price":current,"source":market["source"],"fetched_at":market["fetched_at"],"indicators":{"sma20":ma20,"sma50":ma50,"rsi14":rsi14,"atr14":sma(true_ranges,min(14,len(true_ranges))) if true_ranges else None},"checklist":{"passed":passed,"evaluated":evaluated,"total":len(checks),"items":checks},"signal":signal,"probability":None,"entry":None,"stop_loss":None,"targets":[],"warning":"قراءة أولية وليست توصية مالية. لا تعرض احتمالات أو أهدافًا قبل تطوير استراتيجية واختبارها تاريخيًا."}
