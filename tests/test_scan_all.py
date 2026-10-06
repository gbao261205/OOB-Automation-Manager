"""Scan tat ca (lay menu + verify desc) co checkpoint, va Deep Verify luu ket
qua SAU MOI OPTION - dung giua chung khong mat phan da xong. Chay tren thiet bi
Cisco OOB gia lap qua socket that (tests/fake_oob_device.py)."""

import json
import socket
import threading
import time

import pytest

import oob_lib
import oob_monitor
from oob_monitor import (
    run_deep_verify, run_scan_all, load_scan_all_progress, scan_all_summary,
    poll_host_multi, get_options_by_host, _load_latest_verify_results,
)
from tests.fake_oob_device import FakeCiscoOOB

DEAD_IP = "10.255.255.1"


def _closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _quiet(*a, **k):
    pass


@pytest.fixture
def device():
    dev = FakeCiscoOOB(lines={
        "1": {"ip": "10.0.0.1", "port": 2001, "text": "HCM-ROUTER-01", "target": "HCM-ROUTER-01"},
        "2": {"ip": "10.0.0.1", "port": 2002, "text": "HCM-SWITCH-02", "target": "WRONG-DEVICE-99"},
        "3": {"ip": "10.0.0.1", "port": 2003, "text": "HCM-FW-03", "target": "HCM-FW-03"},
    })
    yield dev
    dev.stop()


@pytest.fixture
def cfg(base_cfg, isolated_cwd, device, monkeypatch):
    oob_monitor._DB_INIT_CACHE.clear()
    oob_monitor._ip_list_cache["path"] = None
    monkeypatch.setattr(oob_lib, "_PARAMIKO_OK", False)
    monkeypatch.setattr(oob_monitor, "ping_host", lambda ip, *a, **k: ip == "127.0.0.1")
    base_cfg.update({
        "username": "admin", "password": "secret", "enable_password": "enpass",
        "telnet_port": device.port, "ssh_port": _closed_port(),
        "verify_wait_after_connect_telnet": 0.3,
    })
    with open(base_cfg["ip_list"], "w", encoding="utf-8") as f:
        f.write(f"127.0.0.1 OOB-A\n{DEAD_IP} OOB-DEAD\n")
    yield base_cfg
    oob_monitor._DB_INIT_CACHE.clear()
    oob_monitor._ip_list_cache["path"] = None


def _reverse_ports(device, since=0):
    return [c.split()[2] for c in device.commands[since:] if c.startswith("telnet ")]


# ----------------------------------------------------------- run_deep_verify

def test_verify_result_is_saved_before_next_option(cfg, device):
    _, _, snapshot, _ = poll_host_multi("127.0.0.1", cfg)
    saved_at_callback = []

    def on_result(key, res):
        saved_at_callback.append((key, {r["key"] for r in _load_latest_verify_results("OOB-A")}))

    run_deep_verify(cfg, "OOB-A", "127.0.0.1", snapshot, print_fn=_quiet, on_result=on_result)
    assert [k for k, _ in saved_at_callback] == ["1", "2", "3"]
    for key, saved in saved_at_callback:
        assert key in saved   # da nam tren dia NGAY khi option do xong


def test_verify_stop_midway_keeps_done_and_previous_results(cfg, device):
    _, _, snapshot, _ = poll_host_multi("127.0.0.1", cfg)
    run_deep_verify(cfg, "OOB-A", "127.0.0.1", snapshot, print_fn=_quiet)   # lan 1: du 3 option
    time.sleep(1.1)   # ten file theo giay
    done = []
    results = run_deep_verify(cfg, "OOB-A", "127.0.0.1", snapshot, print_fn=_quiet,
                              on_result=lambda k, r: done.append(k), should_stop=lambda: bool(done))
    assert done == ["1"]
    saved = {r["key"]: r for r in _load_latest_verify_results("OOB-A")}
    assert set(saved) == {"1", "2", "3"}          # option chua chay giu ket qua cu
    assert {r["key"] for r in results} == {"1", "2", "3"}
    assert saved["2"]["status"] == "CANH BAO"


def test_latest_verify_results_matches_exact_alias(isolated_cwd):
    import os
    os.makedirs("verify-logs")
    with open("verify-logs/Verify_ACS_DC2_20260101_000000.json", "w") as f:
        json.dump([{"key": "9", "status": "OK"}], f)
    assert _load_latest_verify_results("ACS") == []
    assert _load_latest_verify_results("ACS_DC2")[0]["key"] == "9"


# ------------------------------------------------------------- run_scan_all

def test_scan_all_scans_and_verifies_every_device(cfg, device):
    prog = run_scan_all(cfg, print_fn=_quiet)
    hosts = {h["alias"]: h for h in prog["hosts"]}
    assert hosts["OOB-A"]["scan"] == "ok" and hosts["OOB-A"]["verify"] == "done"
    assert sorted(hosts["OOB-A"]["verified_keys"]) == ["1", "2", "3"]
    assert hosts["OOB-DEAD"]["scan"] == "offline"
    s = scan_all_summary(load_scan_all_progress())
    assert s["finished"] and s["done"] == s["total"] == 2
    assert s["opts_verified"] == s["opts_total"] == 3
    _, _, bl = get_options_by_host(cfg["baseline_db"], "baseline_menu", "127.0.0.1")
    assert set(bl) == {"1", "2", "3"}             # baseline da luu
    saved = {r["key"]: r["status"] for r in _load_latest_verify_results("OOB-A")}
    assert saved == {"1": "OK", "2": "CANH BAO", "3": "OK"}


def test_scan_all_stop_then_resume_skips_finished_work(cfg, device):
    def stop_after_first_option():
        p = load_scan_all_progress()
        return bool(p) and any(h.get("verified_keys") for h in p["hosts"])

    prog = run_scan_all(cfg, print_fn=_quiet, should_stop=stop_after_first_option)
    assert prog["stopped"] and not prog["finished"]
    s = scan_all_summary(load_scan_all_progress())
    assert s["opts_verified"] == 1 and s["done"] < s["total"]
    assert _reverse_ports(device) == ["2001"]
    n_cmds = len(device.commands)

    prog = run_scan_all(cfg, print_fn=_quiet, resume=True)
    assert prog["finished"]
    assert _reverse_ports(device, n_cmds) == ["2002", "2003"]     # KHONG verify lai line 1
    assert not any("menu" in c for c in device.commands[n_cmds:])  # KHONG scan lai menu
    s = scan_all_summary(load_scan_all_progress())
    assert s["done"] == s["total"] and s["opts_verified"] == 3
    saved = {r["key"]: r["status"] for r in _load_latest_verify_results("OOB-A")}
    assert saved == {"1": "OK", "2": "CANH BAO", "3": "OK"}


def test_scan_all_fresh_run_ignores_old_progress(cfg, device):
    run_scan_all(cfg, print_fn=_quiet)
    n_cmds = len(device.commands)
    run_scan_all(cfg, print_fn=_quiet)            # tu dau: lam lai het
    assert _reverse_ports(device, n_cmds) == ["2001", "2002", "2003"]


# ---------------------------------------------------------------- Web API

@pytest.fixture
def web_client(isolated_cwd, monkeypatch):
    import oob_web
    started = threading.Event()

    def fake_run(cfg, print_fn=None, resume=False, should_stop=None, **kw):
        started.set()
        deadline = time.time() + 10
        while not should_stop() and time.time() < deadline:
            time.sleep(0.05)
        fake_run.resume = resume

    monkeypatch.setattr(oob_monitor, "run_scan_all", fake_run)
    oob_web.app.config["TESTING"] = True
    with oob_web.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
        c.started, c.fake_run = started, fake_run
        yield c
    oob_web._scan_all_state["stop"].set()


def _wait_idle(client, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get("/api/scan-all/status").get_json()["running"]:
            return True
        time.sleep(0.05)
    return False


def test_api_scan_all_single_run_and_stop(web_client):
    r = web_client.post("/api/scan-all", json={})
    assert r.status_code == 200 and r.get_json()["task_id"].startswith("scanall_")
    assert web_client.started.wait(3)
    assert web_client.post("/api/scan-all", json={}).status_code == 409   # chi 1 lan chay
    st = web_client.get("/api/scan-all/status").get_json()
    assert st["running"] and not st["stopping"]
    assert web_client.post("/api/scan-all/stop").status_code == 200
    assert _wait_idle(web_client)
    assert web_client.post("/api/scan-all/stop").status_code == 400


def test_api_scan_all_resume_requires_unfinished_progress(web_client, isolated_cwd):
    assert web_client.post("/api/scan-all", json={"resume": True}).status_code == 400
    with open(oob_monitor.SCAN_ALL_PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"run_id": "x", "hosts": [{"ip": "1.1.1.1", "alias": "A", "scan": "ok", "verify": "partial"}]}, f)
    st = web_client.get("/api/scan-all/status").get_json()
    assert st["progress"]["done"] == 0 and st["progress"]["total"] == 1
    assert web_client.post("/api/scan-all", json={"resume": True}).status_code == 200
    assert web_client.started.wait(3)
    web_client.post("/api/scan-all/stop")
    assert _wait_idle(web_client)
    assert web_client.fake_run.resume is True


def test_api_scan_all_requires_login(isolated_cwd):
    import oob_web
    with oob_web.app.test_client() as c:
        assert c.post("/api/scan-all", json={}).status_code == 401
        assert c.get("/api/scan-all/status").status_code == 401
