"""Source rules shared by every JS/TS app (Next.js, React, Expo)."""
from __future__ import annotations

import re
from pathlib import Path

from ..findings import Finding
from .common import Rule, balanced_span, is_test_path, line_of, make, suppressed

JS001 = Rule("JS001", "medium", "Network call has no timeout: a slow API leaves the user staring at a spinner",
             "Pass `signal: AbortSignal.timeout(ms)` (or an AbortController) and render a timeout state with retry.",
             "Hung requests never reach the error UI, so users see an endless spinner.")
JS002 = Rule("JS002", "high", "Error swallowed silently: the user gets no message and no way to recover",
             "Surface the failure: set an error state the UI renders, with a retry/back action; log it too.",
             "Empty catch blocks turn outages into blank or stale screens.")
JS003 = Rule("JS003", "high", "On failure the code substitutes mock/sample data, so users act on fake numbers",
             "Never substitute fabricated data. Show an explicit error or a clearly labelled cached/stale banner.",
             "A failing API that falls back to mock data looks healthy to the user and to monitoring.")
JS004 = Rule("JS004", "medium", "Raw error text rendered to the user",
             "Map errors to plain-language copy with a next step; keep the raw message in logs.",
             "Messages like 'TypeError: cannot read properties of undefined' tell a user nothing.")
JS005 = Rule("JS005", "medium", "Error only logged to the console; nothing reaches the screen",
             "Set an error state the UI renders (message + retry) in addition to logging.",
             "console.error is invisible to the person using the app.")
JS006 = Rule("JS006", "medium", "Data hook used without handling its error state",
             "Read `isError`/`error` from the hook and render an error state with retry.",
             "When the query fails the component renders as if there were simply no data.")

JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs")
JSX_SUFFIXES = (".tsx", ".jsx")
MOCKY = re.compile(r"\b\w*(mock|fake|dummy|sample|demo|placeholder|fixture)\w*\b", re.I)


def strip_comments(src: str) -> str:
    """Blank out // and /* */ comments, keeping offsets and newlines; string literals are left intact."""
    out = list(src)
    i, n, quote = 0, len(src), None
    while i < n:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


_FETCH = re.compile(r"(?<![\w.$])(?:window\.|globalThis\.)?fetch\s*\(")
_CATCH_BLOCK = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{")
_CATCH_CB = re.compile(r"\.catch\s*\(")
_RAW_ERR = re.compile(r"\{\s*(?:\w*err\w*|e)\??\.(message|stack)\s*\}|\{\s*String\(\s*\w*err\w*\s*\)\s*\}|"
                      r"\{\s*JSON\.stringify\(\s*\w*err\w*", re.I)
_BODY_READ = re.compile(r"\.(json|text|blob|arrayBuffer|formData)\(\s*\)\s*$")
_DATA_HOOK = re.compile(r"\b(useQuery|useSWR|useInfiniteQuery|useSuspenseQuery)\s*\(")


def _body_kind(body: str) -> str | None:
    """Classify a catch body: 'empty', 'mock', 'console' or None (handled)."""
    inner = body.strip().rstrip(";").strip()
    if not inner or re.fullmatch(r"return(\s+(null|undefined|false|\[\]|\{\}))?", inner):
        return "empty"
    if MOCKY.search(inner) and re.search(r"\breturn\b|=|set\w+\(", inner):
        return "mock"
    # remove console.*(...) calls; if only quiet returns remain, the failure never reaches the screen
    rest, had_console = inner, False
    while (m := re.search(r"\bconsole\.\w+\s*\(", rest)):
        had_console = True
        rest = rest[:m.start()] + rest[balanced_span(rest, m.end() - 1):]
    leftovers = [s.strip() for s in re.split(r"[;\n]", rest) if s.strip()]
    quiet_return = re.compile(r"^return(\s+(null|undefined|false|\[\]|\{\}))?$")
    if had_console and all(quiet_return.match(s) for s in leftovers):
        return "console"
    return None


def check_file(path: Path, root: Path, target: str) -> list[Finding]:
    rel = path.relative_to(root).as_posix()
    if is_test_path(rel):
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if len(raw) > 2000 and raw.count(chr(10)) < len(raw) / 400:  # minified/generated: average line longer than 400 chars
        return []
    src = strip_comments(raw)
    lines = raw.splitlines()
    out: list[Finding] = []

    def add(rule: Rule, offset: int, evidence: str, end: int | None = None) -> None:
        ln = line_of(src, offset)
        if suppressed(lines, ln, rule.id):
            return
        f = make(rule, target=target, file=path, root=root, line=ln, evidence=evidence)
        if rule is JS002 and end is not None and re.search(r"//|/\*", raw[offset:end]):
            # a commented swallow is usually deliberate (e.g. cookie writes in server components); keep it visible
            f.severity, f.title = "low", "Error swallowed on purpose (commented): confirm the user never needs to know"
        out.append(f)

    # JS001 fetch without abort/timeout
    for m in _FETCH.finditer(src):
        end = balanced_span(src, m.end() - 1)
        call = src[m.start():end]
        if "signal" in call:
            continue
        args = call[call.find("(") + 1:-1]
        if "," in args and re.search(r",\s*[A-Za-z_$][\w$]*\s*\)?$", args.strip()) and "signal" in src:
            continue  # options passed by variable and the file manages signals somewhere; give benefit of doubt
        add(JS001, m.start(), call.splitlines()[0])

    # catch { ... }
    for m in _CATCH_BLOCK.finditer(src):
        end = balanced_span(src, m.end() - 1, "{", "}")
        body = src[m.end():end - 1]
        kind = _body_kind(body)
        snippet = src[m.start():min(end, m.start() + 160)]
        if kind == "empty":
            add(JS002, m.start(), snippet, end)
        elif kind == "mock":
            add(JS003, m.start(), snippet)
        elif kind == "console":
            add(JS005, m.start(), snippet)

    # .catch(cb)
    for m in _CATCH_CB.finditer(src):
        end = balanced_span(src, m.end() - 1)
        cb = src[m.end():end - 1].strip()
        arrow = re.match(r"^(?:async\s*)?(?:\(\s*\w*\s*\)|\w+)\s*=>\s*(?P<body>.*)$", cb, re.S)
        if not arrow:
            continue
        body = arrow.group("body").strip()
        if body.startswith("{") and body.endswith("}"):
            body = body[1:-1]
        elif body in ("null", "undefined", "[]", "{}", "''", '""', "false"):
            body = ""
        else:
            body = "return " + body
        kind = _body_kind(body)
        snippet = src[m.start():min(end, m.start() + 160)]
        if kind == "empty" and _BODY_READ.search(src[max(0, m.start() - 40):m.start()]):
            # `await res.text().catch(() => "")`: only the body *detail* is optional. If the code then throws
            # anyway, the failure still surfaces; flag nothing. Otherwise it's a minor, low finding.
            if re.search(r"\bthrow\b|Promise\.reject|\breject\(", src[end:end + 400]):
                continue
            f = make(JS002, target=target, file=path, root=root, line=line_of(src, m.start()), evidence=snippet)
            f.severity, f.title = "low", "Response body read failure ignored: check the error still reaches the user"
            if not suppressed(lines, f.line, "JS002"):
                out.append(f)
            continue
        if kind == "empty":
            add(JS002, m.start(), snippet, end)
        elif kind == "mock":
            add(JS003, m.start(), snippet)
        elif kind == "console":
            add(JS005, m.start(), snippet)

    # JS004 raw error in JSX
    if path.suffix in JSX_SUFFIXES:
        for m in _RAW_ERR.finditer(src):
            add(JS004, m.start(), m.group(0))

    # JS006 data hook without error handling
    hook = _DATA_HOOK.search(src)
    if hook and not re.search(r"\b(isError|error|status|errorComponent|throwOnError|useErrorBoundary)\b", src):
        add(JS006, hook.start(), src[hook.start():hook.start() + 80].splitlines()[0])
    return out
