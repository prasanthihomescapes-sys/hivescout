"""Shared lint plumbing: rule metadata, suppression comments, line lookup."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..findings import Finding

TEST_FILE = re.compile(r"(\.test\.|\.spec\.|\.stories\.|__tests__|/tests?/|/e2e/|/fixtures?/|/mocks?/|/__mocks__/)", re.I)
SUPPRESS = re.compile(r"hivescout-ignore(?::\s*(?P<rules>[A-Z0-9, -]+))?")


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    title: str
    fix: str
    why: str = ""  # the user-visible harm; shown in reports


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


COMMENT_LINE = re.compile(r"^\s*(//|#|/\*|\*|<!--)")


def suppressed(lines: list[str], line: int, rule_id: str) -> bool:
    """A `hivescout-ignore` (or `hivescout-ignore: RULE1, RULE2`) marker on the flagged line, or anywhere in the
    unbroken comment block directly above it, so the reason can span several lines."""
    candidates = [line]
    ln = line - 1
    while 1 <= ln <= len(lines) and COMMENT_LINE.match(lines[ln - 1]):
        candidates.append(ln)
        ln -= 1
    for ln in candidates:
        if 1 <= ln <= len(lines):
            m = SUPPRESS.search(lines[ln - 1])
            if m and (not m.group("rules") or rule_id in m.group("rules")):
                return True
    return False


def is_test_path(rel: str) -> bool:
    return bool(TEST_FILE.search("/" + rel.replace("\\", "/")))


def make(rule: Rule, *, target: str, file: Path | None, root: Path, line: int | None, evidence: str = "",
         title: str | None = None) -> Finding:
    rel = file.relative_to(root).as_posix() if file else None
    return Finding(rule=rule.id, severity=rule.severity, title=title or rule.title, channel="lint", target=target,
                   file=rel, line=line, evidence=re.sub(r"\s+", " ", evidence).strip()[:300], fix=rule.fix, extra={"why": rule.why} if rule.why else {})


def balanced_span(text: str, open_idx: int, open_ch: str = "(", close_ch: str = ")") -> int:
    """Index just past the bracket matching text[open_idx]; skips string literals. Returns len(text) if unbalanced."""
    depth, i, n, quote = 0, open_idx, len(text), None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n
