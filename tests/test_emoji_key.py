"""表情密钥派生/校验与内存扫描单测（合成样本，不依赖真机）。"""

import ctypes
import os

import pytest

from tests.fixtures.emoji_factory import (
    encrypt_emoji, gif_bytes, jpeg_bytes, png_bytes,
)
from wechat_export.emoji_key import (
    _plaintext_ok, account_wxid, derive_key, extract_emoji_key, parse_emoji_key,
    verify_key,
)
from wechat_export.exceptions import ConfigError

# 合成向量：冻结 "seed + wxid + EMOTICON" 的拼接顺序，不含真实账号数据
SEED = "1234567"
WXID = "wxid_demo"
KEY = bytes.fromhex("943713aae3f94c28005cdb11c17d3b80")


def _samples(tmp_path, key=KEY):
    paths = []
    for name, plain in (("a", gif_bytes()), ("b", png_bytes()), ("c", jpeg_bytes())):
        path = tmp_path / name
        path.write_bytes(encrypt_emoji(plain, key))
        paths.append(path)
    return paths


def test_derive_key_uses_frozen_concat_order():
    assert derive_key(SEED, WXID) == KEY


def test_account_wxid():
    assert account_wxid("/x/y/wxid_demo_1a2b") == WXID
    assert account_wxid("/x/y/wxid_plain") == "wxid_plain"


def test_parse_emoji_key():
    assert parse_emoji_key("0123456789abcdef0123456789abcdef") == bytes.fromhex(
        "0123456789abcdef0123456789abcdef")
    assert parse_emoji_key("0x" + "ab" * 16) == bytes.fromhex("ab" * 16)
    assert parse_emoji_key("0123456789abcdef") == b"0123456789abcdef"
    with pytest.raises(ConfigError):
        parse_emoji_key("tooshort")


def test_verify_key_true_and_false(tmp_path):
    samples = _samples(tmp_path)
    assert verify_key(KEY, samples) is True
    assert verify_key(derive_key("7654321", WXID), samples) is False
    assert verify_key(b"\x00" * 16, samples) is False


def test_verify_key_single_head_needs_one_hit(tmp_path):
    path = tmp_path / "only"
    path.write_bytes(encrypt_emoji(gif_bytes(), KEY))
    assert verify_key(KEY, [path]) is True


def test_plaintext_ok_rejects_weak_magic():
    # BM 只有 2 字节 magic，在数万 seed 候选下会误报，必须拒绝
    assert _plaintext_ok(b"BM" + b"\x00" * 20) is False
    assert _plaintext_ok(b"RIFF\x00\x00\x00\x00WAVEfmt ") is False
    assert _plaintext_ok(b"RIFF\x00\x00\x00\x00WEBPVP8 ") is True
    assert _plaintext_ok(gif_bytes()) is True
    assert _plaintext_ok(b"wxam" + b"\x00" * 12) is True


def test_extract_emoji_key_from_memory(tmp_path, monkeypatch):
    import wechat_export.emoji_key as ek

    samples = _samples(tmp_path)
    # seed 以十进制串形式驻留内存；旁边放干扰数字串
    payload = b"\x00" * 32 + b"20240101" + b"\x00" * 16 + SEED.encode() + b"\x00" * 32
    buf = ctypes.create_string_buffer(payload)
    monkeypatch.setattr(ek, "find_weixin_pids", lambda: [os.getpid()])
    try:
        found = ek.extract_emoji_key(tmp_path, samples, wxid=WXID)
    finally:
        del buf
    assert found == KEY


def test_extract_emoji_key_without_process(tmp_path, monkeypatch):
    import wechat_export.emoji_key as ek

    samples = _samples(tmp_path)
    monkeypatch.setattr(ek, "find_weixin_pids", lambda: [])
    assert ek.extract_emoji_key(tmp_path, samples, wxid=WXID) is None


def test_extract_emoji_key_no_samples(tmp_path, monkeypatch):
    import wechat_export.emoji_key as ek

    monkeypatch.setattr(
        ek, "find_weixin_pids",
        lambda: pytest.fail("无样本时不应触碰进程内存"))
    assert ek.extract_emoji_key(tmp_path, [], wxid=WXID) is None
