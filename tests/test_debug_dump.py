import os

from oob_monitor import debug_dump, DEBUG_VERIFY_LOG


def test_debug_dump_noop_when_disabled(base_cfg, isolated_cwd):
    base_cfg["debug_verify"] = False
    debug_dump(base_cfg, "alias1", "1", "LABEL", "some text")
    assert not os.path.exists("debug-logs")


def test_debug_dump_writes_file_when_enabled(base_cfg, isolated_cwd):
    base_cfg["debug_verify"] = True
    debug_dump(base_cfg, "alias1", "1", "LABEL", "some text")
    assert os.path.exists(DEBUG_VERIFY_LOG)
    with open(DEBUG_VERIFY_LOG, encoding="utf-8") as f:
        content = f.read()
    assert "LABEL" in content
    assert "alias1" in content
    assert "len=9" in content


def test_debug_dump_includes_elapsed_ms_when_provided(base_cfg, isolated_cwd):
    base_cfg["debug_verify"] = True
    debug_dump(base_cfg, "alias1", "1", "LABEL", "some text", elapsed_ms=123)
    with open(DEBUG_VERIFY_LOG, encoding="utf-8") as f:
        content = f.read()
    assert "+123ms" in content


def test_debug_dump_omits_elapsed_tag_when_not_provided(base_cfg, isolated_cwd):
    base_cfg["debug_verify"] = True
    debug_dump(base_cfg, "alias1", "1", "LABEL", "some text")
    with open(DEBUG_VERIFY_LOG, encoding="utf-8") as f:
        content = f.read()
    assert "ms]" not in content
    assert "+" not in content.split("LABEL")[0].splitlines()[-1]
