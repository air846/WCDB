"""message_N.db 行 → 统一消息模型（M0 实测的 4.1.13 结构）。

- `local_type = (subtype << 32) | base_type`
- `create_time` 为秒级
- `source`/`message_content` 可能 zstd 压缩（`WCDB_CT_*` = 4）
- `real_sender_id` → `Name2Id.rowid` → user_name
"""

import re
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path

from wechat_export.appmsg import parse_appmsg
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


def _app_element(root: ET.Element) -> ET.Element:
    """返回 appmsg 元素；根即 appmsg 时返回根。"""
    if root.tag == "appmsg":
        return root
    app = root.find("appmsg")
    return app if app is not None else root


def _int_attr(value) -> int:
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


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
        app = _app_element(root)
        app_type = _int_attr(app.findtext("type"))
        attach = app.find("appattach")
        filename = (app.findtext("title") or "").strip()
        fileext = ""
        if attach is not None:
            fileext = (attach.get("fileext") or attach.findtext("fileext") or "").strip()
            md5 = (attach.get("md5") or attach.findtext("md5") or "").strip()
            size = _int_attr(attach.findtext("totallen"))
        md5 = md5 or (app.findtext("md5") or "").strip()
        # 仅 type=6（或旧库无 type 但带文件特征）视为文件；引用/链接等 appmsg
        # 也可能带 <md5>（缩略图）或异常 <fileext>（如拍一拍）。
        if app_type != 6 and not (app_type == 0 and (fileext or md5)):
            return None
        ext = "." + fileext.lstrip(".") if fileext else (Path(filename).suffix or ".dat")
        return Media(kind=kind, md5=md5, size=size, ext=ext, filename=filename)
    if not md5 and kind not in ("voice", "video"):
        return None
    duration_ms = size if kind == "voice" else 0
    return Media(kind=kind, md5=md5, size=size if kind != "voice" else 0,
                 ext=ext, duration_ms=duration_ms)


def media_file_id(packed_info) -> str:
    """4.x 磁盘文件名：packed_info_data 内嵌的 32 位 hex（非 XML 的 md5）。"""
    if isinstance(packed_info, (bytes, bytearray)):
        m = re.search(rb"[0-9a-f]{32}", packed_info)
        return m.group(0).decode("ascii") if m else ""
    return ""


_IMG_TAG_RE = re.compile(r"<img[^>]*>")
_WC_LINK_RE = re.compile(r"</?_wc_custom_link_[^>]*>")
_WS_RE = re.compile(r"\s+")


def _strip_markup(text: str) -> str:
    text = _IMG_TAG_RE.sub("", text)
    text = _WC_LINK_RE.sub("", text)
    return _WS_RE.sub(" ", unescape(text)).strip()


def _sysmsg_display(content: str) -> str:
    """系统消息（type=10000）：撤回/模板消息取纯文本，其余去标签。"""
    root = _safe_xml(content)
    if root is not None and root.tag == "sysmsg":
        if root.get("type", "") == "revokemsg":
            el = root.find(".//revokemsg/content")
            if el is not None and (el.text or "").strip():
                return el.text.strip()
        plain = root.find(".//sysmsgtemplate//plain")
        if plain is not None and (plain.text or "").strip():
            return plain.text.strip()
        content_el = root.find(".//content")
        if content_el is not None and (content_el.text or "").strip():
            return _strip_markup(content_el.text)
        text = _WS_RE.sub(" ", " ".join(
            t for t in root.itertext() if t and t.strip())).strip()
        if text:
            return text
    return _strip_markup(content)


def _special_display(base_type: int, content: str) -> str:
    """非文本、非 appmsg 的常见结构化消息 → 纯文本占位/摘要。"""
    if base_type == 3:
        return "[图片]"
    if base_type == 43:
        return "[视频]"
    if base_type == 47:
        return "[表情]"
    if base_type == 34:
        root = _safe_xml(content)
        el = root.find(".//voicemsg") if root is not None else None
        ms = _int_attr(el.get("voicelength")) if el is not None else 0
        return f"[语音 {ms / 1000:.1f}″]" if ms else "[语音]"
    if base_type == 10000:
        return _sysmsg_display(content)
    if base_type == 48:
        root = _safe_xml(content)
        loc = root.find(".//location") if root is not None else None
        if loc is not None:
            label = loc.get("label") or loc.get("poiname") or ""
            return f"[位置] {label}".strip()
        return "[位置]"
    if base_type == 50:
        root = _safe_xml(content)
        el = root.find(".//msg") if root is not None else None
        text = (el.text or "").strip() if el is not None else ""
        return f"[通话] {text}" if text else "[通话]"
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
    appmsg = parse_appmsg(content) if base_type == 49 else None
    display = appmsg.text() if appmsg else _special_display(base_type, content)
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
        content=content, media=media, raw=raw, appmsg=appmsg, display=display,
    )
