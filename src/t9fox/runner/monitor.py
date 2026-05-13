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


class MaBreakoutDayTrader:
    """
    Strategy 3 — MA 突破（單支股票，simulation-safe）

    Pre-conditions (checked before open, all must pass):
        ① MA60 < MA20           多頭排列
        ② 昨收 < MA20           突破前在線下

    Entry (first qualifying tick at/after 09:00):
        ③ tick > MA20           開盤突破
        ④ gap < 3%              跳空不超過 3%（首 tick 相對昨收計算）

    Exit:
        tick <= close_prev × (1 − stop_loss_pct/100)   → stop loss (default: 回昨收)
        tick >= buy_price  × (1 + take_profit_pct/100) → take profit
        clock >= sell_time (default 13:20)              → force-sell
    """

    def __init__(
        self,
        symbol: str,
        ma_fast: float,
        ma_slow: float,
        close_prev: float,
        vol_ratio: float,
        take_profit_pct: float = 3.0,
        stop_loss_pct: float = 9.0,
        lots: int = 1,
        sell_time: str = "13:20",
    ):
        self.symbol          = symbol
        self.ma_fast         = ma_fast
        self.ma_slow         = ma_slow
        self.close_prev      = close_prev
        self.vol_ratio       = vol_ratio
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct   = stop_loss_pct
        self.lots            = lots
        self.sell_time       = datetime.time(*map(int, sell_time.split(":")))

        self._lock                      = threading.Lock()
        self._bought_today              = False
        self._pending_buy_order_id      : str | None = None
        self._pending_buy_filled_shares : int   = 0      # 累計已成交股數
        self._pending_buy_cost          : float = 0.0    # 累計成本（計算均價用）
        self._position_lots             = 0
        self._buy_price                 = 0.0
        self._sold                      = False
        self._pending_sell_order_id     : str | None = None
        self._stop                  = threading.Event()
        self._open_px               = None   # 首 tick 作為開盤代理（跳空計算用）
        self._traded_today          : threading.Event | None = None  # 共用旗標

        # 盤前可確認的條件
        self._condition_ok = (
            ma_slow < ma_fast
            and close_prev < ma_fast
        )

    # ── public ─────────────────────────────────────────────────────────

    @classmethod
    def from_broker(
        cls,
        symbol: str,
        broker: SinopacBroker,
        take_profit_pct: float = 3.0,
        stop_loss_pct: float = 9.0,
        lots: int = 1,
        sell_time: str = "13:20",
    ) -> "MaBreakoutDayTrader":
        from t9fox.strategy.ma_breakout import MaBreakoutParams, calc_ma_breakout_signal
        sig = calc_ma_breakout_signal(broker, symbol, MaBreakoutParams())
        return cls(
            symbol=symbol,
            ma_fast=sig.ma_fast,
            ma_slow=sig.ma_slow,
            close_prev=sig.close_last,
            vol_ratio=sig.vol_ratio,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            lots=lots,
            sell_time=sell_time,
        )

    def run(self, broker: SinopacBroker) -> None:
        """Block until market close (or Ctrl-C)."""
        cond_str = "OK" if self._condition_ok else "SKIP"
        _log(self.symbol,
             f"MA20={self.ma_fast:.2f}  MA60={self.ma_slow:.2f}  "
             f"昨收={self.close_prev:.2f}  量比={self.vol_ratio:.2f}x  "
             f"條件={cond_str}  TP={self.take_profit_pct:.1f}%  "
             f"lots={self.lots}")

        if not self._condition_ok:
            _log(self.symbol, "Pre-condition not met — monitoring only (no trades today).")

        import shioaji.constant as c  # type: ignore[import]

        contract = broker.api.Contracts.Stocks[self.symbol]

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
        price = float(tick.close)
        now   = datetime.datetime.now().time()

        with self._lock:
            # ── forced sell at sell_time ──────────────────────────────
            if now >= self.sell_time and self._position_lots > 0 and not self._sold:
                self._do_sell(broker, price, reason="FORCE-SELL")
                return

            if self._position_lots > 0 and not self._sold:
                fully_filled = self._pending_buy_order_id is None
                # ── stop loss / take profit：等完全成交再觸發 ─────────
                if fully_filled:
                    stop_px = self.close_prev * (1 - self.stop_loss_pct / 100)
                    if price <= stop_px:
                        self._do_sell(broker, price, reason="STOP-LOSS")
                        return
                    target = self._buy_price * (1 + self.take_profit_pct / 100)
                    if price >= target:
                        self._do_sell(broker, price, reason="TAKE-PROFIT")
                return

            # ── 首個 tick：鎖定開盤價，一次性判斷條件②④ ──────────────
            if self._open_px is None and now >= _MARKET_OPEN and price > 0:
                self._open_px = price
                gap_pct = (price - self.close_prev) / self.close_prev * 100
                if gap_pct >= 3.0:
                    _log(self.symbol, f"跳空 {gap_pct:.1f}% >= 3% — 今日不進場")
                    self._condition_ok = False
                elif price <= self.ma_fast:
                    _log(self.symbol,
                         f"開盤 {price:.2f} <= MA20 {self.ma_fast:.2f} — 今日不進場")
                    self._condition_ok = False

            # ── 進場：僅在開盤首 tick 且所有條件通過時成交 ───────────
            if self._traded_today is not None and self._traded_today.is_set():
                return

            if (
                not self._bought_today
                and self._open_px is not None   # 首 tick 已處理
                and self._condition_ok
            ):
                self._do_buy(broker, price)

    def _do_buy(self, broker: SinopacBroker, price: float) -> None:
        target = price * (1 + self.take_profit_pct / 100)
        _log(
            self.symbol,
            f"ENTRY  last={price:.2f} > MA20={self.ma_fast:.2f}"
            f"  (MA60={self.ma_slow:.2f} < MA20)"
            f"  target={target:.2f} (+{self.take_profit_pct:.1f}%)"
            f"  → BUY {self.lots} lot @ {price:.2f}",
        )
        try:
            result = broker.place_stock_order(self.symbol, "Buy", self.lots, price)
            self._bought_today         = True          # 防止重複下單
            self._pending_buy_order_id = result.order_id  # 等成交回報才設倉位
            _log(self.symbol, f"Order sent  id={result.order_id}  status={result.status}"
                              f"  (awaiting fill)")
            _save_trade(self.symbol, "Buy", self.lots, price,
                        result.order_id, result.status, broker.creds.simulation,
                        strategy="ma_breakout_s3")
            if self._traded_today is not None:
                self._traded_today.set()
                _log(self.symbol, "今日已下單，其他標的停止進場")
        except Exception as e:
            _log(self.symbol, f"BUY ERROR: {e}", error=True)

    def _do_sell(self, broker: SinopacBroker, price: float, *, reason: str) -> None:
        sell_px = _one_tick_below(price)   # 低 1 檔，提高撮合機率
        pnl = (sell_px - self._buy_price) * self._position_lots * 1000
        _log(
            self.symbol,
            f"{reason}  last={price:.2f}  sell@{sell_px:.2f}(-1tick)"
            f"  bought={self._buy_price:.2f}  est-PnL={pnl:+,.0f} TWD"
            f"  → SELL {self._position_lots} lot @ {sell_px:.2f}",
        )
        try:
            result = broker.place_stock_order(self.symbol, "Sell", self._position_lots, sell_px)
            self._sold                  = True          # 防止重複賣出
            self._pending_sell_order_id = result.order_id  # 等成交回報才清倉
            _log(self.symbol, f"Order sent  id={result.order_id}  status={result.status}"
                              f"  (awaiting sell fill)")
            _save_trade(self.symbol, "Sell", self._position_lots, sell_px,
                        result.order_id, result.status, broker.creds.simulation,
                        strategy="ma_breakout_s3")
        except Exception as e:
            self._sold = False   # 下單失敗 → 允許重試
            _log(self.symbol, f"SELL ERROR: {e}", error=True)

    def _on_sell_fill(self, order_id: str) -> None:
        """賣單成交回報 — 確認後才清倉並停止監控。"""
        with self._lock:
            if order_id != self._pending_sell_order_id:
                return
            self._position_lots         = 0
            self._pending_sell_order_id = None
            self._stop.set()
            _log(self.symbol, f"SELL CONFIRMED  order={order_id}  position cleared")

    def _on_order_failed(self, order_id: str) -> None:
        """買單被交易所拒絕或取消 — 重置狀態，開放其他標的補進場。"""
        with self._lock:
            if order_id != self._pending_buy_order_id:
                return
            self._bought_today              = False
            self._pending_buy_order_id      = None
            self._pending_buy_filled_shares = 0
            self._pending_buy_cost          = 0.0
            if self._traded_today is not None:
                self._traded_today.clear()
            _log(self.symbol,
                 f"Buy order {order_id} failed/cancelled — state reset, others may trade",
                 error=True)

    def _on_fill(self, order_id: str, fill_price: float, fill_qty_shares: int) -> None:
        """訂單成交回報 — 累計所有 fill 事件，全部成交後確認倉位。"""
        with self._lock:
            if order_id != self._pending_buy_order_id:
                return
            self._pending_buy_filled_shares += fill_qty_shares
            self._pending_buy_cost          += fill_price * fill_qty_shares
            filled_lots = self._pending_buy_filled_shares // 1000
            if filled_lots <= 0:
                return
            avg_px = self._pending_buy_cost / self._pending_buy_filled_shares
            self._position_lots = filled_lots
            self._buy_price     = avg_px
            if filled_lots >= self.lots:
                # 完全成交：清除待確認狀態
                self._pending_buy_order_id      = None
                self._pending_buy_filled_shares = 0
                self._pending_buy_cost          = 0.0
                _log(self.symbol,
                     f"FULLY FILLED  {filled_lots}lot @ avg {avg_px:.2f}")
            else:
                _log(self.symbol,
                     f"PARTIAL FILL  {filled_lots}/{self.lots}lot"
                     f" @ {fill_price:.2f}  avg={avg_px:.2f}")


# ── helpers ────────────────────────────────────────────────────────────

def _tick_size(price: float) -> float:
    """台灣股市每檔跳動（依交易所規則）。"""
    if price < 10:
        return 0.01
    elif price < 50:
        return 0.05
    elif price < 100:
        return 0.1
    elif price < 500:
        return 0.5
    elif price < 1000:
        return 1.0
    else:
        return 5.0


def _one_tick_below(price: float) -> float:
    """賣出限價 = 現價 − 1 檔，提高撮合機率。"""
    return round(price - _tick_size(price), 2)


def _log(symbol: str, msg: str, *, error: bool = False) -> None:
    ts  = datetime.datetime.now().strftime("%H:%M:%S")
    out = sys.stderr if error else sys.stdout
    print(f"[{ts}][{symbol}] {msg}", file=out)


def _save_trade(
    symbol: str, action: str, lots: int, price: float,
    order_id: str, order_status: str, simulation: bool,
    strategy: str = "breakout_20d",
) -> None:
    try:
        from t9fox.db.store import insert_trade
        insert_trade(
            date=datetime.date.today().isoformat(),
            symbol=symbol, action=action, lots=lots, price=price,
            order_id=order_id, order_status=order_status, simulation=simulation,
            strategy=strategy,
        )
    except Exception as e:
        _log(symbol, f"DB write failed: {e}", error=True)


def run_ma_breakout_watchlist(
    symbols: list,
    broker: SinopacBroker,
    take_profit_pct: float = 3.0,
    lots: int = 1,
    sell_time: str = "13:20",
) -> None:
    """Strategy 3 — 掃描整個 watchlist，符合條件的股票同時監控下單。"""
    import shioaji.constant as c  # type: ignore[import]

    # ── 盤前：計算所有標的訊號 ──────────────────────────────────────
    traders: dict[str, MaBreakoutDayTrader] = {}
    print(f"[watchlist] 計算訊號中，共 {len(symbols)} 支 ...")
    for sym in symbols:
        try:
            t = MaBreakoutDayTrader.from_broker(
                sym, broker,
                take_profit_pct=take_profit_pct,
                lots=lots,
                sell_time=sell_time,
            )
            traders[sym] = t
            status = "進場候選" if t._condition_ok else "條件未過"
            _log(sym, f"MA20={t.ma_fast:.2f}  MA60={t.ma_slow:.2f}  "
                      f"昨收={t.close_prev:.2f}  量比={t.vol_ratio:.2f}x  [{status}]")
        except Exception as e:
            _log(sym, f"訊號錯誤: {e}", error=True)

    candidates = [s for s, t in traders.items() if t._condition_ok]
    print(f"[watchlist] 進場候選: {len(candidates)}/{len(traders)} 支"
          f"  {candidates if candidates else '（今日無候選）'}")

    if not traders:
        print("[watchlist] 無有效標的，結束。")
        return

    # ── 共用「今日已成交」旗標（先搶先贏） ────────────────────────
    traded_today = threading.Event()
    for t in traders.values():
        t._traded_today = traded_today

    # ── 訂單成交回報 callback：確認買單成交才建倉 ─────────────────────
    @broker.api.on_order_state_change()
    def _on_order_state(stat, msg):
        import shioaji.constant as sc  # type: ignore[import]
        try:
            trade    = msg["trade"]
            order_id = trade["order"]["id"]

            if stat == sc.OrderState.StockDeal:
                for deal in trade.get("deals", []):
                    fill_price = float(deal["price"])
                    fill_qty   = int(deal["quantity"])
                    for t in traders.values():
                        t._on_fill(order_id, fill_price, fill_qty)  # 買單成交
                        t._on_sell_fill(order_id)                    # 賣單成交

            elif stat == sc.OrderState.StockOrder:
                status = trade.get("order", {}).get("status", "")
                if status in ("Failed", "Cancelled"):
                    for t in traders.values():
                        t._on_order_failed(order_id)                 # 買單拒絕/取消

        except Exception as e:
            _log("order_cb", f"parse error: {e}", error=True)

    # ── 單一共用 tick callback，依 code 派送給對應 trader ─────────────
    @broker.api.on_tick_stk_v1()
    def _on_tick(exchange, tick):
        t = traders.get(tick.code)
        if t:
            t._handle_tick(broker, tick)

    # ── 訂閱所有標的 tick ──────────────────────────────────────────
    for sym in traders:
        try:
            broker.api.quote.subscribe(
                broker.api.Contracts.Stocks[sym],
                quote_type=c.QuoteType.Tick,
                version=c.QuoteVersion.v1,
            )
        except Exception as e:
            _log(sym, f"Subscribe 失敗: {e}", error=True)

    _log("watchlist", f"監控中 {len(traders)} 支，按 Ctrl-C 停止 ...")

    # ── 等待收盤 ───────────────────────────────────────────────────
    stop = threading.Event()
    try:
        while not stop.is_set():
            if datetime.datetime.now().time() >= _MARKET_CLOSE:
                _log("watchlist", "收盤，停止監控。")
                break
            stop.wait(timeout=5)
    except KeyboardInterrupt:
        print("\n[watchlist] 使用者中斷。")
    finally:
        for sym in traders:
            try:
                broker.api.quote.unsubscribe(
                    broker.api.Contracts.Stocks[sym],
                    quote_type=c.QuoteType.Tick,
                    version=c.QuoteVersion.v1,
                )
            except Exception:
                pass
