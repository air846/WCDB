"""HTML 会话视图渲染：bubble 布局、按天分组、媒体内嵌、双主题、零 CDN。"""

import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from wechat_export.message_model import Media, Message, Session

TEMPLATES_DIR = Path(__file__).parent / "templates"


def group_by_day(messages: list[Message]) -> list[tuple[str, list[Message]]]:
    days: dict[str, list[Message]] = {}
    for m in messages:
        day = datetime.datetime.fromtimestamp(m.ts / 1000).strftime("%Y-%m-%d")
        days.setdefault(day, []).append(m)
    return sorted(days.items())


def _env(templates_dir: Path | None) -> Environment:
    return Environment(
        loader=FileSystemLoader(templates_dir or TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )


class HtmlRenderer:
    def __init__(self, templates_dir: Path | None = None):
        self.env = _env(templates_dir)

    def render_index(self, out_root: Path, sessions: list[Session],
                     counts: dict, meta: dict) -> None:
        out_root = Path(out_root)
        out_root.mkdir(parents=True, exist_ok=True)
        tpl = self.env.get_template("index.html.j2")
        html = tpl.render(sessions=sessions, counts=counts, meta=meta,
                          total_messages=sum(counts.values()))
        (out_root / "index.html").write_text(html, encoding="utf-8")

    def render_session(self, out_dir: Path, session: Session,
                       messages: list[Message], counts: dict) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tpl = self.env.get_template("session.html.j2")
        days = group_by_day(messages)
        html = tpl.render(session=session, days=days, counts=counts,
                          messages_count=len(messages))
        (out_dir / "index.html").write_text(html, encoding="utf-8")
