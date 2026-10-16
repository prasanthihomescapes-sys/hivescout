"""Rules that run ON test files: a guard test that cannot tell a working guard from a broken one.

TS001: a test of a security/capability guard (RLS policy, permission check, auth gate, rate limit, refusal path)
whose assertions all expect refusal, with nothing proving an allowed caller gets through. Such a test passes when
the guard blocks the wrong people, blocks everyone, or the setup never reached the guard at all (e.g. `set local
role service_role` does not make `auth.role()` return service_role: it reads a JWT claim, so the "service" call
was refused for a different reason and the test still went green).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from ..findings import Finding
from .common import SUPPRESS, Rule, balanced_span, line_of, make, suppressed
from .js import JS_SUFFIXES, strip_comments

TS001 = Rule("TS001", "medium",
             "A guard is only tested for refusal, so a test that passes cannot tell 'blocks the wrong people' "
             "from 'blocks everyone' or 'never ran'",
             "Pair every must-fail assertion with a must-succeed control for an allowed caller.",
             "Refusal-only guard tests stay green when the guard locks out real users, or when the test setup never "
             "reached the guard; the bug ships and users are the first to find it.")

# Guard vocabulary. Bare assertion verbs (throws/raises/rejects) are deliberately NOT guard terms on their own:
# a parser test that only checks that bad input throws is not testing a security guard.
GUARD = re.compile(
    r"\brls\b|row[ _-]?level|\bpolic(?:y|ies)\b|permission|forbidden|unauthori[sz]ed|unauthenticated|"
    r"\b40[13]\b|\b429\b|42501|P0001|insufficient_privilege|not allowed|rate[ _-]?limit|access[ _-]control|"
    r"auth[ _-]?gate|service_role|auth\.(?:role|uid|jwt)\(", re.I)
# "refuses"/"rejects"/"denied" also describe input validation; they mark a guard only when a caller is in play
WEAK_GUARD = re.compile(r"\bden(?:y|ied|ies)\b|\brefus\w*|\breject(?:s|ed|ion)?\b", re.I)
CALLER = re.compile(r"\b(?:users?|callers?|roles?|tenants?|anon\w*|viewers?|admins?|operators?|members?|owners?|"
                    r"guests?|approvers?|sessions?|jwt|logged[ _-]?(?:in|out)|signed[ _-]?(?:in|out))\b", re.I)
# assertion syntax that would otherwise make every throwing test look like a guard test
_ASSERT_NOISE = re.compile(r"\.rejects\b|\.toThrow\w*|\bpytest\.raises\b|\bassertRaises\w*|\bthrows_\w+", re.I)

# words inside an assertion that mean "this expects a refusal"
_REFUSAL_WORDS = re.compile(
    r"permission denied|forbidden|unauthori[sz]ed|unauthenticated|\bdenied\b|not allowed|\bcannot\b|\bmust not\b|"
    r"\bnot (?:be )?(?:able|permitted|visible)|refus\w*|reject\w*|blocked|\b40[13]\b|\b429\b|42501|P0001|"
    r"insufficient_privilege|row[ -]level security|append-only|violates", re.I)

JS_TEST = re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs)$")
PY_TEST = re.compile(r"(^test_.*|.*_test)\.py$")


def is_guard_test_file(rel: str) -> bool:
    """Files this rule reads: JS/TS *.test.* / *.spec.*, Python test_*.py / *_test.py, SQL under a tests dir."""
    name = rel.rsplit("/", 1)[-1]
    if name.endswith(JS_SUFFIXES):
        return bool(JS_TEST.search(name))
    if name.endswith(".py"):
        return bool(PY_TEST.match(name))
    if name.endswith(".sql"):
        parts = rel.lower().split("/")
        return any(p in ("test", "tests", "__tests__") for p in parts[:-1]) or "test" in name.lower()
    return False


def _is_guard(text: str) -> bool:
    text = _ASSERT_NOISE.sub(" ", text)
    return bool(GUARD.search(text) or (WEAK_GUARD.search(text) and CALLER.search(text)))


def _verdict(kinds: list[str], text: str, what: str = "assertion(s)") -> str | None:
    """Flag only when there is at least one assertion, every one is a refusal, and the unit is about a guard."""
    if kinds and all(k == "refuse" for k in kinds) and _is_guard(text):
        return f"{len(kinds)} {what}, every one expects a refusal"
    return None


# ---------------------------------------------------------------------------------------------------- JS / TS

_EXPECT = re.compile(r"(?<![\w$.])expect\s*\(")
_JS_UNIT = re.compile(r"(?<![\w$.])describe(?:\.\w+)?\s*\(")
_JS_CASE = re.compile(r"(?<![\w$.])(?:it|test)(?:\.\w+)?\s*\(")


def _js_kinds(src: str) -> list[str]:
    """Classify each expect(...) chain in src as 'refuse' | 'succeed' | 'other'."""
    kinds = []
    for m in _EXPECT.finditer(src):
        close = balanced_span(src, m.end() - 1)
        subject = src[m.end():close - 1]
        chain = src[close:]
        mm = re.match(r"\s*((?:\.\w+)+)\s*\(", chain)
        chain = chain[:balanced_span(chain, mm.end() - 1)] if mm else ""
        kinds.append(_js_kind(subject, chain))
    return kinds


def _js_kind(subject: str, chain: str) -> str:
    c = re.sub(r"\s+", "", chain)
    neg = ".not." in c
    if ".resolves." in c or (neg and ".toThrow" in c):
        return "succeed"
    if ".rejects." in c or ".toThrow" in c or re.search(r"(?<![\w$])(?:\w+[!?]?\.)?fails?\(", subject):
        return "refuse"
    m = re.search(r"\.(?:toBe|toEqual|toStrictEqual)\((\d{3})\)", c) or \
        re.search(r"toHaveProperty\(['\"]status(?:Code)?['\"],(\d{3})\)", c)
    if m:
        status = m.group(1)
        if neg:
            return "other"
        return "succeed" if status[0] == "2" else "refuse" if status[0] == "4" else "other"
    if re.search(r"\berr(?:or)?s?\b", subject, re.I):
        if not neg and re.search(r"\.(toBeNull|toBeUndefined|toBeFalsy)\(", c):
            return "succeed"
        if re.search(r"\.(toBeTruthy|toBeDefined|toBeInstanceOf|toMatch|toContain)\(", c) or \
                (neg and re.search(r"\.(toBeNull|toBeUndefined)\(", c)):
            return "refuse"
    if re.search(r"\.ok\s*$", subject) and (b := re.search(r"\.toBe\((true|false)\)", c)):
        return "succeed" if (b.group(1) == "true") != neg else "refuse"
    if re.search(r"ok:\s*true", chain):
        return "succeed"
    if re.search(r"ok:\s*false", chain):
        return "refuse"
    if not neg and re.search(r"\.(toHaveLength|toBe)\(0\)|\.(toEqual|toStrictEqual)\(\[\]\)|\.toBeNull\(\)", c):
        return "refuse"  # zero rows / nothing returned: the RLS form of "refused"
    if not neg and _REFUSAL_WORDS.search(chain):
        return "refuse"
    return "other"


def _check_js(src: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    spans = [(m.start(), balanced_span(src, m.end() - 1)) for m in _JS_UNIT.finditer(src)]
    flagged: list[tuple[int, int]] = []
    for s, e in spans:  # outermost first; a refusal-only parent already covers its children
        if any(fs <= s < fe for fs, fe in flagged):
            continue
        why = _verdict(_js_kinds(src[s:e]), src[s:e])
        if why:
            flagged.append((s, e))
            out.append((s, why))
    # tests outside any describe() are judged together as the file-level unit
    rest = list(src)
    for s, e in spans:
        for k in range(s, e):
            if rest[k] != "\n":
                rest[k] = " "
    rest_src = "".join(rest)
    case = _JS_CASE.search(rest_src)
    if case:
        why = _verdict(_js_kinds(rest_src), rest_src, "top-level assertion(s)")
        if why:
            out.append((case.start(), why))
    return out


# ---------------------------------------------------------------------------------------------------- Python

def _py_kind(node: ast.AST, seg: str) -> str | None:
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call):
                fn = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                if fn in ("raises", "assertRaises", "assertRaisesRegex"):
                    return "refuse"
        return None
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        fn = node.value.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name in ("assertRaises", "assertRaisesRegex"):
            return "refuse"
        if name.startswith("assert") and not name.startswith("assert_"):  # unittest asserts, not mock asserts
            return _py_text_kind(seg)
        return None
    if isinstance(node, ast.Assert):
        return _py_text_kind(seg)
    return None


def _py_text_kind(seg: str) -> str:
    s = re.sub(r"\s+", " ", seg)
    m = re.search(r"\b([2-5]\d\d)\b", s)
    if m and re.search(r"status|code|==|\bin\b|assertEqual", s):
        neg = "!=" in s or " not in " in s
        if m.group(1)[0] == "2":
            return "refuse" if neg else "succeed"
        if m.group(1)[0] == "4":
            return "succeed" if neg else "refuse"
    if re.search(r"\.ok\b|\bis_success\b", s):
        return "refuse" if re.search(r"\bnot\b|is False|== False", s) else "succeed"
    if re.search(r"\b(?:err(?:or)?|exc)\w*\s+is\s+not\s+None\b", s):
        return "refuse"
    if re.search(r"\b(?:err(?:or)?|exc)\w*\s+is\s+None\b", s):
        return "succeed"
    if re.search(r"==\s*0\b|==\s*\[\]|\bis None\b|assertEqual\([^,]+,\s*0\)", s):
        return "refuse"
    if _REFUSAL_WORDS.search(s):
        return "refuse"
    return "other"


def _check_py(raw: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return []
    lines = raw.splitlines()

    def seg(n: ast.AST) -> str:
        return "\n".join(lines[n.lineno - 1:(getattr(n, "end_lineno", None) or n.lineno)])

    def judge(nodes: list[ast.AST]) -> str | None:
        kinds = [k for top in nodes for n in ast.walk(top) if hasattr(n, "lineno") and (k := _py_kind(n, seg(n)))]
        return _verdict(kinds, "\n".join(seg(n) for n in nodes))

    out = []
    loose = []
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name.startswith("Test"):
            why = judge([n])
            if why:
                out.append((n.lineno, why))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            loose.append(n)
    if loose:
        why = judge(loose)
        if why:
            out.append((loose[0].lineno, why))
    return out


# ---------------------------------------------------------------------------------------------------- SQL

def _sql_strip(src: str) -> str:
    """Blank out -- and /* */ comments, keeping offsets."""
    src = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), src, flags=re.S)
    return re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), src)


_PGTAP = re.compile(r"(?<![\w.])(throws_ok|throws_like|throws_ilike|throws_matching|throws_imatching|is_empty|"
                    r"lives_ok|lives_and|isnt_empty|results_eq|set_eq|bag_eq|row_eq|ok|is|isnt|cmp_ok)\s*\(", re.I)
_PGTAP_SUCCEED = {"lives_ok", "lives_and", "isnt_empty", "results_eq", "set_eq", "bag_eq", "row_eq"}
_DO_BLOCK = re.compile(r"\bdo\s+(\$\w*\$)(.*?)\1", re.I | re.S)
_SQL_ASSERT = re.compile(r"\bassert\b(.*?);", re.I | re.S)


def _check_sql(raw: str) -> list[tuple[int, str]]:
    src = _sql_strip(raw)
    found: list[tuple[int, str]] = []  # (offset, kind)
    for m in _PGTAP.finditer(src):
        fn = m.group(1).lower()
        args = src[m.end() - 1:balanced_span(src, m.end() - 1)]
        if fn.startswith("throws_") or fn == "is_empty":
            found.append((m.start(), "refuse"))
        elif fn in _PGTAP_SUCCEED:
            found.append((m.start(), "succeed"))
        elif fn == "is" and re.match(r"\(.*?,\s*'?0'?(?:::\w+)?\s*[,)]", args, re.S):
            found.append((m.start(), "refuse"))
        else:
            found.append((m.start(), "refuse" if _REFUSAL_WORDS.search(args) else "other"))
    for m in _DO_BLOCK.finditer(src):
        body = m.group(2)
        handler = re.search(r"\bexception\s+when\b", body, re.I)
        if handler and re.search(r"\braise\s+exception\b", body[:handler.start()], re.I):
            # the expect-fail idiom: run the statement, raise if it got through, swallow the expected error
            found.append((m.start(), "refuse"))
        for a in _SQL_ASSERT.finditer(body):
            expr = a.group(1)
            kind = "refuse" if re.search(r"=\s*0\b", expr) or _REFUSAL_WORDS.search(expr) else "succeed"
            found.append((m.start(2) + a.start(), kind))
    found.sort()
    why = _verdict([k for _, k in found], src, "check(s)")
    return [(found[0][0], why)] if why else []


# ---------------------------------------------------------------------------------------------------- entry

def _marker(line: str) -> bool:
    m = SUPPRESS.search(line)
    return bool(m and (not m.group("rules") or TS001.id in m.group("rules")))


def _header_suppressed(lines: list[str]) -> bool:
    """A `hivescout-ignore: TS001` anywhere in the file's leading comment block covers the whole file."""
    for ln in lines:
        if not ln.strip():
            continue
        if not re.match(r"^\s*(//|#|--|/\*|\*)", ln):
            return False
        if _marker(ln):
            return True
    return False


def _sql_suppressed(lines: list[str], line: int) -> bool:
    """SQL comments are `--`, which common.suppressed() does not know; walk the comment block above by hand."""
    ln = line
    while 1 <= ln <= len(lines) and (ln == line or lines[ln - 1].lstrip().startswith("--")):
        if _marker(lines[ln - 1]):
            return True
        ln -= 1
    return False


def check_file(path: Path, root: Path, target: str) -> list[Finding]:
    rel = path.relative_to(root).as_posix()
    if not is_guard_test_file(rel):
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = raw.splitlines()
    if _header_suppressed(lines):
        return []
    if path.suffix == ".py":
        hits = _check_py(raw)
    elif path.suffix == ".sql":
        hits = [(line_of(raw, off), why) for off, why in _check_sql(raw)]
    else:
        src = strip_comments(raw)
        hits = [(line_of(src, off), why) for off, why in _check_js(src)]
    out = []
    for ln, why in hits:
        if suppressed(lines, ln, TS001.id) or _sql_suppressed(lines, ln):
            continue
        evidence = f"{why}; no assertion shows an allowed caller succeeding: {lines[ln - 1].strip()[:160]}"
        out.append(make(TS001, target=target, file=path, root=root, line=ln, evidence=evidence))
    return out
