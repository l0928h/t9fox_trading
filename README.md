# T9FOX Trade

台股（TWSE）資料抓取、策略與回測的 Python CLI；另含靜態 **TradingView 風格** 網頁預覽（`web/netflix-style`）。

## 環境需求

- Python **3.10+**
- Windows 若終端機無法執行 `python`，可改用 **`py -3`**（Python Launcher）。

## 安裝（CLI）

在專案根目錄：

```powershell
py -3 -m pip install -e .
```

安裝後在終端機執行 **`t9fox`**。若找不到指令，請確認已啟用虛擬環境，或使用 `py -3 -m pip install -e .` 後檢查 Python 的 `Scripts` 目錄是否已在 PATH。

## CLI 啟動與常用指令

CLI 為 **一次性命令**，無需長駐程序。

| 用途 | 範例 |
|------|------|
| 抓取日線並寫入快取 | `t9fox fetch 2330 --start 2020-01-01` |
| 同上（指定區間、強制重抓） | `t9fox fetch 2330 --start 2020-01-01 --end 2024-12-31 --refresh` |
| 回測 | `t9fox backtest 2330 --start 2020-01-01` |
| 回測參數 | `t9fox backtest 2330 --start 2020-01-01 --cash 1000000 --fast 10 --slow 30` |
| 本機網頁 + **證交所日線 API** | 見下方 **`t9fox serve`**（建議） |

說明：

```text
t9fox fetch <股票代碼> --start YYYY-MM-DD [--end YYYY-MM-DD] [--refresh]
t9fox backtest <股票代碼> --start YYYY-MM-DD [--end ...] [--strategy ma] [--fast 10] [--slow 30] [--cash ...] [--refresh]
t9fox serve [--host 127.0.0.1] [--port 8765] [--static DIR]
```

## Web 預覽 + 證交所資料（建議）

以 **`t9fox serve`** 同時提供 **TradingView 風格靜態頁** 與 **證交所個股日線 JSON**（底層為 `load_or_fetch_daily_bars` → TWSE `STOCK_DAY`）。

**若畫面出現「偵測不到 /api/twse/daily（404）」**：代表目前網頁是由 **純 `python -m http.server`** 提供的，**沒有**後端 API。請改在**專案根目錄**（含 `pyproject.toml` 的那層）啟動：

- **Windows**：雙擊 **`serve.bat`**，或在 PowerShell 執行 **`.\serve.ps1`**
- 或手動：`py -3 -m t9fox.cli serve`（腳本會設定 `PYTHONPATH=src`，未 `pip install -e .` 時也常能跑）

然後用瀏覽器開 **http://127.0.0.1:8765/**，並可先開 **http://127.0.0.1:8765/api/health** 確認回傳 `"ok": true`。

| 項目 | 內容 |
|------|------|
| 指令 | 專案根目錄：`serve.bat`／`serve.ps1`，或 `py -3 -m t9fox.cli serve`，或安裝後 `t9fox serve` |
| 預設網址 | `http://127.0.0.1:8765/` |
| 證交所 API | `GET /api/twse/daily?symbol=2330&start=YYYY-MM-DD` |
| 健康檢查 | `GET /api/health`（回傳 `{"ok":true,"service":"t9fox-serve"}`，用以區分是否誤用純 `http.server`） |
| 選用參數 | `end`、`refresh=1`（略過快取重抓）、`limit`（只回傳最後 N 筆，減少 JSON 大小） |

範例：

```text
http://127.0.0.1:8765/api/twse/daily?symbol=2330&start=2024-01-01&limit=120
```

首頁載入後會向 **同源** `/api/twse/daily` 取樣（預設 2330、約兩年內最多 520 筆），並以 **K 線圖** 呈現歷史 OHLC（圖表程式透過 CDN 載入 Lightweight Charts）。若只用檔案協定開 `index.html` 而沒有跑 `serve`，瀏覽器無法呼叫 API，畫面上會顯示提示。

### 僅靜態檔（無證交所 API）

若只要樣式、不接後端：

```powershell
cd web\netflix-style
py -3 -m http.server 8765
```

### 停止伺服器

在執行 `t9fox serve`（或 `http.server`）的終端機按 **Ctrl+C**。

## 開發測試（可選）

```powershell
py -3 -m pip install -e ".[dev]"
py -3 -m pytest
```
