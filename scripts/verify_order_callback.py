"""
驗證 on_order_state_change 訂單回報格式。

在模擬環境下對 watchlist 第一支股票下買單 + 賣單，
把每個 callback 的 (stat, msg) 完整印出來。
確認 monitor.py 的 _on_order_state 能正確解析。

用法：
    py -3 scripts/verify_order_callback.py
"""
import json
import sys
import threading
import time
from pathlib import Path

from t9fox.broker.credentials import SinopacCredentials
from t9fox.broker.sinopac import SinopacBroker

# ── 載入 watchlist 第一支股票 ──────────────────────────────────────────
wl = Path("watchlist.txt")
symbols = [ln.strip() for ln in wl.read_text(encoding="utf-8").splitlines()
           if ln.strip() and not ln.strip().startswith("#")]
SYMBOL = symbols[0]
print(f"[verify] 測試標的: {SYMBOL}", file=sys.stderr)

received = threading.Event()
messages: list = []


def _safe_dump(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str, indent=2)
    except Exception:
        return repr(obj)


creds = SinopacCredentials.from_env()
assert creds.simulation, "請確認 .env 的 SINOPAC_SIMULATION=true 再執行"
assert creds.has_ca,     "CA 未設定，無法下單"

with SinopacBroker(creds) as broker:

    # ── 訂閱訂單回報 ──────────────────────────────────────────────────
    def _on_order_state(stat, msg):
        print(f"\n{'='*60}")
        print(f"[callback] stat = {stat}")
        print(f"[callback] msg  =\n{_safe_dump(msg)}")
        print('='*60)
        messages.append((stat, msg))

        # 解析驗證（與 monitor.py 相同邏輯）
        try:
            import shioaji.constant as sc  # type: ignore[import]
            order_id = msg["order"]["id"]
            print(f"[parse]  order_id = {order_id}")

            if stat == sc.OrderState.StockDeal:
                for deal in msg.get("deals", []):
                    fill_price = float(deal["price"])
                    fill_qty   = int(deal["quantity"])
                    print(f"[parse]  DEAL  price={fill_price}  qty={fill_qty} shares"
                          f"  ({fill_qty//1000} lots)")

            elif stat == sc.OrderState.StockOrder:
                op_code = msg.get("operation", {}).get("op_code", "?")
                op_msg  = msg.get("operation", {}).get("op_msg", "")
                print(f"[parse]  ORDER op_code={op_code}  op_msg={op_msg}")

        except Exception as e:
            print(f"[parse]  ERROR: {e}  ← monitor.py 需要修正！", file=sys.stderr)

        if len(messages) >= 2:   # 買 + 賣各一個主要事件後解除阻塞
            received.set()

    broker.api.set_order_callback(_on_order_state)

    # ── 取得價格（盤中用快照，盤後用 Parquet 快取昨收）────────────────
    snap = broker.get_snapshot(SYMBOL)
    last_price = snap.get("close") or snap.get("buy_price") or 0.0
    if last_price <= 0:
        from t9fox.data.kbars_cache import load_daily_bt
        df = load_daily_bt(SYMBOL)
        last_price = float(df["close"].iloc[-1]) if not df.empty else 0.0
        print(f"[verify] {SYMBOL} 快照無效（盤後），使用昨收 = {last_price}", file=sys.stderr)
    else:
        print(f"[verify] {SYMBOL} 最新價 = {last_price}", file=sys.stderr)
    assert last_price > 0, f"無法取得 {SYMBOL} 報價"

    # 買單：用略低於市價的限價（確保模擬環境不立即成交也沒關係）
    print(f"\n[verify] 下買單 1張 @ {last_price:.2f} (simulation) ...", file=sys.stderr)
    buy_result = broker.place_stock_order(SYMBOL, "Buy", 1, last_price)
    print(f"[verify] buy  order_id={buy_result.order_id}  status={buy_result.status}",
          file=sys.stderr)

    # 等幾秒讓 callback 進來
    time.sleep(3)

    # 賣單（模擬當沖平倉）
    print(f"\n[verify] 下賣單 1張 @ {last_price:.2f} (simulation) ...", file=sys.stderr)
    sell_result = broker.place_stock_order(SYMBOL, "Sell", 1, last_price)
    print(f"[verify] sell order_id={sell_result.order_id}  status={sell_result.status}",
          file=sys.stderr)

    # 等 callback（最多 10 秒）
    received.wait(timeout=10)
    time.sleep(2)

print(f"\n[verify] 共收到 {len(messages)} 個回報事件")
if not messages:
    print("[verify] NG  未收到任何回報 — 可能需要改 callback 註冊方式或等更長時間")
else:
    print("[verify] OK  callback 有觸發，請對照上方 [parse] 輸出確認欄位名稱")
