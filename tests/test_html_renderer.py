import re

from wechat_export.exporter.html_renderer import HtmlRenderer, group_by_day
from wechat_export.message_model import Media, Message, Session


def _msg(i: int, ts: int, type_: int = 1, content: str = "hi",
         media: Media | None = None) -> Message:
    return Message(msg_id=str(i), ts=ts, type=type_, type_name="文本",
                   direction="in" if i % 2 else "out",
                   sender={"wxid": "wxid_b", "name": "李四", "is_self": i % 2 == 0},
                   content=content, media=media)


def test_group_by_day():
    msgs = [_msg(1, 1700000000000), _msg(2, 1700000000000 + 86400000)]
    days = group_by_day(msgs)
    assert len(days) == 2
    assert days[0][0] != days[1][0]


def test_render_index_and_session(tmp_path):
    renderer = HtmlRenderer()
    sessions = [Session(id="wxid_b", name="李四"), Session(id="g1", name="群聊", chat_type="group")]
    renderer.render_index(tmp_path, sessions, {"wxid_b": 3, "g1": 5}, {"account": "wxid_abc"})
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "李四" in html and "wxid_abc" in html
    assert "http://" not in html and "https://" not in html  # 零 CDN

    out = tmp_path / "wxid_b"
    msgs = [
        _msg(1, 1700000000000, 3, "图片", Media(kind="image", rel_path="media/image/a.jpg")),
        _msg(2, 1700001000000, 34, "语音", Media(kind="voice", rel_path="media/voice/a.amr")),
    ]
    renderer.render_session(out, sessions[0], msgs, {"messages": 2})
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "media/image/a.jpg" in html
    assert '<audio' in html
