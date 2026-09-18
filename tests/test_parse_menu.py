"""Test cho parser Cisco menu / Vertiv ACS duy nhat con lai sau WP-E(b):
oob_monitor.py::_parse_cisco_menu_config() / _parse_vertiv_acs_show(), dung
boi poll_host_multi() (duong san xuat that su).

WP-E(b) da xoa oob_lib.py::parse_menu()/poll_host()/detect_and_fetch_menu()
(dead code - khong bao gio duoc goi tu oob_monitor.py/oob_web.py, xac nhan
bang grep toan repo). Cac test cu characterization cho ban oob_lib da bi xoa
theo, chi giu lai test cho ban con dang chay san xuat.

LUU Y VE FIXTURE: chua co du lieu output THAT tu thiet bi (khong co quyen
truy cap thiet bi/log that trong phien lam viec nay). Cac dong config duoi
day la SYNTHETIC nhung dung DUNG cu phap ma TEXT_RE/CMD_TELNET_RE/CMD_SSH_RE
(oob_monitor.py) dinh nghia (cu phap lenh "menu ... text/command" chuan cua
async terminal server) - khong phai doan van ban tuy tien. Nen thay bang
sample that (an danh hoa) khi co."""

from oob_monitor import _parse_cisco_menu_config, _parse_vertiv_acs_show


CISCO_MENU_CONFIG = """
menu MAINMENU text 1 Router-HCM-CORE-01
menu MAINMENU command 1 telnet 10.0.0.1 2001
menu MAINMENU text 2 Switch-HCM-ACCESS-02
menu MAINMENU command 2 ssh -l admin 10.0.0.2
menu MAINMENU text [3] Firewall-HCM-EDGE-03
menu MAINMENU command [3] telnet 10.0.0.3 2003
menu MAINMENU text q Exit
"""

VERTIV_ACS_SHOW = """
===============================
ACS-HCM-01
===============================
port    number  type    status
HCM-ROUTER-01   1  serial  CAS
HCM-SWITCH-02   2  serial  CAS
"""


def test_parse_cisco_menu_config_basic():
    final_options = _parse_cisco_menu_config(CISCO_MENU_CONFIG, ["MAINMENU"])
    assert set(final_options.keys()) == {"1", "2", "3"}
    assert final_options["1"]["ip"] == "10.0.0.1"
    assert final_options["1"]["port"] == 2001
    assert final_options["1"]["protocol"] == "telnet"
    assert final_options["1"]["description"] == "Router-HCM-CORE-01"
    assert final_options["2"]["protocol"] == "ssh"
    assert final_options["2"]["port"] == 22  # port SSH mac dinh khi khong ghi ro
    assert final_options["3"]["description"] == "Firewall-HCM-EDGE-03"
    # "q" (Exit) khong co IP dich -> phai bi loc bo
    assert "q" not in final_options


def test_parse_cisco_menu_config_wrong_menu_name_ignored():
    final_options = _parse_cisco_menu_config(CISCO_MENU_CONFIG, ["OTHERMENU"])
    assert final_options == {}


def test_parse_cisco_menu_config_multi_menu_prefixes_key():
    multi = CISCO_MENU_CONFIG + """
menu SECOND text 1 Extra-Device-01
menu SECOND command 1 telnet 10.0.0.9 2009
"""
    final_options = _parse_cisco_menu_config(multi, ["MAINMENU", "SECOND"])
    assert "MAINMENU [1]" in final_options
    assert "SECOND [1]" in final_options
    assert final_options["SECOND [1]"]["ip"] == "10.0.0.9"


def test_parse_vertiv_acs_show():
    hostname, final_options = _parse_vertiv_acs_show(VERTIV_ACS_SHOW, "10.9.9.9")
    assert hostname == "ACS-HCM-01"
    assert set(final_options.keys()) == {"1", "2"}
    assert final_options["1"] == {
        "description": "HCM-ROUTER-01", "ip": "10.9.9.9", "port": 1,
        "protocol": "serial", "_raw_key": "1", "_menu_name": "access", "vendor": "vertiv",
    }


def test_parse_vertiv_acs_show_no_serial_lines_returns_empty():
    hostname, final_options = _parse_vertiv_acs_show("===\nACS-EMPTY\n===\n(khong co gi)\n", "10.9.9.9")
    assert hostname == "ACS-EMPTY"
    assert final_options == {}
