from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from t9fox.broker.sinopac import SinopacBroker


def calc_n_day_high_from_broker(broker: "SinopacBroker", symbol: str, n: int = 20) -> float:
    """
    Highest high over the last n trading days (excluding today), via Sinopac kbars.
    Works for both TWSE-listed and OTC-listed stocks.
    """
    end   = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=n * 3)).strftime("%Y-%m-%d")   # 3x buffer for holidays
    df = broker.get_daily_kbars(symbol, start=start, end=end)
    if df.empty or len(df) < n:
        raise ValueError(
            f"{symbol}: need at least {n} trading days from Sinopac, got {len(df)}."
        )
    return float(df["high"].tail(n).max())


def calc_n_day_high(symbol: str, n: int = 20) -> float:
    """
    Highest high via TWSE public API + local Parquet cache.
    Fallback for non-broker contexts (backtest, etc.).
    """
    from t9fox.data.twse_daily import load_or_fetch_daily_bars
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=n * 3)
    df    = load_or_fetch_daily_bars(symbol, str(start), str(end), refresh=False)
    if df.empty or len(df) < n:
        raise ValueError(
            f"{symbol}: need at least {n} trading days, got {len(df)}. "
            "Run `t9fox fetch` first."
        )
    return float(df["high"].tail(n).max())
