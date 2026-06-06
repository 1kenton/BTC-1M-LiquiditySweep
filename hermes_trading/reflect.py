"""
Reflection cycle: reads closed trades, calls Anthropic API, proposes ONE
strategy variable change, updates strategy.yaml in place.

Usage:
  python -m hermes_trading.reflect   (called internally by loop.py)
"""
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

TRADES_FILE     = Path("state/trades.jsonl")
STRATEGY_FILE   = Path("state/strategy.yaml")
GOAL_FILE       = Path("state/goal.yaml")
HYPOTHESES_FILE = Path("state/hypotheses.jsonl")
HISTORY_DIR     = Path("state/history")

TUNABLE = ["entry.swing_lookback", "entry.min_wick_ratio",
           "entry.valid_window_bars", "entry.sl_multiplier"]


# ── helpers ────────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _load_trades(n: int = 25) -> list:
    if not TRADES_FILE.exists():
        return []
    lines = TRADES_FILE.read_text().strip().splitlines()
    records = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def _closed_trade_count() -> int:
    if not TRADES_FILE.exists():
        return 0
    return sum(1 for line in TRADES_FILE.read_text().splitlines() if line.strip())


def _save_history(strategy: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    version = strategy.get("version", "00")
    dest = HISTORY_DIR / f"v{version.zfill(4)}.yaml"
    dest.write_text(yaml.dump(strategy, default_flow_style=False))


def _bump_version(strategy: dict) -> dict:
    try:
        v = int(strategy.get("version", "01"))
        strategy["version"] = str(v + 1).zfill(2)
    except ValueError:
        strategy["version"] = "02"
    return strategy


def _apply_change(strategy: dict, variable: str, new_value) -> dict:
    """Apply dot-notation variable change (e.g. 'entry.swing_lookback')."""
    keys = variable.split(".")
    node = strategy
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = new_value
    return strategy


# ── Anthropic reflection ───────────────────────────────────────────────────────

def run_reflection() -> bool:
    """
    Call Anthropic API, parse hypothesis, update strategy.yaml.
    Returns True if a change was applied.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping AI reflection")
        return False

    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed")
        return False

    trades   = _load_trades(25)
    strategy = _load_yaml(STRATEGY_FILE)
    goal     = _load_yaml(GOAL_FILE)

    if len(trades) < 5:
        logger.info(f"Only {len(trades)} closed trades — need 5 to reflect")
        return False

    # Build stats summary
    returns   = [t.get("pnl_pct", 0) for t in trades]
    wins      = [r for r in returns if r > 0]
    losses    = [r for r in returns if r <= 0]
    win_rate  = len(wins) / len(returns) if returns else 0
    avg_win   = sum(wins) / len(wins) if wins else 0
    avg_loss  = sum(losses) / len(losses) if losses else 0
    total_ret = sum(returns)

    prompt = f"""You are a systematic trading strategy optimizer.

STRATEGY (current):
{yaml.dump(strategy, default_flow_style=False)}

GOAL:
{yaml.dump(goal, default_flow_style=False)}

LAST {len(trades)} CLOSED TRADES:
{json.dumps(trades, indent=2)}

PERFORMANCE SUMMARY:
- Win rate:    {win_rate:.1%}
- Avg win:     {avg_win*100:.3f}%
- Avg loss:    {avg_loss*100:.3f}%
- Total return:{total_ret*100:.3f}%
- Target/30d:  {goal.get('target_return_30d', 0.05)*100:.1f}%

TUNABLE VARIABLES (only these can be changed):
{json.dumps(TUNABLE, indent=2)}

TASK:
Propose exactly ONE change to improve performance toward the goal.
Reply with ONLY valid JSON in this exact format — no other text:
{{
  "variable": "<dot.notation.key from tunable list>",
  "old_value": <current numeric value>,
  "new_value": <proposed numeric value>,
  "reasoning": "<one sentence why>"
}}"""

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        hypothesis = json.loads(raw)
    except Exception as e:
        logger.error(f"Anthropic call or parse failed: {e}")
        return False

    variable = hypothesis.get("variable", "")
    new_value = hypothesis.get("new_value")
    if variable not in TUNABLE or new_value is None:
        logger.warning(f"Invalid hypothesis variable '{variable}' — skipping")
        return False

    # Save history, apply change, write back
    _save_history(strategy)
    strategy = _bump_version(strategy)
    strategy = _apply_change(strategy, variable, new_value)
    STRATEGY_FILE.write_text(yaml.dump(strategy, default_flow_style=False))

    # Log hypothesis
    HYPOTHESES_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "version": strategy["version"],
        "trades_evaluated": len(trades),
        "win_rate": round(win_rate, 4),
        "total_return": round(total_ret, 6),
        **hypothesis,
    }
    with open(HYPOTHESES_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    logger.info(
        f"Reflection applied | v{strategy['version']} | "
        f"{variable}: {hypothesis['old_value']} → {new_value} | "
        f"{hypothesis['reasoning']}"
    )
    return True
