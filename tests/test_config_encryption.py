import json

from oob_monitor import (
    load_config, save_config, _encrypt_cred, _decrypt_cred,
    _encrypt_config_fields, _decrypt_config_fields, _SENSITIVE_CONFIG_FIELDS,
)


def test_encrypt_cred_roundtrip():
    plain = "S3cr3t!"
    enc = _encrypt_cred(plain)
    assert enc != plain
    assert enc.startswith("ENC:") or enc.startswith("B64:")
    assert _decrypt_cred(enc) == plain


def test_encrypt_cred_empty_passthrough():
    assert _encrypt_cred("") == ""
    assert _decrypt_cred("") == ""


def test_encrypt_config_fields_covers_all_sensitive_fields(base_cfg):
    for k in _SENSITIVE_CONFIG_FIELDS:
        base_cfg[k] = f"plain-{k}"
    base_cfg["credentials"] = [{"username": "u1", "password": "p1", "enable_password": "e1"}]

    enc = _encrypt_config_fields(base_cfg)
    for k in _SENSITIVE_CONFIG_FIELDS:
        assert enc[k] != base_cfg[k]
        assert enc[k].startswith("ENC:") or enc[k].startswith("B64:")
    assert enc["credentials"][0]["password"] != "p1"
    assert enc["credentials"][0]["username"] == "u1"  # username khong ma hoa

    dec = _decrypt_config_fields(enc)
    for k in _SENSITIVE_CONFIG_FIELDS:
        assert dec[k] == base_cfg[k]
    assert dec["credentials"][0]["password"] == "p1"


def test_encrypt_config_fields_does_not_mutate_original(base_cfg):
    base_cfg["password"] = "plain-pass"
    enc = _encrypt_config_fields(base_cfg)
    assert base_cfg["password"] == "plain-pass"
    assert enc["password"] != "plain-pass"


def test_save_then_load_config_roundtrip(base_cfg, isolated_cwd):
    base_cfg["password"] = "hunter2"
    base_cfg["vertiv_connect_password"] = "vpass"
    base_cfg["credentials"] = [{"username": "u1", "password": "p1", "enable_password": ""}]
    save_config("oob_config.json", base_cfg)

    with open("oob_config.json", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["password"] != "hunter2"
    assert "hunter2" not in json.dumps(raw)

    loaded = load_config("oob_config.json")
    assert loaded["password"] == "hunter2"
    assert loaded["vertiv_connect_password"] == "vpass"
    assert loaded["credentials"][0]["password"] == "p1"


def test_load_config_backward_compatible_with_plaintext_file(isolated_cwd):
    with open("oob_config.json", "w", encoding="utf-8") as f:
        json.dump({"username": "admin", "password": "plaintext-old"}, f)

    loaded = load_config("oob_config.json")
    assert loaded["password"] == "plaintext-old"

    save_config("oob_config.json", loaded)
    with open("oob_config.json", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["password"] != "plaintext-old"
    assert load_config("oob_config.json")["password"] == "plaintext-old"


def test_load_config_missing_file_returns_default(isolated_cwd):
    cfg = load_config("does_not_exist.json")
    assert cfg["password"] == ""
