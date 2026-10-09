"""Push doi port_name tren Vertiv ACS8000 - mo phong CLI dung nhu thiet bi
that: 'set port_name' chi hop le trong /ports/serial_ports/<N>/cas/, va
thay doi chi duoc luu khi 'commit' (lenh 'save' khong ton tai)."""

import re

import oob_lib


class FakeACS:
    def __init__(self, names):
        self.names = dict(names)      # port -> ten da commit
        self.pending = {}
        self.path = "/"
        self.sent = []
        self._out = "--:- / cli-> "

    def _prompt(self):
        mark = "#" if self.pending else ""
        if self.path.endswith("/cas"):
            return f"--:{mark}- [serial_ports/cas] cli-> "
        if re.fullmatch(r"/ports/serial_ports/\d+", self.path):
            return f"--:{mark}- [serial_ports/physical] cli-> "
        return f"--:{mark}- {self.path.rstrip('/').split('/')[-1] or '/'} cli-> "

    def write(self, cmd):
        self.sent.append(cmd)
        out = ""
        if cmd.startswith("cd "):
            target = cmd[3:].strip().rstrip("/")
            new = target if target.startswith("/") else (self.path.rstrip("/") + "/" + target)
            new = new or "/"
            ok = (new in ("/", "/ports", "/ports/serial_ports")
                  or re.fullmatch(r"/ports/serial_ports/(\d+)(/cas)?", new)
                  and int(re.match(r"/ports/serial_ports/(\d+)", new).group(1)) in self.names)
            if ok: self.path = new
            else: out = f"\nError: Invalid path: {target}\n"
        elif cmd.startswith("set "):
            m = re.fullmatch(r'set port_name="(.*)"', cmd)
            if not self.path.endswith("/cas"):
                out = "\nError: Invalid parameter name: port_name\n" if m else "\nError: Invalid command: set\n"
            else:
                self.pending[int(self.path.split("/")[3])] = m.group(1)
        elif cmd == "commit":
            others = {n.lower() for p, n in self.names.items() if p not in self.pending}
            if any(v.lower() in others for v in self.pending.values()):
                out = "\nError: port_name already in use\n"
            else:
                self.names.update(self.pending); self.pending = {}
        elif cmd == "revert":
            self.pending = {}
        elif cmd == "show" and self.path.endswith("/cas"):
            port = int(self.path.split("/")[3])
            out = f"\nport: {port}\nport_name = {self.pending.get(port, self.names[port])}\nprotocol = ssh\n"
        elif cmd == "show" and self.path == "/ports/serial_ports":
            rows = [f"  {p:<4}  ttyS{p:<4}  {n:<40}  cas  9600 8N1 ssh local" for p, n in sorted(self.names.items())]
            # Phan trang nhu thiet bi that: 3 dong/trang
            pages = [rows[i:i + 3] for i in range(0, len(rows), 3)] or [[]]
            self._pages = pages[1:]
            self._out = cmd + "\n  port  device  name\n  ====\n" + "\n".join(pages[0]) + ("\n-- MORE --:" if self._pages else "\n" + self._prompt())
            return
        elif cmd in ("exit",):
            pass
        else:
            out = f"\nError: Invalid command: {cmd}\n"
        self._out = cmd + out + "\n" + self._prompt()

    def write_raw(self, data):
        page = self._pages.pop(0)
        self._out = "\n" + "\n".join(page) + ("\n-- MORE --:" if self._pages else "\n" + self._prompt())

    def read_until(self, pattern, timeout=5):
        out, self._out = self._out, ""
        return out

    def close(self):
        pass


def _run(monkeypatch, acs, updates):
    monkeypatch.setattr(oob_lib, "connect_auto", lambda *a, **k: acs)
    logs = []
    ok = oob_lib.push_vertiv_port_names("1.1.1.1", 22, 23, "admin", "pw", updates,
                                        print_fn=logs.append, dry_run=False)
    return ok, logs


def test_push_vertiv_commits_port_name(monkeypatch):
    acs = FakeACS({15: "HCM-FLI-LEAF-FNX02L03-01", 16: "OLD-16"})
    ok, _ = _run(monkeypatch, acs, [("access", "15", "HCM-FPLSys-AggSW-FNX02L03H1I7-QFX512-01")])
    assert ok is True
    assert acs.names[15] == "HCM-FPLSys-AggSW-FNX02L03H1I7-QFX512-01"
    assert acs.names[16] == "OLD-16"
    assert "commit" in acs.sent and "save" not in acs.sent


def test_push_vertiv_unknown_port_fails_without_touching_others(monkeypatch):
    acs = FakeACS({15: "A"})
    ok, logs = _run(monkeypatch, acs, [("access", "99", "NEW")])
    assert ok is False
    assert acs.names == {15: "A"}
    assert not any(c.startswith("set ") for c in acs.sent)


def test_push_vertiv_multiple_ports(monkeypatch):
    acs = FakeACS({1: "a", 2: "b", 3: "c"})
    ok, _ = _run(monkeypatch, acs, [("access", "1", "X1"), ("access", "3", "X3")])
    assert ok is True
    assert acs.names == {1: "X1", 2: "b", 3: "X3"}


def test_push_vertiv_dry_run_sends_nothing(monkeypatch):
    def boom(*a, **k): raise AssertionError("khong duoc ket noi khi dry_run")
    monkeypatch.setattr(oob_lib, "connect_auto", boom)
    logs = []
    assert oob_lib.push_vertiv_port_names("1.1.1.1", 22, 23, "u", "p", [("access", "15", "N")],
                                          print_fn=logs.append, dry_run=True) is True
    assert any("cd 15/" in l for l in logs) and any("commit" in l for l in logs)


def test_push_vertiv_numbers_duplicate_name(monkeypatch):
    # Port 31/32 (re0/re1) cung tra ve hostname "HCM-SMC-04" -> ten thu 2 phai danh so
    acs = FakeACS({30: "HCM-SMC-04", 31: "old-31", 32: "old-32", 33: "a", 34: "b"})
    applied = {}
    monkeypatch.setattr(oob_lib, "connect_auto", lambda *a, **k: acs)
    ok = oob_lib.push_vertiv_port_names("1.1.1.1", 22, 23, "u", "p",
                                        [("access", "31", "HCM-SMC-04"), ("access", "32", "HCM-SMC-04")],
                                        dry_run=False, applied=applied)
    assert ok is True
    assert acs.names[30] == "HCM-SMC-04"
    assert acs.names[31] == "HCM-SMC-04_2"
    assert acs.names[32] == "HCM-SMC-04_3"
    assert applied == {"31": "HCM-SMC-04_2", "32": "HCM-SMC-04_3"}


def test_unique_port_name_and_strip():
    assert oob_lib.unique_port_name("X", ["A"]) == "X"
    assert oob_lib.unique_port_name("X", ["x", "X_2"]) == "X_3"
    assert oob_lib.strip_dup_suffix("HCM-SMC-04_2") == "HCM-SMC-04"
    assert oob_lib.strip_dup_suffix("HCM-CGNAT-13_RE0") == "HCM-CGNAT-13_RE0"
