from __future__ import annotations

import datetime
import json
import urllib.request
import urllib.error
from urllib.parse import urlparse

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.twse.com.tw/",
}


def _get_json(url: str) -> dict:
    """GET url → parsed JSON, following 307/308 redirects manually."""
    for _ in range(6):
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location", "")
                if loc.startswith("/"):
                    p = urlparse(url)
                    loc = f"{p.scheme}://{p.netloc}{loc}"
                if loc:
                    url = loc
                    continue
            raise
    raise RuntimeError(f"Too many redirects: {url}")


def _twse_net(date: datetime.date) -> dict[str, int]:
    """三大法人買賣超 from TWSE T86. Returns {code: net_shares}."""
    url = (
        f"https://www.twse.com.tw/rwd/zh/fund/T86"
        f"?date={date.strftime('%Y%m%d')}&selectType=ALL&response=json"
    )
    data = _get_json(url)

    if data.get("stat") != "OK" or not data.get("data"):
        return {}

    fields = data.get("fields", [])
    net_col = next((i for i, f in enumerate(fields) if "三大法人" in f), -1)
    if net_col < 0:
        return {}

    result: dict[str, int] = {}
    for row in data["data"]:
        try:
            code = row[0].strip()
            result[code] = int(row[net_col].replace(",", "").replace("+", ""))
        except (ValueError, IndexError):
            pass
    return result


def _tpex_net(date: datetime.date) -> dict[str, int]:
    """三大法人買賣超 from TPEX. Returns {code: net_shares}."""
    minguo = date.year - 1911
    date_str = f"{minguo}/{date.month:02d}/{date.day:02d}"
    url = (
        f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
        f"3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={date_str}&s=0,asc&o=json"
    )
    data = _get_json(url)

    # response: {"tables": [{"data": [...]}, ...]}
    rows: list = []
    tables = data.get("tables", [])
    if tables:
        rows = tables[0].get("data", [])
    if not rows:
        rows = data.get("aaData", [])  # fallback for old format

    result: dict[str, int] = {}
    for row in rows:
        try:
            code = row[0].strip()
            result[code] = int(row[-1].replace(",", "").replace("+", ""))
        except (ValueError, IndexError):
            pass
    return result


def fetch_institutional_net(
    tse_symbols: list[str],
    otc_symbols: list[str],
) -> tuple[dict[str, int], str]:
    """
    Fetch three-major-institutional net buy/sell (shares) for TSE + OTC stocks.
    Returns (net_map, date_used).  Tries today, falls back up to 4 trading days.
    net_map values are in shares (股); divide by 1000 for lots (張).
    """
    today = datetime.date.today()
    all_syms = set(tse_symbols + otc_symbols)

    for delta in range(6):
        d = today - datetime.timedelta(days=delta)
        if d.weekday() >= 5:
            continue

        net: dict[str, int] = {}
        if tse_symbols:
            try:
                net.update(_twse_net(d))
            except Exception:
                pass
        if otc_symbols:
            try:
                net.update(_tpex_net(d))
            except Exception:
                pass

        if any(s in net for s in all_syms):
            return net, d.isoformat()

    return {}, ""
