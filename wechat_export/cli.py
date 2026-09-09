"""CLI 入口：参数解析 + 流水线编排 + 错误码 + 汇总报告。"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from wechat_export import db_access, locator
from wechat_export.exceptions import (
    ExportError, PartialExportError, WeChatNotFoundError, format_error,
)
from wechat_export.exporter.html_renderer import HtmlRenderer
from wechat_export.exporter.json_writer import (
    write_export_meta, write_session_json, write_session_messages,
    write_sessions_index,
)
from wechat_export.exporter.media_archive import MediaArchive
from wechat_export.key_provider import KeyProvider, parse_key_hex
from wechat_export.message_model import Message, Session
from wechat_export.parser import parse_message_row
from wechat_export.schema import SchemaInfo, load_schema

SESSION_SAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_session_dir(name: str) -> str:
    cleaned = SESSION_SAFE_CHARS.sub("_", name).strip()
    return cleaned or "unknown"


@dataclass
class ExportReport:
    sessions_done: int = 0
    messages: int = 0
    media_ok: int = 0
    media_missing: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="wechat-export",
        description="Windows 微信 4.x 聊天记录全量导出（HTML + JSON + 媒体）",
    )
    p.add_argument("--out", required=True, help="导出输出目录")
    p.add_argument("--data-dir", help="微信数据根目录（默认自动扫描）")
    p.add_argument("--wxid", help="指定账号（默认第一个找到的）")
    p.add_argument("--session", action="append", default=[],
                   help="会话白名单（talker id，可重复）")
    p.add_argument("--key-hex", help="手动提供 32 字节数据库密钥（hex）")
    p.add_argument("--no-media", action="store_true", help="跳过媒体归档")
    p.add_argument("--resume", action="store_true", help="跳过已完成会话（.done 标记）")
    return p.parse_args(argv)


def run_export(args: argparse.Namespace) -> ExportReport:
    report = ExportReport()
    if args.key_hex:
        args.key_hex = parse_key_hex(args.key_hex)  # 参数错误优先于任何定位
    accounts = locator.locate_data(args.data_dir)
    account = accounts[0]
    if args.wxid:
        account = next((a for a in accounts if a.wxid == args.wxid), accounts[0])
    if not account.db_storage:
        raise WeChatNotFoundError(
            f"账号 {account.wxid} 未找到 db_storage 目录",
            hint="请确认该账号已完整登录并同步过数据",
        )
    db_files = locator.message_db_files(account.db_storage)
    if not db_files:
        raise WeChatNotFoundError(
            f"账号 {account.wxid} 未找到 message_*.db 库文件",
            hint="请确认已用该账号登录微信 4.x 并同步过消息",
        )

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    renderer = HtmlRenderer()
    schema_info = load_schema(_load_snapshot())
    done_sessions: list[Session] = []
    counts: dict[str, int] = {}

    for db_path in db_files:
        try:
            key = KeyProvider(db_path, manual_key=args.key_hex,
                              account=account).get_key()
            edb = db_access.open_encrypted(db_path, key)
            try:
                talkers = _distinct_talkers(edb, schema_info)
                if args.session:
                    wanted = set(args.session)
                    talkers = [t for t in talkers if t in wanted]
                for talker in talkers:
                    out_dir = out_root / _safe_session_dir(talker)
                    if args.resume and (out_dir / ".done").exists():
                        report.skipped += 1
                        continue
                    session = _make_session(talker)
                    msgs = [parse_message_row(r, schema_info, account.wxid, talker)
                            for r in _query_messages(edb, schema_info, talker)]
                    ok, missing = _archive_media(msgs, out_dir, args)
                    write_session_messages(out_dir, session, msgs)
                    write_session_json(out_dir, session,
                                       {"messages": len(msgs)})
                    renderer.render_session(out_dir, session, msgs,
                                            {"messages": len(msgs)})
                    report.sessions_done += 1
                    report.messages += len(msgs)
                    report.media_ok += ok
                    report.media_missing += missing
                    done_sessions.append(session)
                    counts[talker] = len(msgs)
            finally:
                edb.close()
        except ExportError as exc:
            report.errors.append(f"{db_path.name}: {exc}（{exc.hint}）")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{db_path.name}: 未预期错误 {exc}")

    write_export_meta(out_root, {
        "account": account.wxid,
        "stats": {"sessions": report.sessions_done,
                  "messages": report.messages,
                  "media": {"ok": report.media_ok,
                             "missing": report.media_missing},
                  "skipped": report.skipped},
    })
    write_sessions_index(out_root, done_sessions, counts)
    renderer.render_index(out_root, done_sessions, counts, {
        "account": account.wxid,
        "created_at": "见 export_meta.json",
    })
    if report.errors:
        raise PartialExportError(
            f"导出完成但存在 {len(report.errors)} 个失败项：\n"
            + "\n".join(report.errors),
            hint="失败会话未写入；修正后可用 --resume 续导",
        )
    return report


def _load_snapshot() -> dict | None:
    snap_path = Path(__file__).parent / "schema_snapshots" / "message_0.schema.json"
    if snap_path.exists():
        return json.loads(snap_path.read_text(encoding="utf-8"))
    return None


def _distinct_talkers(edb, schema: SchemaInfo) -> list[str]:
    rows = edb.query(
        f"SELECT DISTINCT {schema.talker} AS talker FROM {schema.message_table}"
    )
    return [r["talker"] for r in rows]


def _make_session(talker: str) -> Session:
    # 4.x 群聊 talker 以 @chatroom 结尾；显示名解析在 Task 14 依据实测增强
    is_group = talker.endswith("@chatroom")
    return Session(id=talker, name=talker,
                   chat_type="group" if is_group else "single")


def _query_messages(edb, schema: SchemaInfo, talker: str) -> list[dict]:
    return edb.query(
        f"SELECT * FROM {schema.message_table} WHERE {schema.talker} = ? "
        f"ORDER BY {schema.create_time} ASC",
        (talker,),
    )


def _archive_media(msgs: list[Message], out_dir: Path, args) -> tuple[int, int]:
    """返回 (成功数, 缺失数)。4.x 媒体二进制定位待 M0 实测
    （Task 14 Step 5 依据实测接入真实归档，本阶段如实标记缺失）。"""
    if args.no_media:
        return 0, 0
    MediaArchive(out_dir / "media")  # 预创建 media 目录结构
    ok = miss = 0
    for m in msgs:
        if m.media and m.media.status == "ok":
            m.media.status = "missing"  # 二进制定位未实现前如实计数
            miss += 1
    return ok, miss


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_export(args)
        print(f"导出完成：会话 {report.sessions_done} 个，消息 {report.messages} 条，"
              f"媒体成功 {report.media_ok} / 缺失 {report.media_missing}，"
              f"跳过 {report.skipped} 个")
        print(f"输出目录：{Path(args.out).resolve()}")
        return 0
    except ExportError as e:
        print(format_error(e), file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("用户中断", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"未预期错误：{e}", file=sys.stderr)
        return 1
