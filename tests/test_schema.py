from wechat_export.schema import DEFAULT_SCHEMA, SchemaInfo, load_schema, snapshot_db
from tests.fixtures import db_factory as f
from wechat_export.db_access import open_encrypted

KEY = "e" * 64


def test_default_schema_fields():
    assert DEFAULT_SCHEMA.message_table == "message"
    assert DEFAULT_SCHEMA.content == "content"
    assert DEFAULT_SCHEMA.is_sender == "isSender"


def test_load_schema_with_snapshot():
    info = load_schema({"table": "message", "msg_id": "IdNew"})
    assert info.msg_id == "IdNew"
    assert info.is_sender == "isSender"  # 未提供字段回落默认


def test_snapshot_db(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY)
    f.insert_message(db, key_hex=KEY, msg_id=1, ts=1, type_=1, content="x",
                     is_sender=0, talker="t")
    edb = open_encrypted(db, KEY)
    snap = snapshot_db(edb)
    edb.close()
    assert "message" in snap["tables"]
    assert "content" in snap["columns"]["message"]
    assert isinstance(DEFAULT_SCHEMA, SchemaInfo)
