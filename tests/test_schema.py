import hashlib

from tests.fixtures import db_factory as f
from wechat_export.db_access import open_encrypted
from wechat_export.schema import (
    DEFAULT_SCHEMA, SchemaInfo, is_session_table, load_schema,
    session_table_name, snapshot_db,
)

KEY = "e" * 64


def test_default_schema_fields():
    assert DEFAULT_SCHEMA.message_table_prefix == "Msg_"
    assert DEFAULT_SCHEMA.content == "message_content"
    assert DEFAULT_SCHEMA.type == "local_type"
    assert DEFAULT_SCHEMA.sender_id == "real_sender_id"
    assert DEFAULT_SCHEMA.create_time == "create_time"


def test_session_table_name():
    assert session_table_name("wxid_b") == "Msg_" + hashlib.md5(b"wxid_b").hexdigest()
    assert is_session_table(session_table_name("wxid_b"))
    assert not is_session_table("Name2Id")
    assert not is_session_table("Msg_nothex")


def test_load_schema_with_snapshot():
    info = load_schema({"table_prefix": "Msg_", "msg_id": "local_id",
                        "columns": {"Msg_" + "a" * 32: ["local_id", "message_content"]}})
    assert info.msg_id == "local_id"
    assert info.content == "message_content"
    assert info.columns == ["local_id", "message_content"]


def test_snapshot_db(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY)
    f.insert_message(db, key_hex=KEY, username="wxid_b", local_id=1, local_type=1,
                     real_sender_id=1, create_time=1700000000, content="x")
    edb = open_encrypted(db, KEY)
    snap = snapshot_db(edb)
    edb.close()
    assert f.session_table("wxid_b") in snap["tables"]
    assert "message_content" in snap["columns"][f.session_table("wxid_b")]
    assert isinstance(DEFAULT_SCHEMA, SchemaInfo)
