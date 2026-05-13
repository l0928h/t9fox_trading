from __future__ import annotations

from datetime import date, datetime, timedelta
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


def _to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Resample intraday (minute) bars to daily OHLCV. No-op if already daily."""
    if df.empty:
        return df
    # detect if already daily: median gap between bars > 4 hours
    if len(df) > 1:
        gaps = df.index.to_series().diff().dropna()
        if gaps.median() >= pd.Timedelta(hours=4):
            return df  # already daily or coarser

    # per-column resample to avoid pandas named-aggregation quirks
    daily = pd.DataFrame({
        "open":   df["open"].resample("D").first(),
        "high":   df["high"].resample("D").max(),
        "low":    df["low"].resample("D").min(),
        "close":  df["close"].resample("D").last(),
        "volume": df["volume"].resample("D").sum(),
    })
    # drop calendar days with no actual trading (weekends / holidays → NaN open/close)
    return daily.dropna(subset=["open", "close"])


def load_daily_bt(symbol: str) -> pd.DataFrame:
    """Load cached daily_bt Parquet without a broker connection. Returns empty df if not found."""
    cache_dir = ensure_cache_dir()
    path = cache_dir / f"{symbol}_daily_bt.parquet"
    if not path.is_file():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    return df


def load_kbars_range(
    broker: "SinopacBroker",
    symbol: str,
    start: str,
    end: str | None = None,
    ma_warmup: int = 90,
) -> pd.DataFrame:
    """
    Return DAILY OHLCV from Sinopac covering [start - ma_warmup_days, end].

    Sinopac kbars() returns minute bars; this function resamples to daily before
    caching so the Parquet file always contains one row per trading day.

    Caches to data/cache/<symbol>_daily_bt.parquet (separate from minute cache).
    Both TWSE and OTC symbols are supported via Sinopac API.

    start / end : 'YYYY-MM-DD'
    ma_warmup   : extra calendar days fetched before `start` for MA pre-warming
    """
    cache_dir = ensure_cache_dir()
    # separate cache key from minute-bar cache to avoid mixing granularities
    path = cache_dir / f"{symbol}_daily_bt.parquet"

    start_dt   = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt     = datetime.strptime(end, "%Y-%m-%d").date() if end else date.today()
    fetch_from = (start_dt - timedelta(days=ma_warmup)).strftime("%Y-%m-%d")
    fetch_to   = end_dt.strftime("%Y-%m-%d")

    def _load_source() -> pd.DataFrame:
        """Try _daily_bt cache → _kbars minute cache → API, return daily bars."""
        # 1. existing daily_bt cache
        if path.is_file():
            cached = pd.read_parquet(path)
            if not isinstance(cached.index, pd.DatetimeIndex):
                cached.index = pd.to_datetime(cached.index)
            if not cached.empty:
                return cached

        # 2. minute-bar kbars cache (from precheck / load_or_fetch_kbars)
        kbars_path = cache_dir / f"{symbol}_kbars.parquet"
        if kbars_path.is_file():
            raw = pd.read_parquet(kbars_path)
            if not isinstance(raw.index, pd.DatetimeIndex):
                raw.index = pd.to_datetime(raw.index)
            daily = _to_daily(raw)
            if not daily.empty:
                daily.to_parquet(path)   # save as daily_bt for next time
                return daily

        # 3. live API (fallback — may be rate-limited)
        raw = broker.get_daily_kbars(symbol, start=fetch_from, end=fetch_to)
        daily = _to_daily(raw)
        if not daily.empty:
            daily.to_parquet(path)
        return daily

    cached_daily = _load_source()
    if cached_daily.empty:
        return cached_daily

    if not isinstance(cached_daily.index, pd.DatetimeIndex):
        cached_daily.index = pd.to_datetime(cached_daily.index)

    return cached_daily.loc[fetch_from:fetch_to]
