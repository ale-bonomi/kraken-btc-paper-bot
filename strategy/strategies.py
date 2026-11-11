"""
Strategy lab — paper trading only.

Two simple, transparent strategies for BTC.

Both take a list of close prices and return a list of signals with the
same length, where each signal is the *desired position* for that bar:
    1  = long
    0  = flat / no position
   -1  = short (sell / exit a long in the long-only reading)

Warm-up bars, before an indicator can be computed, return 0.

No live orders, no API keys, no external calls in this module.
Pure Python, standard library only, so the logic is easy to audit.
"""

from __future__ import annotations

__all__ = [
    "sma",
    "sma_crossover",
    "compute_rsi",
    "rsi_strategy",
]


def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average. First ``period - 1`` entries are None."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    window_sum = 0.0
    for i, v in enumerate(values):
        window_sum += v
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            out[i] = window_sum / period
    return out


def sma_crossover(
    closes: list[float], fast: int = 10, slow: int = 30
) -> list[int]:
    """
    SMA crossover, fast 10 / slow 30 on close.

    Desired position:
      1  when fast SMA > slow SMA
     -1  when fast SMA < slow SMA
      0  during warm-up (first slow-1 bars) or on an exact tie

    This is a position signal, not just a crossover event: it stays
    long while the fast average is above the slow one, so the
    backtester only trades when the position actually changes.
    """
    if fast >= slow:
        raise ValueError("fast must be smaller than slow")
    if len(closes) < slow:
        return [0] * len(closes)
    fast_sma = sma(closes, fast)
    slow_sma = sma(closes, slow)
    signals: list[int] = []
    for f, s in zip(fast_sma, slow_sma):
        if f is None or s is None:
            signals.append(0)
        elif f > s:
            signals.append(1)
        elif f < s:
            signals.append(-1)
        else:
            signals.append(0)
    return signals


def compute_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """
    RSI (Wilder's method), 14-period by default.

    Returns None for the first ``period`` bars. Values are 0-100.
    The first RSI uses a simple average of gains/losses over the
    first ``period`` changes; later values use Wilder smoothing.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    # First average: over the first `period` changes.
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        rs = g / l
        return 100.0 - (100.0 / (1.0 + rs))

    # closes[period] is the first bar with a full window.
    out[period] = _rsi(avg_gain, avg_loss)

    for i in range(period + 1, len(closes)):
        # gains/losses index: change i corresponds to gains[i-1]
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def rsi_strategy(
    closes: list[float],
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> list[int]:
    """
    RSI strategy, 14-period by default.

    Desired position (stateful, hold until the opposite extreme):
      1  when RSI < 30 (oversold -> buy), held until RSI > 70
     -1  when RSI > 70 (overbought -> sell / short), held until RSI < 30
      0  during warm-up, and in the neutral zone before any extreme
         has been seen

    This is a contrarian mean-reversion reading. Unlike a one-bar
    signal, the position is held after an extreme is hit, so the
    backtester does not churn in and out on every neutral bar.
    """
    if not (0 <= oversold < overbought <= 100):
        raise ValueError("need 0 <= oversold < overbought <= 100")
    rsi_values = compute_rsi(closes, period)
    signals: list[int] = []
    current = 0
    for r in rsi_values:
        if r is None:
            signals.append(0)
        elif r < oversold:
            current = 1
            signals.append(1)
        elif r > overbought:
            current = -1
            signals.append(-1)
        else:
            signals.append(current)
    return signals
