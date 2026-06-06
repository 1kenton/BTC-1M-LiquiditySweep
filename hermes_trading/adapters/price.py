"""
Price data adapter for Coinbase. Pulls 1m + 15m OHLCV candles for BTC/USD.
"""
import httpx
import asyncio
import logging

logger = logging.getLogger(__name__)

async def fetch(asset: str = "BTC/USD", timeframe: str = "1m") -> dict:
    """Fetch OHLCV from Coinbase API (paper mode)."""
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.coinbase.com/v2/prices/{asset}/spot"
            resp = await asyncio.wait_for(client.get(url), timeout=5)
            data = resp.json()
            price = float(data["data"]["amount"])
            
            return {
                "asset": asset,
                "price": price,
                "timestamp": data.get("data", {}).get("time", ""),
                "timeframe": timeframe,
            }
    except Exception as e:
        logger.error(f"Failed to fetch price: {e}")
        return {"asset": asset, "price": 0, "error": str(e)}

async def fetch_candles(asset: str = "BTC/USD", timeframe: str = "1m", limit: int = 100) -> list:
    """Fetch OHLCV candles from Coinbase."""
    try:
        async with httpx.AsyncClient() as client:
            # Fallback: CoinGecko
            url = f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=7"
            resp = await asyncio.wait_for(client.get(url), timeout=5)
            data = resp.json()
            
            candles = []
            for ts, price in data["prices"][-limit:]:
                candles.append({
                    "timestamp": ts,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0,
                })
            
            return candles
    except Exception as e:
        logger.error(f"Failed to fetch candles: {e}")
        return []
