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

# Import tu oob_lib: Them ham push_menu_descriptions o cuoi
from oob_lib import (
    poll_host, MiniTelnet, connect_auto, fetch_hostname,
    fetch_hostname_via_auto, hostname_matches_description,
    push_menu_descriptions
)

CONFIG_FILE_DEFAULT = "oob_config.json"

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "enable_password": "",
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
    # Lich chay Deep Verify tu dong (run_verify_daemon). "interval" = giu
    # hanh vi cu (lap lai moi verify_interval giay). "daily" = chay 1 lan/ngay
    # vao dung gio verify_schedule_time. "weekly" = chay 1 lan/tuan vao dung
    # thu + gio da chon.
    "verify_schedule_mode": "interval",   # "interval" | "daily" | "weekly"
    "verify_schedule_time": "01:00",      # "HH:MM", dung cho "daily"/"weekly"
    "verify_schedule_weekday": "mon",     # mon/tue/wed/thu/fri/sat/sun, dung cho "weekly"
    # Lich chay Thu thap cau hinh tu dong (run_daemon, Luong 1). Cung 3 che do
    # nhu verify_schedule_mode o tren: "interval" = giu hanh vi cu (lap lai
    # moi "interval" giay). "daily"/"weekly" = chi chay 1 lan vao dung
    # gio/thu co dinh moi ngay/tuan.
    "scan_schedule_mode": "interval",     # "interval" | "daily" | "weekly"
    "scan_schedule_time": "01:00",        # "HH:MM", dung cho "daily"/"weekly"
    "scan_schedule_weekday": "mon",       # mon/tue/wed/thu/fri/sat/sun, dung cho "weekly"
    # Thoi gian cho sau khi telnet/ssh den port console (giay). Giam xuong 0.5s
    # neu mang nhanh, tang len 2-3s neu thiet bi phan hoi cham (#9).
    "verify_wait_after_connect": 1.5,
    # Timeout tong the cho 1 lan run_deep_verify() (giay). Neu quet 1 OOB vuot
    # qua gioi han nay, cac option con lai se bi bo qua de tranh treo thread (#10).
    "max_verify_duration": 300,
}

# ---------------------------------------------------------------------------
# Module-level compiled regex constants (#7: tranh compile lai moi lan goi)
# ---------------------------------------------------------------------------
_DESC_PREFIX_RE    = re.compile(r'^[-=>\s]+')
_HOSTNAME_PROMPT_RE = re.compile(r'([A-Za-z0-9_\-\.]+)[>#]')
_HOSTNAME_LOGIN_RE  = re.compile(r'([A-Za-z0-9_\-\.]+)\s+login:', re.IGNORECASE)
_HOSTNAME_BSD_RE    = re.compile(r'\(([A-Za-z0-9_\-\.]+)\)\s*\(tty', re.IGNORECASE)
_ANSI_STRIP_RE      = re.compile(r'\x1b\[.*?m')
_CONN_ERR_RE        = re.compile(r'refused|time(d)?[\s-]?out|unreachable|no route to host|unknown host|% ', re.IGNORECASE)
_IP_RE              = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')

# ---------------------------------------------------------------------------
# Hệ thống UI đa luồng (Chia đôi màn hình)
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
    """Cập nhật dữ liệu vào 2 khung panel."""
    with ui_lock:
        layout["upper"].update(Panel(Text.from_markup("\n".join(oob_logs)), title="[bold cyan]🔍 OOB MONITORING (Cấu hình)[/]", border_style="cyan"))
        layout["lower"].update(Panel(Text.from_markup("\n".join(verify_logs)), title="[bold magenta]⚡ DEEP VERIFY (Thiết bị cuối)[/]", border_style="magenta"))
        if _live_ui and _live_ui.is_started:
            _live_ui.update(layout)

def log_oob(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    oob_logs.append(f"[dim]\\[{ts}][/] {msg}")
    # FIX: update_ui() dung ui_lock nen goi tu nhieu thread van an toan.
    # Rich Live chi ve lai dung Panel da duoc set trong layout tu lan
    # update_ui() gan nhat — neu khong goi lai o day, panel se dung yen
    # o trang thai rong ban dau mai mai du log van duoc append vao deque.
    update_ui()

def log_verify(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    verify_logs.append(f"[dim]\\[{ts}][/] {msg}")
    # FIX: xem giai thich o log_oob() phia tren.
    update_ui()


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


# ---------------------------------------------------------------------------
# Lich chay theo gio/thu trong ngay (dung cho Deep Verify tu dong)
# ---------------------------------------------------------------------------

_WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_WEEKDAY_LABELS = {
    "mon": "Thu 2", "tue": "Thu 3", "wed": "Thu 4", "thu": "Thu 5",
    "fri": "Thu 6", "sat": "Thu 7", "sun": "Chu nhat",
}


def _parse_hhmm(text, default="01:00"):
    """Doc chuoi 'HH:MM' -> (gio, phut). Tra ve gia tri mac dinh neu sai dinh dang."""
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
    """Tinh thoi diem (datetime) cua lan chay TIEP THEO dua tren lich da cau
    hinh - dung cho Deep Verify tu dong khi 'verify_schedule_mode' la 'daily'
    hoac 'weekly' (thay vi lap lai theo 'verify_interval' giay nhu truoc).

    - mode='daily' : chay moi ngay dung vao gio 'time_str' (VD '01:00' = 1h sang).
    - mode='weekly': chay 1 lan/tuan, dung thu 'weekday_str' (mon..sun) va gio
      'time_str'.
    """
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

    # mode == "daily" (mac dinh khi cau hinh khong hop le)
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
  1. Lap lai theo chu ky (interval, giay) - hanh vi mac dinh cu
  2. Hang ngay, vao 1 gio co dinh (VD 01:00 = 1 gio sang)
  3. Hang tuan, vao 1 thu + gio co dinh (VD Thu 2 luc 01:00)
""")
    mode_choice = _con.input("  [cyan]Chon che do (1/2/3)[/]: ").strip()

    if mode_choice == "1":
        cfg["verify_schedule_mode"] = "interval"
        _con.print("  [green][*][/] Da chuyen ve che do lap lai theo chu ky (muc 'v' o menu Cau hinh).")
        return

    if mode_choice == "2":
        cfg["verify_schedule_mode"] = "daily"
        val = _con.input(f"  [cyan]Gio chay moi ngay, dinh dang HH:MM (hien tai: {cfg.get('verify_schedule_time', '01:00')})[/]: ").strip()
        if val:
            h, m = _parse_hhmm(val)
            cfg["verify_schedule_time"] = f"{h:02d}:{m:02d}"
        _con.print(f"  [green][*][/] Da dat lich: Hang ngay luc {cfg.get('verify_schedule_time', '01:00')}.")
        return

    if mode_choice == "3":
        cfg["verify_schedule_mode"] = "weekly"
        _con.print("  Chon thu trong tuan: mon=Thu2 tue=Thu3 wed=Thu4 thu=Thu5 fri=Thu6 sat=Thu7 sun=CN")
        wd_val = _con.input(f"  [cyan]Thu (hien tai: {cfg.get('verify_schedule_weekday', 'mon')})[/]: ").strip().lower()
        if wd_val[:3] in _WEEKDAY_MAP:
            cfg["verify_schedule_weekday"] = wd_val[:3]
        val = _con.input(f"  [cyan]Gio chay, dinh dang HH:MM (hien tai: {cfg.get('verify_schedule_time', '01:00')})[/]: ").strip()
        if val:
            h, m = _parse_hhmm(val)
            cfg["verify_schedule_time"] = f"{h:02d}:{m:02d}"
        _con.print(f"  [green][*][/] Da dat lich: {_describe_verify_schedule(cfg)}.")
        return

    _con.print("  [yellow][!][/] Lua chon khong hop le, giu nguyen lich cu.")


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
  --- Lich chay Thu thap cau hinh (Luong 1) tu dong ---
  Hien tai: {_describe_scan_schedule(cfg)}
  1. Lap lai theo chu ky (giay) - hanh vi mac dinh cu
  2. Hang ngay, vao 1 gio co dinh (VD 01:00 = 1 gio sang)
  3. Hang tuan, vao 1 thu + gio co dinh (VD Thu 2 luc 01:00)
""")
    mode_choice = _con.input("  [cyan]Chon che do (1/2/3)[/]: ").strip()

    if mode_choice == "1":
        cfg["scan_schedule_mode"] = "interval"
        _con.print("  [green][*][/] Da chuyen ve che do lap lai theo chu ky (muc '7' o menu Cau hinh).")
        return

    if mode_choice == "2":
        cfg["scan_schedule_mode"] = "daily"
        val = _con.input(f"  [cyan]Gio chay moi ngay, dinh dang HH:MM (hien tai: {cfg.get('scan_schedule_time', '01:00')})[/]: ").strip()
        if val:
            h, m = _parse_hhmm(val)
            cfg["scan_schedule_time"] = f"{h:02d}:{m:02d}"
        _con.print(f"  [green][*][/] Da dat lich: Hang ngay luc {cfg.get('scan_schedule_time', '01:00')}.")
        return

    if mode_choice == "3":
        cfg["scan_schedule_mode"] = "weekly"
        _con.print("  Chon thu trong tuan: mon=Thu2 tue=Thu3 wed=Thu4 thu=Thu5 fri=Thu6 sat=Thu7 sun=CN")
        wd_val = _con.input(f"  [cyan]Thu (hien tai: {cfg.get('scan_schedule_weekday', 'mon')})[/]: ").strip().lower()
        if wd_val[:3] in _WEEKDAY_MAP:
            cfg["scan_schedule_weekday"] = wd_val[:3]
        val = _con.input(f"  [cyan]Gio chay, dinh dang HH:MM (hien tai: {cfg.get('scan_schedule_time', '01:00')})[/]: ").strip()
        if val:
            h, m = _parse_hhmm(val)
            cfg["scan_schedule_time"] = f"{h:02d}:{m:02d}"
        _con.print(f"  [green][*][/] Da dat lich: {_describe_scan_schedule(cfg)}.")
        return

    _con.print("  [yellow][!][/] Lua chon khong hop le, giu nguyen lich cu.")


def _test_connection(cfg):
    """Thu ket noi nhanh toi 1 IP bat ky de kiem tra credential (#13)."""
    test_ip = _con.input("  [cyan]Nhap IP OOB can thu ket noi[/]: ").strip()
    if not test_ip:
        _con.print("  [yellow][!][/] Khong nhap IP. Huy.")
        return
    if not cfg.get("password"):
        _con.print("  [yellow][!][/] Chua cau hinh password. Vui long cau hinh [2] truoc.")
        return
    _con.print(f"  [cyan][*][/] Dang thu ket noi toi [bold]{test_ip}[/]...")
    try:
        session = connect_auto(
            test_ip, cfg.get("ssh_port", 22), cfg.get("telnet_port", 23),
            cfg.get("username", ""), cfg.get("password", ""),
            cfg.get("enable_password", ""), timeout=8,
        )
        hn = fetch_hostname(session)
        session.close()
        if hn:
            _con.print(f"  [green bold]✓ Ket noi thanh cong![/] Hostname: [bold cyan]{hn}[/]")
        else:
            _con.print("  [yellow][?][/] Ket noi OK nhung khong lay duoc hostname (co the la OOB moi).")
    except Exception as e:
        _con.print(f"  [red bold]✗ Ket noi that bai:[/] {e}")


def settings_menu(cfg, config_path):

    while True:
        schedule_mode = cfg.get("verify_schedule_mode", "interval")
        v_note = ("[dim red]<- Khong hieu luc (dang dung lich co dinh — doi o muc [d])[/]"
                  if schedule_mode in ("daily", "weekly")
                  else "[dim green]<- Dang co hieu luc[/]")
        scan_schedule_mode = cfg.get("scan_schedule_mode", "interval")
        s_note = ("[dim red]<- Khong hieu luc (dang dung lich co dinh — doi o muc [s])[/]"
                  if scan_schedule_mode in ("daily", "weekly")
                  else "[dim green]<- Dang co hieu luc[/]")
        auto_v = "[green bold]BAT[/]" if cfg.get('auto_verify', True)     else "[red bold]TAT[/]"
        auto_p = "[green bold]BAT[/]" if cfg.get('auto_push_desc', True)  else "[red bold]TAT[/]"
        wait_s = cfg.get('verify_wait_after_connect', 1.5)
        max_d  = cfg.get('max_verify_duration', 300)

        g = Table.grid(padding=(0, 1))
        g.add_column(style="bold cyan", min_width=4, justify="right")
        g.add_column()

        g.add_row("", "[dim]── KET NOI ──────────────────────────────────────────────────────────[/]")
        g.add_row("[1]",  f"Username              : {cfg['username'] or '[dim](khong dung)[/]'}")
        g.add_row("[2]",  f"Password              : {mask(cfg['password'])}")
        g.add_row("[3]",  f"Enable password       : {mask(cfg['enable_password'])}")
        g.add_row("[5]",  f"SSH port (uu tien)    : {cfg.get('ssh_port', 22)}")
        g.add_row("[6]",  f"Telnet port (du phong): {cfg.get('telnet_port', 23)}")
        g.add_row("", "")
        g.add_row("", "[dim]── FILE DU LIEU ────────────────────────────────────────────────────[/]")
        g.add_row("[8]",  f"File danh sach IP     : {cfg['ip_list']}")
        g.add_row("[9]",  f"File baseline DB      : {cfg['baseline_db']}")
        g.add_row("\\[a]",  f"File snapshot DB      : {cfg['snapshot_db']}")
        g.add_row("", "")
        g.add_row("", "[dim]── LUONG 1: GIAM SAT CAU HINH (daemon chay lien tuc) ─────────────[/]")
        g.add_row("", f"[dim]   Cu moi chu ky daemon ket noi OOB doc menu config,[/]")
        g.add_row("", "[dim]   roi so sanh voi baseline → canh bao ngay neu co thay doi.[/]")
        g.add_row("[4]",  f"Ten menu (rong=tu dong) : {cfg['menu_name_override'] or '[dim](tu dong do)[/]'}")
        g.add_row("\\[s]", f"Lich chay Thu thap      : [cyan]{_describe_scan_schedule(cfg)}[/]")
        g.add_row("", "[dim]   • daily/weekly → chay vao dung gio/ngay co dinh[/]")
        g.add_row("", "[dim]   • interval     → chay lap theo chu ky (muc [7] phia duoi)[/]")
        g.add_row("[7]",  f"Chu ky interval (s)     : [bold cyan]{cfg['interval']}[/]  {s_note}")
        g.add_row("", "")
        g.add_row("", "[dim]── LUONG 2: VERIFY VAT LY (Deep Verify — chay theo lich) ─────────[/]")
        g.add_row("", "[dim]   Daemon pivot vao tung port console, lay hostname thuc[/]")
        g.add_row("", "[dim]   de kiem tra description co dung khong. Chay theo lich.[/]")
        g.add_row("\\[b]", f"Tu dong Verify ngam     : {auto_v}")
        g.add_row("\\[c]",   f"Tu dong Sua loi ngam    : {auto_p}")
        g.add_row("\\[d]",   f"Lich chay Verify        : [cyan]{_describe_verify_schedule(cfg)}[/]")
        g.add_row("", "[dim]   • daily/weekly → chay vao dung gio/ngay co dinh[/]")
        g.add_row("", "[dim]   • interval     → chay lap theo chu ky (muc \\[v] phia duoi)[/]")
        g.add_row("\\[v]",   f"Chu ky interval (s)         : [bold cyan]{cfg.get('verify_interval', 3600)}[/]  {v_note}")
        g.add_row("", "[dim]   Chi co hieu luc khi muc \\[d] dang o che do \"interval\".[/]")
        g.add_row("\\[w]",   f"Cho sau connect console (s)  : [bold cyan]{wait_s}[/]  [dim](0.1-10.0, mac dinh 1.5)[/]")
        g.add_row("\\[m]",   f"Timeout tong Verify (s)      : [bold cyan]{max_d}[/]  [dim](>=30, mac dinh 300)[/]")
        g.add_row("", "")
        g.add_row("\\[t]",   "[bold yellow]Thu ket noi nhanh (test credential)[/]")
        g.add_row("[0]",   "[bold red]Quay lai menu chinh[/]")

        _con.print()
        _con.print(Panel(g, title="[bold cyan] ⚙️  CAI DAT HE THONG [/]", border_style="cyan", padding=(1, 2)))
        choice = _con.input("[bold]Chon muc can sua[/]: ").strip().lower()

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
        elif choice == "v":
            val = input(f"  Chu ky Verify vat ly, giay (hien tai: {cfg.get('verify_interval', 3600)}): ").strip()
            if val.isdigit(): cfg["verify_interval"] = int(val)
        elif choice == "w":
            val = input(f"  Cho sau connect console, giay float (hien tai: {cfg.get('verify_wait_after_connect', 1.5)}): ").strip()
            try:
                fval = float(val)
                if 0.1 <= fval <= 10.0:
                    cfg["verify_wait_after_connect"] = round(fval, 2)
                else:
                    _con.print("  [yellow][!][/] Gia tri hop le: 0.1 den 10.0 giay.")
                    continue
            except ValueError:
                _con.print("  [yellow][!][/] Vui long nhap so thuc hop le.")
                continue
        elif choice == "m":
            val = input(f"  Timeout tong Verify, giay (hien tai: {cfg.get('max_verify_duration', 300)}): ").strip()
            if val.isdigit() and int(val) >= 30:
                cfg["max_verify_duration"] = int(val)
            else:
                _con.print("  [yellow][!][/] Gia tri hop le: so nguyen >= 30 giay.")
                continue
        elif choice == "t":
            _test_connection(cfg)
            continue
        elif choice == "8":
            val = input(f"  File danh sach IP moi: ").strip()
            if val: cfg["ip_list"] = val
        elif choice == "9":
            val = input(f"  File baseline DB moi: ").strip()
            if val: cfg["baseline_db"] = val
        elif choice == "a":
            val = input(f"  File snapshot DB moi: ").strip()
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

# Cache cho load_ip_list — chi doc lai file khi mtime thay doi (#5)
_ip_list_cache: dict = {"path": None, "mtime": 0, "hosts": []}

def load_ip_list_cached(path: str) -> list:
    """Tra ve danh sach OOB IP, chi doc lai file khi co thay doi thuc su."""
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
    if "device_name" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN device_name TEXT")
    if "protocol" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN protocol TEXT DEFAULT 'telnet'")
    if "raw_key" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN raw_key TEXT")
    if "real_menu_name" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN real_menu_name TEXT")
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
    if not rows:
        return None, None, None
    menu_name   = rows[0][0]
    device_name = rows[0][2]
    options = {}
    for _mn, key, _dn, desc, ip, port, proto, raw_key, real_menu_name in rows:
        entry = {"description": desc, "ip": ip, "port": port, "protocol": proto}
        # _raw_key/_menu_name giu CHINH XAC cu phap key that tren thiet bi (vd
        # "[4]" khac "4") va ten menu thuc su cua option nay - dung khi push
        # cau hinh, KHONG duoc tu suy dien lai tu chuoi hien thi (option_key).
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
# Module Thu thập Menu (Đa Menu)
# ---------------------------------------------------------------------------

MENU_NAME_RE = re.compile(r'^\s*menu\s+(\S+)\s+(?:text|command)\b', re.IGNORECASE | re.MULTILINE)
TEXT_RE       = re.compile(r'^\s*menu\s+(\S+)\s+text\s+(\S+)\s+(.+)',                          re.IGNORECASE)
CMD_TELNET_RE = re.compile(r'^\s*menu\s+(\S+)\s+command\s+(\S+)\s+telnet\s+(\S+)(?:\s+(\d+))?',  re.IGNORECASE)
CMD_SSH_RE    = re.compile(r'^\s*menu\s+(\S+)\s+command\s+(\S+)\s+ssh\s+(?:-l\s+\S+\s+|\S+@)?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?:\s+(\d+))?', re.IGNORECASE)

def clean_key(raw_key: str) -> str:
    return raw_key.strip()

def poll_host_multi(ip, telnet_port, username, password, enable_password, menu_name_override=None, ssh_port=22, timeout=10):
    tn = connect_auto(ip, ssh_port, telnet_port, username, password, enable_password, timeout=timeout)
    try:
        hostname = fetch_hostname(tn)
        tn.write("terminal length 0")
        tn.read_until("#", timeout=5)
        tn.write("show running-config | include menu")
        # QUAN TRONG: dung read_until_prompt() (dua vao PROMPT_TAIL_RE), KHONG
        # dung read_until("#") - vi output nay co the dai nhieu dong va chua
        # ky tu '#' NGAY TRONG NOI DUNG (vd description "##" nhu tren thiet bi
        # HCM-OOB-FNX02L03H1-02), khien read_until("#") cat buffer NGANG GIUA
        # CHUNG output ngay khi gap ky tu '#' dau tien - con lai toan bo cac
        # dong "menu ... command ..." phia sau se bi mat, dan den
        # final_options rong va bao nham "Khong parse duoc menu hop le".
        raw = tn.read_until_prompt(timeout=15)
        
        detected_names = list(set(MENU_NAME_RE.findall(raw)))
        if menu_name_override:
            if menu_name_override not in detected_names:
                # Day KHONG phai loi parse - thiet bi co menu hop le, chi la
                # ten menu ep dung (cau hinh toan cuc trong Settings) khong
                # khop voi thiet bi nay. Bao ro nguyen nhan thay vi de rot
                # xuong thanh "Khong parse duoc menu hop le" chung chung.
                found_str = ", ".join(detected_names) if detected_names else "(khong tim thay menu nao)"
                raise ValueError(
                    f"Ten menu ep dung 'menu_name_override={menu_name_override}' (cau hinh trong Settings) "
                    f"khong ton tai tren thiet bi nay. Menu thuc te phat hien duoc: {found_str}. "
                    f"Neu moi thiet bi dung ten menu khac nhau, hay de trong 'menu_name_override' de tu dong do."
                )
            menu_names = [menu_name_override]
        else:
            menu_names = detected_names
            
        if not menu_names:
            return hostname, None, {}
            
        # ==========================================
        # THUẬT TOÁN 2 BƯỚC (TWO-PASS PARSER)
        # ==========================================
        all_texts = {}     # Rổ chứa nhãn hiển thị: (menu_name, key) -> description
        all_commands = {}  # Rổ chứa lệnh IP: (menu_name, key) -> {ip, port, protocol}
        
        # BƯỚC 1: Quét và phân loại riêng biệt
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            
            m = TEXT_RE.match(line)
            if m and m.group(1) in menu_names:
                m_name, raw_k, desc = m.group(1), m.group(2).strip(), m.group(3).strip()
                all_texts[(m_name, raw_k)] = desc
                continue
                
            m = CMD_TELNET_RE.match(line)
            if m and m.group(1) in menu_names:
                m_name, raw_k = m.group(1), m.group(2).strip()
                all_commands[(m_name, raw_k)] = {
                    "ip": m.group(3),
                    "port": int(m.group(4)) if m.group(4) else 23,
                    "protocol": "telnet"
                }
                continue
                
            m = CMD_SSH_RE.match(line)
            if m and m.group(1) in menu_names:
                m_name, raw_k = m.group(1), m.group(2).strip()
                all_commands[(m_name, raw_k)] = {
                    "ip": m.group(3),
                    "port": int(m.group(4)) if m.group(4) else 22,
                    "protocol": "ssh"
                }

        # BƯỚC 2: Móc nối Command với Text
        final_options = {}
        for (m_name, cmd_k), cmd_data in all_commands.items():
            # ƯU TIÊN 1: Tìm nhãn có ngoặc vuông (VD: lệnh 4 -> ưu tiên móc với text [4])
            if (m_name, f"[{cmd_k}]") in all_texts:
                real_key = f"[{cmd_k}]"
                desc = all_texts[(m_name, f"[{cmd_k}]")]
            # ƯU TIÊN 2: Tìm nhãn số trần (VD: lệnh 4 -> móc với text 4)
            elif (m_name, cmd_k) in all_texts:
                real_key = cmd_k
                desc = all_texts[(m_name, cmd_k)]
            # Không có nhãn hiển thị nào
            else:
                real_key = cmd_k
                desc = ""
                
            # Render ra key để hiển thị UI (có kèm tên Menu nếu có nhiều Menu).
            # LUU Y: real_key co the DA la "[4]" (nhan ngoac vuong) hoac "4"
            # (nhan tran) - phai giu CHINH XAC dinh dang nay rieng cho push cau
            # hinh sau nay; chuoi hien thi (ui_key) chi de con nguoi doc, khong
            # duoc dung de suy nguoc ra cu phap that (se gay nham "[4]" <-> "4").
            display_key = real_key[1:-1] if real_key.startswith("[") and real_key.endswith("]") else real_key
            ui_key = f"{m_name} [{display_key}]" if len(menu_names) > 1 else display_key

            final_options[ui_key] = {
                "description": desc,
                "ip": cmd_data["ip"],
                "port": cmd_data["port"],
                "protocol": cmd_data["protocol"],
                "_raw_key": real_key,      # cu phap that: "4" hoac "[4]"
                "_menu_name": m_name,      # ten menu that (khong phai chuoi gop)
            }
            
        combined_menu_name = " + ".join(sorted(menu_names))
        return hostname, combined_menu_name, final_options
    finally:
        try:
            tn.write("exit")
        except OSError:
            pass
        tn.close()


# ---------------------------------------------------------------------------
# Module Deep Verify & Smart Clear Line
# ---------------------------------------------------------------------------

def clear_stuck_line(cfg, oob_ip, console_port):
    """Đăng nhập vào OOB và clear line console đang bị kẹt."""
    line_num = console_port - 2000 
    
    if line_num <= 0 or line_num > 200: 
        return False
        
    try:
        session = connect_auto(
            oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"], 
            cfg["username"], cfg["password"], cfg["enable_password"], timeout=5
        )
        session.write(f"clear line {line_num}")
        session.read_until("[confirm]", timeout=3)
        session.write("\n")
        session.read_until("#", timeout=3)
        session.close()
        return True
    except Exception:
        return False

def extract_hostname(output: str) -> str:
    """Loc hostname tu luong ky tu tra ve cua Console."""
    output = _ANSI_STRIP_RE.sub('', output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    auth_seen = False
    
    for line in reversed(lines):
        if any(x in line for x in ["telnet ", "ssh ", "Trying ", "Open", "Connection refused", "disconnect", "clear line"]):
            continue

        if _CONN_ERR_RE.search(line):
            continue
            
        m = _HOSTNAME_PROMPT_RE.search(line)
        if m: return m.group(1)
            
        m_login = _HOSTNAME_LOGIN_RE.search(line)
        if m_login: return m_login.group(1)
            
        m_bsd = _HOSTNAME_BSD_RE.search(line)
        if m_bsd: return m_bsd.group(1)
            
        if re.search(r'Username:|Password:|login:', line, re.IGNORECASE):
            auth_seen = True
            
    if auth_seen: return "AUTH_REQUIRED"
    return None

def _truncate(text, width):
    text = "" if text is None else str(text)
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"

_VERIFY_STATUS_ORDER = {
    "CANH BAO": 0,
    "KHONG PIVOT": 1,
    "TIMEOUT": 2,
    "YEU CAU DANG NHAP": 3,
    "OK": 4,
}
_VERIFY_STATUS_LABEL = {
    "CANH BAO": "CANH BAO",
    "KHONG PIVOT": "KO PIVOT",
    "TIMEOUT": "TIMEOUT",
    "YEU CAU DANG NHAP": "YC DANG NHAP",
    "OK": "OK",
}

def _build_verify_report(alias, oob_ip, own_hostname, results):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = "   ".join(
        f"{_VERIFY_STATUS_LABEL[s]}: {counts.get(s, 0)}"
        for s in ("CANH BAO", "KHONG PIVOT", "TIMEOUT", "YEU CAU DANG NHAP", "OK")
        if counts.get(s, 0)
    ) or "Khong co option nao duoc quet"

    headers = ["STT", "Option", "Trang thai", "Hostname thuc te", "Description", "Port", "Ghi chu"]
    max_w   = [4,     22,       12,           22,                 32,             6,      36]

    ordered = sorted(
        enumerate(results),
        key=lambda p: (_VERIFY_STATUS_ORDER.get(p[1]["status"], 9), p[0]),
    )

    rows = []
    for i, (_, r) in enumerate(ordered, start=1):
        rows.append([
            str(i),
            _truncate(r["key"], max_w[1]),
            _VERIFY_STATUS_LABEL.get(r["status"], r["status"]),
            _truncate(r.get("act_host") or "-", max_w[3]),
            _truncate(r.get("desc") or "-", max_w[4]),
            str(r.get("port", "") or ""),
            _truncate(r.get("note") or "", max_w[6]),
        ])

    widths = []
    for i, h in enumerate(headers):
        col_max = max([len(h)] + [len(row[i]) for row in rows]) if rows else len(h)
        widths.append(min(max(col_max, len(h)), max_w[i]))

    def fmt_row(cols):
        return "│ " + " │ ".join(cols[i].ljust(widths[i]) for i in range(len(cols))) + " │"

    def fmt_sep(left, mid, right):
        return left + mid.join("─" * (w + 2) for w in widths) + right

    table_width = sum(widths) + 3 * len(widths) + 1
    bar = "=" * max(table_width, 60)

    lines = []
    lines.append(bar)
    lines.append(f" BAO CAO DEEP VERIFY - OOB: {alias} ({oob_ip})")
    lines.append(f" Thoi gian      : {now_str}")
    lines.append(f" Hostname OOB   : {own_hostname or '(khong xac dinh duoc)'}")
    lines.append(f" Tong so option : {len(results)}")
    lines.append(f" Tom tat        : {summary}")
    lines.append(bar)
    lines.append(fmt_sep("┌", "┬", "┐"))
    lines.append(fmt_row(headers))
    lines.append(fmt_sep("├", "┼", "┤"))
    if rows:
        for row in rows:
            lines.append(fmt_row(row))
    else:
        lines.append("│ " + "(khong co option nao co description de kiem tra)".ljust(table_width - 4) + " │")
    lines.append(fmt_sep("└", "┴", "┘"))
    lines.append(bar)
    lines.append(f" HOAN THANH luc {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(bar)
    return "\n".join(lines)


def run_deep_verify(cfg, alias, oob_ip, options, print_fn=None):
    """Thực thi verify vật lý (PIVOT Mới + Smart Clear) va xuat log.

    Timeout tong the: cfg['max_verify_duration'] (mac dinh 300s). Neu quet 1 OOB
    vuot qua moc nay, cac option con lai se bi bo qua tranh thread bi treo (#10).
    """
    if print_fn is None:
        print_fn = log_verify

    max_duration = float(cfg.get("max_verify_duration", 300))
    _verify_deadline = time.time() + max_duration

    print_fn(f"[*] Bat dau kiem tra vat ly (PIVOT) cho OOB: [bold]{alias}[/]")

    own_hostname = None
    try:
        own_hostname = fetch_hostname_via_auto(
            oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"],
            cfg["username"], cfg["password"], cfg["enable_password"], timeout=6,
        )
    except Exception:
        own_hostname = None
    own_hostname_clean = (own_hostname or "").strip().lower()

    os.makedirs("verify-logs", exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join("verify-logs", f"Verify_{alias}_{ts_str}.log")

    results = [] 
    
    session = None
    def get_session():
        nonlocal session
        if session is None:
            session = connect_auto(
                oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"], 
                cfg["username"], cfg["password"], cfg["enable_password"], timeout=8
            )
            session.write("terminal length 0")
            session.read_until("#", timeout=2)
        return session
        
    def reset_session():
        nonlocal session
        if session:
            session.close()
        session = None

    def check_port_via_oob(t_ip, t_port, proto):
        try:
            s = get_session()
        except Exception:
            raise RuntimeError("Khong the ket noi toi OOB")
            
        cmd = f"ssh -l admin {t_ip}" if proto == "ssh" else f"telnet {t_ip} {t_port}"
        s.write(cmd)
        wait_time = float(cfg.get("verify_wait_after_connect", 1.5))
        time.sleep(wait_time) 
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
            else:
                reset_session()
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
        # Kiem tra timeout tong the — neu qua han, bo qua option con lai (#10)
        if time.time() > _verify_deadline:
            scanned = len(results)
            total   = len(options)
            print_fn(f"[yellow][!][/] {alias}: Vuot timeout tong {int(max_duration)}s "
                     f"(da quet {scanned}/{total} option). Bo qua phan con lai.")
            break
        desc = opt.get("description", "")
        if not desc: continue
            
        target_ip = opt.get("ip")
        port = opt.get("port", 23)
        proto = opt.get("protocol", "telnet")
        
        act_host = None
        conn_error = ""
        note_parts = []

        try:
            output = check_port_via_oob(target_ip, port, proto)
            act_host = extract_hostname(output)
        except Exception as e:
            conn_error = str(e)
            
        if not act_host:
            if port > 2000:
                line_to_clear = port - 2000
                print_fn(f"[yellow][!][/] {alias} (Opt {key}): Line {line_to_clear} dang ket. Dang clear line {line_to_clear}...")
                note_parts.append(f"Da phat hien ket line {line_to_clear}, da thu clear line")

                if clear_line_via_oob(port):
                    time.sleep(2) 
                    try:
                        output = check_port_via_oob(target_ip, port, proto)
                        act_host = extract_hostname(output)
                    except Exception as e:
                        conn_error = str(e)
                else:
                    print_fn(f"[dim][-][/] {alias}: Khong the clear line {line_to_clear} vao luc nay.")
                    note_parts.append("Khong clear duoc line")
            else:
                note_parts.append("Port la Direct Access, bo qua clear line")

        note = "; ".join(note_parts)

        if not act_host:
            msg_ui = f"[dim][-][/] {alias} (Opt {key}): TIMEOUT hoac Loi mang"
            print_fn(msg_ui)
            results.append({"key": key, "status": "TIMEOUT", "act_host": None,
                             "desc": desc, "port": port, "note": note})
            continue
            
        if act_host == "AUTH_REQUIRED":
            msg_ui = f"[yellow][?][/] {alias} (Opt {key}): Thiet bi yeu cau dang nhap (Khong thay hostname)"
            print_fn(msg_ui)
            results.append({"key": key, "status": "YEU CAU DANG NHAP", "act_host": None,
                             "desc": desc, "port": port, "note": note})
            continue
            
        desc_clean = _DESC_PREFIX_RE.sub('', desc).strip().lower()
        act_host_clean = act_host.strip().lower()

        if own_hostname_clean and act_host_clean == own_hostname_clean:
            msg_ui = f"[dim][-][/] {alias} (Opt {key}): Khong pivot duoc toi thiet bi dich (van dang o console OOB)"
            print_fn(msg_ui)
            note = "; ".join(note_parts + [f"Van o console cua chinh OOB ({own_hostname})"])
            results.append({"key": key, "status": "KHONG PIVOT", "act_host": act_host,
                             "desc": desc, "port": port, "note": note})
            continue

        if act_host_clean == desc_clean or hostname_matches_description(act_host, desc_clean):
            msg_ui = f"[green][OK][/] {alias} (Opt {key}): Khop ({act_host})"
            print_fn(msg_ui)
            results.append({"key": key, "status": "OK", "act_host": act_host,
                             "desc": desc, "port": port, "note": note})
        else:
            msg_ui = f"[bold red]CANH BAO[/] Thiet bi noi line ([yellow]{act_host}[/]) khac description ([yellow]{desc_clean}[/]) tai Opt {key}!"
            print_fn(msg_ui)
            results.append({"key": key, "status": "CANH BAO", "act_host": act_host,
                             "desc": desc, "port": port, "note": note})

    print_fn(f"[green]✓[/] Hoan thanh Verify cho OOB: [bold]{alias}[/]\n")
    
    if session:
        session.close()

    report_text = _build_verify_report(alias, oob_ip, own_hostname, results)
    with open(log_file_path, "w", encoding="utf-8") as f:
        f.write(report_text)
        
    json_file_path = log_file_path.replace(".log", ".json")
    try:
        import json
        with open(json_file_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print_fn(f"[yellow][!][/] Khong the luu file JSON: {e}")

    # TRẢ VỀ KẾT QUẢ CHO TIẾN TRÌNH TỰ ĐỘNG SỬA LỖI
    return results


# ---------------------------------------------------------------------------
# Module Self-Healing (Tự động phục hồi Description bị sai)
# ---------------------------------------------------------------------------

def check_ip_collision(cfg, target_ip, current_oob_ip):
    """Kiểm tra xem target_ip đích có đang bị gán lặp chéo ở một thiết bị OOB nào khác không."""
    conn = _init_db(cfg["baseline_db"], "baseline_menu")
    cur = conn.execute("SELECT host FROM baseline_menu WHERE target_ip=?", (target_ip,))
    hosts = {row[0] for row in cur.fetchall()}
    conn.close()
    return len(hosts - {current_oob_ip}) > 0

def process_push_and_reverify(cfg, alias, oob_ip, baseline, verify_results, print_fn=None):
    """Xử lý đẩy cấu hình tự động và tách chuỗi Menu/Key chuẩn."""
    if print_fn is None: print_fn = log_verify

    # CHI push tu dong khi tinh nang nay dang duoc BAT trong cau hinh.
    if not cfg.get("auto_push_desc", True):
        return

    warnings = [r for r in verify_results if r["status"] == "CANH BAO"]
    if not warnings:
        return

    updates_list = []
    push_log_entries = []
    
    mn, dn, _ = get_options_by_host(cfg["baseline_db"], "baseline_menu", oob_ip)
    if not mn:
        return

    for w in warnings:
        key = w["key"]
        act_host = w["act_host"]
        opt = baseline.get(key, {})
        target_ip = opt.get("ip")
        
        if check_ip_collision(cfg, target_ip, oob_ip):
            print_fn(f"[yellow][!][/] {alias} (Opt {key}): Nghi ngo trung IP {target_ip} tren nhieu OOB. Bo qua tu dong sua.")
            continue
            
        new_desc = f"----> {act_host}"
        
        # --- DUNG CHINH XAC MENU NAME / KEY THAT DA LUU TU LUC QUET ---
        # opt["_menu_name"]/opt["_raw_key"] duoc poll_host_multi() ghi lai dung
        # cu phap tren thiet bi (vd key that su la "[4]" chu khong phai "4").
        # KHONG duoc tu suy dien lai tu chuoi hien thi 'key' - do la nguyen
        # nhan gay ra loi push nham "4" thay vi "[4]" (tao option moi thay vi
        # sua option cu).
        real_menu_name = opt.get("_menu_name")
        real_key = opt.get("_raw_key")
        if not real_menu_name or not real_key:
            # Baseline cu (luu truoc khi co _menu_name/_raw_key) - khong the
            # biet chac cu phap that, bo qua de an toan thay vi doan mo.
            print_fn(f"[yellow][!][/] {alias} (Opt {key}): Baseline cu khong co thong tin key that, "
                     f"bo qua tu dong sua (hay quet lai Option 7 de cap nhat baseline).")
            continue

        updates_list.append((real_menu_name, real_key, new_desc))
        
        push_log_entries.append({
            "key": key, 
            "real_menu_name": real_menu_name,
            "real_key": real_key,
            "target_ip": target_ip,
            "old": w["desc"], 
            "new": new_desc
        })

    if not updates_list: return

    print_fn(f"[*] Dang tu dong PUSH sua loi {len(updates_list)} option cho {alias}...")
    
    # Gửi mảng dữ liệu đã làm sạch xuống thư viện
    success = push_menu_descriptions(
        oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"],
        cfg["username"], cfg["password"], cfg["enable_password"],
        updates_list, timeout=10
    )

    if not success:
        print_fn(f"[red][LOI][/] {alias}: Push cau hinh that bai (Cisco tu choi lenh hoac mat ket noi).")
        # Dừng lại ngay, không update memory hay file DB để không sinh ra Ảo ảnh
        return

    # CHỈ CẬP NHẬT DB VÀ LOG KHI SWITCH THỰC SỰ CHẤP NHẬN LỆNH
    os.makedirs("push-logs", exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join("push-logs", f"Push_{alias}_{ts_str}.log")
    
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"=== PUSH LOG TỰ ĐỘNG: {alias} ({oob_ip}) ===\n")
        f.write(f"Thoi gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        for entry in push_log_entries:
            # Cập nhật Baseline vào RAM
            key = entry["key"]
            baseline[key]["description"] = entry["new"] 
            
            f.write(f"- Option [{key}] (Target IP: {entry['target_ip']}):\n")
            f.write(f"  + Cu : {entry['old']}\n")
            f.write(f"  + Moi: {entry['new']}\n")
            f.write(f"  + REVERT CMD: menu {entry['real_menu_name']} text {entry['real_key']} {entry['old']}\n\n")

    # Lưu Baseline mới xuống File DB
    save_options(cfg["baseline_db"], "baseline_menu", oob_ip, mn, dn, baseline)
    print_fn(f"[green]✓[/] Da cap nhat cau hinh & Baseline (Khong Auto-save write memory).")

    print_fn(f"[*] Tu dong Re-Verify lai cac option vua sua tren {alias}...")
    subset_options = {entry["key"]: baseline[entry["key"]] for entry in push_log_entries}
    run_deep_verify(cfg, alias, oob_ip, subset_options, print_fn=print_fn)

def _thread_verify_and_push(cfg, alias, ip, snapshot):
    res = run_deep_verify(cfg, alias, ip, snapshot)
    process_push_and_reverify(cfg, alias, ip, snapshot, res)


# ---------------------------------------------------------------------------
# Vòng lặp giám sát (Daemon Thread)
# ---------------------------------------------------------------------------

def run_verify_daemon(cfg):
    """Tiến trình Daemon thứ 2 chuyên lặp lịch Deep Verify độc lập & Tự động phục hồi.

    Hỗ trợ 3 chế độ lập lịch qua cfg['verify_schedule_mode']:
      - "interval" (mặc định, giữ nguyên hành vi cũ): lặp lại mỗi
        cfg['verify_interval'] giây.
      - "daily": chạy 1 lần/ngày, đúng giờ cfg['verify_schedule_time'] (VD
        "01:00" = chạy lúc 1 giờ sáng mỗi ngày).
      - "weekly": chạy 1 lần/tuần, đúng thứ cfg['verify_schedule_weekday'] +
        giờ cfg['verify_schedule_time'] (VD Thứ 2 lúc 01:00).
    """
    schedule_mode = cfg.get("verify_schedule_mode", "interval")
    log_verify(f"[green][START][/] Khoi dong Verify vat ly - lich: {_describe_verify_schedule(cfg)}.")

    time.sleep(15)

    while True:
        # Bổ sung kiểm tra điều kiện auto_verify tại đây
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

        # Doc lai lich moi lan (cho phep nhan thay doi neu cfg duoc cap nhat
        # trong cung tien trinh) roi tinh thoi gian cho toi lan chay tiep theo.
        schedule_mode = cfg.get("verify_schedule_mode", "interval")
        if schedule_mode in ("daily", "weekly"):
            next_run = compute_next_scheduled_run(
                schedule_mode,
                cfg.get("verify_schedule_time", "01:00"),
                cfg.get("verify_schedule_weekday", "mon"),
            )
            sleep_seconds = max(1, int((next_run - datetime.now()).total_seconds()))
            log_verify(f"[dim][zzz] Lan Verify tiep theo: {next_run.strftime('%Y-%m-%d %H:%M')} "
                       f"(con {sleep_seconds}s)...[/]")
        else:
            sleep_seconds = cfg.get("verify_interval", 3600)
            log_verify(f"[dim][zzz] Dang cho {sleep_seconds}s cho dot Verify tiep theo...[/]")

        time.sleep(sleep_seconds)

def input_with_timeout(prompt_text: str, timeout: int = 5):
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

def _scan_wait(cfg):
    """Tinh & cho toi lan Thu thap cau hinh (Luong 1) tiep theo, dua tren
    cfg['scan_schedule_mode']:
      - "interval" (mac dinh, giu hanh vi cu): cho dung cfg['interval'] giay.
      - "daily"/"weekly": cho toi dung gio/thu co dinh da cau hinh, dung
        cung logic voi Deep Verify (compute_next_scheduled_run).
    Doc lai cfg moi lan goi de nhan thay doi lich neu duoc sua trong menu
    Cau hinh (option 3) trong cung tien trinh.
    """
    mode = cfg.get("scan_schedule_mode", "interval")
    if mode in ("daily", "weekly"):
        next_run = compute_next_scheduled_run(
            mode,
            cfg.get("scan_schedule_time", "01:00"),
            cfg.get("scan_schedule_weekday", "mon"),
        )
        sleep_seconds = max(1, int((next_run - datetime.now()).total_seconds()))
        log_oob(f"[dim][zzz] Lan Thu thap tiep theo: {next_run.strftime('%Y-%m-%d %H:%M')} "
                f"(con {sleep_seconds}s)...[/]")
    else:
        sleep_seconds = cfg["interval"]
        log_oob(f"[dim][zzz] Dang cho {sleep_seconds}s de quet lai...[/]")
    time.sleep(sleep_seconds)


def _config_reload_loop(cfg, config_path, check_every=5):
    """Thread ngam: theo doi file cfg (oob_config.json) qua mtime, giong het
    co che load_ip_list_cached() dang dung cho danh sach IP. Khi phat hien
    file thay doi (VD: sua o cua so Menu dang chay song song), tu doc lai va
    cap nhat TRUC TIEP vao cung 1 dict `cfg` (khong tao dict moi) — nho vay
    moi noi dang giu tham chieu toi `cfg` (vong lap Luong 1, thread Luong 2,
    heartbeat...) deu thay gia tri moi ngay lan doc tiep theo, khong can
    khoi dong lai daemon.
    """
    try:
        last_mtime = os.path.getmtime(config_path)
    except OSError:
        last_mtime = 0

    while True:
        time.sleep(check_every)
        try:
            mtime = os.path.getmtime(config_path)
        except OSError:
            continue
        if mtime == last_mtime:
            continue
        last_mtime = mtime

        new_cfg = load_config(config_path)
        with ui_lock:
            cfg.update(new_cfg)
        log_oob("[cyan][CONFIG][/] Phat hien oob_config.json thay doi — da tu dong ap dung cau hinh moi.")


def run_daemon(cfg, config_path=None):
    """Vòng lặp giám sát hiển thị đa luồng chia đôi màn hình."""
    global _live_ui
    _con.print(Panel("[bold green]OOB MONITOR DAEMON[/]\n[dim]Dang giam sat lien tuc. Nhan Ctrl+C de dung.[/]", border_style="green"))
    
    if not cfg.get("password"):
        _con.print("[red][!][/] Chua cau hinh password! Vui long cau hinh truoc.")
        return

    update_ui()
    
    threading.Thread(target=run_verify_daemon, args=(cfg,), daemon=True).start()
    threading.Thread(target=_daemon_heartbeat_loop, daemon=True).start()  # #12: heartbeat
    if config_path:
        threading.Thread(target=_config_reload_loop, args=(cfg, config_path), daemon=True).start()
    else:
        log_oob("[yellow][!][/] Khong biet duong dan file config — se KHONG tu dong ap dung thay doi cau hinh.")
    
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
                    log_oob(f"[cyan][SCAN][/] [bold]{alias}[/] ({ip}) ...")
                    try:
                        hostname, menu_name, snapshot = poll_host_multi(
                            ip, cfg["telnet_port"], cfg["username"], cfg["password"],
                            cfg["enable_password"], menu_name_override=cfg.get("menu_name_override") or None,
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
                            
                            # Tu dong Verify (Chay Thread de khong dung Daemon)
                            if cfg.get("auto_verify", True):
                                threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
                            
                        else:
                            _con.print("  [dim][--] Het thoi gian hoac tu choi, se hoi lai chu ky sau.[/]")
                            
                        live.start() 
                        continue

                    if options_equal(baseline, snapshot):
                        log_oob(f"[green][OK][/] {alias}: Khop voi baseline ({menu_n} option).")
                        continue

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
                        
                        # Tu dong Verify sau khi doi Baseline (Thread ngam)
                        threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
                        
                    else:
                        _con.print(f"  [yellow][!][/] Het thoi gian hoac tu choi. Giu nguyen baseline cu cho {alias}.")
                    
                    live.start() 

                _scan_wait(cfg)

        except KeyboardInterrupt:
            pass
            
    _con.print("\n[yellow][STOP][/] Da nhan Ctrl+C. Dung Daemon.")


# ---------------------------------------------------------------------------
# Chuc nang Xem/Tim Kiem & Quản lý
# ---------------------------------------------------------------------------

def view_latest_verify_log():
    """Doc va hien thi file log verify gan nhat."""
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
        with open(latest_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        _con.print()
        _con.print(Panel(
            content, 
            title=f"[bold magenta]📝 LOG VERIFY GAN NHAT: {os.path.basename(latest_file)}[/]", 
            border_style="magenta", 
            padding=(1, 2)
        ))
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
        _con.print(f"    [cyan]→[/] OOB: [bold]{alias}[/] ({ip} - host: {dn}) | Opt [bold cyan]{key}[/] {entry.get('description', '')} [dim]→ {pr}://{entry['ip']}:{pt}[/]")

def scan_specific_devices(cfg):
    """Option 7: Quet cau hinh tuc thi (Chi dinh hoac tat ca)"""
    targets_input = _con.input("  [cyan]Nhap IP/Alias can kiem tra cau hinh (cach nhau dau phay, de trong de quet TAT CA)[/]: ").strip()
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

    _con.print(f"\n  [green][*] Bat dau quet Cấu hình tuc thi {len(hosts_to_scan)} thiet bi...[/]")
    
    for ip, alias in hosts_to_scan:
        _con.print(f"\n  [cyan][SCAN CONFIG][/] [bold]{alias}[/] ({ip}) ...")
        try:
            hostname, menu_name, snapshot = poll_host_multi(
                ip, cfg["telnet_port"], cfg["username"], cfg["password"],
                cfg["enable_password"], menu_name_override=cfg.get("menu_name_override") or None,
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
                
                if cfg.get("auto_verify", True):
                    threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
                
            else:
                _con.print("  [dim][--] Bo qua.[/]")
            continue

        if options_equal(baseline, snapshot):
            _con.print(f"  [green][OK][/] {alias}: Khop voi baseline ({menu_n} option).")
            threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
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
            
            threading.Thread(target=_thread_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
            
        else:
            _con.print(f"  [yellow][!][/] Giu nguyen baseline cu cho {alias}.")


def verify_specific_devices(cfg):
    """Option 8: Quet Verify Vat ly tuc thi hien thi truc tiep tren Terminal 1 & Hoi Push"""
    targets_input = _con.input("  [cyan]Nhap IP/Alias can Verify vat ly (cach nhau dau phay, de trong de quet TAT CA)[/]: ").strip()
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

    _con.print(f"\n  [green][*] Bat dau Verify vat ly tuc thi {len(hosts_to_scan)} thiet bi...[/]")
    
    def cli_print(msg):
        _con.print(f"    {msg}")

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
                results_map[(alias, ip, baseline)] = future.result()
            except Exception as exc:
                _con.print(f"  [red][LOI][/] {alias}: {exc}")

    for (alias, ip, baseline), results in results_map.items():
        has_warning = any(r["status"] == "CANH BAO" for r in results)
        if has_warning:
            ans = _con.input(f"\n  [bold yellow]Phat hien sai lech tren {alias} (CANH BAO). Tu dong PUSH sua Description? (y/N)[/]: ").strip().lower()
            if ans == 'y':
                process_push_and_reverify(cfg, alias, ip, baseline, results, print_fn=cli_print)
            else:
                _con.print(f"  [dim]Da bo qua viec sua Description cho {alias}.[/]")


# ---------------------------------------------------------------------------
# Import / Export Excel
# ---------------------------------------------------------------------------

def _parse_verify_logs_for_status(max_age_hours: float = 24.0) -> dict:
    """Doc log verify gan nhat cho tung OOB alias (tu file JSON), tra ve dict:
    {(alias, opt_key): {"status": ..., "act_host": ...}}
    Chi lay log trong vong max_age_hours gio gan nhat (#11).
    De su dung lam nguon du lieu cho cot Desc Status trong Export Excel."""
    log_dir = "verify-logs"
    if not os.path.exists(log_dir):
        return {}

    cutoff = time.time() - max_age_hours * 3600

    # Nhom file theo alias. Ten file: Verify_ALIAS_YYYYMMDD_HHMMSS.json
    alias_files: dict = {}
    for fname in os.listdir(log_dir):
        if not fname.endswith('.json') or not fname.startswith('Verify_'):
            continue
        body = fname[len('Verify_'):-len('.json')]  # ALIAS_YYYYMMDD_HHMMSS
        if len(body) < 17:
            continue
        alias = body[:-16]          # cat 16 ky tu cuoi = _YYYYMMDD_HHMMSS
        fpath = os.path.join(log_dir, fname)
        mtime = os.path.getmtime(fpath)
        if mtime < cutoff:          # Bo qua file qua cu (#11)
            continue
        if alias not in alias_files or mtime > alias_files[alias][1]:
            alias_files[alias] = (fpath, mtime)

    STATUS_MAP = {
        "OK":           "OK",
        "CANH BAO":     "CANH BAO",
        "KO PIVOT":     "KHONG PIVOT",
        "TIMEOUT":      "TIMEOUT",
        "YC DANG NHAP": "YEU CAU DANG NHAP",
    }
    result: dict = {}
    
    import json
    for alias, (fpath, _) in alias_files.items():
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
            
        for item in data:
            opt_key = item.get("key")
            if not opt_key: continue
            status_raw = item.get("status", "")
            act_host = item.get("act_host")
            result[(alias, opt_key)] = {
                "status": STATUS_MAP.get(status_raw, status_raw),
                "act_host": act_host if act_host not in ('-', '') else None,
            }
    return result


def _export_excel_template():
    """Tao file Excel mau (oob_import_template.xlsx) de nguoi dung tham khao dinh dang import."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        _con.print("  [red][!][/] Thieu openpyxl. Chay: pip install openpyxl")
        return
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "OOB_Import"
    for col, header in enumerate(["IP", "Alias (ten goi)"], 1):
        c = ws.cell(1, col, header)
        c.font = Font(bold=True, color="FFFFFF", size=11)
        c.fill = PatternFill(fill_type="solid", fgColor="1F4E79")
        c.alignment = Alignment(horizontal="center", vertical="center")
    for r, (ip, alias) in enumerate([("192.168.1.1", "OOB-HCM-01"),
                                      ("192.168.1.2", "OOB-HCM-02"),
                                      ("10.0.0.1",    "OOB-HAN-01")], 2):
        ws.cell(r, 1, ip)
        ws.cell(r, 2, alias)
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 24
    ws.row_dimensions[1].height = 22
    tpl = "oob_import_template.xlsx"
    wb.save(tpl)
    _con.print(f"  [green]✓[/] Da tao file mau: [bold]{tpl}[/]")
    _con.print("      Dien IP vao cot A, ten alias vao cot B (tu dong 2 tro xuong).")
    _con.print("      Sau do chon lai option \\[i] → nhap duong dan file de import.")


def import_from_excel(cfg):
    """Import danh sach thiet bi OOB tu file Excel (.xlsx) vao ip_list.
    Moi dong trong Excel: cot A = IP, cot B = Alias (ten goi).
    Tu dong bo qua IP da co trong danh sach va IP khong hop le.
    """
    try:
        import openpyxl
    except ImportError:
        _con.print("  [red][!][/] Thieu thu vien openpyxl. Cai dat bang lenh:")
        _con.print("      [bold]pip install openpyxl[/]")
        return

    want_tpl = _con.input(
        "  [cyan]Xuat file mau Excel de tham khao dinh dang? (y/N)[/]: "
    ).strip().lower()
    if want_tpl == 'y':
        _export_excel_template()
        return

    file_path = _con.input("  [cyan]Duong dan file Excel (.xlsx)[/]: ").strip()
    if not file_path:
        _con.print("  [yellow][!][/] Khong nhap duong dan. Huy.")
        return
    if not os.path.exists(file_path):
        _con.print(f"  [red][!][/] Khong tim thay file: {file_path}")
        return

    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
    except Exception as e:
        _con.print(f"  [red][!][/] Khong doc duoc file Excel: {e}")
        return

    existing     = load_ip_list(cfg["ip_list"])
    existing_ips = {h[0] for h in existing}
    ip_re        = _IP_RE
    added = skipped_dup = skipped_invalid = 0

    try:
        with open(cfg["ip_list"], "a", encoding="utf-8") as f:
            for row_idx, row in enumerate(
                ws.iter_rows(min_row=2, values_only=True), 2
            ):
                if not row or row[0] is None:
                    continue
                ip_raw    = str(row[0]).strip()
                alias_raw = (str(row[1]).strip()
                             if len(row) > 1 and row[1] is not None else "")
                if not ip_re.match(ip_raw):
                    _con.print(
                        f"  [yellow][!][/] Dong {row_idx}: '{ip_raw}' "
                        "khong phai IP hop le, bo qua."
                    )
                    skipped_invalid += 1
                    continue
                if ip_raw in existing_ips:
                    skipped_dup += 1
                    continue
                alias = alias_raw or ip_raw
                f.write(f"{ip_raw} {alias}\n")
                existing_ips.add(ip_raw)
                added += 1
    except Exception as e:
        _con.print(f"  [red][!][/] Loi khi ghi file: {e}")
        return
    finally:
        try:
            wb.close()
        except Exception:
            pass

    _con.print()
    _con.print(
        f"  [green]✓[/] Import tu [bold]{os.path.basename(file_path)}[/] hoan tat:"
    )
    _con.print(f"      Da them                  : [green bold]{added}[/] thiet bi")
    _con.print(f"      Bo qua (IP trung)         : [yellow]{skipped_dup}[/]")
    _con.print(f"      Bo qua (IP khong hop le)  : [yellow]{skipped_invalid}[/]")


def export_menu_report(cfg):
    """Xuat bao cao menu OOB ra file Excel 2 sheet:
    - Sheet 'Chi tiet': moi dong = 1 option, co cot Desc Status kiem tra description.
    - Sheet 'Tom tat' : moi dong = 1 OOB, tong hop so luong OK/SAI/Chua verify.
    Khong can ket noi thiet bi — doc tu baseline DB va file log verify da co.
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        _con.print("  [red][!][/] Thieu thu vien openpyxl. Cai dat:")
        _con.print("      [bold]pip install openpyxl[/]")
        return

    hosts = load_ip_list(cfg["ip_list"])
    if not hosts:
        _con.print("  [yellow][!][/] Danh sach IP dang trong. Chua co thiet bi nao.")
        return

    _con.print("  [cyan][*][/] Dang doc du lieu tu baseline DB va log verify...")
    verify_st = _parse_verify_logs_for_status()

    # Mau sac cot Desc Status
    C_MATCH, C_WRONG  = "C6EFCE", "FFC7CE"   # xanh la / do nhat
    C_UNVER, C_NO_CON = "FFEB9C", "FFCC99"   # vang / cam nhat
    C_NO_DS           = "D9D9D9"              # xam nhat (khong co desc)
    C_HDR, C_HDR2     = "1F4E79", "2E75B6"   # header sheet 1 / sheet 2

    def mk_fill(c): return PatternFill(fill_type="solid", fgColor=c)
    def mk_bdr():
        s = Side(style='thin', color='BFBFBF')
        return Border(left=s, right=s, top=s, bottom=s)

    wb = openpyxl.Workbook()

    # ── Sheet 1: Chi tiet ────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Chi tiet"
    h1 = ["OOB IP", "OOB Alias", "OOB Hostname", "Menu Name",
          "Option Key", "Description", "Target IP", "Target Port",
          "Protocol", "Desc Status", "Ghi chu"]
    for ci, h in enumerate(h1, 1):
        c = ws1.cell(1, ci, h)
        c.font      = Font(bold=True, color="FFFFFF", size=11)
        c.fill      = mk_fill(C_HDR)
        c.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
        c.border = mk_bdr()
    ws1.row_dimensions[1].height = 28
    ws1.freeze_panes              = "A2"
    ws1.auto_filter.ref           = f"A1:{get_column_letter(len(h1))}1"

    ri      = 2
    summary = []

    for ip, alias in hosts:
        mn, device_name, baseline = get_options_by_host(
            cfg["baseline_db"], "baseline_menu", ip
        )
        if baseline is None:
            # OOB chua co baseline — ghi 1 dong thong bao
            for ci, val in enumerate(
                [ip, alias, "(chua co baseline)",
                 "", "", "", "", "", "", "", ""], 1
            ):
                c = ws1.cell(ri, ci, val)
                c.fill      = mk_fill("F2F2F2")
                c.border    = mk_bdr()
                c.alignment = Alignment(vertical="center")
            ri += 1
            summary.append({"alias": alias, "ip": ip,
                            "hn": "(chua co baseline)", "mn": "",
                            "total": 0, "match": 0, "wrong": 0,
                            "unverif": 0, "no_conn": 0, "no_desc": 0})
            continue

        hn  = device_name or ""
        cnt = dict(match=0, wrong=0, unverif=0, no_conn=0, no_desc=0)

        for opt_key in sorted(baseline):
            opt    = baseline[opt_key]
            desc   = opt.get("description", "") or ""
            t_ip   = opt.get("ip", "")
            t_port = opt.get("port", "")
            proto  = opt.get("protocol", "telnet")

            # ── Xac dinh Desc Status ─────────────────────────────────
            if not desc:
                slabel, scolor = "Khong co desc", C_NO_DS
                note = "O description trong"
                cnt["no_desc"] += 1
            else:
                vr = verify_st.get((alias, opt_key))
                if vr is None:
                    slabel, scolor = "Chua Verify", C_UNVER
                    note = "Chua co du lieu verify (chua chay Deep Verify)"
                    cnt["unverif"] += 1
                elif vr["status"] == "OK":
                    slabel, scolor = "OK - Khop", C_MATCH
                    note = f"Hostname thuc te: {vr.get('act_host', '')}"
                    cnt["match"] += 1
                elif vr["status"] == "CANH BAO":
                    slabel, scolor = "SAI - Sai desc", C_WRONG
                    note = (
                        f"Hostname thuc: {vr.get('act_host', '')} "
                        "!= Description"
                    )
                    cnt["wrong"] += 1
                elif vr["status"] in ("TIMEOUT", "KHONG PIVOT"):
                    slabel, scolor = "Khong ket noi duoc", C_NO_CON
                    note = f"Trang thai: {vr['status']}"
                    cnt["no_conn"] += 1
                else:
                    slabel, scolor = f"? {vr['status']}", C_UNVER
                    note = vr.get("status", "")
                    cnt["unverif"] += 1

            for ci, val in enumerate(
                [ip, alias, hn, mn or "", opt_key,
                 desc, t_ip, t_port, proto, slabel, note], 1
            ):
                c = ws1.cell(ri, ci, val)
                c.border    = mk_bdr()
                c.alignment = Alignment(vertical="center")
                if ci == 10:   # cot Desc Status
                    c.fill      = mk_fill(scolor)
                    c.font      = Font(bold=True)
                    c.alignment = Alignment(
                        horizontal="center", vertical="center"
                    )
            ri += 1

        summary.append({"alias": alias, "ip": ip, "hn": hn,
                        "mn": mn or "", "total": len(baseline), **cnt})

    for i, w in enumerate([16, 16, 18, 20, 12, 38, 16, 12, 10, 22, 44], 1):
        ws1.column_dimensions[get_column_letter(i)].width = w

    # ── Sheet 2: Tom tat ─────────────────────────────────────────────────
    ws2 = wb.create_sheet("Tom tat")
    h2 = ["OOB Alias", "OOB IP", "Hostname OOB", "Menu",
          "Tong Option", "OK Khop", "SAI",
          "Chua Verify", "Khong KN", "Khong Desc"]
    for ci, h in enumerate(h2, 1):
        c = ws2.cell(1, ci, h)
        c.font      = Font(bold=True, color="FFFFFF", size=11)
        c.fill      = mk_fill(C_HDR2)
        c.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
        c.border = mk_bdr()
    ws2.row_dimensions[1].height = 32
    ws2.freeze_panes              = "A2"
    ws2.auto_filter.ref           = f"A1:{get_column_letter(len(h2))}1"

    for ri2, sd in enumerate(summary, 2):
        vals = [sd["alias"], sd["ip"], sd["hn"], sd["mn"],
                sd["total"], sd["match"], sd["wrong"],
                sd["unverif"], sd["no_conn"], sd["no_desc"]]
        for ci, val in enumerate(vals, 1):
            c = ws2.cell(ri2, ci, val)
            c.border    = mk_bdr()
            c.alignment = Alignment(
                vertical="center",
                horizontal="center" if ci > 4 else "left"
            )
        if sd.get("wrong", 0) > 0:
            ws2.cell(ri2, 7).fill = mk_fill(C_WRONG)
            ws2.cell(ri2, 7).font = Font(bold=True)
        if sd.get("match", 0) > 0:
            ws2.cell(ri2, 6).fill = mk_fill(C_MATCH)

    for i, w in enumerate([18, 16, 20, 20, 14, 10, 10, 14, 12, 13], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # ── Sheet 3: Canh bao (chi chua cac option co van de) (#14) ──────────
    ws3 = wb.create_sheet("Canh bao")
    h3 = ["OOB IP", "OOB Alias", "OOB Hostname", "Menu Name",
          "Option Key", "Description", "Target IP", "Target Port",
          "Protocol", "Desc Status", "Ghi chu"]
    for ci, h in enumerate(h3, 1):
        c = ws3.cell(1, ci, h)
        c.font      = Font(bold=True, color="FFFFFF", size=11)
        c.fill      = mk_fill("C00000")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border    = mk_bdr()
    ws3.row_dimensions[1].height = 28
    ws3.freeze_panes              = "A2"
    ws3.auto_filter.ref           = f"A1:{get_column_letter(len(h3))}1"

    alert_statuses = {"SAI - Sai desc", "Khong ket noi duoc"}
    ri3 = 2
    for row_idx in range(2, ri):
        status_val = ws1.cell(row_idx, 10).value
        if status_val in alert_statuses:
            for ci in range(1, len(h3) + 1):
                src = ws1.cell(row_idx, ci)
                dst = ws3.cell(ri3, ci, src.value)
                dst.border    = mk_bdr()
                dst.alignment = Alignment(vertical="center")
                if ci == 10:
                    dst.fill  = mk_fill(C_WRONG)
                    dst.font  = Font(bold=True)
                    dst.alignment = Alignment(horizontal="center", vertical="center")
            ri3 += 1

    if ri3 == 2:
        ws3.cell(2, 1, "(Khong co option nao co van de trong 24h gan nhat)")

    for i, w in enumerate([16, 16, 18, 20, 12, 38, 16, 12, 10, 22, 44], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    # Luu file
    os.makedirs("reports", exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join("reports", f"OOB_Menu_Report_{ts}.xlsx")
    wb.save(out)
    warn_count = ri3 - 2
    _con.print(f"\n  [green]✓[/] Da xuat: [bold]{out}[/]")
    _con.print(f"      Sheet 'Chi tiet': {ri - 2} dong option")
    _con.print(f"      Sheet 'Tom tat' : {len(summary)} OOB")
    _con.print(f"      Sheet 'Canh bao': [{'red bold' if warn_count else 'green'}]{warn_count}[/] option co van de (SAI desc/Khong KN)")
    _con.print(
        "      [dim]Mo bang Excel hoac LibreOffice de xem day du mau sac va auto-filter.[/]"
    )


# ---------------------------------------------------------------------------
# Daemon Heartbeat (PID file, #12)
# ---------------------------------------------------------------------------

DAEMON_PID_FILE = "daemon.pid"

def _daemon_heartbeat_loop():
    """Thread ngam: ghi file daemon.pid moi 30s de menu biet daemon con song."""
    while True:
        try:
            with open(DAEMON_PID_FILE, "w") as f:
                f.write(f"{os.getpid()}\n{datetime.now().isoformat()}\n")
        except OSError:
            pass
        time.sleep(30)

def _get_daemon_status() -> str:
    """Kiem tra trang thai daemon qua file PID heartbeat. Tra ve chuoi Rich markup."""
    try:
        with open(DAEMON_PID_FILE, "r") as f:
            lines = f.read().splitlines()
        pid = int(lines[0])
        ts  = datetime.fromisoformat(lines[1])
        age = (datetime.now() - ts).total_seconds()
        if age < 90:   # Con song neu cap nhat trong vong 90 giay
            return f"[green bold]RUNNING[/] [dim](PID {pid}, {int(age)}s ago)[/]"
        return f"[yellow bold]STALE[/] [dim](cap nhat {int(age)}s truoc)[/]"
    except Exception:
        return "[dim]KHONG RO (chua chay daemon?)[/]"


def _show_menu(cfg):
    hosts_n = len(load_ip_list_cached(cfg["ip_list"]))
    user = f"[bold]{cfg['username']}[/]" if cfg["username"] else "[dim yellow](chua dat)[/]"
    menu_label = cfg['menu_name_override'] or "tu dong do"
    daemon_status = _get_daemon_status()
    
    info = Text.from_markup(
        f"Thiet bi : [bold]{hosts_n}[/]   Menu: [bold]{menu_label}[/]   User: {user}\n"
        f"Daemon   : {daemon_status}"
    )
    
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", min_width=5, justify="right")
    grid.add_column(min_width=40)
    
    grid.add_row("[1]", "Them thiet bi OOB")
    grid.add_row("[2]", "Xoa thiet bi OOB")
    grid.add_row("\\[i]", "[cyan]Import danh sach OOB tu file Excel (.xlsx)[/]")
    grid.add_row("", "")
    grid.add_row("[3]", "Cau hinh (username/password/port...)")
    grid.add_row("[4]", "Xem danh sach thiet bi")
    grid.add_row("[5]", "Xem baseline (Chuan)")
    grid.add_row("[6]", "Tim kiem thiet bi")
    grid.add_row("[7]", "[bold green]Quet kiem tra Cau hinh tuc thi (Chi dinh hoac Tat ca)[/]")
    grid.add_row("[8]", "[bold magenta]Deep Verify Vat ly tuc thi (Chi dinh hoac Tat ca)[/]")
    grid.add_row("[9]", "[dim magenta]Xem ket qua Verify vat ly gan nhat[/]")
    grid.add_row("\\[e]", "[yellow]Xuat bao cao menu OOB ra Excel (tat ca thiet bi)[/]")
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
        elif choice == "8":
            verify_specific_devices(cfg)
        elif choice == "9":
            view_latest_verify_log()
        elif choice == "i":
            import_from_excel(cfg)
        elif choice == "e":
            export_menu_report(cfg)
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
        run_daemon(cfg, config_path)
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
        run_daemon(cfg, config_path)
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