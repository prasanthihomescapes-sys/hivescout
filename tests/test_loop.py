"""Loop closure: findings -> report, regression spec, KB entries; plus redaction."""
import sys
from pathlib import Path

from hivescout.cli import main
from hivescout.export import spec
from hivescout.findings import Finding, dump, load
from hivescout.kbsync import entries, push
from hivescout.redact import redact

ROOT = Path(__file__).resolve().parent.parent


def web_finding(rule="WEB004", fault="http500", route="/dashboard"):
    return Finding(rule=rule, severity="high", title="Data failed to load and the user is not told", channel="web",
                   target="shop", route=route, fault=fault, evidence="visible: Orders", fix="Render an error state.")


def test_export_spec_has_one_test_per_route_fault():
    fs = [web_finding(), web_finding("WEB006"), web_finding(fault="timeout"), web_finding(fault="empty", rule="WEB008")]
    text = spec(fs)
    assert text.count("  test(") == 3
    assert "route.fulfill({ status: 500" in text
    assert "new Promise(() => {})" in text      # hang
    assert "create your first" in text           # empty-state expectation
    assert 'page.goto("/dashboard")' in text


def test_kb_entries_group_by_target_and_rule():
    fs = [web_finding(), web_finding(route="/b"), web_finding("WEB001")]
    es = entries(fs)
    assert len(es) == 2
    assert all(e["project"] == "shop" and "ux-finding" in e["tags"] for e in es)
    assert "2 occurrence(s)" in next(e for e in es if "WEB004" in e["title"])["problem"]


def test_kb_push_calls_add(tmp_path):
    fake = tmp_path / "kb.py"
    log = tmp_path / "calls.txt"
    fake.write_text(f"import sys\nopen(r'{log}', 'a').write(' '.join(sys.argv[1:4]) + '\\n')\nprint('created x-1')\n")
    out = push([web_finding()], [sys.executable, str(fake)])
    assert out == ["created x-1"]
    assert log.read_text().startswith("add --type gotcha")


def test_redaction():
    assert "REDACTED" in redact("password=hunter2hunter2")
    assert "sk-a...[REDACTED]" in redact("key sk-ant-abcdefghijklmnopqrstu")
    assert redact("password=<your-password>") == "password=<your-password>"


def test_dump_redacts_evidence(tmp_path):
    f = web_finding()
    f.evidence = "token: api_key=abcd1234efgh5678"
    dump([f], tmp_path / "f.json")
    _, back = load(tmp_path / "f.json")
    assert "abcd1234efgh5678" not in back[0].evidence


def test_cli_lint_writes_report_and_fails_on_high(tmp_path):
    rc = main(["lint", str(ROOT / "examples" / "broken-next"), "--out", str(tmp_path)])
    assert rc == 1
    assert (tmp_path / "findings.json").exists()
    assert "NX001" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert main(["lint", str(ROOT / "examples" / "broken-next"), "--out", str(tmp_path), "--fail-on", "never"]) == 0
