"""Deterministic detectors: turn a page snapshot under a fault into user-experience findings."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..findings import Finding
from .faults import Fault

STACK = re.compile(
    r"(Traceback \(most recent call last\)|\bat [\w$.<>]+ \(.*:\d+:\d+\)|\b(TypeError|ReferenceError|SyntaxError|"
    r"RangeError|KeyError|AttributeError|NameError|ValueError|ZeroDivisionError|PrismaClient\w*Error|"
    r"Unhandled Runtime Error|Application error: a (client|server)-side exception)\b|"
    r"Unexpected token .{0,20} in JSON|is not valid JSON|ECONNREFUSED|ERR_[A-Z_]{6,}|\[object Object\]|"
    r"\bundefined is not\b|Cannot read propert(y|ies) of (undefined|null))")
ERROR_COPY = re.compile(
    r"(something went wrong|went wrong|\berror\b|failed|failure|couldn.?t|could not|can.?t (load|reach|connect)|"
    r"unable to|try again|offline|no (internet|connection)|connection (lost|problem)|not available|unavailable|"
    r"session (has )?expired|sign in again|log in again|not found|timed out|taking longer)", re.I)
RECOVERY = re.compile(r"(retry|try again|reload|refresh|go back|back|home|contact|support|sign in|log in|login|"
                      r"dismiss|start over|reconnect)", re.I)
EMPTY_COPY = re.compile(r"(no \w+ (yet|found)|nothing (here|yet|to show)|empty|get started|create your first|"
                        r"add your first|you have(n.?t| not| no)|0 results|no results|no data)", re.I)


@dataclass
class Snapshot:
    text: str
    media: int
    spinners: int
    actions: list[str]
    alerts: list[str]
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    intercepted: int = 0
    status: int | None = None

    @property
    def words(self) -> int:
        return len(self.text.split())


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "0", t)).strip()


def judge(snap: Snapshot, base: Snapshot | None, fault: Fault, *, target: str, route: str, persona: str,
          shot: str | None) -> list[Finding]:
    out: list[Finding] = []

    def add(rule: str, sev: str, title: str, fix: str, evidence: str = "") -> None:
        out.append(Finding(rule=rule, severity=sev, title=title, channel="web", target=target, route=route,
                           fault=fault.name, evidence=evidence[:400], fix=fix, artifacts=[shot] if shot else [],
                           extra={"persona": persona, "intercepted": snap.intercepted}))

    visible = snap.text.strip()
    shows_error = bool(ERROR_COPY.search(visible)) or any(ERROR_COPY.search(a) for a in snap.alerts)
    has_recovery = any(RECOVERY.search(a) for a in snap.actions)

    if len(visible) < 3 and snap.media == 0:
        add("WEB001", "high", "Blank screen: the user sees nothing at all",
            "Render an error/empty state from an error boundary (error.tsx / ErrorBoundary) instead of nothing.",
            f"visible text: {visible!r}")
        return out  # everything else is moot on a blank page

    m = STACK.search(visible)
    if m:
        add("WEB003", "high", "Raw error or stack trace shown to the user",
            "Catch it at the boundary and show plain-language copy with a next step; log the detail.",
            visible[max(0, m.start() - 80):m.end() + 120])

    if fault.kind in ("hang", "delay") and snap.spinners > 0 and not shows_error:
        sev = "high" if fault.kind == "hang" else "medium"
        add("WEB002", sev, "Spinner never resolves: no timeout message, no way out" if fault.kind == "hang"
            else "Still only a spinner after a slow response, with no reassurance or progress",
            "Abort after a deadline (AbortSignal.timeout) and show 'taking longer than usual' with retry.",
            f"{snap.spinners} spinner(s) visible after the wait")

    if fault.kind == "hang" and snap.spinners == 0 and not shows_error and base is not None \
            and snap.intercepted > 0 and _norm(visible) != _norm(base.text):
        add("WEB004", "high", "Request hung and the page quietly shows partial content with no explanation",
            "Show a loading state while waiting and a timeout message with retry when the deadline passes.")

    if fault.failure and fault.kind != "hang" and snap.intercepted > 0:
        if not shows_error and base is not None and base.words > 5 and snap.words >= 0.6 * base.words:
            same = _norm(visible) == _norm(base.text)
            add("WEB005", "high", "Failure is invisible: the page looks as if the data loaded"
                + (" (identical to the healthy page)" if same else " (different data, no notice)"),
                "The page is serving cached/mock/static content without telling the user. Show an error or a "
                "clearly labelled 'showing saved data' notice.", f"visible text after fault: {visible[:200]!r}")
        elif not shows_error:
            add("WEB004", "high", "Data failed to load and the user is not told",
                "Render an explicit error state for this fault with a retry action.",
                f"visible text after fault: {visible[:200]!r}")
        elif not has_recovery:
            add("WEB006", "medium", "Error is shown but the user has no way to recover",
                "Add a retry / go back / contact action next to the message.",
                f"actions on screen: {snap.actions[:10]}")

    if fault.name == "empty" and snap.intercepted > 0 and not EMPTY_COPY.search(visible) and not shows_error \
            and base is not None and snap.words < base.words:
        add("WEB008", "medium", "No empty state: a first-time user sees a bare page with no guidance",
            "Show what will appear here and the action that creates the first item.",
            f"visible text: {visible[:200]!r}")

    if snap.page_errors:
        add("WEB007", "medium", "Uncaught exception in the page under this fault",
            "Handle the failure in the data layer; an uncaught error usually means a broken screen state.",
            snap.page_errors[0])
    return out
