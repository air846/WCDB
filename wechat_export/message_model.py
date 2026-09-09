from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Contact:
    wxid: str
    name: str = ""
    remark: str = ""
    avatar: Optional[str] = None

    def to_dict(self) -> dict:
        return {"wxid": self.wxid, "name": self.name,
                "remark": self.remark, "avatar": self.avatar}


@dataclass
class Session:
    id: str
    name: str
    chat_type: str = "single"  # single | group
    member_count: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "chat_type": self.chat_type,
                "member_count": self.member_count}


@dataclass
class Media:
    kind: str  # image | video | voice | file
    md5: str = ""
    size: int = 0
    ext: str = ""
    rel_path: str = ""
    status: str = "ok"  # ok | missing
    filename: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "md5": self.md5, "size": self.size,
                "ext": self.ext, "rel_path": self.rel_path,
                "status": self.status, "filename": self.filename,
                "duration_ms": self.duration_ms}


@dataclass
class Message:
    msg_id: str
    ts: int  # 毫秒时间戳
    type: int
    type_name: str
    direction: str  # in | out
    sender: dict  # {"wxid","name","is_self"}
    content: str
    media: Optional[Media] = None
    raw: Optional[dict] = field(default=None)
    appmsg: Optional[object] = None  # appmsg.AppMsg（type 49）
    display: str = ""  # 人类可读展示文本（非纯文本消息）

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id, "ts": self.ts, "type": self.type,
            "type_name": self.type_name, "direction": self.direction,
            "sender": self.sender, "content": self.content,
            "media": self.media.to_dict() if self.media else None,
            "raw": self.raw,
            "appmsg": self.appmsg.to_dict() if self.appmsg else None,
            "display": self.display or self.content,
        }
