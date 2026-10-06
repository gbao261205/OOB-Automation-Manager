"""Thiet bi OOB Cisco (terminal server / async menu) GIA LAP qua Telnet tren
socket TCP that - de chay END-TO-END dung code ket noi that (MiniTelnet,
connect_auto, poll_host_multi, run_deep_verify, push_menu_descriptions) thay
vi mock. Mo phong hanh vi IOS o muc du dung cho tool nay:

- IAC negotiation luc ket noi (IAC WILL ECHO / WILL SGA / DO TTYPE).
- Login Username/Password -> user mode '>' -> 'enable' + password -> '#'.
- Echo lai lenh go vao (tru o nhap mat khau), moi lan Enter tra 1 prompt.
  CR, LF, CRLF deu tinh la MOT lan Enter (giong IOS).
- show running-config | include ^hostname / include menu, terminal length 0.
- configure terminal -> 'menu X text K DESC' cap nhat cau hinh menu that.
- Reverse telnet 'telnet <ip> <port>' sang tung line console: line con song
  thi in prompt cua thiet bi dich, line chet thi im lang.
- Ctrl+^ x (b'\\x1ex') de suspend phien, 'disconnect' + [confirm].
- 'clear line N' + [confirm].

Moi lenh nhan duoc o che do priv/config duoc ghi vao self.commands de test
co the assert (vd: dry-run KHONG BAO GIO duoc gui 'configure terminal')."""

import socket
import threading

IAC, WILL, WONT, DO, DONT = 255, 251, 252, 253, 254


class FakeCiscoOOB:
    def __init__(self, hostname="OOB-HCM-01", username="admin", password="secret",
                 enable_password="enpass", menu_name="MAINMENU", lines=None, banner=""):
        """lines: {key: {"ip": str, "port": int, "text": str, "target": str|None}}
        target = hostname thiet bi that su dang cam o line do (None = line chet)."""
        self.hostname = hostname
        self.username, self.password, self.enable_password = username, password, enable_password
        self.menu_name = menu_name
        self.banner = banner  # banner exec in ra sau dang nhap, truoc prompt
        self.lines = {k: dict(v) for k, v in (lines or {}).items()}
        self.commands = []
        self.connections = 0
        self._lock = threading.Lock()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = self._srv.getsockname()[1]
        self._stopped = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    # ------------------------------------------------------------------ server
    def stop(self):
        self._stopped = True
        try: self._srv.close()
        except OSError: pass

    def menu_text(self, key):
        with self._lock:
            return self.lines[key]["text"]

    def _session_cls(self):
        return _Session

    def _accept_loop(self):
        while not self._stopped:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with self._lock:
                self.connections += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    # --------------------------------------------------------------- session
    def _handle(self, conn):
        s = self._session_cls()(self, conn)
        try:
            s.run()
        except OSError:
            pass
        finally:
            try: conn.close()
            except OSError: pass


class _Session:
    def __init__(self, dev, conn):
        self.dev, self.conn = dev, conn
        self.state = "username"
        self.reverse_line = None      # key cua line dang reverse-telnet
        self.suspended_line = None    # key cua line da suspend (Ctrl+^ x)
        self._last_cr = False
        self._iac_skip = 0

    def send(self, text):
        self.conn.sendall(text.encode())

    @property
    def prompt(self):
        return f"{self.dev.hostname}#"

    # state -> (chuoi byte escape, ten method xu ly) - phim tat thoat phien
    ESCAPES = {"reverse": (b"\x1ex", "_suspend")}

    def greeting(self):
        self.send("\r\n\r\nUser Access Verification\r\n\r\nUsername: ")

    def run(self):
        # IOS bat dau bang negotiation - client phai tra loi WONT/DONT va bo qua
        self.conn.sendall(bytes([IAC, WILL, 1, IAC, WILL, 3, IAC, DO, 24]))
        self.greeting()
        buf = b""
        while True:
            data = self.conn.recv(4096)
            if not data:
                return
            data = self._strip_iac(data)
            self._on_raw(data)
            esc = self.ESCAPES.get(self.state)
            if esc and esc[0] in (buf + data):
                combined = buf + data
                idx = combined.index(esc[0])
                combined = combined[idx + len(esc[0]):]
                getattr(self, esc[1])()
                buf = b""
                data = combined
            for line in self._split_lines(buf + data):
                if line is None:
                    continue
                if self._on_line(line) == "close":
                    return
            buf = self._pending

    def _on_raw(self, data):
        """Hook: byte tho (da bo IAC) nhan duoc, truoc khi tach dong."""

    def _strip_iac(self, data):
        out = bytearray()
        for b in data:
            if self._iac_skip:
                self._iac_skip -= 1
                continue
            if b == IAC:
                self._iac_skip = 2
                continue
            out.append(b)
        return bytes(out)

    def _split_lines(self, data):
        """CR, LF, CRLF deu la 1 Enter. Giu phan chua ket thuc trong
        self._pending. Tra ve list cac dong (str) da hoan chinh."""
        lines, cur = [], bytearray()
        for b in data:
            if b == 0x0A and self._last_cr:   # LF ngay sau CR -> cung 1 Enter
                self._last_cr = False
                continue
            self._last_cr = False
            if b in (0x0D, 0x0A):
                self._last_cr = (b == 0x0D)
                lines.append(cur.decode(errors="ignore"))
                cur = bytearray()
            else:
                cur.append(b)
        self._pending = bytes(cur)
        return lines

    def _suspend(self):
        self.suspended_line = self.reverse_line
        self.reverse_line = None
        self.state = "priv"
        self.send(f"\r\n{self.prompt}")

    # -------------------------------------------------------------- state machine
    def _on_line(self, line):
        dev = self.dev
        st = self.state
        if st == "username":
            self.send(line + "\r\nPassword: ")
            self._user = line
            self.state = "password"
        elif st == "password":
            if self._user == dev.username and line == dev.password:
                self.state = "user"
                if dev.banner:
                    # banner exec va prompt den o 2 goi TCP rieng (nhu thiet bi that)
                    import time as _t
                    self.send(f"\r\n{dev.banner}")
                    _t.sleep(0.2)
                self.send(f"\r\n\r\n{dev.hostname}>")
            else:
                self.state = "username"
                self.send("\r\n% Login invalid\r\n\r\nUsername: ")
        elif st == "user":
            self.send(line)
            if line.strip() == "enable":
                self.state = "enable_pw"
                self.send("\r\nPassword: ")
            elif line.strip() in ("exit", "logout"):
                return "close"
            else:
                self.send(f"\r\n{dev.hostname}>")
        elif st == "enable_pw":
            if line == dev.enable_password:
                self.state = "priv"
                self.send(f"\r\n{self.prompt}")
            else:
                self.state = "user"
                self.send(f"\r\n% Access denied\r\n\r\n{dev.hostname}>")
        elif st == "priv":
            return self._priv(line)
        elif st == "config":
            return self._config(line)
        elif st == "reverse":
            target = dev.lines[self.reverse_line]["target"]
            if target:  # thiet bi dich con song: moi Enter in lai prompt cua no
                self.send(f"\r\n{target}>")
        elif st == "confirm_clear":
            self.state = "priv"
            self.send(f"\r\n[OK]\r\n{self.prompt}")
        elif st == "confirm_disc":
            self.suspended_line = None
            self.state = "priv"
            self.send(f"\r\n{self.prompt}")

    def _priv(self, line):
        dev = self.dev
        cmd = line.strip()
        self.send(line)
        if cmd:
            with dev._lock:
                dev.commands.append(cmd)
        if cmd == "":
            self.send(f"\r\n{self.prompt}")
        elif cmd == "terminal length 0":
            self.send(f"\r\n{self.prompt}")
        elif cmd == "show running-config | include ^hostname":
            self.send(f"\r\nhostname {dev.hostname}\r\n{self.prompt}")
        elif cmd == "show running-config | include menu":
            out = []
            with dev._lock:
                for k, ln in dev.lines.items():
                    out.append(f"menu {dev.menu_name} text {k} {ln['text']}")
                    out.append(f"menu {dev.menu_name} command {k} telnet {ln['ip']} {ln['port']}")
            self.send("\r\n" + "\r\n".join(out) + f"\r\n{self.prompt}")
        elif cmd == "configure terminal":
            self.state = "config"
            self.send(f"\r\nEnter configuration commands, one per line.  End with CNTL/Z.\r\n{dev.hostname}(config)#")
        elif cmd.startswith("telnet "):
            parts = cmd.split()
            port = int(parts[2]) if len(parts) > 2 else 23
            key = next((k for k, ln in dev.lines.items() if ln["port"] == port), None)
            if key is None:
                self.send(f"\r\nTrying {parts[1]}, {port} ...\r\n% Connection refused by remote host\r\n{self.prompt}")
                return
            self.reverse_line, self.state = key, "reverse"
            self.send(f"\r\nTrying {parts[1]}, {port} ... Open\r\n")
            target = dev.lines[key]["target"]
            if target:
                self.send(f"\r\n{target}>")
        elif cmd.startswith("clear line "):
            self.state = "confirm_clear"
            self.send("\r\n[confirm]")
        elif cmd == "disconnect":
            if self.suspended_line is not None:
                ln = dev.lines[self.suspended_line]
                self.state = "confirm_disc"
                self.send(f"\r\nClosing connection to {ln['ip']} [confirm]")
            else:
                self.send(f"\r\n% No connection to close\r\n{self.prompt}")
        elif cmd in ("exit", "logout"):
            return "close"
        else:
            self.send(f"\r\n% Invalid input detected at '^' marker.\r\n{self.prompt}")

    def _config(self, line):
        dev = self.dev
        cmd = line.strip()
        self.send(line)
        if cmd:
            with dev._lock:
                dev.commands.append(cmd)
        parts = cmd.split(None, 4)
        if cmd == "end":
            self.state = "priv"
            self.send(f"\r\n{self.prompt}")
        elif len(parts) >= 5 and parts[0] == "menu" and parts[2] == "text":
            _, name, _, key, desc = parts
            with dev._lock:
                if name == dev.menu_name and key in dev.lines:
                    dev.lines[key]["text"] = desc
                    self.send(f"\r\n{dev.hostname}(config)#")
                    return
            self.send(f"\r\n% Invalid menu\r\n{dev.hostname}(config)#")
        else:
            self.send(f"\r\n% Invalid input detected at '^' marker.\r\n{dev.hostname}(config)#")


# =============================================================================
# Vertiv ACS (Avocent ACS8000) gia lap
# =============================================================================

class FakeVertivACS(FakeCiscoOOB):
    """Vertiv ACS gia lap - mo phong DUNG cac do tre nguoi van hanh mo ta tren
    thiet bi that:
      1. Sau 'connect <port_name>', mat 1 luc (delay_before_pw) MOI hien 'Password:'.
      2. Nhap pass xong, lai mat them 1 khoang (delay_after_pw) moi vao phien.
      3. Thiet bi dich can go Enter NHIEU LAN (enters_needed) moi hien prompt.

    Dang nhap ACS qua Telnet dung prompt 'Username:' cho khop connect_auto()
    (thiet bi that thuong dung SSH - tang van chuyen khong phai doi tuong
    test o day, logic Vertiv trong check_port_via_oob() moi la doi tuong).

    ports: {key: {"name": port_name, "target": hostname may dich|None,
                  "style": "cisco"|"freebsd", "password": str|None,
                  "delay_before_pw": s, "delay_after_pw": s, "enters_needed": n,
                  "reject_delay": s}}

    vpw_attempts: moi mat khau port nhan duoc (theo thu tu); vpw_raw: byte tho
    nhan duoc o trang thai 'Password:' (de kiem tra ky tu Enter CR/LF)."""

    def __init__(self, hostname="ACS-HCM-01", username="admin", password="secret", ports=None):
        super().__init__(hostname=hostname, username=username, password=password, enable_password="", lines={})
        self.ports = {k: dict(v) for k, v in (ports or {}).items()}
        self.enters_at_target = []   # so lan Enter thiet bi dich nhan duoc moi phien connect
        self.vpw_attempts = []
        self.vpw_raw = b""

    def _session_cls(self):
        return _VertivSession


class _VertivSession(_Session):
    ESCAPES = {"target": (b"\x1a", "_ctrl_z"), "vpw": (b"\x1a", "_ctrl_z")}
    HOTKEY = "\r\nType the hot key to suspend the connection: <CTRL>Z\r\n"

    def greeting(self):
        self.cwd = "/"
        self.port = None
        self.send("\r\nUsername: ")

    @property
    def prompt(self):
        return f"--:- {'access' if self.cwd == 'access' else '/'} cli-> "

    def _on_raw(self, data):
        if self.state == "vpw":
            self.dev.vpw_raw += data

    def _ctrl_z(self):
        self.state = "cli"
        self.port = None
        self.send(f"\r\n{self.prompt}")

    def _enter_target(self):
        import time as _t
        _t.sleep(self.port.get("delay_after_pw", 0))
        self.state = "target"
        self._enters = 0
        self.dev.enters_at_target.append(0)
        self.send(self.HOTKEY)

    def _on_line(self, line):
        import time as _t
        dev = self.dev
        st = self.state
        if st == "username":
            self._user = line
            self.state = "password"
            self.send(line + "\r\nPassword: ")
        elif st == "password":
            if self._user == dev.username and line == dev.password:
                self.state = "cli"
                self.send(f"\r\nWelcome to ACS8000 <{dev.hostname}>!\r\n{self.prompt}")
            else:
                self.state = "username"
                self.send("\r\nLogin incorrect\r\nUsername: ")
        elif st == "cli":
            cmd = line.strip()
            self.send(line)
            if cmd:
                with dev._lock:
                    dev.commands.append(cmd)
            if cmd in ("cd access/", "cd access"):
                self.cwd = "access"
                self.send(f"\r\n{self.prompt}")
            elif cmd == "cd /":
                self.cwd = "/"
                self.send(f"\r\n{self.prompt}")
            elif cmd == "show" and self.cwd == "access":
                rows = [f"{p['name']}   {k}  serial  CAS" for k, p in dev.ports.items()]
                self.send("\r\n===============================\r\n" + dev.hostname +
                          "\r\n===============================\r\nport    number  type    status\r\n" +
                          "\r\n".join(rows) + f"\r\n{self.prompt}")
            elif cmd.startswith("connect "):
                name = cmd.split(None, 1)[1]
                key = next((k for k, p in dev.ports.items() if p["name"] == name), None)
                if key is None:
                    self.send(f"\r\nError: Invalid target: {name}\r\n{self.prompt}")
                    return
                self.port = dev.ports[key]
                if self.port.get("password"):
                    _t.sleep(self.port.get("delay_before_pw", 0))
                    self.state = "vpw"
                    self.send("\r\nPassword: ")
                else:
                    self._enter_target()
            elif cmd in ("exit", "logout"):
                return "close"
            elif cmd == "":
                self.send(f"\r\n{self.prompt}")
            else:
                self.send(f"\r\nError: Invalid command: {cmd}\r\n{self.prompt}")
        elif st == "vpw":
            dev.vpw_attempts.append(line)
            if line == self.port["password"]:
                self._enter_target()
            else:
                # ACS that (PAM) tre ~7s roi moi hoi lai Password:
                _t.sleep(self.port.get("reject_delay", 0))
                self.send("\r\nPassword: ")
        elif st == "target":
            self._enters += 1
            dev.enters_at_target[-1] = self._enters
            if self.port["target"] and self._enters >= self.port.get("enters_needed", 1):
                t = self.port["target"]
                if self.port.get("style") == "freebsd":
                    self.send(f"\r\n\r\nFreeBSD/amd64 ({t}) (ttyu0)\r\n\r\nlogin: ")
                else:
                    self.send(f"\r\n{t}>")
