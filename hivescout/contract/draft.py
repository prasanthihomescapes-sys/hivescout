"""Optional: ask Claude Code (`claude -p`, subscription auth, no API key) to fill a contract's TODOs from a spec.

The result is a *draft*: it is written next to the contract as `<name>.draft.yml` for a human to review.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

from . import SCHEMA

SYSTEM = (
    "You design the non-happy paths of software for end users who are not technical. For every flow and state, "
    "write what the user sees in plain words (`shows`), the recovery action, and short user-facing `copy`. Never "
    "propose silently substituting data: if data fails, the user is told. Keep `not_applicable` only when a state "
    "truly cannot occur, with the reason. Return the full contract."
)


def find_claude() -> list[str]:
    explicit = os.environ.get("HIVESCOUT_CLAUDE_BIN")
    if explicit:
        return [explicit]
    for name in ("claude", "claude.exe", "claude.cmd"):
        w = shutil.which(name)
        if w:
            return [w]
    raise SystemExit("claude CLI not found; install Claude Code or set HIVESCOUT_CLAUDE_BIN")


def draft(contract: Path, spec: Path | None, model: str = "sonnet", timeout: int = 600) -> Path:
    doc = yaml.safe_load(contract.read_text(encoding="utf-8"))
    prompt = "Fill every TODO in this UX contract.\n\nCONTRACT (YAML):\n" + yaml.safe_dump(doc, sort_keys=False)
    if spec:
        prompt += "\n\nFEATURE SPEC:\n" + spec.read_text(encoding="utf-8", errors="replace")[:30000]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_SESSION") and k != "CLAUDECODE"}
    args = find_claude() + ["-p", "--output-format", "json", "--model", model, "--allowedTools", "",
                            "--append-system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA)]
    r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env=env)
    if r.returncode != 0 and not r.stdout.strip():
        raise SystemExit(f"claude failed ({r.returncode}): {r.stderr[:500]}")
    res = json.loads(r.stdout)
    filled = res.get("structured_output")
    if not isinstance(filled, dict):
        raise SystemExit("claude returned no structured contract; is this a recent Claude Code with --json-schema?")
    out = contract.with_name(contract.stem + ".draft.yml")
    out.write_text("# DRAFT from hivescout contract draft: review, then replace the contract.\n"
                   + yaml.safe_dump(filled, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out
