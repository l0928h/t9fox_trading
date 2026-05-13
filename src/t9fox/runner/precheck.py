from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class StockReport:
    symbol: str
    prev_close: float
    prev_change: float
    prev_change_pct: float
    high_20d: float
    gap: float
    gap_pct: float


def load_watchlist(path: str | Path) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def _weekday_en(wd: int) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][wd]


def _status_label(gap: float, gap_pct: float) -> str:
    if gap <= 0:
        return "BREAKOUT"
    if gap_pct < 3:
        return "NEAR"
    return "WAIT"


def run_precheck(symbols: list[str], lookback: int = 20) -> None:
    from t9fox.broker.credentials import SinopacCredentials
    from t9fox.broker.sinopac import SinopacBroker
    from t9fox.db.store import upsert_signal
    from t9fox.strategy.breakout import calc_n_day_high_from_broker

    try:
        creds = SinopacCredentials.from_env()
    except (EnvironmentError, FileNotFoundError) as e:
        print(f"Credential error: {e}", file=sys.stderr)
        return

    today = date.today()
    today_str = today.isoformat()
    total = len(symbols)

    print(f"\n{'='*62}")
    print(f"  T9FOX Pre-Market Report  {today} ({_weekday_en(today.weekday())})  {total} symbols")
    print(f"{'='*62}")

    snap_map: dict[str, dict] = {}
    reports: list[StockReport] = []

    with SinopacBroker(creds) as broker:

        # ── Step 1: batch snapshots ────────────────────────────────────
        print(f"  Fetching snapshots ({total} symbols) ...", end="", flush=True)
        try:
            contracts = [broker.api.Contracts.Stocks[s] for s in symbols]
            snaps = broker.api.snapshots(contracts)
            for s in snaps:
                snap_map[s.code] = {
                    "close":      float(s.close),
                    "change":     float(s.change_price),
                    "change_pct": float(s.change_rate),
                }
            print(f" OK ({len(snap_map)} records)")
        except Exception as e:
            print(f" ERROR: {e}", file=sys.stderr)

        # ── Step 2: 20d-high via cached kbars ─────────────────────────
        print(f"  Calculating {lookback}d-high (cached kbars) ...")
        for i, symbol in enumerate(symbols, 1):
            print(f"  [{i:2d}/{total}] {symbol}", end="\r", flush=True)
            snap = snap_map.get(symbol)
            if not snap:
                continue
            try:
                high_20d = calc_n_day_high_from_broker(broker, symbol, lookback)
                gap      = high_20d - snap["close"]
                gap_pct  = gap / high_20d * 100 if high_20d else 0.0
                reports.append(StockReport(
                    symbol=symbol,
                    prev_close=snap["close"],
                    prev_change=snap["change"],
                    prev_change_pct=snap["change_pct"],
                    high_20d=high_20d,
                    gap=gap,
                    gap_pct=gap_pct,
                ))
                # persist to SQLite
                upsert_signal(
                    date=today_str, symbol=symbol,
                    prev_close=snap["close"], chg_pct=snap["change_pct"],
                    high_20d=high_20d, gap=gap, gap_pct=gap_pct,
                    status=_status_label(gap, gap_pct),
                )
            except Exception as e:
                print(f"\n  {symbol}: {e}", file=sys.stderr)

    print(" " * 50, end="\r")

    if not reports:
        print("  No data.\n")
        return

    reports.sort(key=lambda r: r.gap)

    print(f"\n{'Symbol':6s}  {'Close':>8s}  {'Chg':>16s}  {'20d-High':>8s}  {'Gap':>14s}  Status")
    print(f"{'-'*6}  {'-'*8}  {'-'*16}  {'-'*8}  {'-'*14}  {'-'*15}")

    breakout_count = near_count = 0
    for r in reports:
        chg_str = f"{r.prev_change:+.2f}({r.prev_change_pct:+.1f}%)"
        gap_str = f"{r.gap:+.2f}({r.gap_pct:+.1f}%)"
        lbl = _status_label(r.gap, r.gap_pct)
        if lbl == "BREAKOUT":
            status = "*** BREAKOUT ***"
            breakout_count += 1
        elif lbl == "NEAR":
            status = "! Near breakout"
            near_count += 1
        else:
            status = f"Need +{r.gap:.2f}"
        print(f"{r.symbol:6s}  {r.prev_close:>8.2f}  {chg_str:>16s}  "
              f"{r.high_20d:>8.2f}  {gap_str:>14s}  {status}")

    print(f"\n  Total {len(reports)} | Breakout: {breakout_count} | Near(<3%): {near_count}")
    print(f"  Saved to DB: data/cache/t9fox.db")
    print(f"{'='*62}\n")
