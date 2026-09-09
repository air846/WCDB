"""纯 Python SQLCipher 页加解密（pycryptodome）。

布局依据 SQLCipher 官方源码（src/sqlcipher.c 的 sqlite3Codec）核实：
- 原始 32 字节密钥（PRAGMA key = "x'...'")直接作为 AES-256 密钥，不经 PBKDF2
- 页 1：文件[0:16]为明文随机 salt；加密区为 [16, 页大小-保留区)；
  解密时首 16 字节由常量 SQLite 魔数替换（源码：memcpy(ctx->buffer, SQLITE_FILE_HEADER, 16)）
- 其他页：加密区为 [0, 页大小-保留区)
- 每页 IV = 该页保留区前 16 字节（明文存储）；HMAC 仅完整性，读取时忽略
- 页大小/保留区在文件头不可读（源码注释："the first page of the database is
  encrypted and thus sqlite can't effectively determine the pagesize"），
  因此用候选布局枚举 + 页 1 头部特征校验探测（find_layout）
- 错误密钥检测依赖解密后页 1 头部的确定性特征模式（页大小字段、版本字节、
  保留区=0、fraction 常量 0x40/0x20/0x20），而非被注入的魔数
"""

import os
from Crypto.Cipher import AES

SQLITE_MAGIC = b"SQLite format 3\x00"
IV_SIZE = 16
KEY_SIZE = 32
HEADER_SIZE = 16  # 页 1 明文 salt / 注入魔数区大小

PAGE_SIZE_CANDIDATES = [4096, 1024, 2048, 8192, 512, 65536]
RESERVED_CANDIDATES = [48, 16]  # 16(IV)+32(HMAC-SHA512) 或 16+20>36→48 取整；无 HMAC=16


class SqlcipherError(Exception):
    pass


def is_plaintext_sqlite(data: bytes) -> bool:
    # byte20 可为 0（普通库）或 16/48（SQLCipher 源库自带保留区），均为明文库
    return len(data) >= 32 and data[:16] == SQLITE_MAGIC and data[20] in (0,) + tuple(RESERVED_CANDIDATES)


def _parse_key(key_hex: str) -> bytes:
    raw = bytes.fromhex(key_hex)
    if len(raw) != KEY_SIZE:
        raise SqlcipherError(f"密钥必须为 {KEY_SIZE} 字节")
    return raw


def _decrypt_region(ct: bytes, key: bytes, iv: bytes) -> bytes:
    if len(ct) % 16 != 0:
        raise SqlcipherError("加密区长度不是 16 的倍数")
    return AES.new(key, AES.MODE_CBC, iv).decrypt(ct)


def decrypt_db(data: bytes, key_hex: str, page_size: int, reserved: int) -> bytes:
    key = _parse_key(key_hex)
    if len(data) % page_size != 0:
        raise SqlcipherError("文件大小不是页大小的整数倍")
    if reserved < IV_SIZE or (page_size - reserved) % 16 != 0:
        raise SqlcipherError("非法布局参数")
    out = bytearray()
    for off in range(0, len(data), page_size):
        page = data[off:off + page_size]
        iv = page[page_size - reserved:page_size - reserved + IV_SIZE]
        reserved_region = page[page_size - reserved:page_size]
        if off == 0:
            ct = page[HEADER_SIZE:page_size - reserved]
            out += SQLITE_MAGIC
            out += _decrypt_region(ct, key, iv)
        else:
            ct = page[:page_size - reserved]
            out += _decrypt_region(ct, key, iv)
        out += reserved_region  # 保留区原样回填，保持每页 page_size 字节对齐
    # 头部的每页保留区字节必须与 reserved 一致，否则 sqlite 会把保留字节当数据
    out[20] = reserved
    return bytes(out)


def encrypt_db(plain: bytes, key_hex: str, page_size: int = 4096,
               reserved: int = 48) -> bytes:
    """明文 SQLite 文件 → SQLCipher 加密文件（夹具库生成用）。"""
    key = _parse_key(key_hex)
    if len(plain) % page_size != 0:
        raise SqlcipherError("明文大小不是页大小的整数倍")
    if reserved < IV_SIZE or (page_size - reserved) % 16 != 0:
        raise SqlcipherError("非法布局参数")
    out = bytearray()
    for off in range(0, len(plain), page_size):
        page = plain[off:off + page_size]
        iv = os.urandom(IV_SIZE)
        if off == 0:
            salt = os.urandom(HEADER_SIZE)
            ct = AES.new(key, AES.MODE_CBC, iv).encrypt(page[HEADER_SIZE:page_size - reserved])
            out += salt + ct
        else:
            ct = AES.new(key, AES.MODE_CBC, iv).encrypt(page[:page_size - reserved])
            out += ct
        out += iv + os.urandom(reserved - IV_SIZE)
    return bytes(out)


def page1_header_ok(plain_page1: bytes, page_size: int, reserved: int = 0) -> bool:
    """解密后页 1 头部特征：页大小字段/版本字节/保留区/fraction 常量。"""
    if len(plain_page1) < 32:
        return False
    ps_field = 1 if page_size == 65536 else page_size
    if plain_page1[16:18] != ps_field.to_bytes(2, "big"):
        return False
    if plain_page1[18] not in (1, 2) or plain_page1[19] not in (1, 2):
        return False
    if plain_page1[20] != reserved:
        return False
    return plain_page1[21] == 0x40 and plain_page1[22] == 0x20 and plain_page1[23] == 0x20


def find_layout(data: bytes, key_hex: str) -> tuple[int, int] | None:
    """按候选顺序探测 (page_size, reserved)；4096×48（SQLCipher 4 默认）优先。"""
    key = _parse_key(key_hex)
    for page_size in PAGE_SIZE_CANDIDATES:
        if len(data) % page_size != 0:
            continue
        for reserved in RESERVED_CANDIDATES:
            if page_size - reserved <= HEADER_SIZE:
                continue
            page = data[:page_size]
            iv = page[page_size - reserved:page_size - reserved + IV_SIZE]
            try:
                plain = SQLITE_MAGIC + _decrypt_region(
                    page[HEADER_SIZE:page_size - reserved], key, iv)
            except SqlcipherError:
                continue
            if page1_header_ok(plain, page_size, reserved):
                return page_size, reserved
    return None
