"""Test cho WP-H: validate IP qua api_device POST, PUT de sua alias/IP giu
nguyen vi tri dong, va /api/daemon-status doc dung trang thai daemon.pid."""

import json
import os

import pytest


@pytest.fixture
def oob_web_module(isolated_cwd):
    import oob_web
    return oob_web


@pytest.fixture
def client(oob_web_module):
    oob_web_module.app.config["TESTING"] = True
    with oob_web_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "admin"
        yield c


def test_api_device_post_rejects_invalid_ip(client, isolated_cwd):
    resp = client.post("/api/device", json={"ip": "not-an-ip", "alias": "Bad"})
    assert resp.status_code == 400


def test_api_device_post_accepts_valid_ip(client, isolated_cwd, oob_web_module):
    import oob_monitor
    resp = client.post("/api/device", json={"ip": "10.0.0.5", "alias": "Good"})
    assert resp.status_code == 200
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    assert ("10.0.0.5", "Good") in oob_monitor.load_ip_list(cfg["ip_list"])


def test_api_device_put_updates_alias_in_place(client, isolated_cwd, oob_web_module):
    import oob_monitor
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    with open(cfg["ip_list"], "w", encoding="utf-8") as f:
        f.write("10.0.0.1 First\n10.0.0.2 Second\n")

    resp = client.put("/api/device", json={"old_ip": "10.0.0.1", "alias": "Renamed"})
    assert resp.status_code == 200
    hosts = oob_monitor.load_ip_list(cfg["ip_list"])
    assert hosts == [("10.0.0.1", "Renamed"), ("10.0.0.2", "Second")]


def test_api_device_put_rejects_invalid_new_ip(client, isolated_cwd, oob_web_module):
    import oob_monitor
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    with open(cfg["ip_list"], "w", encoding="utf-8") as f:
        f.write("10.0.0.1 First\n")

    resp = client.put("/api/device", json={"old_ip": "10.0.0.1", "new_ip": "garbage"})
    assert resp.status_code == 400
    assert oob_monitor.load_ip_list(cfg["ip_list"]) == [("10.0.0.1", "First")]


def test_api_device_put_missing_old_ip_400(client, isolated_cwd):
    resp = client.put("/api/device", json={"alias": "X"})
    assert resp.status_code == 400


def test_api_daemon_status_unknown_when_no_pid_file(client, isolated_cwd):
    resp = client.get("/api/daemon-status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "unknown"
    assert body["pid"] is None


def test_api_daemon_status_running_when_fresh_heartbeat(client, isolated_cwd):
    from datetime import datetime
    with open("daemon.pid", "w") as f:
        f.write(f"12345\n{datetime.now().isoformat()}\n")

    resp = client.get("/api/daemon-status")
    body = resp.get_json()
    assert body["status"] == "running"
    assert body["pid"] == 12345


def test_api_daemon_status_stale_when_old_heartbeat(client, isolated_cwd):
    from datetime import datetime, timedelta
    with open("daemon.pid", "w") as f:
        f.write(f"12345\n{(datetime.now() - timedelta(minutes=10)).isoformat()}\n")

    resp = client.get("/api/daemon-status")
    body = resp.get_json()
    assert body["status"] == "stale"
