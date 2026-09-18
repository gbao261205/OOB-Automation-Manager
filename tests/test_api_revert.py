"""Test cho api_revert (WP-B): tu choi revert 1 push-log MO PHONG (khong co
gi de revert vi chua he gui gi that), va Revert cung phai tuan theo cong tac
push_live_mode giong het Push (truoc day dry_run=True bi hardcode rieng,
khong dong bo voi push_live_mode)."""

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


class ImmediateThread:
    """Thay threading.Thread that de _run_rev() chay dong bo ngay trong test,
    khong can cho background thread."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _write_push_log(mode_label, alias="TestOOB", ip="10.0.0.9"):
    os.makedirs("push-logs", exist_ok=True)
    fname = f"Push_{alias}_20260101_000000.log"
    with open(os.path.join("push-logs", fname), "w", encoding="utf-8") as f:
        f.write(f"=== PUSH LOG THU CONG [{mode_label}]: {alias} ({ip}) ===\nThoi gian: 2026-01-01 00:00:00\n\n")
        f.write("- Option [1] (Target IP: 10.0.0.9):\n  + Cu : ----> OLD-HOST\n  + Moi: ----> NEW-HOST\n")
        f.write("  REVERT CMD: menu MAINMENU text 1 ----> OLD-HOST\n")
    return fname


def test_revert_rejects_simulated_push_log(client, isolated_cwd):
    fname = _write_push_log("MO PHONG")
    resp = client.post("/api/revert", json={"filename": fname})
    assert resp.status_code == 400
    body = resp.get_json()
    assert "MO PHONG" in body["error"]


def test_revert_of_real_log_is_simulated_when_push_live_mode_off(client, isolated_cwd, oob_web_module, monkeypatch):
    fname = _write_push_log("THAT")
    monkeypatch.setattr(oob_web_module.threading, "Thread", ImmediateThread)

    push_calls = []
    def fake_push(*args, **kwargs):
        push_calls.append(kwargs)
        return True
    monkeypatch.setattr(oob_web_module.oob_monitor, "push_menu_descriptions", fake_push)

    resp = client.post("/api/revert", json={"filename": fname})
    assert resp.status_code == 200
    assert push_calls[0]["dry_run"] is True  # push_live_mode mac dinh False


def test_revert_of_real_log_is_live_when_push_live_mode_on(client, isolated_cwd, oob_web_module, monkeypatch):
    import oob_monitor
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    cfg["push_live_mode"] = True
    oob_monitor.save_config(oob_monitor.CONFIG_FILE_DEFAULT, cfg)

    fname = _write_push_log("THAT")
    monkeypatch.setattr(oob_web_module.threading, "Thread", ImmediateThread)

    push_calls = []
    def fake_push(*args, **kwargs):
        push_calls.append(kwargs)
        return True
    monkeypatch.setattr(oob_web_module.oob_monitor, "push_menu_descriptions", fake_push)

    resp = client.post("/api/revert", json={"filename": fname})
    assert resp.status_code == 200
    assert push_calls[0]["dry_run"] is False


def test_revert_missing_filename_400(client, isolated_cwd):
    resp = client.post("/api/revert", json={})
    assert resp.status_code == 400


def test_revert_nonexistent_file_404(client, isolated_cwd):
    resp = client.post("/api/revert", json={"filename": "Push_Ghost_99999999_000000.log"})
    assert resp.status_code == 404
