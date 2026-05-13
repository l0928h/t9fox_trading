from __future__ import annotations

import json
import queue
import sys
import threading
import time
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from t9fox.config import Settings
from t9fox.data.twse_daily import load_or_fetch_daily_bars


# ── Tick streamer (persistent Sinopac connection + SSE push) ─────────────────

class _TickStreamer:
    """
    Subscribes to Sinopac tick feed for the watchlist.
    Batches updates every 300 ms and pushes to SSE clients.

    Lifecycle:
        streamer.start(symbols)   # call once in background thread
        streamer.stop()           # call on server shutdown
    """

    _BATCH_MS = 300   # flush accumulated ticks every N ms

    def __init__(self) -> None:
        self._prices: dict[str, dict] = {}   # symbol → latest tick data
        self._lock    = threading.Lock()
        self._dirty   = threading.Event()
        self._stop    = threading.Event()
        self._clients: list[queue.Queue] = []
        self._cl_lock = threading.Lock()
        self._broker  = None
        self._active  = False
        self._mode    = "offline"            # "tick" | "offline"

    # ── public API ────────────────────────────────────────────────────────

    def start(self, symbols: list[str]) -> None:
        """Connect + subscribe. Blocks until stopped (run in a daemon thread)."""
        try:
            from t9fox.broker.credentials import SinopacCredentials
            from t9fox.broker.sinopac import SinopacBroker
            import shioaji.constant as c   # type: ignore[import]

            creds = SinopacCredentials.from_env()
            broker = SinopacBroker(creds)
            broker.login()

            sym_set = set(symbols)

            @broker.api.on_tick_stk_v1()
            def _on_tick(exchange, tick):
                if tick.code not in sym_set:
                    return
                with self._lock:
                    self._prices[tick.code] = {
                        "symbol":       tick.code,
                        "open":         float(getattr(tick, "open",  0) or 0),
                        "close":        float(tick.close),
                        "change_price": float(getattr(tick, "change_price", 0)),
                        "change_rate":  float(getattr(tick, "change_rate",  0)),
                        "buy_price":    float(getattr(tick, "buy_price",   0) or 0),
                        "sell_price":   float(getattr(tick, "sell_price",  0) or 0),
                        "volume":       int(getattr(tick, "total_volume",  0) or 0),
                    }
                self._dirty.set()

            ok = 0
            for sym in symbols:
                try:
                    contract = broker.api.Contracts.Stocks[sym]
                    broker.api.quote.subscribe(
                        contract,
                        quote_type=c.QuoteType.Tick,
                        version=c.QuoteVersion.v1,
                    )
                    ok += 1
                except Exception as e:
                    sys.stderr.write(f"[tick] subscribe {sym}: {e}\n")

            self._broker = broker
            self._active = True
            self._mode   = "tick"
            sys.stderr.write(f"[tick] Subscribed {ok}/{len(symbols)} symbols\n")

            # sender loop (runs in this thread until stopped)
            self._sender_loop()

        except Exception as e:
            sys.stderr.write(f"[tick] Cannot start tick stream: {e}\n")
            self._mode = "offline"

    def stop(self) -> None:
        self._stop.set()
        if self._broker:
            try:
                self._broker.logout()
            except Exception:
                pass

    def add_client(self, q: queue.Queue) -> None:
        with self._cl_lock:
            self._clients.append(q)
        # send mode + current snapshot immediately
        payload = {"mode": self._mode, "rows": []}
        with self._lock:
            if self._prices:
                payload["rows"] = list(self._prices.values())
        try:
            q.put_nowait(f"data: {json.dumps(payload)}\n\n".encode())
        except queue.Full:
            pass

    def remove_client(self, q: queue.Queue) -> None:
        with self._cl_lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    @property
    def mode(self) -> str:
        return self._mode

    # ── internal ─────────────────────────────────────────────────────────

    def _sender_loop(self) -> None:
        interval = self._BATCH_MS / 1000
        while not self._stop.is_set():
            triggered = self._dirty.wait(timeout=15)
            self._dirty.clear()

            if triggered:
                with self._lock:
                    rows = list(self._prices.values())
                payload = {"mode": "tick", "rows": rows}
                self._broadcast(f"data: {json.dumps(payload)}\n\n".encode())
                time.sleep(interval)   # anti-flood: 300 ms between SSE frames
            else:
                # heartbeat so clients know the connection is alive
                self._broadcast(b": heartbeat\n\n")

    def _broadcast(self, msg: bytes) -> None:
        dead: list[queue.Queue] = []
        with self._cl_lock:
            for q in self._clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._clients.remove(q)


# module-level singleton; start() called by run_server()
_streamer = _TickStreamer()


# ── snapshot fallback (used when tick stream is offline) ─────────────────────

_snap_lock  = threading.Lock()
_snap_cache: list[dict] = []
_snap_ts: float = 0.0
_SNAP_TTL   = 60.0


def _get_snapshot(symbols: list[str]) -> tuple[list[dict], str]:
    global _snap_cache, _snap_ts
    with _snap_lock:
        if time.time() - _snap_ts < _SNAP_TTL and _snap_cache:
            return _snap_cache, "cache"
        try:
            from t9fox.broker.credentials import SinopacCredentials
            from t9fox.broker.sinopac import SinopacBroker
            creds = SinopacCredentials.from_env()
            rows: list[dict] = []
            with SinopacBroker(creds) as broker:
                contracts = [broker.api.Contracts.Stocks[s] for s in symbols]
                snaps = broker.api.snapshots(contracts)
                for s in snaps:
                    rows.append({
                        "symbol":       s.code,
                        "open":         float(getattr(s, "open", 0) or 0),
                        "close":        float(s.close),
                        "change_price": float(s.change_price),
                        "change_rate":  float(s.change_rate),
                        "buy_price":    float(s.buy_price),
                        "sell_price":   float(s.sell_price),
                        "volume":       int(s.total_volume),
                    })
            _snap_cache = rows
            _snap_ts = time.time()
            return rows, "sinopac"
        except Exception as e:
            sys.stderr.write(f"[snapshot] {e}\n")
            if _snap_cache:
                return _snap_cache, "cache_stale"
            raise


def _df_to_records(df) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.reset_index()
    c0  = out.columns[0]
    out = out.rename(columns={c0: "date"})
    raw = out.to_json(orient="records", date_format="iso", default_handler=str)
    return json.loads(raw)


# ── HTTP handler factory ──────────────────────────────────────────────────────

def make_twse_handler_class(static_root: Path):
    static_root = static_root.resolve()

    class TwseDevHandler(SimpleHTTPRequestHandler):
        server_version = "T9FOX/1.0"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_root), **kwargs)

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send_json(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            p = parsed.path
            if p == "/api/health":
                self._send_json(HTTPStatus.OK, {
                    "ok": True, "service": "t9fox-serve",
                    "tick_mode": _streamer.mode,
                })
                return
            if p == "/api/stream":
                self._handle_sse()
                return
            if p == "/api/twse/daily":
                self._handle_twse_daily(parsed.query)
                return
            if p == "/api/watchlist":
                self._handle_watchlist()
                return
            if p == "/api/signals":
                self._handle_signals(parsed.query)
                return
            if p == "/api/snapshot":
                self._handle_snapshot()
                return

            path_only = urllib.parse.unquote(p)
            if path_only in ("", "/"):
                self.path = "/index.html"
            else:
                self.path = path_only
            super().do_GET()

        # ── SSE ──────────────────────────────────────────────────────────

        def _handle_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type",  "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            q: queue.Queue = queue.Queue(maxsize=50)
            _streamer.add_client(q)
            try:
                while True:
                    try:
                        msg = q.get(timeout=20)
                        self.wfile.write(msg if isinstance(msg, bytes) else msg.encode())
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                _streamer.remove_client(q)

        # ── REST endpoints ────────────────────────────────────────────────

        def _handle_watchlist(self) -> None:
            wl = static_root.parent.parent / "watchlist.txt"
            symbols = (
                [ln.strip() for ln in wl.read_text("utf-8").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
                if wl.is_file() else []
            )
            self._send_json(HTTPStatus.OK, {"symbols": symbols})

        def _handle_signals(self, qs_str: str) -> None:
            try:
                import datetime
                from t9fox.db.store import query_signals
                qs   = urllib.parse.parse_qs(qs_str)
                date = (qs.get("date") or [datetime.date.today().isoformat()])[0]
                rows = query_signals(date=date, limit=200)
                self._send_json(HTTPStatus.OK, {"date": date, "rows": rows})
            except Exception:
                traceback.print_exc()
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "db error"})

        def _handle_snapshot(self) -> None:
            try:
                wl = static_root.parent.parent / "watchlist.txt"
                symbols = (
                    [ln.strip() for ln in wl.read_text("utf-8").splitlines()
                     if ln.strip() and not ln.strip().startswith("#")]
                    if wl.is_file() else []
                )
                if not symbols:
                    self._send_json(HTTPStatus.OK, {"rows": [], "source": "empty"})
                    return
                rows, source = _get_snapshot(symbols)
                self._send_json(HTTPStatus.OK, {"rows": rows, "source": source})
            except Exception as e:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE,
                                {"error": str(e), "hint": "Sinopac credentials required"})

        def _handle_twse_daily(self, qs_str: str) -> None:
            try:
                qs     = urllib.parse.parse_qs(qs_str, keep_blank_values=False)
                symbol = (qs.get("symbol") or [None])[0]
                start  = (qs.get("start")  or [None])[0]
                if not symbol or not start:
                    self._send_json(HTTPStatus.BAD_REQUEST,
                                    {"error": "required: symbol, start"})
                    return
                end       = (qs.get("end")     or [None])[0]
                refresh   = (qs.get("refresh") or ["0"])[0].lower() in ("1", "true", "yes")
                limit_raw = (qs.get("limit")   or [None])[0]
                limit_n   = int(limit_raw) if limit_raw else None
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid limit"})
                return
            try:
                df = load_or_fetch_daily_bars(symbol.strip(), start,
                                              end if end else None, refresh=refresh)
            except Exception:
                traceback.print_exc()
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                {"error": "failed to load TWSE data"})
                return
            if df.empty:
                self._send_json(HTTPStatus.OK,
                                {"symbol": symbol.strip(), "source": "TWSE STOCK_DAY", "rows": []})
                return
            if limit_n:
                df = df.tail(limit_n)
            self._send_json(HTTPStatus.OK, {
                "symbol": symbol.strip(),
                "source": "TWSE STOCK_DAY",
                "rows":   _df_to_records(df),
            })

    return TwseDevHandler


# ── Server entry point ────────────────────────────────────────────────────────

def run_server(host: str, port: int, static_dir: Path | None = None) -> None:
    root   = Settings.load().root
    static = (static_dir or root / "web" / "netflix-style").resolve()
    if not static.is_dir():
        sys.stderr.write(f"Static directory not found: {static}\n")
        raise SystemExit(1)

    # load watchlist for tick subscription
    wl_path = root / "watchlist.txt"
    symbols: list[str] = []
    if wl_path.is_file():
        symbols = [
            ln.strip() for ln in wl_path.read_text("utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]

    # start tick streamer in background (non-blocking; falls back gracefully)
    if symbols:
        t = threading.Thread(target=_streamer.start, args=(symbols,), daemon=True)
        t.start()
        print(f"  Tick stream: starting for {len(symbols)} symbols (async)")
    else:
        print("  Tick stream: no watchlist found, tick streaming disabled")

    handler_cls = make_twse_handler_class(static)
    httpd = ThreadingHTTPServer((host, port), handler_cls)

    print(f"Serving http://{host}:{port}/")
    print(f"  SSE stream: http://{host}:{port}/api/stream")
    print(f"  Signals:    http://{host}:{port}/api/signals")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
    finally:
        _streamer.stop()
        httpd.server_close()
