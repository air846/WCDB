"""type 49 应用消息（appmsg XML）→ 结构化摘要。

微信 `local_type=49` 的 `message_content` 是 appmsg XML，涵盖引用、链接、
文件、转账、红包、聊天记录、小程序、视频号、拍一拍等十几种类型。直接展示
XML 不可读；本模块解析为统一模型，供 HTML 卡片与 JSON 使用。
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

KIND_NAMES = {
    1: "文本", 3: "音乐", 4: "短视频", 5: "链接", 6: "文件", 8: "表情",
    17: "位置", 19: "聊天记录", 24: "收藏笔记", 33: "小程序", 36: "小程序",
    44: "视频号", 51: "视频号", 53: "视频号直播", 57: "引用", 62: "拍一拍",
    63: "视频号直播", 68: "游戏圈", 87: "群公告", 2000: "转账",
    2001: "红包", 2002: "群收款", 2003: "直播", 2004: "微信支付",
    2005: "接龙", 2006: "接龙",
}

# 引用的原消息若为媒体，不展开 XML，仅给出占位。
REFER_PLACEHOLDERS = {
    3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情]",
    49: "[应用消息]", 50: "[通话]", 62: "[拍一拍]",
}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return (value.text or "").strip()


def _int(value) -> int:
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _human_size(size: int) -> str:
    if size <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


@dataclass
class AppMsg:
    type: int = 0
    kind: str = "应用消息"
    title: str = ""
    desc: str = ""
    url: str = ""
    source: str = ""
    quote: dict | None = None
    filename: str = ""
    ext: str = ""
    size: int = 0
    count: int = 0
    md5: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def safe_url(self) -> str:
        """仅允许 http(s) 链接进入 HTML href，避免 javascript: 注入。"""
        url = (self.url or "").strip()
        return url if url.lower().startswith(("http://", "https://")) else ""

    def to_dict(self) -> dict:
        return {
            "type": self.type, "kind": self.kind, "title": self.title,
            "desc": self.desc, "url": self.url, "source": self.source,
            "quote": self.quote, "filename": self.filename, "ext": self.ext,
            "size": self.size, "count": self.count, "md5": self.md5,
            "extra": self.extra,
        }

    def text(self) -> str:
        """单行/多行纯文本摘要（JSON display 与兜底渲染）。"""
        lines: list[str] = []
        if self.kind == "引用" and self.quote:
            name = self.quote.get("name") or ""
            quoted = self.quote.get("content") or ""
            lines.append(f"[引用] {name}：{quoted}" if name else f"[引用] {quoted}")
            if self.title:
                lines.append(self.title)
        elif self.kind == "文件":
            size = _human_size(self.size)
            lines.append(f"[文件] {self.filename}" + (f"（{size}）" if size else ""))
        elif self.kind in ("转账", "红包", "群收款"):
            head = f"[{self.kind}]"
            lines.append(f"{head} {self.title}".strip() if self.title else head)
            if self.desc:
                lines.append(self.desc)
            if self.extra.get("memo"):
                lines.append(str(self.extra["memo"]))
        elif self.kind == "聊天记录":
            suffix = f"（{self.count} 条）" if self.count else ""
            lines.append(f"[聊天记录] {self.title}{suffix}".strip())
            if self.desc:
                lines.append(self.desc)
        else:
            head = f"[{self.kind}]"
            if self.source:
                head += f" {self.source}"
            if self.title:
                lines.append(f"{head} {self.title}".strip())
            else:
                lines.append(head)
            if self.desc and self.desc != self.title:
                lines.append(self.desc)
        return "\n".join(line for line in lines if line).strip()


def _parse_record_count(app) -> int:
    record = app.find("recorditem")
    if record is None or not record.text:
        return 0
    try:
        info = ET.fromstring(record.text)
    except ET.ParseError:
        return 0
    datalist = info.find(".//datalist")
    return _int(datalist.get("count")) if datalist is not None else 0


def _parse_quote(app) -> dict | None:
    ref = app.find("refermsg")
    if ref is None:
        return None
    rtype = _int(ref.findtext("type"))
    content = _text(ref.findtext("content"))
    if rtype != 1:
        content = REFER_PLACEHOLDERS.get(rtype, f"[类型 {rtype}]")
    return {
        "name": _text(ref.findtext("displayname")),
        "content": content,
        "ts": _int(ref.findtext("createtime")) * 1000,
    }


def parse_appmsg(content: str | None) -> AppMsg | None:
    """解析 appmsg XML；非 appmsg/非法 XML 返回 None。"""
    if not content:
        return None
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    app = root if root.tag == "appmsg" else root.find("appmsg")
    if app is None:
        return None

    mtype = _int(app.findtext("type"))
    msg = AppMsg(type=mtype, kind=KIND_NAMES.get(mtype, "应用消息"))
    msg.title = _text(app.findtext("title"))
    msg.desc = _text(app.findtext("des"))
    msg.url = _text(app.findtext("url"))
    msg.source = _text(app.findtext("sourcedisplayname"))

    if mtype == 6:
        attach = app.find("appattach")
        msg.filename = msg.title
        if attach is not None:
            msg.ext = _text(attach.findtext("fileext")).lstrip(".")
            msg.size = _int(attach.findtext("totallen"))
            msg.md5 = _text(attach.findtext("md5"))
        msg.md5 = msg.md5 or _text(app.findtext("md5"))
        if not msg.ext and msg.filename:
            msg.ext = msg.filename.rsplit(".", 1)[-1] if "." in msg.filename else ""
    elif mtype == 57:
        msg.quote = _parse_quote(app)
    elif mtype == 19:
        msg.count = _parse_record_count(app)
    elif mtype in (51, 53, 63):
        feed = app.find("finderFeed")
        if feed is not None:
            msg.source = _text(feed.findtext("nickname")) or msg.source
            msg.desc = _text(feed.findtext("desc")) or msg.desc
            if msg.source:
                msg.title = msg.source
    elif mtype == 8:
        attach = app.find("appattach")
        if attach is not None:
            msg.md5 = _text(attach.findtext("emoticonmd5"))
    elif mtype == 2000:
        pay = app.find("wcpayinfo")
        if pay is not None:
            msg.desc = _text(pay.findtext("feedesc")) or msg.desc
            memo = _text(pay.findtext("pay_memo"))
            if memo:
                msg.extra["memo"] = memo
    elif mtype == 2001:
        pay = app.find("wcpayinfo")
        if pay is not None:
            wording = (_text(pay.findtext("sendertitle"))
                       or _text(pay.findtext("receivertitle")))
            if wording:
                msg.extra["wording"] = wording
                if not msg.desc:
                    msg.desc = wording
    return msg
