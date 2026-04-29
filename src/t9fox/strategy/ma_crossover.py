from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MaCrossParams:
    fast: int = 10
    slow: int = 30


def ma_crossover_targets(ohlcv: pd.DataFrame, params: MaCrossParams | None = None) -> pd.Series:
    """Long when fast SMA of close > slow SMA; else flat. Shifted so no lookahead on same bar."""
    p = params or MaCrossParams()
    close = ohlcv["close"].astype(float)
    fast = close.rolling(p.fast, min_periods=p.fast).mean()
    slow = close.rolling(p.slow, min_periods=p.slow).mean()
    long_signal = (fast > slow).astype(float)
    return long_signal.reindex(ohlcv.index).fillna(0.0)
