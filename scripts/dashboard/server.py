"""server.py — HTTP Handler class + routing + main()."""

import json
import logging
import os
import sys
import urllib.parse
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from scripts.dashboard.constants import HOME, PORT, CANVAS_HTML

from scripts.dashboard.collectors import collect_data
from scripts.dashboard.services import (
    handle_services, handle_service_restart, collect_debug,
)
from scripts.dashboard.handlers import (
    handle_set_mode, handle_set_regime, handle_api_state, handle_api_config,
    handle_set_trading, handle_close_position, handle_modify_sltp,
    handle_place_order, handle_cancel_order, handle_exchange_balance,
    handle_symbol_info, handle_orderbook,
    handle_api_scan_log, handle_api_health, handle_suggest_mode,
)
from scripts.dashboard.exchange_auth import (
    handle_binance_status, handle_binance_connect, handle_binance_disconnect,
    handle_aster_status, handle_aster_connect, handle_aster_disconnect,
    handle_hl_status, handle_hl_connect, handle_hl_disconnect,
)
from scripts.dashboard.backtest import (
    handle_bt_list, handle_bt_klines, handle_bt_results, handle_bt_status,
    handle_bt_run, handle_bt_export, handle_bt_import,
    handle_bt_aggtrades, handle_bt_aggtrades_status,
    handle_bt_nfs_fvz,
    handle_bt_shootout_list, handle_bt_shootout_detail,
)
from scripts.dashboard.chat import handle_chat
from scripts.dashboard.paper_trading import (
    handle_paper_trading_status, handle_paper_trading_start,
    handle_paper_trading_stop,
)
from scripts.dashboard.polymarket import (
    handle_polymarket_data, handle_polymarket_set_mode,
    handle_polymarket_force_scan, handle_polymarket_reset_cb,
    handle_polymarket_check_merge, handle_polymarket_run_cycle,
    handle_polymarket_cycle_status,
)
from scripts.dashboard.files import (
    handle_file_read, handle_open_folder, get_docs_list, serve_doc,
    generate_share_package,
)
from scripts.dashboard.pending_sltp import _load_pending_sltp
from scripts.dashboard.services import _auto_bootstrap

# ── CORS Origins ─────────────────────────────────────────────────────
_ALLOWED_ORIGINS = {
    f"http://127.0.0.1:{PORT}",
    f"http://localhost:{PORT}",
}


class Handler(BaseHTTPRequestHandler):

    def _check_origin(self):
        """Block cross-origin POST requests (CSRF protection)."""
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")
        if origin:
            if origin not in _ALLOWED_ORIGINS:
                self._json_response(403, {"error": "Forbidden origin"})
                return False
        elif referer:
            if not any(referer == o or referer.startswith(o + "/") for o in _ALLOWED_ORIGINS):
                self._json_response(403, {"error": "Forbidden referer"})
                return False
        ct = self.headers.get("Content-Type", "")
        if not ct.startswith("application/json"):
            self._json_response(400, {"error": "Content-Type must be application/json"})
            return False
        return True

    def _json_response(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self.headers.get("Origin", "") if hasattr(self, 'headers') and self.headers else ""
        allowed = origin if origin in _ALLOWED_ORIGINS else f"http://127.0.0.1:{PORT}"
        self.send_header("Access-Control-Allow-Origin", allowed)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)
        if path == "/api/data":
            self._json_response(200, collect_data())
        elif path == "/api/state":
            self._json_response(200, handle_api_state())
        elif path == "/api/config":
            self._json_response(200, handle_api_config())
        elif path == "/api/scan-log":
            self._json_response(200, handle_api_scan_log())
        elif path == "/api/health":
            self._json_response(200, handle_api_health())
        elif path == "/api/debug":
            if qs.get("token", [""])[0] != "axc-debug":
                self._json_response(403, {"error": "Forbidden"})
            else:
                self._json_response(200, collect_debug())
        elif path == "/api/suggest_mode":
            self._json_response(200, handle_suggest_mode())
        elif path == "/api/binance/status":
            code, data = handle_binance_status()
            self._json_response(code, data)
        elif path == "/api/aster/status":
            code, data = handle_aster_status()
            self._json_response(code, data)
        elif path == "/api/hl/status":
            code, data = handle_hl_status()
            self._json_response(code, data)
        elif path == "/api/exchange/balance":
            self._json_response(200, handle_exchange_balance())
        elif path == "/api/exchange/symbol-info":
            code, data = handle_symbol_info(qs)
            self._json_response(code, data)
        elif path == "/api/orderbook":
            code, data = handle_orderbook(qs)
            self._json_response(code, data)
        elif path == "/api/file":
            rel = qs.get("path", [""])[0]
            code, content = handle_file_read(rel)
            if isinstance(content, str):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", f"http://127.0.0.1:{PORT}")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self._json_response(code, {"error": content})
        elif path == "/api/open_folder":
            rel = qs.get("path", [""])[0]
            code, data = handle_open_folder(rel)
            self._json_response(code, data)
        # ── Backtest API ──
        elif path == "/api/backtest/list":
            self._json_response(200, handle_bt_list())
        elif path == "/api/backtest/klines":
            code, data = handle_bt_klines(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/results":
            code, data = handle_bt_results(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/status":
            code, data = handle_bt_status(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/aggtrades":
            code, data = handle_bt_aggtrades(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/aggtrades/status":
            code, data = handle_bt_aggtrades_status(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/export":
            code, data = handle_bt_export(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/shootout/list":
            self._json_response(200, handle_bt_shootout_list())
        elif path == "/api/backtest/shootout/detail":
            code, data = handle_bt_shootout_detail(qs)
            self._json_response(code, data)
        elif path == "/backtest":
            bt_path = os.path.join(HOME, "canvas/backtest.html")
            try:
                with open(bt_path, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"canvas/backtest.html not found")
        elif path == "/details":
            details_path = os.path.join(HOME, "canvas/details.html")
            try:
                with open(details_path, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"details.html not found")
        elif path == "/paper":
            paper_path = os.path.join(HOME, "canvas/paper.html")
            try:
                with open(paper_path, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"canvas/paper.html not found")
        elif path == "/api/docs-list":
            self._json_response(200, get_docs_list())
        elif path.startswith("/api/doc/"):
            filename = urllib.parse.unquote(path[9:])
            code, content, ctype = serve_doc(filename)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", f"http://127.0.0.1:{PORT}")
            self.end_headers()
            self.wfile.write(content.encode() if isinstance(content, str) else content)
        elif path in ("/share", "/share/windows"):
            fname = "share-windows.html" if path == "/share/windows" else "share.html"
            share_path = os.path.join(HOME, "canvas", fname)
            if os.path.exists(share_path):
                with open(share_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()
        elif path == "/polymarket":
            poly_path = os.path.join(HOME, "canvas/polymarket.html")
            try:
                with open(poly_path, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"canvas/polymarket.html not found")
        elif path == "/api/polymarket/data":
            code, data = handle_polymarket_data()
            self._json_response(code, data)
        elif path == "/api/polymarket/cycle_status":
            code, data = handle_polymarket_cycle_status()
            self._json_response(code, data)
        elif path == "/api/paper-trading":
            code, data = handle_paper_trading_status()
            self._json_response(code, data)
        elif path == "/api/services":
            self._json_response(200, handle_services())
        elif path == "/api/share/package":
            try:
                zip_bytes = generate_share_package()
                date_str = datetime.now().strftime("%Y%m%d")
                filename = f"axc-setup-{date_str}.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"'
                )
                self.send_header("Content-Length", str(len(zip_bytes)))
                self.end_headers()
                self.wfile.write(zip_bytes)
            except Exception as e:
                err = f"Error: {e}".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
        elif path.startswith("/svg/") or path.endswith((".css", ".js")):
            _mime = {".svg": "image/svg+xml", ".png": "image/png",
                     ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".css": "text/css; charset=utf-8",
                     ".js": "application/javascript; charset=utf-8"}
            ext = os.path.splitext(path)[1].lower()
            ctype = _mime.get(ext)
            img_path = os.path.join(HOME, "canvas", path.lstrip("/"))
            if ctype and os.path.isfile(img_path):
                with open(img_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-cache, must-revalidate")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()
        else:
            try:
                with open(CANVAS_HTML, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"canvas/index.html not found")

    def do_POST(self):
        if not self._check_origin():
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length > 0 else ""
        if self.path == "/api/set_mode":
            code, data = handle_set_mode(body)
            self._json_response(code, data)
        elif self.path == "/api/config/mode":
            code, data = handle_set_mode(body)
            self._json_response(code, data)
        elif self.path == "/api/set_regime":
            code, data = handle_set_regime(body)
            self._json_response(code, data)
        elif self.path == "/api/config/trading":
            code, data = handle_set_trading(body)
            self._json_response(code, data)
        elif self.path == "/api/binance/connect":
            code, data = handle_binance_connect(body)
            self._json_response(code, data)
        elif self.path == "/api/binance/disconnect":
            code, data = handle_binance_disconnect()
            self._json_response(code, data)
        elif self.path == "/api/aster/connect":
            code, data = handle_aster_connect(body)
            self._json_response(code, data)
        elif self.path == "/api/aster/disconnect":
            code, data = handle_aster_disconnect()
            self._json_response(code, data)
        elif self.path == "/api/hl/connect":
            code, data = handle_hl_connect(body)
            self._json_response(code, data)
        elif self.path == "/api/hl/disconnect":
            code, data = handle_hl_disconnect()
            self._json_response(code, data)
        elif self.path == "/api/close-position":
            code, data = handle_close_position(body)
            self._json_response(code, data)
        elif self.path == "/api/modify-sltp":
            code, data = handle_modify_sltp(body)
            self._json_response(code, data)
        elif self.path == "/api/place-order":
            code, data = handle_place_order(body)
            self._json_response(code, data)
        elif self.path == "/api/cancel-order":
            code, data = handle_cancel_order(body)
            self._json_response(code, data)
        elif self.path == "/api/backtest/run":
            code, data = handle_bt_run(body)
            self._json_response(code, data)
        elif self.path == "/api/backtest/nfs-fvz":
            code, data = handle_bt_nfs_fvz(body)
            self._json_response(code, data)
        elif self.path == "/api/backtest/import":
            if len(body) > 50 * 1024 * 1024:  # 50 MB limit
                self._json_response(413, {"error": "File too large (max 50 MB)"})
            else:
                code, data = handle_bt_import(body)
                self._json_response(code, data)
        elif self.path == "/api/chat":
            code, data = handle_chat(body)
            self._json_response(code, data)
        elif self.path == "/api/paper-trading/start":
            code, data = handle_paper_trading_start()
            self._json_response(code, data)
        elif self.path == "/api/paper-trading/stop":
            code, data = handle_paper_trading_stop()
            self._json_response(code, data)
        elif self.path == "/api/polymarket/set_mode":
            code, data = handle_polymarket_set_mode(body)
            self._json_response(code, data)
        elif self.path == "/api/polymarket/force_scan":
            code, data = handle_polymarket_force_scan(body)
            self._json_response(code, data)
        elif self.path == "/api/polymarket/reset_cb":
            code, data = handle_polymarket_reset_cb(body)
            self._json_response(code, data)
        elif self.path == "/api/polymarket/check_merge":
            code, data = handle_polymarket_check_merge(body)
            self._json_response(code, data)
        elif self.path == "/api/polymarket/run_cycle":
            code, data = handle_polymarket_run_cycle(body)
            self._json_response(code, data)
        elif self.path == "/api/service/restart":
            data = handle_service_restart(body)
            self._json_response(200 if data["ok"] else 400, data)
        else:
            self._json_response(404, {"error": "Not found"})

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        allowed = origin if origin in _ALLOWED_ORIGINS else f"http://127.0.0.1:{PORT}"
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", allowed)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    port = PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    # Restore pending SL/TP state from disk (crash recovery)
    _load_pending_sltp()

    # Auto-bootstrap stopped services
    _auto_bootstrap()

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
    bind = "127.0.0.1"
    server = ThreadedHTTPServer((bind, port), Handler)
    print(f"AXC Dashboard: http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
