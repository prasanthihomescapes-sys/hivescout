"""Web probe: open each route as a real browser user, inject one fault at a time, judge what the user sees.

Faults are injected on browser-side requests matching `api_pattern`. Server-side fetches (React Server
Components, getServerSideProps) never pass through the browser; the report says so when a fault was
never exercised, rather than passing the route.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from ..findings import Finding
from .faults import DEFAULT_SET, FAULTS, Fault
from .heuristics import Snapshot, judge

INSPECT = (Path(__file__).parent / "inspect.js").read_text(encoding="utf-8")

PERSONAS = {
    "desktop": {"viewport": {"width": 1280, "height": 800}},
    "mobile": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True,
               "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/126.0 Mobile Safari/537.36"},
}


@dataclass
class ProbeConfig:
    base_url: str
    routes: list[str]
    out_dir: Path
    target: str = "app"
    api_pattern: str = "**/api/**"
    faults: list[str] | None = None
    personas: list[str] | None = None
    settle_ms: int = 2500        # wait after load before judging
    hang_wait_ms: int = 9000     # how long a user waits on a hung request before we judge
    nav_timeout_ms: int = 30000
    headless: bool = True
    storage_state: str | None = None  # Playwright auth state for logged-in routes


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-") or "root"


async def _install_fault(context, pattern: str, fault: Fault, counter: dict) -> None:
    if fault.kind == "none":
        async def passthrough(route):
            counter["n"] += 1
            await route.continue_()
        await context.route(pattern, passthrough)
        return

    async def handler(route):
        if route.request.resource_type == "document":
            await route.continue_()  # never break the page navigation itself; break its data
            return
        counter["n"] += 1
        if fault.kind == "status" or fault.kind == "body":
            await route.fulfill(status=fault.status, body=fault.body, content_type=fault.content_type)
        elif fault.kind == "abort":
            await route.abort("internetdisconnected")
        elif fault.kind == "delay":
            try:
                await asyncio.sleep(fault.delay_ms / 1000)
                await route.continue_()
            except Exception:
                pass  # page closed while we slept; nothing to deliver to
        elif fault.kind == "hang":
            release = asyncio.Event()  # the user-visible hang lasts until _visit releases it at teardown
            counter.setdefault("held", []).append(release)
            await release.wait()
            try:
                await route.abort()
            except Exception:
                pass
    await context.route(pattern, handler)


async def _snapshot(page, counter: dict, console: list[str], errors: list[str], status: int | None) -> Snapshot:
    data = await page.evaluate(INSPECT)
    return Snapshot(text=data["text"], media=data["media"], spinners=data["spinners"], actions=data["actions"],
                    alerts=data["alerts"], console_errors=list(console), page_errors=list(errors),
                    intercepted=counter["n"], status=status)


async def _visit(browser, cfg: ProbeConfig, route: str, fault: Fault, persona: str) -> tuple[Snapshot, str | None]:
    kwargs = dict(PERSONAS[persona])
    if cfg.storage_state:
        kwargs["storage_state"] = cfg.storage_state
    context = await browser.new_context(**kwargs)
    counter: dict = {"n": 0}
    await _install_fault(context, cfg.api_pattern, fault, counter)
    page = await context.new_page()
    console: list[str] = []
    errors: list[str] = []
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    url = urljoin(cfg.base_url.rstrip("/") + "/", route.lstrip("/"))
    status = None
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms)
        status = resp.status if resp else None
        if fault.kind == "abort":
            await context.set_offline(True)
        wait = cfg.hang_wait_ms if fault.kind in ("hang", "delay") else cfg.settle_ms
        await page.wait_for_timeout(wait)
        snap = await _snapshot(page, counter, console, errors, status)
        shot_dir = cfg.out_dir / "screens"
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot = shot_dir / f"{_slug(route)}__{fault.name}__{persona}.png"
        await page.screenshot(path=str(shot), full_page=False)
        return snap, shot.as_posix()
    finally:
        for release in counter.get("held", []):
            release.set()
        await asyncio.sleep(0.05)
        await context.close()


async def run_async(cfg: ProbeConfig) -> tuple[list[Finding], dict]:
    from playwright.async_api import async_playwright

    faults = [FAULTS[f] for f in (cfg.faults or DEFAULT_SET)]
    personas = cfg.personas or ["desktop"]
    findings: list[Finding] = []
    coverage: dict[str, dict] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=cfg.headless)
        try:
            for route in cfg.routes:
                for persona in personas:
                    try:
                        base, _ = await _visit(browser, cfg, route, FAULTS["baseline"], persona)
                    except Exception as e:  # the route itself is unreachable: that's a finding, not a crash
                        findings.append(Finding(rule="WEB000", severity="high", channel="web", target=cfg.target,
                                                route=route, fault="baseline", title="Route did not load at all",
                                                evidence=str(e)[:300], fix="Check the server is up and the route exists."))
                        continue
                    cov = coverage.setdefault(f"{route} [{persona}]", {"api_calls": base.intercepted, "faults": {}})
                    if base.status and base.status >= 400:
                        findings.append(Finding(rule="WEB000", severity="high", channel="web", target=cfg.target,
                                                route=route, fault="baseline", title=f"Route returns HTTP {base.status} with no fault injected",
                                                fix="Fix the route or remove it from the probe list."))
                    findings += judge(base, None, FAULTS["baseline"], target=cfg.target, route=route,
                                      persona=persona, shot=None)
                    for fault in faults:
                        try:
                            snap, shot = await _visit(browser, cfg, route, fault, persona)
                        except Exception as e:
                            cov["faults"][fault.name] = f"probe error: {str(e)[:120]}"
                            continue
                        cov["faults"][fault.name] = "exercised" if snap.intercepted else "not exercised (no matching browser request)"
                        findings += judge(snap, base, fault, target=cfg.target, route=route, persona=persona, shot=shot)
        finally:
            await browser.close()
    return findings, {"coverage": coverage, "base_url": cfg.base_url, "api_pattern": cfg.api_pattern,
                      "faults": [f.name for f in faults], "personas": personas}


def run(cfg: ProbeConfig) -> tuple[list[Finding], dict]:
    return asyncio.run(run_async(cfg))
