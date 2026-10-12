"""Targets and app discovery.

A *workspace* is a private folder (kept out of this repo) holding `targets.json`, contracts and run output:

    {"targets": [{"name": "shop", "path": "C:/code/shop", "base_url": "http://localhost:3000",
                  "api_pattern": "**/api/**", "contracts": "contracts/shop"}]}

Paths in targets.json are absolute or relative to the workspace folder.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    "node_modules", ".next", ".git", "dist", "build", "out", ".venv", "venv", "env", "__pycache__", "coverage",
    ".turbo", ".expo", ".vercel", "android", "ios", ".pytest_cache", ".mypy_cache", ".ruff_cache", "site-packages",
    "playwright-report", "test-results", "public", "static", "vendor", "generated", "storybook-static", "e2e-logs",
}
MINIFIED = (".min.js", ".bundle.js", ".chunk.js")


@dataclass
class Target:
    name: str
    path: Path
    base_url: str | None = None
    api_pattern: str = "**/api/**"
    contracts: Path | None = None
    routes: list[str] = field(default_factory=list)
    stack: str | None = None  # override auto-detection


def workspace_dir(explicit: str | None) -> Path | None:
    w = explicit or os.environ.get("HIVESCOUT_WORKSPACE")
    return Path(w).expanduser().resolve() if w else None


def load_targets(ws: Path) -> list[Target]:
    f = ws / "targets.json"
    if not f.exists():
        raise SystemExit(f"no targets.json in workspace {ws}")
    data = json.loads(f.read_text(encoding="utf-8"))
    out = []
    for t in data.get("targets", []):
        p = Path(t["path"])
        c = t.get("contracts")
        out.append(Target(
            name=t["name"], path=p if p.is_absolute() else ws / p, base_url=t.get("base_url"),
            api_pattern=t.get("api_pattern", "**/api/**"),
            contracts=(Path(c) if Path(c).is_absolute() else ws / c) if c else ws / "contracts" / t["name"],
            routes=t.get("routes", []), stack=t.get("stack"),
        ))
    return out


def pick(targets: list[Target], name: str) -> Target:
    for t in targets:
        if t.name == name:
            return t
    raise SystemExit(f"unknown target {name!r}; known: {', '.join(t.name for t in targets)}")


def walk(root: Path, suffixes: tuple[str, ...]):
    """Yield files under root with the given suffixes, skipping vendored/build dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(suffixes) and not fn.endswith(MINIFIED):
                yield Path(dirpath) / fn


def _pkg_deps(pkg: Path) -> set[str]:
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    deps: set[str] = set()
    for k in ("dependencies", "devDependencies", "peerDependencies"):
        deps |= set((data.get(k) or {}).keys())
    return deps


def detect_apps(root: Path) -> list[tuple[str, Path]]:
    """Return [(stack, app_root)] for every app found under root (monorepo aware).

    stack is one of: nextjs, expo, react, python.
    """
    apps: list[tuple[str, Path]] = []
    for pkg in walk(root, ("package.json",)):
        deps = _pkg_deps(pkg)
        if "next" in deps:
            apps.append(("nextjs", pkg.parent))
        elif "expo" in deps or "react-native" in deps:
            apps.append(("expo", pkg.parent))
        elif "react" in deps and ({"vite", "react-scripts", "react-dom"} & deps):
            apps.append(("react", pkg.parent))
    py_markers = ("pyproject.toml", "requirements.txt", "setup.py")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if any(m in filenames for m in py_markers):
            text = ""
            for m in py_markers:
                f = Path(dirpath) / m
                if f.exists():
                    text += f.read_text(encoding="utf-8", errors="replace").lower()
            if any(fw in text for fw in ("fastapi", "flask", "django", "starlette")):
                apps.append(("python", Path(dirpath)))
                dirnames[:] = []  # nested python packages belong to this app
    # drop apps nested inside another app of the same stack
    apps.sort(key=lambda a: len(a[1].parts))
    kept: list[tuple[str, Path]] = []
    for stack, p in apps:
        if not any(s == stack and p != q and q in p.parents for s, q in kept):
            kept.append((stack, p))
    return kept
