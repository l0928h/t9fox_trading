from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from t9fox.broker.credentials import SinopacCredentials

if TYPE_CHECKING:
    import shioaji as sj  # type: ignore[import]

# ── Shioaji v1.3 API reference (inspected from installed package) ──────────
# Shioaji(simulation=False)
# login(api_key, secret_key, fetch_contract=True, subscribe_trade=True, receive_window=30000)
# activate_ca(ca_path, ca_passwd, person_id='', store=0) -> bool
# place_order(contract, order, timeout=5000) -> Trade
# snapshots([contract, ...]) -> [Snapshot]
# list_positions(account, unit=Unit.Common) -> [StockPosition]
#
# StockPosition fields: code, quantity(lots), price(avg), last_price, pnl, direction
# Snapshot fields: code, open, high, low, close, volume, buy_price, sell_price, ...
# Order(price, quantity(lots), action, price_type, order_type, order_lot, account)


@dataclass
class OrderResult:
    symbol: str
    action: str       # "Buy" | "Sell"
    lots: int
    price: float
    status: str
    order_id: str


@dataclass
class Position:
    symbol: str
    lots: int         # 張數
    shares: int       # 股數（lots × 1000）
    avg_price: float
    last_price: float
    pnl: float


class SinopacBroker:
    """
    Wraps Shioaji v1.x.

    simulation=True  → real credentials + real quotes + simulated orders
    simulation=False → real credentials + real quotes + real orders (live)
    CA required for any order placement.
    """

    def __init__(self, creds: SinopacCredentials):
        self.creds = creds
        self._api: "sj.Shioaji | None" = None
        self._stock_account = None

    # ── context manager ────────────────────────────────────────────────

    def __enter__(self) -> "SinopacBroker":
        self.login()
        return self

    def __exit__(self, *_) -> None:
        self.logout()

    # ── login / logout ─────────────────────────────────────────────────

    def login(self) -> None:
        try:
            import shioaji as sj  # type: ignore[import]
        except ModuleNotFoundError:
            raise ModuleNotFoundError(
                'shioaji not installed. Run: pip install -e ".[sinopac]"'
            )

        mode = "simulation" if self.creds.simulation else "LIVE"
        print(f"[sinopac] Logging in ({mode}) …", file=sys.stderr)

        self._api = sj.Shioaji(simulation=self.creds.simulation)
        accounts = self._api.login(
            api_key=self.creds.api_key,
            secret_key=self.creds.secret_key,
            fetch_contract=True,
            subscribe_trade=True,
        )
        print(f"[sinopac] Logged in. Accounts: {[str(a) for a in accounts]}", file=sys.stderr)

        # Explicitly pick the stock account (StockAccount type, broker_id H = securities)
        from shioaji.account import StockAccount  # type: ignore[import]
        for acc in accounts:
            if isinstance(acc, StockAccount):
                self._stock_account = acc
                break
        if self._stock_account is None and accounts:
            self._stock_account = accounts[0]
        print(f"[sinopac] Stock account: {self._stock_account}", file=sys.stderr)

        if self.creds.has_ca:
            ok = self._api.activate_ca(
                ca_path=str(self.creds.ca_path),
                ca_passwd=self.creds.ca_passwd,
                person_id=self.creds.person_id or "",
            )
            print(f"[sinopac] CA activated: {ok}", file=sys.stderr)
        else:
            print("[sinopac] No CA — data-only mode (order placement unavailable).", file=sys.stderr)

    def logout(self) -> None:
        if self._api is not None:
            self._api.logout()
            self._api = None
            print("[sinopac] Logged out.", file=sys.stderr)

    # ── properties ─────────────────────────────────────────────────────

    @property
    def api(self) -> "sj.Shioaji":
        if self._api is None:
            raise RuntimeError("Not logged in. Call login() or use as context manager.")
        return self._api

    @property
    def stock_account(self):
        if self._stock_account is not None:
            return self._stock_account
        return self.api.stock_account

    # ── market data ────────────────────────────────────────────────────

    def get_daily_kbars(self, symbol: str, start: str, end: str) -> "pd.DataFrame":
        """
        Daily OHLCV from Sinopac kbars API (covers both TWSE and OTC).

        start / end: 'YYYY-MM-DD'
        Returns DataFrame with DatetimeIndex and columns: open, high, low, close, volume.
        """
        import pandas as pd  # type: ignore[import]

        contract = self.api.Contracts.Stocks[symbol]
        kb = self.api.kbars(contract, start=start, end=end)
        if not kb or not kb.ts:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame({
            "open":   kb.Open,
            "high":   kb.High,
            "low":    kb.Low,
            "close":  kb.Close,
            "volume": kb.Volume,
        }, index=pd.to_datetime(kb.ts, unit="ns"))
        df.index.name = "date"
        return df.sort_index()

    def get_snapshot(self, symbol: str) -> dict:
        """Latest snapshot for a stock (close/OHLC/volume/bid/ask)."""
        contract = self.api.Contracts.Stocks[symbol]
        snaps = self.api.snapshots([contract])
        if not snaps:
            return {}
        s = snaps[0]
        return {
            "symbol": symbol,
            "open": s.open,
            "high": s.high,
            "low": s.low,
            "close": s.close,
            "volume": s.volume,
            "total_volume": s.total_volume,
            "buy_price": s.buy_price,
            "sell_price": s.sell_price,
            "change_price": s.change_price,
            "change_rate": s.change_rate,
        }

    # ── positions ──────────────────────────────────────────────────────

    def list_positions(self) -> list[Position]:
        """All open stock positions (quantity in lots AND shares)."""
        raw = self.api.list_positions(self.stock_account)
        return [
            Position(
                symbol=p.code,
                lots=p.quantity,
                shares=p.quantity * 1000,
                avg_price=p.price,
                last_price=p.last_price,
                pnl=p.pnl,
            )
            for p in raw
        ]

    def get_position_lots(self, symbol: str) -> int:
        """Current lots held for a symbol (0 if none)."""
        for p in self.list_positions():
            if p.symbol == symbol:
                return p.lots
        return 0

    # ── orders ─────────────────────────────────────────────────────────

    def place_stock_order(
        self,
        symbol: str,
        action: str,
        lots: int,
        price: float,
    ) -> OrderResult:
        """
        ROD limit order for a stock.

        action : "Buy" | "Sell"
        lots   : number of 張 (1 張 = 1000 shares)
        price  : limit price TWD
        """
        if not self.creds.has_ca:
            raise RuntimeError("CA certificate required for order placement.")
        if lots <= 0:
            raise ValueError(f"lots must be > 0, got {lots}")

        import shioaji.constant as c  # type: ignore[import]

        contract = self.api.Contracts.Stocks[symbol]
        act = c.Action.Buy if action.lower() == "buy" else c.Action.Sell
        order = self.api.Order(
            price=price,
            quantity=lots,
            action=act,
            price_type=c.StockPriceType.LMT,
            order_type=c.OrderType.ROD,
            order_lot=c.StockOrderLot.Common,
            account=self.stock_account,
        )
        trade = self.api.place_order(contract, order)
        return OrderResult(
            symbol=symbol,
            action=action,
            lots=lots,
            price=price,
            status=str(trade.status.status),
            order_id=trade.status.id,
        )
