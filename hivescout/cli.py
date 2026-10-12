"""hivescout CLI.

    hivescout lint [PATH] | --target NAME | --all
    hivescout contract init|check [PATH] [--contracts DIR] | --target NAME
    hivescout contract draft CONTRACT.yml [--spec SPEC.md]
    hivescout probe --target NAME | --base-url URL --route /a --route /b
    hivescout report FINDINGS.json
    hivescout export-test FINDINGS.json --out e2e/hivescout.spec.ts
    hivescout kb-sync FINDINGS.json --kb path/to/kb.py
    hivescout auth --target NAME [--manual|--scripted|--check]
    hivescout targets
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from . import contract as contract_mod
from .findings import Finding, at_or_above, dump, load
from .lint import lint as run_lint
from .report import render
from .workspace import Target, load_targets, pick, workspace_dir


def _now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _out_dir(args, target: str, channel: str) -> Path:
    if args.out:
        return Path(args.out)
    ws = workspace_dir(args.workspace)
    base = ws / "runs" if ws else Path("hivescout-out")
    return base / target / f"{_now()}-{channel}"


def _publish(findings: list[Finding], meta: dict, out: Path, args) -> Path:
    meta = {"generated": datetime.now().isoformat(timespec="seconds"), "version": __version__, **meta}
    dump(findings, out / "findings.json", meta)
    report = out / "report.md"
    render(findings, meta, report)
    ws = workspace_dir(args.workspace)
    if ws and not args.out:  # keep a stable "latest" copy per target and channel
        latest = ws / "reports" / f"{meta['target']}-{'-'.join(meta.get('channels', []))}.md"
        render(findings, meta, latest)  # re-rendered so screenshot links resolve from reports/
    return report


def _summary(findings: list[Finding]) -> str:
    from collections import Counter
    c = Counter(f.severity for f in findings)
    return f"high={c['high']} medium={c['medium']} low={c['low']}"


def _exit(findings: list[Finding], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    return 1 if at_or_above(findings, fail_on) else 0


def _targets(args) -> list[Target]:
    ws = workspace_dir(args.workspace)
    if not ws:
        raise SystemExit("--target/--all need a workspace: pass --workspace DIR or set HIVESCOUT_WORKSPACE")
    return load_targets(ws)


# ----------------------------------------------------------------------------- commands

def cmd_lint(args) -> int:
    jobs: list[tuple[str, Path, str | None]] = []
    if args.all:
        jobs = [(t.name, t.path, t.stack) for t in _targets(args)]
    elif args.target:
        t = pick(_targets(args), args.target)
        jobs = [(t.name, t.path, t.stack)]
    else:
        p = Path(args.path or ".").resolve()
        jobs = [(p.name, p, None)]
    worst = 0
    for name, path, stack in jobs:
        if not path.exists():
            print(f"{name}: path not found ({path}), skipped", file=sys.stderr)
            continue
        findings, apps = run_lint(path, name, stack)
        out = _out_dir(args, name, "lint")
        report = _publish(findings, {"target": name, "channels": ["lint"], "apps": apps}, out, args)
        print(f"{name}: {_summary(findings)} apps={[a for _, a in apps] or 'none detected'} -> {report}")
        if args.json:
            print(json.dumps([f.__dict__ for f in findings], indent=2))
        worst = max(worst, _exit(findings, args.fail_on))
    return worst


def cmd_contract(args) -> int:
    if args.action == "draft":
        from .contract.draft import draft
        out = draft(Path(args.path), Path(args.spec) if args.spec else None, model=args.model)
        print(f"draft written: {out}")
        return 0
    if args.target:
        t = pick(_targets(args), args.target)
        name, app, folder = t.name, t.path, t.contracts
    else:
        app = Path(args.path or ".").resolve()
        name = app.name
        folder = Path(args.contracts) if args.contracts else app / "hivescout" / "contracts"
    if args.action == "init":
        written = contract_mod.init(app, folder, name, update=args.update)
        print("\n".join(f"wrote {p}" for p in written) or f"nothing to write (contracts exist in {folder}; use --update)")
        return 0
    findings = contract_mod.check(folder, name, app)
    out = _out_dir(args, name, "contract")
    report = _publish(findings, {"target": name, "channels": ["contract"]}, out, args)
    print(f"{name}: {_summary(findings)} -> {report}")
    return _exit(findings, args.fail_on)


def _load_journeys(args, contracts_dir: Path | None):
    import yaml

    from .probe.journey import Journey, load_from_contracts
    js = []
    if contracts_dir and contracts_dir.exists():
        js += load_from_contracts(contract_mod.load_contracts(contracts_dir))
    for f in args.journeys or []:
        doc = yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}
        items = doc.get("journeys", doc) if isinstance(doc, dict) else doc
        js += [Journey.from_dict(d) for d in items]
    if args.journey:
        wanted = set(args.journey)
        missing = wanted - {j.id for j in js}
        if missing:
            raise SystemExit(f"unknown journey(s): {', '.join(sorted(missing))}; known: {', '.join(j.id for j in js) or 'none'}")
        js = [j for j in js if j.id in wanted]
    return js


def cmd_probe(args) -> int:
    from .probe.web import ProbeConfig, run
    storage, login_path, contracts_dir = args.storage_state, None, None
    if args.target:
        t = pick(_targets(args), args.target)
        name, base, routes, pattern = t.name, args.base_url or t.base_url, args.route or t.routes, args.api_pattern or t.api_pattern
        contracts_dir = t.contracts
        if not routes and t.contracts and t.contracts.exists():
            routes = [fl["route"] for _, doc in contract_mod.load_contracts(t.contracts)
                      if doc.get("surface") in ("web", "mobile") for fl in doc.get("flows", [])
                      if fl.get("route") and "[" not in fl["route"]]
        if t.auth is not None:
            login_path = t.auth.login_path
            if not storage and t.auth.state and t.auth.state.exists():
                storage = str(t.auth.state)
            elif not storage:
                print(f"note: {name} has signed-in areas but no saved session; run `hivescout auth --target {name}`. "
                      "Signed-in routes will be reported as not tested.", file=sys.stderr)
    else:
        name, base, routes, pattern = args.name or "app", args.base_url, args.route, args.api_pattern or "**/api/**"
        login_path = args.login_path
    try:
        journeys = _load_journeys(args, contracts_dir)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    if args.no_routes:
        routes = []
    if not base:
        raise SystemExit("no base URL: pass --base-url or set base_url in targets.json (start the app first)")
    if not routes and not journeys:
        raise SystemExit("nothing to probe: pass --route or --journeys, list routes in targets.json, or write a web contract")
    routes = routes or []
    bad = [r for r in routes if not r.startswith("/")]
    if bad:
        hint = (" Git Bash rewrote it into a Windows path; rerun with MSYS_NO_PATHCONV=1 or use //route."
                if any(re.match(r"^[A-Za-z]:[\\/]", r) for r in bad) else "")
        raise SystemExit(f"routes must be URL paths starting with '/': {bad}.{hint}")
    routes = ["/" + r.lstrip("/") for r in routes]  # '//login' (an MSYS escape) -> '/login'
    out = _out_dir(args, name, "web")
    from .probe.faults import FAULTS
    faults = args.faults.split(",") if args.faults else None
    unknown = [f for f in faults or [] if f not in FAULTS or f == "baseline"]
    if unknown:
        raise SystemExit(f"unknown fault(s) {unknown}; choose from {', '.join(k for k in FAULTS if k != 'baseline')}")
    cfg = ProbeConfig(base_url=base, routes=routes, out_dir=out, target=name, api_pattern=pattern, faults=faults,
                      personas=args.personas.split(",") if args.personas else None,
                      headless=not args.headed, storage_state=storage, login_path=login_path,
                      journeys=journeys, allow_mutations=args.allow_mutations, cors_relay=args.cors_relay)
    findings, meta = run(cfg)
    meta["journey_defs"] = {j.id: {"start": j.start, "steps": j.steps, "act": j.act} for j in journeys}
    report = _publish(findings, {"target": name, "channels": ["web"], **meta}, out, args)
    not_ex = sum(1 for c in meta["coverage"].values() for v in c["faults"].values() if not v.startswith("exercised"))
    print(f"{name}: {_summary(findings)} routes={len(routes)} journeys={len(journeys)} "
          f"signed-in={'yes' if storage else 'no'} faults-not-exercised={not_ex} -> {report}")
    return _exit(findings, args.fail_on)


def cmd_auth(args) -> int:
    from . import auth as auth_mod
    t = pick(_targets(args), args.target)
    if t.auth is None:
        raise SystemExit(f'{t.name} has no "auth" block in targets.json; add e.g. '
                         '{"auth": {"login_path": "/login", "check_path": "/dashboard"}}')
    base = args.base_url or t.base_url
    if not base:
        raise SystemExit("no base URL: pass --base-url or set base_url in targets.json (start the app first)")
    state = t.auth.state
    if args.check:
        ok, landed = auth_mod.check(base, t.auth, state)
        print(f"{t.name}: {'signed in' if ok else 'NOT signed in'} ({landed})"
              + (f"; {auth_mod.describe_state(state)}" if state.exists() else ""))
        return 0 if ok else 1
    manual = True if args.manual else (False if args.scripted else None)
    out = auth_mod.capture(base, t.auth, state, manual=manual, timeout_s=args.timeout)
    ok, landed = auth_mod.check(base, t.auth, out)
    print(f"saved {out} ({auth_mod.describe_state(out)}). Keep it out of git: it holds live session cookies.")
    print(f"check: {'signed in' if ok else 'NOT signed in'} at {landed}")
    return 0 if ok else 1


def cmd_report(args) -> int:
    meta, findings = load(Path(args.findings))
    out = Path(args.out) if args.out else Path(args.findings).with_name("report.md")
    render(findings, meta, out)
    print(out)
    return 0


def cmd_export(args) -> int:
    from .export import write
    meta, findings = load(Path(args.findings))
    n = write(findings, Path(args.out), args.api_pattern or meta.get("api_pattern", "**/api/**"),
              meta.get("journey_defs") or {})
    print(f"{n} test(s) -> {args.out}")
    return 0


def cmd_kbsync(args) -> int:
    from .kbsync import kb_command, push
    _, findings = load(Path(args.findings))
    for line in push(findings, kb_command(args.kb), min_severity=args.min, source=str(Path(args.findings).resolve()),
                     dry_run=args.dry_run):
        print(line)
    return 0


def cmd_targets(args) -> int:
    for t in _targets(args):
        ok = "ok " if t.path.exists() else "MISSING"
        print(f"{ok} {t.name:24} {t.path}  base_url={t.base_url or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hivescout", description="User-side UX and resilience testing for AI-built software.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--workspace", help="private workspace folder with targets.json (or HIVESCOUT_WORKSPACE)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(s, fail_default="high"):
        s.add_argument("--out", help="output folder (default: <workspace>/runs/<target>/<time>-<channel>)")
        s.add_argument("--fail-on", choices=["high", "medium", "low", "never"], default=fail_default)

    s = sub.add_parser("lint", help="static checks for missing error/empty/loading/offline handling")
    s.add_argument("path", nargs="?")
    s.add_argument("--target")
    s.add_argument("--all", action="store_true")
    s.add_argument("--json", action="store_true")
    common(s)
    s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("contract", help="design-time UX contracts: init, check (gate), draft (Claude)")
    s.add_argument("action", choices=["init", "check", "draft"])
    s.add_argument("path", nargs="?", help="app root (init/check) or contract file (draft)")
    s.add_argument("--target")
    s.add_argument("--contracts", help="contracts folder (default: <app>/hivescout/contracts)")
    s.add_argument("--update", action="store_true", help="init: append flows for new routes")
    s.add_argument("--spec", help="draft: feature spec to draft from")
    s.add_argument("--model", default="sonnet")
    common(s)
    s.set_defaults(fn=cmd_contract)

    s = sub.add_parser("probe", help="drive the running app as a user under injected faults")
    s.add_argument("--target")
    s.add_argument("--name")
    s.add_argument("--base-url")
    s.add_argument("--route", action="append")
    s.add_argument("--api-pattern")
    s.add_argument("--faults", help="comma list: http500,http401,http404,timeout,slow,malformed,empty,offline")
    s.add_argument("--personas", help="comma list: desktop,mobile")
    s.add_argument("--storage-state", help="Playwright storage state JSON for signed-in routes (default: the target's saved session)")
    s.add_argument("--login-path", help="where an expired session lands (with --base-url; targets set auth.login_path)")
    s.add_argument("--journeys", action="append", help="YAML file of journeys (also read from the target's contracts)")
    s.add_argument("--journey", action="append", help="only run this journey id (repeatable)")
    s.add_argument("--no-routes", action="store_true", help="run journeys only")
    s.add_argument("--allow-mutations", action="store_true",
                   help="also run journeys marked mutates:true without a fault (creates real data)")
    s.add_argument("--cors-relay", action="store_true",
                   help="relay API reads through the probe with an allow-origin header (local build vs prod API)")
    s.add_argument("--headed", action="store_true")
    common(s)
    s.set_defaults(fn=cmd_probe)

    s = sub.add_parser("auth", help="capture or check a signed-in session for a target")
    s.add_argument("--target", required=True)
    s.add_argument("--base-url")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--manual", action="store_true", help="open a browser and let a person sign in (default without auth.steps)")
    g.add_argument("--scripted", action="store_true", help="run auth.steps headless with ${env:...} values")
    s.add_argument("--check", action="store_true", help="only check the saved session still works")
    s.add_argument("--timeout", type=int, default=300, help="seconds to wait for sign-in")
    s.set_defaults(fn=cmd_auth)

    s = sub.add_parser("report", help="re-render report.md from findings.json")
    s.add_argument("findings")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("export-test", help="write a Playwright regression spec from probe findings")
    s.add_argument("findings")
    s.add_argument("--out", required=True)
    s.add_argument("--api-pattern")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("kb-sync", help="record high-impact findings in a lessons KB (kb.py add ...)")
    s.add_argument("findings")
    s.add_argument("--kb", required=True, help="path to kb.py or a command accepting `add --type --title ...`")
    s.add_argument("--min", choices=["high", "medium", "low"], default="high")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_kbsync)

    s = sub.add_parser("targets", help="list workspace targets")
    s.set_defaults(fn=cmd_targets)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
