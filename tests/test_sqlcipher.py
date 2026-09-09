import sqlite3

import pytest

from wechat_export import sqlcipher as sc

KEY = "ab" * 32


def _make_plain_sqlite(tmp_path, page_size=4096, reserved=80) -> bytes:
    """生成带保留区的明文 SQLite 库。

    SQLCipher 页格式中每页末尾 reserved 字节存放 IV+HMAC，因此源明文库
    必须自带等量保留区（byte20=reserved、数据只在 [0, P-R)）。python
    sqlite3 默认生成 byte20=0 的库（数据可打包到页尾，无法无损加密），
    故先建库再把 byte20 设为 reserved 并 VACUUM 重建，让 sqlite 把数据
    排布到 [0, P-R) 内、保留区置零。
    """
    p = tmp_path / "p.db"
    conn = sqlite3.connect(str(p))
    conn.execute(f"PRAGMA page_size={page_size}")
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.execute("INSERT INTO t VALUES ('你好')")
    conn.commit()
    conn.close()
    data = bytearray(p.read_bytes())
    data[20] = reserved
    p.write_bytes(data)
    conn = sqlite3.connect(str(p))
    conn.execute("VACUUM")
    conn.commit()
    conn.close()
    return p.read_bytes()


def _decrypt_and_open(db_bytes: bytes) -> sqlite3.Connection:
    page_size, reserved = sc.find_layout(db_bytes, KEY)
    assert page_size is not None
    plain = sc.decrypt_db(db_bytes, KEY, page_size, reserved)
    conn = sqlite3.connect(":memory:")
    conn.deserialize(plain)
    return conn


def test_roundtrip_default_layout(tmp_path):
    plain = _make_plain_sqlite(tmp_path)
    enc = sc.encrypt_db(plain, KEY)
    assert not sc.is_plaintext_sqlite(enc)
    conn = _decrypt_and_open(enc)
    assert conn.execute("SELECT a FROM t").fetchall() == [("你好",)]
    conn.close()


def test_roundtrip_nondefault_layout(tmp_path):
    plain = _make_plain_sqlite(tmp_path, page_size=1024, reserved=16)
    enc = sc.encrypt_db(plain, KEY, page_size=1024, reserved=16)
    page_size, reserved = sc.find_layout(enc, KEY)
    assert (page_size, reserved) == (1024, 16)
    plain2 = sc.decrypt_db(enc, KEY, page_size, reserved)
    assert plain2[:16] == sc.SQLITE_MAGIC


def test_wrong_key_layout_not_found(tmp_path):
    plain = _make_plain_sqlite(tmp_path)
    enc = sc.encrypt_db(plain, KEY)
    assert sc.find_layout(enc, "cd" * 32) is None


def test_plain_sqlite_detected(tmp_path):
    plain = _make_plain_sqlite(tmp_path)
    assert sc.is_plaintext_sqlite(plain)
    assert sc.find_layout(plain, KEY) is None  # 无保留区，无合法布局


def test_bad_key_length_raises(tmp_path):
    plain = _make_plain_sqlite(tmp_path)
    with pytest.raises(sc.SqlcipherError):
        sc.encrypt_db(plain, "a" * 10)
