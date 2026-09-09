import struct

import pytest

from tests.fixtures import db_factory as f
from wechat_export.exceptions import ConfigError
from wechat_export.key_provider import (
    KeyProvider, derive_pad, deobfuscate, parse_codec_context, parse_key_hex,
    scan_pattern, verify_key,
)

PAD = bytes(range(32))  # 合成 pad；真实 pad 属本机运行时数据，不入库
KEY = "d" * 64


@pytest.mark.parametrize("bad", ["", "xyz", "a" * 63, "a" * 65, "a" * 32])
def test_parse_key_hex_bad(bad):
    with pytest.raises(ConfigError):
        parse_key_hex(bad)


@pytest.mark.parametrize("good", ["ab" * 32, "0x" + "cd" * 32, "x'ef" * 32 + "'"])
def test_parse_key_hex_good(good):
    assert len(parse_key_hex(good)) == 64


def _obf(plain: bytes, pad: bytes = PAD) -> bytes:
    return bytes(b ^ pad[i % 32] for i, b in enumerate(plain))


def _fake_codec_memory(key_hex: str, salt_hex: str, base: int = 0x100000,
                       pad: bytes = PAD):
    mem = bytearray(0x2000)

    def put(addr, data):
        mem[addr - base:addr - base + len(data)] = data

    codec = base + 0x100
    salt_addr = base + 0x400
    hmac_salt_addr = base + 0x500
    cipher_r = base + 0x600
    cipher_w = base + 0x700
    ks = base + 0x800
    key_addr = base + 0xA00
    hmac_addr = base + 0xB00
    salt = bytes.fromhex(salt_hex)
    ks_pt = b"x'" + key_hex.encode() + salt_hex.encode() + b"'"
    put(salt_addr, salt)
    put(hmac_salt_addr, bytes(b ^ 0x3A for b in salt))
    put(ks, _obf(ks_pt, pad))
    codec_data = bytearray(0x88)
    struct.pack_into("<15I", codec_data, 0,
                     0, 256000, 2, 16, 32, 16, 16, 4096, 99, 80, 64, 0, 2, 2, 0)
    struct.pack_into("<Q", codec_data, 0x48, salt_addr)
    struct.pack_into("<Q", codec_data, 0x50, hmac_salt_addr)
    struct.pack_into("<Q", codec_data, 0x68, cipher_r)
    struct.pack_into("<Q", codec_data, 0x70, cipher_w)
    put(codec, bytes(codec_data))
    for caddr in (cipher_r, cipher_w):
        c = bytearray(0x28)
        struct.pack_into("<iiQQQQ", c, 0, 0, 0, key_addr, hmac_addr, 0, ks)
        put(caddr, bytes(c))

    def read(addr, size):
        start = addr - base
        if start < 0 or start + size > len(mem):
            return b""
        return bytes(mem[start:start + size])

    return read, codec, base, len(mem)


def test_derive_pad_and_deobfuscate():
    salt_hex = "26cb02f2cc8e5da335c5cade877de4fa"
    ks_pt = b"x'" + KEY.encode() + salt_hex.encode() + b"'"
    ks_ct = _obf(ks_pt)
    pad = derive_pad(ks_ct, salt_hex)
    assert pad == PAD
    assert deobfuscate(ks_ct, pad) == ks_pt
    assert derive_pad(ks_ct, "00" * 16) is None


def test_parse_codec_context_finds_key():
    salt_hex = "26cb02f2cc8e5da335c5cade877de4fa"
    read, codec, _, _ = _fake_codec_memory(KEY, salt_hex)
    assert parse_codec_context(read, codec) == [(salt_hex, KEY)]


def test_scan_pattern_finds_codec():
    salt_hex = "26cb02f2cc8e5da335c5cade877de4fa"
    read, codec, base, size = _fake_codec_memory(KEY, salt_hex)
    assert codec in scan_pattern(read, [(base, size)])


def test_verify_key(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY)
    page1 = db.read_bytes()[:4096]
    assert verify_key(KEY, page1)
    assert not verify_key("cd" * 32, page1)


def test_key_provider_manual(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY)
    kp = KeyProvider([db], manual_key=KEY)
    assert list(kp.get_keys().values()) == [KEY]


def test_key_provider_preloaded(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY)
    salt = db.read_bytes()[:16].hex()
    kp = KeyProvider([db], keys={salt: KEY})
    assert kp.get_keys() == {salt: KEY}
