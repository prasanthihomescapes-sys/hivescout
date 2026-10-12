from pathlib import Path

from hivescout.lint import lint
from hivescout.lint import js, python_api

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"


def rules(findings):
    return sorted(f.rule for f in findings)


def test_broken_next_flags_every_gap():
    fs, apps = lint(EX / "broken-next")
    assert apps == [("nextjs", ".")]
    r = rules(fs)
    for rule in ("NX001", "NX002", "NX003", "JS001", "JS002", "JS003", "JS004", "JS005", "JS006"):
        assert rule in r, f"{rule} missing from {r}"
    nx1 = next(f for f in fs if f.rule == "NX001")
    assert "3 of 3 routes" in nx1.title and "/dashboard" in nx1.evidence


def test_broken_api_flags_every_gap():
    fs, apps = lint(EX / "broken-api")
    assert apps == [("python", ".")]
    by = {(f.rule, f.line) for f in fs}
    assert ("PY003", 12) in by          # return 100.0 fallback
    assert ("PY002", 24) in by          # undefined logger in except
    assert ("PY001", 32) in by          # except: pass
    assert any(f.rule == "PY004" and f.severity == "high" for f in fs)  # traceback to client
    assert any(f.rule == "PY004" and f.severity == "medium" for f in fs)  # str(e) to client


def _js(tmp_path, code, name="a.tsx"):
    f = tmp_path / name
    f.write_text(code, encoding="utf-8")
    return js.check_file(f, tmp_path, "t")


def test_js_handled_catch_is_clean(tmp_path):
    code = """
    async function load() {
      try { const r = await fetch('/api/x', { signal: AbortSignal.timeout(3000) }); return r.json(); }
      catch (e) { setError('Could not load'); }
    }"""
    assert _js(tmp_path, code) == []


def test_js_commented_swallow_is_low(tmp_path):
    fs = _js(tmp_path, "try { x() } catch { /* server components cannot set cookies */ }")
    assert [(f.rule, f.severity) for f in fs] == [("JS002", "low")]


def test_js_suppression(tmp_path):
    fs = _js(tmp_path, "// hivescout-ignore: JS001\nfetch('/api/x')\n")
    assert fs == []


def test_js_multiline_console_only_catch(tmp_path):
    code = "try { a() } catch (err) {\n  console.error(\n    'failed', err\n  );\n  return null;\n}"
    assert rules(_js(tmp_path, code)) == ["JS005"]


def test_js_ignores_comments_and_tests(tmp_path):
    assert _js(tmp_path, "// fetch('/api/x') catch {}\n") == []
    (tmp_path / "__tests__").mkdir()
    assert _js(tmp_path, "fetch('/api/x')", "__tests__/a.test.ts") == []


def test_js_minified_skipped(tmp_path):
    assert _js(tmp_path, "fetch('/x');" * 400 + "catch{}", "vendor.js") == []


def _py(tmp_path, code):
    f = tmp_path / "m.py"
    f.write_text(code, encoding="utf-8")
    return python_api.check_file(f, tmp_path, "t")


def test_py_defined_logger_is_clean(tmp_path):
    code = "import logging\nlogger = logging.getLogger(__name__)\ntry:\n    x = 1\nexcept ValueError:\n    logger.warning('x')\n    raise\n"
    assert _py(tmp_path, code) == []


def test_py_star_import_disables_undefined_check(tmp_path):
    code = "from os import *\ntry:\n    x = 1\nexcept ValueError:\n    logger.warning('x')\n    raise\n"
    assert _py(tmp_path, code) == []


def test_ignore_rules_config(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "m.py").write_text("try:\n    x = 1\nexcept Exception:\n    pass\n")
    assert rules(lint(tmp_path)[0]) == ["PY001"]
    (tmp_path / ".hivescout.yml").write_text("ignore_rules: [PY001]\n")
    assert lint(tmp_path)[0] == []


def test_nextjs_segment_error_coverage(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"next": "15"}}')
    for d in ("app", "app/a", "app/b"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / d / "page.tsx").write_text("export default function P(){return null}")
    (tmp_path / "app/a/error.tsx").write_text("'use client'")
    fs, _ = lint(tmp_path)
    nx1 = sorted(f.route for f in fs if f.rule == "NX001")
    assert nx1 == ["/", "/b"]  # /a is covered by its own error.tsx


def test_py_specific_exception_pass_is_low(tmp_path):
    fs = _py(tmp_path, "import json\ntry:\n    json.loads('x')\nexcept json.JSONDecodeError:\n    pass\n")
    assert [(f.rule, f.severity) for f in fs] == [("PY001", "low")]
