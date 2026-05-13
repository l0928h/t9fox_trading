from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from t9fox.config import ensure_cache_dir

if TYPE_CHECKING:
    from t9fox.broker.sinopac import SinopacBroker


def load_or_fetch_kbars(
    broker: "SinopacBroker",
    symbol: str,
    n_days: int = 60,
) -> pd.DataFrame:
    """
    Return the last n_days of daily OHLCV from Sinopac kbars.

    Cache strategy (Parquet at data/cache/<symbol>_kbars.parquet):
    - First run : fetch ~3 months and write cache
    - Subsequent: load cache, check last date, fetch only missing days, append
    """
    cache_dir  = ensure_cache_dir()
    path       = cache_dir / f"{symbol}_kbars.parquet"
    today      = date.today()
    yesterday  = today - timedelta(days=1)
    end_str    = yesterday.strftime("%Y-%m-%d")
    start_full = (today - timedelta(days=n_days * 3)).strftime("%Y-%m-%d")

    if path.is_file():
        cached = pd.read_parquet(path)
        if not isinstance(cached.index, pd.DatetimeIndex):
            cached.index = pd.to_datetime(cached.index)

        last_cached = cached.index.max().date()

        if last_cached >= yesterday:
            return cached.tail(n_days)

        # incremental fetch: only missing days
        fetch_start = (last_cached + timedelta(days=1)).strftime("%Y-%m-%d")
        new = broker.get_daily_kbars(symbol, start=fetch_start, end=end_str)

        if not new.empty:
            merged = pd.concat([cached, new])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            merged.to_parquet(path)
            return merged.tail(n_days)

        return cached.tail(n_days)

    # first time: full fetch
    df = broker.get_daily_kbars(symbol, start=start_full, end=end_str)
    if not df.empty:
        df.to_parquet(path)
    return df.tail(n_days)
