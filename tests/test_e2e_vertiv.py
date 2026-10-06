"""END-TO-END Deep Verify qua Vertiv ACS gia lap (tests/fake_oob_device.py)
voi DUNG cac do tre nguoi van hanh mo ta tren thiet bi that:
  - sau 'connect <line>' mat 1 luc moi hien 'Password:',
  - nhap pass xong lai mat them 1 khoang moi vao phien,
  - phai go Enter nhieu lan moi ket noi toi thiet bi dich."""

import socket
import time

import pytest

import oob_lib
import oob_monitor
from oob_monitor import poll_host_multi, run_deep_verify
from tests.fake_oob_device import FakeVertivACS


def _closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _port(name, **kw):
    p = {"name": name, "target": name, "style": "cisco", "password": "vpass",
         "delay_before_pw": 0, "delay_after_pw": 0, "enters_needed": 1}
    p.update(kw)
    return p


@pytest.fixture
def make_acs(base_cfg, isolated_cwd, monkeypatch):
    oob_monitor._DB_INIT_CACHE.clear()
    monkeypatch.setattr(oob_lib, "_PARAMIKO_OK", False)
    devices = []

    def _make(ports):
        dev = FakeVertivACS(ports=ports)
        devices.append(dev)
        base_cfg.update({
            "username": "admin", "password": "secret", "enable_password": "",
            "telnet_port": dev.port, "ssh_port": _closed_port(),
            "vertiv_connect_password": "vpass",
        })
        return dev, base_cfg

    yield _make
    for d in devices:
        d.stop()
    oob_monitor._DB_INIT_CACHE.clear()


def _verify(cfg):
    _, _, snapshot, state = poll_host_multi("127.0.0.1", cfg)
    assert state == "ok"
    return {r["key"]: r for r in run_deep_verify(cfg, "ACS", "127.0.0.1", snapshot, print_fn=lambda *a, **k: None)}


def test_vertiv_scan_lists_ports(make_acs):
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01"), "2": _port("HCM-SW-02")})
    hostname, menu_name, snapshot, state = poll_host_multi("127.0.0.1", cfg)
    assert state == "ok"
    assert menu_name == "Vertiv ACS"
    assert snapshot["1"]["description"] == "HCM-ROUTER-01"
    assert snapshot["2"]["vendor"] == "vertiv"


def test_vertiv_fast_device_ok_and_not_slowed_down(make_acs):
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01")})
    t = time.time()
    r = _verify(cfg)
    assert r["1"]["status"] == "OK"
    assert time.time() - t < 20


def test_vertiv_slow_password_prompt(make_acs):
    """'connect' xong mat 7s moi hien Password: (lau hon muc cho 5s cu)."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01", delay_before_pw=7)})
    r = _verify(cfg)
    assert r["1"]["status"] == "OK", r["1"]


def test_vertiv_slow_after_password(make_acs):
    """Nhap pass xong mat 8s moi vao duoc phien."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01", delay_after_pw=8)})
    r = _verify(cfg)
    assert r["1"]["status"] == "OK", r["1"]


def test_vertiv_needs_several_enters(make_acs):
    """Thiet bi dich can 4 lan Enter moi hien prompt (code cu chi go 2 lan)."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01", enters_needed=4)})
    r = _verify(cfg)
    assert r["1"]["status"] == "OK", r["1"]
    assert dev.enters_at_target[-1] >= 4


def test_vertiv_realistic_combined_freebsd(make_acs):
    """Ket hop ca 3 do tre, may dich FreeBSD (hostname lay tu banner login)."""
    dev, cfg = make_acs({"1": _port("HCM-FW-01", style="freebsd", delay_before_pw=3,
                                    delay_after_pw=4, enters_needed=3)})
    r = _verify(cfg)
    assert r["1"]["status"] == "OK", r["1"]
    assert r["1"]["act_host"] == "HCM-FW-01"


def test_vertiv_password_ends_with_single_enter(make_acs):
    """Mat khau port gui kem DUNG 1 phim Enter (CR) nhu go tay, khong co LF thua."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01")})
    assert _verify(cfg)["1"]["status"] == "OK"
    assert dev.vpw_attempts == ["vpass"]
    assert dev.vpw_raw.startswith(b"vpass\r") and b"\n" not in dev.vpw_raw


def test_minissh_write_cr_sends_carriage_return():
    """SSH: write_cr() phai gui '\\r' (Enter cua PuTTY/OpenSSH) - truoc day gui '\\n'."""
    import socket as _s

    class _Shell:
        sent = b""
        def settimeout(self, t): pass
        def recv(self, n): raise _s.timeout()
        def send(self, b): _Shell.sent += b

    ssh = object.__new__(oob_lib.MiniSSH)
    ssh._shell, ssh.buffer = _Shell(), b""
    ssh.write_cr("pw")
    assert _Shell.sent == b"pw\r"


def test_vertiv_retry_with_acs_login_password(make_acs):
    """Port auth cua ACS xac thuc lai user dang nhap ACS: [y] sai nhung mat
    khau dang nhap ACS dung -> lan 2 thu mat khau dang nhap va vao duoc."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01", password="secret", reject_delay=0.5)})
    r = _verify(cfg)
    assert r["1"]["status"] == "OK", r["1"]
    assert dev.vpw_attempts == ["vpass", "secret"]


def test_vertiv_accepted_password_reused_first(make_acs):
    """Mat khau da duoc chap nhan o option truoc duoc thu TRUOC o option sau
    (khong ton them 1 lan bi tu choi ~7s moi option)."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01", password="secret"),
                         "2": _port("HCM-SW-02", password="secret")})
    r = _verify(cfg)
    assert r["1"]["status"] == "OK" and r["2"]["status"] == "OK"
    assert dev.vpw_attempts == ["vpass", "secret", "secret"]


def test_vertiv_rejected_password_reported_and_not_repeated(make_acs):
    """Moi mat khau bi tu choi: ket qua co ghi chu ro, va cac option sau KHONG
    gui mat khau nua (tranh khoa tai khoan ACS)."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01", password="other", reject_delay=0.5),
                         "2": _port("HCM-SW-02", password="other"),
                         "3": _port("HCM-SW-03", password="other")})
    r = _verify(cfg)
    assert dev.vpw_attempts == ["vpass", "secret"]
    assert r["1"]["status"] != "OK"
    assert "TU CHOI" in r["1"]["note"]
    for k in ("2", "3"):
        assert r[k]["status"] != "OK"
        assert "khong thu lai" in r[k]["note"]


def test_vertiv_undecrypted_password_never_sent(make_acs):
    """Chuoi chua giai ma (ENC:/B64:) khong bao gio duoc gui lam mat khau."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01", password="secret")})
    cfg["vertiv_connect_password"] = "ENC:gAAAAABfake"
    assert _verify(cfg)["1"]["status"] == "OK"
    assert dev.vpw_attempts == ["secret"]


def test_vertiv_wrong_target_still_detected(make_acs):
    """Nhieu Enter KHONG duoc lam mat kha nang phat hien sai lech."""
    dev, cfg = make_acs({"1": _port("HCM-ROUTER-01", target="OTHER-DEVICE", enters_needed=3)})
    r = _verify(cfg)
    assert r["1"]["status"] == "CANH BAO"
    assert r["1"]["act_host"] == "OTHER-DEVICE"
