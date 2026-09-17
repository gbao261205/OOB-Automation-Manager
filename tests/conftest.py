import copy

import pytest

from oob_monitor import DEFAULT_CONFIG


@pytest.fixture
def base_cfg():
    """Bản sao DEFAULT_CONFIG dùng chung cho các test, tránh test này làm
    hỏng config của test khác (DEFAULT_CONFIG là dict module-level, dùng
    chung reference nếu không copy)."""
    return copy.deepcopy(DEFAULT_CONFIG)


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Chuyển cwd sang thư mục tạm cho các test ghi file (debug-logs/,
    verify-logs/, baseline-logs/...) để không làm bẩn thư mục repo thật."""
    monkeypatch.chdir(tmp_path)
    return tmp_path
