"""DB 原始行 → 统一消息模型。

媒体二进制定位（库内 blob / FileStorage 文件）依赖 M0 实测结果，
本阶段先从 content 的 XML 描述中解析出 md5/时长/文件名等元数据；
实际媒体归档在 exporter 层由 media resolver 填补（见 Task 10 接口）。
"""

import re
import xml.etree.ElementTree as ET

from wechat_export.message_model import Media, Message
from wechat_export.schema import SchemaInfo

TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频",
    49: "文件/卡片", 10000: "系统消息",
}

_MEDIA_EXT = {"image": ".jpg", "video": ".mp4", "voice": ".amr", "file": ".dat"}


def resolve_kind_and_ext(type_: int) -> tuple[str, str] | None:
    kind_map = {3: "image", 34: "voice", 43: "video", 49: "file"}
    kind = kind_map.get(type_)
    if not kind:
        return None
    return kind, _MEDIA_EXT[kind]


def _safe_xml(content: str) -> ET.Element | None:
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None


def parse_media_from_content(content: str, type_: int) -> Media | None:
    kind_ext = resolve_kind_and_ext(type_)
    if not kind_ext:
        return None
    kind, ext = kind_ext
    root = _safe_xml(content)
    if root is None:
        return None
    md5 = ""
    size = 0
    if kind == "image":
        el = root.find("img")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = ".jpg"
    elif kind == "voice":
        el = root.find("voicemsg")
        if el is None:
            return None
        size = int(el.get("voicelength", 0) or 0)
        ext = ".amr"
    elif kind == "video":
        el = root.find("videomsg")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = ".mp4"
    elif kind == "file":
        el = root.find(".//appattach")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = "." + (el.get("fileext", "dat") or "dat")
    if not md5 and kind != "voice":
        return None
    return Media(kind=kind, md5=md5, size=size, ext=ext)


def parse_message_row(row: dict, schema: SchemaInfo, self_wxid: str, session_id: str) -> Message:
    is_sender = int(row.get(schema.is_sender, 0) or 0) == 1
    msg_type = int(row.get(schema.type, 0) or 0)
    content = str(row.get(schema.content, "") or "")
    msg_id = str(row.get(schema.msg_id) or f"{session_id}-{row.get(schema.create_time)}")
    ts = int(row.get(schema.create_time, 0) or 0)
    talker = str(row.get(schema.talker, "") or session_id)

    sender = {
        "wxid": self_wxid if is_sender else talker,
        "name": "我" if is_sender else talker,
        "is_self": is_sender,
    }
    media = parse_media_from_content(content, msg_type)
    type_name = TYPE_NAMES.get(msg_type, f"未知({msg_type})")
    raw = None if type_name in TYPE_NAMES else dict(row)
    return Message(
        msg_id=msg_id, ts=ts, type=msg_type, type_name=type_name,
        direction="out" if is_sender else "in",
        sender=sender, content=content or "",
        media=media, raw=raw,
    )
