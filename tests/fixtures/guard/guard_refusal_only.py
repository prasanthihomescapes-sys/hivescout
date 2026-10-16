import pytest

from app.policy import PermissionDenied, publish


def test_viewer_cannot_publish(client):
    r = client.post("/publish", headers={"role": "viewer"})
    assert r.status_code == 403


def test_anon_is_unauthorized(client):
    assert client.post("/publish").status_code == 401


def test_policy_refuses_other_tenant():
    with pytest.raises(PermissionDenied):
        publish(tenant="b", user="a")
