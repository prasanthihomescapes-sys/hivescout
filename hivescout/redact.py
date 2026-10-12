"""Mask credentials before findings leave the machine (reports, KB entries, LLM prompts).

Rule: keep the first 4 characters, then "...[REDACTED]". Placeholders (example, xxx, <...>, ${...}) are left alone.
"""
from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"(?i)(your[-_ ]?|example|xxx|placeholder|changeme|change-me|dummy|<[^>]+>|\$\{|\$\(|redacted|\.\.\.)")
_PATTERNS = [re.compile(p) for p in [
    r"(?i)\b(api[_-]?key|api[_-]?token|auth[_-]?token|access[_-]?token|refresh[_-]?token|secret[_-]?key|client[_-]?secret|"
    r"service[_-]?role[_-]?key|private[_-]?key|password|passwd|secret)\b\s*[:=]\s*['\"]?(?P<secret>[A-Za-z0-9_\-./+=:@]{8,})",
    r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{16,}",
    r"\bAIza[0-9A-Za-z_\-]{20,}",
    r"\bGOCSPX-[0-9A-Za-z_\-]{20,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}",
    r"\bxox[bap]-[A-Za-z0-9\-]{10,}",
    r"\b(?:AC|SK)[0-9a-f]{32}\b",
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
    r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s'\"/:@]+:(?P<secret>[^\s'\"@]+)@",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9._\-]{24,})",
]]


def redact(text: str) -> str:
    if not text:
        return text
    for rx in _PATTERNS:
        def repl(m: re.Match) -> str:
            whole = m.group(0)
            secret = m.groupdict().get("secret")
            value = secret if secret is not None else whole
            if _PLACEHOLDER.search(value):
                return whole
            masked = f"{value[:4]}...[REDACTED]"
            if secret is None:
                return masked
            s, e = m.start("secret") - m.start(0), m.end("secret") - m.start(0)
            return whole[:s] + masked + whole[e:]
        text = rx.sub(repl, text)
    return text
