"""Phase 7 - hardening: chong hoi quy cho cac lo hong tim thay khi
security-review toan bo diff tich luy."""

import os
import socket

import pytest

import oob_lib
import oob_monitor
from oob_monitor import _clean_alias, add_ip, update_ip, load_ip_list, poll_host_multi, run_deep_verify
from tests.fake_oob_device import FakeVertivACS


# ---------------------------------------------------------------- alias ----
def test_clean_alias_replaces_whitespace_and_control_chars():
    assert _clean_alias("HCM OOB 01") == "HCM_OOB_01"
    assert _clean_alias("A\nB") == "A_B"
    assert _clean_alias("A\r\n10.9.9.9 evil") == "A_10.9.9.9_evil"
    assert _clean_alias("  ") is None
    assert _clean_alias(None) is None
    assert _clean_alias("x" * 100) == "x" * 64


def test_add_ip_alias_with_newline_cannot_inject_extra_host(isolated_cwd):
    add_ip("oob_ips.txt", "10.0.0.1", "A\n10.9.9.9 evil")
    hosts = load_ip_list("oob_ips.txt")
    assert hosts == [("10.0.0.1", "A_10.9.9.9_evil")]


def test_update_ip_alias_with_newline_cannot_inject_extra_host(isolated_cwd):
    add_ip("oob_ips.txt", "10.0.0.1", "A")
    ok, _ = update_ip("oob_ips.txt", "10.0.0.1", new_alias="B\n10.9.9.9 evil")
    assert ok
    assert load_ip_list("oob_ips.txt") == [("10.0.0.1", "B_10.9.9.9_evil")]


# ------------------------------------------------------------------ XSS ----
def test_no_esc_inside_single_quoted_js_string_in_web_template():
    """esc() (HTML entity) KHONG an toan trong chuoi JS cua onclick - trinh
    duyet giai ma entity truoc khi chay handler. Phai dung jsArg()."""
    src = open(os.path.join(os.path.dirname(oob_monitor.__file__), "oob_web.py"), encoding="utf-8").read()
    assert "'${esc(" not in src


# ------------------------------------------------- mat khau Vertiv bi lo ----
def _closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


@pytest.fixture
def vertiv_cfg(base_cfg, isolated_cwd, monkeypatch):
    oob_monitor._DB_INIT_CACHE.clear()
    monkeypatch.setattr(oob_lib, "_PARAMIKO_OK", False)
    dev = FakeVertivACS(ports={"1": {"name": "HCM-ROUTER-01", "target": "HCM-ROUTER-01",
                                     "password": "S3cretVPass", "enters_needed": 1}})
    base_cfg.update({"username": "admin", "password": "secret", "enable_password": "",
                     "telnet_port": dev.port, "ssh_port": _closed_port(),
                     "vertiv_connect_password": "S3cretVPass"})
    yield base_cfg
    dev.stop()
    oob_monitor._DB_INIT_CACHE.clear()


def test_live_debug_never_streams_vertiv_password(vertiv_cfg):
    """Live Debug tren Web phat MOI dong qua SSE (/api/events khong can dang
    nhap) - mat khau Vertiv tuyet doi khong duoc xuat hien trong output."""
    _, _, snapshot, _ = poll_host_multi("127.0.0.1", vertiv_cfg)
    lines = []
    results = run_deep_verify(vertiv_cfg, "ACS", "127.0.0.1", snapshot,
                              print_fn=lambda m, *a, **k: lines.append(str(m)), live_debug_opt="1")
    assert results[0]["status"] == "OK"
    joined = "\n".join(lines)
    assert "S3cretVPass" not in joined
    assert "******" in joined  # van thong bao la co gui mat khau


def test_debug_log_never_contains_vertiv_password(vertiv_cfg):
    vertiv_cfg["debug_verify"] = True
    _, _, snapshot, _ = poll_host_multi("127.0.0.1", vertiv_cfg)
    run_deep_verify(vertiv_cfg, "ACS", "127.0.0.1", snapshot, print_fn=lambda *a, **k: None)
    with open(oob_monitor.DEBUG_VERIFY_LOG, encoding="utf-8") as f:
        content = f.read()
    assert "B2-after-vpass" in content
    assert "S3cretVPass" not in content


# ------------------------------------------- phat hien tu code-review high ----
import threading
import time


def test_drain_has_deadline_on_chatty_console():
    """Console thiet bi xa log lien tuc (Cisco 'logging console') - drain
    khong bao gio im lang 0.15s. Truoc khi sua: write() treo vinh vien."""
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def chatty():
        conn, _ = srv.accept()
        try:
            while not stop.is_set():
                conn.sendall(b"%LINK-3-UPDOWN: Interface Gi0/1, changed state to up\r\n")
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            conn.close()

    threading.Thread(target=chatty, daemon=True).start()
    tn = oob_lib.MiniTelnet("127.0.0.1", port, timeout=5)
    try:
        t = time.time()
        tn.write("show clock")
        assert time.time() - t < oob_lib.DRAIN_MAX_SECONDS + 1.0
    finally:
        stop.set(); tn.close(); srv.close()


def test_read_until_prompt_uses_leftover_buffer():
    a, b = socket.socketpair()
    try:
        tn = oob_lib.MiniTelnet.__new__(oob_lib.MiniTelnet)
        tn.sock, tn.buffer, tn.last_read_timed_out = a, b"menu X text 1 R1\r\nOOB-HCM-01#", False
        t = time.time()
        out = tn.read_until_prompt(timeout=5)
        assert time.time() - t < 1.0
        assert tn.last_read_timed_out is False
        assert "menu X text 1 R1" in out
    finally:
        a.close(); b.close()


def test_banner_containing_password_word_is_not_a_login_failure(base_cfg, isolated_cwd, monkeypatch):
    from tests.fake_oob_device import FakeCiscoOOB
    oob_monitor._DB_INIT_CACHE.clear()
    monkeypatch.setattr(oob_lib, "_PARAMIKO_OK", False)
    dev = FakeCiscoOOB(banner="Reminder - change your Password:",
                       lines={"1": {"ip": "10.0.0.1", "port": 2001, "text": "R1", "target": "R1"}})
    try:
        base_cfg.update({"username": "admin", "password": "secret", "enable_password": "enpass",
                         "telnet_port": dev.port, "ssh_port": _closed_port()})
        _, _, snapshot, state = poll_host_multi("127.0.0.1", base_cfg)
        assert state == "ok"
        assert set(snapshot) == {"1"}
    finally:
        dev.stop()


@pytest.fixture
def web_client(isolated_cwd):
    import oob_web
    oob_web.app.config["TESTING"] = True
    with oob_web.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
        yield c


@pytest.mark.parametrize("payload", [
    {"verify_max_workers": 0}, {"scan_max_workers": "10"}, {"vertiv_wake_enters": "5x"},
    {"vertiv_prompt_timeout": -1}, {"ssh_port": 70000}, {"max_verify_duration": 10},
    {"verify_schedule_day_of_month": 32}, {"interval": True},
])
def test_api_config_rejects_invalid_numbers(web_client, payload):
    before = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    resp = web_client.post("/api/config", json=payload)
    assert resp.status_code == 400
    after = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    key = next(iter(payload))
    assert after[key] == before[key]


def test_api_config_accepts_valid_numbers(web_client):
    resp = web_client.post("/api/config", json={"verify_max_workers": 20, "vertiv_prompt_timeout": 30,
                                                "verify_wait_after_connect_ssh": 2.5})
    assert resp.status_code == 200
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    assert (cfg["verify_max_workers"], cfg["vertiv_prompt_timeout"], cfg["verify_wait_after_connect_ssh"]) == (20, 30, 2.5)


def test_export_excel_works_when_cwd_differs_from_code_dir(web_client, isolated_cwd):
    """Web chay duoi dang service/Task Scheduler thuong co cwd khac thu muc
    code - truoc khi sua, send_file() tim file trong thu muc code -> 500."""
    assert os.path.abspath(os.getcwd()) != os.path.dirname(os.path.abspath(oob_monitor.__file__))
    resp = web_client.get("/api/export/excel")
    assert resp.status_code == 200
    assert resp.data[:2] == b"PK"  # xlsx la file zip
