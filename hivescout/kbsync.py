"""Close the loop: push recurring, high-impact findings into a lessons knowledge base.

Built for SweepKB-style CLIs (`kb.py add --type ... --title ... --problem ... --fix ...`), but any command that
accepts those flags works. One entry per (target, rule), so a rerun refreshes rather than floods.
"""
from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from .findings import Finding, at_or_above, base_title
from .redact import redact


def entries(findings: list[Finding], min_severity: str = "high") -> list[dict]:
    groups: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for f in at_or_above(findings, min_severity):
        groups[(f.target, f.rule)].append(f)
    out = []
    for (target, rule), fs in sorted(groups.items()):
        f0 = fs[0]
        where = sorted({(x.file or "") + (f":{x.line}" if x.line else "") if x.file else f"{x.route} [{x.fault}]"
                        for x in fs})
        title = f"Why does {target} fail users with: {base_title(f0).rstrip('.')}? (hivescout {rule})"
        problem = (f"hivescout {f0.channel} check {rule} found {len(fs)} occurrence(s) in {target}. "
                   f"Examples: {'; '.join(where[:6])}. Evidence: {f0.evidence[:200]}")
        out.append({
            "type": "gotcha", "title": title, "project": target,
            "tags": ",".join(["ux-finding", "hivescout", f0.channel, rule.lower()]),
            "problem": redact(problem), "fix": redact(f0.fix or "See the hivescout report."),
            "why": redact(f0.extra.get("why", "") or "Found from the user side: the code passed its own tests."),
            "apply": f"Rerun `hivescout` for {target} after fixing; the {rule} count should drop to zero.",
        })
    return out


def push(findings: list[Finding], kb_cmd: list[str], *, min_severity: str = "high", source: str | None = None,
         dry_run: bool = False) -> list[str]:
    results = []
    for e in entries(findings, min_severity):
        args = kb_cmd + ["add", "--type", e["type"], "--title", e["title"], "--project", e["project"],
                         "--tags", e["tags"], "--problem", e["problem"], "--fix", e["fix"], "--why", e["why"],
                         "--apply", e["apply"]]
        if source:
            args += ["--source-path", source]
        if dry_run:
            results.append("DRY " + e["title"])
            continue
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        results.append((r.stdout or r.stderr or "").strip().splitlines()[-1] if (r.stdout or r.stderr) else f"exit {r.returncode}")
    return results


def kb_command(kb: str) -> list[str]:
    p = Path(kb)
    return [sys.executable, str(p)] if p.suffix == ".py" else [kb]
