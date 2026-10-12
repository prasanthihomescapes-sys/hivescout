"""Static lint entry point: detect apps under a root and run the matching rule sets."""
from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

from ..findings import Finding
from ..workspace import detect_apps, walk
from . import js, python_api, structure


def _config(root: Path) -> dict:
    for name in (".hivescout.yml", ".hivescout.yaml"):
        f = root / name
        if f.exists():
            return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {}


def lint(root: Path, target: str | None = None, stack: str | None = None) -> tuple[list[Finding], list[tuple[str, str]]]:
    """Return (findings, apps) where apps is [(stack, app_path_relative_to_root)]."""
    root = root.resolve()
    target = target or root.name
    cfg = _config(root)
    ignore_rules = set(cfg.get("ignore_rules", []))
    ignore_paths = list(cfg.get("ignore_paths", []))
    apps = [(stack, root)] if stack else detect_apps(root)
    findings: list[Finding] = []
    seen_files: set[Path] = set()
    for st, app in apps:
        if st == "nextjs":
            findings += structure.check_nextjs(app, target)
        elif st == "react":
            findings += structure.check_react(app, target)
        elif st == "expo":
            findings += structure.check_expo(app, target)
        if st in ("nextjs", "react", "expo"):
            for f in walk(app, js.JS_SUFFIXES):
                if f.name.endswith(".d.ts") or f in seen_files:
                    continue
                seen_files.add(f)
                findings += js.check_file(f, app, target)
        elif st == "python":
            for f in walk(app, (".py",)):
                if f in seen_files:
                    continue
                seen_files.add(f)
                findings += python_api.check_file(f, app, target)
        # report paths relative to the scanned root, not the app
        prefix = app.relative_to(root).as_posix()
        for fd in findings:
            if fd.extra.get("_rooted"):
                continue
            fd.extra["_rooted"] = True
            if fd.file is not None and prefix != ".":
                fd.file = f"{prefix}/{fd.file}"
            elif fd.file is None:
                fd.file = (prefix + "/") if prefix != "." else "./"
    out = []
    for fd in findings:
        fd.extra.pop("_rooted", None)
        if fd.rule in ignore_rules or any(fnmatch.fnmatch(fd.file or "", p) for p in ignore_paths):
            continue
        out.append(fd)
    return out, [(s, a.relative_to(root).as_posix()) for s, a in apps]
