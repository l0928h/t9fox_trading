from t9fox.backtest.ma_breakout import MaBreakoutBtParams, backtest_ma_breakout
from t9fox.data.kbars_cache import load_daily_bt

symbols = [ln.strip() for ln in open('watchlist.txt', encoding='utf-8') if ln.strip() and not ln.strip().startswith('#')]
p = MaBreakoutBtParams(
    fast=20, slow=60, take_profit_pct=3.0, lots=3, initial_cash=1_000_000,
    commission_rate=0.001425,   # 0.1425% 每腿，標準無折扣
    sell_tax_rate=0.0015,       # 0.15% 當沖證交稅（減半）
)

total_net = 0.0
total_trades = 0
total_wins = 0

for sym in symbols:
    df = load_daily_bt(sym)
    if df.empty or len(df) < p.slow + 5:
        continue
    df = df.loc['2023-01-01':]
    if len(df) < p.slow + 5:
        continue
    try:
        r = backtest_ma_breakout(df, p)
        if r.trades.empty:
            continue
        net = float(r.trades['net_pnl'].sum())
        n = len(r.trades)
        w = int((r.trades['net_pnl'] > 0).sum())
        total_net += net
        total_trades += n
        total_wins += w
        tag = "+" if net >= 0 else ""
        print(f"{sym:6s}  {n:3d}筆  勝{w:3d}  淨損益 {tag}{net:>10,.0f}")
    except Exception as e:
        print(f"{sym:6s}  ERROR: {e}")

print()
print("=" * 45)
print(f"總交易筆數 : {total_trades}")
if total_trades:
    print(f"總獲利筆數 : {total_wins}  ({total_wins/total_trades*100:.1f}%)")
print(f"合計淨損益 : {'+' if total_net >= 0 else ''}{total_net:,.0f} TWD")
