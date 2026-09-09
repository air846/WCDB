"""合成 SQLCipher 加密夹具库生成器（镜像微信 4.1.13 真实结构）。

stdlib sqlite3 建明文（每会话 `Msg_<md5(username)>` 表 + `Name2Id` + `TimeStamp`）
→ `sqlcipher.encrypt_db` 加密。默认 reserved=80（与真实库一致）。
"""

import hashlib
import sqlite3
from pathlib import Path

import zstandard as zstd

from wechat_export import sqlcipher as sc

KEY = "ab" * 32
FIXTURE_PAGE_SIZE = 4096
FIXTURE_RESERVED = 80

MSG_COLUMNS = {
    "local_id": "INTEGER PRIMARY KEY",
    "server_id": "INTEGER",
    "local_type": "INTEGER",
    "sort_seq": "INTEGER",
    "real_sender_id": "INTEGER",
    "create_time": "INTEGER",
    "status": "INTEGER",
    "upload_status": "INTEGER",
    "download_status": "INTEGER",
    "server_seq": "INTEGER",
    "origin_source": "INTEGER",
    "source": "TEXT",
    "message_content": "TEXT",
    "compress_content": "TEXT",
    "packed_info_data": "BLOB",
    "WCDB_CT_message_content": "INTEGER",
    "WCDB_CT_source": "INTEGER",
}


def session_table(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def make_message_schema() -> dict:
    return {"table": session_table("wxid_b"), "columns": MSG_COLUMNS}


def compress(text: str) -> tuple[bytes, int]:
    return zstd.ZstdCompressor().compress(text.encode("utf-8")), 4


def _create_plain(path: Path, key_hex: str, page_size: int, reserved: int) -> None:
    seed = path.with_name(path.name + ".seed")
    conn = sqlite3.connect(str(seed))
    conn.execute(f"PRAGMA page_size = {page_size}")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()
    plain = bytearray(seed.read_bytes())
    plain[20] = reserved  # 镜像真实 SQLCipher 建库
    plain[105:107] = (page_size - reserved).to_bytes(2, "big")  # 页1 内容区起点
    seed.write_bytes(bytes(plain))
    conn = sqlite3.connect(str(seed))
    conn.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
    conn.execute("CREATE TABLE TimeStamp (timestamp INTEGER)")
    cols = ", ".join(f"{k} {v}" for k, v in MSG_COLUMNS.items())
    conn.execute(f'CREATE TABLE "{session_table("wxid_b")}" ({cols})')
    conn.commit()
    conn.close()
    raw = seed.read_bytes()
    seed.unlink()
    path.write_bytes(sc.encrypt_db(raw, key_hex, page_size, reserved))


def create_encrypted_db(path: Path, key_hex: str = KEY,
                        page_size: int = FIXTURE_PAGE_SIZE,
                        reserved: int = FIXTURE_RESERVED) -> None:
    _create_plain(path, key_hex, page_size, reserved)


def _with_plain(db_path: Path, key_hex: str, fn) -> None:
    layout = sc.find_layout(db_path.read_bytes(), key_hex)
    if layout is None:
        raise ValueError(f"无法解密 {db_path.name}（密钥可能错误）")
    page_size, reserved = layout
    salt = db_path.read_bytes()[:16]
    plain = sc.decrypt_db(db_path.read_bytes(), key_hex, page_size, reserved)
    tmp = db_path.with_name(db_path.name + ".plain")
    tmp.write_bytes(plain)
    conn = sqlite3.connect(str(tmp))
    try:
        fn(conn)
        conn.commit()
    finally:
        conn.close()
    enc = sc.encrypt_db(tmp.read_bytes(), key_hex, page_size, reserved, salt=salt)
    tmp.unlink()
    db_path.write_bytes(enc)


def add_name(db_path: Path, *, key_hex: str = KEY, rowid: int, user_name: str,
             is_session: int = 0) -> None:
    def fn(conn):
        conn.execute("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (?, ?, ?)",
                     (rowid, user_name, is_session))
    _with_plain(db_path, key_hex, fn)


def add_session(db_path: Path, *, key_hex: str = KEY, username: str = "wxid_b") -> None:
    cols = ", ".join(f"{k} {v}" for k, v in MSG_COLUMNS.items())

    def fn(conn):
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{session_table(username)}" ({cols})')
    _with_plain(db_path, key_hex, fn)


def insert_message(db_path: Path, *, key_hex: str = KEY, username: str = "wxid_b",
                   local_id: int = 1, local_type: int = 1, real_sender_id: int = 1,
                   create_time: int = 1700000000, content: str = "",
                   source: str = "", ct_content: int = 0, ct_source: int = 0) -> None:
    table = session_table(username)
    payload = content
    if ct_content == 4:
        payload, ct_content = compress(content)
    src = source
    if ct_source == 4:
        src, ct_source = compress(source)

    def fn(conn):
        conn.execute(
            f'INSERT INTO "{table}" (local_id, server_id, local_type, sort_seq,'
            " real_sender_id, create_time, status, upload_status, download_status,"
            " server_seq, origin_source, source, message_content, compress_content,"
            " packed_info_data, WCDB_CT_message_content, WCDB_CT_source)"
            " VALUES (?, 0, ?, 0, ?, ?, 3, 0, 0, 0, 2, ?, ?, NULL, NULL, ?, ?)",
            (local_id, local_type, real_sender_id, create_time, src, payload,
             ct_content, ct_source))
    _with_plain(db_path, key_hex, fn)
