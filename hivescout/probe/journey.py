"""Journeys: short user tasks (sign in, save a form, place an order) replayed under faults.

A journey has `steps` (setup, run normally) and `act` (the user action; faults are armed from here):

    journeys:
      - id: save-order
        start: /orders/new
        steps:
          - fill: {label: Order name, value: "HiveScout test order"}
        act:
          - click: {role: button, name: Save}
        expect: {text: "Order saved"}      # success check for the unfaulted run
        mutates: true                      # unfaulted run needs --allow-mutations

Locators: role (+ name), label, placeholder, text, testid or selector; `exact` and `nth` are optional.
Values may reference the environment: "${env:HIVESCOUT_TEST_PASSWORD}" or "${env:NAME|default}".
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

ACTIONS = ("goto", "fill", "click", "select", "check", "uncheck", "press", "wait", "expect_text", "expect_visible")
LOCATOR_KEYS = ("role", "label", "placeholder", "text", "testid", "selector")
_ENV = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)(?:\|([^}]*))?\}")


@dataclass
class Journey:
    id: str
    start: str
    steps: list[dict] = field(default_factory=list)
    act: list[dict] = field(default_factory=list)
    expect: dict = field(default_factory=dict)
    mutates: bool = True
    auth: bool | None = None   # None: use the target's signed-in state if it has one
    flow: str | None = None

    @classmethod
    def from_dict(cls, d: dict, flow: str | None = None) -> Journey:
        j = cls(id=d["id"], start=d.get("start") or "/", steps=list(d.get("steps") or []), act=list(d.get("act") or []),
                expect=dict(d.get("expect") or {}), mutates=bool(d.get("mutates", True)), auth=d.get("auth"), flow=flow)
        problems = validate(j)
        if problems:
            raise ValueError(f"journey {j.id!r}: " + "; ".join(problems))
        return j


def validate(j: Journey) -> list[str]:
    out = []
    if not j.start.startswith("/"):
        out.append("start must be a path starting with '/'")
    if not j.act:
        out.append("needs at least one `act` step (the user action to break)")
    for i, step in enumerate(j.steps + j.act):
        if not isinstance(step, dict) or len(step) != 1:
            out.append(f"step {i + 1} must be a single-key mapping like {{click: {{...}}}}")
            continue
        (action, arg), = step.items()
        if action not in ACTIONS:
            out.append(f"step {i + 1}: unknown action {action!r} (use {', '.join(ACTIONS)})")
        elif action in ("fill", "click", "select", "check", "uncheck", "press", "expect_visible"):
            if not isinstance(arg, dict) or not any(k in arg for k in LOCATOR_KEYS):
                out.append(f"step {i + 1}: {action} needs a locator ({', '.join(LOCATOR_KEYS)})")
            elif action in ("fill", "select") and "value" not in arg:
                out.append(f"step {i + 1}: {action} needs a value")
            elif action == "press" and "key" not in arg:
                out.append(f"step {i + 1}: press needs a key")
    return out


def resolve(value: str) -> str:
    """Expand ${env:NAME} / ${env:NAME|default}. A missing variable without a default is an error, not ''."""
    def repl(m: re.Match) -> str:
        v = os.environ.get(m.group(1))
        if v is None:
            if m.group(2) is None:
                raise KeyError(f"environment variable {m.group(1)} is not set (journey value {m.group(0)})")
            return m.group(2)
        return v
    return _ENV.sub(repl, str(value))


def describe(loc: dict) -> str:
    for k in LOCATOR_KEYS:
        if k in loc:
            return f"{k}={loc[k]!r}" + (f" name={loc['name']!r}" if k == "role" and "name" in loc else "")
    return repr(loc)


def locate(page, loc: dict):
    exact = loc.get("exact")
    if "role" in loc:
        kw = {"name": loc["name"]} if "name" in loc else {}
        if exact is not None and "name" in loc:
            kw["exact"] = exact
        lc = page.get_by_role(loc["role"], **kw)
    elif "label" in loc:
        lc = page.get_by_label(loc["label"], exact=bool(exact))
    elif "placeholder" in loc:
        lc = page.get_by_placeholder(loc["placeholder"], exact=bool(exact))
    elif "text" in loc:
        lc = page.get_by_text(loc["text"], exact=bool(exact))
    elif "testid" in loc:
        lc = page.get_by_test_id(loc["testid"])
    else:
        lc = page.locator(loc["selector"])
    return lc.nth(int(loc["nth"])) if "nth" in loc else lc.first


async def run_step(page, step: dict, base_url: str, timeout_ms: int = 10000) -> None:
    (action, arg), = step.items()
    if action == "goto":
        await page.goto(base_url.rstrip("/") + "/" + str(arg).lstrip("/"), wait_until="domcontentloaded")
    elif action == "wait":
        await page.wait_for_timeout(int(arg))
    elif action == "expect_text":
        await page.get_by_text(str(arg)).first.wait_for(state="visible", timeout=timeout_ms)
    elif action == "expect_visible":
        await locate(page, arg).wait_for(state="visible", timeout=timeout_ms)
    elif action == "fill":
        await locate(page, arg).fill(resolve(arg["value"]), timeout=timeout_ms)
    elif action == "select":
        await locate(page, arg).select_option(resolve(arg["value"]), timeout=timeout_ms)
    elif action in ("check", "uncheck"):
        lc = locate(page, arg)
        await (lc.check(timeout=timeout_ms) if action == "check" else lc.uncheck(timeout=timeout_ms))
    elif action == "press":
        await locate(page, arg).press(arg["key"], timeout=timeout_ms)
    elif action == "click":
        await locate(page, arg).click(timeout=timeout_ms)


def last_click(j: Journey) -> dict | None:
    for step in reversed(j.act):
        if "click" in step:
            return step["click"]
    return None


def filled_fields(j: Journey) -> list[dict]:
    """Locators the user typed into before acting: their content should survive a failed submit."""
    return [s["fill"] for s in j.steps + j.act if "fill" in s]


def load_from_contracts(contracts: list[tuple[object, dict]]) -> list[Journey]:
    out = []
    for _path, doc in contracts:
        for flow in doc.get("flows", []) or []:
            for d in flow.get("journeys", []) or []:
                out.append(Journey.from_dict(d, flow=flow.get("id")))
    return out
