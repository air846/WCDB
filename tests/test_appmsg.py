"""type 49 应用消息（appmsg XML）→ 人类可读摘要。"""

from wechat_export.appmsg import AppMsg, parse_appmsg

QUOTE_XML = """<?xml version="1.0"?>
<msg>
  <appmsg appid="" sdkver="0">
    <title>示例标题</title>
    <type>57</type>
    <refermsg>
      <type>1</type>
      <svrid>1234567890123456789</svrid>
      <fromusr>wxid_example00001</fromusr>
      <displayname>示例昵称</displayname>
      <content>示例引用内容</content>
      <createtime>1700000000</createtime>
    </refermsg>
  </appmsg>
</msg>"""

LINK_XML = ('<msg><appmsg><title>看电影 &#x1F929;</title><des>一起来看电影</des>'
            '<type>5</type><url>http://example.com/film</url></appmsg></msg>')

FILE_XML = ('<msg><appmsg><title>报告.pdf</title><type>6</type>'
            '<appattach><totallen>540823</totallen><fileext>pdf</fileext></appattach>'
            '<md5>d2aed1eed118275147de85dfe21c668c</md5></appmsg></msg>')

TRANSFER_XML = ('<msg><appmsg><title><![CDATA[微信转账]]></title>'
                '<des><![CDATA[收到转账50.00元]]></des><type>2000</type>'
                '<wcpayinfo><paysubtype>1</paysubtype>'
                '<feedesc><![CDATA[￥50.00]]></feedesc>'
                '<pay_memo><![CDATA[午饭]]></pay_memo></wcpayinfo></appmsg></msg>')

REDPACKET_XML = ('<msg><appmsg><type>2001</type>'
                 '<title><![CDATA[微信红包]]></title>'
                 '<des><![CDATA[我给你发了一个红包，赶紧去拆!]]></des>'
                 '<wcpayinfo><sendertitle><![CDATA[恭喜发财，大吉大利]]></sendertitle>'
                 '</wcpayinfo></appmsg></msg>')

HISTORY_XML = ('<msg><appmsg><title>张三与李四的聊天记录</title>'
               '<des>张三: hi</des><type>19</type>'
               '<recorditem><![CDATA[<recordinfo><datalist count="25">'
               '</datalist></recordinfo>]]></recorditem></appmsg></msg>')

MINIPROGRAM_XML = ('<msg><appmsg><title>示例小程序分享</title><type>36</type>'
                   '<sourcedisplayname>王者荣耀</sourcedisplayname></appmsg></msg>')

PAT_XML = '<msg><appmsg><title>"示例好友" 拍了拍我</title><type>62</type></appmsg></msg>'

QUOTE_IMAGE_XML = ('<msg><appmsg><title>好看</title><type>57</type>'
                   '<refermsg><type>3</type><displayname>张三</displayname>'
                   '<content><![CDATA[<msg><img md5="x"/></msg>]]></content>'
                   '</refermsg></appmsg></msg>')


def test_parse_quote():
    a = parse_appmsg(QUOTE_XML)
    assert a.type == 57
    assert a.kind == "引用"
    assert a.title == "示例标题"
    assert a.quote == {"name": "示例昵称", "content": "示例引用内容", "ts": 1700000000000}
    text = a.text()
    assert "示例引用内容" in text and "示例标题" in text
    assert "<msg" not in text and "<?xml" not in text


def test_parse_link():
    a = parse_appmsg(LINK_XML)
    assert a.kind == "链接"
    assert a.title == "看电影 🤩"  # XML 实体解码
    assert a.desc == "一起来看电影"
    assert a.url == "http://example.com/film"


def test_parse_file():
    a = parse_appmsg(FILE_XML)
    assert a.kind == "文件"
    assert a.filename == "报告.pdf"
    assert a.ext == "pdf"
    assert a.size == 540823
    assert a.md5 == "d2aed1eed118275147de85dfe21c668c"


def test_parse_transfer():
    a = parse_appmsg(TRANSFER_XML)
    assert a.kind == "转账"
    assert "￥50.00" in a.text()
    assert "午饭" in a.text()


def test_parse_redpacket():
    a = parse_appmsg(REDPACKET_XML)
    assert a.kind == "红包"
    assert "微信红包" in a.text()
    assert "红包" in a.text()


def test_parse_chat_history():
    a = parse_appmsg(HISTORY_XML)
    assert a.kind == "聊天记录"
    assert a.count == 25
    assert "张三与李四的聊天记录" in a.text()
    assert "25" in a.text()


def test_parse_miniprogram():
    a = parse_appmsg(MINIPROGRAM_XML)
    assert a.kind == "小程序"
    assert a.source == "王者荣耀"
    assert "示例小程序分享" in a.text()


def test_parse_pat():
    a = parse_appmsg(PAT_XML)
    assert a.kind == "拍一拍"
    assert "拍了拍我" in a.text()


def test_quote_non_text_content_not_xml():
    a = parse_appmsg(QUOTE_IMAGE_XML)
    assert a.quote["content"] == "[图片]"
    assert "<msg" not in a.text()


def test_unknown_type_falls_back_without_xml():
    a = parse_appmsg('<msg><appmsg><title>T</title><des>D</des>'
                     '<type>999</type></appmsg></msg>')
    assert a.kind == "应用消息"
    assert "T" in a.text() and "D" in a.text()
    assert "<" not in a.text()


def test_invalid_xml_returns_none():
    assert parse_appmsg("not xml at all") is None
    assert parse_appmsg("") is None
    assert parse_appmsg(None) is None


def test_to_dict_roundtrip():
    a = parse_appmsg(QUOTE_XML)
    d = a.to_dict()
    assert d["kind"] == "引用"
    assert d["quote"]["content"] == "示例引用内容"
    assert isinstance(AppMsg(**{k: v for k, v in d.items()
                                if k in AppMsg.__dataclass_fields__}), AppMsg)
