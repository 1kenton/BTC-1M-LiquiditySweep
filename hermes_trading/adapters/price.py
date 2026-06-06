"""
Price data adapter — Coinbase Exchange public API.
Provides spot price and OHLCV candles for BTC-USD.
"""
import httpx
import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

PRODUCT = "BTC-USD"
BASE_URL = "https://api.exchange.coinbase.com"


async def fetch(asset: str = "BTC/USD", timeframe: str = "1m") -> dict:
    """Current spot price via Coinbase exchange-rates endpoint."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await asyncio.wait_for(
                client.get("https://api.coinbase.com/v2/exchange-rates?currency=BTC"),
                timeout=5,
            )
            data = resp.json()
            price = float(data["data"]["rates"]["USD"])
        return {
            "asset": asset,
            "price": price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timeframe": timeframe,
        }
    except Exception as e:
        logger.error(f"spot fetch failed: {e}")
        return {"asset": asset, "price": 0.0, "timestamp": datetime.now(timezone.utc).isoformat(), "timeframe": timeframe}


async def fetch_ohlcv(granularity: int = 60, limit: int = 50) -> list:
    """
    OHLCV candles from Coinbase Exchange public API.

    granularity: bar size in seconds (60=1m, 900=15m)
    limit:       number of candles to return (max 300)
    Returns list of dicts in chronological order (oldest first):
      {timestamp, open, high, low, close, volume}
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=granularity * (limit + 2))

    params = {
        "granularity": granularity,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }

    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                resp = await asyncio.wait_for(
                    client.get(f"{BASE_URL}/products/{PRODUCT}/candles", params=params),
                    timeout=10,
                )
                resp.raise_for_status()
                raw = resp.json()  # [[ts, low, high, open, close, vol], ...] newest first

            candles = []
            for row in reversed(raw):
                ts, low, high, open_, close, volume = row
                candles.append({
                    "timestamp": int(ts),
                    "open": float(open_),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    "volume": float(volume),
                })
            return candles[-limit:]

        except Exception as e:
            wait = 2 ** attempt
            logger.warning(f"ohlcv fetch attempt {attempt + 1} failed ({e}), retry in {wait}s")
            await asyncio.sleep(wait)

    logger.error("ohlcv fetch failed after 3 attempts")
    return []
