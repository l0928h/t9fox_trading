from __future__ import annotations

import datetime
import sys
import threading
from decimal import Decimal

from t9fox.broker.sinopac import SinopacBroker

# Taiwan market hours
_MARKET_OPEN  = datetime.time(9,  0)
_MARKET_CLOSE = datetime.time(13, 30)


class BreakoutDayTrader:
    """
    Intraday breakout strategy (single stock, simulation-safe).

    Entry : first tick whose deal price > n_day_high  → buy `lots` at limit
    Exit  : when clock >= sell_time                   → sell all at limit
    Guard : at most one round-trip per calendar day
    """

    def __init__(
        self,
        symbol: str,
        n_day_high: float,
        lots: int = 1,
        sell_time: str = "13:20",
    ):
        self.symbol      = symbol
        self.n_day_high  = n_day_high
        self.lots        = lots
        self.sell_time   = datetime.time(*map(int, sell_time.split(":")))

        self._lock           = threading.Lock()
        self._bought_today   = False
        self._position_lots  = 0
        self._buy_price      = 0.0
        self._sold           = False
        self._stop           = threading.Event()

    # ── public ─────────────────────────────────────────────────────────

    @classmethod
    def from_broker(cls, symbol: str, broker: SinopacBroker, lots: int = 1,
                    lookback: int = 20, sell_time: str = "13:20") -> "BreakoutDayTrader":
        """Build a trader by calculating n_day_high directly from Sinopac kbars."""
        from t9fox.strategy.breakout import calc_n_day_high_from_broker
        high = calc_n_day_high_from_broker(broker, symbol, lookback)
        return cls(symbol=symbol, n_day_high=high, lots=lots, sell_time=sell_time)

    def run(self, broker: SinopacBroker) -> None:
        """Block until market close (or Ctrl-C)."""
        _log(self.symbol, f"20d-high = {self.n_day_high:.2f}  sell at {self.sell_time}")
        _log(self.symbol, "Subscribing to ticks …")

        import shioaji.constant as c  # type: ignore[import]

        contract = broker.api.Contracts.Stocks[self.symbol]

        # register tick callback
        @broker.api.on_tick_stk_v1()
        def _on_tick(exchange, tick):
            if tick.code != self.symbol:
                return
            self._handle_tick(broker, tick)

        broker.api.quote.subscribe(
            contract,
            quote_type=c.QuoteType.Tick,
            version=c.QuoteVersion.v1,
        )
        _log(self.symbol, "Listening … press Ctrl-C to stop")

        try:
            while not self._stop.is_set():
                now = datetime.datetime.now().time()
                if now >= _MARKET_CLOSE:
                    _log(self.symbol, "Market closed — stopping.")
                    break
                self._stop.wait(timeout=5)
        except KeyboardInterrupt:
            print("\n[monitor] Interrupted by user.")
        finally:
            try:
                broker.api.quote.unsubscribe(
                    contract,
                    quote_type=c.QuoteType.Tick,
                    version=c.QuoteVersion.v1,
                )
            except Exception:
                pass

    # ── internal ───────────────────────────────────────────────────────

    def _handle_tick(self, broker: SinopacBroker, tick) -> None:
        price = float(tick.close)          # last deal price
        now   = datetime.datetime.now().time()

        with self._lock:
            # ── forced sell at sell_time ──────────────────────────────
            if now >= self.sell_time and self._position_lots > 0 and not self._sold:
                self._do_sell(broker, price)
                return

            # ── buy on breakout (once per day) ────────────────────────
            if (
                not self._bought_today
                and now >= _MARKET_OPEN
                and price > self.n_day_high
            ):
                self._do_buy(broker, price)

    def _do_buy(self, broker: SinopacBroker, price: float) -> None:
        _log(
            self.symbol,
            f"BREAKOUT  last={price:.2f} > 20d-high={self.n_day_high:.2f}"
            f"  → BUY {self.lots} lot @ {price:.2f}",
        )
        try:
            result = broker.place_stock_order(self.symbol, "Buy", self.lots, price)
            self._bought_today  = True
            self._position_lots = self.lots
            self._buy_price     = price
            _log(self.symbol, f"Order sent  id={result.order_id}  status={result.status}")
            _save_trade(self.symbol, "Buy", self.lots, price,
                        result.order_id, result.status, broker.creds.simulation)
        except Exception as e:
            _log(self.symbol, f"BUY ERROR: {e}", error=True)

    def _do_sell(self, broker: SinopacBroker, price: float) -> None:
        pnl = (price - self._buy_price) * self._position_lots * 1000
        _log(
            self.symbol,
            f"SELL TIME  last={price:.2f}  bought={self._buy_price:.2f}"
            f"  est-PnL={pnl:+,.0f} TWD"
            f"  → SELL {self._position_lots} lot @ {price:.2f}",
        )
        try:
            result = broker.place_stock_order(self.symbol, "Sell", self._position_lots, price)
            _save_trade(self.symbol, "Sell", self._position_lots, price,
                        result.order_id, result.status, broker.creds.simulation)
            self._position_lots = 0
            self._sold          = True
            _log(self.symbol, f"Order sent  id={result.order_id}  status={result.status}")
            self._stop.set()   # done for today
        except Exception as e:
            _log(self.symbol, f"SELL ERROR: {e}", error=True)


# ── helpers ────────────────────────────────────────────────────────────

def _log(symbol: str, msg: str, *, error: bool = False) -> None:
    ts  = datetime.datetime.now().strftime("%H:%M:%S")
    out = sys.stderr if error else sys.stdout
    print(f"[{ts}][{symbol}] {msg}", file=out)


def _save_trade(
    symbol: str, action: str, lots: int, price: float,
    order_id: str, order_status: str, simulation: bool,
) -> None:
    try:
        from t9fox.db.store import insert_trade
        insert_trade(
            date=datetime.date.today().isoformat(),
            symbol=symbol, action=action, lots=lots, price=price,
            order_id=order_id, order_status=order_status, simulation=simulation,
        )
    except Exception as e:
        _log(symbol, f"DB write failed: {e}", error=True)
