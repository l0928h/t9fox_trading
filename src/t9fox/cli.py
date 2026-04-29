from __future__ import annotations

import argparse
import sys
from t9fox.backtest.engine import BacktestConfig, run_backtest, simple_metrics
from t9fox.data.twse_daily import load_or_fetch_daily_bars
from t9fox.strategy.ma_crossover import MaCrossParams, ma_crossover_targets


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

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
