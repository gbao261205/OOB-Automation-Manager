"""Test cho WP-G: HostLockRegistry thay the action_lock toan cuc. Muc tieu:
Scan/Verify tren CUNG 1 host khong bao gio chay chong (mutual exclusion),
nhung tren KHAC host thi chay THAT SU dong thoi - dung git-thread that (khong
mock), vi day chinh la dieu can kiem chung (hanh vi threading that)."""

import threading
import time

import oob_monitor
from oob_monitor import HostLockRegistry, _thread_verify_only


def test_same_ip_returns_same_lock_object():
    reg = HostLockRegistry()
    l1 = reg.get("10.0.0.1")
    l2 = reg.get("10.0.0.1")
    assert l1 is l2


def test_different_ip_returns_different_lock_object():
    reg = HostLockRegistry()
    l1 = reg.get("10.0.0.1")
    l2 = reg.get("10.0.0.2")
    assert l1 is not l2


def test_registry_get_is_thread_safe_under_concurrent_access():
    """100 thread cung goi .get() dong thoi voi tap IP lap lai - phai khong
    deadlock (moi thread join() trong timeout ngan) va phai hoi tu ve DUNG 1
    lock object cho moi IP (khong tao lock trung do race condition trong
    chinh registry)."""
    reg = HostLockRegistry()
    ips = [f"10.0.0.{i % 10}" for i in range(100)]  # 10 IP khac nhau, lap lai 10 lan
    results = [None] * 100
    errors = []

    def worker(idx):
        try:
            results[idx] = reg.get(ips[idx])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)

    assert not any(t.is_alive() for t in threads)  # khong deadlock
    assert not errors
    # Voi 1 IP, tat ca 10 lan .get() phai tra ve CUNG 1 object
    for i in range(10):
        same_ip_results = [results[j] for j in range(100) if ips[j] == f"10.0.0.{i}"]
        assert all(r is same_ip_results[0] for r in same_ip_results)


def test_same_host_mutual_exclusion_real_threads():
    """2 thread cung xin lock CUNG 1 IP - khong duoc phep vao vung critical
    section cung luc (mo phong: khong duoc co 2 phien SSH/Telnet dong thoi
    toi cung 1 thiet bi)."""
    reg = HostLockRegistry()
    overlap_detected = []
    active = {"count": 0}
    state_lock = threading.Lock()

    def worker():
        with reg.get("10.0.0.5"):
            with state_lock:
                active["count"] += 1
                if active["count"] > 1: overlap_detected.append(True)
            time.sleep(0.05)
            with state_lock:
                active["count"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)

    assert not overlap_detected


def test_different_host_runs_concurrently_real_threads():
    """Scan(host A) va Verify(host B) phai chay DONG THOI THAT SU - day chinh
    la muc tieu cua WP-G (khac voi action_lock toan cuc truoc day, buoc
    TOAN BO Scan cho TOAN BO Verify xong)."""
    reg = HostLockRegistry()
    both_active_at_once = threading.Event()
    barrier = threading.Barrier(2, timeout=5)

    def worker(ip):
        with reg.get(ip):
            barrier.wait()  # ca 2 thread phai cung o trong critical section tai day
            both_active_at_once.set()
            time.sleep(0.02)

    t1 = threading.Thread(target=worker, args=("10.0.0.1",))
    t2 = threading.Thread(target=worker, args=("10.0.0.2",))
    t1.start(); t2.start()
    t1.join(timeout=5); t2.join(timeout=5)

    assert both_active_at_once.is_set()  # ca 2 cung vao duoc critical section -> that su song song


def test_thread_verify_only_holds_host_lock_during_run_deep_verify(monkeypatch):
    """Xac nhan wiring THAT: _thread_verify_only() phai giu host_lock_registry
    cho dung IP trong suot luc goi run_deep_verify() - khong phai chi co
    HostLockRegistry ton tai ma khong duoc dung o dau ca."""
    ip = "10.0.0.7"
    lock_was_held = []

    def fake_run_deep_verify(cfg, alias, oob_ip, snapshot, print_fn=None):
        # Lock cho IP nay dang bi _thread_verify_only giu - .acquire(blocking=False)
        # phai THAT BAI (tra ve False) neu dang bi giu boi thread hien tai.
        acquired = oob_monitor.host_lock_registry.get(ip).acquire(blocking=False)
        lock_was_held.append(not acquired)
        if acquired:
            oob_monitor.host_lock_registry.get(ip).release()

    monkeypatch.setattr(oob_monitor, "run_deep_verify", fake_run_deep_verify)
    _thread_verify_only({}, "TestAlias", ip, {})

    assert lock_was_held == [True]
