from tests.fixtures import db_factory as f
from wechat_export.parser import (
    TYPE_NAMES, decode_local_type, media_file_id, parse_media_from_content,
    parse_message_row,
)
from wechat_export.schema import DEFAULT_SCHEMA

SELF = "wxid_self"
SENDER_MAP = {1: {"wxid": "wxid_b", "name": "李四"}, 2: {"wxid": SELF, "name": "我"}}


def _row(**kw):
    base = dict(local_id=1, local_type=1, real_sender_id=1,
                create_time=1700000000, message_content="hi",
                WCDB_CT_message_content=0, source="", WCDB_CT_source=0)
    base.update(kw)
    return base


def test_text_row_in():
    m = parse_message_row(_row(), DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.type_name == "文本"
    assert m.direction == "in"
    assert m.content == "hi"
    assert m.ts == 1700000000000  # 秒 -> 毫秒
    assert m.sender["is_self"] is False
    assert m.media is None
    assert m.raw is None


def test_text_row_out():
    m = parse_message_row(_row(real_sender_id=2), DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.direction == "out"
    assert m.sender["is_self"] is True
    assert m.sender["wxid"] == SELF


def test_combined_local_type_subtype():
    base, subtype = decode_local_type((57 << 32) | 49)
    assert (base, subtype) == (49, 57)
    m = parse_message_row(_row(local_type=(57 << 32) | 49), DEFAULT_SCHEMA, SELF,
                          "wxid_b", SENDER_MAP)
    assert m.type == 49
    assert m.raw == {"subtype": 57}


def test_zstd_content():
    blob, ct = f.compress("压缩内容")
    m = parse_message_row(_row(message_content=blob, WCDB_CT_message_content=ct),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.content == "压缩内容"


def test_parse_image_media():
    content = '<msg><img h="100" w="200" md5="abc123"/></msg>'
    media = parse_media_from_content(content, 3)
    assert media is not None
    assert media.kind == "image"
    assert media.md5 == "abc123"


def test_parse_emoji_media():
    content = '<msg><emoji md5="em123" /></msg>'
    media = parse_media_from_content(content, 47)
    assert media.kind == "emoji"
    assert media.md5 == "em123"


def test_parse_voice_media():
    content = '<msg><voicemsg voicelength="3000" /></msg>'
    media = parse_media_from_content(content, 34)
    assert media.kind == "voice"
    assert media.ext == ".amr"
    assert media.md5 == ""


def test_parse_file_media():
    content = ('<msg><appmsg><appattach><fileext>pdf</fileext>'
               '<md5>ff00</md5></appattach></appmsg></msg>')
    media = parse_media_from_content(content, 49)
    assert media.kind == "file"
    assert media.ext == ".pdf"
    assert media.md5 == "ff00"


def test_media_file_id_from_packed_info():
    packed = bytes.fromhex(
        "080210011a22222038323931663333393230303739306262363533613665626438336638633431375800")
    assert media_file_id(packed) == "8291f339200790bb653a6ebd83f8c417"
    assert media_file_id(None) == ""


def test_parse_image_media_uses_packed_file_id():
    packed = bytes.fromhex(
        "080210011a22222038323931663333393230303739306262363533613665626438336638633431375800")
    row = _row(local_type=3, message_content='<msg><img md5="xmlmd5"/></msg>',
               packed_info_data=packed)
    m = parse_message_row(row, DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.media.kind == "image"
    assert m.media.md5 == "8291f339200790bb653a6ebd83f8c417"


def test_parse_bad_content_none():
    assert parse_media_from_content("not xml at all", 1) is None


def test_unknown_type_fallback_raw():
    m = parse_message_row(_row(local_type=9999, message_content="???"),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.raw["local_type"] == 9999
    assert m.type_name.startswith("未知")
    assert TYPE_NAMES[1] == "文本"
