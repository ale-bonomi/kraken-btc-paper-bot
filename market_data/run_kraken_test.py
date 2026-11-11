"""Small live test / run script for kraken_public.py.

Run from the project root or anywhere:
    python3 ~/workspace/trading-bot/market_data/run_kraken_test.py

Public data only, no auth. Fetches ticker, OHLC (60m) and recent trades,
checks the price looks sane, and saves the OHLC to data/.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kraken_public import (
    KrakenAPIError,
    get_ohlc,
    get_recent_trades,
    get_ticker,
    save_ohlc,
)


def main() -> int:
    try:
        ticker = get_ticker()
    except KrakenAPIError as exc:
        print(f"TICKER FAILED: {exc}")
        return 1

    last = ticker["last"]
    print(f"Ticker XBTUSD: last=${last:,.2f} bid=${ticker['bid']:,.2f} ask=${ticker['ask']:,.2f}")
    print(f"  24h low/high: ${ticker['low_24h']:,.2f} / ${ticker['high_24h']:,.2f}")
    print(f"  24h volume: {ticker['volume_24h']:,.2f} BTC, VWAP ${ticker['vwap_24h']:,.2f}")

    # Sanity check: BTC should be well above $1,000 and below $1,000,000,
    # and last should sit inside the 24h range (allowing a tiny tolerance
    # for a fast move between fields).
    sane = 1_000 < last < 1_000_000 and ticker["low_24h"] <= last <= ticker["high_24h"] * 1.01
    print(f"  Price sanity check: {'PASS' if sane else 'FAIL'}")
    if not sane:
        print("  WARNING: price outside expected range — check the raw response.")
        return 1

    time.sleep(1)  # be polite to the public API
    try:
        candles = get_ohlc(interval=60)
    except KrakenAPIError as exc:
        print(f"OHLC FAILED: {exc}")
        return 1
    print(f"OHLC: {len(candles)} hourly candles, first {candles[0]['datetime_utc']}, "
          f"latest close=${candles[-1]['close']:,.2f}")

    csv_path = save_ohlc(candles, fmt="csv")
    json_path = save_ohlc(candles, fmt="json")
    print(f"Saved OHLC CSV: {csv_path}")
    print(f"Saved OHLC JSON: {json_path}")

    time.sleep(1)
    try:
        trades = get_recent_trades()
    except KrakenAPIError as exc:
        print(f"TRADES FAILED: {exc}")
        return 1
    print(f"Trades: {len(trades)} recent trades, latest ${trades[-1]['price']:,.2f} "
          f"({trades[-1]['side']}, {trades[-1]['volume']} BTC)")

    print("All live checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
