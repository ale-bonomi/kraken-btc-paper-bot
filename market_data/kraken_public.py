"""
Kraken PUBLIC market data module — BTC paper-trading test.

Read-only. Public REST API only, no auth, no keys, no private endpoints.
Pair: XBTUSD (BTC/USD)

Endpoints used:
  - https://api.kraken.com/0/public/Ticker?pair=XBTUSD
  - https://api.kraken.com/0/public/OHLC
  - https://api.kraken.com/0/public/Trades

Uses only the Python standard library (urllib) so no extra installs are needed.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://api.kraken.com/0/public"
PAIR = "XBTUSD"
DEFAULT_TIMEOUT = 10  # seconds
ALLOWED_INTERVALS = (1, 5, 15, 30, 60, 240, 1440, 10080, 21600)

# Kraken public-data rate limits are generous but not unlimited; be polite.
# A small pause between calls in this module is handled by callers if needed.
DATA_DIR = Path.home() / "workspace" / "trading-bot" / "data"


class KrakenAPIError(Exception):
    """Raised when Kraken returns an error, or the request fails."""


def _get(endpoint: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """GET a Kraken public endpoint and return the parsed ``result`` dict.

    Raises KrakenAPIError on HTTP errors, timeouts, bad JSON, or a
    non-empty Kraken ``error`` field.
    """
    url = f"{BASE_URL}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "muse-paper-trading-test/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 429 = rate limited. Read a Retry-After hint if present, then fail
        # loudly so the caller can back off instead of silently returning
        # stale/empty data.
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        hint = f" Retry-After: {retry_after}." if retry_after else ""
        raise KrakenAPIError(
            f"Kraken HTTP {exc.code} on {endpoint}.{hint} Body: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise KrakenAPIError(f"Network error calling {endpoint}: {exc}") from exc
    except TimeoutError as exc:
        raise KrakenAPIError(f"Timeout ({timeout}s) calling {endpoint}") from exc
    except json.JSONDecodeError as exc:
        raise KrakenAPIError(f"Invalid JSON from {endpoint}: {exc}") from exc

    errors = payload.get("error") or []
    if errors:
        raise KrakenAPIError(f"Kraken API error on {endpoint}: {errors}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise KrakenAPIError(f"Unexpected Kraken response shape on {endpoint}: {payload!r:.500}")
    return result


def _pair_data(result: dict) -> dict | list:
    """Return the pair-specific payload, tolerating Kraken's key variants.

    Ticker/OHLC/Trades return the pair under 'XXBTZUSD' (sometimes
    'XBTUSD'). Pick the first key that is not 'last'.
    """
    for key, value in result.items():
        if key != "last":
            return value
    raise KrakenAPIError(f"No pair data in Kraken result: {result!r:.500}")


def get_ticker(pair: str = PAIR) -> dict:
    """Fetch the current XBTUSD ticker.

    Returns a clean dict, e.g.:
        {
          "pair": "XBTUSD",
          "ask": 102345.6, "bid": 102340.1, "last": 102342.0,
          "open": 101900.0, "high_24h": 103000.0, "low_24h": 101500.0,
          "volume_24h": 1234.5, "vwap_24h": 102100.0,
          "trades_24h": 45678, "fetched_at": "2026-11-10T...Z",
        }
    All prices are floats in USD, volume in BTC.
    """
    result = _get("Ticker", {"pair": pair})
    raw = _pair_data(result)
    # Kraken ticker fields: a=ask, b=bid, c=last, v=volume, p=vwap,
    # t=trades, l=low, h=high, o=open. Each is [today, last 24h] where applicable.
    return {
        "pair": pair,
        "ask": float(raw["a"][0]),
        "bid": float(raw["b"][0]),
        "last": float(raw["c"][0]),
        "open": float(raw["o"]),
        "high_24h": float(raw["h"][1]),
        "low_24h": float(raw["l"][1]),
        "volume_24h": float(raw["v"][1]),
        "vwap_24h": float(raw["p"][1]),
        "trades_24h": int(raw["t"][1]),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def get_ohlc(interval: int = 60, since: int | None = None, pair: str = PAIR) -> list[dict]:
    """Fetch OHLC candles for XBTUSD.

    interval: candle size in minutes. Kraken allows
        1, 5, 15, 30, 60, 240, 1440, 10080, 21600. Default 60 (1 hour).
    since: optional Unix timestamp (seconds) to start from; Kraken returns
        candles after this time and a ``last`` id for paging.

    Returns a list of dicts sorted by time, e.g.:
        [{"time": 1762819200, "datetime_utc": "...", "open": ..., "high": ...,
          "low": ..., "close": ..., "vwap": ..., "volume": ..., "count": 123}, ...]
    The current (incomplete) candle is the last element, as Kraken returns it.
    """
    if interval not in ALLOWED_INTERVALS:
        raise ValueError(f"interval must be one of {ALLOWED_INTERVALS}, got {interval}")
    params: dict[str, str] = {"pair": pair, "interval": str(interval)}
    if since is not None:
        params["since"] = str(int(since))

    result = _get("OHLC", params)
    raw = _pair_data(result)
    candles = []
    for row in raw:
        # [time, open, high, low, close, vwap, volume, count]
        ts = int(row[0])
        candles.append(
            {
                "time": ts,
                "datetime_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "vwap": float(row[5]),
                "volume": float(row[6]),
                "count": int(row[7]),
            }
        )
    candles.sort(key=lambda c: c["time"])
    return candles


def get_recent_trades(pair: str = PAIR) -> list[dict]:
    """Fetch recent XBTUSD trades (Kraken returns the last ~1000).

    Returns a list of dicts sorted by time, e.g.:
        [{"price": ..., "volume": ..., "time": ..., "datetime_utc": "...",
          "side": "b"|"s", "order_type": "m"|"l"}, ...]
    side: 'b' = buy (taker bought), 's' = sell. order_type: 'm' = market,
    'l' = limit.
    """
    result = _get("Trades", {"pair": pair})
    raw = _pair_data(result)
    trades = []
    for row in raw:
        # [price, volume, time, buy/sell, market/limit, miscellaneous]
        ts = float(row[2])
        trades.append(
            {
                "price": float(row[0]),
                "volume": float(row[1]),
                "time": ts,
                "datetime_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "side": row[3],
                "order_type": row[4],
            }
        )
    trades.sort(key=lambda t: t["time"])
    return trades


def save_ohlc(
    candles: list[dict],
    filename: str | None = None,
    fmt: str = "csv",
    data_dir: str | Path = DATA_DIR,
) -> Path:
    """Save OHLC candles to CSV or JSON under ~/workspace/trading-bot/data/.

    fmt: "csv" (default) or "json".
    filename: optional name; defaults to ``ohlc_XBTUSD_60.csv`` / ``.json``.
    Returns the Path written.
    """
    fmt = fmt.lower()
    if fmt not in ("csv", "json"):
        raise ValueError(f"fmt must be 'csv' or 'json', got {fmt!r}")
    if not candles:
        raise ValueError("No candles to save")

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = f"ohlc_{PAIR}_60.{fmt}"
    path = data_dir / filename

    if fmt == "csv":
        fieldnames = ["time", "datetime_utc", "open", "high", "low", "close", "vwap", "volume", "count"]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(candles)
    else:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(candles, fh, indent=2)

    return path


if __name__ == "__main__":
    ticker = get_ticker()
    print(f"XBTUSD last: ${ticker['last']:,.2f}  (bid ${ticker['bid']:,.2f} / ask ${ticker['ask']:,.2f})")
    print(f"24h range: ${ticker['low_24h']:,.2f} - ${ticker['high_24h']:,.2f}, volume {ticker['volume_24h']:,.2f} BTC")
    candles = get_ohlc(interval=60)
    print(f"Fetched {len(candles)} hourly candles, latest close ${candles[-1]['close']:,.2f}")
    trades = get_recent_trades()
    print(f"Fetched {len(trades)} recent trades, latest ${trades[-1]['price']:,.2f}")
    saved = save_ohlc(candles)
    print(f"Saved OHLC to {saved}")
