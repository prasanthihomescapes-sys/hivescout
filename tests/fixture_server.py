"""Tiny local server for probe tests: /<page> serves fixtures/site/<page>.html, /api/items returns 3 items."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE = Path(__file__).parent / "fixtures" / "site"
ITEMS = [{"name": "Order 1001"}, {"name": "Order 1002"}, {"name": "Order 1003"}]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].strip("/")
        if path == "api/items":
            self._send(200, json.dumps(ITEMS).encode(), "application/json")
            return
        f = SITE / f"{path or 'good'}.html"
        if f.exists():
            self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"<h1>Not found</h1>", "text/html")


def start() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    s, url = start()
    print(url)
    threading.Event().wait()
