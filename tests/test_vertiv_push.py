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
            self.names.update(self.pending); self.pending = {}
        elif cmd == "revert":
            self.pending = {}
        elif cmd == "show" and self.path.endswith("/cas"):
            port = int(self.path.split("/")[3])
            out = f"\nport: {port}\nport_name = {self.pending.get(port, self.names[port])}\nprotocol = ssh\n"
        elif cmd in ("exit",):
            pass
        else:
            out = f"\nError: Invalid command: {cmd}\n"
        self._out = cmd + out + "\n" + self._prompt()

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
