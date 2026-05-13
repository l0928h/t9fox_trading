# Strategy 3 — MA 突破＋量能過濾 Backtest Engine
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import numpy as np

from t9fox.constants import DEFAULT_COMMISSION_RATE, DEFAULT_SELL_TAX_RATE


@dataclass(frozen=True)
class MaBreakoutBtParams:
    fast: int = 20
    slow: int = 60
    take_profit_pct: float = 3.0
    stop_loss_pct: float = 9.0   # 跌停前出場（昨收 -9%，跌停 -10% 的前 1% 離場）
    commission_rate: float = DEFAULT_COMMISSION_RATE
    sell_tax_rate: float = DEFAULT_SELL_TAX_RATE
    lots: int = 1
    initial_cash: float = 1_000_000.0


@dataclass
class TradeRecord:
    date: str
    entry_price: float
    exit_price: float
    target_price: float
    ma_fast: float
    ma_slow: float
    return_pct: float
    gross_pnl: float
    net_pnl: float
    exit_reason: str         # "take_profit" | "stop_loss" | "eod"


@dataclass
class MaBreakoutBtResult:
    trades: pd.DataFrame
    equity_curve: pd.Series
    metrics: dict[str, float]
    params: MaBreakoutBtParams


def backtest_ma_breakout(
    ohlcv: pd.DataFrame,
    params: MaBreakoutBtParams | None = None,
) -> MaBreakoutBtResult:
    """
    Day-trade backtest for the MA-breakout strategy using daily OHLCV bars.

    Entry  : open[i] > MA20[i-1]  AND  MA60[i-1] < MA20[i-1]
    Exit   : if high[i] >= open[i] * (1 + take_profit_pct/100)
                 → sell at target price (take profit)
             else
                 → sell at close[i] (end-of-day force-sell)

    Commission is charged on both legs; sell tax on the sell leg only.
    One trade per day; no overnight positions.
    """
    p = params or MaBreakoutBtParams()

    df = ohlcv.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index().dropna(subset=["open", "high", "low", "close"])

    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)
    ma_fast  = close.rolling(p.fast, min_periods=p.fast).mean()
    ma_slow  = close.rolling(p.slow, min_periods=p.slow).mean()
    vol_ma20 = volume.rolling(20, min_periods=20).mean()

    shares_per_lot = 1000
    share_qty = p.lots * shares_per_lot

    trades: list[TradeRecord] = []
    cash = p.initial_cash
    equity_vals: list[float] = []
    dates: list = []

    for i in range(len(df)):
        dates.append(df.index[i])

        if i == 0:
            equity_vals.append(cash)
            continue

        maf_prev = ma_fast.iloc[i - 1]
        mas_prev = ma_slow.iloc[i - 1]

        if not (np.isfinite(maf_prev) and np.isfinite(mas_prev)):
            equity_vals.append(cash)
            continue

        open_px      = float(df["open"].iloc[i])
        high_px      = float(df["high"].iloc[i])
        low_px       = float(df["low"].iloc[i])
        close_px     = float(df["close"].iloc[i])
        close_prev   = float(df["close"].iloc[i - 1])
        vol_today    = float(volume.iloc[i])
        vol_ma_prev  = vol_ma20.iloc[i - 1]

        if not (np.isfinite(open_px) and open_px > 0):
            equity_vals.append(cash)
            continue

        gap_pct = (open_px - close_prev) / close_prev * 100

        condition_ok = mas_prev < maf_prev
        entry_signal = (
            condition_ok
            and open_px > maf_prev
            and close_prev < maf_prev
            and gap_pct < 3.0
        )

        if not entry_signal:
            equity_vals.append(cash)
            continue

        target   = open_px * (1 + p.take_profit_pct / 100)
        stop_px  = close_prev * (1 - p.stop_loss_pct / 100)

        if low_px <= stop_px:
            exit_px     = stop_px
            exit_reason = "stop_loss"
        elif high_px >= target:
            exit_px     = target
            exit_reason = "take_profit"
        else:
            exit_px     = close_px
            exit_reason = "eod"

        buy_cost  = open_px * share_qty
        buy_fee   = buy_cost * p.commission_rate
        sell_proceeds = exit_px * share_qty
        sell_fee  = sell_proceeds * p.commission_rate
        sell_tax  = sell_proceeds * p.sell_tax_rate

        gross_pnl = (exit_px - open_px) * share_qty
        net_pnl   = gross_pnl - buy_fee - sell_fee - sell_tax
        cash += net_pnl
        ret_pct   = (exit_px - open_px) / open_px * 100

        trades.append(TradeRecord(
            date=df.index[i].strftime("%Y-%m-%d"),
            entry_price=open_px,
            exit_price=exit_px,
            target_price=round(target, 2),
            ma_fast=round(float(maf_prev), 2),
            ma_slow=round(float(mas_prev), 2),
            return_pct=ret_pct,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            exit_reason=exit_reason,
        ))
        equity_vals.append(cash)

    equity = pd.Series(equity_vals, index=dates, name="equity", dtype=float)
    trades_df = pd.DataFrame([t.__dict__ for t in trades]) if trades else pd.DataFrame()

    metrics = _compute_metrics(equity, trades_df, p.initial_cash)
    return MaBreakoutBtResult(
        trades=trades_df,
        equity_curve=equity,
        metrics=metrics,
        params=p,
    )


def _compute_metrics(
    equity: pd.Series,
    trades: pd.DataFrame,
    initial_cash: float,
) -> dict[str, float]:
    m: dict[str, float] = {}

    if trades.empty:
        return {"n_trades": 0, "win_rate": float("nan"), "avg_return_pct": float("nan"),
                "total_return_pct": float("nan"), "cagr": float("nan"),
                "sharpe": float("nan"), "max_drawdown_pct": float("nan"),
                "avg_win_pct": float("nan"), "avg_loss_pct": float("nan"),
                "profit_factor": float("nan")}

    n = len(trades)
    wins  = trades[trades["net_pnl"] > 0]
    loses = trades[trades["net_pnl"] <= 0]

    m["n_trades"]       = float(n)
    m["win_rate"]       = len(wins) / n
    m["avg_return_pct"] = float(trades["return_pct"].mean())
    m["avg_win_pct"]    = float(wins["return_pct"].mean()) if len(wins) else float("nan")
    m["avg_loss_pct"]   = float(loses["return_pct"].mean()) if len(loses) else float("nan")

    gross_wins  = wins["net_pnl"].sum() if len(wins) else 0.0
    gross_loses = abs(loses["net_pnl"].sum()) if len(loses) else 1e-9
    m["profit_factor"] = gross_wins / gross_loses if gross_loses > 1e-10 else float("inf")

    final_eq = float(equity.iloc[-1]) if len(equity) else initial_cash
    m["total_return_pct"] = (final_eq - initial_cash) / initial_cash * 100

    days = (equity.index[-1] - equity.index[0]).days
    years = days / 365.25
    if years > 0 and initial_cash > 0:
        m["cagr"] = ((final_eq / initial_cash) ** (1 / years) - 1) * 100
    else:
        m["cagr"] = float("nan")

    rets = equity.pct_change().dropna()
    m["sharpe"] = (
        float(np.sqrt(252) * rets.mean() / rets.std())
        if len(rets) > 1 and rets.std() > 1e-12
        else float("nan")
    )

    roll_max = equity.cummax()
    m["max_drawdown_pct"] = float((equity / roll_max - 1.0).min() * 100)

    tp_trades = trades[trades["exit_reason"] == "take_profit"]
    sl_trades = trades[trades["exit_reason"] == "stop_loss"]
    m["take_profit_rate"] = len(tp_trades) / n
    m["stop_loss_rate"]   = len(sl_trades) / n

    return m
