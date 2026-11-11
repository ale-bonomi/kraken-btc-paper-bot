"""End-to-end paper test runner for Kraken BTC.

Paper only, no real orders, no API keys.
Usage:
  python3 run_paper_test.py
  python3 run_paper_test.py --trade 500   # paper-buy $500 at live price
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from market_data.kraken_public import get_ticker
from strategy.strategies import sma_crossover, rsi_strategy, compute_rsi
from strategy.backtester import backtest
from paper.paper_engine import PaperEngine


def load_closes():
    closes = []
    with open(Path(__file__).parent / "data" / "ohlc_XBTUSD_60.csv") as f:
        for row in csv.DictReader(f):
            closes.append(float(row["close"]))
    return closes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade", type=float, default=0, help="Paper-buy USD amount at live price")
    args = parser.parse_args()

    ticker = get_ticker()
    price = float(ticker["last"])
    print(f"Live XBTUSD last: ${price:,.2f}  bid ${ticker['bid']} ask ${ticker['ask']}")

    closes = load_closes()
    print(f"OHLC closes loaded: {len(closes)}  first ${closes[0]:,.2f}  last ${closes[-1]:,.2f}")

    sma_sig = sma_crossover(closes, 10, 30)
    rsi_sig = rsi_strategy(closes)
    rsi_vals = compute_rsi(closes)
    print(f"Latest SMA(10/30) signal: {sma_sig[-1]}  RSI signal: {rsi_sig[-1]}  RSI value: {rsi_vals[-1]:.2f}")

    for name, sig in [("sma", sma_sig), ("rsi", rsi_sig)]:
        result = backtest(closes, sig)
        print(f"Backtest {name}: final ${result['final_value']:,.2f}  return {result['total_return_pct']:.2f}%  max DD {result['max_drawdown_pct']:.2f}%  trades {result['number_of_trades']}  win rate {result['win_rate']*100:.1f}%")

    eng = PaperEngine()
    print("Paper state before:", eng.get_state(price=price))
    if args.trade:
        print("Paper buy result:", eng.buy(price, args.trade))
        print("Paper state after:", eng.get_state(price=price))

    print("Note: Reddit direct JSON was blocked (HTTP 403) from this VM in the last run. Sentiment file is at data/reddit_sentiment.json with errors recorded. Use browser/social search as fallback for live sentiment ideas.")


if __name__ == "__main__":
    main()
