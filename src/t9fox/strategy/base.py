from __future__ import annotations

from typing import Protocol

import pandas as pd


class Strategy(Protocol):
    """Maps OHLCV to target long-only allocation in [0, 1]."""

    def targets(self, ohlcv: pd.DataFrame) -> pd.Series: ...
