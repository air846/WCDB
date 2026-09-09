"""只读访问微信 4.x 消息库：纯 Python SQLCipher 解密（内存）→ stdlib sqlite3。

解密后的明文只存在于内存（sqlite3.Connection.deserialize），不写盘。
"""

import sqlite3
from pathlib import Path

from wechat_export import sqlcipher as sc
from wechat_export.exceptions import DecryptError


def is_encrypted_db(db_path: Path) -> bool:
    with open(db_path, "rb") as fp:
        head = fp.read(100)
    return not sc.is_plaintext_sqlite(head)


class EncryptedDb:
    def __init__(self, db_path: Path, key_hex: str):
        if not db_path.exists():
            raise DecryptError(f"数据库文件不存在：{db_path}")
        if not is_encrypted_db(db_path):
            raise DecryptError(
                f"{db_path.name} 不是 SQLCipher 加密库",
                hint="请确认目标为微信 4.x 数据库",
            )
        data = db_path.read_bytes()
        try:
            layout = sc.find_layout(data, key_hex)
        except (sc.SqlcipherError, ValueError):  # 0 字节/短文件等无法探测，统一按密钥错误处理
            layout = None
        if layout is None:
            raise DecryptError(
                f"无法解密数据库 {db_path.name}（密钥可能错误或布局不受支持）",
                hint="请确认密钥正确，或改用 --key-hex 手动提供",
            )
        page_size, reserved = layout
        self.params = f"page_size={page_size};reserved={reserved}"
        plain = sc.decrypt_db(data, key_hex, page_size, reserved)
        self.conn = sqlite3.connect(":memory:")
        self.conn.deserialize(plain)
        self._plain = plain  # 保持引用，防止被 GC

    def tables(self) -> list[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r["name"] for r in rows]

    def columns(self, table: str) -> list[str]:
        rows = self.query(f'PRAGMA table_info("{table}")')
        return [r["name"] for r in rows]

    def query(self, sql: str, args=()) -> list[dict]:
        cur = self.conn.execute(sql, args)
        return [dict(zip([d[0] for d in cur.description], row))
                for row in cur.fetchall()]

    def close(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


def open_encrypted(db_path: Path, key_hex: str) -> EncryptedDb:
    return EncryptedDb(db_path, key_hex)