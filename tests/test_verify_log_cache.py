"""Test cho WP-F: cache _parse_verify_logs_for_status() (khong doc lai NOI
DUNG file .json khi thu muc verify-logs/ chua doi), WAL mode cho SQLite, va
overlap guard cho 2 vong lap tu dong (run_daemon/run_verify_daemon)."""

import json
import os
import sqlite3
import time

import pytest

import oob_monitor
from oob_monitor import (
    _parse_verify_logs_for_status, _init_db, _try_enter_cycle, _exit_cycle,
)


@pytest.fixture(autouse=True)
def _clear_verify_log_cache():
    # Cache _parse_verify_logs_for_status() dung signature (so file, mtime
    # lon nhat) khong biet ve cwd - an toan trong production (cwd khong doi
    # trong 1 lan chay process that) nhung co the "dung" nham cache tu 1 test
    # khac (thu muc tmp khac) neu 2 file vo tinh co cung mtime (rare nhung co
    # the xay ra do do phan giai mtime cua he dieu hanh). Clear truoc/sau moi
    # test de moi test luon bat dau tu cache rong, khong phu thuoc thu tu chay.
    oob_monitor._verify_log_status_cache.clear()
    yield
    oob_monitor._verify_log_status_cache.clear()


def _write_verify_log(alias, ts_str, results):
    os.makedirs("verify-logs", exist_ok=True)
    fpath = os.path.join("verify-logs", f"Verify_{alias}_{ts_str}.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(results, f)
    return fpath


def test_parse_verify_logs_returns_empty_when_no_dir(isolated_cwd):
    assert _parse_verify_logs_for_status() == {}


def test_parse_verify_logs_reads_file_and_maps_status(isolated_cwd):
    _write_verify_log("TestOOB", "20260101_000000", [
        {"key": "1", "status": "CANH BAO", "act_host": "NEW-HOST"},
        {"key": "2", "status": "OK", "act_host": "OK-HOST"},
    ])
    result = _parse_verify_logs_for_status()
    assert result[("TestOOB", "1")] == {"status": "CANH BAO", "act_host": "NEW-HOST"}
    assert result[("TestOOB", "2")] == {"status": "OK", "act_host": "OK-HOST"}


def test_parse_verify_logs_cache_avoids_reparsing_unchanged_dir(isolated_cwd, monkeypatch):
    _write_verify_log("TestOOB", "20260101_000000", [{"key": "1", "status": "OK", "act_host": "H1"}])

    load_calls = []
    real_json_load = json.load
    def counting_load(f):
        load_calls.append(1)
        return real_json_load(f)
    monkeypatch.setattr(oob_monitor.json, "load", counting_load)

    r1 = _parse_verify_logs_for_status()
    assert len(load_calls) == 1
    r2 = _parse_verify_logs_for_status()  # khong co file moi -> phai dung cache, KHONG doc lai
    assert len(load_calls) == 1
    assert r1 == r2


def test_parse_verify_logs_cache_invalidates_on_new_file(isolated_cwd, monkeypatch):
    _write_verify_log("TestOOB", "20260101_000000", [{"key": "1", "status": "OK", "act_host": "H1"}])
    r1 = _parse_verify_logs_for_status()
    assert r1[("TestOOB", "1")]["status"] == "OK"

    # File .json moi voi mtime chac chan lon hon (gia lap thoi gian troi qua)
    fpath = _write_verify_log("TestOOB", "20260101_000001", [{"key": "1", "status": "CANH BAO", "act_host": "H2"}])
    os.utime(fpath, (time.time() + 10, time.time() + 10))

    r2 = _parse_verify_logs_for_status()
    assert r2[("TestOOB", "1")]["status"] == "CANH BAO"  # phai lay ban MOI NHAT, khong dung cache cu


def test_init_db_enables_wal_mode(isolated_cwd):
    oob_monitor._DB_INIT_CACHE.clear()
    conn = _init_db("test_wal.db", "baseline_menu")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_overlap_guard_blocks_concurrent_cycle():
    state = {"active": False, "started_at": 0.0}
    logs = []
    assert _try_enter_cycle(state, "Scan", logs.append) is True
    assert _try_enter_cycle(state, "Scan", logs.append) is False  # chu ky truoc chua exit
    assert len(logs) == 1
    _exit_cycle(state)
    assert _try_enter_cycle(state, "Scan", logs.append) is True  # sau khi exit thi vao lai duoc


def test_overlap_guard_auto_resets_after_max_age(monkeypatch):
    state = {"active": True, "started_at": time.time() - 999999}  # "treo" tu rat lau
    logs = []
    assert _try_enter_cycle(state, "Verify", logs.append) is True  # tu dong coi la stale, cho vao lai
    assert logs == []


def test_default_config_has_max_workers_keys():
    assert oob_monitor.DEFAULT_CONFIG["scan_max_workers"] == 10
    assert oob_monitor.DEFAULT_CONFIG["verify_max_workers"] == 10
