"""Web Console: mo phien terminal tuong tac toi thiet bi dau xa cua 1 line
(pivot qua OOB) va chuyen tiep byte 2 chieu cho trinh duyet (xterm.js).

Luong: dang nhap OOB (connect_auto, thu lan luot cac credential) -> pivot
    Cisco : 'telnet <ip> <port>' (hoac 'ssh -l admin <ip>')
    Vertiv: 'cd /access/' + 'connect <ten port>'
-> 1 thread doc byte tho tu socket, day vao bo dem; trinh duyet nhan qua SSE
va gui phim go qua POST. Phien tu dong dong khi khong co tuong tac
CONSOLE_IDLE_TIMEOUT giay hoac khi ket noi bi dong.
"""

import base64
import secrets
import socket
import threading
import time

import oob_lib

CONSOLE_IDLE_TIMEOUT = 15 * 60
CONSOLE_MAX_SESSIONS = 10
_BUF_LIMIT = 256 * 1024          # giu toi da 256KB output gan nhat (de nguoi
                                 # vao lai/reload trang van thay man hinh cu)

_sessions = {}
_lock = threading.Lock()


class ConsoleSession:
    def __init__(self, sid, owner, ip, key, label):
        self.sid, self.owner, self.ip, self.key, self.label = sid, owner, ip, key, label
        self.sess = None
        self.chunks = []           # [(seq, bytes)]
        self.seq = 0
        self.base_seq = 0          # seq cua chunk dau con giu trong bo dem
        self.size = 0
        self.cond = threading.Condition()
        self.closed = False
        self.close_reason = ""
        self.last_active = time.time()

    # ---- output ----
    def push(self, data: bytes):
        if not data:
            return
        with self.cond:
            self.seq += 1
            self.chunks.append((self.seq, data))
            self.size += len(data)
            while self.size > _BUF_LIMIT and len(self.chunks) > 1:
                _s, d = self.chunks.pop(0)
                self.size -= len(d)
            self.base_seq = self.chunks[0][0]
            self.cond.notify_all()

    def info(self, text):
        self.push(("\r\n\x1b[36m[console] " + text + "\x1b[0m\r\n").encode())

    def read_since(self, after_seq, timeout=20):
        """Cho output moi hon after_seq. Tra ve (seq_moi_nhat, bytes, closed)."""
        with self.cond:
            if self.seq <= after_seq and not self.closed:
                self.cond.wait(timeout)
            data = b"".join(d for s, d in self.chunks if s > after_seq)
            return self.seq, data, self.closed

    def close(self, reason=""):
        with self.cond:
            if self.closed:
                return
            self.closed = True
            self.close_reason = reason
            self.cond.notify_all()
        if reason:
            self.info(reason)
        try:
            if self.sess:
                self.sess.close()
        except Exception:
            pass

    # ---- input ----
    def send(self, data: bytes):
        self.last_active = time.time()
        if self.closed or not self.sess:
            return False
        try:
            if isinstance(self.sess, oob_lib.MiniSSH):
                self.sess._shell.send(data)
            else:
                self.sess.sock.sendall(data)
            return True
        except Exception as e:
            self.close(f"Mat ket noi khi gui: {e}")
            return False

    # ---- doc tho tu socket ----
    def _recv_raw(self):
        s = self.sess
        if isinstance(s, oob_lib.MiniSSH):
            s._shell.settimeout(1.0)
            try:
                d = s._shell.recv(4096)
            except socket.timeout:
                return b""
            if not d:
                raise EOFError
            return d
        s.sock.settimeout(1.0)
        try:
            d = s.sock.recv(4096)
        except socket.timeout:
            return b""
        if not d:
            raise EOFError
        return s._strip_iac(d)

    def reader_loop(self):
        while not self.closed:
            if time.time() - self.last_active > CONSOLE_IDLE_TIMEOUT:
                self.close(f"Tu dong dong sau {CONSOLE_IDLE_TIMEOUT // 60} phut khong thao tac.")
                break
            try:
                d = self._recv_raw()
            except (EOFError, OSError):
                self.close("Thiet bi da dong ket noi.")
                break
            if d:
                self.push(d)


def _connect_and_pivot(cs, cfg, oob_ip, opt, creds):
    vendor = str(opt.get("vendor", "cisco")).lower()
    last_err = None
    cs.info(f"Dang ket noi OOB {oob_ip} ...")
    for c in creds:
        try:
            cs.sess = oob_lib.connect_auto(oob_ip, cfg.get("ssh_port", 22), cfg.get("telnet_port", 23),
                                           c["username"], c["password"], c["enable_password"], timeout=8)
            break
        except Exception as e:
            last_err = e
    if cs.sess is None:
        cs.close(f"Khong dang nhap duoc OOB: {last_err}")
        return

    # Bo phan banner/prompt da doc trong luc dang nhap (con nam trong buffer).
    cs.sess.buffer = b""
    if vendor == "vertiv":
        cmd = f"connect {opt.get('description', '')}"
        cs.send(b"cd /access/\r")
        time.sleep(0.5)
        try:
            cs._recv_raw()
        except Exception:
            pass
    elif opt.get("protocol") == "ssh":
        cmd = f"ssh -l admin {opt.get('ip')}"
    else:
        cmd = f"telnet {opt.get('ip')} {opt.get('port', 23)}"
    cs.info(f"Pivot: {cmd}  (Enter neu man hinh chua hien prompt)")
    cs.send((cmd + "\r").encode())
    threading.Thread(target=cs.reader_loop, daemon=True).start()


def open_console(cfg, owner, oob_ip, key, opt, alias, creds):
    with _lock:
        _gc_locked()
        live = [s for s in _sessions.values() if not s.closed]
        if len(live) >= CONSOLE_MAX_SESSIONS:
            raise RuntimeError(f"Dang co {len(live)} phien console mo (toi da {CONSOLE_MAX_SESSIONS}). Dong bot phien cu.")
        sid = secrets.token_urlsafe(16)
        cs = ConsoleSession(sid, owner, oob_ip, key, f"{alias} / Opt {key} - {opt.get('description', '')}")
        _sessions[sid] = cs
    threading.Thread(target=_connect_and_pivot, args=(cs, cfg, oob_ip, opt, creds), daemon=True).start()
    return cs


def get(sid, owner):
    with _lock:
        cs = _sessions.get(sid)
    return cs if cs and cs.owner == owner else None


def close(sid, owner):
    cs = get(sid, owner)
    if cs:
        cs.close("Da dong phien console.")
        with _lock:
            _sessions.pop(sid, None)
    return cs is not None


def _gc_locked():
    now = time.time()
    for sid, s in list(_sessions.items()):
        if s.closed and now - s.last_active > 60:
            _sessions.pop(sid, None)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
