"""Structural rules: does the app have the screens a user needs when things go wrong?"""
from __future__ import annotations

import re
from pathlib import Path

from ..findings import Finding
from ..workspace import walk
from .common import Rule, make

NX001 = Rule("NX001", "high", "Route has no error boundary (error.tsx): a crash shows a blank or default error screen",
             "Add `error.tsx` (client component with a reset() retry button) at the app root and in segments with "
             "their own data; add `global-error.tsx` for layout crashes.",
             "Without error.tsx a thrown error in a server component takes the whole page down.")
NX002 = Rule("NX002", "medium", "Route has no loading state (loading.tsx): users see a frozen page while data loads",
             "Add `loading.tsx` with a skeleton matching the page layout.",
             "Server-rendered data fetches block navigation with no feedback.")
NX003 = Rule("NX003", "low", "No custom not-found page: bad links land on a bare 404",
             "Add `app/not-found.tsx` with navigation back to a useful page.")
NX004 = Rule("NX004", "medium", "Pages router app has no custom error page (_error / 500)",
             "Add `pages/_error.tsx` or `pages/500.tsx` with plain-language copy and a way back.")
RX001 = Rule("RX001", "high", "React app has no error boundary: any render error blanks the whole screen",
             "Wrap the app (and risky widgets) in an ErrorBoundary (e.g. react-error-boundary) with a retry fallback.")
EX001 = Rule("EX001", "high", "Mobile app has no error boundary: a render error shows a red/blank screen",
             "Export `ErrorBoundary` from expo-router layouts or wrap the root in react-error-boundary.")
EX002 = Rule("EX002", "medium", "Mobile app never checks connectivity: offline users get spinners and silent failures",
             "Use @react-native-community/netinfo (or TanStack onlineManager) to show an offline banner and queue/retry.")

PAGE = re.compile(r"^page\.(tsx|jsx|ts|js|mdx)$")


def _has(dirp: Path, stem: str) -> bool:
    return any((dirp / f"{stem}.{ext}").exists() for ext in ("tsx", "jsx", "ts", "js"))


def _app_dirs(root: Path) -> list[Path]:
    return [d for d in (root / "app", root / "src" / "app") if d.is_dir()]


def _route_of(app: Path, page_dir: Path) -> str:
    parts = [p for p in page_dir.relative_to(app).parts if not (p.startswith("(") and p.endswith(")")) and not p.startswith("@")]
    return "/" + "/".join(parts)


def _covered(app: Path, page_dir: Path, stem: str) -> bool:
    d = page_dir
    while True:
        if _has(d, stem):
            return True
        if d == app:
            return False
        d = d.parent


def check_nextjs(root: Path, target: str) -> list[Finding]:
    out: list[Finding] = []
    for app in _app_dirs(root):
        pages = sorted({p.parent for p in walk(app, (".tsx", ".jsx", ".ts", ".js", ".mdx")) if PAGE.match(p.name)})
        if not pages:
            continue
        for stem, rule in (("error", NX001), ("loading", NX002)):
            missing = [d for d in pages if not _covered(app, d, stem)]
            if not missing:
                continue
            routes = [_route_of(app, d) for d in missing]
            if len(missing) == len(pages) and not (stem == "error" and _has(app, "global-error")):
                out.append(make(rule, target=target, file=None, root=root, line=None,
                                title=f"{rule.title} ({len(routes)} of {len(pages)} routes, none covered)",
                                evidence="routes: " + ", ".join(routes[:25]) + (" ..." if len(routes) > 25 else "")))
                out[-1].file = app.relative_to(root).as_posix() + "/"
            elif stem == "error" or len(missing) <= 10:
                for d, r in zip(missing, routes, strict=True):
                    f = make(rule, target=target, file=None, root=root, line=None, evidence=f"route {r}")
                    f.file, f.route = d.relative_to(root).as_posix() + "/", r
                    out.append(f)
        if not _has(app, "not-found"):
            f = make(NX003, target=target, file=None, root=root, line=None)
            f.file = app.relative_to(root).as_posix() + "/"
            out.append(f)
    for pages_dir in (root / "pages", root / "src" / "pages"):
        if pages_dir.is_dir() and not (_has(pages_dir, "_error") or _has(pages_dir, "500")):
            f = make(NX004, target=target, file=None, root=root, line=None)
            f.file = pages_dir.relative_to(root).as_posix() + "/"
            out.append(f)
    return out


def _grep_any(root: Path, pattern: re.Pattern) -> bool:
    for f in walk(root, (".ts", ".tsx", ".js", ".jsx")):
        try:
            if pattern.search(f.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


BOUNDARY = re.compile(r"ErrorBoundary|componentDidCatch|getDerivedStateFromError|react-error-boundary|errorElement")


def check_react(root: Path, target: str) -> list[Finding]:
    if _grep_any(root, BOUNDARY):
        return []
    return [make(RX001, target=target, file=None, root=root, line=None)]


def check_expo(root: Path, target: str) -> list[Finding]:
    out = []
    if not _grep_any(root, BOUNDARY):
        out.append(make(EX001, target=target, file=None, root=root, line=None))
    if not _grep_any(root, re.compile(r"netinfo|useNetInfo|NetInfo|onlineManager|isConnected", re.I)):
        out.append(make(EX002, target=target, file=None, root=root, line=None))
    return out
