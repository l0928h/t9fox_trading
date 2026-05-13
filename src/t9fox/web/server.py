from __future__ import annotations

import json
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

# ── snapshot cache (60-second TTL, avoids repeated Sinopac logins) ──────────
_snap_lock   = threading.Lock()
_snap_cache: list[dict] = []
_snap_ts: float = 0.0
_SNAP_TTL = 60.0


def _fetch_snapshot_rows(symbols: list[str]) -> list[dict]:
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
                "open":         float(getattr(s, "open",  0) or 0),
                "close":        float(s.close),
                "change_price": float(s.change_price),
                "change_rate":  float(s.change_rate),
                "buy_price":    float(s.buy_price),
                "sell_price":   float(s.sell_price),
                "volume":       int(s.total_volume),
            })
    return rows


def _get_snapshot(symbols: list[str]) -> tuple[list[dict], str]:
    """Return (rows, source) using 60-second in-memory cache."""
    global _snap_cache, _snap_ts
    with _snap_lock:
        if time.time() - _snap_ts < _SNAP_TTL and _snap_cache:
            return _snap_cache, "cache"
        try:
            rows = _fetch_snapshot_rows(symbols)
            _snap_cache = rows
            _snap_ts = time.time()
            return rows, "sinopac"
        except Exception as e:
            sys.stderr.write(f"[snapshot] Sinopac error: {e}\n")
            if _snap_cache:
                return _snap_cache, "cache_stale"
            raise


def _df_to_records(df) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.reset_index()
    c0 = out.columns[0]
    out = out.rename(columns={c0: "date"})
    raw = out.to_json(orient="records", date_format="iso", default_handler=str)
    return json.loads(raw)


def make_twse_handler_class(static_root: Path):
    static_root = static_root.resolve()

    class TwseDevHandler(SimpleHTTPRequestHandler):
        server_version = "T9FOX-TWSE/0.1"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_root), **kwargs)

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send_json(self, status: int, payload: dict[str, Any] | list[Any]) -> None:
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
                self._send_json(HTTPStatus.OK, {"ok": True, "service": "t9fox-serve"})
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

        def _handle_watchlist(self) -> None:
            wl = static_root.parent.parent / "watchlist.txt"
            if not wl.is_file():
                self._send_json(HTTPStatus.OK, {"symbols": []})
                return
            symbols = [
                ln.strip()
                for ln in wl.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            self._send_json(HTTPStatus.OK, {"symbols": symbols})

        def _handle_signals(self, query_string: str) -> None:
            try:
                from t9fox.db.store import query_signals
                import datetime
                qs  = urllib.parse.parse_qs(query_string)
                date = (qs.get("date") or [datetime.date.today().isoformat()])[0]
                rows = query_signals(date=date, limit=200)
                self._send_json(HTTPStatus.OK, {"date": date, "rows": rows})
            except Exception:
                traceback.print_exc()
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "db error"})

        def _handle_snapshot(self) -> None:
            try:
                # resolve symbols from watchlist
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
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(e), "hint": "Sinopac credentials required"}
                )

        def _handle_twse_daily(self, query_string: str) -> None:
            try:
                qs = urllib.parse.parse_qs(query_string, keep_blank_values=False)
                symbol = (qs.get("symbol") or [None])[0]
                start = (qs.get("start") or [None])[0]
                if not symbol or not start:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "required: symbol, start (YYYY-MM-DD)"},
                    )
                    return
                end = (qs.get("end") or [None])[0]
                refresh = (qs.get("refresh") or ["0"])[0].lower() in ("1", "true", "yes")
                limit_raw = (qs.get("limit") or [None])[0]
                limit_n = int(limit_raw) if limit_raw else None
                if limit_raw is not None and limit_n is not None and limit_n < 1:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "limit must be >= 1"})
                    return
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid limit"})
                return

            try:
                df = load_or_fetch_daily_bars(
                    symbol.strip(),
                    start,
                    end if end else None,
                    refresh=refresh,
                )
            except Exception:
                traceback.print_exc()
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "failed to load TWSE data"},
                )
                return

            if df.empty:
                self._send_json(
                    HTTPStatus.OK,
                    {"symbol": symbol.strip(), "source": "TWSE STOCK_DAY", "rows": []},
                )
                return

            if limit_n:
                df = df.tail(limit_n)
            rows = _df_to_records(df)
            self._send_json(
                HTTPStatus.OK,
                {"symbol": symbol.strip(), "source": "TWSE STOCK_DAY", "rows": rows},
            )

    return TwseDevHandler


def run_server(host: str, port: int, static_dir: Path | None = None) -> None:
    root = Settings.load().root
    static = (static_dir or root / "web" / "netflix-style").resolve()
    if not static.is_dir():
        print("Static directory not found: %s" % static, file=sys.stderr)
        raise SystemExit(1)
    handler_cls = make_twse_handler_class(static)
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    print("Serving http://%s:%s/" % (host, port))
    print(
        "  TWSE API: http://%s:%s/api/twse/daily?symbol=2330&start=2024-01-01"
        % (host, port)
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
    finally:
        httpd.server_close()
