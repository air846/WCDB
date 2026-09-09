import pytest

from tests.fixtures import db_factory as f
from wechat_export.db_access import EncryptedDb, open_encrypted
from wechat_export.exceptions import DecryptError

KEY = f.KEY


@pytest.fixture
def encrypted_db(tmp_path):
    db = tmp_path / "message_0.db"
    f.create_encrypted_db(db, KEY)
    f.insert_message(db, key_hex=KEY, username="wxid_b", local_id=1,
                     local_type=1, real_sender_id=1, create_time=1700000000,
                     content="你好")
    return db


def test_open_and_query(encrypted_db):
    edb = EncryptedDb(encrypted_db, KEY)
    assert f.session_table("wxid_b") in edb.tables()
    rows = edb.query(f'SELECT message_content FROM "{f.session_table("wxid_b")}"')
    assert rows[0]["message_content"] == "你好"
    assert "page_size=4096" in edb.params
    assert "reserved=80" in edb.params
    edb.close()


def test_wrong_key_raises_decrypt_error(encrypted_db):
    with pytest.raises(DecryptError):
        open_encrypted(encrypted_db, "cd" * 32)


def test_columns(encrypted_db):
    edb = open_encrypted(encrypted_db, KEY)
    assert "message_content" in edb.columns(f.session_table("wxid_b"))
    edb.close()


def test_plain_sqlite_rejected(tmp_path):
    import sqlite3
    db = tmp_path / "plain.db"
    sqlite3.connect(str(db)).close()
    with pytest.raises(DecryptError):
        open_encrypted(db, KEY)
