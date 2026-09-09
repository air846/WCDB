from wechat_export.message_model import Media
from wechat_export.parser import (
    TYPE_NAMES, parse_media_from_content, parse_message_row,
)
from wechat_export.schema import DEFAULT_SCHEMA

SELF = "wxid_self"


def _row(**kw):
    base = dict(id=1, talker="wxid_b", type=1, subtype=0, content="hi",
                createTime=1700000000000, isSender=0, status=0)
    base.update(kw)
    return base


def test_text_row_in():
    m = parse_message_row(_row(), DEFAULT_SCHEMA, SELF, "wxid_b")
    assert m.type_name == "文本"
    assert m.direction == "in"
    assert m.content == "hi"
    assert m.sender["is_self"] is False
    assert m.media is None


def test_text_row_out():
    m = parse_message_row(_row(isSender=1), DEFAULT_SCHEMA, SELF, "wxid_b")
    assert m.direction == "out"
    assert m.sender["is_self"] is True
    assert m.sender["wxid"] == SELF


def test_unknown_type_fallback_raw():
    m = parse_message_row(_row(type=9999, content="???"), DEFAULT_SCHEMA, SELF, "wxid_b")
    assert m.raw["type"] == 9999
    assert m.type_name.startswith("未知")


def test_parse_image_media():
    content = '<msg><img h="100" w="200" md5="abc123"/></msg>'
    media = parse_media_from_content(content, 3)
    assert media is not None
    assert media.kind == "image"
    assert media.md5 == "abc123"


def test_parse_voice_media():
    content = '<msg><voicemsg voicelength="3000" /></msg>'
    media = parse_media_from_content(content, 34)
    assert media.kind == "voice"
    assert media.ext == ".amr"
    assert media.md5 == ""


def test_parse_bad_content_none():
    assert parse_media_from_content("not xml at all", 1) is None
