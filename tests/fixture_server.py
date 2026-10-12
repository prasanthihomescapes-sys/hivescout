"""Tiny local server for probe tests.

GET  /<page>        -> fixtures/site/<page>.html  (pages under /app* need the session cookie, else 302 /login)
GET  /api/items     -> 3 items
POST /api/login     -> sets the session cookie when email/password match the fixture test account
POST /api/orders    -> appends an order (in memory), returns it
GET  /api/orders    -> the orders
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE = Path(__file__).parent / "fixtures" / "site"
ITEMS = [{"name": "Order 1001"}, {"name": "Order 1002"}, {"name": "Order 1003"}]
TEST_EMAIL = "scout@example.test"       # fixture-only test account, served from 127.0.0.1
TEST_PASSWORD = "fixture-pass-123"
SESSION = "hs_session=fixture-ok"


class Handler(BaseHTTPRequestHandler):
    orders: list[dict] = []
    lock = threading.Lock()

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj, headers: dict | None = None) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json", headers)

    def _signed_in(self) -> bool:
        return SESSION in (self.headers.get("Cookie") or "")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0].strip("/")
        if path == "api/items":
            self._json(200, ITEMS)
            return
        if path == "api/orders":
            self._json(200, self.orders)
            return
        if path.startswith("app") and not self._signed_in():
            self._send(302, b"", "text/html", {"Location": "/login"})
            return
        f = SITE / f"{path or 'good'}.html"
        if f.exists():
            self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"<h1>Not found</h1>", "text/html")

    def do_POST(self):
        path = self.path.split("?")[0].strip("/")
        body = self._body()
        if path == "api/login":
            if body.get("email") == TEST_EMAIL and body.get("password") == TEST_PASSWORD:
                self._json(200, {"ok": True}, {"Set-Cookie": f"{SESSION}; Path=/; HttpOnly"})
            else:
                self._json(401, {"error": "invalid credentials"})
            return
        if path == "api/orders":
            if not self._signed_in():
                self._json(401, {"error": "unauthorized"})
                return
            with self.lock:
                order = {"name": str(body.get("name", ""))[:80]}
                self.orders.append(order)
            self._json(201, order)
            return
        self._json(404, {"error": "not found"})


def start() -> tuple[ThreadingHTTPServer, str]:
    Handler.orders = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    s, url = start()
    print(url)
    threading.Event().wait()
