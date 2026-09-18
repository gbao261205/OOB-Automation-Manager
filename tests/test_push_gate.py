"""Test cho WP-B: push_live_mode phai chan Push/Revert that su gui lenh toi
thiet bi tru khi nguoi dung CHU DONG bat. Mac dinh (push_live_mode=False)
hanh vi phai giong het truoc day (mo phong, khong gui gi that, Baseline
khong bi cap nhat sai su that)."""

import os
import re

import pytest

import oob_monitor
from oob_monitor import process_push_and_reverify, save_options, get_options_by_host


def _seed_baseline(base_cfg, oob_ip, description="----> OLD-HOST"):
    """Tao 1 baseline co san trong DB tam, gom 1 option se duoc coi la
    CANH BAO khi so voi ket qua verify."""
    oob_monitor._DB_INIT_CACHE.clear()  # tranh cache _init_db dinh tu test khac (path trung ten)
    options = {
        "1": {"description": description, "ip": oob_ip, "port": 23, "protocol": "telnet",
              "_raw_key": "1", "_menu_name": "MAINMENU", "vendor": "cisco"},
    }
    save_options(base_cfg["baseline_db"], "baseline_menu", oob_ip, "MAINMENU", "OOB-SELF", options)
    return options


def _verify_results():
    return [{"key": "1", "status": "CANH BAO", "act_host": "NEW-HOST", "desc": "----> OLD-HOST", "port": 23, "note": ""}]


@pytest.fixture(autouse=True)
def _clear_db_cache():
    oob_monitor._DB_INIT_CACHE.clear()
    yield
    oob_monitor._DB_INIT_CACHE.clear()


def test_default_push_live_mode_off_is_simulated_and_does_not_touch_baseline(base_cfg, isolated_cwd, monkeypatch):
    assert base_cfg["push_live_mode"] is False  # xac nhan default an toan

    push_calls = []
    def fake_push(*args, **kwargs):
        push_calls.append(kwargs)
        return True  # thiet bi "chap nhan" - nhung day la MO PHONG
    monkeypatch.setattr(oob_monitor, "push_menu_descriptions", fake_push)

    verify_called = []
    monkeypatch.setattr(oob_monitor, "run_deep_verify", lambda *a, **k: verify_called.append(1))

    baseline = _seed_baseline(base_cfg, "10.0.0.9")
    process_push_and_reverify(base_cfg, "TestOOB", "10.0.0.9", baseline, _verify_results(), print_fn=lambda *a, **k: None)

    assert push_calls[0]["dry_run"] is True
    # Baseline trong DB PHAI giu nguyen (khong duoc tu nhan la da sua xong)
    _, _, reloaded = get_options_by_host(base_cfg["baseline_db"], "baseline_menu", "10.0.0.9")
    assert reloaded["1"]["description"] == "----> OLD-HOST"
    # Mo phong thi KHONG duoc tu dong re-verify (khong co gi that de kiem tra lai)
    assert verify_called == []

    # Push-log phai ghi ro la MO PHONG
    log_files = os.listdir("push-logs")
    assert len(log_files) == 1
    with open(os.path.join("push-logs", log_files[0]), encoding="utf-8") as f:
        content = f.read()
    assert "[MO PHONG]" in content
    assert "REVERT CMD: menu MAINMENU text 1 ----> OLD-HOST" in content


def test_push_live_mode_on_sends_real_and_updates_baseline(base_cfg, isolated_cwd, monkeypatch):
    base_cfg["push_live_mode"] = True

    push_calls = []
    def fake_push(*args, **kwargs):
        push_calls.append(kwargs)
        return True
    monkeypatch.setattr(oob_monitor, "push_menu_descriptions", fake_push)

    verify_called = []
    monkeypatch.setattr(oob_monitor, "run_deep_verify", lambda *a, **k: verify_called.append(1))

    baseline = _seed_baseline(base_cfg, "10.0.0.9")
    process_push_and_reverify(base_cfg, "TestOOB", "10.0.0.9", baseline, _verify_results(), print_fn=lambda *a, **k: None)

    assert push_calls[0]["dry_run"] is False
    _, _, reloaded = get_options_by_host(base_cfg["baseline_db"], "baseline_menu", "10.0.0.9")
    assert reloaded["1"]["description"] == "----> NEW-HOST"
    assert verify_called == [1]  # that thi phai tu dong re-verify

    log_files = os.listdir("push-logs")
    with open(os.path.join("push-logs", log_files[0]), encoding="utf-8") as f:
        content = f.read()
    assert "[THAT]" in content


def test_push_device_rejects_command_baseline_not_updated(base_cfg, isolated_cwd, monkeypatch):
    base_cfg["push_live_mode"] = True
    monkeypatch.setattr(oob_monitor, "push_menu_descriptions", lambda *a, **k: False)  # thiet bi tu choi
    verify_called = []
    monkeypatch.setattr(oob_monitor, "run_deep_verify", lambda *a, **k: verify_called.append(1))

    baseline = _seed_baseline(base_cfg, "10.0.0.9")
    process_push_and_reverify(base_cfg, "TestOOB", "10.0.0.9", baseline, _verify_results(), print_fn=lambda *a, **k: None)

    _, _, reloaded = get_options_by_host(base_cfg["baseline_db"], "baseline_menu", "10.0.0.9")
    assert reloaded["1"]["description"] == "----> OLD-HOST"
    assert verify_called == []
    assert not os.path.isdir("push-logs") or os.listdir("push-logs") == []
