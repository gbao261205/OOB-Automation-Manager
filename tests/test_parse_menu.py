"""Characterization test cho parse_menu (oob_lib.py) va logic parse tuong
duong trong poll_host_multi (oob_monitor.py::_parse_cisco_menu_config /
_parse_vertiv_acs_show).

LUU Y VE FIXTURE: chua co du lieu output THAT tu thiet bi (khong co quyen
truy cap thiet bi/log that trong phien lam viec nay). Cac dong config duoi
day la SYNTHETIC nhung dung DUNG cu phap ma TEXT_RE/CMD_TELNET_RE/CMD_SSH_RE
cua chinh code nay dinh nghia (cu phap lenh "menu ... text/command" chuan
cua async terminal server) - khong phai doan van ban tuy tien. Nen thay bang
sample that (an danh hoa) khi co, va doi chieu lai ket qua truoc khi dua vao
WP-E(b) gop parser."""

from oob_lib import parse_menu
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


def test_oob_lib_parse_menu_basic():
    options = parse_menu(CISCO_MENU_CONFIG, "MAINMENU")
    assert set(options.keys()) == {"1", "2", "[3]"}
    assert options["1"] == {"description": "Router-HCM-CORE-01", "ip": "10.0.0.1", "port": 2001, "protocol": "telnet"}
    assert options["2"] == {"description": "Switch-HCM-ACCESS-02", "ip": "10.0.0.2", "port": 22, "protocol": "ssh"}
    assert options["[3]"] == {"description": "Firewall-HCM-EDGE-03", "ip": "10.0.0.3", "port": 2003, "protocol": "telnet"}
    # "q" (Exit) khong co IP dich -> phai bi loc bo
    assert "q" not in options


def test_oob_lib_parse_menu_wrong_menu_name_ignored():
    options = parse_menu(CISCO_MENU_CONFIG, "OTHERMENU")
    assert options == {}


def test_oob_monitor_parse_cisco_menu_config_matches_oob_lib_shape():
    final_options = _parse_cisco_menu_config(CISCO_MENU_CONFIG, ["MAINMENU"])
    assert set(final_options.keys()) == {"1", "2", "3"}
    assert final_options["1"]["ip"] == "10.0.0.1"
    assert final_options["1"]["port"] == 2001
    assert final_options["1"]["protocol"] == "telnet"
    assert final_options["1"]["description"] == "Router-HCM-CORE-01"
    assert final_options["3"]["description"] == "Firewall-HCM-EDGE-03"
    # Khac biet DA BIET voi oob_lib.parse_menu: key hien thi cho option co
    # ngoac vuong ([3]) la "3" (da boc ngoac) chu khong phai "[3]" nhu ban
    # oob_lib - day chinh la diem lech hanh vi can quyet dinh khi gop (WP-E(b)).
    assert "[3]" not in final_options


def test_oob_monitor_parse_cisco_menu_config_multi_menu_prefixes_key():
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
