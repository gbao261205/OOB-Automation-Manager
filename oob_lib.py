"""
oob_lib.py

Thu vien dung chung: ket noi SSH (uu tien) hoac Telnet (du phong) toi thiet bi
Cisco IOS, dang nhap, lay va parse cau hinh "menu OOB_MENU" de doi chieu (verify)
voi baseline da luu. CHI DOC (read-only) - khong con chuc nang day/sua cau hinh
nguoc lai thiet bi.

Tat ca ket noi deu dung connect_auto():
    - Thu SSH truoc (paramiko) -> neu that bai -> fallback Telnet (MiniTelnet).
    - MiniSSH va MiniTelnet co cung interface (read_until / write / close) nen
      cac ham ben tren (fetch_hostname, detect_and_fetch_menu, ...)
      khong can biet dang dung protocol nao.

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

TEXT_RE       = re.compile(r'menu\s+(\S+)\s+text\s+(\S+)\s+(.+)',                          re.IGNORECASE)
CMD_TELNET_RE = re.compile(r'menu\s+(\S+)\s+command\s+(\S+)\s+telnet\s+(\S+)(?:\s+(\d+))?',  re.IGNORECASE)
# SSH: bao gom 'ssh -l user IP', 'ssh user@IP', 'ssh IP'
CMD_SSH_RE    = re.compile(
    r'menu\s+(\S+)\s+command\s+(\S+)\s+ssh\s+'
    r'(?:-l\s+\S+\s+|\S+@)?'                            # -l username  hoac  user@
    r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'           # IP
    r'(?:\s+(\d+))?',                                   # port tuy chon
    re.IGNORECASE
)


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
        self._shell.send((text + "\r\n").encode())

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


MENU_NAME_RE = re.compile(r'^\s*menu\s+(\S+)\s+(?:text|command)\b', re.IGNORECASE | re.MULTILINE)


def detect_and_fetch_menu(tn):
    """Tat phan trang, chay 'show running-config | include menu' MOT LAN DUY NHAT:
    vua lay toan bo cac dong cau hinh 'menu ...' tren thiet bi, vua TU DONG DO ten
    menu (menu_name) tu dong dau tien khop dang 'menu <ten> text|command ...'.

    Khong con can biet truoc menu_name de build lenh 'section menu <ten>' nhu truoc.

    Tra ve (menu_name, raw_output):
        menu_name  -> str neu tim thay, None neu thiet bi khong co cau hinh menu nao.
        raw_output -> toan bo output tho (dung lai duoc cho parse_menu(), khong can
                       goi show lan thu 2).
    """
    tn.write("terminal length 0")
    tn.read_until("#", timeout=5)
    tn.write("show running-config | include menu")
    output = tn.read_until("#", timeout=15)
    m = MENU_NAME_RE.search(output)
    menu_name = m.group(1) if m else None
    return menu_name, output


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


# ---------------------------------------------------------------------------
# Parse menu
# ---------------------------------------------------------------------------

def parse_menu(output: str, menu_name: str) -> dict:
    """{option_key: {"description":..., "ip":..., "port":..., "protocol":...}}
    Bo qua option khong co IP dich (vi du 'q'/menu-exit/resume).
    Ho tro ca lenh telnet lan ssh trong menu."""
    options = {}
    
    # 1. Hàm làm sạch cơ bản (giữ nguyên để tránh lỗi khoảng trắng)
    def clean_key(raw_key: str) -> str:
        return raw_key.strip()

    # 2. HÀM MỚI: Hàm Chuẩn hóa để GOM NHÓM (Bóc ngoặc vuông nếu có)
    # Ví dụ: "[1]" -> "1", "1" -> "1", "[KTHT]" -> "KTHT"
    def normalize_key(raw_key: str) -> str:
        k = raw_key.strip()
        if k.startswith("[") and k.endswith("]"):
            return k[1:-1]
        return k
        
    for raw_line in output.splitlines():
        line = raw_line.strip()

        # Quét dòng TEXT (Hiển thị)
        m = TEXT_RE.match(line)
        if m and m.group(1) == menu_name:
            original_key = clean_key(m.group(2))
            norm_key = normalize_key(original_key)
            
            # Khởi tạo dict nếu chưa có, lưu lại CẢ original_key để dùng lúc Push
            if norm_key not in options:
                options[norm_key] = {"original_key": original_key}
                
            options[norm_key]["description"] = m.group(3).strip()
            # Cập nhật original_key nếu dòng text có ngoặc vuông (ưu tiên lưu key hiển thị)
            if original_key.startswith("["):
                options[norm_key]["original_key"] = original_key
            continue

        # Quét dòng COMMAND TELNET
        m = CMD_TELNET_RE.match(line)
        if m and m.group(1) == menu_name:
            norm_key = normalize_key(clean_key(m.group(2)))
            
            if norm_key not in options:
                options[norm_key] = {"original_key": clean_key(m.group(2))}
                
            entry = options[norm_key]
            entry["ip"]       = m.group(3)
            entry["port"]     = int(m.group(4)) if m.group(4) else 23
            entry["protocol"] = "telnet"
            continue

        # Quét dòng COMMAND SSH
        m = CMD_SSH_RE.match(line)
        if m and m.group(1) == menu_name:
            norm_key = normalize_key(clean_key(m.group(2)))
            
            if norm_key not in options:
                options[norm_key] = {"original_key": clean_key(m.group(2))}
                
            entry = options[norm_key]
            entry["ip"]       = m.group(3)
            entry["port"]     = int(m.group(4)) if m.group(4) else 22
            entry["protocol"] = "ssh"

    # Trả về dict, LỌC BỎ CÁC OPTION KHÔNG CÓ IP (như exit, resume)
    # SỬ DỤNG original_key LÀM KEY CHÍNH CỦA DICTIONARY ĐỂ TOOL HIỂN THỊ ĐÚNG NGOẶC VUÔNG
    final_options = {}
    for norm_k, v in options.items():
        if "ip" in v:
            final_key = v.get("original_key", norm_k)
            # Dọn dẹp original_key ra khỏi value dict trước khi trả về (cho sạch data)
            if "original_key" in v:
                del v["original_key"]
            final_options[final_key] = v
            
    return final_options


# ---------------------------------------------------------------------------
# Poll va Push
# ---------------------------------------------------------------------------

def poll_host(host, telnet_port, username, password, enable_password,
              menu_name=None, ssh_port=22, timeout=10, debug=False):
    """Ket noi (SSH-first, fallback Telnet), lay hostname va parse menu cua 1 thiet bi OOB.

    menu_name:
        - None (mac dinh) -> TU DONG DO ten menu tren thiet bi bang
          'show running-config | include menu' (xem detect_and_fetch_menu()).
        - Truyen mot chuoi cu the -> ep dung ten do de loc, bo qua ten tu dong do
          duoc (huu ich neu thiet bi co nhieu menu va chi muon lay 1 menu cu the).

    Tra ve (hostname, menu_name_da_dung, options). menu_name_da_dung co the None
    neu khong tim thay cau hinh menu nao tren thiet bi.
    Luon dong ket noi khi xong. debug=True: in raw output truoc khi parse de chan doan loi.
    """
    tn = connect_auto(host, ssh_port, telnet_port,
                      username, password, enable_password, timeout=timeout)
    try:
        hostname = fetch_hostname(tn)
        detected_name, raw = detect_and_fetch_menu(tn)
        effective_name = menu_name or detected_name

        if debug:
            print(f"\n    ===== [DEBUG] RAW OUTPUT TU SSH/TELNET ({len(raw)} chars) =====")
            # In repr() de thay ro escape codes, \r, \n, ky tu an
            for i, chunk in enumerate([raw[j:j+120] for j in range(0, min(len(raw), 1200), 120)]):
                print(f"    {repr(chunk)}")
            if len(raw) > 1200:
                print(f"    ... (con {len(raw)-1200} chars nua, bi cat bot)")
            print(f"    ===== [DEBUG] KET THUC RAW OUTPUT =====\n")
            print(f"    [DEBUG] menu_name tu dong do duoc: {detected_name!r} (dang dung: {effective_name!r})")

        options = parse_menu(raw, effective_name) if effective_name else {}

        if debug:
            print(f"    [DEBUG] parse_menu -> {len(options)} option(s): {list(options.keys())}")

        return hostname, effective_name, options
    finally:
        try:
            tn.write("exit")
        except OSError:
            pass
        tn.close()



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

def push_menu_descriptions(host, ssh_port, telnet_port, username, password, enable_password, updates_list, timeout=10):
    """
    Kết nối, ghi đè cấu hình và BẮT LỖI TỪ CISCO IOS.
    updates_list: danh sách các tuple (real_menu_name, real_key, new_desc)
    """
    if not updates_list: 
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