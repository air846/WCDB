"""结构化 JSON 写出：messages 分块 + 会话/全局元信息 + .done 标记。"""

import json
import time
from pathlib import Path

from wechat_export.message_model import Message, Session

CHUNK_SIZE = 5000


def _dump_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def write_session_messages(dir_: Path, session: Session, messages: list[Message]) -> int:
    dir_ = Path(dir_)
    dir_.mkdir(parents=True, exist_ok=True)
    total = len(messages)
    n_files = 0
    for i in range(0, total, CHUNK_SIZE):
        n_files += 1
        chunk = messages[i:i + CHUNK_SIZE]
        name = "messages.json" if n_files == 1 else f"messages_{n_files:04d}.json"
        _dump_json(dir_ / name, {
            "session": session.to_dict(),
            "chunk": n_files,
            "total": total,
            "count": len(chunk),
            "messages": [m.to_dict() for m in chunk],
        })
    if total == 0:
        _dump_json(dir_ / "messages.json", {
            "session": session.to_dict(), "chunk": 1, "total": 0,
            "count": 0, "messages": [],
        })
    (dir_ / ".done").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    return max(n_files, 1)


def write_session_json(dir_: Path, session: Session, counts: dict) -> None:
    _dump_json(Path(dir_) / "session.json",
               {**session.to_dict(), "stats": counts})


def write_export_meta(out_root: Path, meta: dict) -> None:
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    meta.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    _dump_json(out_root / "export_meta.json", meta)


def write_sessions_index(out_root: Path, sessions: list[Session], counts: dict) -> None:
    _dump_json(Path(out_root) / "sessions.json", {
        "sessions": [s.to_dict() for s in sessions],
        "counts": counts,
    })
