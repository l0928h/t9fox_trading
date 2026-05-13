from __future__ import annotations

import argparse
import sys
from t9fox.backtest.engine import BacktestConfig, run_backtest, simple_metrics
from t9fox.data.twse_daily import load_or_fetch_daily_bars
from t9fox.strategy.ma_crossover import MaCrossParams, ma_crossover_targets


def _cmd_report(args: argparse.Namespace) -> int:
    from t9fox.db.store import query_signals, query_trades

    if args.type == "signals":
        rows = query_signals(date=args.date, symbol=args.symbol,
                             status=args.status, limit=args.limit)
        if not rows:
            print("No signals found.")
            return 0
        print(f"\n{'Date':10s}  {'Symbol':6s}  {'Close':>8s}  {'20d-High':>8s}  {'Gap%':>7s}  Status")
        print(f"{'-'*10}  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*15}")
        for r in rows:
            print(f"{r['date']:10s}  {r['symbol']:6s}  {r['prev_close']:>8.2f}  "
                  f"{r['high_20d']:>8.2f}  {r['gap_pct']:>+6.1f}%  {r['status']}")
    else:
        rows = query_trades(symbol=args.symbol, date=args.date, limit=args.limit)
        if not rows:
            print("No trades found.")
            return 0
        print(f"\n{'Date':10s}  {'Symbol':6s}  {'Action':5s}  {'Lots':>4s}  "
              f"{'Price':>8s}  {'Sim':>3s}  Status")
        print(f"{'-'*10}  {'-'*6}  {'-'*5}  {'-'*4}  {'-'*8}  {'-'*3}  {'-'*12}")
        for r in rows:
            sim = "Yes" if r["simulation"] else "No"
            print(f"{r['date']:10s}  {r['symbol']:6s}  {r['action']:5s}  "
                  f"{r['lots']:>4d}  {r['price']:>8.2f}  {sim:>3s}  {r['order_status']}")
    print()
    return 0


def _cmd_fetch_all(args: argparse.Namespace) -> int:
    from t9fox.runner.precheck import load_watchlist
    from t9fox.data.twse_daily import load_or_fetch_daily_bars
    from pathlib import Path

    if args.file:
        symbols = load_watchlist(args.file)
    else:
        default = Path(__file__).resolve().parents[2] / "watchlist.txt"
        symbols = load_watchlist(default) if default.is_file() else []
    if not symbols:
        print("No symbols found.", file=sys.stderr)
        return 1

    print(f"Fetching {len(symbols)} symbols from {args.start} ...")
    ok, fail = 0, 0
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i:2d}/{len(symbols)}] {sym}", end=" ", flush=True)
        try:
            df = load_or_fetch_daily_bars(sym, args.start, refresh=args.refresh)
            print(f"-> {len(df)} rows")
            ok += 1
        except Exception as e:
            print(f"-> ERROR: {e}", file=sys.stderr)
            fail += 1
    print(f"\nDone: {ok} ok, {fail} failed.")
    return 0 if fail == 0 else 1


def _cmd_precheck(args: argparse.Namespace) -> int:
    from t9fox.runner.precheck import run_precheck, load_watchlist
    from pathlib import Path

    if args.file:
        symbols = load_watchlist(args.file)
        if not symbols:
            print(f"No symbols found in {args.file}", file=sys.stderr)
            return 1
    else:
        symbols = args.symbols or []
        if not symbols:
            # auto-detect watchlist.txt in project root
            default = Path(__file__).resolve().parents[2] / "watchlist.txt"
            if default.is_file():
                symbols = load_watchlist(default)
                print(f"(Using {default})")
            else:
                print("Provide symbols or --file, or place watchlist.txt in project root.", file=sys.stderr)
                return 1

    run_precheck(symbols, lookback=args.lookback)
    return 0


def _cmd_auto(args: argparse.Namespace) -> int:
    from t9fox.runner.scheduler import run_daily_loop
    try:
        run_daily_loop(
            symbol=args.symbol,
            lookback=args.lookback,
            lots=args.lots,
            start_time=args.start_time,
            sell_time=args.sell_time,
        )
    except (EnvironmentError, FileNotFoundError) as e:
        print(f"Credential error: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_monitor(args: argparse.Namespace) -> int:
    from t9fox.broker.credentials import SinopacCredentials
    from t9fox.broker.sinopac import SinopacBroker
    from t9fox.strategy.breakout import calc_n_day_high
    from t9fox.runner.monitor import BreakoutDayTrader

    try:
        creds = SinopacCredentials.from_env()
    except (EnvironmentError, FileNotFoundError) as e:
        print(f"Credential error: {e}", file=sys.stderr)
        return 1

    try:
        high = calc_n_day_high(args.symbol, args.lookback)
    except ValueError as e:
        print(f"Data error: {e}", file=sys.stderr)
        return 1

    trader = BreakoutDayTrader(
        symbol=args.symbol,
        n_day_high=high,
        lots=args.lots,
        sell_time=args.sell_time,
    )

    with SinopacBroker(creds) as broker:
        trader.run(broker)

    return 0


def _cmd_connect(args: argparse.Namespace) -> int:
    from t9fox.broker.credentials import SinopacCredentials
    from t9fox.broker.sinopac import SinopacBroker

    try:
        creds = SinopacCredentials.from_env()
    except (EnvironmentError, FileNotFoundError) as e:
        print(f"Credential error: {e}", file=sys.stderr)
        return 1

    print(creds)
    with SinopacBroker(creds) as broker:
        positions = broker.list_positions()
        print(f"Positions ({len(positions)}):")
        if positions:
            for p in positions:
                print(f"  {p.symbol}  qty={p.quantity}  avg={p.avg_price:.2f}  pnl={p.pnl:.2f}")
        else:
            print("  (none)")

        if args.symbol:
            snap = broker.get_snapshot(args.symbol)
            print(f"\nSnapshot {args.symbol}: {snap}")

    return 0


def _cmd_price(args: argparse.Namespace) -> int:
    from pathlib import Path
    from t9fox.broker.credentials import SinopacCredentials
    from t9fox.broker.sinopac import SinopacBroker
    from t9fox.runner.precheck import load_watchlist

    # resolve symbol list
    if args.file:
        symbols = load_watchlist(args.file)
        if not symbols:
            print(f"No symbols in {args.file}", file=sys.stderr)
            return 1
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols]
    else:
        default = Path(__file__).resolve().parents[2] / "watchlist.txt"
        if default.is_file():
            symbols = load_watchlist(default)
            print(f"(Using {default})")
        else:
            print("Provide symbols, --file, or place watchlist.txt in project root.", file=sys.stderr)
            return 1

    try:
        creds = SinopacCredentials.from_env()
    except (EnvironmentError, FileNotFoundError) as e:
        print(f"Credential error: {e}", file=sys.stderr)
        return 1

    with SinopacBroker(creds) as broker:
        contracts = [broker.api.Contracts.Stocks[s] for s in symbols]
        try:
            snaps = broker.api.snapshots(contracts)
        except Exception as e:
            print(f"Snapshot error: {e}", file=sys.stderr)
            return 1

        snap_map = {s.code: s for s in snaps}

        # optionally merge today's signal data from DB (20d-high, gap%)
        sig_map: dict[str, dict] = {}
        try:
            from t9fox.db.store import query_signals
            import datetime
            today = datetime.date.today().isoformat()
            rows = query_signals(date=today, limit=len(symbols) + 10)
            sig_map = {r["symbol"]: r for r in rows}
        except Exception:
            pass

        has_sig = bool(sig_map)
        hdr = f"{'Symbol':6s}  {'Close':>8s}  {'Chg':>14s}  {'Bid':>8s}  {'Ask':>8s}  {'Volume':>10s}"
        if has_sig:
            hdr += f"  {'20d-High':>8s}  {'Gap%':>7s}  Status"
        print(f"\n{hdr}")
        sep = f"{'-'*6}  {'-'*8}  {'-'*14}  {'-'*8}  {'-'*8}  {'-'*10}"
        if has_sig:
            sep += f"  {'-'*8}  {'-'*7}  {'-'*15}"
        print(sep)

        for symbol in symbols:
            s = snap_map.get(symbol)
            if not s:
                print(f"{symbol:6s}  (no data)")
                continue
            chg = float(s.change_price)
            chg_pct = float(s.change_rate)
            sign = "+" if chg >= 0 else ""
            chg_str = f"{sign}{chg:.2f}({sign}{chg_pct:.2f}%)"
            line = (
                f"{symbol:6s}  {float(s.close):>8.2f}  {chg_str:>14s}  "
                f"{float(s.buy_price):>8.2f}  {float(s.sell_price):>8.2f}  "
                f"{int(s.total_volume):>10,}"
            )
            if has_sig and symbol in sig_map:
                r = sig_map[symbol]
                line += f"  {r['high_20d']:>8.2f}  {r['gap_pct']:>+6.1f}%  {r['status']}"
            print(line)
        print(f"\n{len(snap_map)} symbols")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    df = load_or_fetch_daily_bars(
        args.symbol,
        args.start,
        args.end,
        refresh=args.refresh,
    )
    if df.empty:
        print("No data returned.", file=sys.stderr)
        return 1
    print(df.tail(10).to_string())
    print(f"\nrows={len(df)} cache updated")
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    df = load_or_fetch_daily_bars(
        args.symbol,
        args.start,
        args.end,
        refresh=args.refresh,
    )
    if df.empty or len(df) < 5:
        print("Insufficient data for backtest.", file=sys.stderr)
        return 1

    if args.strategy == "ma":
        params = MaCrossParams(fast=args.fast, slow=args.slow)
        targets = ma_crossover_targets(df, params)
    else:
        print(f"Unknown strategy: {args.strategy}", file=sys.stderr)
        return 1

    cfg = BacktestConfig(initial_cash=args.cash)
    result = run_backtest(df, targets, cfg)
    m = simple_metrics(result)
    print(f"Symbol {args.symbol}  {args.start} .. {args.end or 'today'}")
    print(f"Final equity: {result.final_equity:,.2f}")
    print(f"CAGR: {m['cagr']:.4f}  Sharpe: {m['sharpe']:.4f}  MaxDD: {m['max_drawdown']:.4f}")
    print(f"Trades: {len(result.trades)}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from pathlib import Path

    from t9fox.web.server import run_server

    static = Path(args.static).expanduser().resolve() if args.static else None
    run_server(args.host, args.port, static)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="t9fox", description="T9FOX Taiwan stock quant CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser(
        "serve",
        help="HTTP server: web UI + /api/twse/daily (TWSE via load_or_fetch_daily_bars)",
    )
    s.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    s.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    s.add_argument(
        "--static",
        default=None,
        metavar="DIR",
        help="Static root (default: <project>/web/netflix-style, TradingView-style UI)",
    )
    s.set_defaults(func=_cmd_serve)

    f = sub.add_parser("fetch", help="Download TWSE daily bars into cache")
    f.add_argument("symbol", help="Stock code, e.g. 2330")
    f.add_argument("--start", required=True, help="YYYY-MM-DD")
    f.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    f.add_argument("--refresh", action="store_true", help="Ignore cache and refetch")
    f.set_defaults(func=_cmd_fetch)

    b = sub.add_parser("backtest", help="Run backtest on cached/fetched daily data")
    b.add_argument("symbol", help="Stock code, e.g. 2330")
    b.add_argument("--start", required=True, help="YYYY-MM-DD")
    b.add_argument("--end", default=None, help="YYYY-MM-DD")
    b.add_argument("--cash", type=float, default=1_000_000.0, help="Initial cash TWD")
    b.add_argument("--strategy", default="ma", choices=["ma"], help="Strategy id")
    b.add_argument("--fast", type=int, default=10)
    b.add_argument("--slow", type=int, default=30)
    b.add_argument("--refresh", action="store_true")
    b.set_defaults(func=_cmd_backtest)

    rp = sub.add_parser("report", help="Query DB: signal history or trade log")
    rp.add_argument("type", choices=["signals", "trades"], help="Report type")
    rp.add_argument("--symbol", default=None)
    rp.add_argument("--date",   default=None, help="YYYY-MM-DD")
    rp.add_argument("--status", default=None, help="BREAKOUT | NEAR | WAIT (signals only)")
    rp.add_argument("--limit",  type=int, default=100)
    rp.set_defaults(func=_cmd_report)

    fa = sub.add_parser("fetch-all", help="Bulk-fetch TWSE daily data for all symbols in watchlist")
    fa.add_argument("--file", default=None, metavar="FILE", help="Watchlist file (default: watchlist.txt)")
    fa.add_argument("--start", default="2024-01-01", help="Start date YYYY-MM-DD (default: 2024-01-01)")
    fa.add_argument("--refresh", action="store_true", help="Force re-fetch ignoring cache")
    fa.set_defaults(func=_cmd_fetch_all)

    pc = sub.add_parser("precheck", help="Pre-market report: snapshot + 20d-high for watchlist via Sinopac")
    pc.add_argument("symbols", nargs="*", help="Stock codes (optional if --file or watchlist.txt exists)")
    pc.add_argument("--file", default=None, metavar="FILE", help="Watchlist file, one symbol per line (default: watchlist.txt)")
    pc.add_argument("--lookback", type=int, default=20, help="N-day high lookback (default: 20)")
    pc.set_defaults(func=_cmd_precheck)

    au = sub.add_parser("auto", help="Daily loop: sleep until market open, run monitor, repeat each trading day")
    au.add_argument("symbol", help="Stock code, e.g. 6449")
    au.add_argument("--lookback",   type=int, default=20,      help="N-day high lookback (default: 20)")
    au.add_argument("--lots",       type=int, default=1,       help="Lots per trade (default: 1)")
    au.add_argument("--start-time", default="09:00",           help="Session start HH:MM (default: 09:00)")
    au.add_argument("--sell-time",  default="13:20",           help="Force-sell time HH:MM (default: 13:20)")
    au.set_defaults(func=_cmd_auto)

    mo = sub.add_parser("monitor", help="Intraday breakout strategy: buy on N-day high break, sell before close")
    mo.add_argument("symbol", help="Stock code, e.g. 6449")
    mo.add_argument("--lookback", type=int, default=20, help="N-day high lookback (default: 20)")
    mo.add_argument("--lots", type=int, default=1, help="Lots per trade (default: 1)")
    mo.add_argument("--sell-time", default="13:20", help="Force-sell time HH:MM (default: 13:20)")
    mo.set_defaults(func=_cmd_monitor)

    pr = sub.add_parser("price", help="Query real-time snapshot for symbols or whole watchlist")
    pr.add_argument("symbols", nargs="*", help="Stock code(s) (omit to use watchlist.txt)")
    pr.add_argument("--file", default=None, metavar="FILE", help="Watchlist file (default: watchlist.txt)")
    pr.set_defaults(func=_cmd_price)

    cn = sub.add_parser("connect", help="Test Sinopac login, list positions, optionally get snapshot")
    cn.add_argument("--symbol", default=None, help="Stock code to snapshot, e.g. 2330")
    cn.set_defaults(func=_cmd_connect)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
