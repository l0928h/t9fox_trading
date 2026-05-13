from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta

_MARKET_OPEN_H,  _MARKET_OPEN_M  = 9,  0
_MARKET_CLOSE_H, _MARKET_CLOSE_M = 13, 35   # stop monitoring after this


def _next_session_start(start_h: int, start_m: int) -> datetime:
    """Return the next datetime at which we should begin monitoring."""
    now   = datetime.now()
    today = now.date()

    # Build candidate: today at start_time
    candidate = datetime(today.year, today.month, today.day, start_h, start_m)

    # If today's session hasn't started yet, use today
    if now < candidate and today.weekday() < 5:
        return candidate

    # Otherwise advance to next weekday
    next_day = today + timedelta(days=1)
    while next_day.weekday() >= 5:          # skip Sat(5) Sun(6)
        next_day += timedelta(days=1)

    return datetime(next_day.year, next_day.month, next_day.day, start_h, start_m)


def run_daily_loop(
    symbol: str,
    lookback: int,
    lots: int,
    start_time: str,
    sell_time: str,
) -> None:
    """
    Infinite loop: sleep until market open → run BreakoutDayTrader → repeat.
    Weekends are skipped automatically.
    Press Ctrl-C to exit.
    """
    from t9fox.broker.credentials import SinopacCredentials
    from t9fox.broker.sinopac import SinopacBroker
    from t9fox.runner.monitor import BreakoutDayTrader
    from t9fox.strategy.breakout import calc_n_day_high

    start_h, start_m = map(int, start_time.split(":"))
    creds = SinopacCredentials.from_env()

    print(f"[auto] Symbol={symbol}  lookback={lookback}d  lots={lots}  "
          f"start={start_time}  sell={sell_time}")
    print("[auto] Running daily loop. Press Ctrl-C to exit.\n")

    while True:
        next_start = _next_session_start(start_h, start_m)
        wait_secs  = (next_start - datetime.now()).total_seconds()

        if wait_secs > 0:
            h, m = divmod(int(wait_secs), 3600)
            m, s = divmod(m, 60)
            print(f"[auto] Next session: {next_start.strftime('%Y-%m-%d %H:%M')}"
                  f"  (sleeping {h:02d}h {m:02d}m {s:02d}s)", flush=True)
            try:
                time.sleep(wait_secs)
            except KeyboardInterrupt:
                print("\n[auto] Stopped by user.")
                return

        # ── run today's session ────────────────────────────────────────
        today_str = date.today().isoformat()
        print(f"\n[auto] ===== Session {today_str} =====")
        try:
            high = calc_n_day_high(symbol, lookback)
            trader = BreakoutDayTrader(
                symbol=symbol,
                n_day_high=high,
                lots=lots,
                sell_time=sell_time,
            )
            with SinopacBroker(creds) as broker:
                trader.run(broker)
        except KeyboardInterrupt:
            print("\n[auto] Stopped by user.")
            return
        except Exception as exc:
            print(f"[auto] Session error: {exc}", file=sys.stderr)

        print(f"[auto] Session {today_str} ended. Waiting for next session …\n")
        time.sleep(30)   # brief pause before recalculating next_start
