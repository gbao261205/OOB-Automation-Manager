#!/usr/bin/env python3
"""
oob_monitor.py

Cong cu giam sat, Verify vat ly va Tu dong phuc hoi (Self-healing) menu OOB.
Kien truc Multi-Terminal:
    - Terminal 1: Giam sat lien tuc (Daemon) + Deep Verify (Smart Clear) + Auto Push.
    - Terminal 2: Menu Quan ly - Them/Xoa IP, Cau hinh, Xem danh sach, Log, Manual Push.
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
import concurrent.futures
from datetime import datetime, timedelta
from collections import deque

from rich import box as rbox
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.live import Live

# Import tu oob_lib
from oob_lib import (
    poll_host, MiniTelnet, connect_auto, fetch_hostname,
    fetch_hostname_via_auto, hostname_matches_description,
    push_menu_descriptions, ping_host
)

CONFIG_FILE_DEFAULT = "oob_config.json"

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "enable_password": "",
    "credentials": [],          # Danh sach tai khoan phu (ho tro Multi-Account)
    "menu_name_override": "",
    "ssh_port": 22,
    "telnet_port": 23,
    "interval": 30,
    "verify_interval": 3600,
    "ip_list": "oob_ips.txt",
    "baseline_db": "baseline.db",
    "snapshot_db": "snapshot.db",
    "auto_verify": True,        
    "auto_push_desc": True,     
    "verify_schedule_mode": "interval", 
    "verify_schedule_time": "01:00",    
    "verify_schedule_weekday": "mon",   
    "scan_schedule_mode": "interval",   
    "scan_schedule_time": "01:00",      
    "scan_schedule_weekday": "mon",     
    "verify_wait_after_connect": 1.5,
    "max_verify_duration": 300,
}

# ---------------------------------------------------------------------------
# Module-level compiled regex constants
# ---------------------------------------------------------------------------
_DESC_PREFIX_RE    = re.compile(r'^[-=>\s]+')
_HOSTNAME_PROMPT_RE = re.compile(r'([A-Za-z0-9_\-\.]+)[>#]')
_HOSTNAME_LOGIN_RE  = re.compile(r'([A-Za-z0-9_\-\.]+)\s+login:', re.IGNORECASE)
_HOSTNAME_BSD_RE    = re.compile(r'\(([A-Za-z0-9_\-\.]+)\)\s*\(tty', re.IGNORECASE)
_ANSI_STRIP_RE      = re.compile(r'\x1b\[.*?m')
_CONN_ERR_RE        = re.compile(r'refused|time(d)?[\s-]?out|unreachable|no route to host|unknown host|% ', re.IGNORECASE)
_IP_RE              = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')

# ---------------------------------------------------------------------------
# Hệ thống UI đa luồng
# ---------------------------------------------------------------------------
_con = Console(highlight=False)

MAX_LOG = 15
oob_logs = deque(maxlen=MAX_LOG)
verify_logs = deque(maxlen=MAX_LOG)
ui_lock = threading.Lock()

layout = Layout()
layout.split_column(
    Layout(name="upper"),
    Layout(name="lower")
)

_live_ui = None

def update_ui():
    with ui_lock:
        layout["upper"].update(Panel(Text.from_markup("\n".join(oob_logs)), title="[bold cyan]🔍 OOB MONITORING (Cấu hình)[/]", border_style="cyan"))
        layout["lower"].update(Panel(Text.from_markup("\n".join(verify_logs)), title="[bold magenta]⚡ DEEP VERIFY (Thiết bị cuối)[/]", border_style="magenta"))
        if _live_ui and _live_ui.is_started:
            _live_ui.update(layout)

def log_oob(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    oob_logs.append(f"[dim]\\[{ts}][/] {msg}")
    update_ui()

def log_verify(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    verify_logs.append(f"[dim]\\[{ts}][/] {msg}")
    update_ui()

def log_baseline_change(alias, ip, action):
    """Luu log khi cap nhat Baseline tu dong."""
    os.makedirs("baseline-logs", exist_ok=True)
    log_path = os.path.join("baseline-logs", "baseline_updates.log")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {action}: {alias} ({ip})\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Trang thai thiet bi (ping + tinh trang menu) - dung de hien thi trong bao cao
# ---------------------------------------------------------------------------
DEVICE_STATUS_FILE = "device_status.json"
_device_status_lock = threading.Lock()

def load_device_status() -> dict:
    if not os.path.exists(DEVICE_STATUS_FILE):
        return {}
    try:
        with open(DEVICE_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_device_status(ip, **fields):
    """Cap nhat trang thai (ping/menu_state/...) cho 1 IP, giu lai cac field cu."""
    with _device_status_lock:
        data = load_device_status()
        entry = data.get(ip, {})
        entry.update(fields)
        entry["checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data[ip] = entry
        try:
            with open(DEVICE_STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

MENU_STATE_LABELS = {
    "ok": "OK",
    "no_menu": "Khong co menu",
    "fetch_failed": "Khong lay duoc thong tin menu",
}


# ---------------------------------------------------------------------------
# Các hàm tiện ích, cấu hình và Database
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

def get_all_credentials(cfg):
    """Gop tai khoan chinh va danh sach tai khoan phu de duyet Multi-Account."""
    creds = []
    if cfg.get("username") or cfg.get("password"):
        creds.append({
            "username": cfg.get("username", ""),
            "password": cfg.get("password", ""),
            "enable_password": cfg.get("enable_password", "")
        })
    for c in cfg.get("credentials", []):
        if c not in creds:
            creds.append(c)
    if not creds:
        creds.append({"username": "", "password": "", "enable_password": ""})
    return creds

_WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_WEEKDAY_LABELS = {
    "mon": "Thu 2", "tue": "Thu 3", "wed": "Thu 4", "thu": "Thu 5",
    "fri": "Thu 6", "sat": "Thu 7", "sun": "Chu nhat",
}

def _parse_hhmm(text, default="01:00"):
    try:
        h_str, m_str = str(text).strip().split(":")
        h, m = int(h_str), int(m_str)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    dh, dm = default.split(":")
    return int(dh), int(dm)

def compute_next_scheduled_run(mode, time_str, weekday_str, now=None):
    now = now or datetime.now()
    hh, mm = _parse_hhmm(time_str)
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if mode == "weekly":
        target_wd = _WEEKDAY_MAP.get((weekday_str or "mon").strip().lower()[:3], 0)
        days_ahead = (target_wd - now.weekday()) % 7
        candidate = candidate + timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate

def _describe_verify_schedule(cfg):
    mode = cfg.get("verify_schedule_mode", "interval")
    if mode == "daily":
        return f"Hang ngay luc {cfg.get('verify_schedule_time', '01:00')}"
    if mode == "weekly":
        wd = (cfg.get("verify_schedule_weekday", "mon") or "mon").strip().lower()[:3]
        wd_label = _WEEKDAY_LABELS.get(wd, wd)
        return f"Hang tuan vao {wd_label} luc {cfg.get('verify_schedule_time', '01:00')}"
    return f"Lap lai moi {cfg.get('verify_interval', 3600)}s (che do interval)"

def _edit_verify_schedule(cfg):
    _con.print(f"""
  --- Lich chay Deep Verify tu dong ---
  Hien tai: {_describe_verify_schedule(cfg)}
  1. Lap lai theo chu ky (interval, giay)
  2. Hang ngay, vao 1 gio co dinh
  3. Hang tuan, vao 1 thu + gio co dinh
""")
    mode_choice = _con.input("  [cyan]Chon che do (1/2/3)[/]: ").strip()
    if mode_choice == "1":
        cfg["verify_schedule_mode"] = "interval"
        _con.print("  [green][*][/] Da chuyen ve che do lap lai theo chu ky.")
    elif mode_choice == "2":
        cfg["verify_schedule_mode"] = "daily"
        val = _con.input(f"  [cyan]Gio chay moi ngay, dinh dang HH:MM[/]: ").strip()
        if val:
            h, m = _parse_hhmm(val)
            cfg["verify_schedule_time"] = f"{h:02d}:{m:02d}"
        _con.print(f"  [green][*][/] Da dat lich: Hang ngay luc {cfg.get('verify_schedule_time', '01:00')}.")
    elif mode_choice == "3":
        cfg["verify_schedule_mode"] = "weekly"
        wd_val = _con.input(f"  [cyan]Thu (mon/tue/wed/thu/fri/sat/sun)[/]: ").strip().lower()
        if wd_val[:3] in _WEEKDAY_MAP:
            cfg["verify_schedule_weekday"] = wd_val[:3]
        val = _con.input(f"  [cyan]Gio chay, dinh dang HH:MM[/]: ").strip()
        if val:
            h, m = _parse_hhmm(val)
            cfg["verify_schedule_time"] = f"{h:02d}:{m:02d}"
        _con.print(f"  [green][*][/] Da dat lich: {_describe_verify_schedule(cfg)}.")
    else:
        _con.print("  [yellow][!][/] Lua chon khong hop le.")

def _describe_scan_schedule(cfg):
    mode = cfg.get("scan_schedule_mode", "interval")
    if mode == "daily":
        return f"Hang ngay luc {cfg.get('scan_schedule_time', '01:00')}"
    if mode == "weekly":
        wd = (cfg.get("scan_schedule_weekday", "mon") or "mon").strip().lower()[:3]
        wd_label = _WEEKDAY_LABELS.get(wd, wd)
        return f"Hang tuan vao {wd_label} luc {cfg.get('scan_schedule_time', '01:00')}"
    return f"Lap lai moi {cfg.get('interval', 30)}s (che do interval)"

def _edit_scan_schedule(cfg):
    _con.print(f"""
  --- Lich chay Thu thap cau hinh tu dong ---
  Hien tai: {_describe_scan_schedule(cfg)}
  1. Lap lai theo chu ky (giay)
  2. Hang ngay, vao 1 gio co dinh
  3. Hang tuan, vao 1 thu + gio co dinh
""")
    mode_choice = _con.input("  [cyan]Chon che do (1/2/3)[/]: ").strip()
    if mode_choice == "1":
        cfg["scan_schedule_mode"] = "interval"
        _con.print("  [green][*][/] Da chuyen ve che do lap lai theo chu ky.")
    elif mode_choice == "2":
        cfg["scan_schedule_mode"] = "daily"
        val = _con.input(f"  [cyan]Gio chay moi ngay, dinh dang HH:MM[/]: ").strip()
        if val:
            h, m = _parse_hhmm(val)
            cfg["scan_schedule_time"] = f"{h:02d}:{m:02d}"
        _con.print(f"  [green][*][/] Da dat lich: Hang ngay luc {cfg.get('scan_schedule_time', '01:00')}.")
    elif mode_choice == "3":
        cfg["scan_schedule_mode"] = "weekly"
        wd_val = _con.input(f"  [cyan]Thu (mon/tue/wed/thu/fri/sat/sun)[/]: ").strip().lower()
        if wd_val[:3] in _WEEKDAY_MAP:
            cfg["scan_schedule_weekday"] = wd_val[:3]
        val = _con.input(f"  [cyan]Gio chay, dinh dang HH:MM[/]: ").strip()
        if val:
            h, m = _parse_hhmm(val)
            cfg["scan_schedule_time"] = f"{h:02d}:{m:02d}"
        _con.print(f"  [green][*][/] Da dat lich: {_describe_scan_schedule(cfg)}.")
    else:
        _con.print("  [yellow][!][/] Lua chon khong hop le.")

def _test_connection(cfg):
    test_ip = _con.input("  [cyan]Nhap IP OOB can thu ket noi[/]: ").strip()
    if not test_ip:
        _con.print("  [yellow][!][/] Khong nhap IP. Huy.")
        return
        
    creds = get_all_credentials(cfg)
    if not any(c.get("password") for c in creds):
        _con.print("  [yellow][!][/] Chua cau hinh bat ky password nao. Vui long cau hinh truoc.")
        return
        
    _con.print(f"  [cyan][*][/] Dang thu ket noi toi [bold]{test_ip}[/] (Multi-Account)...")
    last_exc = None
    
    for idx, c in enumerate(creds):
        try:
            session = connect_auto(
                test_ip, cfg.get("ssh_port", 22), cfg.get("telnet_port", 23),
                c["username"], c["password"], c["enable_password"], timeout=8,
            )
            hn = fetch_hostname(session)
            session.close()
            _con.print(f"  [green bold]✓ Ket noi thanh cong ({c['username']})![/] Hostname: [bold cyan]{hn or '?'}[/]")
            return
        except Exception as e:
            last_exc = e
            
    _con.print(f"  [red bold]✗ Ket noi that bai (thu {len(creds)} tai khoan):[/] {last_exc}")

def settings_menu(cfg, config_path):
    while True:
        schedule_mode = cfg.get("verify_schedule_mode", "interval")
        v_note = ("[dim red]<- Khong hieu luc[/]" if schedule_mode in ("daily", "weekly") else "[dim green]<- Dang co hieu luc[/]")
        scan_schedule_mode = cfg.get("scan_schedule_mode", "interval")
        s_note = ("[dim red]<- Khong hieu luc[/]" if scan_schedule_mode in ("daily", "weekly") else "[dim green]<- Dang co hieu luc[/]")
        auto_v = "[green bold]BAT[/]" if cfg.get('auto_verify', True) else "[red bold]TAT[/]"
        auto_p = "[green bold]BAT[/]" if cfg.get('auto_push_desc', True) else "[red bold]TAT[/]"

        g = Table.grid(padding=(0, 1))
        g.add_column(style="bold cyan", min_width=4, justify="right")
        g.add_column()

        g.add_row("", "[dim]── KET NOI ──────────────────────────────────────────────────────────[/]")
        g.add_row("[1]",  f"Username (chinh)      : {cfg['username'] or '[dim](khong dung)[/]'}")
        g.add_row("[2]",  f"Password (chinh)      : {mask(cfg['password'])}")
        g.add_row("[3]",  f"Enable pass (chinh)   : {mask(cfg['enable_password'])}")
        g.add_row("\\[k]",  f"Tai khoan phu (multi) : [bold]{len(cfg.get('credentials', []))} tai khoan[/]")
        g.add_row("[5]",  f"SSH port (uu tien)    : {cfg.get('ssh_port', 22)}")
        g.add_row("[6]",  f"Telnet port (du phong): {cfg.get('telnet_port', 23)}")
        g.add_row("", "")
        g.add_row("", "[dim]── FILE DU LIEU ────────────────────────────────────────────────────[/]")
        g.add_row("[8]",  f"File danh sach IP     : {cfg['ip_list']}")
        g.add_row("[9]",  f"File baseline DB      : {cfg['baseline_db']}")
        g.add_row("\\[a]",  f"File snapshot DB      : {cfg['snapshot_db']}")
        g.add_row("", "")
        g.add_row("", "[dim]── LUONG 1: GIAM SAT CAU HINH ────────────────────────────────────[/]")
        g.add_row("[4]",  f"Ten menu ep dung      : {cfg['menu_name_override'] or '[dim](tu dong do)[/]'}")
        g.add_row("\\[s]", f"Lich chay Thu thap    : [cyan]{_describe_scan_schedule(cfg)}[/]")
        g.add_row("[7]",  f"Chu ky interval (s)   : [bold cyan]{cfg['interval']}[/]  {s_note}")
        g.add_row("", "")
        g.add_row("", "[dim]── LUONG 2: VERIFY VAT LY ────────────────────────────────────────[/]")
        g.add_row("\\[b]", f"Tu dong Verify ngam   : {auto_v}")
        g.add_row("\\[c]", f"Tu dong Sua loi ngam  : {auto_p}")
        g.add_row("\\[d]", f"Lich chay Verify      : [cyan]{_describe_verify_schedule(cfg)}[/]")
        g.add_row("\\[v]", f"Chu ky interval (s)   : [bold cyan]{cfg.get('verify_interval', 3600)}[/]  {v_note}")
        g.add_row("\\[w]", f"Cho sau connect (s)   : [bold cyan]{cfg.get('verify_wait_after_connect', 1.5)}[/]")
        g.add_row("\\[m]", f"Timeout Verify (s)    : [bold cyan]{cfg.get('max_verify_duration', 300)}[/]")
        g.add_row("", "")
        g.add_row("\\[t]",   "[bold yellow]Thu ket noi nhanh (test credential)[/]")
        g.add_row("[0]",   "[bold red]Quay lai menu chinh[/]")

        _con.print()
        _con.print(Panel(g, title="[bold cyan] ⚙️  CAI DAT HE THONG [/]", border_style="cyan", padding=(1, 2)))
        choice = _con.input("[bold]Chon muc can sua[/]: ").strip().lower()

        if choice == "1":
            cfg["username"] = input("  Username moi (Enter de bo trong): ").strip()
        elif choice == "2":
            cfg["password"] = getpass.getpass("  Password moi: ").strip()
        elif choice == "3":
            cfg["enable_password"] = getpass.getpass("  Enable password moi: ").strip()
        elif choice == "k":
            _con.print("\n  [bold]Danh sach Tai khoan phu (Multi-Account)[/]")
            creds = cfg.setdefault("credentials", [])
            if not creds:
                _con.print("  [dim](Chua co tai khoan phu nao)[/]")
            for i, c in enumerate(creds):
                _con.print(f"  {i+1}. User: [cyan]{c.get('username')}[/] | Pass: ***")
            _con.print("\n  [a] Them tai khoan moi  |  [d] Xoa 1 tai khoan (theo so)  |  [c] Xoa tat ca  |  [0] Quay lai")
            sub = input("  Chon: ").strip().lower()
            if sub == "a":
                u = input("  Username: ").strip()
                p = getpass.getpass("  Password: ").strip()
                e = getpass.getpass("  Enable Password: ").strip()
                creds.append({"username": u, "password": p, "enable_password": e})
                _con.print("  [green]✓[/] Da them.")
            elif sub == "d":
                if not creds:
                    _con.print("  [yellow][!][/] Danh sach tai khoan phu dang trong.")
                else:
                    idx_raw = input(f"  Nhap so thu tu can xoa (1-{len(creds)}): ").strip()
                    if idx_raw.isdigit() and 1 <= int(idx_raw) <= len(creds):
                        removed = creds.pop(int(idx_raw) - 1)
                        _con.print(f"  [green]✓[/] Da xoa tai khoan: {removed.get('username')}")
                    else:
                        _con.print("  [red][!][/] So thu tu khong hop le.")
            elif sub == "c":
                cfg["credentials"] = []
                _con.print("  [green]✓[/] Da xoa toan bo tai khoan phu.")
        elif choice == "4":
            val = input(f"  Ten menu ep dung (de trong = tu dong do): ").strip()
            cfg["menu_name_override"] = val
        elif choice == "5":
            val = input("  SSH port moi: ").strip()
            if val.isdigit(): cfg["ssh_port"] = int(val)
        elif choice == "6":
            val = input("  Telnet port moi: ").strip()
            if val.isdigit(): cfg["telnet_port"] = int(val)
        elif choice == "7":
            val = input("  Chu ky thu thap (giay): ").strip()
            if val.isdigit(): cfg["interval"] = int(val)
        elif choice == "v":
            val = input("  Chu ky Verify vat ly (giay): ").strip()
            if val.isdigit(): cfg["verify_interval"] = int(val)
        elif choice == "w":
            val = input("  Cho sau connect (giay): ").strip()
            try:
                fval = float(val)
                if 0.1 <= fval <= 10.0: cfg["verify_wait_after_connect"] = round(fval, 2)
            except ValueError: pass
        elif choice == "m":
            val = input("  Timeout tong Verify (giay): ").strip()
            if val.isdigit() and int(val) >= 30: cfg["max_verify_duration"] = int(val)
        elif choice == "t":
            _test_connection(cfg)
            continue
        elif choice == "8":
            val = input("  File danh sach IP moi: ").strip()
            if val: cfg["ip_list"] = val
        elif choice == "9":
            val = input("  File baseline DB moi: ").strip()
            if val: cfg["baseline_db"] = val
        elif choice == "a":
            val = input("  File snapshot DB moi: ").strip()
            if val: cfg["snapshot_db"] = val
        elif choice == "b":
            cfg["auto_verify"] = not cfg.get("auto_verify", True)
        elif choice == "c":
            cfg["auto_push_desc"] = not cfg.get("auto_push_desc", True)
        elif choice == "d":
            _edit_verify_schedule(cfg)
        elif choice == "s":
            _edit_scan_schedule(cfg)
        elif choice == "0":
            save_config(config_path, cfg)
            _con.print(f"[green][*][/] Da luu cau hinh vao {config_path}")
            return
        else:
            _con.print("[yellow][!][/] Lua chon khong hop le.")
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

_ip_list_cache: dict = {"path": None, "mtime": 0, "hosts": []}

def load_ip_list_cached(path: str) -> list:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cache = _ip_list_cache
    if cache["path"] == path and cache["mtime"] == mtime:
        return cache["hosts"]
    hosts = load_ip_list(path)
    cache.update({"path": path, "mtime": mtime, "hosts": hosts})
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

_DB_INIT_CACHE = set()

def _init_db(path, table):
    conn = sqlite3.connect(path)
    if (path, table) in _DB_INIT_CACHE:
        return conn
        
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
    if "device_name" not in cols: conn.execute(f"ALTER TABLE {table} ADD COLUMN device_name TEXT")
    if "protocol" not in cols: conn.execute(f"ALTER TABLE {table} ADD COLUMN protocol TEXT DEFAULT 'telnet'")
    if "raw_key" not in cols: conn.execute(f"ALTER TABLE {table} ADD COLUMN raw_key TEXT")
    if "real_menu_name" not in cols: conn.execute(f"ALTER TABLE {table} ADD COLUMN real_menu_name TEXT")
    conn.commit()
    
    _DB_INIT_CACHE.add((path, table))
    return conn

def get_options_by_host(db_path, table, host):
    conn = _init_db(db_path, table)
    cur = conn.execute(
        f"SELECT menu_name, option_key, device_name, description, target_ip, target_port, protocol, "
        f"raw_key, real_menu_name FROM {table} WHERE host=?", (host,)
    )
    rows = cur.fetchall()
    conn.close()
    if not rows: return None, None, None
    menu_name   = rows[0][0]
    device_name = rows[0][2]
    options = {}
    for _mn, key, _dn, desc, ip, port, proto, raw_key, real_menu_name in rows:
        entry = {"description": desc, "ip": ip, "port": port, "protocol": proto}
        entry["_raw_key"] = raw_key if raw_key else key
        entry["_menu_name"] = real_menu_name if real_menu_name else _mn
        options[key] = entry
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
            f"target_ip, target_port, protocol, raw_key, real_menu_name, updated_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (host, menu_name, key, device_name, entry.get("description", ""), entry["ip"],
             entry.get("port", 23), entry.get("protocol", "telnet"),
             entry.get("_raw_key", key), entry.get("_menu_name", menu_name), now),
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
    if d["extra"]: _con.print(f"    [red]+ Option la (them): {', '.join(d['extra'])}[/]")
    if d["missing"]: _con.print(f"    [red]- Option bi mat: {', '.join(d['missing'])}[/]")
    if d["changed"]:
        _con.print(f"    [yellow]~ Option bi doi noi dung: {', '.join(d['changed'])}[/]")
        t = Table(box=rbox.SIMPLE_HEAD, header_style="bold dim", show_header=True)
        t.add_column("Option",  style="bold cyan", min_width=6)
        t.add_column("Baseline (chuan)", min_width=36)
        t.add_column("Hien tai", min_width=36)
        for key in d["changed"]:
            b, s = baseline[key], snapshot[key]
            t.add_row(f"{key}", f"{b.get('description','')} [dim]{_fmt_entry(b)}[/]",
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
        t.add_row(f"{key}", e.get("description", ""), f"[{col}]{proto}[/]://{e['ip']}:{port}")
    _con.print(t)


# ---------------------------------------------------------------------------
# Module Thu thập Menu (Đa Menu & Multi-Account)
# ---------------------------------------------------------------------------
MENU_NAME_RE = re.compile(r'^\s*menu\s+(\S+)\s+(?:text|command)\b', re.IGNORECASE | re.MULTILINE)
TEXT_RE       = re.compile(r'^\s*menu\s+(\S+)\s+text\s+(\S+)\s+(.+)', re.IGNORECASE)
CMD_TELNET_RE = re.compile(r'^\s*menu\s+(\S+)\s+command\s+(\S+)\s+telnet\s+(\S+)(?:\s+(\d+))?', re.IGNORECASE)
CMD_SSH_RE    = re.compile(r'^\s*menu\s+(\S+)\s+command\s+(\S+)\s+ssh\s+(?:-l\s+\S+\s+|\S+@)?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?:\s+(\d+))?', re.IGNORECASE)

def poll_host_multi(ip, cfg, timeout=10):
    """Tra ve (hostname, menu_name, options, state).
    state co the la:
        "ok"           -> lay du va parse duoc menu binh thuong.
        "no_menu"      -> KET NOI THANH CONG, lay du du lieu tu thiet bi,
                           nhung thiet bi khong co dong cau hinh 'menu ...' nao
                           (thuc su khong co menu, khong phai loi ket noi).
        "fetch_failed" -> KET NOI THANH CONG nhung KHONG lay duoc du lieu lenh
                           'show running-config | include menu' day du (timeout/
                           mat ket noi giua chung) - can thu lai, khac voi "no_menu".
    Neu ca 2/nhieu tai khoan deu khong ket noi duoc, raise exception cuoi cung
    (loi ket noi/xac thuc - khac hoan toan voi 2 state ben tren)."""
    menu_name_override = cfg.get("menu_name_override") or None
    creds = get_all_credentials(cfg)
    last_exc = None
    
    for c in creds:
        try:
            tn = connect_auto(
                ip, cfg.get("ssh_port", 22), cfg.get("telnet_port", 23),
                c["username"], c["password"], c["enable_password"], timeout=timeout
            )
            try:
                hostname = fetch_hostname(tn)
                tn.write("terminal length 0")
                tn.read_until("#", timeout=5)
                tn.write("show running-config | include menu")
                raw = tn.read_until_prompt(timeout=15)

                if tn.last_read_timed_out and not raw.strip():
                    # Ket noi/dang nhap thanh cong nhung khong doc duoc gi tu lenh
                    # show - phan biet voi truong hop thiet bi that su khong co menu.
                    return hostname, None, {}, "fetch_failed"

                detected_names = list(set(MENU_NAME_RE.findall(raw)))
                if menu_name_override:
                    if menu_name_override not in detected_names:
                        raise ValueError(f"Ten menu ep dung khong ton tai tren thiet bi.")
                    menu_names = [menu_name_override]
                else:
                    menu_names = detected_names

                if not menu_names:
                    if tn.last_read_timed_out:
                        # Doc duoc mot phan nhung bi cat ngang giua chung -> khong
                        # chac chan la thiet bi khong co menu, coi la fetch that bai.
                        return hostname, None, {}, "fetch_failed"
                    return hostname, None, {}, "no_menu"
                    
                all_texts = {}
                all_commands = {}
                
                for raw_line in raw.splitlines():
                    line = raw_line.strip()
                    m = TEXT_RE.match(line)
                    if m and m.group(1) in menu_names:
                        all_texts[(m.group(1), m.group(2).strip())] = m.group(3).strip()
                        continue
                        
                    m = CMD_TELNET_RE.match(line)
                    if m and m.group(1) in menu_names:
                        all_commands[(m.group(1), m.group(2).strip())] = {
                            "ip": m.group(3), "port": int(m.group(4)) if m.group(4) else 23, "protocol": "telnet"
                        }
                        continue
                        
                    m = CMD_SSH_RE.match(line)
                    if m and m.group(1) in menu_names:
                        all_commands[(m.group(1), m.group(2).strip())] = {
                            "ip": m.group(3), "port": int(m.group(4)) if m.group(4) else 22, "protocol": "ssh"
                        }

                final_options = {}
                for (m_name, cmd_k), cmd_data in all_commands.items():
                    if (m_name, f"[{cmd_k}]") in all_texts:
                        real_key, desc = f"[{cmd_k}]", all_texts[(m_name, f"[{cmd_k}]")]
                    elif (m_name, cmd_k) in all_texts:
                        real_key, desc = cmd_k, all_texts[(m_name, cmd_k)]
                    else:
                        real_key, desc = cmd_k, ""
                        
                    display_key = real_key[1:-1] if real_key.startswith("[") and real_key.endswith("]") else real_key
                    ui_key = f"{m_name} [{display_key}]" if len(menu_names) > 1 else display_key

                    final_options[ui_key] = {
                        "description": desc, "ip": cmd_data["ip"], "port": cmd_data["port"], "protocol": cmd_data["protocol"],
                        "_raw_key": real_key, "_menu_name": m_name,
                    }
                    
                return hostname, " + ".join(sorted(menu_names)), final_options, "ok"
            finally:
                try: tn.write("exit")
                except OSError: pass
                tn.close()
        except Exception as e:
            last_exc = e
            
    raise last_exc


# ---------------------------------------------------------------------------
# Module Deep Verify & Smart Clear Line (Multi-Account)
# ---------------------------------------------------------------------------

def clear_stuck_line(cfg, oob_ip, console_port):
    line_num = console_port - 2000 
    if line_num <= 0 or line_num > 200: return False
    
    for c in get_all_credentials(cfg):
        try:
            session = connect_auto(
                oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"], 
                c["username"], c["password"], c["enable_password"], timeout=5
            )
            session.write(f"clear line {line_num}")
            session.read_until("[confirm]", timeout=3)
            session.write("\n")
            session.read_until("#", timeout=3)
            session.close()
            return True
        except Exception:
            pass
    return False

def extract_hostname(output: str) -> str:
    output = _ANSI_STRIP_RE.sub('', output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    auth_seen = False
    for line in reversed(lines):
        if any(x in line for x in ["telnet ", "ssh ", "Trying ", "Open", "Connection refused", "disconnect", "clear line"]): continue
        if _CONN_ERR_RE.search(line): continue
        m = _HOSTNAME_PROMPT_RE.search(line)
        if m: return m.group(1)
        m_login = _HOSTNAME_LOGIN_RE.search(line)
        if m_login: return m_login.group(1)
        m_bsd = _HOSTNAME_BSD_RE.search(line)
        if m_bsd: return m_bsd.group(1)
        if re.search(r'Username:|Password:|login:', line, re.IGNORECASE): auth_seen = True
    if auth_seen: return "AUTH_REQUIRED"
    return None

def _truncate(text, width):
    text = "" if text is None else str(text)
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"

_VERIFY_STATUS_ORDER = {"CANH BAO": 0, "KHONG PIVOT": 1, "TIMEOUT": 2, "YEU CAU DANG NHAP": 3, "OK": 4}
_VERIFY_STATUS_LABEL = {"CANH BAO": "CANH BAO", "KHONG PIVOT": "KO PIVOT", "TIMEOUT": "TIMEOUT", "YEU CAU DANG NHAP": "YC DANG NHAP", "OK": "OK"}

def _build_verify_report(alias, oob_ip, own_hostname, results):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    counts = {}
    for r in results: counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = "   ".join(f"{_VERIFY_STATUS_LABEL[s]}: {counts.get(s, 0)}" for s in ("CANH BAO", "KHONG PIVOT", "TIMEOUT", "YEU CAU DANG NHAP", "OK") if counts.get(s, 0)) or "Khong co option nao duoc quet"
    headers = ["STT", "Option", "Trang thai", "Hostname thuc te", "Description", "Port", "Ghi chu"]
    max_w   = [4, 22, 12, 22, 32, 6, 36]
    ordered = sorted(enumerate(results), key=lambda p: (_VERIFY_STATUS_ORDER.get(p[1]["status"], 9), p[0]))
    rows = []
    for i, (_, r) in enumerate(ordered, start=1):
        rows.append([str(i), _truncate(r["key"], max_w[1]), _VERIFY_STATUS_LABEL.get(r["status"], r["status"]), _truncate(r.get("act_host") or "-", max_w[3]), _truncate(r.get("desc") or "-", max_w[4]), str(r.get("port", "") or ""), _truncate(r.get("note") or "", max_w[6])])
    widths = []
    for i, h in enumerate(headers):
        col_max = max([len(h)] + [len(row[i]) for row in rows]) if rows else len(h)
        widths.append(min(max(col_max, len(h)), max_w[i]))
    def fmt_row(cols): return "│ " + " │ ".join(cols[i].ljust(widths[i]) for i in range(len(cols))) + " │"
    def fmt_sep(left, mid, right): return left + mid.join("─" * (w + 2) for w in widths) + right
    table_width = sum(widths) + 3 * len(widths) + 1
    bar = "=" * max(table_width, 60)
    lines = [bar, f" BAO CAO DEEP VERIFY - OOB: {alias} ({oob_ip})", f" Thoi gian      : {now_str}", f" Hostname OOB   : {own_hostname or '(khong xac dinh duoc)'}", f" Tong so option : {len(results)}", f" Tom tat        : {summary}", bar, fmt_sep("┌", "┬", "┐"), fmt_row(headers), fmt_sep("├", "┼", "┤")]
    if rows:
        for row in rows: lines.append(fmt_row(row))
    else: lines.append("│ " + "(khong co option nao co description de kiem tra)".ljust(table_width - 4) + " │")
    lines.extend([fmt_sep("└", "┴", "┘"), bar, f" HOAN THANH luc {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", bar])
    return "\n".join(lines)


def run_deep_verify(cfg, alias, oob_ip, options, print_fn=None):
    if print_fn is None: print_fn = log_verify
    max_duration = float(cfg.get("max_verify_duration", 300))
    _verify_deadline = time.time() + max_duration
    print_fn(f"[*] Bat dau kiem tra vat ly (PIVOT) cho OOB: [bold]{alias}[/]")

    creds = get_all_credentials(cfg)
    own_hostname = None
    working_cred = creds[0]

    for c in creds:
        try:
            tn = connect_auto(oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"], c["username"], c["password"], c["enable_password"], timeout=6)
            own_hostname = fetch_hostname(tn)
            tn.close()
            working_cred = c
            break
        except Exception:
            pass

    own_hostname_clean = (own_hostname or "").strip().lower()
    os.makedirs("verify-logs", exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join("verify-logs", f"Verify_{alias}_{ts_str}.log")

    results = [] 
    session = None
    
    def get_session():
        nonlocal session
        if session is None:
            session = connect_auto(oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"], working_cred["username"], working_cred["password"], working_cred["enable_password"], timeout=8)
            session.write("terminal length 0")
            session.read_until("#", timeout=2)
        return session
        
    def reset_session():
        nonlocal session
        if session: session.close()
        session = None

    def check_port_via_oob(t_ip, t_port, proto):
        try: s = get_session()
        except Exception: raise RuntimeError("Khong the ket noi toi OOB")
        cmd = f"ssh -l admin {t_ip}" if proto == "ssh" else f"telnet {t_ip} {t_port}"
        s.write(cmd)
        time.sleep(float(cfg.get("verify_wait_after_connect", 1.5))) 
        s.write("\r\n\r\n") 
        out = s.read_until([">", "#", "login:", "Username:", "Password:", "Connection refused", "refused", "unknown"], timeout=5)
        try:
            s.write_raw(b"\x1ex")
            after_esc = s.read_until("#", timeout=2)
            if "#" in after_esc or "#" in out:
                s.write("disconnect")
                cfm = s.read_until(["[confirm]", "#", "No connection"], timeout=2)
                if "[confirm]" in cfm:
                    s.write("")
                    s.read_until("#", timeout=2)
            else: reset_session()
        except Exception:
            reset_session()
        return out

    def clear_line_via_oob(t_port):
        line_num = t_port - 2000
        if line_num <= 0: return False
        try:
            s = get_session()
            s.write(f"clear line {line_num}")
            cfm = s.read_until(["[confirm]", "#"], timeout=3)
            if "[confirm]" in cfm:
                s.write("")
                s.read_until("#", timeout=3)
            return True
        except Exception:
            reset_session()
            return False

    for key, opt in options.items():
        if time.time() > _verify_deadline:
            print_fn(f"[yellow][!][/] {alias}: Vuot timeout tong {int(max_duration)}s. Bo qua phan con lai.")
            break
        desc = opt.get("description", "")
        if not desc: continue
            
        target_ip = opt.get("ip")
        port = opt.get("port", 23)
        proto = opt.get("protocol", "telnet")
        
        act_host, conn_error, note_parts = None, "", []

        try:
            output = check_port_via_oob(target_ip, port, proto)
            act_host = extract_hostname(output)
        except Exception as e:
            conn_error = str(e)
            
        if not act_host:
            if port > 2000:
                line_to_clear = port - 2000
                print_fn(f"[yellow][!][/] {alias} (Opt {key}): Dang clear line {line_to_clear}...")
                note_parts.append(f"Da thu clear line {line_to_clear}")
                if clear_line_via_oob(port):
                    time.sleep(2) 
                    try:
                        output = check_port_via_oob(target_ip, port, proto)
                        act_host = extract_hostname(output)
                    except Exception as e:
                        conn_error = str(e)
                else: note_parts.append("Khong clear duoc line")
            else: note_parts.append("Port la Direct Access, bo qua clear line")

        note = "; ".join(note_parts)
        if not act_host:
            print_fn(f"[dim][-][/] {alias} (Opt {key}): TIMEOUT hoac Loi mang")
            results.append({"key": key, "status": "TIMEOUT", "act_host": None, "desc": desc, "port": port, "note": note})
            continue
        if act_host == "AUTH_REQUIRED":
            print_fn(f"[yellow][?][/] {alias} (Opt {key}): Thiet bi yeu cau dang nhap")
            results.append({"key": key, "status": "YEU CAU DANG NHAP", "act_host": None, "desc": desc, "port": port, "note": note})
            continue
            
        desc_clean = _DESC_PREFIX_RE.sub('', desc).strip().lower()
        act_host_clean = act_host.strip().lower()

        if own_hostname_clean and act_host_clean == own_hostname_clean:
            print_fn(f"[dim][-][/] {alias} (Opt {key}): Khong pivot duoc (van o console OOB)")
            note = "; ".join(note_parts + [f"Van o console OOB ({own_hostname})"])
            results.append({"key": key, "status": "KHONG PIVOT", "act_host": act_host, "desc": desc, "port": port, "note": note})
            continue

        if act_host_clean == desc_clean or hostname_matches_description(act_host, desc_clean):
            print_fn(f"[green][OK][/] {alias} (Opt {key}): Khop ({act_host})")
            results.append({"key": key, "status": "OK", "act_host": act_host, "desc": desc, "port": port, "note": note})
        else:
            print_fn(f"[bold red]CANH BAO[/] Thiet bi noi line ([yellow]{act_host}[/]) khac description ([yellow]{desc_clean}[/]) tai Opt {key}!")
            results.append({"key": key, "status": "CANH BAO", "act_host": act_host, "desc": desc, "port": port, "note": note})

    print_fn(f"[green]✓[/] Hoan thanh Verify cho OOB: [bold]{alias}[/]\n")
    if session: session.close()

    report_text = _build_verify_report(alias, oob_ip, own_hostname, results)
    with open(log_file_path, "w", encoding="utf-8") as f: f.write(report_text)
    try:
        import json
        with open(log_file_path.replace(".log", ".json"), "w", encoding="utf-8") as f: json.dump(results, f, ensure_ascii=False, indent=2)
    except Exception: pass
    return results

# ---------------------------------------------------------------------------
# Module Self-Healing (Tự động phục hồi)
# ---------------------------------------------------------------------------

def check_ip_collision(cfg, target_ip, current_oob_ip):
    conn = _init_db(cfg["baseline_db"], "baseline_menu")
    cur = conn.execute("SELECT host FROM baseline_menu WHERE target_ip=?", (target_ip,))
    hosts = {row[0] for row in cur.fetchall()}
    conn.close()
    return len(hosts - {current_oob_ip}) > 0

def process_push_and_reverify(cfg, alias, oob_ip, baseline, verify_results, print_fn=None):
    if print_fn is None: print_fn = log_verify
    if not cfg.get("auto_push_desc", True): return

    warnings = [r for r in verify_results if r["status"] == "CANH BAO"]
    if not warnings: return

    updates_list, push_log_entries = [], []
    mn, dn, _ = get_options_by_host(cfg["baseline_db"], "baseline_menu", oob_ip)
    if not mn: return

    for w in warnings:
        key, act_host, opt = w["key"], w["act_host"], baseline.get(w["key"], {})
        target_ip = opt.get("ip")
        if check_ip_collision(cfg, target_ip, oob_ip):
            print_fn(f"[yellow][!][/] {alias} (Opt {key}): Nghi ngo trung IP tren nhieu OOB. Bo qua tu dong sua.")
            continue
        new_desc = f"----> {act_host}"
        real_menu_name, real_key = opt.get("_menu_name"), opt.get("_raw_key")
        if not real_menu_name or not real_key:
            print_fn(f"[yellow][!][/] {alias} (Opt {key}): Baseline cu khong co thong tin key that, bo qua.")
            continue
        updates_list.append((real_menu_name, real_key, new_desc))
        push_log_entries.append({"key": key, "real_menu_name": real_menu_name, "real_key": real_key, "target_ip": target_ip, "old": w["desc"], "new": new_desc})

    if not updates_list: return
    print_fn(f"[*] Dang tu dong PUSH sua loi {len(updates_list)} option cho {alias}...")
    
    success = False
    for c in get_all_credentials(cfg):
        success = push_menu_descriptions(oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"], c["username"], c["password"], c["enable_password"], updates_list, timeout=10)
        if success: break

    if not success:
        print_fn(f"[red][LOI][/] {alias}: Push cau hinh that bai (Cisco tu choi hoac sai authen).")
        return

    os.makedirs("push-logs", exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join("push-logs", f"Push_{alias}_{ts_str}.log"), "w", encoding="utf-8") as f:
        f.write(f"=== PUSH LOG TỰ ĐỘNG: {alias} ({oob_ip}) ===\nThoi gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        for entry in push_log_entries:
            baseline[entry["key"]]["description"] = entry["new"] 
            f.write(f"- Option [{entry['key']}] (Target IP: {entry['target_ip']}):\n  + Cu : {entry['old']}\n  + Moi: {entry['new']}\n  + REVERT CMD: menu {entry['real_menu_name']} text {entry['real_key']} {entry['old']}\n\n")

    save_options(cfg["baseline_db"], "baseline_menu", oob_ip, mn, dn, baseline)
    print_fn(f"[green]✓[/] Da cap nhat cau hinh & Baseline (Khong Auto-save write memory).")
    print_fn(f"[*] Tu dong Re-Verify lai cac option vua sua tren {alias}...")
    run_deep_verify(cfg, alias, oob_ip, {entry["key"]: baseline[entry["key"]] for entry in push_log_entries}, print_fn=print_fn)

def _thread_verify_and_push(cfg, alias, ip, snapshot):
    res = run_deep_verify(cfg, alias, ip, snapshot)
    process_push_and_reverify(cfg, alias, ip, snapshot, res)


# ---------------------------------------------------------------------------
# Vòng lặp giám sát (Daemon Thread)
# ---------------------------------------------------------------------------

def run_verify_daemon(cfg):
    log_verify(f"[green][START][/] Khoi dong Verify vat ly - lich: {_describe_verify_schedule(cfg)}.")
    time.sleep(15)
    while True:
        if cfg.get("auto_verify", True):
            hosts = load_ip_list(cfg["ip_list"])
            if hosts:
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    for ip, alias in hosts:
                        _mn, _dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
                        if baseline:
                            executor.submit(_thread_verify_and_push, cfg, alias, ip, baseline)
        else:
            log_verify("[dim][zzz] Tinh nang Verify ngam dang bi TAT trong cau hinh. Dang cho...[/]")

        schedule_mode = cfg.get("verify_schedule_mode", "interval")
        if schedule_mode in ("daily", "weekly"):
            next_run = compute_next_scheduled_run(schedule_mode, cfg.get("verify_schedule_time", "01:00"), cfg.get("verify_schedule_weekday", "mon"))
            sleep_seconds = max(1, int((next_run - datetime.now()).total_seconds()))
            log_verify(f"[dim][zzz] Lan Verify tiep theo: {next_run.strftime('%Y-%m-%d %H:%M')} (con {sleep_seconds}s)...[/]")
        else:
            sleep_seconds = cfg.get("verify_interval", 3600)
            log_verify(f"[dim][zzz] Dang cho {sleep_seconds}s cho dot Verify tiep theo...[/]")
        time.sleep(sleep_seconds)

def _scan_wait(cfg):
    mode = cfg.get("scan_schedule_mode", "interval")
    if mode in ("daily", "weekly"):
        next_run = compute_next_scheduled_run(mode, cfg.get("scan_schedule_time", "01:00"), cfg.get("scan_schedule_weekday", "mon"))
        sleep_seconds = max(1, int((next_run - datetime.now()).total_seconds()))
        log_oob(f"[dim][zzz] Lan Thu thap tiep theo: {next_run.strftime('%Y-%m-%d %H:%M')} (con {sleep_seconds}s)...[/]")
    else:
        sleep_seconds = cfg["interval"]
        log_oob(f"[dim][zzz] Dang cho {sleep_seconds}s de quet lai...[/]")
    time.sleep(sleep_seconds)

def _config_reload_loop(cfg, config_path, check_every=5):
    try: last_mtime = os.path.getmtime(config_path)
    except OSError: last_mtime = 0
    while True:
        time.sleep(check_every)
        try: mtime = os.path.getmtime(config_path)
        except OSError: continue
        if mtime == last_mtime: continue
        last_mtime = mtime
        new_cfg = load_config(config_path)
        with ui_lock: cfg.update(new_cfg)
        log_oob("[cyan][CONFIG][/] Phat hien oob_config.json thay doi — da tu dong ap dung cau hinh moi.")

def run_daemon(cfg, config_path=None):
    global _live_ui
    _con.print(Panel("[bold green]OOB MONITOR DAEMON[/]\n[dim]Dang giam sat lien tuc. Nhan Ctrl+C de dung.[/]", border_style="green"))
    
    creds = get_all_credentials(cfg)
    if not any(c.get("password") for c in creds):
        _con.print("[red][!][/] Chua cau hinh password! Vui long cau hinh truoc.")
        return

    update_ui()
    threading.Thread(target=run_verify_daemon, args=(cfg,), daemon=True).start()
    threading.Thread(target=_daemon_heartbeat_loop, daemon=True).start()
    if config_path: threading.Thread(target=_config_reload_loop, args=(cfg, config_path), daemon=True).start()
    
    with Live(layout, refresh_per_second=4, screen=False) as live:
        _live_ui = live
        log_oob(f"[green][START][/] Khoi dong Thu thap cau hinh - lich: {_describe_scan_schedule(cfg)}.")
        
        try:
            while True:
                hosts = load_ip_list_cached(cfg["ip_list"])
                if not hosts:
                    log_oob("[yellow][!][/] Danh sach IP trong. Doi them thiet bi...")
                    _scan_wait(cfg)
                    continue

                for ip, alias in hosts:
                    log_oob(f"[cyan][PING][/] [bold]{alias}[/] ({ip}) ...")
                    alive = ping_host(ip)
                    save_device_status(ip, alias=alias, ping=alive)
                    if not alive:
                        log_oob(f"[red][!][/] {alias} ({ip}): Khong ping duoc (thiet bi co the dang down). Bo qua vong nay.")
                        continue

                    log_oob(f"[cyan][SCAN][/] [bold]{alias}[/] ({ip}) ...")
                    try:
                        hostname, menu_name, snapshot, menu_state = poll_host_multi(ip, cfg, timeout=cfg.get("interval", 30))
                    except Exception as exc:
                        save_device_status(ip, alias=alias, menu_state="conn_failed")
                        log_oob(f"[red][LOI][/] {alias} ({ip}): {exc}")
                        continue

                    save_device_status(ip, alias=alias, menu_state=menu_state)
                    hn_label = f"hostname=[bold]{hostname}[/]" if hostname else "hostname=?"
                    menu_n   = len(snapshot)

                    if menu_state == "fetch_failed":
                        log_oob(f"[yellow][!][/] {alias}: Da ket noi nhung KHONG lay duoc thong tin menu (timeout doc du lieu). Se thu lai vong sau.")
                        continue

                    if menu_state == "no_menu" or not menu_name or not snapshot:
                        log_oob(f"[yellow][!][/] {alias}: Thiet bi khong co cau hinh menu (da xac nhan, khong phai loi ket noi).")
                        continue

                    save_options(cfg["snapshot_db"], "snapshot_menu", ip, menu_name, hostname, snapshot)
                    _mn, _dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)

                    if baseline is None:
                        live.stop() 
                        _con.print(f"\n  [yellow][?][/] Chua co baseline cho [bold]{alias}[/] ({ip}).")
                        _con.print(f"      {hn_label} | {menu_n} option:")
                        print_options(snapshot)
                        
                        save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
                        log_baseline_change(alias, ip, "TAO MOI BASELINE")
                        _con.print(f"  [green][OK][/] Da TU DONG luu baseline cho {alias}.")
                        
                        if cfg.get("auto_verify", True):
                            threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
                            
                        live.start() 
                        continue

                    if options_equal(baseline, snapshot):
                        log_oob(f"[green][OK][/] {alias}: Khop voi baseline ({menu_n} option).")
                        continue

                    live.stop()
                    _con.rule(f"[bold red]CANH BAO  {alias} ({ip}) KHAC baseline![/]", style="red")
                    print_diff(baseline, snapshot)
                    
                    save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
                    log_baseline_change(alias, ip, "CAP NHAT BASELINE")
                    _con.print(f"  [green][OK][/] Da TU DONG cap nhat baseline moi cho {alias}.")
                    
                    if cfg.get("auto_verify", True):
                        threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
                    
                    live.start() 

                _scan_wait(cfg)
        except KeyboardInterrupt:
            pass
    _con.print("\n[yellow][STOP][/] Da nhan Ctrl+C. Dung Daemon.")


# ---------------------------------------------------------------------------
# Chuc nang Xem/Tim Kiem & Quản lý
# ---------------------------------------------------------------------------

def view_latest_verify_log():
    log_dir = "verify-logs"
    if not os.path.exists(log_dir):
        _con.print("\n  [yellow][!][/] Chua co thu muc 'verify-logs'. Chua co ket qua quet nao.")
        return
    files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith('.log')]
    if not files:
        _con.print("\n  [yellow][!][/] Thu muc 'verify-logs' dang trong.")
        return
    latest_file = max(files, key=os.path.getmtime)
    try:
        with open(latest_file, "r", encoding="utf-8") as f: content = f.read()
        _con.print()
        _con.print(Panel(content, title=f"[bold magenta]📝 LOG VERIFY GAN NHAT: {os.path.basename(latest_file)}[/]", border_style="magenta", padding=(1, 2)))
    except Exception as e:
        _con.print(f"  [red][LOI][/] Khong doc duoc file {latest_file}: {e}")

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

    checked_count = 0
    unchecked_count = 0

    for ip, alias in hosts:
        _mn, device_name, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        updated_at = get_updated_at_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if device_name is None:
            _mn, device_name, _ = get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
        
        if baseline:
            bl = "[green]✓ Co[/]"
            checked_count += 1
        else:
            bl = "[yellow]✗ Chua[/]"
            unchecked_count += 1
            
        table.add_row(alias, ip, device_name or "[dim](chua ro)[/]", bl, updated_at or "[dim]-[/]")

    _con.print()
    _con.print(f"  [cyan]TONG KET: Co [bold]{len(hosts)}[/] thiet bi | Đã check (co baseline): [green bold]{checked_count}[/] | Chưa check: [yellow bold]{unchecked_count}[/][/]")
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
    if not found: _con.print("  [dim](Chua co baseline nao duoc xac nhan)[/]")
    _con.print(Rule(style="cyan"))

def search_device(cfg):
    query = input("  Nhap IP hoac ten thiet bi can tim: ").strip().lower()
    if not query: return
    hosts = load_ip_list(cfg["ip_list"])
    found = []
    for ip, alias in hosts:
        _mn, dn, source = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if source is None: _mn, dn, source = get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
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
        _con.print(f"    [cyan]→[/] OOB: [bold]{alias}[/] ({ip} - host: {dn}) | Opt [bold cyan]{key}[/] {entry.get('description', '')} [dim]→ {pr}://{entry['ip']}:{pt}[/]")

def scan_specific_devices(cfg):
    targets_input = _con.input("  [cyan]Nhap IP/Alias can quet (cach nhau dau phay, de trong quet TAT CA)[/]: ").strip()
    all_hosts = load_ip_list(cfg["ip_list"])
    if not all_hosts:
        _con.print("  [yellow][!][/] Danh sach IP hien dang trong. Vui long them IP truoc.")
        return

    hosts_to_scan = []
    if not targets_input: hosts_to_scan = all_hosts
    else:
        target_list = [t.strip().lower() for t in targets_input.split(",")]
        for ip, alias in all_hosts:
            if ip.lower() in target_list or alias.lower() in target_list: hosts_to_scan.append((ip, alias))

    if not hosts_to_scan:
        _con.print("  [yellow][!][/] Khong co IP/Alias nao khop voi danh sach 'oob_ips.txt'.")
        return

    _con.print(f"\n  [green][*] Bat dau quet Cấu hình tuc thi {len(hosts_to_scan)} thiet bi...[/]")
    for ip, alias in hosts_to_scan:
        _con.print(f"\n  [cyan][PING][/] [bold]{alias}[/] ({ip}) ...")
        alive = ping_host(ip)
        save_device_status(ip, alias=alias, ping=alive)
        if not alive:
            _con.print(f"  [red][!][/] {alias} ({ip}): Khong ping duoc (thiet bi co the dang down). Bo qua.")
            continue

        _con.print(f"  [cyan][SCAN CONFIG][/] [bold]{alias}[/] ({ip}) ...")
        try:
            hostname, menu_name, snapshot, menu_state = poll_host_multi(ip, cfg, timeout=10)
        except Exception as exc:
            save_device_status(ip, alias=alias, menu_state="conn_failed")
            _con.print(f"  [red][LOI][/] {alias} ({ip}): {exc}")
            continue

        save_device_status(ip, alias=alias, menu_state=menu_state)
        hn_label = f"hostname=[bold]{hostname}[/]" if hostname else "hostname=?"
        menu_n   = len(snapshot)

        if menu_state == "fetch_failed":
            _con.print(f"  [yellow][!][/] {alias}: Da ket noi nhung KHONG lay duoc thong tin menu (timeout doc du lieu). Nen thu lai.")
            continue

        if menu_state == "no_menu" or not menu_name or not snapshot:
            _con.print(f"  [yellow][!][/] {alias}: Thiet bi khong co cau hinh menu (da xac nhan, khong phai loi ket noi).")
            continue

        save_options(cfg["snapshot_db"], "snapshot_menu", ip, menu_name, hostname, snapshot)
        _mn, _dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)

        if baseline is None:
            _con.print(f"  [yellow][?][/] Chua co baseline cho [bold]{alias}[/] ({ip}).")
            _con.print(f"      {hn_label} | {menu_n} option:")
            print_options(snapshot)
            
            save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
            log_baseline_change(alias, ip, "TAO MOI BASELINE")
            _con.print(f"  [green][OK][/] Da TU DONG luu baseline cho {alias}.")
            
            if cfg.get("auto_verify", True):
                threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
            continue

        if options_equal(baseline, snapshot):
            _con.print(f"  [green][OK][/] {alias}: Khop voi baseline ({menu_n} option).")
            if cfg.get("auto_verify", True):
                threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
            continue

        _con.rule(f"[bold red]CANH BAO  {alias} ({ip}) KHAC baseline![/]", style="red")
        print_diff(baseline, snapshot)
        
        save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
        log_baseline_change(alias, ip, "CAP NHAT BASELINE")
        _con.print(f"  [green][OK][/] Da TU DONG cap nhat baseline moi cho {alias}.")
        
        if cfg.get("auto_verify", True):
            threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()

def verify_specific_devices(cfg):
    targets_input = _con.input("  [cyan]Nhap IP/Alias can Verify (cach nhau dau phay, de trong quet TAT CA)[/]: ").strip()
    all_hosts = load_ip_list(cfg["ip_list"])
    if not all_hosts:
        _con.print("  [yellow][!][/] Danh sach IP hien dang trong. Vui long them IP truoc.")
        return

    hosts_to_scan = []
    if not targets_input: hosts_to_scan = all_hosts
    else:
        target_list = [t.strip().lower() for t in targets_input.split(",")]
        for ip, alias in all_hosts:
            if ip.lower() in target_list or alias.lower() in target_list: hosts_to_scan.append((ip, alias))

    if not hosts_to_scan: return
    _con.print(f"\n  [green][*] Bat dau Verify vat ly tuc thi {len(hosts_to_scan)} thiet bi...[/]")
    def cli_print(msg): _con.print(f"    {msg}")
    
    results_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_alias = {}
        for ip, alias in hosts_to_scan:
            _con.print(f"\n  [cyan][VERIFY][/] [bold]{alias}[/] ({ip}) ...")
            _mn, _dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
            if not baseline:
                _con.print(f"  [yellow][!][/] OOB nay chua co Baseline. Vui long quet cau hinh truoc (Option 7)!")
                continue
            future = executor.submit(run_deep_verify, cfg, alias, ip, baseline, cli_print)
            future_to_alias[future] = (alias, ip, baseline)

        for future in concurrent.futures.as_completed(future_to_alias):
            alias, ip, baseline = future_to_alias[future]
            try: 
                # ?? S?A T?I ??Y: ??y baseline v?o ph?n Value thay v? Key
                results_map[(alias, ip)] = (baseline, future.result())
            except Exception as exc: 
                _con.print(f"  [red][LOI][/] {alias}: {exc}")

    # ?? S?A T?I ??Y: Unpack ??ng c?u tr?c d? li?u m?i c?a results_map
    for (alias, ip), (baseline, results) in results_map.items():
        if any(r["status"] == "CANH BAO" for r in results):
            ans = _con.input(f"\n  [bold yellow]Phat hien sai lech tren {alias}. Tu dong PUSH sua Description? (y/N)[/]: ").strip().lower()
            if ans == 'y': process_push_and_reverify(cfg, alias, ip, baseline, results, print_fn=cli_print)
            else: _con.print(f"  [dim]Da bo qua viec sua Description cho {alias}.[/]")


# ---------------------------------------------------------------------------
# Import / Export Excel
# ---------------------------------------------------------------------------
def _parse_verify_logs_for_status(max_age_hours: float = 24.0) -> dict:
    log_dir = "verify-logs"
    if not os.path.exists(log_dir): return {}
    cutoff = time.time() - max_age_hours * 3600
    alias_files: dict = {}
    for fname in os.listdir(log_dir):
        if not fname.endswith('.json') or not fname.startswith('Verify_'): continue
        body = fname[len('Verify_'):-len('.json')]
        if len(body) < 17: continue
        alias, fpath = body[:-16], os.path.join(log_dir, fname)
        mtime = os.path.getmtime(fpath)
        if mtime < cutoff: continue
        if alias not in alias_files or mtime > alias_files[alias][1]: alias_files[alias] = (fpath, mtime)

    STATUS_MAP = {"OK": "OK", "CANH BAO": "CANH BAO", "KO PIVOT": "KHONG PIVOT", "TIMEOUT": "TIMEOUT", "YC DANG NHAP": "YEU CAU DANG NHAP"}
    result: dict = {}
    import json
    for alias, (fpath, _) in alias_files.items():
        try:
            with open(fpath, "r", encoding="utf-8") as f: data = json.load(f)
        except Exception: continue
        for item in data:
            opt_key = item.get("key")
            if not opt_key: continue
            status_raw = item.get("status", "")
            act_host = item.get("act_host")
            result[(alias, opt_key)] = {"status": STATUS_MAP.get(status_raw, status_raw), "act_host": act_host if act_host not in ('-', '') else None}
    return result

def import_from_excel(cfg):
    try: import openpyxl
    except ImportError: return _con.print("  [red][!][/] Thieu thu vien openpyxl. Cai dat bang lenh: pip install openpyxl")
    file_path = _con.input("  [cyan]Duong dan file Excel (.xlsx)[/]: ").strip()
    if not file_path or not os.path.exists(file_path): return _con.print(f"  [red][!][/] Khong tim thay file.")
    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
    except Exception as e: return _con.print(f"  [red][!][/] Khong doc duoc: {e}")

    existing = load_ip_list(cfg["ip_list"])
    existing_ips = {h[0] for h in existing}
    added = skipped_dup = skipped_invalid = 0

    try:
        with open(cfg["ip_list"], "a", encoding="utf-8") as f:
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row or row[0] is None: continue
                ip_raw = str(row[0]).strip()
                alias = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ip_raw
                if not _IP_RE.match(ip_raw):
                    skipped_invalid += 1; continue
                if ip_raw in existing_ips:
                    skipped_dup += 1; continue
                f.write(f"{ip_raw} {alias}\n")
                existing_ips.add(ip_raw)
                added += 1
    finally:
        try: wb.close()
        except: pass
    _con.print(f"\n  [green]✓[/] Da them {added}, bo qua {skipped_dup} trung, bo qua {skipped_invalid} khong hop le.")

def export_menu_report(cfg):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError: return _con.print("  [red][!][/] Thieu thu vien openpyxl. (pip install openpyxl)")

    hosts = load_ip_list(cfg["ip_list"])
    if not hosts: return _con.print("  [yellow][!][/] Danh sach IP dang trong.")
    _con.print("  [cyan][*][/] Dang xuat bao cao Excel...")
    verify_st = _parse_verify_logs_for_status()
    dev_status = load_device_status()

    def _ping_label(ip):
        p = dev_status.get(ip, {}).get("ping")
        if p is True: return "Song (OK)"
        if p is False: return "Down (khong ping duoc)"
        return "Chua kiem tra"

    def _menu_state_label(ip):
        ms = dev_status.get(ip, {}).get("menu_state")
        if ms == "conn_failed": return "Loi ket noi/xac thuc"
        return MENU_STATE_LABELS.get(ms, "Chua kiem tra")

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Chi tiet"
    h1 = ["OOB IP", "OOB Alias", "Ping", "Trang thai Menu", "OOB Hostname", "Menu Name", "Option Key", "Description", "Target IP", "Target Port", "Protocol", "Desc Status", "Ghi chu"]
    for ci, h in enumerate(h1, 1): ws1.cell(1, ci, h).font = Font(bold=True)
    
    ri, summary = 2, []
    for ip, alias in hosts:
        ping_lbl, menu_state_lbl = _ping_label(ip), _menu_state_label(ip)
        mn, device_name, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if baseline is None:
            for ci, val in enumerate([ip, alias, ping_lbl, menu_state_lbl, "(chua co baseline)", "", "", "", "", "", "", "", ""], 1): ws1.cell(ri, ci, val)
            ri += 1
            summary.append({"alias": alias, "ip": ip, "ping": ping_lbl, "menu_state": menu_state_lbl, "hn": "", "mn": "", "total": 0, "match": 0, "wrong": 0, "unverif": 0, "no_conn": 0, "no_desc": 0})
            continue

        hn = device_name or ""
        cnt = dict(match=0, wrong=0, unverif=0, no_conn=0, no_desc=0)
        for opt_key in sorted(baseline):
            opt = baseline[opt_key]
            desc, t_ip, t_port, proto = opt.get("description", ""), opt.get("ip", ""), opt.get("port", ""), opt.get("protocol", "telnet")
            if not desc:
                slabel, note = "Khong co desc", "O description trong"
                cnt["no_desc"] += 1
            else:
                vr = verify_st.get((alias, opt_key))
                if vr is None:
                    slabel, note = "Chua Verify", "Chua co du lieu verify"
                    cnt["unverif"] += 1
                elif vr["status"] == "OK":
                    slabel, note = "OK - Khop", f"Hostname thuc te: {vr.get('act_host', '')}"
                    cnt["match"] += 1
                elif vr["status"] == "CANH BAO":
                    slabel, note = "SAI - Sai desc", f"Hostname thuc: {vr.get('act_host', '')}"
                    cnt["wrong"] += 1
                elif vr["status"] in ("TIMEOUT", "KHONG PIVOT"):
                    slabel, note = "Khong ket noi duoc", f"Trang thai: {vr['status']}"
                    cnt["no_conn"] += 1
                else:
                    slabel, note = f"? {vr['status']}", vr.get("status", "")
                    cnt["unverif"] += 1

            for ci, val in enumerate([ip, alias, ping_lbl, menu_state_lbl, hn, mn or "", opt_key, desc, t_ip, t_port, proto, slabel, note], 1): ws1.cell(ri, ci, val)
            ri += 1
        summary.append({"alias": alias, "ip": ip, "ping": ping_lbl, "menu_state": menu_state_lbl, "hn": hn, "mn": mn or "", "total": len(baseline), **cnt})

    ws2 = wb.create_sheet("Tom tat")
    for ci, h in enumerate(["OOB Alias", "OOB IP", "Ping", "Trang thai Menu", "Hostname OOB", "Menu", "Tong Option", "OK Khop", "SAI", "Chua Verify", "Khong KN", "Khong Desc"], 1): ws2.cell(1, ci, h).font = Font(bold=True)
    for ri2, sd in enumerate(summary, 2):
        for ci, val in enumerate([sd["alias"], sd["ip"], sd["ping"], sd["menu_state"], sd["hn"], sd["mn"], sd["total"], sd["match"], sd["wrong"], sd["unverif"], sd["no_conn"], sd["no_desc"]], 1): ws2.cell(ri2, ci, val)

    os.makedirs("reports", exist_ok=True)
    out = os.path.join("reports", f"OOB_Menu_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    wb.save(out)
    _con.print(f"\n  [green]✓[/] Da xuat bao cao: [bold]{out}[/]")


# ---------------------------------------------------------------------------
# Daemon Heartbeat
# ---------------------------------------------------------------------------
DAEMON_PID_FILE = "daemon.pid"

def _daemon_heartbeat_loop():
    while True:
        try:
            with open(DAEMON_PID_FILE, "w") as f: f.write(f"{os.getpid()}\n{datetime.now().isoformat()}\n")
        except OSError: pass
        time.sleep(30)

def _get_daemon_status() -> str:
    try:
        with open(DAEMON_PID_FILE, "r") as f: lines = f.read().splitlines()
        age = (datetime.now() - datetime.fromisoformat(lines[1])).total_seconds()
        if age < 90: return f"[green bold]RUNNING[/] [dim](PID {lines[0]})[/]"
        return f"[yellow bold]STALE[/]"
    except Exception: return "[dim]KHONG RO[/]"

def _show_menu(cfg):
    hosts_n = len(load_ip_list_cached(cfg["ip_list"]))
    user = f"[bold]{cfg['username']}[/]" if cfg["username"] else "[dim yellow](chua dat)[/]"
    menu_label = cfg['menu_name_override'] or "tu dong do"
    
    info = Text.from_markup(f"Thiet bi : [bold]{hosts_n}[/]   Menu: [bold]{menu_label}[/]   User: {user} (+{len(cfg.get('credentials', []))} phu)\nDaemon   : {_get_daemon_status()}")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", min_width=5, justify="right"); grid.add_column(min_width=40)
    grid.add_row("[1]", "Them thiet bi OOB"); grid.add_row("[2]", "Xoa thiet bi OOB"); grid.add_row("\\[i]", "[cyan]Import danh sach OOB tu file Excel (.xlsx)[/]")
    grid.add_row("", ""); grid.add_row("[3]", "Cau hinh (username/password/port/multi-account...)")
    grid.add_row("[4]", "Xem danh sach thiet bi"); grid.add_row("[5]", "Xem baseline (Chuan)"); grid.add_row("[6]", "Tim kiem thiet bi")
    grid.add_row("[7]", "[bold green]Quet kiem tra Cau hinh tuc thi (Chi dinh hoac Tat ca)[/]")
    grid.add_row("[8]", "[bold magenta]Deep Verify Vat ly tuc thi (Chi dinh hoac Tat ca)[/]")
    grid.add_row("[9]", "[dim magenta]Xem ket qua Verify vat ly gan nhat[/]")
    grid.add_row("\\[e]", "[yellow]Xuat bao cao menu OOB ra Excel (tat ca thiet bi)[/]")
    grid.add_row("", ""); grid.add_row("[0]", "[bold red]Thoat[/]")

    _con.print()
    _con.print(Panel(Group(info, Rule(style="dim cyan"), grid), title="[bold cyan] 🎛️  OOB NETWORK MANAGER [/]", border_style="cyan", padding=(1, 2)))

def main_menu(cfg, config_path):
    while True:
        _show_menu(cfg)
        try: choice = _con.input("[bold]Chon[/]: ").strip().lower()
        except (EOFError, KeyboardInterrupt): sys.exit(0)
        _con.print()

        if choice == "1":
            ip = _con.input("  [cyan]IP thiet bi OOB[/]: ").strip()
            alias = _con.input("  [cyan]Ten goi (alias)[/]: ").strip() or None
            if ip: add_ip(cfg["ip_list"], ip, alias)
        elif choice == "2":
            ip = _con.input("  [cyan]IP can xoa[/]: ").strip()
            if ip: remove_ip(cfg["ip_list"], ip)
        elif choice == "3": settings_menu(cfg, config_path)
        elif choice == "4": list_devices(cfg)
        elif choice == "5": view_baseline(cfg)
        elif choice == "6": search_device(cfg)
        elif choice == "7": scan_specific_devices(cfg)
        elif choice == "8": verify_specific_devices(cfg)
        elif choice == "9": view_latest_verify_log()
        elif choice == "i": import_from_excel(cfg)
        elif choice == "e": export_menu_report(cfg)
        elif choice == "0": sys.exit(0)
        else: _con.print("  [red][!][/] Lua chon khong hop le.")

def main():
    config_path = CONFIG_FILE_DEFAULT
    for arg in sys.argv[1:]:
        if not arg.startswith("--"): config_path = arg; break
    cfg = load_config(config_path)
    if not os.path.exists(config_path): save_config(config_path, cfg)

    if "--daemon" in sys.argv: return run_daemon(cfg, config_path)
    elif "--menu" in sys.argv: return main_menu(cfg, config_path)

    _con.print(Panel("[bold cyan]1.[/] Mo Menu Quan Ly (Them/Sua IP, Xem danh sach)\n[bold cyan]2.[/] Mo Trinh Giam Sat (Chay log Daemon o terminal nay)\n[bold cyan]3.[/] Mo CA HAI (Tu dong mo 2 cua so - Yeu cau Windows)", title="[bold yellow]🚀 OOB LAUNCHER MULTI-TERM[/]", border_style="yellow", padding=(1,2)))
    choice = input("Chon che do: ").strip()
    
    if choice == "1": main_menu(cfg, config_path)
    elif choice == "2": run_daemon(cfg, config_path)
    elif choice == "3":
        if platform.system() == "Windows":
            subprocess.Popen(f'start cmd /k "python {sys.argv[0]} --daemon"', shell=True)
            subprocess.Popen(f'start cmd /k "python {sys.argv[0]} --menu"', shell=True)
        else: _con.print("Tren Linux/WSL, vui long mo 2 tab va tu chay tham so --menu / --daemon.")
        sys.exit(0)

if __name__ == "__main__":
    main()