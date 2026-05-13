/* T9FOX — TradingView-style watchlist dashboard */
'use strict';

// ── State ──────────────────────────────────────────────────────
const state = {
  signals:   [],      // from /api/signals
  snap:      {},      // symbol → { open, close, change_price, change_rate, volume }
  symbols:   [],      // from /api/watchlist
  active:    null,    // currently selected symbol
  chartDays: 60,
  sortCol:   'gap_pct',
  sortAsc:   true,
  chart:     null,
  resizeObs: null,
};

// ── Utilities ──────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const fmt2  = n => Number.isFinite(+n) ? (+n).toFixed(2) : '—';
const fmtK  = n => Number.isFinite(+n) ? (+n).toLocaleString('zh-TW') : '—';
const sign  = n => n > 0 ? '+' : '';

function pctClass(v) {
  if (v > 0) return 'chg-up';
  if (v < 0) return 'chg-down';
  return 'chg-flat';
}

function statusPill(s) {
  const cls = s === 'BREAKOUT' ? 'pill-breakout' : s === 'NEAR' ? 'pill-near' : 'pill-wait';
  const lbl = s === 'BREAKOUT' ? 'BREAK' : s === 'NEAR' ? 'NEAR' : 'WAIT';
  return `<span class="status-pill ${cls}">${lbl}</span>`;
}

function setLastUpdate(src) {
  const el = $('last-update');
  if (!el) return;
  const t = new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  el.textContent = `${t}  ${src}`;
  el.classList.toggle('live', src === 'LIVE' || src === 'tick');
}

// ── Watchlist sidebar ──────────────────────────────────────────
function buildWatchlist() {
  const panel  = $('watchlist-panel');
  const search = $('search-input');
  if (!panel || !state.signals.length) return;

  const groups = [
    { key: 'BREAKOUT', label: 'Breakout', dot: 'dot-breakout' },
    { key: 'NEAR',     label: 'Near',     dot: 'dot-near'     },
    { key: 'WAIT',     label: 'Wait',     dot: 'dot-wait'     },
  ];

  const query = (search ? search.value.trim().toLowerCase() : '');

  // merge snap prices into signals
  const rows = state.signals.map(r => ({
    ...r,
    _close: state.snap[r.symbol]?.close ?? r.prev_close,
    _chg:   state.snap[r.symbol]?.change_rate ?? null,
  }));

  const byStatus = {};
  for (const g of groups) byStatus[g.key] = [];
  for (const r of rows) {
    const st = r.status || 'WAIT';
    if (!byStatus[st]) byStatus[st] = [];
    byStatus[st].push(r);
  }

  panel.innerHTML = '';

  for (const g of groups) {
    const items = byStatus[g.key] || [];
    if (!items.length) continue;

    const grp = document.createElement('div');
    grp.className = 'wl-group';
    grp.innerHTML = `<div class="wl-group-header"><span class="wl-group-dot ${g.dot}"></span>${g.label} (${items.length})</div>`;

    for (const r of items) {
      const show = !query || r.symbol.includes(query);
      const chgPct = r._chg;
      const chgStr = chgPct != null ? `${sign(chgPct)}${fmt2(chgPct)}%` : '—';
      const chgCls = chgPct != null ? pctClass(chgPct) : 'chg-flat';
      const badgeCls = r.status === 'BREAKOUT' ? 'badge-breakout' : r.status === 'NEAR' ? 'badge-near' : 'badge-wait';
      const badgeTxt = r.status === 'BREAKOUT' ? '▲' : r.status === 'NEAR' ? '~' : '';

      const row = document.createElement('div');
      row.className = `wl-row${state.active === r.symbol ? ' active' : ''}${show ? '' : ' hidden'}`;
      row.dataset.symbol = r.symbol;
      row.innerHTML = `
        <span class="wl-sym">${r.symbol}</span>
        <span class="wl-price">${fmt2(r._close)}</span>
        <span class="wl-chg ${chgCls}">${chgStr}</span>
        ${badgeTxt ? `<span class="wl-badge ${badgeCls}">${badgeTxt}</span>` : ''}
      `;
      row.addEventListener('click', () => selectSymbol(r.symbol));
      grp.appendChild(row);
    }

    panel.appendChild(grp);
  }
}

// ── Symbol selection ───────────────────────────────────────────
function selectSymbol(symbol) {
  state.active = symbol;
  buildWatchlist();
  updateTableActiveRow();
  loadChart(symbol);
  updateChartHeader(symbol);
}

function updateChartHeader(symbol) {
  $('chart-symbol').textContent = symbol;
  const sn = state.snap[symbol];
  const sg = state.signals.find(r => r.symbol === symbol);
  const close = sn?.close ?? sg?.prev_close;
  const rate  = sn?.change_rate ?? sg?.chg_pct;

  if (close != null) {
    $('chart-price').textContent = fmt2(close);
    $('chart-price').className = 'chart-price ' + (rate > 0 ? 'td-up' : rate < 0 ? 'td-down' : '');
  } else {
    $('chart-price').textContent = '';
  }
  if (rate != null) {
    $('chart-change').textContent = `${sign(rate)}${fmt2(rate)}%`;
    $('chart-change').className = 'chart-chg ' + pctClass(rate);
  } else {
    $('chart-change').textContent = '';
  }
}

// ── Chart ──────────────────────────────────────────────────────
function loadChart(symbol) {
  const end   = new Date().toISOString().slice(0, 10);
  const start = new Date(Date.now() - state.chartDays * 86400000 * 1.5)
                  .toISOString().slice(0, 10);

  const url = `/api/twse/daily?symbol=${symbol}&start=${start}&end=${end}&limit=${state.chartDays + 60}`;

  fetch(url)
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(data => renderChart(data.rows || [], symbol))
    .catch(e => {
      const hint = $('chart-hint');
      if (hint) hint.textContent = `載入失敗 (${symbol}): ${e}`;
    });
}

function renderChart(rows, symbol) {
  const container = $('chart-container');
  const hint = $('chart-hint');
  if (!container) return;

  if (typeof LightweightCharts === 'undefined') {
    if (hint) hint.textContent = '圖表庫未載入（需連線 CDN）';
    return;
  }

  // clean up previous chart
  if (state.resizeObs) { state.resizeObs.disconnect(); state.resizeObs = null; }
  if (state.chart) { state.chart.remove(); state.chart = null; }
  container.innerHTML = '';

  const candles = rows
    .map(r => ({
      time:  String(r.date).slice(0, 10),
      open:  +r.open, high: +r.high, low: +r.low, close: +r.close,
    }))
    .filter(c => [c.open, c.high, c.low, c.close].every(Number.isFinite))
    .sort((a, b) => a.time < b.time ? -1 : 1);

  const volumes = rows
    .map(r => ({
      time:  String(r.date).slice(0, 10),
      value: +r.volume,
      color: (+r.close >= +r.open) ? 'rgba(8,153,129,0.4)' : 'rgba(242,54,69,0.4)',
    }))
    .filter(v => Number.isFinite(v.value))
    .sort((a, b) => a.time < b.time ? -1 : 1);

  if (!candles.length) {
    if (hint) hint.textContent = `${symbol}: 無歷史資料（TWSE STOCK_DAY）`;
    return;
  }

  const chart = LightweightCharts.createChart(container, {
    layout: {
      background: { type: LightweightCharts.ColorType.Solid, color: '#131722' },
      textColor: '#d1d4dc',
      fontSize: 11,
    },
    grid: {
      vertLines: { color: 'rgba(54,58,69,0.5)' },
      horzLines: { color: 'rgba(54,58,69,0.5)' },
    },
    rightPriceScale: {
      borderColor: '#363a45',
      scaleMargins: { top: 0.06, bottom: 0.28 },
    },
    timeScale: {
      borderColor: '#363a45',
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: {
      vertLine: { color: '#758696', width: 1, style: 2 },
      horzLine: { color: '#758696', width: 1, style: 2 },
    },
    width: container.clientWidth,
    height: container.clientHeight || 320,
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: '#089981', downColor: '#f23645',
    borderUpColor: '#089981', borderDownColor: '#f23645',
    wickUpColor: '#089981',   wickDownColor: '#f23645',
  });
  candleSeries.setData(candles);

  const volSeries = chart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
  });
  chart.priceScale('vol').applyOptions({
    scaleMargins: { top: 0.78, bottom: 0 },
    borderVisible: false,
  });
  volSeries.setData(volumes);

  chart.timeScale().fitContent();
  state.chart = chart;

  state.resizeObs = new ResizeObserver(entries => {
    for (const ent of entries) {
      const { width, height } = ent.contentRect;
      chart.applyOptions({ width: Math.max(120, width), height: Math.max(120, height) });
    }
  });
  state.resizeObs.observe(container);

  if (hint) {
    hint.textContent = `${symbol} · ${candles.length} 個交易日 · 拖曳平移，滾輪縮放`;
  }
}

// ── Signals table ──────────────────────────────────────────────
function buildTable() {
  const tbody = $('sig-tbody');
  if (!tbody) return;

  // merge snap into signals
  const rows = state.signals.map(r => {
    const sn = state.snap[r.symbol];
    return {
      ...r,
      _close:   sn?.close        ?? r.prev_close,
      _open:    sn?.open         ?? null,
      _chgPct:  sn?.change_rate  ?? r.chg_pct,
      _volume:  sn?.volume       ?? null,
    };
  });

  // sort
  const col = state.sortCol;
  rows.sort((a, b) => {
    let va = a[col] ?? a['_' + col] ?? 0;
    let vb = b[col] ?? b['_' + col] ?? 0;
    if (typeof va === 'string') va = va.localeCompare(vb);
    else va = va - vb;
    return state.sortAsc ? va : -va;
  });

  tbody.innerHTML = '';
  for (const r of rows) {
    const isActive = r.symbol === state.active;
    const chgCls   = pctClass(r._chgPct);
    const gapCls   = r.gap_pct < 0 ? 'td-up' : r.gap_pct > 0 ? 'td-down' : 'td-flat';

    const tr = document.createElement('tr');
    if (isActive) tr.className = 'active';
    tr.dataset.symbol = r.symbol;
    tr.innerHTML = `
      <td class="col-sym">${r.symbol}</td>
      <td>${r._open != null ? fmt2(r._open) : '—'}</td>
      <td>${fmt2(r._close)}</td>
      <td class="${chgCls}">${r._chgPct != null ? sign(r._chgPct) + fmt2(r._chgPct) + '%' : '—'}</td>
      <td>${r._volume != null ? fmtK(r._volume) : '—'}</td>
      <td>${fmt2(r.high_20d)}</td>
      <td class="${gapCls}">${sign(r.gap)}${fmt2(r.gap)}</td>
      <td class="${gapCls}">${sign(r.gap_pct)}${fmt2(r.gap_pct)}%</td>
      <td>${statusPill(r.status)}</td>
    `;
    tr.addEventListener('click', () => selectSymbol(r.symbol));
    tbody.appendChild(tr);
  }
}

function updateTableActiveRow() {
  document.querySelectorAll('#sig-tbody tr').forEach(tr => {
    tr.classList.toggle('active', tr.dataset.symbol === state.active);
  });
}

// ── Table sort ─────────────────────────────────────────────────
function initTableSort() {
  document.querySelectorAll('.sig-table th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (state.sortCol === col) {
        state.sortAsc = !state.sortAsc;
      } else {
        state.sortCol = col;
        state.sortAsc = col === 'symbol' || col === 'status';
      }
      document.querySelectorAll('.sig-table th').forEach(t => {
        t.classList.remove('sort-asc', 'sort-desc');
      });
      th.classList.add(state.sortAsc ? 'sort-asc' : 'sort-desc');
      buildTable();
    });
  });
  // default sort indicator
  const defTh = document.querySelector('.sig-table th[data-col="gap_pct"]');
  if (defTh) defTh.classList.add('sort-asc');
}

// ── Time-frame buttons ─────────────────────────────────────────
function initTfButtons() {
  document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('tf-btn--active'));
      btn.classList.add('tf-btn--active');
      state.chartDays = +btn.dataset.days;
      if (state.active) loadChart(state.active);
    });
  });
}

// ── Search ─────────────────────────────────────────────────────
function initSearch() {
  const inp = $('search-input');
  if (!inp) return;
  inp.addEventListener('input', () => buildWatchlist());
}

// ── Data fetching ──────────────────────────────────────────────
async function fetchSignals() {
  const today = new Date().toISOString().slice(0, 10);
  const res   = await fetch(`/api/signals?date=${today}`);
  const data  = await res.json();
  state.signals = (data.rows || []).sort((a, b) => a.gap_pct - b.gap_pct);
  const dateEl = $('table-date');
  if (dateEl) dateEl.textContent = data.date || today;
}

async function fetchSnapshot() {
  const btn = $('btn-refresh');
  if (btn) { btn.textContent = '⟳ 載入中…'; btn.classList.add('loading'); }
  try {
    const res  = await fetch('/api/snapshot');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    applyPriceRows(data.rows || [], data.source || 'sinopac');
  } catch (e) {
    console.warn('Snapshot failed:', e.message);
    setLastUpdate('DB only');
  } finally {
    if (btn) { btn.textContent = '⟳ 刷新'; btn.classList.remove('loading'); }
  }
}

function applyPriceRows(rows, source) {
  for (const r of rows) state.snap[r.symbol] = r;
  buildWatchlist();
  buildTable();
  if (state.active) updateChartHeader(state.active);
  setLastUpdate(source);
}

// ── SSE (real-time tick stream) ────────────────────────────────
function initSSE() {
  const liveEl = $('last-update');

  function connect() {
    const es = new EventSource('/api/stream');

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.mode) state.tickMode = data.mode;
        if (data.rows && data.rows.length) {
          applyPriceRows(data.rows, data.mode === 'tick' ? 'LIVE' : 'snapshot');
        }
      } catch { /* ignore parse errors */ }
    };

    es.onerror = () => {
      // SSE reconnects automatically; show offline state briefly
      if (liveEl) liveEl.textContent = '連線中斷，重連…';
      // browser will auto-reconnect after a short delay
    };

    return es;
  }

  state.es = connect();
}

// ── Nav tab switching ──────────────────────────────────────────
function initNavTabs() {
  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('nav-tab--active'));
      tab.classList.add('nav-tab--active');
    });
  });
}

// ── Boot ───────────────────────────────────────────────────────
async function boot() {
  initNavTabs();
  initTableSort();
  initTfButtons();
  initSearch();

  $('btn-refresh')?.addEventListener('click', fetchSnapshot);

  // 1. load signals from DB (instant — no Sinopac needed)
  try {
    await fetchSignals();
    buildWatchlist();
    buildTable();
  } catch (e) {
    console.error('Signals load failed:', e);
  }

  // 2. connect SSE for real-time tick data
  initSSE();
}

document.addEventListener('DOMContentLoaded', boot);
