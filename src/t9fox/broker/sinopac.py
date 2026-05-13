from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from t9fox.broker.credentials import SinopacCredentials

if TYPE_CHECKING:
    import shioaji as sj  # type: ignore[import]


@dataclass
class OrderResult:
    symbol: str
    action: str      # "buy" | "sell"
    quantity: int    # shares
    price: float
    status: str      # from Shioaji trade status
    order_id: str


@dataclass
class Position:
    symbol: str
    quantity: int    # shares (lots × 1000)
    avg_price: float
    pnl: float


class SinopacBroker:
    """
    Wraps Shioaji API.

    simulation=True  → real credentials, real quotes, simulated orders
    simulation=False → real credentials, real quotes, real orders (live)
    CA required for any order placement.
    """

    def __init__(self, creds: SinopacCredentials):
        self.creds = creds
        self._api: "sj.Shioaji | None" = None

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
                "shioaji not installed. Run: pip install -e \".[sinopac]\""
            )

        mode = "simulation" if self.creds.simulation else "LIVE"
        print(f"[sinopac] Logging in ({mode}) …", file=sys.stderr)

        self._api = sj.Shioaji(simulation=self.creds.simulation)
        accounts = self._api.login(
            api_key=self.creds.api_key,
            secret_key=self.creds.secret_key,
            fetch_contract=True,
        )
        print(f"[sinopac] Logged in. Accounts: {[str(a) for a in accounts]}", file=sys.stderr)

        if self.creds.has_ca:
            self._api.activate_ca(
                ca_path=str(self.creds.ca_path),
                ca_passwd=self.creds.ca_passwd,
                person_id=self.creds.person_id,
            )
            print("[sinopac] CA activated.", file=sys.stderr)
        else:
            print("[sinopac] CA not provided — data-only mode (no order placement).", file=sys.stderr)

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
        return self.api.stock_account

    # ── market data ────────────────────────────────────────────────────

    def get_snapshot(self, symbol: str) -> dict:
        """Return latest snapshot (bid/ask/close/volume) for a stock."""
        contract = self.api.Contracts.Stocks[symbol]
        snapshots = self.api.snapshots([contract])
        if not snapshots:
            return {}
        s = snapshots[0]
        return {
            "symbol": symbol,
            "close": s.close,
            "open": s.open,
            "high": s.high,
            "low": s.low,
            "volume": s.volume,
            "bid_price": s.bid_price,
            "ask_price": s.ask_price,
        }

    # ── positions ──────────────────────────────────────────────────────

    def list_positions(self) -> list[Position]:
        """Return all open stock positions."""
        raw = self.api.list_positions(self.stock_account)
        result: list[Position] = []
        for p in raw:
            result.append(Position(
                symbol=p.code,
                quantity=p.quantity * 1000,   # lots → shares
                avg_price=p.price,
                pnl=p.pnl,
            ))
        return result

    def get_position_shares(self, symbol: str) -> int:
        """Return shares held for a symbol (0 if none)."""
        for p in self.list_positions():
            if p.symbol == symbol:
                return p.quantity
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
        Place a ROD limit order.

        action: "buy" | "sell"
        lots:   number of 張 (1 lot = 1000 shares)
        price:  limit price in TWD
        """
        if not self.creds.has_ca:
            raise RuntimeError("CA certificate required for order placement.")
        if lots <= 0:
            raise ValueError(f"lots must be positive, got {lots}")

        import shioaji.constant as c  # type: ignore[import]

        contract = self.api.Contracts.Stocks[symbol]
        order = self.api.Order(
            price=price,
            quantity=lots,
            action=c.Action.Buy if action == "buy" else c.Action.Sell,
            price_type=c.StockPriceType.LMT,
            order_type=c.OrderType.ROD,
            order_lot=c.StockOrderLot.Common,
            account=self.stock_account,
        )
        trade = self.api.place_order(contract, order)
        return OrderResult(
            symbol=symbol,
            action=action,
            quantity=lots * 1000,
            price=price,
            status=str(trade.status.status),
            order_id=trade.status.id,
        )
