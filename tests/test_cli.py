import pytest

from wechat_export import cli
from tests.fixtures import db_factory as f
from pathlib import Path

KEY = "a1" * 32


@pytest.fixture(autouse=True)
def _only_tmp_roots(monkeypatch):
    """固定候选数据根 = 测试传入的 --data-dir，避免扫到开发机上真实的微信数据。"""
    monkeypatch.setattr(cli.locator, "candidate_data_roots",
                        lambda override: [Path(override)] if override else [])


def _make_env(tmp_path):
    """搭建：数据根(wxid_abc/db_storage/message/message_0.db 带 3 条消息) + 输出目录"""
    root = tmp_path / "data" / "wxid_abc" / "db_storage" / "message"
    root.mkdir(parents=True)
    db = root / "message_0.db"
    f.create_encrypted_db(db, KEY)
    for i in range(3):
        f.insert_message(db, key_hex=KEY, msg_id=i + 1, ts=1700000000000 + i,
                         type_=1, content=f"m{i}", is_sender=i % 2,
                         talker="wxid_b")
    return tmp_path


def test_main_success(tmp_path):
    env = _make_env(tmp_path)
    code = cli.main([
        "--data-dir", str(env / "data"), "--key-hex", KEY,
        "--out", str(env / "out"), "--session", "wxid_b",
    ])
    assert code == 0
    out = env / "out"
    assert (out / "index.html").exists()
    assert (out / "sessions.json").exists()
    assert (out / "export_meta.json").exists()
    data = (out / "wxid_b" / "messages.json").read_text(encoding="utf-8")
    assert "m1" in data


def test_main_resume_skips_done(tmp_path):
    env = _make_env(tmp_path)
    out = env / "out"
    (out / "wxid_b").mkdir(parents=True)
    (out / "wxid_b" / ".done").write_text("done")
    code = cli.main([
        "--data-dir", str(env / "data"), "--key-hex", KEY,
        "--out", str(out), "--session", "wxid_b", "--resume",
    ])
    assert code == 0
    # 会话被跳过：.done 内容保持原样（未被重新写入）
    assert (out / "wxid_b" / ".done").read_text() == "done"


def test_main_bad_key_hex(tmp_path):
    code = cli.main(["--key-hex", "zz", "--out", str(tmp_path / "o")])
    assert code == 1  # ConfigError：密钥格式在定位之前先校验


def test_main_missing_wechat(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    code = cli.main(["--data-dir", str(empty), "--out", str(tmp_path / "o")])
    assert code == 2  # WeChatNotFoundError
