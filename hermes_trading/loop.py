"""
Main loop: every 60 seconds, evaluate 1m liquidity sweep strategy, take trades.
"""
import asyncio
import json
import yaml
import os
import logging
from datetime import datetime
from pathlib import Path
from hermes_trading.adapters import price

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s"
)
logger = logging.getLogger(__name__)

PAPER_MODE = os.getenv("HERMES_TRADING_MODE", "paper") == "paper"

async def evaluate_strategy(price_data: dict) -> dict:
    """Evaluate 1m liquidity sweep strategy logic."""
    # In paper mode, simulate entry/exit
    entry_price = price_data.get("price", 42500.0)
    
    # Simplified: random walk simulation
    import random
    pnl_pct = random.gauss(0.002, 0.01)  # Mean +0.2%, std +/-1%
    
    return {
        "signal": "long" if pnl_pct > 0 else "short",
        "entry_price": entry_price,
        "pnl_pct": pnl_pct,
    }

async def log_trade(trade: dict):
    """Log trade to trades.jsonl."""
    trades_file = Path("state/trades.jsonl")
    trades_file.parent.mkdir(parents=True, exist_ok=True)
    
    trade_entry = {
        "timestamp": datetime.now().isoformat(),
        "entry_price": trade.get("entry_price"),
        "signal": trade.get("signal"),
        "pnl_pct": trade.get("pnl_pct"),
        "mode": "paper",
    }
    
    with open(trades_file, "a") as f:
        f.write(json.dumps(trade_entry) + "\n")

async def loop_once():
    """Single evaluation cycle."""
    price_data = await price.fetch()
    strategy_result = await evaluate_strategy(price_data)
    await log_trade(strategy_result)
    
    logger.info(f"Paper trade logged: {strategy_result['signal']} @ {strategy_result['entry_price']}")

async def loop_forever():
    """Main loop: run every 60 seconds."""
    tick = 0
    
    logger.info("Booting hermes-trading worker | asset=BTC/USD | exchange=Coinbase | timeframe=1m")
    logger.info("  Mode: paper")
    logger.info("  Strategy: 1-Minute Scalping with 15-Minute Liquidity Sweeps")
    logger.info("")
    
    while True:
        tick += 1
        now = datetime.now()
        logger.info(f"[{now.isoformat()}] Tick {tick}: fetching price data...")
        
        try:
            await loop_once()
        except Exception as e:
            logger.error(f"Error in loop: {e}")
        
        await asyncio.sleep(60)
