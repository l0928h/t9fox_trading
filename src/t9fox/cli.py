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


def _cmd_ma_watchlist(args: argparse.Namespace) -> int:
    from pathlib import Path
    from t9fox.broker.credentials import SinopacCredentials
    from t9fox.broker.sinopac import SinopacBroker
    from t9fox.runner.monitor import run_ma_breakout_watchlist
    from t9fox.runner.precheck import load_watchlist

    if args.file:
        symbols = load_watchlist(args.file)
    else:
        default = Path(__file__).resolve().parents[2] / "watchlist.txt"
        symbols = load_watchlist(default) if default.is_file() else []
    if not symbols:
        print("No symbols found.", file=sys.stderr)
        return 1

    try:
        creds = SinopacCredentials.from_env()
    except (EnvironmentError, FileNotFoundError) as e:
        print(f"Credential error: {e}", file=sys.stderr)
        return 1

    with SinopacBroker(creds) as broker:
        run_ma_breakout_watchlist(
            symbols=symbols,
            broker=broker,
            take_profit_pct=args.take_profit,
            lots=args.lots,
            sell_time=args.sell_time,
        )
    return 0


def _cmd_ma_monitor(args: argparse.Namespace) -> int:
    from t9fox.broker.credentials import SinopacCredentials
    from t9fox.broker.sinopac import SinopacBroker
    from t9fox.runner.monitor import MaBreakoutDayTrader

    try:
        creds = SinopacCredentials.from_env()
    except (EnvironmentError, FileNotFoundError) as e:
        print(f"Credential error: {e}", file=sys.stderr)
        return 1

    with SinopacBroker(creds) as broker:
        try:
            trader = MaBreakoutDayTrader.from_broker(
                symbol=args.symbol,
                broker=broker,
                take_profit_pct=args.take_profit,
                lots=args.lots,
                sell_time=args.sell_time,
            )
        except ValueError as e:
            print(f"Data error: {e}", file=sys.stderr)
            return 1

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
    import datetime
    from pathlib import Path
    from t9fox.broker.credentials import SinopacCredentials
    from t9fox.broker.sinopac import SinopacBroker
    from t9fox.runner.precheck import load_watchlist
    from t9fox.data.institutional import fetch_institutional_net

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

        # classify TSE vs OTC from snapshot exchange field
        tse_syms = [s.code for s in snaps if str(getattr(s, "exchange", "")).upper() in ("TSE", "TWSE")]
        otc_syms = [s.code for s in snaps if str(getattr(s, "exchange", "")).upper() in ("OTC", "TPEx", "TPEX")]
        # fallback: treat all as both if exchange not detected
        if not tse_syms and not otc_syms:
            tse_syms, otc_syms = symbols, symbols

        # fetch three-major-institutional net buy/sell
        print("Fetching institutional data ...", end="", flush=True)
        inst_net, inst_date = fetch_institutional_net(tse_syms, otc_syms)
        if inst_net:
            print(f" OK ({inst_date})")
        else:
            print(" (unavailable)")

        # merge today's signal data from DB (20d-high, gap%)
        sig_map: dict[str, dict] = {}
        try:
            from t9fox.db.store import query_signals
            today = datetime.date.today().isoformat()
            rows = query_signals(date=today, limit=len(symbols) + 10)
            sig_map = {r["symbol"]: r for r in rows}
        except Exception:
            pass

        has_sig  = bool(sig_map)
        has_inst = bool(inst_net)

        # header
        hdr = (f"{'Symbol':6s}  {'Open':>8s}  {'Close':>8s}  {'Chg':>14s}  {'Volume':>10s}")
        sep = (f"{'-'*6}  {'-'*8}  {'-'*8}  {'-'*14}  {'-'*10}")
        if has_inst:
            hdr += f"  {'3Inst(lots)':>12s}"
            sep += f"  {'-'*12}"
        if has_sig:
            hdr += f"  {'20d-High':>8s}  {'Gap%':>7s}  Status"
            sep += f"  {'-'*8}  {'-'*7}  {'-'*15}"
        print(f"\n{hdr}\n{sep}")

        for symbol in symbols:
            s = snap_map.get(symbol)
            if not s:
                print(f"{symbol:6s}  (no data)")
                continue
            chg     = float(s.change_price)
            chg_pct = float(s.change_rate)
            sign    = "+" if chg >= 0 else ""
            chg_str = f"{sign}{chg:.2f}({sign}{chg_pct:.2f}%)"
            open_px = float(getattr(s, "open", 0) or 0)
            line = (
                f"{symbol:6s}  {open_px:>8.2f}  {float(s.close):>8.2f}  "
                f"{chg_str:>14s}  {int(s.total_volume):>10,}"
            )
            if has_inst:
                net_shares = inst_net.get(symbol)
                if net_shares is not None:
                    net_lots = net_shares // 1000
                    inst_str = f"{net_lots:>+,}"
                else:
                    inst_str = "-"
                line += f"  {inst_str:>12s}"
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
    # batch mode: read symbols from file
    if args.file:
        from pathlib import Path
        wl = Path(args.file).expanduser()
        if not wl.is_file():
            print(f"File not found: {args.file}", file=sys.stderr)
            return 1
        symbols = [
            ln.strip() for ln in wl.read_text("utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        if not symbols:
            print("No symbols found in file.", file=sys.stderr)
            return 1
        if args.strategy != "ma-breakout":
            print("--file batch mode only supports --strategy ma-breakout", file=sys.stderr)
            return 1
        return _backtest_batch(symbols, args)

    # single-symbol mode
    if not args.symbol:
        print("Provide a symbol or use --file for batch mode.", file=sys.stderr)
        return 1

    if args.sinopac and args.strategy == "ma-breakout":
        from t9fox.broker.credentials import SinopacCredentials
        from t9fox.broker.sinopac import SinopacBroker
        from t9fox.data.kbars_cache import load_kbars_range
        try:
            creds = SinopacCredentials.from_env()
        except (EnvironmentError, FileNotFoundError) as e:
            print(f"Credential error: {e}", file=sys.stderr)
            return 1
        with SinopacBroker(creds) as broker:
            broker.login()
            df = load_kbars_range(broker, args.symbol, args.start, args.end, ma_warmup=90)
        if df.empty or len(df) < 5:
            print("Insufficient data for backtest.", file=sys.stderr)
            return 1
        return _backtest_ma_breakout(df, args)

    df = load_or_fetch_daily_bars(
        args.symbol,
        args.start,
        args.end,
        refresh=args.refresh,
    )
    if df.empty or len(df) < 5:
        print("Insufficient data for backtest.", file=sys.stderr)
        return 1

    if args.strategy == "ma-breakout":
        return _backtest_ma_breakout(df, args)

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


def _backtest_ma_breakout(df, args) -> int:
    from t9fox.backtest.ma_breakout import MaBreakoutBtParams, backtest_ma_breakout

    p = MaBreakoutBtParams(
        fast=args.fast,
        slow=args.slow,
        take_profit_pct=args.take_profit,
        lots=args.lots,
        initial_cash=args.cash,
    )
    result = backtest_ma_breakout(df, p)
    m = result.metrics

    period_end = args.end or "today"
    print(f"\nMA Breakout Backtest  {args.symbol}  {args.start} .. {period_end}")
    print(f"Params: MA{p.fast}/MA{p.slow}  take-profit={p.take_profit_pct:.1f}%  lots={p.lots}")
    print()

    if result.trades.empty:
        print("No trades generated.")
        return 0

    # per-trade table
    print(f"{'Date':10s}  {'Entry':>7s}  {'Exit':>7s}  {'Target':>7s}  "
          f"{'Ret%':>6s}  {'Net PnL':>9s}  {'Reason':12s}")
    print(f"{'-'*10}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*9}  {'-'*12}")
    for _, r in result.trades.iterrows():
        reason_tag = "TP" if r["exit_reason"] == "take_profit" else "EOD"
        ret_str = f"{r['return_pct']:+.2f}%"
        print(
            f"{r['date']:10s}  {r['entry_price']:>7.2f}  {r['exit_price']:>7.2f}  "
            f"{r['target_price']:>7.2f}  {ret_str:>6s}  {r['net_pnl']:>+9,.0f}  {reason_tag}"
        )

    print()
    n  = int(m["n_trades"])
    wr = m["win_rate"] * 100
    tp = m["take_profit_rate"] * 100
    print(f"Trades       : {n}")
    print(f"Win rate     : {wr:.1f}%  (take-profit hit: {tp:.1f}%)")
    print(f"Avg return   : {m['avg_return_pct']:+.2f}%"
          f"  (wins: {m['avg_win_pct']:+.2f}%  losses: {m['avg_loss_pct']:+.2f}%)")
    print(f"Profit factor: {m['profit_factor']:.2f}")
    print(f"Total return : {m['total_return_pct']:+.2f}%")
    print(f"CAGR         : {m['cagr']:+.2f}%")
    print(f"Sharpe       : {m['sharpe']:.2f}")
    print(f"Max drawdown : {m['max_drawdown_pct']:.2f}%")
    final_eq = result.equity_curve.iloc[-1]
    print(f"Final equity : {final_eq:,.0f}  (start: {args.cash:,.0f})")
    return 0


def _backtest_batch(symbols: list, args) -> int:
    """Run ma-breakout backtest on multiple symbols and print a ranked summary."""
    import math
    from datetime import date
    from t9fox.backtest.ma_breakout import MaBreakoutBtParams, backtest_ma_breakout

    p = MaBreakoutBtParams(
        fast=args.fast,
        slow=args.slow,
        take_profit_pct=args.take_profit,
        lots=args.lots,
        initial_cash=args.cash,
    )

    period_end = args.end or "today"
    source = "Sinopac" if args.sinopac else "TWSE"
    print(f"Batch backtest  {len(symbols)} symbols  {args.start} .. {period_end}  [{source}]")
    print(f"Params: MA{p.fast}/MA{p.slow}  take-profit={p.take_profit_pct:.1f}%")
    print()

    broker = None
    if args.sinopac:
        from t9fox.broker.credentials import SinopacCredentials
        from t9fox.broker.sinopac import SinopacBroker
        try:
            creds = SinopacCredentials.from_env()
        except (EnvironmentError, FileNotFoundError) as e:
            print(f"Credential error: {e}", file=sys.stderr)
            return 1
        broker = SinopacBroker(creds)
        broker.login()
        print("  Sinopac login OK")

    rows: list[dict] = []
    errors: list[str] = []

    try:
        for i, sym in enumerate(symbols, 1):
            print(f"  [{i:2d}/{len(symbols)}] {sym} ...", end="", flush=True)
            try:
                if broker:
                    from t9fox.data.kbars_cache import load_kbars_range
                    df = load_kbars_range(broker, sym, args.start, args.end, ma_warmup=90)
                else:
                    from t9fox.data.kbars_cache import load_daily_bt
                    df = load_daily_bt(sym)
                    if df.empty:
                        df = load_or_fetch_daily_bars(sym, args.start, args.end, refresh=args.refresh)
                    else:
                        df = df.loc[args.start: (args.end or str(date.today()))]
                if df.empty or len(df) < p.slow + 5:
                    print(f" skip (only {len(df)} bars)")
                    errors.append(f"{sym}: insufficient data ({len(df)} bars)")
                    continue
                result = backtest_ma_breakout(df, p)
                m = result.metrics
                rows.append({
                    "sym":    sym,
                    "trades": int(m["n_trades"]),
                    "wr":     m["win_rate"] * 100,
                    "tp":     m["take_profit_rate"] * 100,
                    "avg":    m["avg_return_pct"],
                    "pf":     m["profit_factor"],
                    "total":  m["total_return_pct"],
                    "cagr":   m["cagr"],
                    "sharpe": m["sharpe"],
                    "mdd":    m["max_drawdown_pct"],
                })
                print(f" {m['total_return_pct']:+.1f}%  CAGR {m['cagr']:+.1f}%  Sharpe {m['sharpe']:.2f}")
            except Exception as e:
                print(f" ERROR: {e}")
                errors.append(f"{sym}: {e}")
    finally:
        if broker:
            try:
                broker.logout()
            except Exception:
                pass

    if not rows:
        print("\nNo results.")
        return 1

    # sort by total return descending
    rows.sort(key=lambda r: r["total"], reverse=True)

    print()
    print(f"{'Rank':4s}  {'Sym':6s}  {'#':>4s}  {'WR%':>5s}  {'TP%':>5s}  "
          f"{'AvgRet':>7s}  {'PF':>5s}  {'Total':>7s}  {'CAGR':>7s}  "
          f"{'Sharpe':>6s}  {'MDD':>7s}")
    print(f"{'-'*4}  {'-'*6}  {'-'*4}  {'-'*5}  {'-'*5}  "
          f"{'-'*7}  {'-'*5}  {'-'*7}  {'-'*7}  "
          f"{'-'*6}  {'-'*7}")

    def _fmt(v, fmt=".1f", suffix=""):
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return "  N/A"
        return f"{v:{fmt}}{suffix}"

    for rank, r in enumerate(rows, 1):
        marker = " <--" if rank == 1 else ("" if rank > 3 else "")
        pf_str = "inf" if not math.isfinite(r["pf"]) else f"{r['pf']:.2f}"
        print(
            f"{rank:4d}  {r['sym']:6s}  {r['trades']:>4d}  "
            f"{r['wr']:>4.1f}%  {r['tp']:>4.1f}%  "
            f"{r['avg']:>+6.2f}%  {pf_str:>5s}  "
            f"{r['total']:>+6.1f}%  {_fmt(r['cagr'], '+.1f', '%'):>7s}  "
            f"{_fmt(r['sharpe'], '.2f'):>6s}  "
            f"{r['mdd']:>+6.1f}%"
            f"{marker}"
        )

    # summary stats across all symbols
    totals = [r["total"] for r in rows]
    winners = [r for r in rows if r["total"] > 0]
    print()
    print(f"Profitable symbols : {len(winners)}/{len(rows)}")
    print(f"Avg total return   : {sum(totals)/len(totals):+.1f}%")
    best  = rows[0]
    worst = rows[-1]
    print(f"Best               : {best['sym']}  {best['total']:+.1f}%")
    print(f"Worst              : {worst['sym']}  {worst['total']:+.1f}%")

    if errors:
        print(f"\nSkipped ({len(errors)}): {', '.join(e.split(':')[0] for e in errors)}")

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
    b.add_argument("symbol", nargs="?", default=None, help="Stock code (omit when using --file)")
    b.add_argument("--file", "-f", default=None, metavar="FILE",
                   help="Batch mode: read symbols from file (e.g. watchlist.txt)")
    b.add_argument("--start", required=True, help="YYYY-MM-DD")
    b.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    b.add_argument("--cash", type=float, default=1_000_000.0, help="Initial cash TWD (default: 1,000,000)")
    b.add_argument("--strategy", default="ma-breakout", choices=["ma", "ma-breakout"],
                   help="Strategy: ma-breakout (default) or ma (MA crossover)")
    b.add_argument("--fast", type=int, default=20, help="Fast MA period (default: 20)")
    b.add_argument("--slow", type=int, default=60, help="Slow MA period (default: 60)")
    b.add_argument("--take-profit", type=float, default=3.0, metavar="PCT",
                   help="Take-profit %% for ma-breakout (default: 3.0)")
    b.add_argument("--lots", type=int, default=1, help="Lots per trade (default: 1)")
    b.add_argument("--refresh", action="store_true", help="Ignore cache and re-fetch TWSE data")
    b.add_argument("--sinopac", action="store_true",
                   help="Use Sinopac API for data (supports both TWSE and OTC; requires .env credentials)")
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

    mm = sub.add_parser("ma-monitor", help="MA-breakout strategy: entry when open > MA20 and MA60 < MA20, take profit at +X%")
    mm.add_argument("symbol", help="Stock code, e.g. 6449")
    mm.add_argument("--take-profit", type=float, default=3.0, metavar="PCT", help="Take-profit %% (default: 3.0)")
    mm.add_argument("--lots", type=int, default=1, help="Lots per trade (default: 1)")
    mm.add_argument("--sell-time", default="13:20", help="Force-sell time HH:MM (default: 13:20)")
    mm.set_defaults(func=_cmd_ma_monitor)

    mw = sub.add_parser("ma-watchlist", help="Strategy 3: 掃描整個 watchlist，符合條件同時監控下單（當沖）")
    mw.add_argument("--file", "-f", default=None, metavar="FILE", help="Watchlist 檔案（預設 watchlist.txt）")
    mw.add_argument("--take-profit", type=float, default=3.0, metavar="PCT", help="停利 %% (預設 3.0)")
    mw.add_argument("--lots", type=int, default=1, help="每筆張數（預設 1）")
    mw.add_argument("--sell-time", default="13:20", help="強制平倉時間 HH:MM（預設 13:20）")
    mw.set_defaults(func=_cmd_ma_watchlist)

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
