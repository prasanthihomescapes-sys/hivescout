"""Python API rules (FastAPI / Flask / Django): what the API consumer, and their user, sees on failure."""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

from ..findings import Finding
from .common import Rule, is_test_path, make, suppressed

PY001 = Rule("PY001", "high", "Exception swallowed with `pass`: the caller gets a success-shaped answer for a failure",
             "Log it and either re-raise, return an explicit error response, or record a degraded state the UI shows.",
             "Silent excepts turn failures into wrong data that reaches users.")
PY002 = Rule("PY002", "high", "Error handler references an undefined name: the graceful path itself crashes",
             "Define/import the name (usually the module logger) and add a test that drives this except branch.",
             "A NameError inside `except` turns a handled failure into a bare 500.")
PY003 = Rule("PY003", "medium", "On failure the code returns a hardcoded number, which users read as real data",
             "Return an explicit error/unknown state instead of a plausible-looking constant.",
             "A fallback like `return 100.0` silently grants budgets or shows invented totals.")
PY004 = Rule("PY004", "high", "Stack trace or raw exception text sent to the client",
             "Return a stable error code + plain-language message; keep the trace in server logs.",
             "Traces leak internals and give the end user nothing actionable.")
PY005 = Rule("PY005", "medium", "Bare `except:` also catches cancellation and shutdown signals",
             "Catch specific exceptions (or `Exception`) and handle them explicitly.")

_BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__package__", "__builtins__"}


def _defined_names(tree: ast.AST) -> set[str]:
    """Every name bound anywhere in the module: deliberately broad, so only truly undefined names are flagged."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == "*":
                    names.add("*")
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
    return names


def _is_quiet(body: list[ast.stmt]) -> bool:
    return all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)) or
               isinstance(s, ast.Continue) for s in body)


_BROAD = {"Exception", "BaseException"}


def _is_broad(t: ast.expr | None) -> bool:
    """True for bare except, `except Exception` / `BaseException`, or a tuple containing one of them."""
    if t is None:
        return True
    names = t.elts if isinstance(t, ast.Tuple) else [t]
    return any((n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "")) in _BROAD for n in names)


def _mentions_trace(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in ("format_exc", "format_exception", "print_exc"):
            return True
    return False


def _response_calls(node: ast.AST):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            fn = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if fn in ("HTTPException", "JSONResponse", "Response", "PlainTextResponse", "jsonify", "make_response",
                      "HttpResponse", "JsonResponse"):
                yield n


def check_file(path: Path, root: Path, target: str) -> list[Finding]:
    rel = path.relative_to(root).as_posix()
    if is_test_path(rel) or rel.startswith(("migrations/", "alembic/")) or "/migrations/" in rel:
        return []
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return []
    lines = src.splitlines()
    defined = _defined_names(tree)
    out: list[Finding] = []

    def add(rule: Rule, node: ast.AST, evidence: str = "") -> None:
        ln = getattr(node, "lineno", None)
        if ln and suppressed(lines, ln, rule.id):
            return
        ev = evidence or (lines[ln - 1].strip() if ln and ln <= len(lines) else "")
        out.append(make(rule, target=target, file=path, root=root, line=ln, evidence=ev))

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            add(PY005, node)
        if _is_quiet(node.body):
            if _is_broad(node.type):
                add(PY001, node)
            elif not suppressed(lines, node.lineno, "PY001"):
                f = make(PY001, target=target, file=path, root=root, line=node.lineno,
                         evidence=lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "")
                f.severity = "low"
                f.title = "A specific exception is ignored with `pass`: confirm no user-visible result depends on it"
                out.append(f)
            continue
        if "*" not in defined:
            for n in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined and n.id not in _BUILTINS:
                    add(PY002, n, f"`{n.id}` is not defined anywhere in {path.name}")
                    break
        for s in node.body:
            if isinstance(s, ast.Return) and isinstance(s.value, ast.Constant) and \
                    isinstance(s.value.value, (int, float)) and not isinstance(s.value.value, bool) and s.value.value not in (0, -1):
                add(PY003, s)

    for call in _response_calls(tree):
        if _mentions_trace(call):
            add(PY004, call)
            continue
        for kw in call.keywords:
            if kw.arg in ("detail", "content", "body") and isinstance(kw.value, ast.Call) and \
                    getattr(kw.value.func, "id", "") in ("str", "repr") and kw.value.args and \
                    isinstance(kw.value.args[0], ast.Name) and kw.value.args[0].id in ("e", "exc", "err", "error", "ex"):
                f = make(PY004, target=target, file=path, root=root, line=call.lineno,
                         evidence=lines[call.lineno - 1].strip() if call.lineno <= len(lines) else "")
                f.severity, f.title = "medium", "Raw exception text sent to the client as the error message"
                if not suppressed(lines, call.lineno, "PY004"):
                    out.append(f)
    return out
