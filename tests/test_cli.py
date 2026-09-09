import json
from pathlib import Path

import pytest

from tests.fixtures import db_factory as f
from wechat_export import cli
from wechat_export.exceptions import KeyExtractError

KEY = "a1" * 32


@pytest.fixture(autouse=True)
def _only_tmp_roots(monkeypatch):
    """固定候选数据根 = 测试传入的 --data-dir，避免扫到开发机上真实的微信数据。"""
    monkeypatch.setattr(cli.locator, "candidate_data_roots",
                        lambda override: [Path(override)] if override else [])


def _make_env(tmp_path):
    db_dir = tmp_path / "data" / "wxid_abc" / "db_storage" / "message"
    db_dir.mkdir(parents=True)
    db = db_dir / "message_0.db"
    f.create_encrypted_db(db, KEY)
    f.add_name(db, key_hex=KEY, rowid=1, user_name="wxid_b")
    f.add_name(db, key_hex=KEY, rowid=2, user_name="wxid_abc")
    f.add_session(db, key_hex=KEY, username="wxid_b")
    for i in range(3):
        f.insert_message(db, key_hex=KEY, username="wxid_b", local_id=i + 1,
                         local_type=1, real_sender_id=1 + (i % 2),
                         create_time=1700000000 + i, content=f"m{i}")
    return tmp_path, db


def _write_keys(tmp_path, keys: dict) -> Path:
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    return path


def test_main_success(tmp_path):
    env, db = _make_env(tmp_path)
    salt = db.read_bytes()[:16].hex()
    code = cli.main([
        "--data-dir", str(env / "data"),
        "--keys-file", str(_write_keys(env, {salt: KEY})),
        "--out", str(env / "out"), "--session", "wxid_b",
    ])
    assert code == 0
    out = env / "out"
    assert (out / "index.html").exists()
    assert (out / "sessions.json").exists()
    assert (out / "export_meta.json").exists()
    data = (out / "wxid_b" / "messages.json").read_text(encoding="utf-8")
    assert "m1" in data


def test_main_key_hex(tmp_path):
    env, _ = _make_env(tmp_path)
    code = cli.main([
        "--data-dir", str(env / "data"), "--key-hex", KEY,
        "--out", str(env / "out"), "--session", "wxid_b",
    ])
    assert code == 0


def test_main_resume_keeps_index(tmp_path):
    env, db = _make_env(tmp_path)
    out = env / "out"
    (out / "wxid_b").mkdir(parents=True)
    (out / "wxid_b" / ".done").write_text("done")
    (out / "wxid_b" / "session.json").write_text(json.dumps({
        "id": "wxid_b", "name": "李四", "chat_type": "single",
        "member_count": 0, "stats": {"messages": 7},
    }), encoding="utf-8")
    salt = db.read_bytes()[:16].hex()
    code = cli.main([
        "--data-dir", str(env / "data"),
        "--keys-file", str(_write_keys(env, {salt: KEY})),
        "--out", str(out), "--session", "wxid_b", "--resume",
    ])
    assert code == 0
    assert (out / "wxid_b" / ".done").read_text() == "done"
    sessions = json.loads((out / "sessions.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in sessions["sessions"]] == ["wxid_b"]
    assert sessions["counts"]["wxid_b"] == 7


def test_needs_reexport(tmp_path):
    out = tmp_path / "s"
    assert cli._needs_reexport(out, b"k" * 16) is False  # 无 session.json
    out.mkdir()
    (out / "session.json").write_text(json.dumps({
        "id": "s", "name": "S", "stats": {"messages": 1},
    }), encoding="utf-8")
    assert cli._needs_reexport(out, b"k" * 16) is True   # 上次无密钥
    (out / "session.json").write_text(json.dumps({
        "id": "s", "name": "S",
        "stats": {"messages": 1, "image_key": True, "wxgf_available": True},
    }), encoding="utf-8")
    assert cli._needs_reexport(out, b"k" * 16) is False
    assert cli._needs_reexport(out, None) is False       # 本次也没有密钥


def test_resume_reprocesses_when_image_key_available(tmp_path):
    env, db = _make_env(tmp_path)
    out = env / "out"
    sdir = out / "wxid_b"
    (sdir / "media" / "image").mkdir(parents=True)
    (sdir / "media" / "image" / "a.dat").write_bytes(b"x")
    (sdir / ".done").write_text("done")
    (sdir / "session.json").write_text(json.dumps({
        "id": "wxid_b", "name": "李四", "chat_type": "single",
        "member_count": 0, "stats": {"messages": 7},
    }), encoding="utf-8")
    salt = db.read_bytes()[:16].hex()
    code = cli.main([
        "--data-dir", str(env / "data"),
        "--keys-file", str(_write_keys(env, {salt: KEY})),
        "--out", str(out), "--session", "wxid_b", "--resume",
        "--image-key", "ab" * 16,
    ])
    assert code == 0
    assert "m1" in (sdir / "messages.json").read_text(encoding="utf-8")


def test_main_bad_key_hex(tmp_path):
    code = cli.main(["--key-hex", "zz", "--out", str(tmp_path / "o")])
    assert code == 1


def test_main_missing_wechat(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    code = cli.main(["--data-dir", str(empty), "--out", str(tmp_path / "o")])
    assert code == 2


class _FailProvider:
    def __init__(self, *args, **kwargs):
        pass

    def get_keys(self):
        raise KeyExtractError("no key", hint="mock")


def test_main_key_extract_failure(tmp_path, monkeypatch):
    env, _ = _make_env(tmp_path)
    monkeypatch.setattr(cli, "KeyProvider", _FailProvider)
    code = cli.main(["--data-dir", str(env / "data"), "--out", str(env / "out")])
    assert code == 3
