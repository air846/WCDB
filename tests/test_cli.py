import json
from pathlib import Path

import pytest

from tests.fixtures import db_factory as f
from wechat_export import cli
from wechat_export.exceptions import KeyExtractError
from wechat_export.message_model import Media

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
        "stats": {"messages": 1, "format_version": cli.EXPORT_FORMAT_VERSION,
                  "image_key": True, "wxgf_available": cli.WXGF_AVAILABLE,
                  "voice_available": cli.VOICE_AVAILABLE},
    }), encoding="utf-8")
    assert cli._needs_reexport(out, b"k" * 16) is False
    assert cli._needs_reexport(out, None) is False       # 本次也没有密钥


def test_needs_reexport_old_format_version(tmp_path):
    out = tmp_path / "s"
    out.mkdir()
    (out / "session.json").write_text(json.dumps({
        "id": "s", "name": "S",
        "stats": {"messages": 1, "format_version": 0, "image_key": True,
                  "wxgf_available": True, "voice_available": True},
    }), encoding="utf-8")
    assert cli._needs_reexport(out, b"k" * 16) is True


def test_resolve_file_from_msg_file(tmp_path):
    account = tmp_path / "acct"
    target = account / "msg" / "file" / "2024-05" / "报告.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"pdf-bytes")
    arc = cli.MediaArchive(tmp_path / "out" / "media")
    resolver = cli.MediaResolver(account, arc, {}, {})
    media = Media(kind="file", ext=".pdf", filename="报告.pdf")
    out = resolver.resolve(media, "wxid_b", 1)
    assert out.status == "ok"
    assert out.rel_path.endswith(".pdf")
    assert (tmp_path / "out" / out.rel_path).read_bytes() == b"pdf-bytes"


def test_resolve_video_from_msg_video(tmp_path):
    account = tmp_path / "acct"
    target = account / "msg" / "video" / "2024-05" / "abc123.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"mp4-bytes")
    arc = cli.MediaArchive(tmp_path / "out" / "media")
    resolver = cli.MediaResolver(account, arc, {}, {})
    media = Media(kind="video", md5="abc123", ext=".mp4")
    out = resolver.resolve(media, "wxid_b", 1)
    assert out.status == "ok"
    assert out.rel_path.endswith(".mp4")
    assert (tmp_path / "out" / out.rel_path).read_bytes() == b"mp4-bytes"


def test_resolve_missing_file_is_placeholder(tmp_path):
    arc = cli.MediaArchive(tmp_path / "media")
    resolver = cli.MediaResolver(tmp_path / "acct", arc, {}, {})
    media = Media(kind="file", ext=".pdf", filename="nope.pdf")
    assert resolver.resolve(media, "wxid_b", 1).status == "missing"


def test_resolve_voice_converts_to_wav(tmp_path):
    pytest.importorskip("rsilk")
    from tests.fixtures.silk_factory import silk_bytes

    arc = cli.MediaArchive(tmp_path / "media")
    resolver = cli.MediaResolver(tmp_path / "acct", arc,
                                 {(1, 5): silk_bytes()}, {"wxid_b": 1})
    out = resolver.resolve(Media(kind="voice", ext=".silk", duration_ms=500),
                           "wxid_b", 5)
    assert out.status == "ok"
    assert out.ext == ".wav"
    assert out.duration_ms == 500
    assert resolver.voice_decoded == 1
    assert (tmp_path / out.rel_path).read_bytes()[:4] == b"RIFF"


def test_resolve_voice_fallback_when_no_decoder(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "decode_silk", lambda data: None)
    monkeypatch.setattr(cli, "VOICE_AVAILABLE", False)
    arc = cli.MediaArchive(tmp_path / "media")
    resolver = cli.MediaResolver(tmp_path / "acct", arc,
                                 {(1, 5): b"silk-bytes"}, {"wxid_b": 1})
    out = resolver.resolve(Media(kind="voice", ext=".silk", duration_ms=1200),
                           "wxid_b", 5)
    assert out.status == "ok"
    assert out.ext == ".silk"
    assert out.duration_ms == 1200
    assert resolver.voice_pending == 1


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
