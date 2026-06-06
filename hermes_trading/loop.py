"""
1-Minute Scalping with 15-Minute Liquidity Sweeps strategy loop.

Setup logic (from the video):
  Step 1 — Mark untouched 15m swing highs/lows.
  Step 2 — Wait for a sweep candle: price wicks through the level but the
            15m candle CLOSES back inside. Candle color must match the sweep
            direction (bearish close for a high sweep, bullish for a low sweep).
  Step 3 — Mark the sweep candle's close price as the trigger line.
            On the 1m chart, wait for the first candle that closes
            above/below the trigger line inside the next 15m window.
            That close is the entry.

Target: next 15m swing point in the trade direction.
Stop:   below/above the 1m entry candle's swing extreme.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from hermes_trading.adapters import price

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

PAPER_MODE     = os.getenv("HERMES_TRADING_MODE", "paper") == "paper"
STATE_FILE     = Path("state/worker_state.json")
TRADES_FILE    = Path("state/trades.jsonl")
STRATEGY_FILE  = Path("state/strategy.yaml")
GOAL_FILE      = Path("state/goal.yaml")


def load_params() -> dict:
    """Read tunable parameters from strategy.yaml. Falls back to safe defaults."""
    try:
        raw = yaml.safe_load(STRATEGY_FILE.read_text()) or {}
        e = raw.get("entry", {})
        return {
            "swing_lookback":    int(e.get("swing_lookback",    2)),
            "min_wick_ratio":    float(e.get("min_wick_ratio",  0.30)),
            "valid_window_bars": int(e.get("valid_window_bars", 2)),
            "sl_multiplier":     float(e.get("sl_multiplier",   0.50)),
        }
    except Exception:
        return {"swing_lookback": 2, "min_wick_ratio": 0.30,
                "valid_window_bars": 2, "sl_multiplier": 0.50}


def reflection_due() -> bool:
    """Return True if a reflection cycle should fire now."""
    try:
        goal = yaml.safe_load(GOAL_FILE.read_text()) or {}
    except Exception:
        goal = {}
    every = int(goal.get("reflection_every", 5))
    if not TRADES_FILE.exists():
        return False
    closed = sum(1 for line in TRADES_FILE.read_text().splitlines() if line.strip())
    # fire on exact multiples so we don't double-fire
    return closed > 0 and closed % every == 0

# ── state helpers ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"active_setup": None, "current_trade": None, "seen_sweep_ts": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def log_trade(record: dict) -> None:
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── swing level detection ──────────────────────────────────────────────────────

def swing_highs(candles: list, n: int = 2) -> list:  # noqa: E302
    """Prices of swing highs in the completed candle list (n-bar pivot)."""
    result = []
    for i in range(n, len(candles) - n):
        c = candles[i]
        if all(c["high"] >= candles[j]["high"] for j in range(i - n, i + n + 1) if j != i):
            result.append(c["high"])
    return result


def swing_lows(candles: list, n: int = 2) -> list:
    """Prices of swing lows in the completed candle list (n-bar pivot)."""
    result = []
    for i in range(n, len(candles) - n):
        c = candles[i]
        if all(c["low"] <= candles[j]["low"] for j in range(i - n, i + n + 1) if j != i):
            result.append(c["low"])
    return result


# ── sweep detection ────────────────────────────────────────────────────────────

def detect_sweep(candle: dict, highs: list, lows: list,
                 min_wick_ratio: float = 0.30) -> dict | None:
    """
    Returns a setup dict if the candle swept a level, else None.

    Bearish sweep of a high  → short setup.
    Bullish sweep of a low   → long setup.
    """
    candle_range = candle["high"] - candle["low"]
    if candle_range < 10:
        return None

    # Short setup: wick above swing high, close back below, bearish body
    for level in sorted(highs):
        if (candle["high"] > level
                and candle["close"] < level
                and candle["close"] < candle["open"]):
            wick = candle["high"] - max(candle["open"], candle["close"])
            if wick / candle_range >= min_wick_ratio:
                return {
                    "direction": "short",
                    "swept_level": level,
                    "trigger_line": candle["close"],
                    "sweep_ts": candle["timestamp"],
                }

    # Long setup: wick below swing low, close back above, bullish body
    for level in sorted(lows, reverse=True):
        if (candle["low"] < level
                and candle["close"] > level
                and candle["close"] > candle["open"]):
            wick = min(candle["open"], candle["close"]) - candle["low"]
            if wick / candle_range >= min_wick_ratio:
                return {
                    "direction": "long",
                    "swept_level": level,
                    "trigger_line": candle["close"],
                    "sweep_ts": candle["timestamp"],
                }

    return None


# ── 1m entry check ─────────────────────────────────────────────────────────────

def find_entry(candles_1m: list, setup: dict, window_start_ts: int) -> dict | None:
    """
    Look for the first 1m candle that closes through the trigger line
    within the current 15m window.
    """
    trigger = setup["trigger_line"]
    direction = setup["direction"]

    for c in candles_1m:
        if c["timestamp"] < window_start_ts:
            continue
        if direction == "long" and c["close"] > trigger:
            sl = c["low"] - abs(c["close"] - trigger) * 0.5
            return {
                "direction": "long",
                "entry_price": c["close"],
                "stop_loss": round(sl, 2),
                "trigger_line": trigger,
                "entry_ts": c["timestamp"],
            }
        if direction == "short" and c["close"] < trigger:
            sl = c["high"] + abs(trigger - c["close"]) * 0.5
            return {
                "direction": "short",
                "entry_price": c["close"],
                "stop_loss": round(sl, 2),
                "trigger_line": trigger,
                "entry_ts": c["timestamp"],
            }
    return None


def find_target(candles_15m: list, entry_price: float, direction: str) -> float | None:
    """Next 15m swing point beyond entry in the trade direction."""
    if direction == "long":
        candidates = [h for h in swing_highs(candles_15m) if h > entry_price]
        return min(candidates) if candidates else None
    else:
        candidates = [l for l in swing_lows(candles_15m) if l < entry_price]
        return max(candidates) if candidates else None


# ── trade exit check ───────────────────────────────────────────────────────────

def check_exit(trade: dict, candles_1m: list) -> dict | None:
    """
    Return closed trade dict if price hit TP or SL, else None.
    Checks each 1m candle after entry.
    """
    direction = trade["direction"]
    entry_ts = trade["entry_ts"]
    sl = trade["stop_loss"]
    tp = trade.get("target")

    for c in candles_1m:
        if c["timestamp"] <= entry_ts:
            continue
        if direction == "long":
            if tp and c["high"] >= tp:
                pnl_pct = (tp - trade["entry_price"]) / trade["entry_price"]
                return {**trade, "exit_price": tp, "exit_reason": "tp", "pnl_pct": round(pnl_pct, 6)}
            if c["low"] <= sl:
                pnl_pct = (sl - trade["entry_price"]) / trade["entry_price"]
                return {**trade, "exit_price": sl, "exit_reason": "sl", "pnl_pct": round(pnl_pct, 6)}
        else:
            if tp and c["low"] <= tp:
                pnl_pct = (trade["entry_price"] - tp) / trade["entry_price"]
                return {**trade, "exit_price": tp, "exit_reason": "tp", "pnl_pct": round(pnl_pct, 6)}
            if c["high"] >= sl:
                pnl_pct = (trade["entry_price"] - sl) / trade["entry_price"]
                return {**trade, "exit_price": sl, "exit_reason": "sl", "pnl_pct": round(pnl_pct, 6)}
    return None


# ── main loop ──────────────────────────────────────────────────────────────────

GRANULARITY_15M = 900  # seconds
GRANULARITY_1M = 60


async def loop_once(state: dict) -> dict:
    """One evaluation cycle. Mutates and returns state."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    params = load_params()

    window_start_ts = (now_ts // GRANULARITY_15M) * GRANULARITY_15M

    candles_15m, candles_1m = await asyncio.gather(
        price.fetch_ohlcv(granularity=GRANULARITY_15M, limit=25),
        price.fetch_ohlcv(granularity=GRANULARITY_1M, limit=30),
    )

    if not candles_15m or not candles_1m:
        logger.warning("candle fetch returned empty — skipping tick")
        return state

    completed_15m = [c for c in candles_15m if c["timestamp"] < window_start_ts]

    # ── check trade exit ───────────────────────────────────────────────────────
    if state["current_trade"]:
        closed = check_exit(state["current_trade"], candles_1m)
        if closed:
            closed["mode"] = "paper"
            closed["close_ts"] = now_ts
            log_trade(closed)
            logger.info(
                f"Trade CLOSED | {closed['direction']} | entry={closed['entry_price']:.2f} "
                f"exit={closed['exit_price']:.2f} reason={closed['exit_reason']} "
                f"pnl={closed['pnl_pct']*100:.3f}%"
            )
            state["current_trade"] = None
            state["active_setup"] = None

            # ── reflection trigger ─────────────────────────────────────────────
            if reflection_due():
                logger.info("Reflection cycle triggered — calling Anthropic API")
                try:
                    from hermes_trading.reflect import run_reflection
                    run_reflection()
                except Exception as e:
                    logger.error(f"Reflection failed: {e}")
        else:
            logger.info(
                f"Trade OPEN | {state['current_trade']['direction']} "
                f"@ {state['current_trade']['entry_price']:.2f} "
                f"SL={state['current_trade']['stop_loss']:.2f} "
                f"TP={state['current_trade'].get('target', 'tbd')}"
            )
        return state

    # ── check for 1m entry on existing setup ──────────────────────────────────
    if state["active_setup"]:
        setup = state["active_setup"]
        setup_expires = setup["sweep_ts"] + GRANULARITY_15M * params["valid_window_bars"]
        if now_ts > setup_expires:
            logger.info(f"Setup expired | {setup['direction']} trigger={setup['trigger_line']:.2f}")
            state["active_setup"] = None
        else:
            entry = find_entry(candles_1m, setup, window_start_ts)
            if entry:
                target = find_target(completed_15m, entry["entry_price"], entry["direction"])
                entry["target"] = target
                entry["mode"] = "paper"
                entry["open_ts"] = now_ts
                state["current_trade"] = entry
                state["active_setup"] = None
                logger.info(
                    f"ENTRY | {entry['direction']} @ {entry['entry_price']:.2f} "
                    f"SL={entry['stop_loss']:.2f} TP={target}"
                )
            else:
                logger.info(
                    f"Watching for 1m entry | {setup['direction']} trigger={setup['trigger_line']:.2f}"
                )
        return state

    # ── scan 15m candles for a fresh sweep ────────────────────────────────────
    if len(completed_15m) < 5:
        logger.info("Not enough 15m history yet — waiting")
        return state

    n = params["swing_lookback"]
    highs = swing_highs(completed_15m, n=n)
    lows  = swing_lows(completed_15m,  n=n)

    recent = completed_15m[-1]
    if recent["timestamp"] in state["seen_sweep_ts"]:
        logger.info("No new sweep detected — standing by")
        return state

    setup = detect_sweep(recent, highs, lows, min_wick_ratio=params["min_wick_ratio"])
    if setup:
        state["active_setup"] = setup
        state["seen_sweep_ts"].append(recent["timestamp"])
        state["seen_sweep_ts"] = state["seen_sweep_ts"][-50:]
        logger.info(
            f"SWEEP DETECTED | {setup['direction']} | level={setup['swept_level']:.2f} "
            f"trigger={setup['trigger_line']:.2f} — watching 1m for entry"
        )
    else:
        logger.info(
            f"No sweep | last 15m H={recent['high']:.2f} L={recent['low']:.2f} "
            f"C={recent['close']:.2f} | highs={[round(h,1) for h in highs[-3:]]} "
            f"lows={[round(l,1) for l in lows[-3:]]}"
        )

    return state


async def loop_forever() -> None:
    logger.info("Booting hermes-trading worker | BTC-1M-LiquiditySweep | paper mode")
    logger.info("Strategy: 15m liquidity sweep → 1m entry on trigger-line break")

    state = load_state()
    tick = 0

    while True:
        tick += 1
        now = datetime.now(timezone.utc)
        logger.info(f"[{now.isoformat()}] Tick {tick}")
        try:
            state = await loop_once(state)
            save_state(state)
        except Exception as e:
            logger.error(f"Tick error: {e}", exc_info=True)
        await asyncio.sleep(GRANULARITY_1M)
