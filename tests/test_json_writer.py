import json

from wechat_export.exporter.json_writer import (
    CHUNK_SIZE, write_export_meta, write_session_json, write_session_messages,
    write_sessions_index,
)
from wechat_export.message_model import Message, Session


def _msg(i: int) -> Message:
    return Message(msg_id=str(i), ts=i, type=1, type_name="文本",
                   direction="in",
                   sender={"wxid": "wxid_b", "name": "李四", "is_self": False},
                   content=f"msg{i}")


def test_write_session_messages_chunk(tmp_path):
    sess = Session(id="wxid_b", name="李四")
    files = write_session_messages(tmp_path, sess, [_msg(i) for i in range(CHUNK_SIZE + 10)])
    assert files == 2
    assert (tmp_path / "messages.json").exists()
    assert (tmp_path / "messages_0002.json").exists()
    data = json.loads((tmp_path / "messages.json").read_text(encoding="utf-8"))
    assert data["session"]["name"] == "李四"
    assert len(data["messages"]) == CHUNK_SIZE
    assert (tmp_path / ".done").exists()


def test_write_export_meta(tmp_path):
    write_export_meta(tmp_path, {"account": "wxid_abc", "stats": {}})
    meta = json.loads((tmp_path / "export_meta.json").read_text(encoding="utf-8"))
    assert meta["account"] == "wxid_abc"


def test_write_session_json(tmp_path):
    write_session_json(tmp_path, Session(id="x", name="群", chat_type="group"), {"messages": 5})
    d = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert d["name"] == "群" and d["stats"]["messages"] == 5


def test_write_sessions_index(tmp_path):
    write_sessions_index(tmp_path, [Session(id="a", name="A")], {"a": 3})
    d = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert d["sessions"][0]["name"] == "A"
