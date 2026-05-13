"""
Strategy 3 — 先搶先贏多股回測
每個交易日只交易第一支觸發進場條件的股票（依 watchlist 順序）。
"""
import numpy as np
import pandas as pd
from pathlib import Path
from t9fox.data.kbars_cache import load_daily_bt
from t9fox.constants import DEFAULT_COMMISSION_RATE

COMMISSION = 0.001425   # 0.1425% 每腿
TAX        = 0.0015     # 0.15%  當沖賣出稅
LOTS       = 3
SHARES     = LOTS * 1000
TP_PCT     = 3.0
SL_PCT     = 9.0    # 昨收 -9%，跌停前 1% 離場
FAST       = 20
SLOW       = 60
START      = "2023-01-01"

symbols = [
    ln.strip()
    for ln in open("watchlist.txt", encoding="utf-8")
    if ln.strip() and not ln.strip().startswith("#")
]

# ── 載入並預處理每支股票 ────────────────────────────────────────────
print(f"載入 {len(symbols)} 支股票資料 ...")
dfs: dict[str, pd.DataFrame] = {}
for sym in symbols:
    df = load_daily_bt(sym)
    if df.empty or len(df) < SLOW + 5:
        continue
    df = df.copy()
    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)
    df["ma_fast"]  = close.rolling(FAST, min_periods=FAST).mean()
    df["ma_slow"]  = close.rolling(SLOW, min_periods=SLOW).mean()
    df["vol_ma20"] = volume.rolling(20,   min_periods=20).mean()
    dfs[sym] = df

# ── 取所有標的的聯集交易日，從 START 開始 ──────────────────────────
all_dates = sorted(set(
    d for df in dfs.values()
    for d in df.loc[START:].index
))
print(f"回測區間: {all_dates[0].date()} ~ {all_dates[-1].date()}  ({len(all_dates)} 個交易日)")

# ── 逐日回測 ────────────────────────────────────────────────────────
trades = []
cash   = 1_000_000.0

for dt in all_dates:
    for sym in symbols:   # watchlist 順序 = 先搶先贏優先序
        df = dfs.get(sym)
        if df is None or dt not in df.index:
            continue

        idx = df.index.get_loc(dt)
        if idx < 1:
            continue

        # 前日指標
        maf_prev  = df["ma_fast"].iloc[idx - 1]
        mas_prev  = df["ma_slow"].iloc[idx - 1]
        vma_prev  = df["vol_ma20"].iloc[idx - 1]
        close_prev = float(df["close"].iloc[idx - 1])

        if not (np.isfinite(maf_prev) and np.isfinite(mas_prev) and np.isfinite(vma_prev)):
            continue

        open_px  = float(df["open"].iloc[idx])
        high_px  = float(df["high"].iloc[idx])
        close_px = float(df["close"].iloc[idx])
        vol_today = float(df["volume"].iloc[idx])

        if not (np.isfinite(open_px) and open_px > 0):
            continue

        gap_pct = (open_px - close_prev) / close_prev * 100

        # 五個進場條件
        if not (
            mas_prev < maf_prev          # ① 多頭排列
            and open_px > maf_prev       # ② 開盤突破 MA20
            and close_prev < maf_prev    # ③ 昨收在 MA20 下
            and vol_today > vma_prev     # ④ 放量
            and gap_pct < TP_PCT         # ⑤ 跳空 < 3%
        ):
            continue

        # 進場 → 模擬出場
        low_px  = float(df["low"].iloc[idx])
        target  = open_px * (1 + TP_PCT / 100)
        stop_px = close_prev * (1 - SL_PCT / 100)
        if low_px <= stop_px:
            exit_px     = stop_px
            exit_reason = "SL"
        elif high_px >= target:
            exit_px     = target
            exit_reason = "TP"
        else:
            exit_px     = close_px
            exit_reason = "EOD"

        buy_cost  = open_px * SHARES
        sell_proc = exit_px * SHARES
        fee = buy_cost * COMMISSION + sell_proc * COMMISSION + sell_proc * TAX
        net = (exit_px - open_px) * SHARES - fee
        cash += net

        trades.append({
            "date":   dt.strftime("%Y-%m-%d"),
            "symbol": sym,
            "open":   open_px,
            "exit":   round(exit_px, 2),
            "target": round(target, 2),
            "gap%":   round(gap_pct, 2),
            "reason": exit_reason,
            "net":    round(net),
        })
        break   # ← 先搶先贏：當日只交易這一支

# ── 輸出結果 ────────────────────────────────────────────────────────
df_t = pd.DataFrame(trades)
print()
if df_t.empty:
    print("無交易記錄。")
else:
    print(f"{'日期':10s}  {'代號':6s}  {'買入':>7s}  {'賣出':>7s}  "
          f"{'跳空%':>6s}  {'出場':4s}  {'淨損益':>9s}")
    print("-" * 65)
    for _, r in df_t.iterrows():
        print(f"{r['date']:10s}  {r['symbol']:6s}  {r['open']:>7.2f}  "
              f"{r['exit']:>7.2f}  {r['gap%']:>+5.1f}%  {r['reason']:4s}  "
              f"{r['net']:>+9,}")

    wins = df_t[df_t["net"] > 0]
    print()
    print("=" * 65)
    print(f"總交易筆數 : {len(df_t)}")
    print(f"獲利筆數   : {len(wins)}  ({len(wins)/len(df_t)*100:.1f}%)")
    print(f"停利命中   : {(df_t['reason']=='TP').sum()}  "
          f"({(df_t['reason']=='TP').mean()*100:.1f}%)")
    print(f"停損觸發   : {(df_t['reason']=='SL').sum()}  "
          f"({(df_t['reason']=='SL').mean()*100:.1f}%)")
    print(f"平均每筆   : {df_t['net'].mean():>+,.0f} TWD")
    print(f"合計淨損益 : {df_t['net'].sum():>+,.0f} TWD")
    print(f"最終資產   : {cash:>,.0f} TWD  (起始 1,000,000)")
    print()
    print("【各代號成交統計】")
    summary = (df_t.groupby("symbol")
               .agg(筆數=("net","count"), 淨損益=("net","sum"))
               .sort_values("淨損益", ascending=False))
    for sym, row in summary.iterrows():
        bar = "+" if row["淨損益"] >= 0 else "-"
        print(f"  {sym:6s}  {row['筆數']:2d}筆  {row['淨損益']:>+9,}")
