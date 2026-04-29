from __future__ import annotations

import json
import sys
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from t9fox.config import Settings
from t9fox.data.twse_daily import load_or_fetch_daily_bars


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
            if parsed.path == "/api/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "service": "t9fox-serve"})
                return
            if parsed.path == "/api/twse/daily":
                self._handle_twse_daily(parsed.query)
                return

            path_only = urllib.parse.unquote(parsed.path)
            if path_only in ("", "/"):
                self.path = "/index.html"
            else:
                self.path = path_only
            super().do_GET()

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
