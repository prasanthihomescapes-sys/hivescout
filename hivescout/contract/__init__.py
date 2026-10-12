"""UX contracts: the design-time half. Declare every non-happy state before code exists, then gate on it.

Layout: one YAML file per surface in a contracts folder, e.g. `contracts/shop/web.yml`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import yaml

from ..findings import Finding
from ..lint.structure import PAGE, _app_dirs, _route_of
from ..workspace import detect_apps, walk

SCHEMA = json.loads((Path(__file__).parent / "schema.json").read_text(encoding="utf-8"))

REQUIRED = {
    "web": ["loading", "empty", "error", "offline"],
    "mobile": ["loading", "empty", "error", "offline"],
    "api": ["error", "timeout", "unauthorized", "invalid_input"],
    "convo": ["error", "unclear_input", "timeout"],
}
SURFACE_OF_STACK = {"nextjs": "web", "react": "web", "expo": "mobile", "python": "api"}
TODO = "TODO"


# ----------------------------------------------------------------------------- route discovery

_PY_ROUTE = re.compile(r"@\w+\.(get|post|put|patch|delete|route)\(\s*[rf]?[\"']([^\"']+)[\"']", re.I)
_RR_ROUTE = re.compile(r"(?:<Route[^>]*\spath=|\bpath:\s*)[\"']([^\"']+)[\"']")


def discover_routes(stack: str, app: Path) -> list[str]:
    routes: set[str] = set()
    if stack == "nextjs":
        for a in _app_dirs(app):
            for p in walk(a, (".tsx", ".jsx", ".ts", ".js", ".mdx")):
                if PAGE.match(p.name):
                    routes.add(_route_of(a, p.parent))
    elif stack == "expo":
        for a in _app_dirs(app):
            for p in walk(a, (".tsx", ".jsx", ".ts", ".js")):
                if p.stem.startswith(("_", "+")):
                    continue
                r = _route_of(a, p.parent).rstrip("/") + ("" if p.stem == "index" else "/" + p.stem)
                routes.add(r or "/")
    elif stack == "react":
        for p in walk(app, (".tsx", ".jsx", ".ts", ".js")):
            routes.update(m.group(1) for m in _RR_ROUTE.finditer(p.read_text(encoding="utf-8", errors="replace")))
    elif stack == "python":
        for p in walk(app, (".py",)):
            for m in _PY_ROUTE.finditer(p.read_text(encoding="utf-8", errors="replace")):
                verb = "ANY" if m.group(1).lower() == "route" else m.group(1).upper()
                routes.add(f"{verb} {m.group(2)}")
    return sorted(routes)


def flow_id(route: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", route.lower()).strip("-")
    return s or "root"


# ----------------------------------------------------------------------------- scaffold

def scaffold(surface: str, routes: list[str], target: str) -> dict:
    flows = []
    for r in routes:
        states = {s: {"shows": TODO, "recovery": "retry"} for s in REQUIRED[surface]}
        if surface in ("web", "mobile") and "[" in r:
            states["not_found"] = {"shows": TODO, "recovery": "back"}
        flows.append({"id": flow_id(r), "route": r, "goal": TODO, "fallback_policy": "error_only", "states": states})
    return {"version": 1, "target": target, "surface": surface, "flows": flows}


def init(app_root: Path, out_dir: Path, target: str, update: bool = False) -> list[Path]:
    """Write a TODO-filled contract per surface for every app under app_root.

    Existing contracts are never overwritten; with update=True, flows for routes that appeared in code since
    are appended and existing flows are left untouched.
    """
    written = []
    by_surface: dict[str, list[str]] = {}
    for stack, app in detect_apps(app_root):
        surface = SURFACE_OF_STACK[stack]
        by_surface.setdefault(surface, []).extend(discover_routes(stack, app))
    out_dir.mkdir(parents=True, exist_ok=True)
    for surface, routes in by_surface.items():
        f = out_dir / f"{surface}.yml"
        header = (f"# hivescout UX contract ({surface}). Fill every TODO before building the flow.\n"
                  "# A state that cannot happen: replace it with `not_applicable: <why>`.\n")
        if f.exists():
            if not update:
                continue
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            have = {fl.get("route") or fl.get("id") for fl in doc.get("flows", [])}
            new_routes = [r for r in sorted(set(routes)) if r not in have]
            if not new_routes:
                continue
            doc.setdefault("flows", []).extend(scaffold(surface, new_routes, target)["flows"])
        else:
            doc = scaffold(surface, sorted(set(routes)), target)
        f.write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        written.append(f)
    return written


# ----------------------------------------------------------------------------- check (the gate)

def _f(rule: str, sev: str, title: str, target: str, file: Path, route: str | None = None, fix: str = "") -> Finding:
    return Finding(rule=rule, severity=sev, title=title, channel="contract", target=target,
                   file=file.as_posix(), route=route, fix=fix)


def load_contracts(folder: Path) -> list[tuple[Path, dict]]:
    out = []
    for f in sorted(list(folder.glob("*.yml")) + list(folder.glob("*.yaml"))):
        if f.name.endswith((".draft.yml", ".draft.yaml")):
            continue
        out.append((f, yaml.safe_load(f.read_text(encoding="utf-8")) or {}))
    return out


def check(folder: Path, target: str, app_root: Path | None = None) -> list[Finding]:
    findings: list[Finding] = []
    contracts = load_contracts(folder) if folder.exists() else []
    if not contracts:
        return [_f("CT000", "high", "No UX contract: non-happy states were never designed", target, folder,
                   fix="Run `hivescout contract init` and fill in every state before building.")]
    declared: dict[str, set[str]] = {}
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for path, doc in contracts:
        errs = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
        for e in errs[:20]:
            loc = "/".join(str(x) for x in e.path)
            findings.append(_f("CT001", "high", f"Contract does not match the schema at {loc or '<root>'}: {e.message}",
                               target, path))
        if errs:
            continue
        surface = doc["surface"]
        declared.setdefault(surface, set())
        for flow in doc["flows"]:
            route = flow.get("route") or flow["id"]
            declared[surface].add(route)
            for s in REQUIRED[surface]:
                if s not in flow["states"]:
                    findings.append(_f("CT002", "high", f"Flow '{flow['id']}' never says what users see when {s}",
                                       target, path, route, fix=f"Add states.{s} (or `not_applicable: <why>`)."))
            todo = [k for k, v in flow["states"].items() if TODO in json.dumps(v)]
            if TODO in str(flow.get("goal", "")):
                todo.append("goal")
            if todo:
                findings.append(_f("CT003", "medium", f"Flow '{flow['id']}' still has TODO in: {', '.join(todo)}",
                                   target, path, route, fix="Decide what the user sees and how they recover."))
            for k, v in flow["states"].items():
                if v.get("recovery") == "none" and k in ("error", "offline", "timeout", "unclear_input"):
                    findings.append(_f("CT004", "medium", f"Flow '{flow['id']}' leaves users stuck on {k} (recovery: none)",
                                       target, path, route, fix="Give a way forward: retry, back, contact or edit input."))
    if app_root is not None:
        for stack, app in detect_apps(app_root):
            surface = SURFACE_OF_STACK[stack]
            have = declared.get(surface, set())
            for r in discover_routes(stack, app):
                if r not in have:
                    findings.append(_f("CT005", "medium", f"Route {r} exists in code but has no contract flow", target,
                                       folder / f"{surface}.yml", r,
                                       fix="Add a flow for it (`hivescout contract init` only writes new files)."))
    return findings
