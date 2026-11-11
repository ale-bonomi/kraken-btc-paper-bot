"""
Backtester — paper trading only.

Takes a list of close prices + a list of position signals (-1, 0, 1)
and simulates trading with virtual cash. No live orders, no API keys.

Rules, kept deliberately simple and transparent:
- Starting cash: $10,000 virtual.
- Fee: 0.26% of the notional value of every trade (Kraken-style
  taker fee). Fees are deducted from cash on each execution.
- Position sizing: 100% of portfolio value. Target BTC =
  desired_position * portfolio_value / price. So long = fully
  invested, short = fully short, flat = all cash.
- Trading happens at the close of the bar whose signal is used.
  Signals are computed from closes up to and including that bar,
  so there is no look-ahead beyond the close itself.
- A signal is the *desired position* for that bar. The backtester
  only trades when the desired position differs from the current
  one. Holding the same signal costs nothing.
- Win rate is over *completed* trades: a trade that opens and then
  closes (flat) or flips to the opposite side. A position still
  open at the end of the data does not count as a win or a loss.
- Max drawdown is the largest peak-to-trough fall in the equity
  curve, in percent.

Data sources, in priority order (see load_closes()):
  1. A CSV in ~/workspace/trading-bot/data/ with a close column.
  2. Kraken public OHLC (no auth): https://api.kraken.com/0/public/OHLC
  3. Synthetic data (deterministic random walk, seed 42), used
     only if the network fetch fails. The run always reports which
     source was used.

Run from the project root:
    python3 -m strategy.backtester
    python3 -m strategy.backtester --strategy rsi
    python3 -m strategy.backtester --strategy sma --csv data/btc_usd_daily.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import urllib.request
from pathlib import Path

try:
    from .strategies import rsi_strategy, sma_crossover
except ImportError:  # run as a plain script: python3 backtester.py
    from strategies import rsi_strategy, sma_crossover  # type: ignore

__all__ = [
    "backtest",
    "fetch_kraken_ohlc",
    "load_closes",
    "load_closes_csv",
    "save_ohlc_csv",
    "synthetic_closes",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

STARTING_CASH = 10_000.0
FEE_PCT = 0.0026


# ---------------------------------------------------------------- data ---

def load_closes_csv(path: str | Path) -> list[float]:
    """Load closes from a CSV.

    Accepts a column named close / Close / CLOSE, or a Kraken-style
    OHLC file (time, open, high, low, close, vwap, volume, count).
    """
    closes: list[float] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return closes
        lower = [h.strip().lower() for h in header]
        if "close" in lower:
            idx = lower.index("close")
        elif len(header) >= 5:
            idx = 4  # Kraken OHLC column order
        else:
            raise ValueError(f"Cannot find a close column in {path}: {header}")
        for row in reader:
            if not row or len(row) <= idx:
                continue
            try:
                closes.append(float(row[idx]))
            except ValueError:
                continue
    return closes


def fetch_kraken_ohlc(
    pair: str = "XBTUSD", interval: int = 1440, timeout: int = 20
) -> tuple[list[float], list[int]]:
    """Fetch daily (default) OHLC closes from Kraken public API.

    No authentication required. Returns (closes, timestamps).
    Raises on network / API error so callers can fall back.
    """
    url = f"{KRAKEN_OHLC_URL}?pair={pair}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "muse-paper-bot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    if payload.get("error"):
        raise RuntimeError(f"Kraken API error: {payload['error']}")
    result = payload["result"]
    key = next(k for k in result if k != "last")
    rows = result[key]
    closes = [float(r[4]) for r in rows]
    times = [int(r[0]) for r in rows]
    return closes, times


def save_ohlc_csv(path: str | Path, times: list[int], closes: list[float]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "close"])
        for t, c in zip(times, closes):
            w.writerow([t, c])


def synthetic_closes(
    n: int = 200, seed: int = 42, start: float = 60_000.0
) -> list[float]:
    """Deterministic random-walk closes, for offline testing only."""
    rng = random.Random(seed)
    closes: list[float] = []
    price = start
    for _ in range(n):
        price *= math.exp(rng.gauss(0.0005, 0.02))
        closes.append(round(price, 2))
    return closes


def load_closes(
    csv_path: str | Path | None = None,
) -> tuple[list[float], str]:
    """Load closes, reporting the source used.

    Returns (closes, source_description).
    1. Explicit --csv path, or any *.csv in data/ with a close column.
    2. Kraken public OHLC (XBTUSD, daily).
    3. Synthetic data if the fetch fails.
    """
    candidates: list[Path] = []
    if csv_path:
        candidates.append(Path(csv_path))
    elif DATA_DIR.is_dir():
        candidates.extend(sorted(DATA_DIR.glob("*.csv")))

    for path in candidates:
        if path.is_file():
            try:
                closes = load_closes_csv(path)
            except Exception:
                continue
            if len(closes) >= 40:
                return closes, f"CSV file: {path}"
    try:
        closes, times = fetch_kraken_ohlc()
        # Cache it so the next run is reproducible offline.
        try:
            save_ohlc_csv(DATA_DIR / "btc_usd_daily.csv", times, closes)
        except Exception:
            pass
        return closes, "Kraken public OHLC, XBTUSD daily (no auth)"
    except Exception as exc:
        closes = synthetic_closes()
        return closes, f"synthetic data (Kraken fetch failed: {exc})"


# ------------------------------------------------------------ backtest ---

def backtest(
    closes: list[float],
    signals: list[int],
    starting_cash: float = STARTING_CASH,
    fee_pct: float = FEE_PCT,
) -> dict:
    """Simulate position signals on closes. See module docstring."""
    if len(closes) != len(signals):
        raise ValueError("closes and signals must have the same length")
    if not closes:
        raise ValueError("closes must not be empty")
    if any(s not in (-1, 0, 1) for s in signals):
        raise ValueError("signals must be -1, 0 or 1")

    cash = starting_cash
    position_btc = 0.0
    current_side = 0  # -1, 0, 1
    entry_price: float | None = None
    equity_curve: list[float] = []
    executions = 0
    completed: list[dict] = []

    for i, price in enumerate(closes):
        desired = signals[i]
        portfolio = cash + position_btc * price

        if desired != current_side:
            # Close the old trade (if any) for win-rate accounting.
            if current_side != 0 and entry_price is not None:
                if current_side == 1:
                    ret = (price - entry_price) / entry_price
                else:
                    ret = (entry_price - price) / entry_price
                completed.append(
                    {
                        "side": current_side,
                        "entry": entry_price,
                        "exit": price,
                        "return": ret,
                        "win": ret > 0,
                    }
                )
            # Rebalance to the target position at this close.
            target_btc = desired * portfolio / price if price > 0 else 0.0
            delta_btc = target_btc - position_btc
            if delta_btc != 0:
                notional = abs(delta_btc * price)
                fee = notional * fee_pct
                cash -= delta_btc * price + fee
                position_btc = target_btc
                executions += 1
            current_side = desired
            entry_price = price if desired != 0 else None

        equity_curve.append(cash + position_btc * price)

    final_value = equity_curve[-1]
    total_return = (final_value - starting_cash) / starting_cash

    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)

    wins = sum(1 for t in completed if t["win"])
    win_rate = (wins / len(completed)) if completed else 0.0

    return {
        "starting_cash": starting_cash,
        "final_value": final_value,
        "total_return": total_return,
        "total_return_pct": total_return * 100,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd * 100,
        "number_of_trades": executions,
        "completed_trades": len(completed),
        "wins": wins,
        "losses": len(completed) - wins,
        "win_rate": win_rate,
        "equity_curve": equity_curve,
        "trades": completed,
    }


def _print_summary(name: str, result: dict, n_bars: int, source: str) -> None:
    print(f"\n--- {name} ---")
    print(f"bars: {n_bars} | data: {source}")
    print(f"starting cash: ${result['starting_cash']:,.2f}")
    print(f"final value:   ${result['final_value']:,.2f}")
    print(f"total return:  {result['total_return_pct']:+.2f}%")
    print(f"max drawdown:  {result['max_drawdown_pct']:.2f}%")
    print(f"trades (executions): {result['number_of_trades']}")
    print(
        f"completed trades: {result['completed_trades']} "
        f"(wins {result['wins']}, losses {result['losses']})"
    )
    print(f"win rate:      {result['win_rate'] * 100:.1f}%")


def run(strategy: str = "sma", csv_path: str | None = None) -> dict:
    closes, source = load_closes(csv_path)
    if strategy == "sma":
        signals = sma_crossover(closes, fast=10, slow=30)
        label = "SMA crossover (10/30)"
    elif strategy == "rsi":
        signals = rsi_strategy(closes, period=14)
        label = "RSI (14, buy <30 / sell >70)"
    else:
        raise ValueError("strategy must be 'sma' or 'rsi'")
    result = backtest(closes, signals)
    _print_summary(label, result, len(closes), source)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper backtester (no live orders)")
    parser.add_argument("--strategy", choices=["sma", "rsi", "both"], default="both")
    parser.add_argument("--csv", default=None, help="CSV file with a close column")
    args = parser.parse_args()
    if args.strategy == "both":
        run("sma", args.csv)
        run("rsi", args.csv)
    else:
        run(args.strategy, args.csv)


if __name__ == "__main__":
    main()
