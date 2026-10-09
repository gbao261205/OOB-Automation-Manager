"""Web Console end-to-end tren thiet bi OOB gia lap (socket TCP that):
mo phien -> pivot sang line -> thay prompt thiet bi dau xa -> go lenh ->
dong phien."""

import socket
import time

import pytest

import oob_console
import oob_lib
import oob_monitor
from tests.fake_oob_device import FakeCiscoOOB, FakeVertivACS


def _closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _wait_for(cs, text, timeout=8):
    deadline, seen, last = time.time() + timeout, b"", 0
    while time.time() < deadline:
        last, data, closed = cs.read_since(last, timeout=0.5)
        seen += data
        if text.encode() in seen:
            return seen.decode(errors="ignore")
        if closed:
            break
    raise AssertionError(f"khong thay {text!r} trong output:\n{seen.decode(errors='ignore')}")


@pytest.fixture
def cfg(base_cfg, isolated_cwd, monkeypatch):
    monkeypatch.setattr(oob_lib, "_PARAMIKO_OK", False)
    base_cfg.update({"username": "admin", "password": "secret", "enable_password": "enpass",
                     "ssh_port": _closed_port()})
    return base_cfg


def _creds():
    return [{"username": "admin", "password": "secret", "enable_password": "enpass"}]


def test_console_cisco_pivot_and_type(cfg):
    dev = FakeCiscoOOB(lines={"1": {"ip": "10.0.0.1", "port": 2001, "text": "R1", "target": "HCM-ROUTER-01"}})
    try:
        cfg["telnet_port"] = dev.port
        opt = {"description": "R1", "ip": "10.0.0.1", "port": 2001, "protocol": "telnet", "vendor": "cisco"}
        cs = oob_console.open_console(cfg, "admin", "127.0.0.1", "1", opt, "OOB-1", _creds())
        _wait_for(cs, "telnet 10.0.0.1 2001")
        cs.send(b"\r")
        _wait_for(cs, "HCM-ROUTER-01")
        assert oob_console.get(cs.sid, "admin") is cs
        assert oob_console.get(cs.sid, "someone-else") is None
        assert oob_console.close(cs.sid, "admin") is True
        assert cs.closed
    finally:
        dev.stop()


def test_console_vertiv_connect(cfg):
    dev = FakeVertivACS(ports={"3": {"name": "HCM-SW-03", "target": "HCM-SW-03"}})
    try:
        cfg["telnet_port"] = dev.port
        opt = {"description": "HCM-SW-03", "ip": "127.0.0.1", "port": 3, "protocol": "serial", "vendor": "vertiv"}
        cs = oob_console.open_console(cfg, "admin", "127.0.0.1", "3", opt, "ACS", _creds())
        _wait_for(cs, "connect HCM-SW-03")
        oob_console.close(cs.sid, "admin")
    finally:
        dev.stop()


def test_console_bad_login_reports_error(cfg):
    dev = FakeCiscoOOB(password="other")
    try:
        cfg["telnet_port"] = dev.port
        opt = {"description": "R1", "ip": "10.0.0.1", "port": 2001, "protocol": "telnet", "vendor": "cisco"}
        cs = oob_console.open_console(cfg, "admin", "127.0.0.1", "1", opt, "OOB-1", _creds())
        _wait_for(cs, "Khong dang nhap duoc OOB", timeout=30)
        assert cs.closed
    finally:
        dev.stop()


def test_console_api_requires_login(isolated_cwd):
    import oob_web
    c = oob_web.app.test_client()
    assert c.post("/api/console/open", json={"ip": "1.1.1.1", "key": "1"}).status_code == 401
    assert c.post("/api/console/x/input", json={"d": "a"}).status_code == 401
