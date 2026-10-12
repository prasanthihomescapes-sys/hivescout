"""The one data shape every channel (lint, contract, probe) emits."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SEVERITIES = ("high", "medium", "low")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass
class Finding:
    rule: str                      # e.g. NX001, PY002, WEB-SILENT
    severity: str                  # high | medium | low
    title: str                     # what the user would experience, in one line
    channel: str                   # lint | contract | web | api
    target: str = ""               # project / app name
    file: str | None = None        # repo-relative path for lint findings
    line: int | None = None
    route: str | None = None       # probe findings
    fault: str | None = None       # probe findings
    evidence: str = ""             # snippet, visible text, console error
    fix: str = ""                  # what to do
    refs: list[str] = field(default_factory=list)       # prior-lesson ids / docs that motivated the rule
    artifacts: list[str] = field(default_factory=list)  # screenshots etc.
    extra: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple:
        return (self.rule, self.target, self.file, self.line, self.route, self.fault)


def base_title(f: "Finding") -> str:
    """Title without per-instance suffixes such as "(12 of 14 routes, none covered)"."""
    import re
    return re.sub(r" \((\d+ of \d+ routes[^)]*|identical to the healthy page|different data, no notice)\)$", "", f.title)


def sort_findings(items: list[Finding]) -> list[Finding]:
    return sorted(items, key=lambda f: (_RANK.get(f.severity, 9), f.rule, f.file or "", f.line or 0, f.route or ""))


def at_or_above(items: list[Finding], level: str) -> list[Finding]:
    cut = _RANK[level]
    return [f for f in items if _RANK.get(f.severity, 9) <= cut]


def dump(items: list[Finding], path: Path, meta: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from .redact import redact

    rows = []
    for f in sort_findings(items):
        d = asdict(f)
        d["evidence"] = redact(d["evidence"])
        rows.append(d)
    payload = {"meta": meta or {}, "findings": rows}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> tuple[dict[str, Any], list[Finding]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("meta", {}), [Finding(**f) for f in data.get("findings", [])]
