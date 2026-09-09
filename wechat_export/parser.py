"""message_N.db 行 → 统一消息模型（M0 实测的 4.1.13 结构）。

- `local_type = (subtype << 32) | base_type`
- `create_time` 为秒级
- `source`/`message_content` 可能 zstd 压缩（`WCDB_CT_*` = 4）
- `real_sender_id` → `Name2Id.rowid` → user_name
"""

import re
import xml.etree.ElementTree as ET

from wechat_export.message_model import Media, Message
from wechat_export.schema import CT_ZSTD, SchemaInfo

TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "表情",
    49: "应用/文件", 50: "通话", 10000: "系统消息",
}

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

_KIND_EXT = {
    3: ("image", ".jpg"),
    34: ("voice", ".amr"),
    43: ("video", ".mp4"),
    47: ("emoji", ".gif"),
    49: ("file", ".dat"),
}


def decode_local_type(local_type: int) -> tuple[int, int]:
    """返回 (base_type, subtype)。"""
    value = int(local_type or 0)
    return value & 0xFFFFFFFF, value >> 32


def decompress(data, ct: int = 0) -> bytes:
    if data is None:
        return b""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if data[:4] == _ZSTD_MAGIC or int(ct or 0) == CT_ZSTD:
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(data, max_output_size=64 << 20)
        except Exception:
            return data
    return data


def to_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def resolve_kind_and_ext(base_type: int) -> tuple[str, str] | None:
    return _KIND_EXT.get(base_type)


def _safe_xml(content: str) -> ET.Element | None:
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None


def _find(root: ET.Element, *tags: str) -> ET.Element | None:
    for tag in tags:
        el = root.find(tag)
        if el is not None:
            return el
    return None


def parse_media_from_content(content: str, base_type: int) -> Media | None:
    kind_ext = resolve_kind_and_ext(base_type)
    if not kind_ext or not content:
        return None
    kind, ext = kind_ext
    root = _safe_xml(content)
    if root is None:
        return None
    md5 = ""
    size = 0
    if kind == "image":
        el = _find(root, "img", ".//img")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = "." + (el.get("type", "") or "jpg") if el.get("type", "").isalpha() else ".jpg"
    elif kind == "emoji":
        el = _find(root, "emoji", ".//emoji")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = ".gif"
    elif kind == "voice":
        el = _find(root, "voicemsg", ".//voicemsg")
        if el is None:
            return None
        size = int(el.get("voicelength", 0) or 0)
        ext = ".amr"
    elif kind == "video":
        el = _find(root, "videomsg", ".//videomsg")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = ".mp4"
    elif kind == "file":
        el = _find(root, ".//appattach", ".//appmsg/appattach")
        if el is None:
            return None
        md5 = el.get("md5", "") or (el.findtext("md5") or "")
        ext = "." + ((el.get("fileext", "") or el.findtext("fileext") or "dat").lstrip("."))
    if not md5 and kind != "voice":
        return None
    return Media(kind=kind, md5=md5, size=size, ext=ext)


def media_file_id(packed_info) -> str:
    """4.x 磁盘文件名：packed_info_data 内嵌的 32 位 hex（非 XML 的 md5）。"""
    if isinstance(packed_info, (bytes, bytearray)):
        m = re.search(rb"[0-9a-f]{32}", packed_info)
        return m.group(0).decode("ascii") if m else ""
    return ""


def parse_message_row(row: dict, schema: SchemaInfo, self_wxid: str, session_id: str,
                      sender_map: dict | None = None) -> Message:
    sender_map = sender_map or {}
    base_type, subtype = decode_local_type(row.get(schema.type, 0))
    local_id = row.get(schema.msg_id)
    ts = int(row.get(schema.create_time, 0) or 0) * 1000  # 秒 → 毫秒
    sender_id = row.get(schema.sender_id)
    info = sender_map.get(sender_id, {})
    sender_wxid = info.get("wxid", "") or f"id_{sender_id}"
    is_self = sender_wxid == self_wxid
    content = to_text(decompress(row.get(schema.content),
                                 int(row.get(schema.content_ct, 0) or 0)))
    media = parse_media_from_content(content, base_type)
    if media is not None:
        file_id = media_file_id(row.get("packed_info_data"))
        if file_id:
            media.md5 = file_id
    type_name = TYPE_NAMES.get(base_type, f"未知({base_type})")
    raw = None
    if base_type not in TYPE_NAMES:
        raw = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in row.items()}
    elif subtype:
        raw = {"subtype": subtype}
    return Message(
        msg_id=str(local_id), ts=ts, type=base_type, type_name=type_name,
        direction="out" if is_self else "in",
        sender={"wxid": sender_wxid,
                "name": info.get("name") or sender_wxid,
                "is_self": is_self},
        content=content, media=media, raw=raw,
    )
