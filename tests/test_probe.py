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


# ----------------------------------------------------------------------------- journeys + auth

def test_glob_to_regex():
    from hivescout.probe.web import glob_to_regex
    rx = glob_to_regex("**/api/**")
    assert rx.match("http://x.test/api/orders") and not rx.match("http://x.test/apix")
    rx = glob_to_regex("**/{api,rest}/v1/*")
    assert rx.match("https://a.b/rest/v1/items") and not rx.match("https://a.b/rest/v1/items/2")


def test_journey_validation():
    from hivescout.probe.journey import Journey
    with pytest.raises(ValueError, match="act"):
        Journey.from_dict({"id": "x", "start": "/", "steps": []})
    with pytest.raises(ValueError, match="locator"):
        Journey.from_dict({"id": "x", "act": [{"click": {"name": "Save"}}]})
    with pytest.raises(ValueError, match="unknown action"):
        Journey.from_dict({"id": "x", "act": [{"tap": {"text": "Save"}}]})


def test_env_values(monkeypatch):
    from hivescout.probe.journey import resolve
    monkeypatch.setenv("HS_T", "abc")
    assert resolve("u-${env:HS_T}") == "u-abc"
    assert resolve("${env:HS_MISSING|dflt}") == "dflt"
    with pytest.raises(KeyError):
        resolve("${env:HS_MISSING}")


SAVE = {"steps": [{"fill": {"label": "Order name", "value": "HiveScout test order"}}],
        "act": [{"click": {"role": "button", "name": "Save order"}}]}


@needs_browser
def test_signed_in_journeys_against_fixture(tmp_path, monkeypatch):
    import fixture_server
    from hivescout.auth import AuthConfig, capture, check
    from hivescout.probe.journey import Journey
    from hivescout.probe.web import ProbeConfig, run

    srv, url = fixture_server.start()
    monkeypatch.setenv("HS_FX_EMAIL", fixture_server.TEST_EMAIL)
    monkeypatch.setenv("HS_FX_PASSWORD", fixture_server.TEST_PASSWORD)
    auth = AuthConfig(login_path="/login", check_path="/app", steps=[
        {"fill": {"label": "Email", "value": "${env:HS_FX_EMAIL}"}},
        {"fill": {"label": "Password", "value": "${env:HS_FX_PASSWORD}"}},
        {"click": {"role": "button", "name": "Sign in"}}])
    state = tmp_path / "auth" / "fx.json"
    try:
        assert check(url, auth, state)[0] is False           # nothing saved yet
        capture(url, auth, state, timeout_s=30)
        assert check(url, auth, state)[0] is True
        journeys = [Journey.from_dict({"id": "good", "start": "/app", "expect": {"text": "Order saved"},
                                       "mutates": False, **SAVE}),
                    Journey.from_dict({"id": "bad", "start": "/app-bad", "mutates": False, **SAVE})]
        fs, meta = run(ProbeConfig(base_url=url, routes=[], out_dir=tmp_path / "run", journeys=journeys,
                                   faults=["http500", "slow", "offline"], storage_state=str(state),
                                   login_path="/login", settle_ms=1200, hang_wait_ms=7000))
        # an expired / missing session is reported, not silently probed as the login page
        fs2, _ = run(ProbeConfig(base_url=url, routes=["/app"], out_dir=tmp_path / "run2", faults=["http500"],
                                 login_path="/login", settle_ms=800))
    finally:
        srv.shutdown()
    got = {(f.route, f.fault, f.rule) for f in fs}
    assert not [g for g in got if g[0] == "journey:good"], got
    for fault in ("http500", "offline"):
        assert ("journey:bad", fault, "WEB010") in got      # typed input wiped
        assert ("journey:bad", fault, "WEB005") in got      # failure looks like success
    assert ("journey:bad", "slow", "WEB009") in got         # double click sends twice
    assert ("journey:bad", "slow", "WEB011") in got         # no feedback after click
    assert [(f.rule, f.title.split(":")[0]) for f in fs2] == [("WEB000", "Signed-in session missing or expired")]


def test_stuck_loading_after_response():
    s = snap("Quote Dashboard Showing 0 of 0 Loading live pipeline...")
    assert rules(s, "empty") == ["WEB012"]
    assert rules(s, "http500") == ["WEB012"]
    # a loader that was already part of the healthy page is not new
    assert rules(s, "empty", base=snap("Quote Dashboard Loading live pipeline... Order 1")) != ["WEB012"]
