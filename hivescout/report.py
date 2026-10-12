"""findings.json -> a Markdown report a human (or the next Claude session) can act on."""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from .findings import SEVERITIES, Finding, base_title, sort_findings


def _loc(f: Finding) -> str:
    if f.channel == "web" or f.fault:
        return f"`{f.route}` under **{f.fault}**" + (f" ({f.extra.get('persona')})" if f.extra.get("persona") else "")
    if f.file:
        return f"`{f.file}{':' + str(f.line) if f.line else ''}`" + (f" route `{f.route}`" if f.route else "")
    return f"`{f.route or '-'}`"


def render(findings: list[Finding], meta: dict, out_path: Path) -> str:
    findings = sort_findings(findings)
    sev = Counter(f.severity for f in findings)
    lines = [f"# hivescout report: {meta.get('target', '')}".rstrip(), ""]
    if meta.get("generated"):
        lines.append(f"Generated {meta['generated']} · channels: {', '.join(meta.get('channels', [])) or '-'}")
        lines.append("")
    lines.append("| high | medium | low |")
    lines.append("|---:|---:|---:|")
    lines.append(f"| {sev['high']} | {sev['medium']} | {sev['low']} |")
    lines.append("")

    by_rule = Counter((f.rule, base_title(f)) for f in findings)
    if by_rule:
        lines += ["## By rule", "", "| rule | count | what the user experiences |", "|---|---:|---|"]
        for (rule, title), n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {rule} | {n} | {title} |")
        lines.append("")

    cov = meta.get("coverage")
    if cov:
        lines += ["## Probe coverage", "",
                  "A fault marked *not exercised* means the page made no matching browser request, usually because "
                  "it fetches on the server. That route was **not** tested for that fault.", ""]
        faults = meta.get("faults", [])
        lines.append("| route | api calls | " + " | ".join(faults) + " |")
        lines.append("|---|---:|" + "---|" * len(faults))
        for route, c in cov.items():
            cells = []
            for fn in faults:
                v = c["faults"].get(fn, "-")
                cells.append("yes" if v == "exercised" else ("**no**" if v.startswith("not") else v[:40]))
            lines.append(f"| {route} | {c['api_calls']} | " + " | ".join(cells) + " |")
        lines.append("")

    for level in SEVERITIES:
        group = [f for f in findings if f.severity == level]
        if not group:
            continue
        lines += [f"## {level.capitalize()} ({len(group)})", ""]
        for f in group:
            lines.append(f"- **{f.rule}** {f.title}: {_loc(f)}")
            if f.evidence:
                seen = f.evidence[:220].replace("`", "'")
                lines.append(f"  - seen: `{seen}`")
            if f.fix:
                lines.append(f"  - fix: {f.fix}")
            if f.extra.get("why"):
                lines.append(f"  - why it matters: {f.extra['why']}")
            if f.refs:
                lines.append(f"  - prior lessons: {', '.join(f.refs)}")
            for a in f.artifacts:
                try:
                    rel = os.path.relpath(a, out_path.parent).replace("\\", "/")
                except ValueError:
                    rel = a
                lines.append(f"  - screenshot: [{Path(a).name}]({rel})")
        lines.append("")
    if not findings:
        cells = [v for c in (cov or {}).values() for v in c["faults"].values()]
        missed = sum(1 for v in cells if v != "exercised")
        if cells and missed == len(cells):
            lines.append("**Not a pass:** no fault reached any route. These pages made no matching browser requests on "
                         "load, so nothing was tested. Probe signed-in routes (`--storage-state`), widen "
                         "`--api-pattern`, or rely on lint for server-rendered data.")
        elif missed:
            lines.append(f"No findings in what was exercised. {missed} of {len(cells)} route/fault pairs were not "
                         "exercised (see coverage).")
        else:
            lines.append("No findings.")
    text = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return text
