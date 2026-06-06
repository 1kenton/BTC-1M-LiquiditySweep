"""
Reflection cycle: analyzes trades and proposes ONE strategy change.
"""
import json
import yaml
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def reflect(trades: list, goal: dict, strategy: dict) -> dict:
    """Propose ONE variable change based on trade performance."""
    if not trades or len(trades) < 5:
        return {"status": "waiting", "trades_count": len(trades)}
    
    last_5 = trades[-5:]
    returns = [t.get("pnl_pct", 0) for t in last_5]
    win_count = len([r for r in returns if r > 0])
    win_rate = win_count / len(last_5)
    avg_return = sum(returns) / len(last_5)
    
    hypothesis = None
    
    if win_rate < 0.4:
        hypothesis = {
            "variable": "wick_rejection_strictness",
            "old_value": "current",
            "new_value": "tighter (avoid weak rejections)",
            "reason": f"Win rate {win_rate:.1%} too low, need stricter wick rejection confirmation",
        }
    elif win_rate < 0.5:
        hypothesis = {
            "variable": "stop_loss_distance",
            "old_value": "1m candle",
            "new_value": "15m candle (safer)",
            "reason": f"Win rate {win_rate:.1%} below 50%, wider stop allows better risk:reward",
        }
    elif win_rate >= 0.7:
        hypothesis = {
            "variable": "position_size",
            "old_value": "0.5R",
            "new_value": "1.0R",
            "reason": f"Win rate {win_rate:.1%} strong, increase position size to capitalize",
        }
    
    return {
        "status": "ready" if hypothesis else "no_change",
        "hypothesis": hypothesis,
        "win_rate": win_rate,
        "avg_return": avg_return,
    }
