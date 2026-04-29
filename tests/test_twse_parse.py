from datetime import date

import pandas as pd
import pytest

from t9fox.backtest.engine import BacktestConfig, run_backtest, simple_metrics
from t9fox.data import twse_daily


def test_parse_roc_date() -> None:
    assert twse_daily._parse_roc_date("112/11/01") == date(2023, 11, 1)
    assert twse_daily._parse_roc_date("") is None


def test_fetch_daily_bars_normalizes_python_date_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: index was datetime.date; comparing to pd.Timestamp raised TypeError."""

    def fake_one_month(
        stock_no: str, yyyymm: str, session: object
    ) -> pd.DataFrame:
        if yyyymm != "202401":
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "open": [10.0],
                "high": [11.0],
                "low": [9.0],
                "close": [10.5],
                "volume": [100],
                "turnover": [1000.0],
            },
            index=[date(2024, 1, 2)],
        )

    monkeypatch.setattr(twse_daily, "_fetch_one_month", fake_one_month)
    monkeypatch.setattr(twse_daily.time, "sleep", lambda _s: None)

    df = twse_daily.fetch_daily_bars("2330", "2024-01-01", "2024-01-31")
    assert len(df) == 1
    assert float(df.iloc[0]["close"]) == 10.5
    assert isinstance(df.index, pd.DatetimeIndex)


def test_backtest_long_only_buy_hold() -> None:
    idx = pd.date_range("2020-01-01", periods=5, freq="B", tz=None)
    ohlcv = pd.DataFrame(
        {
            "open": [100.0] * 5,
            "high": [101.0] * 5,
            "low": [99.0] * 5,
            "close": [100.0] * 5,
            "volume": [1_000_000] * 5,
            "turnover": [100_000_000.0] * 5,
        },
        index=idx,
    )
    tgt = pd.Series(1.0, index=idx)
    r = run_backtest(ohlcv, tgt, BacktestConfig(initial_cash=1_000_000.0))
    assert len(r.equity_curve) == 5
    assert r.final_equity > 0
    m = simple_metrics(r)
    assert "sharpe" in m
