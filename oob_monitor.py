#!/usr/bin/env python3
"""
oob_monitor.py

Cong cu giam sat (READ-ONLY) menu OOB tren nhieu thiet bi (R0, R0-2, ...).
Chi doc du lieu tu thiet bi de doi chieu voi baseline; KHONG sua/day cau hinh
nguoc lai thiet bi trong bat ky truong hop nao.

Chi can chay:
    python3 oob_monitor.py

Khong can truyen tham so dong lenh nao. Toan bo cau hinh (username,
password, enable password, ten menu ep dung (neu co), chu ky thu thap,
duong dan file danh sach IP / database ...) duoc luu trong 1 file JSON
(mac dinh "oob_config.json") va co the chinh sua ngay trong menu (lua chon 4).

Man hinh chinh:
    1. Them IP thiet bi OOB vao danh sach
    2. Xoa IP thiet bi OOB khoi danh sach
    3. Bat dau thu thap lien tuc + so sanh baseline
    4. Cau hinh (username / password / enable password / interval / ...)
    5. Xem danh sach thiet bi (alias, IP, hostname thiet bi, trang thai baseline)
    6. Xem baseline (cau hinh chuan da xac nhan cho tung thiet bi)
    7. Tim kiem thiet bi theo IP hoac ten -> biet no dang duoc OOB nao quan ly
    q. Thoat

Vong thu thap (lua chon 3): moi N giay (mac dinh 30s), voi tung IP trong
danh sach: dang nhap, tu dong do ten menu + lay cau hinh menu bang
"show running-config | include menu" (chi doc), luu vao "snapshot.db"
(db thu 2), roi so sanh voi "baseline.db" (db thu 1 - chuan):
    - Chua co baseline cho thiet bi  -> dung lai, hien du lieu vua lay,
      hoi admin co xac nhan lam CHUAN khong.
    - Snapshot == baseline            -> bao KHOP chuan, sang thiet bi tiep theo.
    - Snapshot != baseline            -> CANH BAO chi tiet (option nao bi
      them la, bi mat, hay bi doi noi dung) va hoi admin co muon cap nhat
      baseline theo trang thai hien tai khong. Neu khong, chi ghi nhan canh
      bao va giu nguyen baseline cu - KHONG sua gi tren thiet bi.
"""

import getpass
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime

from rich import box as rbox
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from oob_lib import poll_host

CONFIG_FILE_DEFAULT = "oob_config.json"

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "enable_password": "",
    # Rong ("") -> TU DONG DO ten menu tren tung thiet bi bang lenh
    # 'show running-config | include menu'. Chi dat gia tri o day neu muon EP
    # dung 1 ten menu cu the cho MOI thiet bi (bo qua ten tu dong do duoc).
    "menu_name_override": "",
    "ssh_port": 22,
    "telnet_port": 23,
    "interval": 30,
    "ip_list": "oob_ips.txt",
    "baseline_db": "baseline.db",
    "snapshot_db": "snapshot.db",
}

# ---------------------------------------------------------------------------
# Rich console & thread state
# ---------------------------------------------------------------------------

_con = Console(highlight=False)
_monitor_thread: "threading.Thread | None" = None
_stop_event = threading.Event()
_print_lock = threading.RLock()


def _mprint(msg: str = "", **kwargs):
    """Thread-safe timestamped print dung trong monitor thread."""
    ts = datetime.now().strftime("%H:%M:%S")
    with _print_lock:
        _con.print(f"[dim]\\[{ts}][/] {msg}", **kwargs)


def _is_monitoring() -> bool:
    return _monitor_thread is not None and _monitor_thread.is_alive()


# ---------------------------------------------------------------------------
# Cau hinh (luu trong file JSON, chinh sua qua menu)
# ---------------------------------------------------------------------------

def load_config(path):
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[!] Khong doc duoc {path} ({exc}), dung cau hinh mac dinh.")
    return cfg


def save_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def mask(value):
    return "******" if value else "(chua dat)"


def settings_menu(cfg, config_path):
    while True:
        print(f"""
--------------------------------------------------
        CAU HINH HIEN TAI
--------------------------------------------------
1. Username           : {cfg['username'] or '(khong dung)'}
2. Password            : {mask(cfg['password'])}
3. Enable password     : {mask(cfg['enable_password'])}
4. Ten menu (rong=tu dong do): {cfg['menu_name_override'] or '(tu dong do)'}
5. SSH port (uu tien)   : {cfg.get('ssh_port', 22)}
6. Telnet port (du phong): {cfg['telnet_port']}
7. Chu ky thu thap (s)  : {cfg['interval']}
8. File danh sach IP    : {cfg['ip_list']}
9. File baseline DB     : {cfg['baseline_db']}
a. File snapshot DB     : {cfg['snapshot_db']}
0. Quay lai menu chinh
--------------------------------------------------""")
        choice = input("Chon muc can sua: ").strip()

        if choice == "1":
            cfg["username"] = input("  Username moi (Enter de bo trong): ").strip()
        elif choice == "2":
            cfg["password"] = getpass.getpass("  Password moi (khong hien khi go): ").strip()
        elif choice == "3":
            cfg["enable_password"] = getpass.getpass("  Enable password moi (khong hien khi go): ").strip()
        elif choice == "4":
            cur = cfg['menu_name_override'] or '(tu dong do)'
            val = input(
                f"  Ten menu ep dung, de trong = tu dong do (hien tai: {cur}): "
            ).strip()
            cfg["menu_name_override"] = val  # rong -> ve lai che do tu dong do
        elif choice == "5":
            val = input(f"  SSH port moi (hien tai: {cfg.get('ssh_port', 22)}): ").strip()
            if val.isdigit():
                cfg["ssh_port"] = int(val)
        elif choice == "6":
            val = input(f"  Telnet port moi (hien tai: {cfg['telnet_port']}): ").strip()
            if val.isdigit():
                cfg["telnet_port"] = int(val)
        elif choice == "7":
            val = input(f"  Chu ky thu thap moi, giay (hien tai: {cfg['interval']}): ").strip()
            if val.isdigit():
                cfg["interval"] = int(val)
        elif choice == "8":
            val = input(f"  File danh sach IP moi (hien tai: {cfg['ip_list']}): ").strip()
            if val:
                cfg["ip_list"] = val
        elif choice == "9":
            val = input(f"  File baseline DB moi (hien tai: {cfg['baseline_db']}): ").strip()
            if val:
                cfg["baseline_db"] = val
        elif choice == "a":
            val = input(f"  File snapshot DB moi (hien tai: {cfg['snapshot_db']}): ").strip()
            if val:
                cfg["snapshot_db"] = val
        elif choice == "0":
            save_config(config_path, cfg)
            print(f"[*] Da luu cau hinh vao {config_path}")
            return
        else:
            print("[!] Lua chon khong hop le.")
            continue

        save_config(config_path, cfg)


# ---------------------------------------------------------------------------
# Quan ly danh sach IP (text file), moi dong: "<ip> [alias]"
# ---------------------------------------------------------------------------

def load_ip_list(path):
    hosts = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                ip = parts[0]
                alias = parts[1] if len(parts) > 1 else ip
                hosts.append((ip, alias))
    except FileNotFoundError:
        pass
    return hosts


def add_ip(path, ip, alias=None):
    hosts = load_ip_list(path)
    if any(h[0] == ip for h in hosts):
        print(f"[!] IP {ip} da co trong danh sach.")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{ip} {alias or ip}\n")
    print(f"[*] Da them {ip} ({alias or ip}) vao {path}")


def remove_ip(path, ip):
    hosts = load_ip_list(path)
    remaining = [h for h in hosts if h[0] != ip]
    if len(remaining) == len(hosts):
        print(f"[!] Khong tim thay IP {ip} trong danh sach.")
        return
    with open(path, "w", encoding="utf-8") as f:
        for h_ip, alias in remaining:
            f.write(f"{h_ip} {alias}\n")
    print(f"[*] Da xoa {ip} khoi {path}")


# ---------------------------------------------------------------------------
# Baseline DB (chuan) va Snapshot DB (du lieu thu thap moi vong)
# ---------------------------------------------------------------------------

def _init_db(path, table):
    conn = sqlite3.connect(path)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            host TEXT NOT NULL,
            menu_name TEXT NOT NULL,
            option_key TEXT NOT NULL,
            device_name TEXT,
            description TEXT,
            target_ip TEXT NOT NULL,
            target_port INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (host, menu_name, option_key)
        )
    """)
    # Migrate DB cu (tao truoc khi co cot device_name)
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if "device_name" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN device_name TEXT")
    conn.commit()
    return conn


def get_options(db_path, table, host, menu_name):
    """Tra ve (device_name, options_dict). Ca hai la None neu chua co du lieu."""
    conn = _init_db(db_path, table)
    cur = conn.execute(
        f"SELECT option_key, device_name, description, target_ip, target_port FROM {table} "
        f"WHERE host=? AND menu_name=?",
        (host, menu_name),
    )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return None, None
    device_name = rows[0][1]
    options = {
        key: {"description": desc, "ip": ip, "port": port}
        for key, _dn, desc, ip, port in rows
    }
    return device_name, options


def get_options_by_host(db_path, table, host):
    """Giong get_options() nhung KHONG can biet truoc menu_name - vi menu_name gio
    duoc TU DONG DO rieng cho tung thiet bi (co the khac nhau giua cac thiet bi).
    Tra ve (menu_name, device_name, options_dict). Ca ba la None neu chua co du lieu."""
    conn = _init_db(db_path, table)
    cur = conn.execute(
        f"SELECT menu_name, option_key, device_name, description, target_ip, target_port "
        f"FROM {table} WHERE host=?",
        (host,),
    )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return None, None, None
    menu_name   = rows[0][0]
    device_name = rows[0][2]
    options = {
        key: {"description": desc, "ip": ip, "port": port}
        for _mn, key, _dn, desc, ip, port in rows
    }
    return menu_name, device_name, options


def get_updated_at_by_host(db_path, table, host):
    """Giong get_updated_at() nhung khong can biet truoc menu_name."""
    conn = _init_db(db_path, table)
    cur = conn.execute(f"SELECT MAX(updated_at) FROM {table} WHERE host=?", (host,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_updated_at(db_path, table, host, menu_name):
    conn = _init_db(db_path, table)
    cur = conn.execute(
        f"SELECT MAX(updated_at) FROM {table} WHERE host=? AND menu_name=?",
        (host, menu_name),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def save_options(db_path, table, host, menu_name, device_name, options):
    conn = _init_db(db_path, table)
    conn.execute(f"DELETE FROM {table} WHERE host=? AND menu_name=?", (host, menu_name))
    now = datetime.now().isoformat(timespec="seconds")
    for key, entry in options.items():
        conn.execute(
            f"INSERT INTO {table} (host, menu_name, option_key, device_name, description, "
            f"target_ip, target_port, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (host, menu_name, key, device_name, entry.get("description", ""), entry["ip"],
             entry.get("port", 23), now),
        )
    conn.commit()
    conn.close()


def _norm_proto(p: str | None) -> str:
    """Chuan hoa protocol: None hoac 'telnet' deu la 'telnet' (tuong thich baseline cu)."""
    return p or "telnet"


def options_equal(a: dict, b: dict) -> bool:
    if set(a.keys()) != set(b.keys()):
        return False
    for key in a:
        if a[key].get("ip")          != b[key].get("ip"):                         return False
        if a[key].get("port")        != b[key].get("port"):                        return False
        if _norm_proto(a[key].get("protocol")) != _norm_proto(b[key].get("protocol")): return False
        if a[key].get("description") != b[key].get("description"):                return False
    return True


def diff_options(baseline: dict, snapshot: dict) -> dict:
    """So sanh chi tiet: option nao bi thieu, thua, hoac thay doi noi dung."""
    extra   = sorted(set(snapshot) - set(baseline))
    missing = sorted(set(baseline) - set(snapshot))
    changed = []
    for key in sorted(set(baseline) & set(snapshot)):
        b, s = baseline[key], snapshot[key]
        if (b.get("ip")       != s.get("ip")
                or b.get("port")                         != s.get("port")
                or _norm_proto(b.get("protocol"))        != _norm_proto(s.get("protocol"))
                or b.get("description")                  != s.get("description")):
            changed.append(key)
    return {"extra": extra, "missing": missing, "changed": changed}


def _fmt_entry(e: dict) -> str:
    proto = _norm_proto(e.get("protocol"))
    port  = e.get("port", 22 if proto == "ssh" else 23)
    return f"{proto}://{e.get('ip','')}:{port}"


def print_diff(baseline: dict, snapshot: dict):
    d = diff_options(baseline, snapshot)
    if d["extra"]:
        _con.print(f"    [red]+ Option la (them tren thiet bi): {', '.join(d['extra'])}[/]")
    if d["missing"]:
        _con.print(f"    [red]- Option bi mat: {', '.join(d['missing'])}[/]")
    if d["changed"]:
        _con.print(f"    [yellow]~ Option bi doi noi dung: {', '.join(d['changed'])}[/]")
        t = Table(box=rbox.SIMPLE_HEAD, header_style="bold dim", show_header=True)
        t.add_column("Option",  style="bold cyan", min_width=6)
        t.add_column("Baseline (chuan)",            min_width=36)
        t.add_column("Hien tai tren thiet bi",       min_width=36)
        for key in d["changed"]:
            b, s = baseline[key], snapshot[key]
            t.add_row(
                f"[{key}]",
                f"{b.get('description','')}  [dim]{_fmt_entry(b)}[/]",
                f"{s.get('description','')}  [dim]{_fmt_entry(s)}[/]",
            )
        _con.print(t)


def print_options(options: dict):
    t = Table(box=rbox.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column(style="bold cyan", justify="right", min_width=4)
    t.add_column(min_width=20)
    t.add_column(style="dim")
    for key in sorted(options):
        e     = options[key]
        proto = _norm_proto(e.get("protocol"))
        port  = e.get("port", 22 if proto == "ssh" else 23)
        col   = "green" if proto == "ssh" else "yellow"
        t.add_row(f"[{key}]", e.get("description", ""), f"[{col}]{proto}[/]://{e['ip']}:{port}")
    _con.print(t)


# ---------------------------------------------------------------------------
# Vong lap thu thap + so sanh + xu ly
# ---------------------------------------------------------------------------

# Chuc nang kiem tra hostname tung option da duoc tach sang oob_verify.py
# (tam thoi khong hoat dong, cho den khi implement invoke_menu_and_check)
# from oob_verify import check_option_hostnames


def monitor_loop(cfg, stop_event: threading.Event):
    """Vong lap thu thap chay trong background thread.
    stop_event.set() de dung vong lap mot cach an toan."""
    if not cfg.get("password"):
        _mprint("[yellow][!] Chua cau hinh password. Vao 'Cau hinh' truoc khi giam sat.[/]")
        return

    _mprint(f"[green][START][/] Bat dau giam sat moi {cfg['interval']}s.")
    while not stop_event.is_set():
        hosts = load_ip_list(cfg["ip_list"])
        if not hosts:
            _mprint("[yellow][!] Danh sach IP trong. Them thiet bi truoc (option 1).[/]")
            stop_event.wait(timeout=cfg["interval"])
            continue

        for ip, alias in hosts:
            if stop_event.is_set():
                break

            _mprint(f"[cyan][SCAN][/] [bold]{alias}[/] ({ip}) ...")
            try:
                hostname, menu_name, snapshot = poll_host(
                    ip, cfg["telnet_port"], cfg["username"], cfg["password"],
                    cfg["enable_password"],
                    menu_name=cfg.get("menu_name_override") or None,  # rong -> tu dong do
                    ssh_port=cfg.get("ssh_port", 22),
                )
            except Exception as exc:
                _mprint(f"  [red][LOI][/] {alias} ({ip}): {exc}")
                continue

            hn_label = f"hostname=[bold]{hostname}[/]" if hostname else "hostname=?"
            menu_n   = len(snapshot)

            if not menu_name:
                _mprint(f"  [yellow][!][/] {alias}: Khong tim thay cau hinh menu nao tren thiet bi "
                        f"('show running-config | include menu' rong).")
                continue

            if not snapshot:
                _mprint(f"  [yellow][!][/] {alias}: Da tim thay menu '{menu_name}' nhung khong parse duoc option nao.")
                continue

            _mprint(f"  [dim]Ten menu tu dong do duoc: '{menu_name}'.[/]")
            save_options(cfg["snapshot_db"], "snapshot_menu", ip, menu_name, hostname, snapshot)
            _baseline_name, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)

            if baseline is None:
                with _print_lock:
                    _con.print(f"\n  [yellow][?][/] Chua co baseline cho [bold]{alias}[/] ({ip}).")
                    _con.print(f"      {hn_label} | {menu_n} option:")
                    print_options(snapshot)
                    ans = _con.input(f"  Xac nhan day la CHUAN cho {alias}? [y/N]: ").strip().lower()
                if ans == "y":
                    save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
                    _mprint(f"  [green][OK][/] Da luu baseline cho {alias}.")
                else:
                    _mprint("  [dim][--] Bo qua, se hoi lai o chu ky sau.[/]")
                continue

            if options_equal(baseline, snapshot):
                _mprint(f"  [green][OK][/] {alias}: Khop voi baseline ({menu_n} option).")
                # [VERIFY - TAM THOI TAT] check_option_hostnames(alias, snapshot, cfg)
                continue

            # Phat hien thay doi -> chi canh bao va bao cao, KHONG tu dong sua thiet bi
            with _print_lock:
                _con.rule(f"[bold red]CANH BAO  {alias} ({ip}) KHAC baseline![/]", style="red")
                print_diff(baseline, snapshot)
                _con.print("  [dim]--- Baseline (chuan) ---[/]")
                print_options(baseline)
                _con.print("  [yellow]--- Hien tai tren thiet bi ---[/]")
                print_options(snapshot)
                ans = _con.input(
                    f"  Cap nhat baseline theo trang thai hien tai cua {alias}? [y/N]: "
                ).strip().lower()

            if ans == "y":
                save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
                _mprint(f"  [green][OK][/] Da cap nhat baseline moi cho {alias}.")
            else:
                _mprint(f"  [yellow][!][/] Giu nguyen baseline cu cho {alias}. "
                        f"Thiet bi VAN DANG khac chuan - se canh bao lai o chu ky sau.")

        if not stop_event.is_set():
            _mprint(f"[dim][zzz] Cho {cfg['interval']}s ...[/]")
            stop_event.wait(timeout=cfg["interval"])

    _mprint("[yellow][STOP][/] Giam sat da dung.")


# ---------------------------------------------------------------------------
# Quan ly background thread
# ---------------------------------------------------------------------------

def start_monitor_bg(cfg):
    """Khoi dong monitor_loop trong background thread. Tra ve False neu da chay."""
    global _monitor_thread, _stop_event
    if _is_monitoring():
        return False
    _stop_event = threading.Event()
    _monitor_thread = threading.Thread(
        target=monitor_loop,
        args=(cfg, _stop_event),
        daemon=True,
        name="OOB-Monitor",
    )
    _monitor_thread.start()
    return True


def stop_monitor_bg():
    """Dung background thread. Tra ve False neu chua chay."""
    global _monitor_thread
    if not _is_monitoring():
        return False
    _stop_event.set()
    _monitor_thread.join(timeout=5)
    _monitor_thread = None
    return True


def list_devices(cfg):
    hosts = load_ip_list(cfg["ip_list"])
    if not hosts:
        _con.print(f"\n  [yellow][!][/] Danh sach trong. Hay them IP truoc (option 1).")
        return

    table = Table(
        title="[bold]Danh Sach Thiet Bi OOB[/]",
        box=rbox.ROUNDED, border_style="cyan",
        header_style="bold cyan", show_lines=False,
    )
    table.add_column("Alias",        style="bold cyan", min_width=12)
    table.add_column("IP",                              min_width=16)
    table.add_column("Hostname",                        min_width=14)
    table.add_column("Baseline",     justify="center",  min_width=9)
    table.add_column("Cap nhat luc",                    min_width=20)

    for ip, alias in hosts:
        _mn, device_name, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        updated_at = get_updated_at_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if device_name is None:
            _mn, device_name, _ = get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
        bl = "[green]✓ Co[/]" if baseline else "[yellow]✗ Chua[/]"
        table.add_row(
            alias, ip,
            device_name or "[dim](chua ro)[/]",
            bl, updated_at or "[dim]-[/]",
        )

    _con.print()
    _con.print(table)


def view_baseline(cfg):
    hosts = load_ip_list(cfg["ip_list"])
    if not hosts:
        _con.print("[yellow][!][/] Danh sach trong. Hay them IP truoc (option 1).")
        return

    _con.print()
    _con.print(Rule("[bold]BASELINE (CHUAN) DA LUU[/]", style="cyan"))
    found_any = False
    for ip, alias in hosts:
        menu_name, device_name, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if baseline is None:
            _con.print(f"  [dim]>> {alias} ({ip}): chua co baseline duoc xac nhan.[/]")
            continue
        found_any = True
        if not device_name:
            _mn, device_name, _ = get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
        if not device_name:
            device_name = alias
        updated_at = get_updated_at_by_host(cfg["baseline_db"], "baseline_menu", ip)
        _con.print(
            f"\n  [bold cyan]{alias}[/] ({ip})  hostname: [bold]{device_name}[/]  "
            f"menu: [dim]{menu_name}[/]  cap nhat: [dim]{updated_at or '-'}[/]"
        )
        print_options(baseline)

    if not found_any:
        _con.print("\n  [dim](Chua co thiet bi nao duoc xac nhan baseline.)[/]")
    _con.print(Rule(style="cyan"))


def search_device(cfg):
    query = input("  Nhap IP hoac ten thiet bi can tim: ").strip()
    if not query:
        return
    query_lower = query.lower()

    hosts = load_ip_list(cfg["ip_list"])
    if not hosts:
        print(f"[!] Danh sach {cfg['ip_list']} dang rong. Hay them IP truoc (lua chon 1).")
        return

    found = []
    for ip, alias in hosts:
        _mn, device_name, source = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if source is None:
            # Chua co baseline duoc xac nhan -> tam thoi tim trong snapshot gan nhat
            _mn, device_name, source = get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
        if not source:
            continue
        # Neu khong lay duoc hostname tu DB, fallback sang snapshot roi alias
        if not device_name:
            _mn2, snap_name, _ = get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
            device_name = snap_name or alias
        for key, entry in source.items():
            target_ip = entry.get("ip", "")
            description = entry.get("description", "")
            if (query_lower in target_ip.lower()
                    or query_lower in description.lower()
                    or query_lower == key.lower()):
                found.append((ip, alias, device_name, key, entry))

    if not found:
        _con.print(f"\n  [yellow][!][/] Khong tim thay thiet bi nao khop voi '[bold]{query}[/]'.")
        return

    _con.print(f"\n  [green][*][/] Tim thay [bold]{len(found)}[/] ket qua cho '[bold]{query}[/]':")
    for ip, alias, device_name, key, entry in found:
        proto = _norm_proto(entry.get("protocol"))
        port  = entry.get("port", 22 if proto == "ssh" else 23)
        _con.print(f"    [cyan]→[/] Quan ly boi OOB: [bold]{alias}[/] ({ip}  hostname: {device_name or '[dim](chua ro)[/]'})")
        _con.print(f"      Option [[bold cyan]{key}[/]] {entry.get('description', '')}  [dim]→ {proto}://{entry['ip']}:{port}[/]")


# ---------------------------------------------------------------------------
# Man hinh chinh
# ---------------------------------------------------------------------------

def _show_menu(cfg):
    """Hien thi menu chinh bang rich Panel."""
    monitoring = _is_monitoring()
    hosts_n    = len(load_ip_list(cfg["ip_list"]))

    status = (
        f"[bold green]● DANG GIAM SAT[/]   chu ky {cfg['interval']}s"
        if monitoring else "[dim]○ DUNG[/]"
    )
    user = f"[bold]{cfg['username']}[/]" if cfg["username"] else "[dim yellow](chua dat)[/]"

    menu_label = cfg['menu_name_override'] or "tu dong do"
    info = Text.from_markup(
        f"Status   : {status}\n"
        f"Thiet bi : [bold]{hosts_n}[/]   Menu: [bold]{menu_label}[/]   User: {user}"
    )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", min_width=5, justify="right")
    grid.add_column(min_width=40)

    def row(k, lbl): grid.add_row(f"[{k}]", lbl)
    def sep():       grid.add_row("", "")

    row("1", "Them thiet bi OOB")
    row("2", "Xoa thiet bi OOB")
    sep()
    if monitoring:
        row("3", "[dim]Giam sat dang chay...[/]")
        row("4", "[bold yellow]Dung giam sat[/]")
    else:
        row("3", "[bold green]Bat dau giam sat (background)[/]")
        row("4", "[dim]Dung giam sat[/]")
    sep()
    row("5", "Cau hinh  (username / password / ...)")
    row("6", "Xem danh sach thiet bi")
    row("7", "Xem baseline")
    row("8", "Tim kiem thiet bi")
    sep()
    row("q", "[bold red]Thoat[/]")

    _con.print()
    _con.print(Panel(
        Group(info, Rule(style="dim cyan"), grid),
        title="[bold cyan]  OOB NETWORK MONITOR  [/]",
        border_style="cyan",
        padding=(1, 2),
    ))


def main_menu(cfg, config_path):
    while True:
        _show_menu(cfg)
        try:
            choice = _con.input("[bold]Chon[/]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _con.print()
            if _is_monitoring():
                stop_monitor_bg()
            _con.print("\n[dim]Tam biet.[/]")
            sys.exit(0)

        _con.print()

        if choice == "1":
            ip    = _con.input("  [cyan]IP thiet bi OOB[/]: ").strip()
            alias = _con.input("  [cyan]Ten goi (alias)[/]: ").strip() or None
            if ip:
                add_ip(cfg["ip_list"], ip, alias)
                _con.print(f"  [green]✓[/] Da them {ip}.")

        elif choice == "2":
            ip = _con.input("  [cyan]IP can xoa[/]: ").strip()
            if ip:
                remove_ip(cfg["ip_list"], ip)
                _con.print(f"  [green]✓[/] Da xoa {ip}.")

        elif choice == "3":
            if _is_monitoring():
                _con.print("  [yellow][!][/] Giam sat dang chay. Chon 4 de dung.")
            else:
                if start_monitor_bg(cfg):
                    _con.print("  [green]✓[/] Giam sat da khoi dong trong background.")
                    _con.print("  [dim]  Output se hien thi co prefix timestamp \\[HH:MM:SS].[/]")

        elif choice == "4":
            if _is_monitoring():
                _con.print("  [yellow][>>][/] Dang dung giam sat ...")
                stop_monitor_bg()
                _con.print("  [green]✓[/] Da dung.")
            else:
                _con.print("  [dim][--] Giam sat hien khong chay.[/]")

        elif choice == "5":
            settings_menu(cfg, config_path)

        elif choice == "6":
            list_devices(cfg)

        elif choice == "7":
            view_baseline(cfg)

        elif choice == "8":
            query = _con.input("  [cyan]Nhap IP, ten, hoac so option[/]: ").strip()
            if query:
                search_device(cfg, query)

        elif choice == "q":
            if _is_monitoring():
                ans = _con.input("  [yellow]Giam sat dang chay. Dung va thoat? [y/N][/]: ").strip().lower()
                if ans != "y":
                    continue
                stop_monitor_bg()
            _con.print("\n[dim]Tam biet.[/]")
            sys.exit(0)

        else:
            _con.print("  [red][!][/] Lua chon khong hop le.")


def main():
    config_path = CONFIG_FILE_DEFAULT
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    cfg = load_config(config_path)
    if not os.path.exists(config_path):
        save_config(config_path, cfg)
        _con.print(f"[yellow][*][/] Tao {config_path} voi gia tri mac dinh. Vao 'Cau hinh' de dat username/password.")

    main_menu(cfg, config_path)


if __name__ == "__main__":
    main()
