from pathlib import Path

import pytest

from wechat_export.exceptions import WeChatNotFoundError
from wechat_export.locator import (
    locate_data, message_db_files, scan_accounts,
)


def _fake_data_root(tmp_path) -> Path:
    root = tmp_path / "data"
    acc = root / "wxid_abc"
    (acc / "db_storage" / "message").mkdir(parents=True)
    (acc / "db_storage" / "message" / "message_0.db").write_bytes(b"x")
    (acc / "db_storage" / "message" / "message_1.db").write_bytes(b"x")
    return root


def test_scan_accounts(tmp_path):
    root = _fake_data_root(tmp_path)
    (root / "not_an_account").mkdir()
    assert scan_accounts(root) == ["wxid_abc"]


def test_message_db_files_sorted(tmp_path):
    root = _fake_data_root(tmp_path)
    acc = root / "wxid_abc"
    files = message_db_files(acc / "db_storage")
    assert [f.name for f in files] == ["message_0.db", "message_1.db"]


def test_locate_data_with_override(tmp_path):
    root = _fake_data_root(tmp_path)
    infos = locate_data(override=root)
    assert [i.wxid for i in infos] == ["wxid_abc"]
    assert infos[0].db_storage.name == "db_storage"


def test_locate_data_not_found(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    # 固定候选根，避免扫到开发机上真实的微信数据目录
    monkeypatch.setattr("wechat_export.locator.candidate_data_roots",
                        lambda override: [empty])
    with pytest.raises(WeChatNotFoundError):
        locate_data(override=empty)
