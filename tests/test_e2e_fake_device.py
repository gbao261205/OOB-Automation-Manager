"""END-TO-END tren socket TCP that voi thiet bi OOB Cisco gia lap
(tests/fake_oob_device.py): Scan -> Deep Verify -> Push (dry-run va that) ->
Revert, dung code ket noi that (MiniTelnet/connect_auto), khong mock tang
mang. Bu dap cho viec khong co thiet bi that trong moi truong test."""

import os
import socket

import pytest

import oob_lib
import oob_monitor
from oob_monitor import (
    poll_host_multi, run_deep_verify, process_push_and_reverify,
    save_options, get_options_by_host,
)
from tests.fake_oob_device import FakeCiscoOOB


def _closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


@pytest.fixture
def device():
    dev = FakeCiscoOOB(lines={
        "1": {"ip": "10.0.0.1", "port": 2001, "text": "HCM-ROUTER-01", "target": "HCM-ROUTER-01"},
        "2": {"ip": "10.0.0.1", "port": 2002, "text": "HCM-SWITCH-02", "target": "WRONG-DEVICE-99"},
    })
    yield dev
    dev.stop()


@pytest.fixture
def cfg(base_cfg, isolated_cwd, device, monkeypatch):
    oob_monitor._DB_INIT_CACHE.clear()
    # Bo qua thu SSH cho nhanh (Windows mat ~2s moi lan bi refused); duong
    # SSH->Telnet fallback duoc test rieng o test_ssh_fallback_to_telnet.
    monkeypatch.setattr(oob_lib, "_PARAMIKO_OK", False)
    base_cfg.update({
        "username": "admin", "password": "secret", "enable_password": "enpass",
        "telnet_port": device.port, "ssh_port": _closed_port(),
        "verify_wait_after_connect_telnet": 0.3,
    })
    yield base_cfg
    oob_monitor._DB_INIT_CACHE.clear()


def _quiet(*a, **k):
    pass


def test_scan_reads_real_menu_over_telnet(cfg, device):
    hostname, menu_name, snapshot, state = poll_host_multi("127.0.0.1", cfg)
    assert state == "ok"
    assert menu_name == "MAINMENU"
    assert set(snapshot) == {"1", "2"}
    assert snapshot["1"]["description"] == "HCM-ROUTER-01"
    assert snapshot["2"]["port"] == 2002
    assert hostname == "OOB-HCM-01"


def test_deep_verify_detects_ok_and_mismatch(cfg, device):
    _, mn, snapshot, _ = poll_host_multi("127.0.0.1", cfg)
    results = {r["key"]: r for r in run_deep_verify(cfg, "TestOOB", "127.0.0.1", snapshot, print_fn=_quiet)}
    assert results["1"]["status"] == "OK"
    assert results["1"]["act_host"] == "HCM-ROUTER-01"
    assert results["2"]["status"] == "CANH BAO"
    assert results["2"]["act_host"] == "WRONG-DEVICE-99"


def _scan_and_verify(cfg):
    hn, mn, snapshot, _ = poll_host_multi("127.0.0.1", cfg)
    save_options(cfg["baseline_db"], "baseline_menu", "127.0.0.1", mn, hn, snapshot)
    _, _, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", "127.0.0.1")
    results = run_deep_verify(cfg, "TestOOB", "127.0.0.1", baseline, print_fn=_quiet)
    return baseline, results


def test_push_dry_run_sends_nothing_to_device(cfg, device):
    assert cfg["push_live_mode"] is False
    baseline, results = _scan_and_verify(cfg)
    process_push_and_reverify(cfg, "TestOOB", "127.0.0.1", baseline, results, print_fn=_quiet)

    assert "configure terminal" not in device.commands  # bang chung manh nhat: khong he vao config mode
    assert device.menu_text("2") == "HCM-SWITCH-02"
    _, _, bl = get_options_by_host(cfg["baseline_db"], "baseline_menu", "127.0.0.1")
    assert bl["2"]["description"] == "HCM-SWITCH-02"


def test_push_live_changes_device_then_reverify_ok(cfg, device):
    cfg["push_live_mode"] = True
    baseline, results = _scan_and_verify(cfg)
    process_push_and_reverify(cfg, "TestOOB", "127.0.0.1", baseline, results, print_fn=_quiet)

    assert "menu MAINMENU text 2 ----> WRONG-DEVICE-99" in device.commands
    assert device.menu_text("2") == "----> WRONG-DEVICE-99"
    assert device.menu_text("1") == "HCM-ROUTER-01"  # option dung KHONG bi dong vao
    _, _, bl = get_options_by_host(cfg["baseline_db"], "baseline_menu", "127.0.0.1")
    assert bl["2"]["description"] == "----> WRONG-DEVICE-99"

    # Scan lai tu thiet bi: cau hinh that da doi, khop baseline moi
    _, _, snap2, _ = poll_host_multi("127.0.0.1", cfg)
    assert oob_monitor.options_equal(bl, snap2)
    # Verify lai: het CANH BAO
    statuses = {r["key"]: r["status"] for r in run_deep_verify(cfg, "TestOOB", "127.0.0.1", bl, print_fn=_quiet)}
    assert statuses == {"1": "OK", "2": "OK"}


def test_revert_via_web_restores_device(cfg, device, monkeypatch):
    import oob_web

    cfg["push_live_mode"] = True
    oob_monitor.save_config(oob_monitor.CONFIG_FILE_DEFAULT, cfg)
    baseline, results = _scan_and_verify(cfg)
    process_push_and_reverify(cfg, "TestOOB", "127.0.0.1", baseline, results, print_fn=_quiet)
    assert device.menu_text("2") == "----> WRONG-DEVICE-99"

    class ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._t, self._a, self._k = target, args, kwargs or {}
        def start(self):
            self._t(*self._a, **self._k)
    monkeypatch.setattr(oob_web.threading, "Thread", ImmediateThread)

    log_name = os.listdir("push-logs")[0]
    oob_web.app.config["TESTING"] = True
    with oob_web.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
        resp = c.post("/api/revert", json={"filename": log_name})
    assert resp.status_code == 200
    assert device.menu_text("2") == "HCM-SWITCH-02"


def test_wrong_password_raises_fast_instead_of_silent_timeout(cfg, device):
    """Truoc khi sua: connect_auto() tra ve session 'nhu da dang nhap' du bi
    tu choi -> cho het ~42s timeout roi bao 'fetch_failed' (sai nguyen nhan)."""
    import time
    cfg["password"] = "WRONG"
    t = time.time()
    with pytest.raises(PermissionError):
        poll_host_multi("127.0.0.1", cfg)
    assert time.time() - t < 10


def test_multi_account_falls_back_to_backup_credential(cfg, device):
    """Tai khoan chinh sai, tai khoan phu dung -> phai thu tai khoan phu va
    Scan thanh cong. Truoc khi sua: chi 1 ket noi, khong bao gio thu tai
    khoan phu."""
    cfg["password"] = "WRONG"
    cfg["credentials"] = [{"username": "admin", "password": "secret", "enable_password": "enpass"}]
    hostname, menu_name, snapshot, state = poll_host_multi("127.0.0.1", cfg)
    assert state == "ok"
    assert set(snapshot) == {"1", "2"}
    assert device.connections == 2


def test_deep_verify_dead_line_times_out_and_clears_line(cfg, device):
    """Line chet (khong co thiet bi tra loi) -> TIMEOUT, va voi port > 2000
    tool phai thu 'clear line' roi verify lai dung 1 lan."""
    device.lines["3"] = {"ip": "10.0.0.1", "port": 2003, "text": "HCM-FW-03", "target": None}
    _, _, snapshot, _ = poll_host_multi("127.0.0.1", cfg)
    only_dead = {"3": snapshot["3"]}
    results = run_deep_verify(cfg, "TestOOB", "127.0.0.1", only_dead, print_fn=_quiet)
    assert results[0]["status"] == "TIMEOUT"
    assert "clear line 3" in device.commands
    assert "Da thu clear line 3" in results[0]["note"]


def test_ssh_fallback_to_telnet(cfg, device, monkeypatch):
    monkeypatch.setattr(oob_lib, "_PARAMIKO_OK", True)
    hostname, menu_name, snapshot, state = poll_host_multi("127.0.0.1", cfg)
    assert state == "ok"
    assert set(snapshot) == {"1", "2"}


def test_cisco_banner_with_gt_still_enters_enable_mode(base_cfg, isolated_cwd, monkeypatch):
    """Chong hoi quy cho viec bo 'enable' voi Vertiv: banner exec Cisco co
    ky tu '>' GIUA dong lam lan doc dau bi cat som - van phai vao enable."""
    oob_monitor._DB_INIT_CACHE.clear()
    monkeypatch.setattr(oob_lib, "_PARAMIKO_OK", False)
    dev = FakeCiscoOOB(banner="Authorized access only -> contact NOC",
                       lines={"1": {"ip": "10.0.0.1", "port": 2001, "text": "R1", "target": "R1"}})
    try:
        base_cfg.update({"username": "admin", "password": "secret", "enable_password": "enpass",
                         "telnet_port": dev.port, "ssh_port": _closed_port()})
        hostname, menu_name, snapshot, state = poll_host_multi("127.0.0.1", base_cfg)
        assert state == "ok"
        assert hostname == "OOB-HCM-01"
    finally:
        dev.stop()
