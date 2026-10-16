from pathlib import Path

import pytest

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


def test_suppression_above_multiline_comment(tmp_path):
    # the reason may span several comment lines, and contain an em dash
    code = ("// hivescout-ignore: JS002 — not swallowed: the throw below is unconditional;\n"
            "// this only guards the best-effort read of the body.\n"
            "const body = await res.json().catch(() => null);\nconsole.log(body);\n")
    assert _js(tmp_path, code) == []


def test_suppression_does_not_leak_past_code(tmp_path):
    code = "// hivescout-ignore: JS002\nconst a = 1;\nx().catch(() => {});\n"
    assert rules(_js(tmp_path, code)) == ["JS002"]


def test_body_read_catch_followed_by_throw_is_clean(tmp_path):
    code = ('if (!res.ok) {\n  const body = await res.text().catch(() => "");\n'
            "  throw new Error(`dispatch failed ${res.status}: ${body}`);\n}\n")
    assert _js(tmp_path, code) == []


def test_body_read_catch_without_throw_is_low(tmp_path):
    fs = _js(tmp_path, "const data = await res.json().catch(() => null);\nrender(data);\n")
    assert [(f.rule, f.severity) for f in fs] == [("JS002", "low")]


def test_deno_and_static_detected_and_nothing_scanned_is_high(tmp_path):
    fn = tmp_path / "supabase" / "functions" / "hook"
    fn.mkdir(parents=True)
    (fn / "index.ts").write_text("try { a() } catch {}\n")
    (tmp_path / "console").mkdir()
    (tmp_path / "console" / "index.html").write_text("<script src=app.js></script>")
    (tmp_path / "console" / "app.js").write_text("fetch('/x').then(r => r.json()).catch(() => [])\n")
    fs, apps = lint(tmp_path)
    assert sorted(s for s, _ in apps) == ["deno", "static"]
    assert sorted(f.file for f in fs if f.rule == "JS002") == ["console/app.js", "supabase/functions/hook/index.ts"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert [(f.rule, f.severity) for f in lint(empty)[0]] == [("SC000", "high")]




# ---- TS001: guard tested only for refusal -------------------------------------------------------------------

GUARD_FX = Path(__file__).resolve().parent / "fixtures" / "guard"
GUARD_CASES = [  # (fixture, name the file gets in the scanned tree, one must-succeed control to append)
    ("refusal_only.test.ts", "src/policy.test.ts",
     '  it("operator can write", async () => {\n    const res = await fetch("/api/decisions", { method: "POST" });\n'
     '    expect(res.status).toBe(201);\n  });\n'),
    ("guard_refusal_only.py", "tests/test_policy.py",
     '\n\ndef test_admin_can_publish(client):\n    assert client.post("/publish", headers={"role": "admin"}).status_code == 200\n'),
    ("refusal_only_rls.sql", "supabase/tests/rls_test.sql",
     "\nselect lives_ok($$ insert into public.approvals (client_id, decision) values ('c1', 'approved') $$,"
     " 'approver can approve');\n"),
]


def _guard_tree(tmp_path, fixture, name, extra=""):
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    text = (GUARD_FX / fixture).read_text(encoding="utf-8")
    if extra.startswith("  it("):  # a JS control goes inside the describe it is meant to control
        end = text.rstrip().rfind("});")
        text = text[:end] + extra + text[end:]
    else:
        text += extra
    f.write_text(text, encoding="utf-8")
    return [x for x in lint(tmp_path)[0] if x.rule == "TS001"]


@pytest.mark.parametrize("fixture,name,control", GUARD_CASES)
def test_ts001_refusal_only_guard_test_is_flagged(tmp_path, fixture, name, control):
    fs = _guard_tree(tmp_path, fixture, name)
    assert [(f.file, f.severity) for f in fs] == [(name, "medium")]
    assert "blocks everyone" in fs[0].title and "must-succeed control" in fs[0].fix
    assert "every one expects a refusal" in fs[0].evidence


@pytest.mark.parametrize("fixture,name,control", GUARD_CASES)
def test_ts001_one_success_control_makes_it_clean(tmp_path, fixture, name, control):
    assert _guard_tree(tmp_path, fixture, name, control) == []


def test_ts001_non_guard_test_never_flagged(tmp_path):
    # throws/rejects alone are validation, not a guard: no caller, no policy, no status code
    assert _guard_tree(tmp_path, "not_guard.test.ts", "src/caption.test.ts") == []
    (tmp_path / "test_math.py").write_text("import pytest\n\ndef test_div():\n    with pytest.raises(ZeroDivisionError):\n"
                                           "        1 / 0\n", encoding="utf-8")
    assert [f for f in lint(tmp_path)[0] if f.rule == "TS001"] == []


def test_ts001_supabase_error_null_is_a_success_control(tmp_path):
    extra = ('\ndescribe("decision_log RLS: owner", () => {\n  it("owner can insert", async () => {\n'
             '    const { error } = await db.insert({});\n    expect(error).toBeNull();\n  });\n});\n')
    fs = _guard_tree(tmp_path, "refusal_only.test.ts", "a.test.ts", extra)
    assert [f.line for f in fs] == [4]  # the refusal-only describe is still flagged; the controlled one is not
    # one describe holding both refusal and success is clean
    code = (GUARD_FX / "refusal_only.test.ts").read_text(encoding="utf-8").rstrip().rstrip("});") + \
        '  it("owner can insert", async () => {\n    const { error } = await db.insert({});\n' \
        '    expect(error).toBeNull();\n  });\n});\n'
    (tmp_path / "a.test.ts").write_text(code, encoding="utf-8")
    assert [f for f in lint(tmp_path)[0] if f.rule == "TS001"] == []


def test_ts001_suppression(tmp_path):
    ts = "// hivescout-ignore: TS001 — success paths live in policy.db.test.ts\n"
    src = (GUARD_FX / "refusal_only.test.ts").read_text(encoding="utf-8").replace('describe("', ts + 'describe("', 1)
    (tmp_path / "a.test.ts").write_text(src, encoding="utf-8")
    sql = "-- hivescout-ignore: TS001 (lives_ok controls in rls_allowed.sql)\n" + \
        (GUARD_FX / "refusal_only_rls.sql").read_text(encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "rls.sql").write_text(sql, encoding="utf-8")
    assert [f for f in lint(tmp_path)[0] if f.rule == "TS001"] == []


def test_ts001_runs_on_test_files_other_rules_skip(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "18"}}')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export default function App(){return null}")
    fs = _guard_tree(tmp_path, "refusal_only.test.ts", "src/__tests__/rls.test.ts")
    assert [f.file for f in fs] == ["src/__tests__/rls.test.ts"]
