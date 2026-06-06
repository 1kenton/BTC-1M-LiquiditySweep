"""
Scoring function: evaluates trade outcomes against goal.yaml
"""
import yaml
import numpy as np
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def load_goal(goal_path: str = "state/goal.yaml") -> dict:
    """Load goals from YAML."""
    try:
        with open(goal_path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load goal: {e}")
        return {}

def score(trades: list, goal: dict) -> dict:
    """Score trades against goal metrics."""
    if not trades:
        return {"score": 0.0, "status": "no_trades"}
    
    returns = [t.get("pnl_pct", 0) for t in trades]
    total_return = sum(returns)
    max_dd = min(returns) if returns else 0
    win_count = len([r for r in returns if r > 0])
    win_rate = win_count / len(returns) if returns else 0
    
    target = goal.get("target_return_30d", 0.10)
    max_dd_goal = goal.get("max_drawdown", 0.05)
    min_sharpe = goal.get("min_sharpe", 1.2)
    
    score = 0.0
    if total_return >= target and max_dd >= -max_dd_goal:
        score = 1.0
    elif total_return >= target * 0.5:
        score = 0.5
    
    return {
        "score": score,
        "total_return": total_return,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "trades_count": len(trades),
    }
