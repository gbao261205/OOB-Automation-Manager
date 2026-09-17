from oob_monitor import options_equal, diff_options


def _opt(ip, port, protocol, description):
    return {"ip": ip, "port": port, "protocol": protocol, "description": description}


def test_options_equal_true_for_identical():
    a = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    b = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    assert options_equal(a, b) is True


def test_options_equal_false_on_different_keys():
    a = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    b = {"2": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    assert options_equal(a, b) is False


def test_options_equal_false_on_description_change():
    a = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    b = {"1": _opt("10.0.0.1", 23, "telnet", "Router-B")}
    assert options_equal(a, b) is False


def test_options_equal_protocol_comparison_is_case_sensitive():
    # Characterization: _norm_proto() KHONG ha thuong protocol, chi thay None
    # bang "telnet" mac dinh - "TELNET" va "telnet" hien bi coi la KHAC NHAU.
    # Day la hanh vi hien tai, khong phai spec mong muon; ghi lai o day de
    # phat hien neu ai do vo tinh doi no khi gop parser (WP-E(b)).
    a = {"1": _opt("10.0.0.1", 23, "TELNET", "Router-A")}
    b = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    assert options_equal(a, b) is False


def test_options_equal_none_protocol_normalized_to_telnet_default():
    a = {"1": _opt("10.0.0.1", 23, None, "Router-A")}
    b = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    assert options_equal(a, b) is True


def test_diff_options_extra_and_missing():
    baseline = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    snapshot = {"2": _opt("10.0.0.2", 23, "telnet", "Router-B")}
    d = diff_options(baseline, snapshot)
    assert d == {"extra": ["2"], "missing": ["1"], "changed": []}


def test_diff_options_changed_description():
    baseline = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    snapshot = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A-RENAMED")}
    d = diff_options(baseline, snapshot)
    assert d == {"extra": [], "missing": [], "changed": ["1"]}


def test_diff_options_changed_ip_or_port():
    baseline = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    snapshot = {"1": _opt("10.0.0.9", 23, "telnet", "Router-A")}
    assert diff_options(baseline, snapshot)["changed"] == ["1"]

    snapshot2 = {"1": _opt("10.0.0.1", 2001, "telnet", "Router-A")}
    assert diff_options(baseline, snapshot2)["changed"] == ["1"]


def test_diff_options_no_changes():
    baseline = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    snapshot = {"1": _opt("10.0.0.1", 23, "telnet", "Router-A")}
    assert diff_options(baseline, snapshot) == {"extra": [], "missing": [], "changed": []}
