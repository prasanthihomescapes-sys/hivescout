"""Signed-in sessions for probing what sits behind a login.

`hivescout auth` saves a Playwright storage state (cookies + localStorage) that later probes reuse:

- manual (default): a real browser window opens on the login page. A person signs in and HiveScout saves the
  session once the page leaves the login path. Works with SSO, OAuth and 2FA. No password ever reaches HiveScout.
- scripted: the target's `auth.steps` (journey step syntax) run headless. Values come from environment variables
  (`${env:HIVESCOUT_TEST_PASSWORD}`), so credentials never live in the workspace or the repo. Use a test
  account, never a personal one.

The saved file holds live session cookies: it is written next to the workspace's other private files and
must stay out of version control.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .probe.journey import run_step, validate, Journey


@dataclass
class AuthConfig:
    login_path: str = "/login"
    check_path: str | None = None   # a signed-in page; landing on login_path from here means the session is gone
    state: Path | None = None
    steps: list[dict] = field(default_factory=list)


def _path(url: str) -> str:
    return urlparse(url).path.rstrip("/") or "/"


async def _capture(base_url: str, cfg: AuthConfig, out: Path, *, manual: bool, timeout_s: int) -> Path:
    from playwright.async_api import async_playwright

    login_url = base_url.rstrip("/") + "/" + cfg.login_path.lstrip("/")
    login = cfg.login_path.rstrip("/") or "/"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not manual)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(login_url, wait_until="domcontentloaded")
            if manual:
                print(f"Sign in in the browser window ({login_url}). Waiting up to {timeout_s // 60} min "
                      "for the page to leave the login path...", flush=True)
            else:
                for i, step in enumerate(cfg.steps):
                    try:
                        await run_step(page, step, base_url)
                    except KeyError as e:  # a missing ${env:...}
                        raise SystemExit(f"auth step {i + 1}: {e.args[0]}") from None
            deadline = time.monotonic() + timeout_s
            while _path(page.url) == login:
                if time.monotonic() > deadline:
                    raise SystemExit("still on the login page; sign-in did not complete "
                                     "(wrong credentials, a CAPTCHA, or a different success URL?)")
                await page.wait_for_timeout(500)
            await page.wait_for_timeout(1500)  # let post-login redirects settle and cookies land
            out.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(out))
        finally:
            await browser.close()
    return out


def capture(base_url: str, cfg: AuthConfig, out: Path, *, manual: bool | None = None, timeout_s: int = 300) -> Path:
    if manual is None:
        manual = not cfg.steps
    if not manual:
        problems = validate(Journey(id="auth", start=cfg.login_path, act=[{"wait": 0}], steps=cfg.steps))
        if problems:
            raise SystemExit("auth.steps: " + "; ".join(problems))
    return asyncio.run(_capture(base_url, cfg, out, manual=manual, timeout_s=timeout_s))


async def _check(base_url: str, cfg: AuthConfig, state: Path) -> tuple[bool, str]:
    from playwright.async_api import async_playwright

    target = cfg.check_path or "/"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context(storage_state=str(state))
            page = await context.new_page()
            await page.goto(base_url.rstrip("/") + "/" + target.lstrip("/"), wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            landed = page.url
        finally:
            await browser.close()
    ok = _path(landed) != (cfg.login_path.rstrip("/") or "/")
    return ok, landed


def check(base_url: str, cfg: AuthConfig, state: Path) -> tuple[bool, str]:
    """(still signed in?, where the check page landed)."""
    if not state.exists():
        return False, f"no saved session at {state}"
    return asyncio.run(_check(base_url, cfg, state))


def describe_state(state: Path) -> str:
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unreadable"
    cookies = data.get("cookies", [])
    soonest = min((c["expires"] for c in cookies if c.get("expires", -1) > 0), default=None)
    left = f", earliest cookie expiry in {int((soonest - time.time()) / 3600)} h" if soonest else ""
    return f"{len(cookies)} cookies, {len(data.get('origins', []))} origins with storage{left}"
