#!/usr/bin/env python3
"""
oob_monitor.py

Cong cu giam sat (READ-ONLY) menu OOB.
Kien truc Multi-Terminal:
    - Terminal 1: Giam sat lien tuc (Daemon) - Phat hien va canh bao khac biet.
    - Terminal 2: Menu Quan ly - Them/Xoa IP, Cau hinh, Xem danh sach.
"""

import getpass
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import re
import threading
from datetime import datetime
from collections import deque

from rich import box as rbox
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.live import Live

# Nạp MiniTelnet từ thư viện của bạn để thực hiện Deep Verify
from oob_lib import poll_host, MiniTelnet

CONFIG_FILE_DEFAULT = "oob_config.json"

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "enable_password": "",
    "menu_name_override": "",
    "ssh_port": 22,
    "telnet_port": 23,
    "interval": 30,
    "ip_list": "oob_ips.txt",
    "baseline_db": "baseline.db",
    "snapshot_db": "snapshot.db",
}

# ---------------------------------------------------------------------------
# Hệ thống UI đa luồng (Chia đôi màn hình)
# ---------------------------------------------------------------------------

_con = Console(highlight=False)

MAX_LOG = 15
oob_logs = deque(maxlen=MAX_LOG)
verify_logs = deque(maxlen=MAX_LOG)
ui_lock = threading.Lock()

# Bố cục màn hình
layout = Layout()
layout.split_column(
    Layout(name="upper"),
    Layout(name="lower")
)

_live_ui = None

def update_ui():
    """Cập nhật dữ liệu vào 2 khung panel."""
    with ui_lock:
        layout["upper"].update(Panel(Text.from_markup("\n".join(oob_logs)), title="[bold cyan]🔍 OOB MONITORING (Cấu hình)[/]", border_style="cyan"))
        layout["lower"].update(Panel(Text.from_markup("\n".join(verify_logs)), title="[bold magenta]⚡ DEEP VERIFY (Thiết bị cuối)[/]", border_style="magenta"))
        if _live_ui and _live_ui.is_started:
            _live_ui.update(layout)

def log_oob(msg: str):
    """Ghi log cho nửa trên màn hình (Daemon cấu hình)."""
    ts = datetime.now().strftime("%H:%M:%S")
    oob_logs.append(f"[dim]\\[{ts}][/] {msg}")
    update_ui()

def log_verify(msg: str):
    """Ghi log cho nửa dưới màn hình (Verify vật lý)."""
    ts = datetime.now().strftime("%H:%M:%S")
    verify_logs.append(f"[dim]\\[{ts}][/] {msg}")
    update_ui()

# ---------------------------------------------------------------------------
# Các hàm tiện ích, cấu hình và Database (Giữ nguyên logic của bạn)
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
        ⚙️  CAU HINH HIEN TAI
--------------------------------------------------
1. Username           : {cfg['username'] or '(khong dung)'}
2. Password            : {mask(cfg['password'])}
3. Enable password     : {mask(cfg['enable_password'])}
4. Ten menu (rong=tu dong): {cfg['menu_name_override'] or '(tu dong do)'}
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
            val = input(f"  Ten menu ep dung, de trong = tu dong do (hien tai: {cur}): ").strip()
            cfg["menu_name_override"] = val
        elif choice == "5":
            val = input(f"  SSH port moi (hien tai: {cfg.get('ssh_port', 22)}): ").strip()
            if val.isdigit(): cfg["ssh_port"] = int(val)
        elif choice == "6":
            val = input(f"  Telnet port moi (hien tai: {cfg['telnet_port']}): ").strip()
            if val.isdigit(): cfg["telnet_port"] = int(val)
        elif choice == "7":
            val = input(f"  Chu ky thu thap, giay (hien tai: {cfg['interval']}): ").strip()
            if val.isdigit(): cfg["interval"] = int(val)
        elif choice == "8":
            val = input(f"  File danh sach IP moi: ").strip()
            if val: cfg["ip_list"] = val
        elif choice == "9":
            val = input(f"  File baseline DB moi: ").strip()
            if val: cfg["baseline_db"] = val
        elif choice == "a":
            val = input(f"  File snapshot DB moi: ").strip()
            if val: cfg["snapshot_db"] = val
        elif choice == "0":
            save_config(config_path, cfg)
            print(f"[*] Da luu cau hinh vao {config_path}")
            return
        else:
            print("[!] Lua chon khong hop le.")
            continue
        save_config(config_path, cfg)


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
        _con.print(f"  [yellow][!][/] IP {ip} da co trong danh sach.")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{ip} {alias or ip}\n")
    _con.print(f"  [green]✓[/] Da them {ip} ({alias or ip})")

def remove_ip(path, ip):
    hosts = load_ip_list(path)
    remaining = [h for h in hosts if h[0] != ip]
    if len(remaining) == len(hosts):
        _con.print(f"  [yellow][!][/] Khong tim thay IP {ip}.")
        return
    with open(path, "w", encoding="utf-8") as f:
        for h_ip, alias in remaining:
            f.write(f"{h_ip} {alias}\n")
    _con.print(f"  [green]✓[/] Da xoa {ip}")


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
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if "device_name" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN device_name TEXT")
    conn.commit()
    return conn

def get_options_by_host(db_path, table, host):
    conn = _init_db(db_path, table)
    cur = conn.execute(
        f"SELECT menu_name, option_key, device_name, description, target_ip, target_port "
        f"FROM {table} WHERE host=?", (host,)
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
    conn = _init_db(db_path, table)
    cur = conn.execute(f"SELECT MAX(updated_at) FROM {table} WHERE host=?", (host,))
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
    return p or "telnet"

def options_equal(a: dict, b: dict) -> bool:
    if set(a.keys()) != set(b.keys()): return False
    for key in a:
        if a[key].get("ip") != b[key].get("ip"): return False
        if a[key].get("port") != b[key].get("port"): return False
        if _norm_proto(a[key].get("protocol")) != _norm_proto(b[key].get("protocol")): return False
        if a[key].get("description") != b[key].get("description"): return False
    return True

def diff_options(baseline: dict, snapshot: dict) -> dict:
    extra   = sorted(set(snapshot) - set(baseline))
    missing = sorted(set(baseline) - set(snapshot))
    changed = []
    for key in sorted(set(baseline) & set(snapshot)):
        b, s = baseline[key], snapshot[key]
        if (b.get("ip") != s.get("ip") or b.get("port") != s.get("port")
            or _norm_proto(b.get("protocol")) != _norm_proto(s.get("protocol"))
            or b.get("description") != s.get("description")):
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
        t.add_column("Baseline (chuan)", min_width=36)
        t.add_column("Hien tai", min_width=36)
        for key in d["changed"]:
            b, s = baseline[key], snapshot[key]
            t.add_row(f"[{key}]", f"{b.get('description','')} [dim]{_fmt_entry(b)}[/]",
                      f"{s.get('description','')} [dim]{_fmt_entry(s)}[/]")
        _con.print(t)

def print_options(options: dict):
    t = Table(box=rbox.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column(style="bold cyan", justify="right", min_width=4)
    t.add_column(min_width=20)
    t.add_column(style="dim")
    for key in sorted(options):
        e = options[key]
        proto = _norm_proto(e.get("protocol"))
        port  = e.get("port", 22 if proto == "ssh" else 23)
        col   = "green" if proto == "ssh" else "yellow"
        t.add_row(f"[{key}]", e.get("description", ""), f"[{col}]{proto}[/]://{e['ip']}:{port}")
    _con.print(t)


# ---------------------------------------------------------------------------
# Module Deep Verify (Background Thread)
# ---------------------------------------------------------------------------

def extract_hostname(output: str) -> str:
    """Lọc hostname từ luồng ký tự trả về của Console."""
    # Loại bỏ mã màu ANSI nếu có
    output = re.sub(r'\x1b\[.*?m', '', output)
    for line in reversed(output.splitlines()):
        line = line.strip()
        # Tìm các dạng prompt phổ biến: Router>, Switch#, [HNI-R1]>
        m = re.search(r'([A-Za-z0-9_\-\.]+)[>#]', line)
        if m:
            return m.group(1)
    return None

def run_deep_verify(alias, options):
    """Tiến trình ngầm để verify thiết bị vật lý đầu cuối."""
    log_verify(f"[*] Bat dau kiem tra vat ly thiet bi cho OOB: [bold]{alias}[/]")
    
    for key, opt in options.items():
        desc = opt.get("description", "")
        if not desc:
            continue
            
        ip = opt.get("ip")
        port = opt.get("port", 23)
        
        try:
            # Giao thức reverse console chủ yếu là telnet
            session = MiniTelnet(ip, port, timeout=4)
            # Gửi 2 lần Enter để đánh thức prompt (AutoCommand behavior)
            session.write("\r\n\r\n")
            
            # Đọc cho đến khi thấy dấu hiệu của prompt mạng
            output = session.read_until([">", "#", "login:", "Password:"], timeout=4)
            session.close()
            
            act_host = extract_hostname(output)
            
            if not act_host:
                log_verify(f"[dim][-][/] {alias} (Opt {key}): Timeout hoac khong the doc prompt.")
                continue
            
            # Verify Rule: Description == Hostname
            if act_host.lower() == desc.lower():
                log_verify(f"[green][OK][/] {alias} (Opt {key}): Khop ({act_host})")
            else:
                log_verify(f"[bold red]CANH BAO[/] Phat hien thiet bi noi line console ([yellow]{act_host}[/]) khac voi description ([yellow]{desc}[/]) tai Opt {key}!")
                
        except Exception as e:
            log_verify(f"[dim][LOI][/] {alias} (Opt {key}): Khong the ket noi den port {port} ({str(e)})")
            
    log_verify(f"[green]✓[/] Hoan thanh Verify cho OOB: [bold]{alias}[/]\n")


# ---------------------------------------------------------------------------
# Vòng lặp giám sát (Chạy trên Terminal Daemon)
# ---------------------------------------------------------------------------

def input_with_timeout(prompt_text: str, timeout: int = 5):
    """Hàm lấy input có giới hạn thời gian, trả về str nếu có nhập, hoặc None nếu timeout."""
    _con.print(prompt_text, end="")
    
    if platform.system() == "Windows":
        import msvcrt
        start_time = time.time()
        input_str = ""
        while time.time() - start_time < timeout:
            if msvcrt.kbhit():
                char = msvcrt.getwche()
                if char in ('\r', '\n'):
                    print()
                    return input_str
                input_str += char
            time.sleep(0.05)
        print() 
        return None
    else:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            return sys.stdin.readline().strip()
        else:
            print()
            return None 

def run_daemon(cfg):
    """Vòng lặp giám sát hiển thị đa luồng chia đôi màn hình."""
    global _live_ui
    _con.print(Panel("[bold green]OOB MONITOR DAEMON[/]\n[dim]Dang giam sat lien tuc. Nhan Ctrl+C de dung.[/]", border_style="green"))
    
    if not cfg.get("password"):
        _con.print("[red][!][/] Chua cau hinh password! Vui long cau hinh truoc.")
        return

    update_ui()
    
    # Khởi chạy giao diện Live
    with Live(layout, refresh_per_second=4, screen=False) as live:
        _live_ui = live
        log_oob(f"[green][START][/] Khoi dong chu ky {cfg['interval']}s.")
        
        try:
            while True:
                hosts = load_ip_list(cfg["ip_list"])
                
                if not hosts:
                    log_oob("[yellow][!][/] Danh sach IP trong. Doi them thiet bi...")
                    time.sleep(cfg["interval"])
                    continue

                for ip, alias in hosts:
                    log_oob(f"[cyan][SCAN][/] [bold]{alias}[/] ({ip}) ...")
                    try:
                        hostname, menu_name, snapshot = poll_host(
                            ip, cfg["telnet_port"], cfg["username"], cfg["password"],
                            cfg["enable_password"], menu_name=cfg.get("menu_name_override") or None,
                            ssh_port=cfg.get("ssh_port", 22),
                        )
                    except Exception as exc:
                        log_oob(f"[red][LOI][/] {alias} ({ip}): {exc}")
                        continue

                    hn_label = f"hostname=[bold]{hostname}[/]" if hostname else "hostname=?"
                    menu_n   = len(snapshot)

                    if not menu_name or not snapshot:
                        log_oob(f"[yellow][!][/] {alias}: Khong the parse menu hop le tren thiet bi.")
                        continue

                    save_options(cfg["snapshot_db"], "snapshot_menu", ip, menu_name, hostname, snapshot)
                    _mn, _dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)

                    if baseline is None:
                        # Tạm ngưng Live để tránh nhiễu I/O khi nhập
                        live.stop() 
                        _con.print(f"\n  [yellow][?][/] Chua co baseline cho [bold]{alias}[/] ({ip}).")
                        _con.print(f"      {hn_label} | {menu_n} option:")
                        print_options(snapshot)
                        
                        prompt_msg = f"  Xac nhan day la CHUAN cho {alias}? (y/N) [Bo qua sau 5s]: "
                        ans_raw = input_with_timeout(prompt_msg, timeout=5)
                        ans = ans_raw.strip().lower() if ans_raw is not None else ""
                        
                        if ans == "y":
                            save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
                            _con.print(f"  [green][OK][/] Da luu baseline cho {alias}.")
                            # Kích hoạt Deep Verify cho thiết bị vừa xác nhận
                            threading.Thread(target=run_deep_verify, args=(alias, snapshot), daemon=True).start()
                        else:
                            _con.print("  [dim][--] Het thoi gian hoac tu choi, se hoi lai chu ky sau.[/]")
                            
                        # Mở lại Live
                        live.start() 
                        continue

                    if options_equal(baseline, snapshot):
                        log_oob(f"[green][OK][/] {alias}: Khop voi baseline ({menu_n} option).")
                        # Thiết bị OK -> Bắn luồng Verify vật lý song song
                        threading.Thread(target=run_deep_verify, args=(alias, baseline), daemon=True).start()
                        continue

                    # Tạm ngưng Live để xử lý cảnh báo
                    live.stop()
                    _con.rule(f"[bold red]CANH BAO  {alias} ({ip}) KHAC baseline![/]", style="red")
                    print_diff(baseline, snapshot)
                    _con.print("  [dim]--- Baseline (chuan) ---[/]")
                    print_options(baseline)
                    _con.print("  [yellow]--- Hien tai tren thiet bi ---[/]")
                    print_options(snapshot)
                    
                    prompt_msg = f"  Cap nhat baseline theo hien tai cua {alias}? (y/N) (Bo qua sau 5s): "
                    ans_raw = input_with_timeout(prompt_msg, timeout=5)
                    ans = ans_raw.strip().lower() if ans_raw else ""

                    if ans == "y":
                        save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
                        _con.print(f"  [green][OK][/] Da cap nhat baseline moi cho {alias}.")
                        # Kích hoạt Verify cấu hình mới
                        threading.Thread(target=run_deep_verify, args=(alias, snapshot), daemon=True).start()
                    else:
                        _con.print(f"  [yellow][!][/] Het thoi gian hoac tu choi. Giu nguyen baseline cu cho {alias}.")
                    
                    live.start() # Mở lại Live

                log_oob(f"[dim][zzz] Dang cho {cfg['interval']}s de quet lai...[/]")
                # Sleep hoàn toàn bình thường, luồng Verify bên dưới vẫn tự do báo cáo lên UI
                time.sleep(cfg["interval"])

        except KeyboardInterrupt:
            pass
            
    _con.print("\n[yellow][STOP][/] Da nhan Ctrl+C. Dung Daemon.")


# ---------------------------------------------------------------------------
# Chuc nang Xem/Tim Kiem & Quản lý (Giữ nguyên)
# ---------------------------------------------------------------------------

def list_devices(cfg):
    hosts = load_ip_list(cfg["ip_list"])
    if not hosts:
        _con.print(f"\n  [yellow][!][/] Danh sach trong. Hay them IP truoc (option 1).")
        return

    table = Table(title="[bold]DANH SACH THIET BI OOB[/]", box=rbox.ROUNDED, border_style="cyan", header_style="bold cyan")
    table.add_column("Alias", style="bold cyan", min_width=12)
    table.add_column("IP", min_width=16)
    table.add_column("Hostname", min_width=14)
    table.add_column("Baseline", justify="center", min_width=9)
    table.add_column("Cap nhat luc", min_width=20)

    for ip, alias in hosts:
        _mn, device_name, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        updated_at = get_updated_at_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if device_name is None:
            _mn, device_name, _ = get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
        bl = "[green]✓ Co[/]" if baseline else "[yellow]✗ Chua[/]"
        table.add_row(alias, ip, device_name or "[dim](chua ro)[/]", bl, updated_at or "[dim]-[/]")

    _con.print()
    _con.print(table)

def view_baseline(cfg):
    hosts = load_ip_list(cfg["ip_list"])
    _con.print("\n" + "[bold cyan]BASELINE (CHUAN) DA LUU[/]".center(50, " "))
    _con.print(Rule(style="cyan"))
    found = False
    for ip, alias in hosts:
        mn, dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if baseline is None: continue
        found = True
        upd = get_updated_at_by_host(cfg["baseline_db"], "baseline_menu", ip)
        _con.print(f"\n  [bold cyan]{alias}[/] ({ip})  host: [bold]{dn or alias}[/]  upd: [dim]{upd}[/]")
        print_options(baseline)
    if not found:
        _con.print("  [dim](Chua co baseline nao duoc xac nhan)[/]")
    _con.print(Rule(style="cyan"))

def search_device(cfg):
    query = input("  Nhap IP hoac ten thiet bi can tim: ").strip().lower()
    if not query: return
    hosts = load_ip_list(cfg["ip_list"])
    found = []
    for ip, alias in hosts:
        _mn, dn, source = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if source is None:
            _mn, dn, source = get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
        if not source: continue
        dn = dn or alias
        for key, entry in source.items():
            if (query in entry.get("ip", "").lower() or query in entry.get("description", "").lower() or query == key.lower()):
                found.append((ip, alias, dn, key, entry))
    if not found:
        _con.print(f"\n  [yellow]Khong tim thay thiet bi nao![/]")
        return
    _con.print(f"\n  [green][*] Tim thay {len(found)} ket qua:[/]")
    for ip, alias, dn, key, entry in found:
        pr = _norm_proto(entry.get("protocol"))
        pt = entry.get("port", 22 if pr == "ssh" else 23)
        _con.print(f"    [cyan]→[/] OOB: [bold]{alias}[/] ({ip} - host: {dn}) | Opt [[bold cyan]{key}[/]] {entry.get('description', '')} [dim]→ {pr}://{entry['ip']}:{pt}[/]")


def scan_specific_devices(cfg):
    targets_input = _con.input("  [cyan]Nhap IP/Alias (cach nhau dau phay, de trong de quet TAT CA)[/]: ").strip()
    all_hosts = load_ip_list(cfg["ip_list"])
    
    if not all_hosts:
        _con.print("  [yellow][!][/] Danh sach IP hien dang trong. Vui long them IP truoc.")
        return

    hosts_to_scan = []

    if not targets_input:
        hosts_to_scan = all_hosts
    else:
        target_list = [t.strip().lower() for t in targets_input.split(",")]
        for ip, alias in all_hosts:
            if ip.lower() in target_list or alias.lower() in target_list:
                hosts_to_scan.append((ip, alias))

    if not hosts_to_scan:
        _con.print("  [yellow][!][/] Khong co IP/Alias nao khop voi danh sach 'oob_ips.txt'.")
        return

    _con.print(f"\n  [green][*] Bat dau quet tuc thi {len(hosts_to_scan)} thiet bi...[/]")
    
    for ip, alias in hosts_to_scan:
        _con.print(f"\n  [cyan][SCAN][/] [bold]{alias}[/] ({ip}) ...")
        try:
            hostname, menu_name, snapshot = poll_host(
                ip, cfg["telnet_port"], cfg["username"], cfg["password"],
                cfg["enable_password"], menu_name=cfg.get("menu_name_override") or None,
                ssh_port=cfg.get("ssh_port", 22),
            )
        except Exception as exc:
            _con.print(f"  [red][LOI][/] {alias} ({ip}): {exc}")
            continue

        hn_label = f"hostname=[bold]{hostname}[/]" if hostname else "hostname=?"
        menu_n   = len(snapshot)

        if not menu_name or not snapshot:
            _con.print(f"  [yellow][!][/] {alias}: Khong parse duoc menu hop le tren thiet bi.")
            continue

        save_options(cfg["snapshot_db"], "snapshot_menu", ip, menu_name, hostname, snapshot)
        _mn, _dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)

        if baseline is None:
            _con.print(f"  [yellow][?][/] Chua co baseline cho [bold]{alias}[/] ({ip}).")
            _con.print(f"      {hn_label} | {menu_n} option:")
            print_options(snapshot)
            ans = _con.input(f"  Xac nhan day la CHUAN cho {alias}? (y/N): ").strip().lower()
            if ans == "y":
                save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
                _con.print(f"  [green][OK][/] Da luu baseline cho {alias}.")
            else:
                _con.print("  [dim][--] Bo qua.[/]")
            continue

        if options_equal(baseline, snapshot):
            _con.print(f"  [green][OK][/] {alias}: Khop voi baseline ({menu_n} option).")
            continue

        _con.rule(f"[bold red]CANH BAO  {alias} ({ip}) KHAC baseline![/]", style="red")
        print_diff(baseline, snapshot)
        _con.print("  [dim]--- Baseline (chuan) ---[/]")
        print_options(baseline)
        _con.print("  [yellow]--- Hien tai tren thiet bi ---[/]")
        print_options(snapshot)
        
        ans = _con.input(f"  Cap nhat baseline theo trang thai hien tai cua {alias}? (y/N): ").strip().lower()
        if ans == "y":
            save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
            _con.print(f"  [green][OK][/] Da cap nhat baseline moi cho {alias}.")
        else:
            _con.print(f"  [yellow][!][/] Giu nguyen baseline cu cho {alias}.")


def _show_menu(cfg):
    hosts_n = len(load_ip_list(cfg["ip_list"]))
    user = f"[bold]{cfg['username']}[/]" if cfg["username"] else "[dim yellow](chua dat)[/]"
    menu_label = cfg['menu_name_override'] or "tu dong do"
    
    info = Text.from_markup(f"Thiet bi : [bold]{hosts_n}[/]   Menu: [bold]{menu_label}[/]   User: {user}")
    
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", min_width=5, justify="right")
    grid.add_column(min_width=40)
    
    grid.add_row("[1]", "Them thiet bi OOB")
    grid.add_row("[2]", "Xoa thiet bi OOB")
    grid.add_row("", "")
    grid.add_row("[3]", "Cau hinh (username/password/port...)")
    grid.add_row("[4]", "Xem danh sach thiet bi")
    grid.add_row("[5]", "Xem baseline (Chuan)")
    grid.add_row("[6]", "Tim kiem thiet bi")
    grid.add_row("[7]", "[bold green]Quet kiem tra tuc thi (Chi dinh hoac Tat ca)[/]")
    grid.add_row("", "")
    grid.add_row("[0]", "[bold red]Thoat[/]")

    _con.print()
    _con.print(Panel(
        Group(info, Rule(style="dim cyan"), grid),
        title="[bold cyan] 🎛️  OOB NETWORK MANAGER [/]",
        border_style="cyan",
        padding=(1, 2),
    ))

def main_menu(cfg, config_path):
    while True:
        _show_menu(cfg)
        try:
            choice = _con.input("[bold]Chon[/]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

        _con.print()

        if choice == "1":
            ip = _con.input("  [cyan]IP thiet bi OOB[/]: ").strip()
            alias = _con.input("  [cyan]Ten goi (alias)[/]: ").strip() or None
            if ip: add_ip(cfg["ip_list"], ip, alias)
        elif choice == "2":
            ip = _con.input("  [cyan]IP can xoa[/]: ").strip()
            if ip: remove_ip(cfg["ip_list"], ip)
        elif choice == "3":
            settings_menu(cfg, config_path)
        elif choice == "4":
            list_devices(cfg)
        elif choice == "5":
            view_baseline(cfg)
        elif choice == "6":
            search_device(cfg)
        elif choice == "7":
            scan_specific_devices(cfg)
        elif choice == "0":
            _con.print("\n[dim]Tam biet.[/]")
            sys.exit(0)
        else:
            _con.print("  [red][!][/] Lua chon khong hop le.")


def main():
    config_path = CONFIG_FILE_DEFAULT
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            config_path = arg
            break

    cfg = load_config(config_path)
    if not os.path.exists(config_path):
        save_config(config_path, cfg)

    if "--daemon" in sys.argv:
        run_daemon(cfg)
        return
    elif "--menu" in sys.argv:
        main_menu(cfg, config_path)
        return

    _con.print(Panel(
        "[bold cyan]1.[/] Mo Menu Quan Ly (Them/Sua IP, Xem danh sach)\n"
        "[bold cyan]2.[/] Mo Trinh Giam Sat (Chay log Daemon o terminal nay)\n"
        "[bold cyan]3.[/] Mo CA HAI (Tu dong mo 2 cua so - Yeu cau Windows)",
        title="[bold yellow]🚀 OOB LAUNCHER MULTI-TERM[/]",
        border_style="yellow", padding=(1,2)
    ))
    
    choice = input("Chon che do: ").strip()
    
    if choice == "1":
        main_menu(cfg, config_path)
    elif choice == "2":
        run_daemon(cfg)
    elif choice == "3":
        script_name = sys.argv[0]
        if platform.system() == "Windows":
            _con.print("[*] Dang mo 2 cua so terminal doc lap...")
            subprocess.Popen(f'start cmd /k "python {script_name} --daemon"', shell=True)
            subprocess.Popen(f'start cmd /k "python {script_name} --menu"', shell=True)
        else:
            _con.print("[yellow]Tinh nang tu dong mo cua so hien chi on dinh tren Windows.[/]")
            _con.print(f"Tren Linux/WSL, vui long mo 2 tab va tu chay lenh:\n  python {script_name} --menu\n  python {script_name} --daemon")
        sys.exit(0)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()