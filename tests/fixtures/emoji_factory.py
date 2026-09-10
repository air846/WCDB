"""合成表情密文样本（AES-128-CBC / IV=key / PKCS7，与真机实测算法一致）。"""

from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util import Padding

BLOCK = 16
FIXTURES = Path(__file__).parent


def encrypt_emoji(plain: bytes, key: bytes) -> bytes:
    """按真机算法加密表情明文：CBC、IV=key、PKCS7。"""
    return AES.new(key, AES.MODE_CBC, key).encrypt(Padding.pad(plain, BLOCK))


def gif_bytes(body: bytes = b"\x00" * 64) -> bytes:
    return b"GIF89a" + body + b";"


def png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"IEND\xae\x42\x60\x82"


def jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32 + b"\xff\xd9"


def wxgf_bytes() -> bytes:
    """真样本（tests/fixtures/sample.wxgf），需要 av 才能转码。"""
    return (FIXTURES / "sample.wxgf").read_bytes()
