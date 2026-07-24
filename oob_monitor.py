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
}

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
    update_ui()

def log_verify(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    verify_logs.append(f"[dim]\\[{ts}][/] {msg}")
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
v. Chu ky Verify vat ly (s): {cfg.get('verify_interval', 3600)}
8. File danh sach IP    : {cfg['ip_list']}
9. File baseline DB     : {cfg['baseline_db']}
a. File snapshot DB     : {cfg['snapshot_db']}
b. Tu dong Verify ngam  : {'[BAT]' if cfg.get('auto_verify', True) else '[TAT]'}
c. Tu dong Sua loi ngam : {'[BAT]' if cfg.get('auto_push_desc', True) else '[TAT]'}
0. Quay lai menu chinh
--------------------------------------------------""")
        choice = input("Chon muc can sua: ").strip().lower()

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
    if "protocol" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN protocol TEXT DEFAULT 'telnet'")
    conn.commit()
    return conn

def get_options_by_host(db_path, table, host):
    conn = _init_db(db_path, table)
    cur = conn.execute(
        f"SELECT menu_name, option_key, device_name, description, target_ip, target_port, protocol "
        f"FROM {table} WHERE host=?", (host,)
    )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return None, None, None
    menu_name   = rows[0][0]
    device_name = rows[0][2]
    options = {
        key: {"description": desc, "ip": ip, "port": port, "protocol": proto}
        for _mn, key, _dn, desc, ip, port, proto in rows
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
            f"target_ip, target_port, protocol, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (host, menu_name, key, device_name, entry.get("description", ""), entry["ip"],
             entry.get("port", 23), entry.get("protocol", "telnet"), now),
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
    return re.sub(r'\W+', '', raw_key)

def poll_host_multi(ip, telnet_port, username, password, enable_password, menu_name_override=None, ssh_port=22, timeout=10):
    tn = connect_auto(ip, ssh_port, telnet_port, username, password, enable_password, timeout=timeout)
    try:
        hostname = fetch_hostname(tn)
        tn.write("terminal length 0")
        tn.read_until("#", timeout=5)
        tn.write("show running-config | include menu")
        raw = tn.read_until("#", timeout=15)
        
        detected_names = list(set(MENU_NAME_RE.findall(raw)))
        if menu_name_override:
            menu_names = [menu_name_override] if menu_name_override in detected_names else []
        else:
            menu_names = detected_names
            
        if not menu_names:
            return hostname, None, {}
            
        all_options = {}
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            
            m = TEXT_RE.match(line)
            if m and m.group(1) in menu_names:
                m_name, raw_k, desc = m.group(1), m.group(2), m.group(3).strip()
                k = f"{m_name} [{clean_key(raw_k)}]" if len(menu_names) > 1 else clean_key(raw_k)
                all_options.setdefault(k, {})["description"] = desc
                continue
                
            m = CMD_TELNET_RE.match(line)
            if m and m.group(1) in menu_names:
                m_name, raw_k, target_ip = m.group(1), m.group(2), m.group(3)
                port = int(m.group(4)) if m.group(4) else 23
                k = f"{m_name} [{clean_key(raw_k)}]" if len(menu_names) > 1 else clean_key(raw_k)
                entry = all_options.setdefault(k, {})
                entry["ip"], entry["port"], entry["protocol"] = target_ip, port, "telnet"
                continue
                
            m = CMD_SSH_RE.match(line)
            if m and m.group(1) in menu_names:
                m_name, raw_k, target_ip = m.group(1), m.group(2), m.group(3)
                port = int(m.group(4)) if m.group(4) else 22
                k = f"{m_name} [{clean_key(raw_k)}]" if len(menu_names) > 1 else clean_key(raw_k)
                entry = all_options.setdefault(k, {})
                entry["ip"], entry["port"], entry["protocol"] = target_ip, port, "ssh"

        final_options = {k: v for k, v in all_options.items() if "ip" in v}
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
    """Lọc hostname từ luồng ký tự trả về của Console."""
    output = re.sub(r'\x1b\[.*?m', '', output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    auth_seen = False
    
    for line in reversed(lines):
        if any(x in line for x in ["telnet ", "ssh ", "Trying ", "Open", "Connection refused", "disconnect", "clear line"]):
            continue

        if re.search(r'refused|time(d)?[\s-]?out|unreachable|no route to host|unknown host|% ', line, re.IGNORECASE):
            continue
            
        m = re.search(r'([A-Za-z0-9_\-\.]+)[>#]', line)
        if m: return m.group(1)
            
        m_login = re.search(r'([A-Za-z0-9_\-\.]+)\s+login:', line, re.IGNORECASE)
        if m_login: return m_login.group(1)
            
        m_bsd = re.search(r'\(([A-Za-z0-9_\-\.]+)\)\s*\(tty', line, re.IGNORECASE)
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
    """Thực thi verify vật lý (PIVOT Mới + Smart Clear) va xuat log."""
    if print_fn is None:
        print_fn = log_verify
        
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

    def check_port_via_oob(t_ip, t_port, proto):
        session = connect_auto(
            oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"], 
            cfg["username"], cfg["password"], cfg["enable_password"], timeout=8
        )
        session.write("terminal length 0")
        session.read_until("#", timeout=2)
        
        cmd = f"ssh -l admin {t_ip}" if proto == "ssh" else f"telnet {t_ip} {t_port}"
        session.write(cmd)
        time.sleep(1.5) 
        session.write("\r\n\r\n") 
        
        out = session.read_until([">", "#", "login:", "Username:", "Password:", "Connection refused", "refused", "unknown"], timeout=5)
        session.close() 
        return out

    def clear_line_via_oob(t_port):
        line_num = t_port - 2000
        if line_num <= 0: return False
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

    for key, opt in options.items():
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
            
        desc_clean = re.sub(r'^[-=>\s]+', '', desc).strip().lower()
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

    report_text = _build_verify_report(alias, oob_ip, own_hostname, results)
    with open(log_file_path, "w", encoding="utf-8") as f:
        f.write(report_text)

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
    """Xử lý đẩy cấu hình tự động cho các Option báo CANH BAO."""
    if print_fn is None: print_fn = log_verify
    
    warnings = [r for r in verify_results if r["status"] == "CANH BAO"]
    if not warnings:
        return

    updates = {}
    push_log_entries = []
    
    mn, dn, _ = get_options_by_host(cfg["baseline_db"], "baseline_menu", oob_ip)
    if not mn:
        return

    for w in warnings:
        key = w["key"]
        act_host = w["act_host"]
        target_ip = baseline[key].get("ip")
        
        if check_ip_collision(cfg, target_ip, oob_ip):
            print_fn(f"[yellow][!][/] {alias} (Opt {key}): Nghi ngo trung IP {target_ip} tren nhieu OOB. Bo qua tu dong sua.")
            continue
            
        new_desc = f"----> {act_host}"
        updates[key] = new_desc
        push_log_entries.append({
            "key": key, "target_ip": target_ip,
            "old": w["desc"], "new": new_desc
        })

    if not updates: return

    print_fn(f"[*] Dang tu dong PUSH sua loi {len(updates)} option cho {alias}...")
    success = push_menu_descriptions(
        oob_ip, cfg.get("ssh_port", 22), cfg["telnet_port"],
        cfg["username"], cfg["password"], cfg["enable_password"],
        mn, updates, timeout=10
    )

    if not success:
        print_fn(f"[red][LOI][/] {alias}: Push cau hinh that bai.")
        return

    os.makedirs("push-logs", exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join("push-logs", f"Push_{alias}_{ts_str}.log")
    
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"=== PUSH LOG TỰ ĐỘNG: {alias} ({oob_ip}) ===\n")
        f.write(f"Thoi gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        for entry in push_log_entries:
            key = entry["key"]
            baseline[key]["description"] = entry["new"] 
            
            f.write(f"- Option [{key}] (Target IP: {entry['target_ip']}):\n")
            f.write(f"  + Cu : {entry['old']}\n")
            f.write(f"  + Moi: {entry['new']}\n")
            f.write(f"  + REVERT CMD (Hồi phục): menu {mn} text {key} {entry['old']}\n\n")

    save_options(cfg["baseline_db"], "baseline_menu", oob_ip, mn, dn, baseline)
    print_fn(f"[green]✓[/] Da cap nhat cau hinh & Baseline (Khong Auto-save write memory).")

    print_fn(f"[*] Tu dong Re-Verify lai cac option vua sua tren {alias}...")
    subset_options = {k: baseline[k] for k in updates.keys()}
    run_deep_verify(cfg, alias, oob_ip, subset_options, print_fn=print_fn)


# ---------------------------------------------------------------------------
# Vòng lặp giám sát (Daemon Thread)
# ---------------------------------------------------------------------------

def run_verify_daemon(cfg):
    """Tiến trình Daemon thứ 2 chuyên lặp lịch Deep Verify độc lập & Tự động phục hồi."""
    verify_interval = cfg.get("verify_interval", 3600)
    log_verify(f"[green][START][/] Khoi dong chu ky Verify vat ly moi {verify_interval}s.")
    
    time.sleep(15) 
    
    while True:
        hosts = load_ip_list(cfg["ip_list"])
        if not hosts:
            time.sleep(verify_interval)
            continue
            
        for ip, alias in hosts:
            _mn, _dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
            if baseline:
                results = run_deep_verify(cfg, alias, ip, baseline)
                process_push_and_reverify(cfg, alias, ip, baseline, results)
                
        log_verify(f"[dim][zzz] Dang cho {verify_interval}s cho dot Verify tiep theo...[/]")
        time.sleep(verify_interval)

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

def run_daemon(cfg):
    """Vòng lặp giám sát hiển thị đa luồng chia đôi màn hình."""
    global _live_ui
    _con.print(Panel("[bold green]OOB MONITOR DAEMON[/]\n[dim]Dang giam sat lien tuc. Nhan Ctrl+C de dung.[/]", border_style="green"))
    
    if not cfg.get("password"):
        _con.print("[red][!][/] Chua cau hinh password! Vui long cau hinh truoc.")
        return

    update_ui()
    
    threading.Thread(target=run_verify_daemon, args=(cfg,), daemon=True).start()
    
    with Live(layout, refresh_per_second=4, screen=False) as live:
        _live_ui = live
        log_oob(f"[green][START][/] Khoi dong chu ky Config moi {cfg['interval']}s.")
        
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
                            def do_verify_and_push(c, al, i, sn):
                                res = run_deep_verify(c, al, i, sn)
                                process_push_and_reverify(c, al, i, sn, res)
                            threading.Thread(target=do_verify_and_push, args=(cfg, alias, ip, snapshot), daemon=True).start()
                            
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
                        def do_verify_and_push_2(c, al, i, sn):
                            res = run_deep_verify(c, al, i, sn)
                            process_push_and_reverify(c, al, i, sn, res)
                        threading.Thread(target=do_verify_and_push_2, args=(cfg, alias, ip, snapshot), daemon=True).start()
                        
                    else:
                        _con.print(f"  [yellow][!][/] Het thoi gian hoac tu choi. Giu nguyen baseline cu cho {alias}.")
                    
                    live.start() 

                log_oob(f"[dim][zzz] Dang cho {cfg['interval']}s de quet lai...[/]")
                time.sleep(cfg["interval"])

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
                
                def do_verify_and_push_manual_1(c, al, i, sn):
                    res = run_deep_verify(c, al, i, sn)
                    process_push_and_reverify(c, al, i, sn, res)
                threading.Thread(target=do_verify_and_push_manual_1, args=(cfg, alias, ip, snapshot), daemon=True).start()
                
            else:
                _con.print("  [dim][--] Bo qua.[/]")
            continue

        if options_equal(baseline, snapshot):
            _con.print(f"  [green][OK][/] {alias}: Khop voi baseline ({menu_n} option).")
            def do_verify_and_push_manual_2(c, al, i, sn):
                res = run_deep_verify(c, al, i, sn)
                process_push_and_reverify(c, al, i, sn, res)
            threading.Thread(target=do_verify_and_push_manual_2, args=(cfg, alias, ip, snapshot), daemon=True).start()
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
            
            def do_verify_and_push_manual_3(c, al, i, sn):
                res = run_deep_verify(c, al, i, sn)
                process_push_and_reverify(c, al, i, sn, res)
            threading.Thread(target=do_verify_and_push_manual_3, args=(cfg, alias, ip, snapshot), daemon=True).start()
            
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

    for ip, alias in hosts_to_scan:
        _con.print(f"\n  [cyan][VERIFY][/] [bold]{alias}[/] ({ip}) ...")
        _mn, _dn, baseline = get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        
        if not baseline:
            _con.print(f"  [yellow][!][/] OOB nay chua co Baseline. Vui long quet cau hinh truoc (Option 7)!")
            continue
            
        # Chạy đồng bộ Verify
        results = run_deep_verify(cfg, alias, ip, baseline, print_fn=cli_print)
        
        # Kiểm tra Cảnh báo để gọi Self-healing ở chế độ thủ công
        has_warning = any(r["status"] == "CANH BAO" for r in results)
        if has_warning:
            ans = _con.input("\n  [bold yellow]Phat hien sai lech thuc te (CANH BAO). Ban co muon tiep tuc tinh nang tu dong PUSH sua Description khong? (y/N)[/]: ").strip().lower()
            if ans == 'y':
                process_push_and_reverify(cfg, alias, ip, baseline, results, print_fn=cli_print)
            else:
                _con.print("  [dim]Da bo qua viec sua Description.[/]")


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
    grid.add_row("[7]", "[bold green]Quet kiem tra Cấu hình tức thì (Chỉ định hoac Tat ca)[/]")
    grid.add_row("[8]", "[bold magenta]Deep Verify Vật lý tức thì (Chỉ định hoac Tat ca)[/]")
    grid.add_row("[9]", "[dim magenta]Xem ket qua Verify vat ly gan nhat[/]")
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