# Strategy 3 — MA 突破＋量能過濾（MA Breakout with Volume Filter）
# 進場條件：MA60 < MA20、開盤 > MA20、昨收 < MA20、量 > 20日均量、跳空 < 3%
# 出場條件：停利 3% 或收盤強制平倉（當沖）
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from t9fox.broker.sinopac import SinopacBroker


@dataclass
class MaBreakoutParams:
    fast: int = 20          # 月線
    slow: int = 60          # 季線
    take_profit_pct: float = 3.0   # 停利 %


@dataclass
class MaBreakoutSignal:
    ma_fast: float          # MA20
    ma_slow: float          # MA60
    close_last: float       # 最新收盤價（昨日）
    vol_ratio: float        # 昨日成交量 / 20日均量
    condition_ok: bool      # MA60 < MA20 且 昨日收盤 < MA20 且量能 > 均量


def calc_ma_breakout_signal(
    broker: "SinopacBroker",
    symbol: str,
    params: MaBreakoutParams,
) -> MaBreakoutSignal:
    """
    Calculate MA20/MA60 from Sinopac kbars cache.
    condition_ok = True  when  MA60 < MA20  (季線在月線下方).
    """
    from t9fox.data.kbars_cache import load_or_fetch_kbars

    n_fetch = params.slow * 3
    df = load_or_fetch_kbars(broker, symbol, n_days=n_fetch)

    if df.empty or len(df) < params.slow:
        raise ValueError(
            f"{symbol}: need {params.slow} trading days, got {len(df)}"
        )

    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)
    ma_fast    = float(close.tail(params.fast).mean())
    ma_slow    = float(close.tail(params.slow).mean())
    close_last = float(close.iloc[-1])
    vol_last   = float(volume.iloc[-1])
    vol_ma20   = float(volume.tail(20).mean())
    vol_ratio  = vol_last / vol_ma20 if vol_ma20 > 0 else 0.0

    return MaBreakoutSignal(
        ma_fast=ma_fast,
        ma_slow=ma_slow,
        close_last=close_last,
        vol_ratio=vol_ratio,
        condition_ok=ma_slow < ma_fast and close_last < ma_fast and vol_ratio > 1.0,
    )
