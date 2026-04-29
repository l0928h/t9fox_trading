/**
 * TWSE historical daily OHLCV → quote strip + candlestick chart (Lightweight Charts).
 * Depends on t9fox serve (same origin /api). Chart script from CDN (unpkg).
 */

function rowsToCandles(rows) {
  const out = [];
  for (const row of rows) {
    const time = String(row.date).slice(0, 10);
    const open = Number(row.open);
    const high = Number(row.high);
    const low = Number(row.low);
    const close = Number(row.close);
    if (!time || [open, high, low, close].some((x) => !Number.isFinite(x))) continue;
    out.push({ time, open, high, low, close });
  }
  out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  const byTime = new Map();
  for (const c of out) byTime.set(c.time, c);
  return [...byTime.values()].sort((a, b) => (a.time < b.time ? -1 : 1));
}

function fillOhlcTable(rows, maxRows) {
  const wrap = document.getElementById("ohlc-wrap");
  const tbody = document.getElementById("ohlc-tbody");
  if (!wrap || !tbody) return;

  const slice = rows.slice(-maxRows).reverse();
  tbody.replaceChildren();
  for (const row of slice) {
    const tr = document.createElement("tr");
    const d = String(row.date).slice(0, 10);
    const fmt = (v) => (Number.isFinite(Number(v)) ? Number(v).toLocaleString("zh-TW") : "—");
    tr.innerHTML = `<td>${d}</td><td>${fmt(row.open)}</td><td>${fmt(row.high)}</td><td>${fmt(row.low)}</td><td>${fmt(row.close)}</td><td>${fmt(row.volume)}</td>`;
    tbody.appendChild(tr);
  }
  wrap.hidden = false;
}

let chartInstance = null;
let chartResizeObserver = null;

function renderCandleChart(candles) {
  const container = document.getElementById("chart-container");
  const hint = document.getElementById("chart-hint");
  if (!container) return;

  if (typeof LightweightCharts === "undefined") {
    if (hint) {
      hint.textContent =
        "圖表程式未載入（請確認可連線 unpkg）。已於下方表格顯示相同日線資料。";
    }
    return;
  }

  if (chartResizeObserver) {
    chartResizeObserver.disconnect();
    chartResizeObserver = null;
  }
  if (chartInstance) {
    chartInstance.remove();
    chartInstance = null;
  }

  container.replaceChildren();

  const w = Math.max(120, container.clientWidth);
  const h = Math.max(200, container.clientHeight || 400);

  const chart = LightweightCharts.createChart(container, {
    width: w,
    height: h,
    layout: {
      background: { type: LightweightCharts.ColorType.Solid, color: "#131722" },
      textColor: "#d1d4dc",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: "rgba(54, 58, 69, 0.6)" },
      horzLines: { color: "rgba(54, 58, 69, 0.6)" },
    },
    rightPriceScale: {
      borderColor: "#363a45",
      scaleMargins: { top: 0.08, bottom: 0.12 },
    },
    timeScale: {
      borderColor: "#363a45",
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: {
      vertLine: { color: "#758696", width: 1, style: 2 },
      horzLine: { color: "#758696", width: 1, style: 2 },
    },
  });

  const series = chart.addCandlestickSeries({
    upColor: "#089981",
    downColor: "#f23645",
    borderDownColor: "#f23645",
    borderUpColor: "#089981",
    wickDownColor: "#f23645",
    wickUpColor: "#089981",
  });

  series.setData(candles);
  chart.timeScale().fitContent();
  chartInstance = chart;

  chartResizeObserver = new ResizeObserver((entries) => {
    for (const ent of entries) {
      const cr = ent.contentRect;
      const nw = Math.max(120, Math.floor(cr.width));
      const nh = Math.max(200, Math.floor(cr.height));
      chart.applyOptions({ width: nw, height: nh });
    }
  });
  chartResizeObserver.observe(container);

  if (hint) {
    hint.textContent = `共 ${candles.length} 個交易日（TWSE STOCK_DAY）。可拖曳平移、滾輪縮放時間軸。`;
  }
}

function showLoadError(el, hintEl, message) {
  if (el) el.textContent = message;
  if (hintEl) hintEl.textContent = message;
}

(function loadTwseDailyAndChart() {
  const el = document.getElementById("twse-quote");
  const titleEl = document.getElementById("chart-title");
  const hint = document.getElementById("chart-hint");

  const start = new Date();
  start.setFullYear(start.getFullYear() - 2);
  const startStr = start.toISOString().slice(0, 10);
  const symbol = "2330";
  const url = `/api/twse/daily?symbol=${symbol}&start=${encodeURIComponent(startStr)}&limit=520`;

  fetch(url)
    .then(async (r) => {
      if (r.status === 404) {
        throw new Error(
          "偵測不到 /api/twse/daily（404）。請在**專案根目錄**執行：py -3 -m t9fox.cli serve（或 t9fox serve），再用 http://127.0.0.1:8765/ 開啟。不要只用 cd web\\netflix-style 後執行 python -m http.server，該方式沒有資料 API。",
        );
      }
      const ct = r.headers.get("content-type") || "";
      const raw = await r.text();
      if (!r.ok) {
        let detail = raw.slice(0, 240);
        try {
          const j = JSON.parse(raw);
          if (j.error) detail = j.error;
        } catch {
          /* ignore */
        }
        throw new Error(`伺服器回應 ${r.status}：${detail || "無內容"}`);
      }
      if (!ct.includes("application/json")) {
        throw new Error("回應不是 JSON。請確認使用 t9fox serve 開啟此頁。");
      }
      return JSON.parse(raw);
    })
    .then((data) => {
      if (!data.rows?.length) {
        const msg =
          "證交所回傳 0 筆日線：可能區間內無資料、證交所暫時無法連線，或網路/封鎖導致。可試 CLI：py -3 -m t9fox.cli fetch 2330 --start 2024-01-01。若快取異常可加 API 參數 refresh=1 重抓。";
        showLoadError(el, hint, msg);
        return;
      }

      if (titleEl) {
        titleEl.textContent = `${data.symbol} · 日線 OHLC（證交所歷史 · ${data.rows.length} 筆）`;
      }

      const last = data.rows[data.rows.length - 1];
      const first = data.rows[0];
      const prev = data.rows.length >= 2 ? data.rows[data.rows.length - 2] : null;
      const close = Number(last.close);
      const prevClose = prev != null ? Number(prev.close) : null;
      const dAbs = prevClose != null ? close - prevClose : null;
      const dPct = prevClose != null && prevClose !== 0 ? (100 * (close - prevClose)) / prevClose : null;

      if (el) {
        const base = document.createElement("span");
        base.textContent = `證交所日線 ${data.symbol}　最新 ${close.toLocaleString("zh-TW")}（${String(last.date).slice(0, 10)}）　區間 ${String(first.date).slice(0, 10)}～${String(last.date).slice(0, 10)}　${data.rows.length} 筆　TWSE STOCK_DAY`;

        el.replaceChildren(base);

        if (dAbs != null && dPct != null && Number.isFinite(dAbs) && Number.isFinite(dPct)) {
          const sign = dAbs >= 0 ? "+" : "";
          const ch = document.createElement("span");
          ch.className = dAbs >= 0 ? "tv-up" : "tv-down";
          ch.textContent = ` ${sign}${dAbs.toLocaleString("zh-TW", { maximumFractionDigits: 2 })} (${sign}${dPct.toFixed(2)}%)`;
          el.appendChild(ch);
        }
      }

      fillOhlcTable(data.rows, 25);

      const candles = rowsToCandles(data.rows);
      if (candles.length) {
        try {
          renderCandleChart(candles);
        } catch (e) {
          console.error(e);
          if (hint) {
            hint.textContent = `K 線繪製失敗：${e instanceof Error ? e.message : String(e)}。下方表格仍有原始資料。`;
          }
        }
      }
    })
    .catch((e) => {
      console.error(e);
      const msg =
        e instanceof Error
          ? e.message
          : `無法載入：${String(e)}。請確認已用 t9fox serve 啟動，並可開啟 http://127.0.0.1:8765/api/health 看到 ok: true。`;
      showLoadError(el, hint, msg);
    });
})();

document.querySelectorAll(".row__track").forEach((track) => {
  track.addEventListener(
    "wheel",
    (e) => {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      if (track.scrollWidth <= track.clientWidth) return;
      e.preventDefault();
      track.scrollLeft += e.deltaY;
    },
    { passive: false },
  );
});
