"""Fault catalogue: what can go wrong between the user's screen and the backend."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fault:
    name: str
    description: str
    kind: str              # status | hang | delay | body | abort | none
    status: int = 200
    body: str = ""
    content_type: str = "application/json"
    delay_ms: int = 0
    failure: bool = True   # does the user deserve to be told something went wrong?


FAULTS: dict[str, Fault] = {f.name: f for f in [
    Fault("baseline", "No fault: reference rendering", "none", failure=False),
    Fault("http500", "API returns 500", "status", status=500, body='{"error":"internal"}'),
    Fault("http401", "Session expired: API returns 401", "status", status=401, body='{"error":"unauthorized"}'),
    Fault("http404", "API resource missing: 404", "status", status=404, body='{"error":"not found"}'),
    Fault("timeout", "API never answers", "hang"),
    Fault("slow", "API answers after 6 s (slow mobile network)", "delay", delay_ms=6000, failure=False),
    Fault("malformed", "API returns 200 with invalid JSON", "body", body="{not json", failure=True),
    Fault("empty", "API returns an empty list (first-time user)", "body", body="[]", failure=False),
    Fault("offline", "Network drops: requests fail and the browser goes offline", "abort"),
]}

DEFAULT_SET = ["http500", "timeout", "offline", "empty", "malformed"]
