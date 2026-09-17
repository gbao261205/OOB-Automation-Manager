"""Test mock cho race condition da xac dinh trong run_deep_verify() /
check_port_via_oob() nhanh Cisco/Telnet/SSH truc tiep (WP-A).

Bug goc: code cu goi _write("") x2 (map toi session.write(), LUON drain/xoa
buffer dang cho truoc khi gui) NGAY TRUOC lan doc dau tien - neu du lieu that
(banner/prompt) da ve dung vao luc dang sleep() cho ket noi, no bi xoa mat
truoc khi kip doc, gay TIMEOUT gia du thiet bi van phan hoi binh thuong.

FakeSession mo phong dung co che nay: write() xoa buffer dang cho,
write_no_drain() thi khong. time.sleep() duoc mock de "bom" du lieu vao dung
thoi diem tuong ung voi luc du lieu that su ve tren day mang thuc te."""

import oob_monitor
from oob_monitor import run_deep_verify


class FakeSession:
    def __init__(self):
        self.buffer = ""
        self.calls = []

    def write(self, text):
        self.calls.append(("write", text))
        self.buffer = ""  # giong MiniSSH.write(): luon drain truoc khi gui

    def write_no_drain(self, text):
        self.calls.append(("write_no_drain", text))
        # KHONG drain - day la diem khac biet then chot voi write()

    def write_cr(self, text):
        self.write(text)

    def write_raw(self, b_text):
        self.calls.append(("write_raw", b_text))

    def read_until(self, match_list, timeout=5):
        data = self.buffer
        self.buffer = ""
        return data

    def close(self):
        pass


def _base_cfg(base_cfg):
    base_cfg["username"] = "admin"
    base_cfg["password"] = "adminpass"
    base_cfg["enable_password"] = ""
    base_cfg["max_verify_duration"] = 300
    base_cfg["verify_wait_after_connect_telnet"] = 1.5
    base_cfg["verify_wait_after_connect_ssh"] = 3.0
    return base_cfg


def _setup_common_mocks(monkeypatch, sessions_created):
    def fake_connect_auto(*args, **kwargs):
        s = FakeSession()
        sessions_created.append(s)
        return s

    monkeypatch.setattr(oob_monitor, "connect_auto", fake_connect_auto)
    monkeypatch.setattr(oob_monitor, "fetch_hostname", lambda tn: "OOB-SELF-HOST")


def test_data_arriving_during_connect_wait_is_not_lost(base_cfg, isolated_cwd, monkeypatch):
    """Characterization cua FIX: du lieu 've dung luc' trong khoang sleep()
    cho ket noi phai duoc doc thanh cong, KHONG bi drain mat (bug cu se lam
    mat du lieu nay va tra ve TIMEOUT gia)."""
    cfg = _base_cfg(base_cfg)
    sessions_created = []
    _setup_common_mocks(monkeypatch, sessions_created)

    sleep_calls = []
    def fake_sleep(duration):
        sleep_calls.append(duration)
        if len(sleep_calls) == 1:
            # Mo phong dung khoanh khac du lieu that "ve" trong khi dang cho
            # ket noi (sleep dau tien sau khi gui lenh telnet/ssh).
            sessions_created[-1].buffer = "HCM-TARGET-01#"
    monkeypatch.setattr(oob_monitor.time, "sleep", fake_sleep)

    options = {"1": {"description": "HCM-TARGET-01", "ip": "10.0.0.5", "port": 23, "protocol": "telnet", "vendor": "cisco"}}
    results = run_deep_verify(cfg, "TestOOB", "10.0.0.9", options, print_fn=lambda *a, **k: None)

    assert len(results) == 1
    assert results[0]["status"] == "OK"
    assert results[0]["act_host"] == "HCM-TARGET-01"
    assert len(sleep_calls) == 1  # khong can retry vi da co data ngay lan doc dau


def test_empty_first_read_triggers_conditional_retry(base_cfg, isolated_cwd, monkeypatch):
    """Neu lan doc dau tien RONG (thiet bi phan hoi cham hon du kien), code
    phai tu dong 'danh thuc' bang write_no_drain() (khong xoa buffer) va thu
    doc lai lan 2 voi timeout dai hon - truoc day KHONG co co che retry nay,
    1 lan rong la TIMEOUT vinh vien trong ca chu ky."""
    cfg = _base_cfg(base_cfg)
    sessions_created = []
    _setup_common_mocks(monkeypatch, sessions_created)

    sleep_calls = []
    def fake_sleep(duration):
        sleep_calls.append(duration)
        if len(sleep_calls) == 2:
            # Data chi ve TRE, sau lan doc dau tien (rong) - trong luc dang
            # "danh thuc" o lan retry.
            sessions_created[-1].buffer = "HCM-TARGET-02#"
    monkeypatch.setattr(oob_monitor.time, "sleep", fake_sleep)

    options = {"1": {"description": "HCM-TARGET-02", "ip": "10.0.0.6", "port": 23, "protocol": "telnet", "vendor": "cisco"}}
    results = run_deep_verify(cfg, "TestOOB", "10.0.0.9", options, print_fn=lambda *a, **k: None)

    assert len(results) == 1
    assert results[0]["status"] == "OK"
    assert results[0]["act_host"] == "HCM-TARGET-02"
    assert len(sleep_calls) >= 2  # co xay ra retry (sleep 0.3s giua 2 lan write_no_drain)


def test_still_timeout_when_device_truly_never_responds(base_cfg, isolated_cwd, monkeypatch):
    """Thiet bi that su khong phan hoi (buffer luon rong) -> van phai la
    TIMEOUT nhu cu sau khi da thu retry, khong duoc bao OK sai."""
    cfg = _base_cfg(base_cfg)
    sessions_created = []
    _setup_common_mocks(monkeypatch, sessions_created)
    monkeypatch.setattr(oob_monitor.time, "sleep", lambda duration: None)

    options = {"1": {"description": "HCM-TARGET-03", "ip": "10.0.0.7", "port": 23, "protocol": "telnet", "vendor": "cisco"}}
    results = run_deep_verify(cfg, "TestOOB", "10.0.0.9", options, print_fn=lambda *a, **k: None)

    assert len(results) == 1
    assert results[0]["status"] == "TIMEOUT"


def test_ssh_and_telnet_use_separate_wait_config(base_cfg, isolated_cwd, monkeypatch):
    """verify_wait_after_connect_ssh phai duoc dung cho proto=ssh,
    verify_wait_after_connect_telnet cho proto=telnet - khong con dung chung
    1 gia tri nhu truoc."""
    cfg = _base_cfg(base_cfg)
    cfg["verify_wait_after_connect_telnet"] = 1.5
    cfg["verify_wait_after_connect_ssh"] = 9.0
    sessions_created = []
    _setup_common_mocks(monkeypatch, sessions_created)

    waits_seen = []
    def fake_sleep(duration):
        waits_seen.append(duration)
    monkeypatch.setattr(oob_monitor.time, "sleep", fake_sleep)

    options = {"1": {"description": "HCM-TARGET-04", "ip": "10.0.0.8", "port": 22, "protocol": "ssh", "vendor": "cisco"}}
    run_deep_verify(cfg, "TestOOB", "10.0.0.9", options, print_fn=lambda *a, **k: None)

    assert waits_seen[0] == 9.0
