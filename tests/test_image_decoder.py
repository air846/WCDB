"""图片解码与密钥提取单测（全部使用合成样本，不依赖真机）。"""

import struct
from pathlib import Path

import pytest
from Crypto.Cipher import AES
from Crypto.Util import Padding

from wechat_export.image_decoder import (
    DEFAULT_XOR_KEY, V1_MAGIC, V2_MAGIC, aligned_aes_size, decode_dat,
    decode_v1, decode_v2, derive_xor_key, detect_format, global_xor_key,
    parse_v2_header,
)
from wechat_export.image_key import (
    _candidate_keys, _plaintext_ok, collect_oracles,
)

KEY = bytes(range(16))
JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01"
PNG_HEAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
JPEG_TAIL = b"\x12\x34\x56\x78\xff\xd9"
PNG_TAIL = b"\x11\x22\x33\x44\xae\x42\x60\x82"


def make_v2(plain: bytes, aes_size: int, xor_key: int = DEFAULT_XOR_KEY,
            aes_key: bytes = KEY, magic: bytes = V2_MAGIC,
            raw: bytes = b"") -> bytes:
    """按 V2 规则构造密文文件。plain = aes_size + raw + xor 部分。"""
    aes_plain = plain[:aes_size]
    rest = plain[aes_size:]
    padded = Padding.pad(aes_plain, 16)
    enc = AES.new(aes_key, AES.MODE_ECB).encrypt(padded)
    xor = bytes(b ^ xor_key for b in rest)
    header = magic + struct.pack("<LL", aes_size, len(rest)) + b"\x01"
    return header + enc + raw + xor


def test_aligned_aes_size():
    assert aligned_aes_size(1024) == 1040
    assert aligned_aes_size(16) == 32
    assert aligned_aes_size(15) == 16
    assert aligned_aes_size(1) == 16
    assert aligned_aes_size(0) == 16


def test_detect_format():
    assert detect_format(JPEG_HEAD)[1] == ".jpg"
    assert detect_format(PNG_HEAD)[1] == ".png"
    assert detect_format(b"GIF89a...")[1] == ".gif"
    assert detect_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ")[1] == ".webp"
    assert detect_format(b"wxgf\x13\x00")[1] == ".wxgf"
    assert detect_format(b"not an image") is None


def test_parse_v2_header():
    data = make_v2(JPEG_HEAD + JPEG_TAIL, aes_size=16)
    header = parse_v2_header(data)
    assert header is not None
    assert header["aes_size"] == 16
    assert header["aligned_aes_size"] == 32
    assert header["xor_size"] == len(JPEG_TAIL)
    assert parse_v2_header(b"\x00" * 40) is None
    assert parse_v2_header(V2_MAGIC + struct.pack("<LL", 16, 1 << 30) + b"\x01") is None


def test_derive_xor_key():
    assert derive_xor_key(bytes(b ^ 0x5A for b in JPEG_TAIL), ".jpg") == 0x5A
    assert derive_xor_key(bytes(b ^ 0x11 for b in PNG_TAIL), ".png") == 0x11
    assert derive_xor_key(b"\x00\x01", ".jpg") is None
    assert derive_xor_key(b"abcdef", ".webp") is None


def test_global_xor_key():
    samples = [bytes(b ^ 0xED for b in JPEG_TAIL) for _ in range(3)]
    samples.append(bytes(b ^ 0x88 for b in JPEG_TAIL))
    assert global_xor_key(samples) == 0xED
    assert global_xor_key([b"xx"]) == DEFAULT_XOR_KEY


def test_decode_v2_roundtrip_jpeg():
    plain = JPEG_HEAD + b"\x00" * 1008 + b"\xaa" * 500 + JPEG_TAIL
    data = make_v2(plain, aes_size=1024)
    result = decode_v2(data, KEY)
    assert result is not None
    out, ext, renderable = result
    assert out == plain
    assert ext == ".jpg" and renderable is True


def test_decode_v2_roundtrip_png_autokey():
    plain = PNG_HEAD + b"\x00" * 1008 + b"\xbb" * 20 + PNG_TAIL
    data = make_v2(plain, aes_size=1024, xor_key=0x5C)
    result = decode_v2(data, KEY)  # xor_key 由尾部反推
    assert result is not None
    assert result[0] == plain
    assert result[1] == ".png"


def test_decode_v2_wrong_key():
    plain = JPEG_HEAD + b"\x00" * 1008 + JPEG_TAIL
    data = make_v2(plain, aes_size=1024)
    assert decode_v2(data, b"\x00" * 16) is None


def test_decode_v2_with_raw_region():
    plain = JPEG_HEAD + b"\x00" * 1008 + b"\xcc" * 30 + JPEG_TAIL
    aes_size = 1024
    # 把 raw 区插入到 AES 与 XOR 之间（rest 前 30 字节作为 raw）
    rest = b"\xcc" * 30 + JPEG_TAIL
    data = make_v2(plain, aes_size=aes_size, raw=rest[:30])
    # make_v2 把 rest 全部 XOR，这里重写：aes 后接 raw（明文）+ XOR(尾部)
    aes_plain = plain[:aes_size]
    padded = Padding.pad(aes_plain, 16)
    enc = AES.new(KEY, AES.MODE_ECB).encrypt(padded)
    tail = JPEG_TAIL
    header = V2_MAGIC + struct.pack("<LL", aes_size, len(tail)) + b"\x01"
    data = header + enc + rest[:30] + bytes(b ^ DEFAULT_XOR_KEY for b in tail)
    result = decode_v2(data, KEY)
    assert result is not None
    assert result[0] == plain


def test_decode_v1_whole_file_xor():
    plain = JPEG_HEAD + b"body" + JPEG_TAIL
    data = bytes(b ^ 0x77 for b in plain)
    result = decode_v1(data)
    assert result is not None
    assert result[0] == plain
    assert result[1] == ".jpg"


def test_decode_v1_first_byte_key():
    plain = PNG_HEAD + b"body" + PNG_TAIL
    key = 0x42
    data = bytes([key]) + bytes(b ^ key for b in plain)
    result = decode_v1(data)
    assert result is not None
    assert result[0] == plain
    assert result[1] == ".png"


def test_decode_v1_fixed_aes_container():
    plain = JPEG_HEAD + b"\x00" * 1008 + JPEG_TAIL
    data = make_v2(plain, aes_size=1024, aes_key=b"cfcd208495d565ef",
                   magic=V1_MAGIC)
    result = decode_dat(data)
    assert result is not None
    assert result[0] == plain


def test_decode_dat_v2_needs_key():
    plain = JPEG_HEAD + b"\x00" * 1008 + JPEG_TAIL
    data = make_v2(plain, aes_size=1024)
    assert decode_dat(data) is None
    assert decode_dat(data, KEY) is not None


def test_candidate_keys():
    assert _candidate_keys(b"a" * 16) == [b"a" * 16]
    keys = _candidate_keys(b"a" * 32)
    assert b"a" * 16 in keys and b"a" * 32 in keys
    hex_keys = _candidate_keys(b"0123456789abcdef0123456789abcdef")
    assert bytes.fromhex("0123456789abcdef0123456789abcdef") in hex_keys
    assert _candidate_keys(b"short") == []


def test_plaintext_ok():
    assert _plaintext_ok(JPEG_HEAD)
    assert _plaintext_ok(PNG_HEAD)
    assert not _plaintext_ok(b"\xff\xd8\xff\x99" + b"\x00" * 12)
    assert not _plaintext_ok(b"random bytes here")


def test_collect_oracles(tmp_path):
    plain_a = JPEG_HEAD + b"\x00" * 1008 + JPEG_TAIL
    plain_b = PNG_HEAD + b"\x00" * 1008 + PNG_TAIL
    (tmp_path / "a.dat").write_bytes(make_v2(plain_a, 1024))
    (tmp_path / "b.dat").write_bytes(make_v2(plain_b, 1024, aes_key=b"x" * 16))
    (tmp_path / "c.dat").write_bytes(b"not v2")
    oracles = collect_oracles([tmp_path / "a.dat", tmp_path / "b.dat", tmp_path / "c.dat"])
    assert len(oracles) == 2
    assert all(len(ct) == 16 for ct in oracles)


WXGF_FIXTURE = Path(__file__).parent / "fixtures" / "sample.wxgf"


def test_decode_wxgf():
    from wechat_export.image_decoder import WXGF_AVAILABLE, decode_wxgf
    if not WXGF_AVAILABLE:
        pytest.skip("av 未安装")
    result = decode_wxgf(WXGF_FIXTURE.read_bytes())
    assert result is not None
    jpg, ext = result
    assert ext == ".jpg" and jpg.startswith(b"\xff\xd8")
    assert decode_wxgf(b"not a wxgf") is None
    assert decode_wxgf(b"wxgf" + b"\x00" * 40) is None


def test_media_resolver_transcodes_wxgf(tmp_path):
    import hashlib
    from wechat_export.image_decoder import WXGF_AVAILABLE
    if not WXGF_AVAILABLE:
        pytest.skip("av 未安装")
    from wechat_export.cli import MediaResolver
    from wechat_export.exporter.media_archive import MediaArchive
    from wechat_export.message_model import Media

    wxgf = WXGF_FIXTURE.read_bytes()
    dat = make_v2(wxgf, aes_size=1024)
    username = "wxid_test"
    md5 = "c" * 32
    attach = (tmp_path / "msg" / "attach"
              / hashlib.md5(username.encode()).hexdigest() / "2026-01" / "Img")
    attach.mkdir(parents=True)
    (attach / f"{md5}.dat").write_bytes(dat)
    archive = MediaArchive(tmp_path / "out" / "media")
    resolver = MediaResolver(tmp_path, archive, {}, {}, image_key=KEY)
    media = resolver.resolve(Media(kind="image", md5=md5), username, 1)
    assert media.ext == ".jpg"
    assert (tmp_path / "out" / media.rel_path).read_bytes().startswith(b"\xff\xd8")


def test_decode_raw_aes():
    from wechat_export.image_decoder import decode_raw_aes
    plain = PNG_HEAD + b"emoji-body" + PNG_TAIL
    padded = Padding.pad(plain, 16)
    data = AES.new(KEY, AES.MODE_ECB).encrypt(padded)
    result = decode_raw_aes(data, KEY)
    assert result is not None
    assert result[0] == plain
    assert result[1] == ".png"
    assert decode_raw_aes(data, b"\x00" * 16) is None
    assert decode_raw_aes(b"short", KEY) is None


def test_detect_emoji_plain():
    from wechat_export.image_decoder import detect_emoji_plain
    assert detect_emoji_plain(b"wxam" + b"\x00" * 12) == ("wxam", ".wxam", False)
    assert detect_emoji_plain(b"GIF89a...")[1] == ".gif"
    assert detect_emoji_plain(b"not an image") is None


def test_decrypt_emoji_roundtrip():
    from tests.fixtures.emoji_factory import (
        encrypt_emoji, gif_bytes, jpeg_bytes, png_bytes,
    )
    from wechat_export.image_decoder import decrypt_emoji, detect_emoji_plain

    key = bytes(range(16))
    cases = [(gif_bytes(), ".gif"), (png_bytes(), ".png"), (jpeg_bytes(), ".jpg"),
             (WXGF_FIXTURE.read_bytes(), ".wxgf")]
    for plain, ext in cases:
        assert decrypt_emoji(encrypt_emoji(plain, key), key) == plain
        assert detect_emoji_plain(plain)[1] == ext


def test_decrypt_emoji_rejects():
    from tests.fixtures.emoji_factory import encrypt_emoji, gif_bytes
    from wechat_export.image_decoder import decrypt_emoji

    key = bytes(range(16))
    data = encrypt_emoji(gif_bytes(), key)
    assert decrypt_emoji(data, b"\x00" * 16) is None      # 错密钥
    assert decrypt_emoji(data, None) is None
    assert decrypt_emoji(data, b"short") is None
    assert decrypt_emoji(b"", key) is None
    assert decrypt_emoji(data + b"\x01", key) is None     # 非 16 倍数
    assert decrypt_emoji(b"\x00" * 8, key) is None        # 过短


def test_decrypt_emoji_tolerates_missing_padding():
    from wechat_export.image_decoder import decrypt_emoji

    key = bytes(range(16))
    plain = PNG_HEAD + b"\xaa" * 16                       # 32 字节，未带 PKCS7
    data = AES.new(key, AES.MODE_CBC, key).encrypt(plain)
    assert decrypt_emoji(data, key) == plain


def test_parse_image_key():
    from wechat_export.cli import parse_image_key
    from wechat_export.exceptions import ConfigError
    import pytest

    assert parse_image_key("0123456789abcdef0123456789abcdef") == bytes.fromhex(
        "0123456789abcdef0123456789abcdef")
    assert parse_image_key("0x" + "ab" * 16) == bytes.fromhex("ab" * 16)
    assert parse_image_key("0123456789abcdef") == b"0123456789abcdef"
    with pytest.raises(ConfigError):
        parse_image_key("tooshort")


def test_media_resolver_decodes_v2(tmp_path):
    import hashlib
    from wechat_export.cli import MediaResolver
    from wechat_export.exporter.media_archive import MediaArchive
    from wechat_export.message_model import Media

    username = "wxid_test"
    md5 = "a" * 32
    plain = JPEG_HEAD + b"\x00" * 1008 + b"body" + JPEG_TAIL
    dat = make_v2(plain, aes_size=1024)
    attach = (tmp_path / "msg" / "attach"
              / hashlib.md5(username.encode()).hexdigest() / "2026-01" / "Img")
    attach.mkdir(parents=True)
    (attach / f"{md5}.dat").write_bytes(dat)

    archive = MediaArchive(tmp_path / "out" / "media")
    resolver = MediaResolver(tmp_path, archive, {}, {}, image_key=KEY)
    media = resolver.resolve(Media(kind="image", md5=md5), username, 1)
    assert media.status == "ok"
    assert media.ext == ".jpg"
    saved = tmp_path / "out" / media.rel_path
    assert saved.read_bytes() == plain


def test_media_resolver_without_key_keeps_dat(tmp_path):
    import hashlib
    from wechat_export.cli import MediaResolver
    from wechat_export.exporter.media_archive import MediaArchive
    from wechat_export.message_model import Media

    username = "wxid_test"
    md5 = "b" * 32
    dat = make_v2(JPEG_HEAD + b"\x00" * 1008 + JPEG_TAIL, aes_size=1024)
    attach = (tmp_path / "msg" / "attach"
              / hashlib.md5(username.encode()).hexdigest() / "2026-01" / "Img")
    attach.mkdir(parents=True)
    (attach / f"{md5}.dat").write_bytes(dat)

    archive = MediaArchive(tmp_path / "out" / "media")
    resolver = MediaResolver(tmp_path, archive, {}, {}, image_key=None)
    media = resolver.resolve(Media(kind="image", md5=md5), username, 1)
    assert media.status == "ok"
    assert media.ext == ".dat"
    saved = tmp_path / "out" / media.rel_path
    assert saved.read_bytes() == dat


def test_extract_image_key_from_memory(tmp_path, monkeypatch):
    import ctypes
    import os
    import wechat_export.image_key as ik

    token = b"abcdefghijklmnopqrstuvwxyz012345"
    key = token[:16]
    plain = JPEG_HEAD + b"\x00" * 1008 + JPEG_TAIL
    dat = tmp_path / "sample.dat"
    dat.write_bytes(make_v2(plain, 1024, aes_key=key))

    buf = ctypes.create_string_buffer(b"\x00" * 32 + token + b"\x00" * 32)
    monkeypatch.setattr(ik, "find_weixin_pids", lambda: [os.getpid()])
    try:
        found = ik.extract_image_key([dat], deep=False)
    finally:
        del buf
    assert found == key


def test_scan_raw_worker(tmp_path):
    import ctypes
    import os
    import wechat_export.image_key as ik

    key = bytes(range(16))
    plain = JPEG_HEAD + b"\x00" * 1008 + JPEG_TAIL
    dat = tmp_path / "sample.dat"
    dat.write_bytes(make_v2(plain, 1024, aes_key=key))
    oracles = ik.collect_oracles([dat])

    buf = ctypes.create_string_buffer(b"\x00" * 16 + key + b"\x00" * 16)
    addr = ctypes.addressof(buf)
    result = ik._scan_raw_worker((os.getpid(), addr, len(buf), oracles))
    del buf
    assert result == key


def test_render_session_image_ext_whitelist(tmp_path):
    from wechat_export.exporter.html_renderer import HtmlRenderer
    from wechat_export.message_model import Media, Message, Session

    def msg(i, media):
        return Message(msg_id=str(i), ts=1700000000000 + i, type=3, type_name="图片",
                       direction="in",
                       sender={"wxid": "b", "name": "B", "is_self": False},
                       content="", media=media)

    msgs = [
        msg(1, Media(kind="image", ext=".jpg", rel_path="media/image/a.jpg")),
        msg(2, Media(kind="image", ext=".dat", rel_path="media/image/b.dat")),
        msg(3, Media(kind="image", ext=".wxgf", rel_path="media/image/c.wxgf")),
        msg(4, Media(kind="emoji", ext=".bin", rel_path="media/emoji/d.bin")),
    ]
    HtmlRenderer().render_session(tmp_path / "s", Session(id="s", name="S"),
                                  msgs, {"messages": 3})
    html = (tmp_path / "s" / "index.html").read_text(encoding="utf-8")
    assert '<img src="media/image/a.jpg"' in html
    assert '<img src="media/image/b.dat"' not in html
    assert '<img src="media/image/c.wxgf"' not in html
    assert 'href="media/image/b.dat"' in html
    assert 'href="media/image/c.wxgf"' in html
    assert 'href="media/emoji/d.bin"' in html
    assert "[表情]" in html
