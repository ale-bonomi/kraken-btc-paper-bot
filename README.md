# Kraken BTC Paper Trading Bot

Paper-only Bitcoin trading system for Kraken, built by orchestrating multiple AI agents.

No real orders. No API keys. No live trading. This is a learning and portfolio project for building, backtesting, and paper-trading a BTC strategy with AI-assisted development.

Built by Alessandro — AI creative builder and strategist focused on product design, creative technology, and agentic AI workflows.

## What it does

- Fetches live BTC/USD price data from Kraken public API, no authentication needed
- Backtests simple, transparent strategies on hourly OHLC data
- Paper-trades with a $10,000 virtual portfolio, fees, and risk limits
- Scans Reddit for sentiment and strategy ideas to test next

## How it was built

This was not written as one big script. It was built as a multi-agent system:

A coordinator split the work into four parallel builds, then integrated them:

1. **Market data** — Kraken public REST: ticker, OHLC, recent trades
2. **Strategy lab** — SMA crossover and RSI, plus a backtester
3. **Paper trading engine** — virtual portfolio, fees, risk limits, kill switch
4. **Sentiment scanner** — Reddit public JSON, keyword sentiment, strategy-idea extraction

Each agent owned its own folder, tested its own module, and reported back. The coordinator verified the pieces together and ran the end-to-end paper test.

That orchestration is the point: defining the problem, designing the system, directing agents to build it, evaluating the results, and iterating.

## Project layout

```
trading-bot/
  market_data/
    kraken_public.py      # get_ticker(), get_ohlc(), get_recent_trades(), save_ohlc()
    run_kraken_test.py
  strategy/
    strategies.py         # sma_crossover(), rsi_strategy(), compute_rsi()
    backtester.py         # backtest() - $10k, 0.26% fee, equity curve, drawdown
  paper/
    paper_engine.py       # PaperEngine - cash, BTC, fees, limits, STOP file
  sentiment/
    reddit_scanner.py     # fetch_subreddit(), score_sentiment(), extract_strategy_ideas()
  data/
    ohlc_XBTUSD_60.csv    # sample: 721 hourly candles, 2026-10-12 to 2026-11-11
  run_paper_test.py       # end-to-end runner: live price + signals + backtest + paper state
  README.md
  LICENSE
  requirements.txt        # stdlib only
```

## Quickstart

Python 3.11+ required. No dependencies to install.

```bash
cd trading-bot
python3 run_paper_test.py
```

To do a paper buy in the test run:

```bash
python3 run_paper_test.py --trade 500
```

To run just the backtester:

```bash
python3 -m strategy.backtester
python3 -m strategy.backtester --strategy rsi
python3 -m strategy.backtester --strategy sma
```

## Live check and honest results

Sample run, 2026-11-10, 721 hourly XBTUSD candles:

- Live price at test time: $73,615.30
- First close in sample: $83,573.70, last close: $73,662.20
- Buy-and-hold over the same window: -11.86%
- Latest signals disagreed: SMA 10/30 = -1, RSI 14 = 1, RSI value 45.37

Backtest, $10,000 start, 0.26% fee per trade:

| Strategy | Final value | Return | Max drawdown | Trades | Win rate |
| --- | --- | --- | --- | --- | --- |
| SMA crossover 10/30 | $6,964.51 | -30.35% | 30.97% | 33 | 25.0% |
| RSI 14 | $9,965.69 | -0.34% | 7.24% | 5 | 75.0% |

Neither strategy was profitable on this 30-day hourly window. The SMA whipsawed in a choppy downtrend. The RSI barely traded and stayed essentially flat. Its 75% win rate is on only 4 completed trades, so it is not statistically meaningful.

That is reported honestly on purpose. A backtest that loses is still useful if it tells you what not to ship.

Paper trade in the end-to-end test:

- $500 paper buy at $73,628.50, fee $1.30, received 0.00677319 BTC
- Cash $9,500.00, portfolio $9,998.70

## Paper engine rules

- Starting cash: $10,000 virtual, BTC starts at 0
- Fee: 0.26% per trade
- Max 10% of portfolio value per single trade
- Max daily loss 5% vs UTC day-start value — blocks new buys, sells still allowed
- Kill switch: if `data/STOP` exists, all buys and sells are blocked
- Every executed paper trade appends to `data/paper_trades.csv` and state saves to `data/paper_state.json`

## Reddit sentiment — current blocker

The scanner is built and its scoring logic is verified on sample posts, 1.0 for bullish text, -1.0 for bearish.

Live Reddit direct JSON was blocked from the test VM with HTTP 403 on r/Bitcoin, r/CryptoCurrency, and r/BitcoinMarkets. The output file records 0 posts and a 0.0 score, which means no data, not neutral sentiment.

Next options for live sentiment: run the scanner from a residential IP, use a browser task, or use another Reddit source. The code is ready for any of those — the fetch layer is isolated in `sentiment/reddit_scanner.py`.

## Ideas to test next

From research while building this:

- MACD + RSI filter: adding an RSI > 50 filter to a MACD crossover cut trades and improved return and drawdown in a 6-year BTC backtest.
- RSI + SMA trend filter: only take RSI buys when price is above a moving average, to avoid buying oversold signals in a downtrend.

Both are good candidates for the strategy lab. They are not in this repo yet — that is the next iteration.

## Safety

- Paper trading only. This code never places a real order.
- No Kraken private API, no API keys, no secrets in this repo.
- Kraken private trading would need HMAC-SHA512 signing with a private key. That is intentionally out of scope for this version.
- If live trading is added later, it should use a separate trade-only key with no withdrawal permission, IP restrictions, tiny limits, and the same kill switch.
- This is not financial advice. Crypto trading is risky. Backtest results do not predict future returns.

## For other people to use

1. Clone the repo
2. Run `python3 run_paper_test.py` — paper mode, public data only, no setup
3. To test a new strategy, add it to `strategy/strategies.py` and run the backtester
4. To add live trading later, keep it behind the paper engine interface and behind the same risk limits

## License

MIT — see `LICENSE`.
