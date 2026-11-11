"""
Paper trading engine for BTC test — paper only.

No real orders, no Kraken private API, no API keys.
Simulates fills at the given price.

Rules:
- Starting cash: $10,000 virtual, BTC position starts at 0.
- Portfolio value = cash + BTC * price.
- Fee: 0.26% per trade.
  - Buy: usd_amount is the total cash spent. Fee is taken from it,
    BTC received = (usd_amount - fee) / price. Cash decreases by usd_amount.
  - Sell: proceeds = btc_amount * price. Fee is taken from proceeds,
    cash received = proceeds - fee. BTC decreases by btc_amount.
- Risk limits:
  - Max 10% of portfolio value per single trade (buy USD value or sell USD value).
  - Max daily loss 5%: if portfolio value has fallen >=5% from the
    start-of-day value, new buys are blocked. Sells are still allowed.
  - Kill switch: if ~/workspace/trading-bot/data/STOP exists, all
    trading (buys and sells) is blocked.
- Logs every executed trade to data/paper_trades.csv and persists
  state to data/paper_state.json after every trade.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path


FEE_RATE = 0.0026  # 0.26%
STARTING_CASH = 10_000.0
MAX_TRADE_FRACTION = 0.10  # 10% of portfolio per trade
MAX_DAILY_LOSS_FRACTION = 0.05  # 5% daily loss blocks buys

DEFAULT_ROOT = Path.home() / "workspace" / "trading-bot"
DEFAULT_DATA_DIR = DEFAULT_ROOT / "data"

TRADE_CSV_COLUMNS = [
    "timestamp_utc",
    "side",
    "price",
    "usd_value",
    "btc_amount",
    "fee_usd",
    "cash_after",
    "btc_after",
    "portfolio_value_after",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class PaperEngine:
    """Virtual BTC paper-trading engine."""

    def __init__(
        self,
        starting_cash: float = STARTING_CASH,
        data_dir: str | Path | None = None,
        fresh: bool = False,
    ):
        self.starting_cash = float(starting_cash)
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.trades_path = self.data_dir / "paper_trades.csv"
        self.state_path = self.data_dir / "paper_state.json"
        self.stop_path = self.data_dir / "STOP"

        self.cash: float = self.starting_cash
        self.btc: float = 0.0
        self.last_price: float | None = None
        self.day: str = _utc_today()
        self.day_start_value: float = self.starting_cash
        self.total_trades: int = 0

        if not fresh:
            self._load_state()

        # Ensure state file exists so other components can read it.
        self._save_state()

    # -- state persistence -------------------------------------------------

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.cash = float(data.get("cash", self.starting_cash))
            self.btc = float(data.get("btc", 0.0))
            self.last_price = data.get("last_price")
            if self.last_price is not None:
                self.last_price = float(self.last_price)
            self.day = str(data.get("day", _utc_today()))
            self.day_start_value = float(
                data.get("day_start_value", self.starting_cash)
            )
            self.total_trades = int(data.get("total_trades", 0))
        except (OSError, ValueError, TypeError):
            # Corrupt / unreadable state: fall back to a fresh start.
            # Do not crash the engine on a bad state file.
            self.cash = self.starting_cash
            self.btc = 0.0
            self.last_price = None
            self.day = _utc_today()
            self.day_start_value = self.starting_cash
            self.total_trades = 0

    def _save_state(self) -> None:
        state = self.get_state(price=self.last_price, save=False)
        # get_state() already includes the fields we persist; write it.
        tmp_path = self.state_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, self.state_path)

    def _log_trade(
        self,
        side: str,
        price: float,
        usd_value: float,
        btc_amount: float,
        fee_usd: float,
        portfolio_value: float,
    ) -> None:
        file_exists = self.trades_path.exists()
        with open(self.trades_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_CSV_COLUMNS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(
                {
                    "timestamp_utc": _utc_now_iso(),
                    "side": side,
                    "price": f"{price:.2f}",
                    "usd_value": f"{usd_value:.2f}",
                    "btc_amount": f"{btc_amount:.8f}",
                    "fee_usd": f"{fee_usd:.2f}",
                    "cash_after": f"{self.cash:.2f}",
                    "btc_after": f"{self.btc:.8f}",
                    "portfolio_value_after": f"{portfolio_value:.2f}",
                }
            )

    # -- helpers -----------------------------------------------------------

    def portfolio_value(self, price: float | None = None) -> float:
        p = price if price is not None else self.last_price
        if p is None:
            return self.cash
        return self.cash + self.btc * float(p)

    def _maybe_roll_day(self, price: float | None = None) -> None:
        today = _utc_today()
        if today != self.day:
            # New UTC day: day-start value is the current portfolio value.
            self.day = today
            self.day_start_value = self.portfolio_value(price)

    def _daily_loss_pct(self, price: float | None = None) -> float:
        """Negative number = loss vs day start. 0 if day_start is 0."""
        if self.day_start_value <= 0:
            return 0.0
        pv = self.portfolio_value(price)
        return (pv - self.day_start_value) / self.day_start_value

    def kill_switch_active(self) -> bool:
        return self.stop_path.exists()

    # -- public API --------------------------------------------------------

    def get_state(self, price: float | None = None, save: bool = False) -> dict:
        if price is not None:
            self.last_price = float(price)
        # Roll day before computing daily P&L so a new day resets the limit.
        self._maybe_roll_day(price=self.last_price)
        pv = self.portfolio_value(self.last_price)
        daily_pct = self._daily_loss_pct(self.last_price)
        state = {
            "cash": round(self.cash, 2),
            "btc": round(self.btc, 8),
            "last_price": self.last_price,
            "portfolio_value": round(pv, 2),
            "day": self.day,
            "day_start_value": round(self.day_start_value, 2),
            "daily_pnl_pct": round(daily_pct, 6),
            "daily_loss_blocked": daily_pct <= -MAX_DAILY_LOSS_FRACTION,
            "kill_switch_active": self.kill_switch_active(),
            "total_trades": self.total_trades,
            "starting_cash": self.starting_cash,
            "fee_rate": FEE_RATE,
            "max_trade_fraction": MAX_TRADE_FRACTION,
            "max_daily_loss_fraction": MAX_DAILY_LOSS_FRACTION,
        }
        if save:
            # Avoid recursion: write directly instead of calling _save_state().
            tmp_path = self.state_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, self.state_path)
        return state

    def buy(self, price: float, usd_amount: float) -> dict:
        """Buy BTC with usd_amount of cash (fee included in that amount)."""
        if price is None or float(price) <= 0:
            return {"success": False, "reason": "price must be > 0"}
        if usd_amount is None or float(usd_amount) <= 0:
            return {"success": False, "reason": "usd_amount must be > 0"}
        price = float(price)
        usd_amount = float(usd_amount)

        if self.kill_switch_active():
            return {"success": False, "reason": "kill switch active (STOP file present)"}

        self.last_price = price
        self._maybe_roll_day(price=price)

        pv = self.portfolio_value(price)
        max_trade = MAX_TRADE_FRACTION * pv
        if usd_amount > max_trade:
            return {
                "success": False,
                "reason": f"exceeds 10% max trade size: ${usd_amount:.2f} > ${max_trade:.2f} (10% of ${pv:.2f})",
                "portfolio_value": round(pv, 2),
                "max_trade_usd": round(max_trade, 2),
            }

        if self._daily_loss_pct(price) <= -MAX_DAILY_LOSS_FRACTION:
            return {
                "success": False,
                "reason": "daily loss limit hit (>=5% down from day start); buys blocked",
                "portfolio_value": round(pv, 2),
                "daily_pnl_pct": round(self._daily_loss_pct(price), 6),
            }

        if usd_amount > self.cash:
            return {
                "success": False,
                "reason": f"insufficient cash: need ${usd_amount:.2f}, have ${self.cash:.2f}",
                "portfolio_value": round(pv, 2),
            }

        fee = usd_amount * FEE_RATE
        btc_bought = (usd_amount - fee) / price

        self.cash -= usd_amount
        self.btc += btc_bought
        self.total_trades += 1
        pv_after = self.portfolio_value(price)

        self._log_trade(
            side="buy",
            price=price,
            usd_value=usd_amount,
            btc_amount=btc_bought,
            fee_usd=fee,
            portfolio_value=pv_after,
        )
        self._save_state()

        return {
            "success": True,
            "side": "buy",
            "price": price,
            "usd_amount": round(usd_amount, 2),
            "btc_amount": round(btc_bought, 8),
            "fee_usd": round(fee, 2),
            "cash": round(self.cash, 2),
            "btc": round(self.btc, 8),
            "portfolio_value": round(pv_after, 2),
        }

    def sell(self, price: float, btc_amount: float) -> dict:
        """Sell btc_amount of BTC. Fee is taken from the proceeds."""
        if price is None or float(price) <= 0:
            return {"success": False, "reason": "price must be > 0"}
        if btc_amount is None or float(btc_amount) <= 0:
            return {"success": False, "reason": "btc_amount must be > 0"}
        price = float(price)
        btc_amount = float(btc_amount)

        if self.kill_switch_active():
            return {"success": False, "reason": "kill switch active (STOP file present)"}

        self.last_price = price
        self._maybe_roll_day(price=price)

        pv = self.portfolio_value(price)
        sell_value = btc_amount * price
        max_trade = MAX_TRADE_FRACTION * pv
        if sell_value > max_trade:
            return {
                "success": False,
                "reason": f"exceeds 10% max trade size: ${sell_value:.2f} > ${max_trade:.2f} (10% of ${pv:.2f})",
                "portfolio_value": round(pv, 2),
                "max_trade_usd": round(max_trade, 2),
            }

        if btc_amount > self.btc:
            return {
                "success": False,
                "reason": f"insufficient BTC: need {btc_amount:.8f}, have {self.btc:.8f}",
                "portfolio_value": round(pv, 2),
            }

        proceeds = sell_value
        fee = proceeds * FEE_RATE
        cash_received = proceeds - fee

        self.btc -= btc_amount
        self.cash += cash_received
        self.total_trades += 1
        pv_after = self.portfolio_value(price)

        self._log_trade(
            side="sell",
            price=price,
            usd_value=proceeds,
            btc_amount=btc_amount,
            fee_usd=fee,
            portfolio_value=pv_after,
        )
        self._save_state()

        return {
            "success": True,
            "side": "sell",
            "price": price,
            "btc_amount": round(btc_amount, 8),
            "usd_value": round(proceeds, 2),
            "fee_usd": round(fee, 2),
            "cash": round(self.cash, 2),
            "btc": round(self.btc, 8),
            "portfolio_value": round(pv_after, 2),
        }
