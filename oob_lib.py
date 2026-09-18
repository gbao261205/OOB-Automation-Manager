"""
oob_lib.py

Thu vien dung chung: ket noi SSH (uu tien) hoac Telnet (du phong) toi thiet bi
Cisco IOS/Vertiv ACS, dang nhap va lay hostname (fetch_hostname). Cung cung cap
push_menu_descriptions()/push_vertiv_port_names() de sua mo ta port khi Deep
Verify phat hien sai lech (xem push_live_mode trong oob_monitor.py de biet co
che dry-run/live). Viec DOC + PARSE cau hinh "menu ..." de doi chieu baseline
nam trong oob_monitor.py (poll_host_multi(), _parse_cisco_menu_config(),
_parse_vertiv_acs_show()) - khong con o file nay (da gop, xoa ban parse_menu()/
poll_host() trung lap o day, xem WP-E(b)).

Tat ca ket noi deu dung connect_auto():
    - Thu SSH truoc (paramiko) -> neu that bai -> fallback Telnet (MiniTelnet).
    - MiniSSH va MiniTelnet co cung interface (read_until / write / close) nen
      cac ham ben tren (fetch_hostname, ...) khong can biet dang dung protocol nao.

Phu thuoc ben ngoai:
    pip install paramiko
"""

import platform
import re
import socket
import subprocess
import time

try:
    import logging
    import paramiko
    # Tat log noi bo cua paramiko (tranh in traceback/exception ra console)
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)
    _PARAMIKO_OK = True
except ImportError:
    _PARAMIKO_OK = False

IAC  = 255
DONT = 254
DO   = 253
WONT = 252
WILL = 251
SB   = 250
SE   = 240

# Dung de doc CAC OUTPUT DAI (vd "show running-config | include menu") mot
# cach an toan: thay vi dung read_until("#") - se ket thuc SAI ngay khi gap
# BAT KY ky tu "#" nao xuat hien trong noi dung cau hinh (vd 1 description
# dat la "##"), pattern nay CHI khop khi "#"/">" la ky tu CUOI CUNG cua buffer
# hien tai VA duoc dung ngay sau 1 chuoi giong ten thiet bi (khong phai dung
# sau 1 ky tu "#" khac hay "----> ") - tuc la dung PROMPT THAT cua thiet bi,
# khong phai 1 doan text nam giua noi dung dang doc.
PROMPT_TAIL_RE = re.compile(r'(?:^|[\r\n])[\w\-\.\(\)]{1,64}[>#]\s*$')


# ---------------------------------------------------------------------------
# MiniTelnet — Telnet client toi gian (du phong khi SSH khong duoc)
# ---------------------------------------------------------------------------

class MiniTelnet:
    """Telnet client toi gian: connect / read_until / write."""

    def __init__(self, host, port=23, timeout=10):
        self.sock   = socket.create_connection((host, port), timeout=timeout)
        self.buffer = b""
        # True neu lan doc read_until_prompt() gan nhat bi TIMEOUT (khong thay
        # prompt that su) thay vi doc du va thanh cong - dung de phan biet
        # "khong lay duoc thong tin" (fetch that bai) voi "lay duoc nhung rong".
        self.last_read_timed_out = False

    def _strip_iac(self, data: bytes) -> bytes:
        out = bytearray()
        i, n = 0, len(data)
        while i < n:
            b = data[i]
            if b == IAC:
                if i + 1 >= n:
                    break
                cmd = data[i + 1]
                if cmd in (DO, DONT, WILL, WONT):
                    if i + 2 < n:
                        opt   = data[i + 2]
                        reply = WONT if cmd == DO else DONT
                        try:
                            self.sock.sendall(bytes([IAC, reply, opt]))
                        except OSError:
                            pass
                        i += 3
                    else:
                        i += 2
                    continue
                elif cmd == SB:
                    j = i + 2
                    while j < n - 1 and not (data[j] == IAC and data[j + 1] == SE):
                        j += 1
                    i = j + 2
                    continue
                else:
                    i += 2
                    continue
            else:
                out.append(b)
                i += 1
        return bytes(out)

    def read_until(self, patterns, timeout=10):
        if isinstance(patterns, (str, bytes)):
            patterns = [patterns]
        patterns = [p.encode() if isinstance(p, str) else p for p in patterns]
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(0.3, deadline - time.time()))
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.buffer += self._strip_iac(chunk)
            for p in patterns:
                idx = self.buffer.find(p)
                if idx != -1:
                    matched      = self.buffer[: idx + len(p)]
                    self.buffer  = self.buffer[idx + len(p):]
                    return matched.decode(errors="ignore")
        data, self.buffer = self.buffer, b""
        return data.decode(errors="ignore")

    def read_until_prompt(self, timeout=10):
        """Doc cho toi khi gap PROMPT THAT cua thiet bi (xem PROMPT_TAIL_RE) -
        an toan cho cac lenh output dai ("show running-config | include menu")
        co the chua ky tu '#' ngay trong noi dung (vd description "##"), khac
        voi read_until("#") se ket thuc SAI ngay khi gap '#' dau tien bat ke
        no nam o dau.

        Sau khi goi ham nay, kiem tra self.last_read_timed_out:
            True  -> KHONG tim thay prompt that su truoc khi het timeout (fetch
                     that bai / mat ket noi giua chung).
            False -> Da doc du toi prompt, du lieu tra ve day du va dang tin cay."""
        self.last_read_timed_out = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(0.3, deadline - time.time()))
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.buffer += self._strip_iac(chunk)
            text = self.buffer.decode(errors="ignore")
            if PROMPT_TAIL_RE.search(text):
                self.buffer = b""
                return text
        data, self.buffer = self.buffer, b""
        self.last_read_timed_out = True
        return data.decode(errors="ignore")

    def write(self, text: str):
        self.sock.sendall((text + "\r\n").encode())

    def write_cr(self, text: str):
        """Giong write() nhung CHI gui '\\r' (khong co '\\n' theo sau).

        Dung rieng cho o nhap MAT KHAU tren mot so serial console/ACS: gui
        'text\\r\\n' (ca hai) co the bi thiet bi hieu them ky tu '\\n' nhu MOT
        LAN NHAN ENTER RONG NUA ngay sau khi mat khau (dung) vua duoc xac nhan
        - dan den bi tinh la "nhap mat khau rong" va bi tu choi, khien thiet
        bi hoi lai Password: mac du mat khau ban dau la CHINH XAC. Go tay chi
        gui 1 Enter (\\r) nen khong gap loi nay."""
        self.sock.sendall((text + "\r").encode())

    def write_no_drain(self, text: str):
        """Giong write(), nhung ten ham nay khang dinh RO RANG la KHONG duoc
        xoa buffer dang cho truoc khi gui (xem MiniSSH.write_no_drain() de biet
        ly do). Voi MiniTelnet thi write() von da khong drain nen day chi la alias,
        nhung dung ham nay o noi can "go phim danh thuc ma khong mat du lieu cu"
        de code chay dung ca tren duong SSH lan Telnet."""
        self.write(text)

    def write_raw(self, data: bytes):
        self.sock.sendall(data)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# MiniSSH — SSH client voi cung interface voi MiniTelnet
# ---------------------------------------------------------------------------

class MiniSSH:
    """SSH client (paramiko invoke_shell) voi cung interface voi MiniTelnet.
    Khong goi truc tiep — dung thong qua connect_auto()."""

    def __init__(self):
        if not _PARAMIKO_OK:
            raise RuntimeError("paramiko chua duoc cai dat. Chay: pip install paramiko")
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._shell  = None
        self.buffer  = b""
        # Xem giai thich o MiniTelnet.read_until_prompt().
        self.last_read_timed_out = False

    def _connect(self, host, port, username, password, timeout):
        """Goi boi connect_auto() de thiet lap ket noi thuc su."""
        self._client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        # term='dumb' de IOS khong gui ANSI escape codes (mau, con tro, ...)
        # width lon de tranh xuong dong gia (line-wrap) lam hong cac dong dai
        self._shell = self._client.invoke_shell(term='dumb', width=250, height=0)
        self._shell.settimeout(timeout)

    # Pattern loai bo ANSI escape codes (ESC[...m, ESC[...H, ESC c, ...)
    _ANSI_RE = re.compile(
        rb'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|[\r]'
    )

    @classmethod
    def _strip_ansi(cls, data: bytes) -> bytes:
        """Xoa ANSI escape codes va \r khoi raw bytes nhan tu SSH shell."""
        return cls._ANSI_RE.sub(b'', data)

    def read_until(self, patterns, timeout=10):
        if isinstance(patterns, (str, bytes)):
            patterns = [patterns]
        patterns = [p.encode() if isinstance(p, str) else p for p in patterns]
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.3, deadline - time.time())
            self._shell.settimeout(remaining)
            try:
                chunk = self._shell.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.buffer += self._strip_ansi(chunk)
            for p in patterns:
                idx = self.buffer.find(p)
                if idx != -1:
                    matched     = self.buffer[: idx + len(p)]
                    self.buffer = self.buffer[idx + len(p):]
                    return matched.decode(errors="ignore")
        data, self.buffer = self.buffer, b""
        return data.decode(errors="ignore")

    def read_until_prompt(self, timeout=10):
        """Doc cho toi khi gap PROMPT THAT cua thiet bi (xem PROMPT_TAIL_RE) -
        an toan cho cac lenh output dai ("show running-config | include menu")
        co the chua ky tu '#' ngay trong noi dung (vd description "##"), khac
        voi read_until("#") se ket thuc SAI ngay khi gap '#' dau tien bat ke
        no nam o dau. Tuong duong ban Telnet, dung cho ca duong SSH (uu tien).

        Sau khi goi ham nay, kiem tra self.last_read_timed_out (xem giai thich
        chi tiet o MiniTelnet.read_until_prompt())."""
        self.last_read_timed_out = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.3, deadline - time.time())
            self._shell.settimeout(remaining)
            try:
                chunk = self._shell.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.buffer += self._strip_ansi(chunk)
            text = self.buffer.decode(errors="ignore")
            if PROMPT_TAIL_RE.search(text):
                self.buffer = b""
                return text
        data, self.buffer = self.buffer, b""
        self.last_read_timed_out = True
        return data.decode(errors="ignore")

    def _drain_pending(self):
        """Xoa buffer noi bo va doc bo du lieu ton dong tren kenh SSH.
        Goi truoc moi lenh moi de tranh du lieu cu (extra prompts, echo thua)
        gay nhieu loan cho read_until tiep theo."""
        self.buffer = b""
        self._shell.settimeout(0.15)
        try:
            while True:
                pending = self._shell.recv(4096)
                if not pending:
                    break
        except (socket.timeout, OSError):
            pass

    def write(self, text: str):
        # Drain buffer truoc khi gui lenh moi — tranh stale data lam hong read_until ke tiep
        self._drain_pending()
        self._shell.send((text + "\n").encode())

    def write_cr(self, text: str):
        """Giong write() nhung CHI gui '\\n' (khong co '\\r'). Xem giai thich
        chi tiet o MiniTelnet.write_cr() - dung cho o nhap MAT KHAU de tranh
        ky tu thua bi hieu nham thanh 1 lan Enter rong ke tiep."""
        self._drain_pending()
        self._shell.send((text + "\n").encode())

    def write_no_drain(self, text: str):
        """Giong write() nhung KHONG goi _drain_pending() truoc khi gui.

        Dung khi go mot phim "danh thuc" (vd Enter rong) ma TRUOC DO thiet bi
        co the da gui san du lieu quan trong nhung minh chua kip doc het (vd
        banner "FreeBSD/amd64 (HOSTNAME) (ttyu0)\\nlogin:" cua thiet bi pivot
        qua Vertiv). write() thuong se drain (xoa sach buffer + rut can du
        lieu dang cho tren socket trong 0.15s) truoc khi gui — dieu nay AN
        TOAN cho luong lenh binh thuong (prompt cu da doc xong), nhung se LAM
        MAT du lieu neu con noi dung chua doc dang cho trong buffer/socket.
        """
        self._shell.send((text + "\n").encode())

    def write_raw(self, data: bytes):
        self._drain_pending()
        self._shell.send(data)

    def close(self):
        try:
            self._client.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Ping test - kiem tra thiet bi co "song" (reachable qua ICMP) truoc khi
# thu dang nhap SSH/Telnet (tiet kiem thoi gian neu thiet bi da down).
# ---------------------------------------------------------------------------

def ping_host(ip: str, timeout: float = 1.0) -> bool:
    """Gui 1 goi ICMP ping toi ip, tra ve True neu co phan hoi (reachable).
    Dung lenh ping cua he dieu hanh (khong can quyen root/raw-socket):
        - Windows : ping -n 1 -w <ms>
        - Linux/Mac: ping -c 1 -W <s>
    Tra ve False neu khong phan hoi, loi, hoac khong tim thay lenh ping."""
    system = platform.system().lower()
    try:
        if system == "windows":
            cmd = ["ping", "-n", "1", "-w", str(max(1, int(timeout * 1000)))]
        else:
            cmd = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout))))]
        cmd.append(ip)
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 3,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Ket noi thong nhat: SSH truoc, fallback Telnet
# ---------------------------------------------------------------------------

def connect_auto(host, ssh_port, telnet_port,
                 username, password, enable_password, timeout=10):
    """Ket noi vao thiet bi: thu SSH truoc (ssh_port), fallback sang Telnet (telnet_port).
    Tra ve session (MiniSSH hoac MiniTelnet) da dang nhap va o enable mode ('#').
    Nem ngoai le neu ca hai deu that bai."""

    # --- Thu SSH ---
    if _PARAMIKO_OK:
        try:
            session = MiniSSH()
            session._connect(host, ssh_port, username, password, timeout)
            # paramiko xu ly xac thuc username/password; doc prompt ban dau
            banner = session.read_until([">", "#"], timeout=8)
            if banner.rstrip().endswith(">"):
                session.write("enable")
                resp = session.read_until(["assword:", "#"], timeout=8)
                if "assword:" in resp:
                    session.write(enable_password or "")
                    session.read_until("#", timeout=8)
            return session
        except Exception as ssh_err:
            err_str = str(ssh_err)
            # Phan loai loi SSH de goi y nguyen nhan cu the
            if "Incompatible version" in err_str or "1.5" in err_str:
                print(f"    [~] SSH that bai: thiet bi {host} dang chay SSHv1 (version 1.5).")
                print(f"         Giai phap tren thiet bi:")
                print(f"           crypto key zeroize rsa")
                print(f"           crypto key generate rsa modulus 2048")
                print(f"           ip ssh version 2")
            elif "Authentication" in err_str or "auth" in err_str.lower():
                print(f"    [~] SSH that bai: sai username/password ({err_str}).")
                print(f"         Kiem tra lai muc 1 (Username) va 2 (Password) trong cai dat.")
            elif "timed out" in err_str or "timeout" in err_str.lower():
                print(f"    [~] SSH that bai: timeout khi ket noi {host}:{ssh_port}.")
                print(f"         Kiem tra 'transport input ssh' va SSH co bat tren thiet bi.")
            elif "Connection refused" in err_str:
                print(f"    [~] SSH that bai: port {ssh_port} bi tu choi tren {host}.")
                print(f"         Kiem tra 'line vty 0 4 / transport input ssh'.")
            else:
                print(f"    [~] SSH that bai: {err_str}")
            print(f"         -> Thu Telnet du phong port {telnet_port} ...")
    else:
        print("    [~] paramiko khong co san, dung Telnet ...")

    # --- Fallback: Telnet ---
    session = MiniTelnet(host, telnet_port, timeout)
    banner  = session.read_until(["sername:", "assword:", ">", "#"], timeout=8)

    if "sername:" in banner:
        session.write(username or "")
        banner = session.read_until(["assword:", ">", "#"], timeout=8)

    if "assword:" in banner:
        session.write(password)
        banner = session.read_until([">", "#"], timeout=8)

    if banner.rstrip().endswith(">"):
        session.write("enable")
        resp = session.read_until(["assword:", "#"], timeout=8)
        if "assword:" in resp:
            session.write(enable_password or "")
            session.read_until("#", timeout=8)

    return session


def connect_and_login(host, port, username, password, enable_password, timeout=10):
    """Deprecated: wrapper tuong thich nguoc. Dung connect_auto() thay the."""
    return connect_auto(
        host,
        ssh_port=22,
        telnet_port=port,
        username=username,
        password=password,
        enable_password=enable_password,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Lay thong tin tu thiet bi
# ---------------------------------------------------------------------------

def fetch_hostname(tn):
    """Lay hostname da cau hinh tren thiet bi (tu 'hostname <ten>' trong running-config).

    Dung ^ (dau dau dong, voi re.MULTILINE) de chi khop dong cau hinh thuc su
    ("hostname R0-CORE"), tranh nham voi chinh dong lenh duoc echo lai
    ("show running-config | include ^hostname") cung chua chu "hostname".
    """
    tn.write("show running-config | include ^hostname")
    output = tn.read_until("#", timeout=8)
    m = re.search(r'^hostname\s+(\S+)', output, re.IGNORECASE | re.MULTILINE)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Kiem tra hostname khop description (word-boundary)
# ---------------------------------------------------------------------------

def _hostname_matches_desc(hostname: str, description: str) -> bool:
    """Kiem tra hostname co xuat hien nhu mot TU DOC LAP trong description khong.
    Tach description theo khoang trang (khong tach theo '-') de tranh false positive:
        "Ket noi R1"   , hostname="R1"   -> {'Ket','noi','R1'}   -> True
        "Ket noi R123" , hostname="R1"   -> {'Ket','noi','R123'} -> False (chinh xac!)
        "Ket noi R123" , hostname="R123" -> {'Ket','noi','R123'} -> True
        "Ket noi R1-SW", hostname="R1"   -> {'Ket','noi','R1-SW'}-> False
    """
    tokens = {t.upper() for t in description.split() if t}
    return hostname.upper() in tokens


# Alias cong khai: oob_monitor.py va cac module khac nen dung ham nay (thay vi so
# sanh chuoi con "in") de tranh nham lan kieu "CTO-SW-02-2" khop nham "CTO-SW-02-20".
hostname_matches_description = _hostname_matches_desc


def fetch_hostname_via_auto(host, ssh_port, telnet_port,
                            username, password, enable_password, timeout=10):
    """Ket noi vao thiet bi dich (option target) va chi lay hostname.
    Dung connect_auto() nen tu dong thu SSH truoc, fallback Telnet neu SSH that bai.
    Tra ve hostname (str) hoac None neu khong lay duoc."""
    tn = connect_auto(host, ssh_port, telnet_port,
                      username, password, enable_password, timeout=timeout)
    try:
        return fetch_hostname(tn)
    finally:
        try:
            tn.write("exit")
        except OSError:
            pass
        tn.close()

def push_vertiv_port_names(host, ssh_port, telnet_port, username, password, updates_list, timeout=10, print_fn=None, dry_run=True):
    """
    Dành riêng cho Vertiv ACS8000: Đăng nhập tài khoản Administrator qua SSH,
    chuyển tới từng port và đổi port_name.
    (Mặc định dry_run=True: Chỉ IN RA màn hình câu lệnh dự định gửi chứ KHÔNG gửi thật)
    """
    if not updates_list:
        return True

    if print_fn:
        print_fn(f"[yellow][DRY-RUN / MOCK PUSH][/] Gia lap ket noi SSH Admin (User: {username}) toi Vertiv {host}...")
        for _m_name, k, new_desc in updates_list:
            print_fn(f"  --:- / cli-> cd /")
            print_fn(f"  --:- / cli-> cd ports/serial_ports/")
            print_fn(f"  --:- serial_ports cli-> cd {k}")
            print_fn(f"  --:#- [serial_ports/physical] cli-> cd cas/")
            print_fn(f'  --:#- [serial_ports/cas] cli-> set port_name="{new_desc}"')
            print_fn(f"  --:#- [serial_ports/cas] cli-> save")

    if dry_run:
        return True

    tn = connect_auto(host, ssh_port, telnet_port, username, password, "", timeout=timeout)
    try:
        tn.read_until("cli->", timeout=5)
        all_success = True
        for _m_name, k, new_desc in updates_list:
            tn.write("cd /")
            tn.read_until("cli->", timeout=5)
            tn.write("cd ports/serial_ports/")
            tn.read_until("cli->", timeout=5)
            tn.write(f"cd {k}")
            out1 = tn.read_until("cli->", timeout=5)
            if "not found" in out1.lower() or "error" in out1.lower() or "invalid" in out1.lower():
                all_success = False
                continue
            tn.write("cd cas/")
            out2 = tn.read_until("cli->", timeout=5)
            if "error" in out2.lower() or "invalid" in out2.lower():
                all_success = False
                continue
            tn.write(f'set port_name="{new_desc}"')
            out3 = tn.read_until("cli->", timeout=5)
            if "error" in out3.lower() or "invalid" in out3.lower():
                all_success = False
            tn.write("save")
            out4 = tn.read_until("cli->", timeout=5)
            if "error" in out4.lower() or "failed" in out4.lower():
                all_success = False
        return all_success
    except Exception:
        return False
    finally:
        try:
            tn.write("exit")
        except OSError:
            pass
        tn.close()

def push_menu_descriptions(host, ssh_port, telnet_port, username, password, enable_password, updates_list, timeout=10, vendor="cisco", cfg=None, print_fn=None, dry_run=True):
    """
    Kết nối, ghi đè cấu hình cho Cisco IOS hoặc Vertiv ACS8000.
    (Mặc định dry_run=True: Chỉ IN RA màn hình câu lệnh dự định gửi chứ KHÔNG gửi thật)
    """
    if not updates_list: 
        return True

    if str(vendor).lower() == "vertiv":
        admin_user = (cfg or {}).get("vertiv_admin_username")
        admin_pass = (cfg or {}).get("vertiv_admin_password")
        if not admin_user or not admin_pass:
            if print_fn:
                print_fn(f"[red][LOI][/] Chua cau hinh Vertiv Admin Username/Password trong Cau hinh he thong!")
            return False
        return push_vertiv_port_names(host, ssh_port, telnet_port, admin_user, admin_pass, updates_list, timeout=timeout, print_fn=print_fn, dry_run=dry_run)

    if print_fn:
        print_fn(f"[yellow][DRY-RUN / MOCK PUSH][/] Gia lap ket noi ({username}) toi Cisco {host}...")
        print_fn("  Cisco# configure terminal")
        for m_name, k, new_desc in updates_list:
            print_fn(f"  Cisco(config)# menu {m_name} text {k} {new_desc}")
        print_fn("  Cisco(config)# end")

    if dry_run:
        return True

    tn = connect_auto(host, ssh_port, telnet_port, username, password, enable_password, timeout=timeout)
    try:
        tn.write("configure terminal")
        tn.read_until("(config)#", timeout=5)
        
        all_success = True
        for m_name, k, new_desc in updates_list:
            tn.write(f"menu {m_name} text {k} {new_desc}")
            out = tn.read_until("(config)#", timeout=5)
            # Kiểm tra xem thiết bị Cisco có từ chối lệnh không
            if "%" in out:
                all_success = False
                
        tn.write("end")
        tn.read_until("#", timeout=5)
        return all_success
    except Exception:
        return False
    finally:
        try:
            tn.write("exit")
        except OSError:
            pass
        tn.close()