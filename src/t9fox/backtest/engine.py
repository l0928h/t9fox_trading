from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from t9fox.constants import DEFAULT_COMMISSION_RATE, DEFAULT_SELL_TAX_RATE


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission_rate: float = DEFAULT_COMMISSION_RATE
    sell_tax_rate: float = DEFAULT_SELL_TAX_RATE
    lot_size: int = 1000


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    returns: pd.Series
    trades: pd.DataFrame
    final_equity: float


def run_backtest(
    ohlcv: pd.DataFrame,
    target_position: pd.Series,
    cfg: BacktestConfig | None = None,
) -> BacktestResult:
    """
    Long-only. `target_position` fraction in [0, 1]: desired invested weight before trades.

    Signal is known at previous bar's close; execution at current bar's open (standard TW daily bar).
    Intraday mark-to-market uses close for equity curve reporting.
    """
    cfg = cfg or BacktestConfig()
    df = ohlcv.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    sig = target_position.reindex(df.index).ffill().fillna(0.0).clip(0.0, 1.0)

    open_px = df["open"].astype(float)
    close_px = df["close"].astype(float)

    cash = float(cfg.initial_cash)
    shares = 0
    trade_rows: list[dict] = []
    equity_vals: list[float] = []

    for j in range(len(df)):
        c = close_px.iloc[j]
        if j == 0:
            equity_vals.append(cash + shares * float(c))
            continue

        prev_sig = float(sig.iloc[j - 1])
        price = float(open_px.iloc[j])
        if not np.isfinite(price) or price <= 0:
            equity_vals.append(cash + shares * float(c))
            continue

        tv = cash + shares * price
        if tv <= 0:
            equity_vals.append(cash + shares * float(c))
            continue

        tgt_shares = int((tv * prev_sig / price / cfg.lot_size)) * cfg.lot_size
        tgt_shares = max(tgt_shares, 0)
        delta = tgt_shares - shares

        if delta > 0:
            buy_qty = delta
            cost = buy_qty * price
            fee = cost * cfg.commission_rate
            pay = cost + fee
            if pay <= cash + 1e-6:
                cash -= pay
                shares += buy_qty
                trade_rows.append(
                    {
                        "time": df.index[j],
                        "side": "buy",
                        "qty": buy_qty,
                        "price": price,
                        "fee": fee,
                    }
                )
        elif delta < 0:
            sell_qty = min(-delta, shares)
            if sell_qty > 0:
                proceeds = sell_qty * price
                fee = proceeds * cfg.commission_rate
                tax = proceeds * cfg.sell_tax_rate
                cash += proceeds - fee - tax
                shares -= sell_qty
                trade_rows.append(
                    {
                        "time": df.index[j],
                        "side": "sell",
                        "qty": sell_qty,
                        "price": price,
                        "fee": fee,
                        "tax": tax,
                    }
                )

        equity_vals.append(cash + shares * float(c))

    equity = pd.Series(equity_vals, index=df.index, name="equity").astype(float)
    rets = equity.pct_change().fillna(0.0)
    trades_df = pd.DataFrame(trade_rows)
    final_eq = float(equity.iloc[-1]) if len(equity) else cfg.initial_cash
    return BacktestResult(
        equity_curve=equity,
        returns=rets,
        trades=trades_df,
        final_equity=final_eq,
    )


def simple_metrics(result: BacktestResult) -> dict[str, float]:
    eq = result.equity_curve
    if len(eq) < 2:
        return {"cagr": float("nan"), "sharpe": float("nan"), "max_drawdown": float("nan")}
    rets = eq.pct_change().dropna()
    if len(rets) and rets.std() > 1e-12:
        sharpe = float(np.sqrt(252) * rets.mean() / rets.std())
    else:
        sharpe = float("nan")
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    if years > 0 and eq.iloc[0] > 0:
        cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1)
    else:
        cagr = float("nan")
    roll_max = eq.cummax()
    max_drawdown = float((eq / roll_max - 1.0).min())
    return {"cagr": cagr, "sharpe": sharpe, "max_drawdown": max_drawdown}
