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


def test_message_display_and_appmsg_to_dict():
    from wechat_export.appmsg import parse_appmsg

    a = parse_appmsg('<msg><appmsg><title>示例标题</title><type>57</type>'
                     '<refermsg><type>1</type><displayname>示例昵称</displayname>'
                     '<content>示例引用内容</content></refermsg></appmsg></msg>')
    m = Message(
        msg_id="789", ts=1, type=49, type_name="应用/文件",
        direction="in", sender={"wxid": "wxid_b", "name": "李四", "is_self": False},
        content="<msg/>", appmsg=a, display=a.text(),
    )
    d = m.to_dict()
    assert d["appmsg"]["kind"] == "引用"
    assert d["appmsg"]["quote"]["content"] == "示例引用内容"
    assert "示例引用内容" in d["display"]


def test_media_filename_and_duration_to_dict():
    media = Media(kind="voice", md5="v1", ext=".wav", duration_ms=2300)
    d = media.to_dict()
    assert d["duration_ms"] == 2300
    assert d["filename"] == ""
    f = Media(kind="file", ext=".pdf", filename="报告.pdf")
    assert f.to_dict()["filename"] == "报告.pdf"
