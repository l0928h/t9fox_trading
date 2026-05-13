from __future__ import annotations

from datetime import date, timedelta

from t9fox.data.twse_daily import load_or_fetch_daily_bars


def calc_n_day_high(symbol: str, n: int = 20) -> float:
    """
    Highest high over the last n trading days, excluding today.
    Used as the breakout trigger level.
    """
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=n * 3)   # buffer for weekends / holidays
    df = load_or_fetch_daily_bars(symbol, str(start), str(end), refresh=True)
    if df.empty or len(df) < n:
        raise ValueError(
            f"{symbol}: need at least {n} trading days, got {len(df)}. "
            "Run `t9fox fetch` first."
        )
    return float(df["high"].tail(n).max())
