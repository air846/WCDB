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


def test_parse_file_media_from_appmsg_level_md5():
    content = ('<msg><appmsg><title>报告.pdf</title><type>6</type>'
               '<appattach><totallen>540823</totallen><fileext>pdf</fileext></appattach>'
               '<md5>d2aed1eed118275147de85dfe21c668c</md5></appmsg></msg>')
    media = parse_media_from_content(content, 49)
    assert media.kind == "file"
    assert media.ext == ".pdf"
    assert media.md5 == "d2aed1eed118275147de85dfe21c668c"
    assert media.filename == "报告.pdf"
    assert media.size == 540823


def test_quote_appmsg_has_no_file_media():
    content = ('<msg><appmsg><title>示例标题</title><type>57</type>'
               '<refermsg><type>1</type><content>示例引用内容</content></refermsg>'
               '<appattach><totallen>0</totallen><fileext /></appattach></appmsg></msg>')
    assert parse_media_from_content(content, 49) is None


def test_link_appmsg_md5_is_not_file_media():
    content = ('<msg><appmsg><title>看电影</title><type>5</type>'
               '<md5>c82b648252d96cf53081f4a499306f21</md5></appmsg></msg>')
    assert parse_media_from_content(content, 49) is None


def test_pat_appmsg_garbage_fileext_is_not_file_media():
    content = ('<msg><appmsg><title>拍了拍我</title><type>62</type>'
               '<appattach><fileext>.18）" 拍了拍我</fileext></appattach></appmsg></msg>')
    assert parse_media_from_content(content, 49) is None


def test_parse_video_media_without_md5():
    content = '<msg><videomsg aeskey="aa" cdnvideourl="bb" /></msg>'
    media = parse_media_from_content(content, 43)
    assert media.kind == "video"
    assert media.ext == ".mp4"
    assert media.md5 == ""


def test_video_media_uses_packed_file_id():
    packed = bytes.fromhex(
        "080210011a22222038323931663333393230303739306262363533613665626438336638633431375800")
    row = _row(local_type=43, message_content='<msg><videomsg aeskey="aa"/></msg>',
               packed_info_data=packed)
    m = parse_message_row(row, DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.media.kind == "video"
    assert m.media.md5 == "8291f339200790bb653a6ebd83f8c417"


def test_missing_media_displays_friendly_placeholder():
    cases = [
        (3, '<msg><img md5="x"/></msg>', "[图片]"),
        (47, '<msg><emoji md5="x"/></msg>', "[表情]"),
        (43, '<msg><videomsg aeskey="a"/></msg>', "[视频]"),
        (34, '<msg><voicemsg voicelength="2300"/></msg>', "[语音 2.3″]"),
    ]
    for base_type, content, expected in cases:
        m = parse_message_row(_row(local_type=base_type, message_content=content),
                              DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
        assert m.display == expected, base_type


def test_parse_bad_content_none():
    assert parse_media_from_content("not xml at all", 1) is None


def test_appmsg_quote_populates_summary():
    content = ('<msg><appmsg><title>示例标题</title><type>57</type>'
               '<refermsg><type>1</type><displayname>示例昵称</displayname>'
               '<content>示例引用内容</content><createtime>1700000000</createtime>'
               '</refermsg></appmsg></msg>')
    m = parse_message_row(_row(local_type=(57 << 32) | 49, message_content=content),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.appmsg.kind == "引用"
    assert m.content == content  # 原始 XML 保留在 content
    assert m.raw == {"subtype": 57}
    assert "示例引用内容" in m.display
    assert "<msg" not in m.display


def test_location_message_display():
    content = ('<msg><location x="22.99" y="113.12" label="禅城区港口东街"'
               ' poiname="龙光·君悦华府" /></msg>')
    m = parse_message_row(_row(local_type=48, message_content=content),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.display == "[位置] 禅城区港口东街"
    assert m.content == content


def test_voip_message_display():
    content = ('<voipmsg type="VoIPBubbleMsg"><VoIPBubbleMsg>'
               '<msg><![CDATA[通话时长 10:00]]></msg></VoIPBubbleMsg></voipmsg>')
    m = parse_message_row(_row(local_type=50, message_content=content),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.display == "[通话] 通话时长 10:00"


def test_revoke_sysmsg_display():
    content = ('<?xml version="1.0"?><sysmsg type="revokemsg"><revokemsg>'
               '<content>"示例好友" 撤回了一条消息</content>'
               '<revoketime>0</revoketime></revokemsg></sysmsg>')
    m = parse_message_row(_row(local_type=10000, message_content=content),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.display == '"示例好友" 撤回了一条消息'


def test_redpacket_sysmsg_display_strips_markup():
    content = ('<img src="SystemMessages_HongbaoIcon.png"/>  示例好友领取了你的'
               '<_wc_custom_link_ color="#FD9931" href="weixin://x">红包</_wc_custom_link_>')
    m = parse_message_row(_row(local_type=10000, message_content=content),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.display == "示例好友领取了你的红包"
    assert "<" not in m.display


def test_sysmsgtemplate_plain_display():
    content = ('<sysmsg type="sysmsgtemplate"><sysmsgtemplate><content_template '
               'type="t"><plain><![CDATA[你收下了雨曦的礼物]]></plain>'
               '</content_template></sysmsgtemplate></sysmsg>')
    m = parse_message_row(_row(local_type=10000, message_content=content),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.display == "你收下了雨曦的礼物"


def test_paymsg_sysmsg_display():
    content = ('<?xml version="1.0"?>\n<sysmsg type="paymsg"><content>'
               '<![CDATA[收款方24小时内未接收你的<_wc_custom_link_ '
               'href="weixin://wxpay/x">转账</_wc_custom_link_>，已过期]]>'
               '</content></sysmsg>')
    m = parse_message_row(_row(local_type=10000, message_content=content),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.display == "收款方24小时内未接收你的转账，已过期"
    assert "<" not in m.display


def test_plain_sysmsg_display_unchanged():
    m = parse_message_row(_row(local_type=10000, message_content="你已添加了雨曦"),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.display == "你已添加了雨曦"


def test_unknown_type_fallback_raw():
    m = parse_message_row(_row(local_type=9999, message_content="???"),
                          DEFAULT_SCHEMA, SELF, "wxid_b", SENDER_MAP)
    assert m.raw["local_type"] == 9999
    assert m.type_name.startswith("未知")
    assert TYPE_NAMES[1] == "文本"
