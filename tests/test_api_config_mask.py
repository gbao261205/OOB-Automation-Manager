import json

import pytest


@pytest.fixture
def oob_web_module(isolated_cwd):
    # Import CHỈ sau khi cwd đã chuyển sang thư mục tạm - oob_web chạy
    # _init_users_db() (ghi baseline.db) ngay tại thời điểm import module,
    # nên phải tránh import lúc cwd còn là thư mục repo thật.
    import oob_web
    return oob_web


@pytest.fixture
def client(oob_web_module):
    oob_web_module.app.config["TESTING"] = True
    with oob_web_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "admin"
        yield c


def test_get_config_masks_sensitive_fields_when_set(client, isolated_cwd, oob_web_module):
    import oob_monitor
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    cfg["password"] = "realsecret"
    cfg["vertiv_connect_password"] = "vsecret"
    oob_monitor.save_config(oob_monitor.CONFIG_FILE_DEFAULT, cfg)

    resp = client.get("/api/config")
    body = resp.get_json()
    assert body["password"] == "******"
    assert body["vertiv_connect_password"] == "******"
    assert "realsecret" not in json.dumps(body)
    assert "credentials" not in body


def test_get_config_empty_password_not_masked(client, isolated_cwd):
    resp = client.get("/api/config")
    body = resp.get_json()
    assert body["password"] == ""


def test_post_config_with_mask_does_not_overwrite_real_password(client, isolated_cwd, oob_web_module):
    import oob_monitor
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    cfg["password"] = "realsecret"
    oob_monitor.save_config(oob_monitor.CONFIG_FILE_DEFAULT, cfg)

    # UI doc lai GET (tra ve "******") roi gui nguyen lai khong sua field password
    resp = client.post("/api/config", json={"password": "******", "username": "newuser"})
    assert resp.status_code == 200

    reloaded = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    assert reloaded["password"] == "realsecret"
    assert reloaded["username"] == "newuser"


def test_post_config_with_real_new_password_updates_it(client, isolated_cwd, oob_web_module):
    import oob_monitor
    resp = client.post("/api/config", json={"password": "brandnewpass"})
    assert resp.status_code == 200
    reloaded = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    assert reloaded["password"] == "brandnewpass"

    with open(oob_monitor.CONFIG_FILE_DEFAULT, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["password"] != "brandnewpass"
