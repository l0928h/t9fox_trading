# T9FOX 交易操作說明

## 目錄
1. [環境設定](#1-環境設定)
2. [上線前檢查清單](#2-上線前檢查清單)
3. [每日交易流程](#3-每日交易流程)
4. [策略3說明](#4-策略3說明)
5. [指令參考](#5-指令參考)
6. [日誌說明](#6-日誌說明)
7. [異常處理](#7-異常處理)

---

## 1. 環境設定

### 1.1 安裝套件

```bash
pip install -e ".[sinopac]"
```

### 1.2 設定 `.env`

複製範本後填入憑證：

```bash
copy .env.example .env
```

`.env` 必填欄位：

```env
# 永豐 API 金鑰（從永豐金 API 後台取得）
SINOPAC_API_KEY=你的_API_KEY
SINOPAC_SECRET_KEY=你的_SECRET_KEY

# 交易模式：true = 模擬  false = 正式交易
SINOPAC_SIMULATION=true

# CA 憑證（下單必須）
SINOPAC_CA_PATH=C:\ekey\551\K121821652\S\Sinopac.pfx
SINOPAC_CA_PASSWD=你的憑證密碼
SINOPAC_PERSON_ID=你的身分證字號
```

### 1.3 確認連線

```bash
py -3 -m t9fox.cli connect
```

輸出應包含：
```
[sinopac] CA activated: True
Positions (0): (none)
```

---

## 2. 上線前檢查清單

正式交易前必須完成以下項目：

| 項目 | 指令/說明 | 狀態 |
|------|-----------|------|
| CA 憑證已啟用 | `connect` 看到 `CA activated: True` | 已完成 |
| 帳戶簽署開通 | 聯絡永豐金客服確認 API 下單開通 | 待確認 |
| 當沖資格 | 帳戶已具備 | 已完成 |
| 模擬下單驗證 | `py -3 scripts/verify_order_callback.py` | 待執行 |
| 正式模式切換 | `.env` 改 `SINOPAC_SIMULATION=false` | 待執行 |

---

## 3. 每日交易流程

### 盤前（08:00 - 08:55）

**步驟一：更新 K 棒快取**

```bash
py -3 -m t9fox.cli fetch-all --start 2023-01-01
```

**步驟二：查看即時報價與訊號**

```bash
py -3 -m t9fox.cli price
```

輸出範例：
```
Symbol    Open    Close         Chg      Volume  3Inst(lots)
------  ------  -------  ----------  ---------  -----------
2492    95.00    97.00  +2.00(+2.1%)   15,234         +123
3152   180.00   185.00  +5.00(+2.8%)    8,901          +56
```

**步驟三：確認今日進場候選**

```bash
py -3 -m t9fox.cli precheck
```

輸出範例：
```
2492  MA20=94.50  MA60=88.20  昨收=93.10  [進場候選]
3152  MA20=178.00  MA60=165.00  昨收=175.00  [進場候選]
6568  MA20=162.00  MA60=158.00  昨收=164.00  [條件未過]
```

---

### 開盤（09:00 - 13:30）

**啟動策略3 Watchlist 監控（主要指令）：**

```bash
py -3 -m t9fox.cli ma-watchlist
```

常用參數：

```bash
# 指定張數
py -3 -m t9fox.cli ma-watchlist --lots 3

# 調整停利
py -3 -m t9fox.cli ma-watchlist --take-profit 3.0

# 調整強制平倉時間（預設 13:20）
py -3 -m t9fox.cli ma-watchlist --sell-time 13:00

# 使用其他 watchlist 檔案
py -3 -m t9fox.cli ma-watchlist --file my_watchlist.txt
```

啟動後輸出範例：
```
[watchlist] 計算訊號中，共 51 支 ...
[09:00:01][2492] MA20=94.50  MA60=88.20  昨收=93.10  [進場候選]
[09:00:01][3152] MA20=178.00  MA60=165.00  昨收=175.00  [進場候選]
[watchlist] 進場候選: 5/51 支

[09:00:05][2492] ENTRY  last=96.00 > MA20=94.50  target=98.88  → BUY 1 lot @ 96.00
[09:00:05][2492] Order sent  id=001234  (awaiting fill)
[09:00:05][2492] FILLED  1lot @ avg 96.00  position confirmed
[09:00:05][2492] 今日已下單，其他標的停止進場
...
[09:30:12][2492] TAKE-PROFIT  last=98.88  sell@98.5(-1tick) → SELL 1 lot @ 98.50
[09:30:12][2492] SELL CONFIRMED  order=001235  position cleared
```

按 `Ctrl-C` 中斷監控。

---

### 盤後（13:30 之後）

**查看今日交易記錄：**

```bash
py -3 -m t9fox.cli report trades --date 2026-05-13
```

---

## 4. 策略3說明

### 進場條件（同時滿足）

| # | 條件 | 說明 |
|---|------|------|
| ① | MA60 < MA20 | 季線在月線下方（多頭排列）|
| ② | 昨收 < MA20 | 昨天收盤尚未突破月線 |
| ③ | 開盤 > MA20 | 今日開盤突破月線 |
| ④ | 跳空 < 3% | 開盤漲幅小於停利幅度 |

### 出場條件（依優先序）

| 優先 | 條件 | 說明 |
|------|------|------|
| 1 | 停損：price ≤ 昨收 × 91% | 跌至跌停前 1% 出場 |
| 2 | 停利：price ≥ 買入 × 103% | 漲 3% 出場 |
| 3 | 強制平倉：13:20 | 當沖收盤前強制賣出 |

### 先搶先贏邏輯

同一天內，Watchlist 中**第一支**觸發進場條件的股票買入後，當天其他股票不再進場。

### 回測績效（2023-2026，51 支，1 張）

- 交易筆數：167 筆
- 勝率：64.1%
- 停利命中：78 筆（46.7%）
- 停損觸發：0 筆（9% 停損幾乎不觸發）
- 合計淨損益：**+343,719 TWD**

---

## 5. 指令參考

### 連線測試

```bash
py -3 -m t9fox.cli connect [--symbol 2330]
```

### 即時報價

```bash
py -3 -m t9fox.cli price                    # 整個 watchlist
py -3 -m t9fox.cli price 2330 2454 6206     # 指定標的
```

### 盤前掃描

```bash
py -3 -m t9fox.cli precheck                 # 整個 watchlist
py -3 -m t9fox.cli precheck 2330 2454       # 指定標的
```

### 策略3 Watchlist 交易（主要交易指令）

```bash
py -3 -m t9fox.cli ma-watchlist [--lots N] [--take-profit PCT] [--sell-time HH:MM]
```

### 策略3 單支股票監控

```bash
py -3 -m t9fox.cli ma-monitor 2492 [--lots 1] [--take-profit 3.0]
```

### 回測

```bash
# 單支股票
py -3 -m t9fox.cli backtest 2492 --start 2023-01-01

# 批次回測整個 watchlist
py -3 -m t9fox.cli backtest --file watchlist.txt --start 2023-01-01

# 使用 Sinopac 資料（含 OTC 股）
py -3 -m t9fox.cli backtest --file watchlist.txt --start 2023-01-01 --sinopac
```

### 查詢記錄

```bash
py -3 -m t9fox.cli report trades                      # 所有交易
py -3 -m t9fox.cli report trades --date 2026-05-13   # 指定日期
py -3 -m t9fox.cli report signals                     # 訊號記錄
```

### 更新 K 棒快取

```bash
py -3 -m t9fox.cli fetch-all                          # 更新整個 watchlist
py -3 -m t9fox.cli fetch 2492 --start 2023-01-01      # 更新單支
```

### 驗證訂單回報格式

```bash
py -3 scripts/verify_order_callback.py
```

---

## 6. 日誌說明

### 關鍵日誌訊息

| 訊息 | 說明 |
|------|------|
| `[進場候選]` | 盤前條件①②已通過，等待開盤確認③④ |
| `ENTRY ... → BUY` | 開盤條件③④通過，下買單 |
| `Order sent (awaiting fill)` | 買單已送出，等待交易所成交回報 |
| `FILLED N lot @ avg XX.XX` | 買單已成交，倉位確認 |
| `TAKE-PROFIT ... sell@XX(-1tick)` | 停利觸發，掛賣單（低 1 檔） |
| `STOP-LOSS ...` | 停損觸發（跌至跌停前 1% 出場） |
| `FORCE-SELL` | 13:20 強制平倉 |
| `SELL CONFIRMED` | 賣單已成交，倉位清空 |
| `今日已下單，其他標的停止進場` | 先搶先贏：當日只成交一筆 |

### 日誌格式

```
[HH:MM:SS][股票代號] 訊息內容
```

---

## 7. 異常處理

### CA 未啟用（Please sign first）

```
{'detail': 'Please sign XXXXXXXXXX first.'}
```

**處理：** 聯絡永豐金客服（0800-095-099），確認 API 程式交易帳戶已完整開通。

### 買單成交後 FILLED 未出現

回報 callback 可能未正確觸發。先執行驗證腳本確認格式：

```bash
py -3 scripts/verify_order_callback.py
```

### 賣單掛出後未成交

賣單採「現價 -1 檔」限價。若股價急跌至跌停，限價單可能無法成交。此時：
1. 手動登入永豐金 App 確認持倉
2. 手動以跌停價掛賣單
3. 次日按正常程序處理（已非當沖）

### Ctrl-C 中斷後持倉殘留

若強制中斷時已有持倉：
1. 立即登入永豐金 App 確認倉位
2. 手動平倉
3. 查詢記錄確認狀態：

```bash
py -3 -m t9fox.cli report trades --date 今日日期
```

### 連線中斷

API 斷線後系統不會自動重連。重新執行 `ma-watchlist` 即可；但盤中斷線若已有持倉，需先確認倉位再重啟。
