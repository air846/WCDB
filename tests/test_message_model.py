from wechat_export.message_model import Contact, Media, Message, Session


def test_session_to_dict():
    s = Session(id="wxid_a", name="张三", chat_type="single", member_count=2)
    assert s.to_dict() == {
        "id": "wxid_a", "name": "张三", "chat_type": "single", "member_count": 2,
    }


def test_message_to_dict_with_media():
    m = Message(
        msg_id="123", ts=1700000000000, type=3, type_name="图片",
        direction="in", sender={"wxid": "wxid_b", "name": "李四", "is_self": False},
        content="图片消息",
        media=Media(kind="image", md5="abc", ext="jpg",
                    rel_path="media/image/abc.jpg"),
    )
    d = m.to_dict()
    assert d["media"]["kind"] == "image"
    assert d["media"]["rel_path"] == "media/image/abc.jpg"
    assert d["sender"]["is_self"] is False


def test_message_without_media_raw_roundtrip():
    m = Message(
        msg_id="456", ts=1, type=49, type_name="文件",
        direction="out", sender={"wxid": "self", "name": "我", "is_self": True},
        content="文件消息", raw={"k": "v"},
    )
    d = m.to_dict()
    assert d["raw"] == {"k": "v"}
    assert d["media"] is None
