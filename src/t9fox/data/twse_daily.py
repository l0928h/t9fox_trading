from __future__ import annotations

import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

from t9fox.config import Settings, ensure_cache_dir

TWSE_STOCK_DAY = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
REQUEST_PAUSE_SEC = 0.3


def _parse_roc_date(s: str) -> date | None:
    s = (s or "").strip()
    m = re.match(r"^(\d{2,3})/(\d{2})/(\d{2})$", s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1000:
        y += 1911
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _num(s: str) -> float:
    s = (s or "").strip().replace(",", "")
    if not s or s == "--":
        return float("nan")
    return float(s)


def _fetch_one_month(stock_no: str, yyyymm: str, session: requests.Session) -> pd.DataFrame:
    """yyyymm: Gregorian 'YYYYMM', e.g. '202311'."""
    day = f"{yyyymm}01"
    params = {"date": day, "stockNo": stock_no.strip(), "response": "json"}
    url = f"{TWSE_STOCK_DAY}?{urlencode(params)}"
    r = session.get(url, timeout=60)
    r.raise_for_status()
    try:
        payload = r.json()
    except ValueError:
        return pd.DataFrame()
    if payload.get("stat") != "OK" or not payload.get("data"):
        return pd.DataFrame()
    rows = []
    for row in payload["data"]:
        if len(row) < 7:
            continue
        d = _parse_roc_date(str(row[0]))
        if d is None:
            continue
        rows.append(
            {
                "date": d,
                "volume": int(_num(str(row[1]))) if str(row[1]).strip() else 0,
                "turnover": _num(str(row[2])),
                "open": _num(str(row[3])),
                "high": _num(str(row[4])),
                "low": _num(str(row[5])),
                "close": _num(str(row[6])),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("date").sort_index()
    return df


def _iter_months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield f"{y:04d}{m:02d}"
        m += 1
        if m > 12:
            m = 1
            y += 1


def fetch_daily_bars(
    stock_no: str,
    start: date | str,
    end: date | str | None = None,
    *,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """
    Daily OHLCV from TWSE `STOCK_DAY` (merged months). Index: date; prices in TWD.

    Request date uses Gregorian YYYYMM01 per month. Be polite: small delay between months.
    """
    if isinstance(start, str):
        start = datetime.strptime(start, "%Y-%m-%d").date()
    if end is None:
        end = date.today()
    elif isinstance(end, str):
        end = datetime.strptime(end, "%Y-%m-%d").date()
    if start > end:
        raise ValueError("start must be on or before end")

    sess = session or requests.Session()
    sess.headers.setdefault(
        "User-Agent",
        "T9FOX-Trade/0.1 (quant research; https://github.com/)",
    )
    parts: list[pd.DataFrame] = []
    for yyyymm in _iter_months(start, end):
        time.sleep(REQUEST_PAUSE_SEC)
        df = _fetch_one_month(stock_no, yyyymm, sess)
        if not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "turnover"])
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index = pd.to_datetime(out.index)
    out = out.loc[(out.index >= pd.Timestamp(start)) & (out.index <= pd.Timestamp(end))]
    return out


def load_or_fetch_daily_bars(
    stock_no: str,
    start: date | str,
    end: date | str | None = None,
    *,
    settings: Settings | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Load Parquet cache under data/cache or fetch from TWSE."""
    s = settings or Settings.load()
    ensure_cache_dir(s)
    path = Path(s.cache_dir) / f"{stock_no.strip()}_daily.parquet"
    if path.is_file() and not refresh:
        cached = pd.read_parquet(path)
        if not isinstance(cached.index, pd.DatetimeIndex):
            cached.index = pd.to_datetime(cached.index)
        if isinstance(start, str):
            start_d = datetime.strptime(start, "%Y-%m-%d").date()
        else:
            start_d = start
        if end is None:
            end_d = date.today()
        elif isinstance(end, str):
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
        else:
            end_d = end
        sl = cached.loc[
            (cached.index >= pd.Timestamp(start_d)) & (cached.index <= pd.Timestamp(end_d))
        ]
        if len(sl) > 0:
            return sl
    df = fetch_daily_bars(stock_no, start, end)
    if not df.empty:
        df.to_parquet(path)
    return df
