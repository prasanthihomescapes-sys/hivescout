"""Web probe: open routes and replay journeys as a real browser user, inject one fault at a time, judge the screen.

Which requests get the fault (see FaultGate):
- requests matching `api_pattern` (fetch/XHR to your API, same- or cross-origin)
- Next.js server actions (POST with a `Next-Action` header), which never match an /api/ pattern
- during a journey's `act` steps: every write request (POST/PUT/PATCH/DELETE), including classic form posts

Server-side fetches (React Server Components, getServerSideProps) never pass through the browser. The coverage
table says so when a fault was never exercised, instead of counting that route as a pass.

Safety: a faulted request is answered locally and never reaches the server. Journeys marked `mutates: true` only
get an unfaulted run with `allow_mutations`, and the `slow` fault releases writes as a 503 unless mutations are
allowed. A probe therefore does not create, change or delete real data by default.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from ..findings import Finding
from .faults import DEFAULT_SET, FAULTS, Fault
from .heuristics import Snapshot, _norm, judge, judge_action
from .journey import Journey, describe, filled_fields, last_click, locate, run_step

INSPECT = (Path(__file__).parent / "inspect.js").read_text(encoding="utf-8")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

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
    storage_state: str | None = None  # Playwright signed-in state (hivescout auth)
    login_path: str | None = None     # where an expired session lands; detected instead of probed
    journeys: list[Journey] | None = None
    allow_mutations: bool = False
    cors_relay: bool = False  # local build reading a prod API that only allows its own origin (see FaultGate)


class SessionExpired(Exception):
    pass


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-") or "root"


def glob_to_regex(glob: str) -> re.Pattern:
    """Playwright-style URL glob: ** any chars, * any chars except '/', {a,b} alternatives."""
    out, i = "", 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**", i):
            out += ".*"
            i += 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "{":
            j = glob.find("}", i)
            if j == -1:
                out += re.escape(c)
            else:
                out += "(" + "|".join(re.escape(p) for p in glob[i + 1:j].split(",")) + ")"
                i = j
        else:
            out += re.escape(c)
        i += 1
    return re.compile("^" + out + "$")


class FaultGate:
    """One handler for every request: decides whether it is the user's data/action and applies the fault."""

    def __init__(self, pattern: str, fault: Fault, *, journey: bool = False, allow_mutations: bool = False,
                 cors_relay: bool = False):
        self.rx = glob_to_regex(pattern)
        self.cors_relay = cors_relay
        self.fault = fault
        self.journey = journey
        self.allow_mutations = allow_mutations
        self.armed = not journey     # journeys arm at the first `act` step
        self.n = 0                   # requests that received the fault (or passed through, for baseline)
        self.writes = 0              # write requests seen while armed
        self.held: list[asyncio.Event] = []

    def targets(self, req) -> bool:
        if req.resource_type == "document" and req.method == "GET":
            return False  # never break navigation itself; break its data
        if self.rx.match(req.url):
            return True
        headers = req.headers
        if "next-action" in headers:
            return True
        return self.journey and self.armed and req.method in WRITE_METHODS

    async def _pass(self, route) -> None:
        """Let a request through. With cors_relay, API reads are fetched by the probe itself and handed to the page
        with an allow-origin header, so a localhost build can read a prod API that only allows its own origin.
        Only the probe's throwaway browser sees the relaxed header; the browser's security settings are untouched."""
        req = route.request
        if not (self.cors_relay and self.rx.match(req.url)):
            await route.continue_()
            return
        origin = req.headers.get("origin") or "*"
        cors = {"access-control-allow-origin": origin, "access-control-allow-credentials": "true",
                "access-control-allow-headers": req.headers.get("access-control-request-headers", "*"),
                "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS"}
        if req.method == "OPTIONS":
            await route.fulfill(status=204, headers=cors)
            return
        resp = await route.fetch()
        await route.fulfill(response=resp, headers={**resp.headers, **cors})

    async def handle(self, route) -> None:
        req = route.request
        if not self.armed or not self.targets(req):
            try:
                await self._pass(route)
            except Exception:
                pass  # page or context closed mid-request
            return
        self.n += 1
        is_write = req.method in WRITE_METHODS
        if is_write:
            self.writes += 1
        f = self.fault
        try:
            if f.kind == "none":
                await self._pass(route)
            elif f.kind in ("status", "body"):
                await route.fulfill(status=f.status, body=f.body, content_type=f.content_type)
            elif f.kind == "abort":
                await route.abort("internetdisconnected")
            elif f.kind == "delay":
                await asyncio.sleep(f.delay_ms / 1000)
                if is_write and self.journey and not self.allow_mutations:
                    # slow, then refused: the user still waits, but no write reaches the server
                    await route.fulfill(status=503, body='{"error":"unavailable"}', content_type="application/json")
                else:
                    await self._pass(route)
            elif f.kind == "hang":
                release = asyncio.Event()
                self.held.append(release)
                await release.wait()
                await route.abort()
        except Exception:
            pass  # page or context closed mid-request; nothing left to answer

    async def release(self) -> None:
        for ev in self.held:
            ev.set()
        await asyncio.sleep(0.05)


async def _snapshot(page, gate: FaultGate, console: list[str], errors: list[str], status: int | None) -> Snapshot:
    data = await page.evaluate(INSPECT)
    return Snapshot(text=data["text"], media=data["media"], spinners=data["spinners"], actions=data["actions"],
                    alerts=data["alerts"], console_errors=list(console), page_errors=list(errors),
                    intercepted=gate.n, status=status, url=data["url"], disabled=data["disabled"], busy=data["busy"])


async def _new_context(browser, cfg: ProbeConfig, persona: str, signed_in: bool):
    kwargs = dict(PERSONAS[persona])
    if signed_in and cfg.storage_state:
        kwargs["storage_state"] = cfg.storage_state
    return await browser.new_context(**kwargs)


def _on_login(cfg: ProbeConfig, url: str, wanted: str) -> bool:
    if not cfg.login_path:
        return False
    path = urlparse(url).path.rstrip("/") or "/"
    login = cfg.login_path.rstrip("/") or "/"
    return path == login and (urlparse(wanted).path.rstrip("/") or "/") != login


def _url(cfg: ProbeConfig, path: str) -> str:
    return urljoin(cfg.base_url.rstrip("/") + "/", path.lstrip("/"))


def _shot_path(cfg: ProbeConfig, name: str) -> Path:
    d = cfg.out_dir / "screens"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.png"


# ----------------------------------------------------------------------------- routes

async def _visit(browser, cfg: ProbeConfig, route: str, fault: Fault, persona: str) -> tuple[Snapshot, str | None]:
    context = await _new_context(browser, cfg, persona, signed_in=True)
    gate = FaultGate(cfg.api_pattern, fault, cors_relay=cfg.cors_relay)
    await context.route("**/*", gate.handle)
    page = await context.new_page()
    console: list[str] = []
    errors: list[str] = []
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    url = _url(cfg, route)
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms)
        status = resp.status if resp else None
        if fault.kind == "abort":
            await context.set_offline(True)
        await page.wait_for_timeout(cfg.hang_wait_ms if fault.kind in ("hang", "delay") else cfg.settle_ms)
        snap = await _snapshot(page, gate, console, errors, status)
        if _on_login(cfg, snap.url, url):
            raise SessionExpired(snap.url)
        shot = _shot_path(cfg, f"{_slug(route)}__{fault.name}__{persona}")
        await page.screenshot(path=str(shot))
        return snap, shot.as_posix()
    finally:
        await gate.release()
        await context.close()


def _expired_finding(cfg: ProbeConfig, where: str, url: str) -> Finding:
    return Finding(rule="WEB000", severity="high", channel="web", target=cfg.target, route=where, fault="baseline",
                   title="Signed-in session missing or expired: the probe landed on the login page",
                   evidence=f"redirected to {url}",
                   fix=f"Run `hivescout auth --target {cfg.target}` to capture a fresh session, then probe again. "
                       "Nothing behind sign-in was tested in this run.")


async def _probe_routes(browser, cfg: ProbeConfig, faults: list[Fault], personas: list[str],
                        findings: list[Finding], coverage: dict) -> None:
    for route in cfg.routes:
        for persona in personas:
            try:
                base, _ = await _visit(browser, cfg, route, FAULTS["baseline"], persona)
            except SessionExpired as e:
                findings.append(_expired_finding(cfg, route, str(e)))
                coverage[f"{route} [{persona}]"] = {"api_calls": 0, "faults": {f.name: "not exercised (session expired)" for f in faults}}
                continue
            except Exception as e:  # the route itself is unreachable: that's a finding, not a crash
                findings.append(Finding(rule="WEB000", severity="high", channel="web", target=cfg.target,
                                        route=route, fault="baseline", title="Route did not load at all",
                                        evidence=str(e)[:300], fix="Check the server is up and the route exists."))
                continue
            cov = coverage.setdefault(f"{route} [{persona}]", {"api_calls": base.intercepted, "faults": {}})
            if base.status and base.status >= 400:
                findings.append(Finding(rule="WEB000", severity="high", channel="web", target=cfg.target,
                                        route=route, fault="baseline",
                                        title=f"Route returns HTTP {base.status} with no fault injected",
                                        fix="Fix the route or remove it from the probe list."))
            findings += judge(base, None, FAULTS["baseline"], target=cfg.target, route=route, persona=persona, shot=None)
            for fault in faults:
                try:
                    snap, shot = await _visit(browser, cfg, route, fault, persona)
                except Exception as e:
                    cov["faults"][fault.name] = f"probe error: {str(e)[:120]}"
                    continue
                cov["faults"][fault.name] = "exercised" if snap.intercepted else "not exercised (no matching browser request)"
                findings += judge(snap, base, fault, target=cfg.target, route=route, persona=persona, shot=shot)


# ----------------------------------------------------------------------------- journeys

@dataclass
class ActResult:
    snap: Snapshot
    shot: str | None
    lost: list[str]
    feedback: bool | None
    sends: int
    failed_step: str | None
    recovery_visible: bool


async def _field_values(page, j: Journey) -> dict[str, str]:
    vals = {}
    for loc in filled_fields(j):
        try:
            lc = locate(page, loc)
            if await lc.count() == 0 or (await lc.get_attribute("type") or "") == "password":
                continue  # clearing a password after failure is acceptable
            vals[describe(loc)] = await lc.input_value(timeout=1000)
        except Exception:
            continue
    return vals


async def _journey_visit(browser, cfg: ProbeConfig, j: Journey, fault: Fault, persona: str) -> ActResult:
    signed_in = j.auth if j.auth is not None else bool(cfg.storage_state)
    context = await _new_context(browser, cfg, persona, signed_in=signed_in)
    gate = FaultGate(cfg.api_pattern, fault, journey=True, allow_mutations=cfg.allow_mutations,
                     cors_relay=cfg.cors_relay)
    await context.route("**/*", gate.handle)
    page = await context.new_page()
    console: list[str] = []
    errors: list[str] = []
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    start = _url(cfg, j.start)
    try:
        resp = await page.goto(start, wait_until="domcontentloaded", timeout=cfg.nav_timeout_ms)
        status = resp.status if resp else None
        await page.wait_for_timeout(min(cfg.settle_ms, 1500))
        if signed_in and _on_login(cfg, page.url, start):
            raise SessionExpired(page.url)
        for i, step in enumerate(j.steps):
            try:
                await run_step(page, step, cfg.base_url)
            except Exception as e:
                raise RuntimeError(f"setup step {i + 1} {step}: {str(e).splitlines()[0][:200]}") from e
        typed = await _field_values(page, j)
        pre = await _snapshot(page, gate, console, errors, status)

        gate.armed = True
        if fault.kind == "abort":
            await context.set_offline(True)
        failed_step = None
        click_loc = last_click(j)
        for i, step in enumerate(j.act):
            try:
                await run_step(page, step, cfg.base_url, timeout_ms=5000)
            except Exception as e:
                failed_step = f"act step {i + 1} {step}: {str(e).splitlines()[0][:160]}"
                break
            if fault.kind == "delay" and click_loc is not None and step.get("click") is click_loc:
                await page.wait_for_timeout(150)
                try:  # the impatient second click; a disabled button makes this time out, which is the good case
                    await locate(page, click_loc).click(timeout=800)
                except Exception:
                    pass
        feedback = None
        if fault.kind in ("delay", "hang") and failed_step is None:
            await page.wait_for_timeout(700)
            mid = await _snapshot(page, gate, console, errors, status)
            feedback = (mid.spinners > pre.spinners or mid.disabled > pre.disabled or mid.busy > pre.busy
                        or _norm(mid.text) != _norm(pre.text) or mid.alerts != pre.alerts)
        await page.wait_for_timeout(cfg.hang_wait_ms if fault.kind in ("hang", "delay") else cfg.settle_ms)
        snap = await _snapshot(page, gate, console, errors, status)
        after = await _field_values(page, j)
        lost = [k for k, v in typed.items() if v and k in after and after[k] == ""]
        recovery_visible = False
        if click_loc is not None:
            try:
                lc = locate(page, click_loc)
                recovery_visible = await lc.is_visible() and await lc.is_enabled()
            except Exception:
                pass
        shot = _shot_path(cfg, f"journey-{_slug(j.id)}__{fault.name}__{persona}")
        await page.screenshot(path=str(shot))
        return ActResult(snap, shot.as_posix(), lost, feedback, gate.writes, failed_step, recovery_visible)
    finally:
        await gate.release()
        await context.close()


def _expect_ok(j: Journey, r: ActResult) -> str | None:
    """Return why the unfaulted run did not reach the journey's success state, or None."""
    e = j.expect
    if r.failed_step:
        return r.failed_step
    if "text" in e and str(e["text"]).lower() not in r.snap.text.lower():
        return f"expected text {e['text']!r} not on screen"
    if "url_contains" in e and e["url_contains"] not in r.snap.url:
        return f"expected URL containing {e['url_contains']!r}, got {r.snap.url}"
    if "url_not" in e and urlparse(r.snap.url).path.rstrip("/") == str(e["url_not"]).rstrip("/"):
        return f"still on {e['url_not']} after the action"
    return None


async def _probe_journeys(browser, cfg: ProbeConfig, faults: list[Fault], personas: list[str],
                          findings: list[Finding], coverage: dict) -> None:
    for j in cfg.journeys or []:
        where = f"journey:{j.id}"
        for persona in personas:
            key = f"{where} [{persona}]"
            cov = coverage.setdefault(key, {"api_calls": 0, "faults": {}})
            base: ActResult | None = None
            if not j.mutates or cfg.allow_mutations:
                try:
                    base = await _journey_visit(browser, cfg, j, FAULTS["baseline"], persona)
                except SessionExpired as e:
                    findings.append(_expired_finding(cfg, where, str(e)))
                    cov["faults"] = {f.name: "not exercised (session expired)" for f in faults}
                    continue
                except Exception as e:
                    findings.append(Finding(rule="JNY000", severity="high", channel="journey", target=cfg.target,
                                            route=where, fault="baseline", title="Journey cannot even start with no fault injected",
                                            evidence=str(e)[:300], fix="Fix the journey steps (locators/values) or the page."))
                    continue
                cov["api_calls"] = base.snap.intercepted
                why = _expect_ok(j, base)
                if why:
                    findings.append(Finding(rule="JNY000", severity="high", channel="journey", target=cfg.target,
                                            route=where, fault="baseline", artifacts=[base.shot] if base.shot else [],
                                            title="The happy path itself fails: the journey does not succeed with no fault",
                                            evidence=why, fix="Fix the flow (or the journey's `expect`) before testing failures."))
            for fault in faults:
                try:
                    r = await _journey_visit(browser, cfg, j, fault, persona)
                except SessionExpired as e:
                    findings.append(_expired_finding(cfg, where, str(e)))
                    cov["faults"] = {f.name: "not exercised (session expired)" for f in faults}
                    break
                except Exception as e:
                    cov["faults"][fault.name] = f"probe error: {str(e)[:120]}"
                    if not findings or findings[-1].rule != "JNY000" or findings[-1].route != where:
                        findings.append(Finding(rule="JNY000", severity="high", channel="journey", target=cfg.target,
                                                route=where, fault=fault.name, title="Journey setup failed under this fault",
                                                evidence=str(e)[:300], fix="Check the setup steps' locators; setup runs without faults."))
                    continue
                if not r.snap.intercepted and fault.kind == "abort":
                    # the browser really is offline; an app that notices and never sends is still judged on its screen
                    r.snap.intercepted = 1
                    cov["faults"][fault.name] = "exercised (app sent nothing while offline)"
                else:
                    cov["faults"][fault.name] = ("exercised" if r.snap.intercepted
                                                 else "not exercised (the action sent no matching request)")
                if not r.snap.intercepted:
                    continue
                extra = {"journey": j.id, "flow": j.flow}
                findings += judge(r.snap, base.snap if base else None, fault, target=cfg.target, route=where,
                                  persona=persona, shot=r.shot, recovery_visible=r.recovery_visible,
                                  channel="journey", extra=extra)
                findings += judge_action(lost=r.lost, feedback=r.feedback, sends=r.sends if fault.kind == "delay" else 0,
                                         fault=fault, target=cfg.target, route=where, persona=persona, shot=r.shot,
                                         extra=extra)


# ----------------------------------------------------------------------------- entry

async def run_async(cfg: ProbeConfig) -> tuple[list[Finding], dict]:
    from playwright.async_api import async_playwright

    faults = [FAULTS[f] for f in (cfg.faults or DEFAULT_SET)]
    personas = cfg.personas or ["desktop"]
    findings: list[Finding] = []
    coverage: dict[str, dict] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=cfg.headless)
        try:
            await _probe_routes(browser, cfg, faults, personas, findings, coverage)
            await _probe_journeys(browser, cfg, faults, personas, findings, coverage)
        finally:
            await browser.close()
    return findings, {"coverage": coverage, "base_url": cfg.base_url, "api_pattern": cfg.api_pattern,
                      "faults": [f.name for f in faults], "personas": personas,
                      "journeys": [j.id for j in cfg.journeys or []], "signed_in": bool(cfg.storage_state),
                      "allow_mutations": cfg.allow_mutations, "cors_relay": cfg.cors_relay}


def run(cfg: ProbeConfig) -> tuple[list[Finding], dict]:
    return asyncio.run(run_async(cfg))
