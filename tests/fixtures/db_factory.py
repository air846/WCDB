"""合成 SQLCipher 加密夹具库生成器：stdlib sqlite3 建明文 → sqlcipher.encrypt_db 加密。"""

import sqlite3
from pathlib import Path

from wechat_export import sqlcipher as sc

KEY = "ab" * 32
FIXTURE_PAGE_SIZE = 4096
FIXTURE_RESERVED = 48

MSG_COLUMNS = {
    "id": "INTEGER PRIMARY KEY",
    "talker": "TEXT",
    "type": "INTEGER",
    "subtype": "INTEGER",
    "content": "TEXT",
    "createTime": "INTEGER",
    "isSender": "INTEGER",
    "status": "INTEGER",
}


def make_message_schema() -> dict:
    return {"table": "message", "columns": MSG_COLUMNS}


def create_encrypted_db(path: Path, key_hex: str = KEY,
                        page_size: int = FIXTURE_PAGE_SIZE,
                        reserved: int = FIXTURE_RESERVED) -> None:
    # 两阶段建库，镜像真实 SQLCipher 建库流程：
    # 1) 空库（页1 空 btree，content area 起点=page_size）→ 置 byte20=reserved，
    #    并把页1 btree 内容区起点对齐到 usable=page_size-reserved；
    # 2) 再建表 —— 让 SQLite 以保留区=reserved 的布局写入 schema，
    #    避免 schema 行落在保留区（[page_size-reserved, page_size)）内。
    seed = path.with_name(path.name + ".seed")
    conn = sqlite3.connect(str(seed))
    conn.execute(f"PRAGMA page_size = {page_size}")
    conn.execute("PRAGMA user_version = 0")  # 触发页1 写入（空 btree）
    conn.commit()
    conn.close()
    plain = bytearray(seed.read_bytes())
    plain[20] = reserved  # 镜像真实 SQLCipher 建库：头部保留区字节=reserved（页含保留区）
    plain[105:107] = (page_size - reserved).to_bytes(2, "big")  # 页1 btree 内容区起点 -> usable
    seed.write_bytes(bytes(plain))
    conn = sqlite3.connect(str(seed))
    cols = ", ".join(f"{k} {v}" for k, v in MSG_COLUMNS.items())
    conn.execute(f"CREATE TABLE message ({cols})")
    conn.commit()
    conn.close()
    raw = seed.read_bytes()
    seed.unlink()
    path.write_bytes(sc.encrypt_db(raw, key_hex, page_size, reserved))


def insert_message(db_path: Path, *, key_hex: str = KEY, msg_id: int, ts: int,
                   type_: int, content: str, is_sender: int, talker: str,
                   subtype: int = 0, status: int = 0) -> None:
    layout = sc.find_layout(db_path.read_bytes(), key_hex)
    if layout is None:
        raise ValueError(f"无法解密 {db_path.name}（密钥可能错误）")
    page_size, reserved = layout
    plain = sc.decrypt_db(db_path.read_bytes(), key_hex, page_size, reserved)
    tmp = db_path.with_name(db_path.name + ".plain")
    tmp.write_bytes(plain)
    conn = sqlite3.connect(str(tmp))
    conn.execute(
        "INSERT INTO message (id, talker, type, subtype, content, createTime, isSender, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, talker, type_, subtype, content, ts, is_sender, status),
    )
    conn.commit()
    conn.close()
    enc = sc.encrypt_db(tmp.read_bytes(), key_hex, page_size, reserved)
    tmp.unlink()
    db_path.write_bytes(enc)