"""Test cho WP-H #15: update_ip() sua alias/IP tai dung vi tri dong trong
file danh sach IP (khac remove_ip()+add_ip() se day dong xuong cuoi file)."""

import os

from oob_monitor import update_ip, load_ip_list, add_ip


def _seed_ip_list(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for ip, alias in entries:
            f.write(f"{ip} {alias}\n")


def test_update_ip_changes_alias_keeps_position(isolated_cwd):
    path = "oob_ips.txt"
    _seed_ip_list(path, [("10.0.0.1", "First"), ("10.0.0.2", "Second"), ("10.0.0.3", "Third")])

    ok, msg = update_ip(path, "10.0.0.2", new_alias="SecondRenamed")
    assert ok is True

    hosts = load_ip_list(path)
    assert hosts == [("10.0.0.1", "First"), ("10.0.0.2", "SecondRenamed"), ("10.0.0.3", "Third")]


def test_update_ip_changes_ip_keeps_position(isolated_cwd):
    path = "oob_ips.txt"
    _seed_ip_list(path, [("10.0.0.1", "First"), ("10.0.0.2", "Second"), ("10.0.0.3", "Third")])

    ok, msg = update_ip(path, "10.0.0.2", new_ip="10.0.0.99")
    assert ok is True

    hosts = load_ip_list(path)
    assert hosts == [("10.0.0.1", "First"), ("10.0.0.99", "Second"), ("10.0.0.3", "Third")]


def test_update_ip_changes_both_alias_and_ip(isolated_cwd):
    path = "oob_ips.txt"
    _seed_ip_list(path, [("10.0.0.1", "First")])

    ok, msg = update_ip(path, "10.0.0.1", new_alias="Renamed", new_ip="10.0.0.50")
    assert ok is True
    assert load_ip_list(path) == [("10.0.0.50", "Renamed")]


def test_update_ip_missing_old_ip_fails(isolated_cwd):
    path = "oob_ips.txt"
    _seed_ip_list(path, [("10.0.0.1", "First")])

    ok, msg = update_ip(path, "10.0.0.99", new_alias="X")
    assert ok is False
    assert load_ip_list(path) == [("10.0.0.1", "First")]  # khong doi gi


def test_update_ip_rejects_duplicate_target_ip(isolated_cwd):
    path = "oob_ips.txt"
    _seed_ip_list(path, [("10.0.0.1", "First"), ("10.0.0.2", "Second")])

    ok, msg = update_ip(path, "10.0.0.1", new_ip="10.0.0.2")
    assert ok is False
    assert load_ip_list(path) == [("10.0.0.1", "First"), ("10.0.0.2", "Second")]  # khong doi gi


def test_update_ip_no_change_when_only_ip_same_as_old(isolated_cwd):
    path = "oob_ips.txt"
    _seed_ip_list(path, [("10.0.0.1", "First")])

    ok, msg = update_ip(path, "10.0.0.1", new_alias="Renamed", new_ip="10.0.0.1")
    assert ok is True
    assert load_ip_list(path) == [("10.0.0.1", "Renamed")]
