"""Probe tests: unit-test the detectors, and run the real browser probe against a local fixture site."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from hivescout.probe.faults import FAULTS
from hivescout.probe.heuristics import Snapshot, judge

sys.path.insert(0, str(Path(__file__).parent))


def snap(text, **kw):
    d = dict(media=0, spinners=0, actions=[], alerts=[], intercepted=1)
    d.update(kw)
    return Snapshot(text=text, **d)


BASE = snap("Orders Order 1001 Order 1002 Order 1003 Total 3")


def rules(s, fault, base=BASE):
    return sorted(f.rule for f in judge(s, base, FAULTS[fault], target="t", route="/", persona="desktop", shot=None))


def test_good_error_state_is_clean():
    s = snap("Orders We could not load your orders. Try again.", actions=["Try again"])
    assert rules(s, "http500") == []


def test_blank():
    assert rules(snap(""), "http500") == ["WEB001"]


def test_silent_substitution():
    assert rules(snap(BASE.text), "http500") == ["WEB005"]


def test_silent_missing_data():
    assert rules(snap("Orders"), "offline") == ["WEB004"]


def test_error_without_recovery():
    assert rules(snap("Orders Something went wrong"), "http500") == ["WEB006"]


def test_endless_spinner():
    assert rules(snap("Orders Loading", spinners=1), "timeout") == ["WEB002"]


def test_stack_trace():
    assert "WEB003" in rules(snap("Orders TypeError: Cannot read properties of undefined (reading 'map')"), "malformed")


def test_fault_not_exercised_is_not_judged():
    assert rules(snap("Orders", intercepted=0), "http500") == []


def test_missing_empty_state():
    assert rules(snap("Orders"), "empty") == ["WEB008"]
    assert rules(snap("Orders No orders yet. Create your first order."), "empty") == []


needs_browser = pytest.mark.skipif(
    importlib.util.find_spec("playwright") is None or os.environ.get("HIVESCOUT_SKIP_BROWSER") == "1",
    reason="playwright not installed")


@needs_browser
def test_browser_probe_against_fixture_site(tmp_path):
    import fixture_server
    from hivescout.probe.web import ProbeConfig, run

    srv, url = fixture_server.start()
    try:
        fs, meta = run(ProbeConfig(base_url=url, routes=["/good", "/spinner", "/mock", "/blank", "/stack"],
                                   out_dir=tmp_path, faults=["http500", "timeout", "offline"],
                                   settle_ms=1200, hang_wait_ms=4500))
    finally:
        srv.shutdown()
    got = {(f.route, f.fault, f.rule) for f in fs}
    assert not [g for g in got if g[0] == "/good"], got
    assert ("/spinner", "timeout", "WEB002") in got
    assert ("/mock", "offline", "WEB005") in got
    assert ("/blank", "http500", "WEB001") in got
    assert ("/stack", "offline", "WEB003") in got
    assert all(v == "exercised" for c in meta["coverage"].values() for v in c["faults"].values())
    assert any(Path(a).exists() for f in fs for a in f.artifacts)
