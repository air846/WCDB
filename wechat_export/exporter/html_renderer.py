"""HTML 会话视图渲染：bubble 布局、按天分组、媒体内嵌、双主题、零 CDN。

超大会话自动分页（每页 PAGE_SIZE 条），`index.html` 为目录页。
"""

import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from wechat_export.message_model import Media, Message, Session

TEMPLATES_DIR = Path(__file__).parent / "templates"
PAGE_SIZE = 3000


def group_by_day(messages: list[Message]) -> list[tuple[str, list[Message]]]:
    days: dict[str, list[Message]] = {}
    for m in messages:
        day = datetime.datetime.fromtimestamp(m.ts / 1000).strftime("%Y-%m-%d")
        days.setdefault(day, []).append(m)
    return sorted(days.items())


def _fmt_day(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")


def _env(templates_dir: Path | None) -> Environment:
    return Environment(
        loader=FileSystemLoader(templates_dir or TEMPLATES_DIR),
        autoescape=True,  # 模板扩展名为 .j2，select_autoescape 不会命中
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
        if len(messages) <= PAGE_SIZE:
            html = tpl.render(session=session, days=group_by_day(messages),
                              counts=counts, messages_count=len(messages),
                              page_num=1, total_pages=1, prev_page="", next_page="")
            (out_dir / "index.html").write_text(html, encoding="utf-8")
            return

        chunks = [messages[i:i + PAGE_SIZE] for i in range(0, len(messages), PAGE_SIZE)]
        pages = []
        for idx, chunk in enumerate(chunks, 1):
            name = f"page_{idx:04d}.html"
            html = tpl.render(
                session=session, days=group_by_day(chunk), counts=counts,
                messages_count=len(chunk), page_num=idx, total_pages=len(chunks),
                prev_page=f"page_{idx - 1:04d}.html" if idx > 1 else "",
                next_page=f"page_{idx + 1:04d}.html" if idx < len(chunks) else "")
            (out_dir / name).write_text(html, encoding="utf-8")
            pages.append({"name": name, "count": len(chunk),
                          "start": _fmt_day(chunk[0].ts), "end": _fmt_day(chunk[-1].ts)})
        toc = self.env.get_template("session_toc.html.j2")
        (out_dir / "index.html").write_text(
            toc.render(session=session, pages=pages, total_messages=len(messages),
                       counts=counts), encoding="utf-8")
