"""Characterization test cho extract_hostname() (oob_monitor.py) - ham vua
duoc rut ra tu closure ben trong run_deep_verify() len module-level (WP-E),
gop voi ban module-level cu (da la dead code, khong bao gio duoc goi - da xoa).

Fixture SYNTHETIC dua tren cac pattern da co san trong code (banner FreeBSD,
Cisco prompt, Vertiv Type the hot key...), khong phai capture that tu thiet
bi - xem ghi chu trong tests/test_parse_menu.py."""

from oob_monitor import extract_hostname


def test_cisco_prompt_hash():
    assert extract_hostname("HCM-ROUTER-01#") == "HCM-ROUTER-01"


def test_cisco_prompt_gt():
    assert extract_hostname("HCM-ROUTER-01>") == "HCM-ROUTER-01"


def test_freebsd_login_banner():
    out = "FreeBSD/amd64 (HCM-OOB-FW01) (ttyu0)\r\nlogin: "
    assert extract_hostname(out) == "HCM-OOB-FW01"


def test_unix_login_prompt():
    assert extract_hostname("HCM-SRV-01 login: ") == "HCM-SRV-01"


def test_huawei_banner():
    out = "*    HCM-HW-CORE01    *\n<HCM-HW-CORE01>"
    assert extract_hostname(out) == "HCM-HW-CORE01"


def test_vertiv_internal_prompt_excluded():
    # "cli->" va cac prompt noi bo Vertiv (access/admin/root) khong duoc
    # nham la hostname thiet bi dich.
    assert extract_hostname("access>") is None
    assert extract_hostname("cli->") is None


def test_auth_required_when_only_login_prompt_seen():
    assert extract_hostname("Username: ") == "AUTH_REQUIRED"
    assert extract_hostname("Password: ") == "AUTH_REQUIRED"


def test_no_signal_returns_none():
    assert extract_hostname("") is None
    assert extract_hostname("khong co gi lien quan\r\n") is None


def test_connection_noise_lines_are_skipped():
    out = "telnet 10.0.0.5\r\nTrying 10.0.0.5...\r\nHCM-DEST-01#"
    assert extract_hostname(out) == "HCM-DEST-01"


def test_vertiv_data_buffering_suspended_line_skipped():
    out = "Data Buffering Suspended\r\nHCM-DEST-02#"
    assert extract_hostname(out) == "HCM-DEST-02"


def test_prefers_last_matching_line_reading_backwards():
    # Duyet nguoc tu cuoi - dong prompt CUOI CUNG phai thang.
    out = "HCM-OLD-01#\r\nHCM-NEW-02#"
    assert extract_hostname(out) == "HCM-NEW-02"
