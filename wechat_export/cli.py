"""CLI 入口：参数解析 + 流水线编排 + 错误码 + 汇总报告（微信 4.x 真实模型）。"""

import argparse
import hashlib
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
from wechat_export.exporter.media_archive import MediaArchive, placeholder_media
from wechat_export.key_provider import KeyProvider, parse_key_hex
from wechat_export.message_model import Media, Message, Session
from wechat_export.parser import parse_message_row
from wechat_export.schema import is_session_table, load_schema

SESSION_SAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SESSION_DB_RE = re.compile(r"^message_\d+\.db$")


def _safe_session_dir(name: str) -> str:
    cleaned = SESSION_SAFE_CHARS.sub("_", name).strip()
    return cleaned or "unknown"


def _numeric_id(msg_id: str) -> int:
    try:
        return int(msg_id)
    except (TypeError, ValueError):
        return 0


def _self_wxid(dir_name: str, known_usernames: set) -> str:
    """账号目录名形如 wxid_xxx_1a2b，库内真实 wxid 为去掉 _<4hex> 后缀的部分。"""
    m = re.match(r"^(wxid_[0-9a-z]+)_[0-9a-f]{4}$", dir_name)
    if m and m.group(1) in known_usernames:
        return m.group(1)
    if dir_name in known_usernames:
        return dir_name
    return m.group(1) if m else dir_name


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
                   help="会话白名单（会话 username，可重复）")
    p.add_argument("--key-hex", help="手动提供密钥（所有库共用一个，hex）")
    p.add_argument("--keys-file", help="密钥文件（JSON：{salt_hex: key_hex}）")
    p.add_argument("--no-media", action="store_true", help="跳过媒体归档")
    p.add_argument("--resume", action="store_true", help="跳过已完成会话（.done 标记）")
    return p.parse_args(argv)


def session_db_files(db_storage: Path) -> list[Path]:
    return [f for f in locator.message_db_files(db_storage)
            if SESSION_DB_RE.match(f.name)]


def _normalize_keys(data) -> dict[str, str]:
    raw = data.get("keys", data) if isinstance(data, dict) else {}
    keys: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, str):
            keys[k.lower()] = v.lower()
        elif isinstance(v, dict):
            salt = str(v.get("salt", k)).lower()
            key = str(v.get("enc_key") or v.get("key") or "").lower()
            if salt and key:
                keys[salt] = key
    return keys


def _load_keys(args, db_files: list[Path]) -> dict[str, str]:
    if args.key_hex:
        key = parse_key_hex(args.key_hex)
        return {f.read_bytes()[:16].hex(): key for f in db_files}
    if args.keys_file:
        data = json.loads(Path(args.keys_file).read_text(encoding="utf-8"))
        return _normalize_keys(data)
    return KeyProvider(db_files).get_keys()


def _load_snapshot() -> dict | None:
    snap_path = Path(__file__).parent / "schema_snapshots" / "message_0.schema.json"
    if snap_path.exists():
        return json.loads(snap_path.read_text(encoding="utf-8"))
    return None


def _db_key(keys: dict[str, str], path: Path) -> str | None:
    try:
        return keys.get(path.read_bytes()[:16].hex())
    except OSError:
        return None


def _load_contacts(db_storage: Path, keys: dict[str, str]) -> dict[str, str]:
    path = db_storage / "contact" / "contact.db"
    key = _db_key(keys, path) if path.exists() else None
    if not key:
        return {}
    try:
        edb = db_access.open_encrypted(path, key)
        rows = edb.query("SELECT username, remark, nick_name FROM contact")
        edb.close()
    except Exception:  # noqa: BLE001
        return {}
    return {r["username"]: (r["remark"] or r["nick_name"] or r["username"])
            for r in rows if r.get("username")}


def _load_session_usernames(db_storage: Path, keys: dict[str, str]) -> list[str]:
    path = db_storage / "session" / "session.db"
    key = _db_key(keys, path) if path.exists() else None
    if not key:
        return []
    try:
        edb = db_access.open_encrypted(path, key)
        rows = edb.query("SELECT username FROM SessionTable")
        edb.close()
    except Exception:  # noqa: BLE001
        return []
    return [r["username"] for r in rows if r.get("username")]


def _load_voice_index(db_storage: Path, keys: dict[str, str]) -> tuple[dict, dict]:
    """返回 ({(chat_name_id, local_id): voice_blob}, {username: chat_name_id})。"""
    path = db_storage / "message" / "media_0.db"
    key = _db_key(keys, path) if path.exists() else None
    if not key:
        return {}, {}
    try:
        edb = db_access.open_encrypted(path, key)
        name2id = {r["user_name"]: r["rowid"] for r in edb.query(
            "SELECT rowid, user_name FROM Name2Id")}
        rows = edb.query("SELECT chat_name_id, local_id, voice_data FROM VoiceInfo")
        edb.close()
    except Exception:  # noqa: BLE001
        return {}, {}
    index = {(r["chat_name_id"], r["local_id"]): r["voice_data"]
             for r in rows if r.get("voice_data")}
    return index, name2id


class MediaResolver:
    def __init__(self, account_root: Path, archive: MediaArchive,
                 voice_index: dict, username_to_name2id: dict):
        self.account_root = account_root
        self.archive = archive
        self.voice_index = voice_index
        self.username_to_name2id = username_to_name2id
        self._index: dict[str, Path] | None = None

    def _session_index(self, username: str) -> dict[str, Path]:
        """按 md5 前缀索引该会话的媒体文件（大文件优先），避免逐消息 rglob。"""
        if self._index is not None:
            return self._index
        attach = (self.account_root / "msg" / "attach"
                  / hashlib.md5(username.encode("utf-8")).hexdigest())
        index: dict[str, Path] = {}
        if attach.is_dir():
            for p in attach.rglob("*"):
                if not p.is_file():
                    continue
                prefix = p.name.split(".")[0]
                old = index.get(prefix)
                if old is None or p.stat().st_size > old.stat().st_size:
                    index[prefix] = p
        self._index = index
        return index

    def resolve(self, media: Media, username: str, local_id: int) -> Media:
        if media.status != "ok":
            return media
        if media.kind == "voice":
            chat_id = self.username_to_name2id.get(username)
            blob = self.voice_index.get((chat_id, local_id)) if chat_id else None
            if blob:
                return self.archive.save_bytes(
                    blob, "voice", ".silk", md5=media.md5 or f"voice_{chat_id}_{local_id}")
            return placeholder_media(media)
        if media.kind == "emoji":
            base = self.account_root / "business" / "emoticon"
            persist = base / "Persist" / media.md5[:2] / media.md5
            if persist.exists():
                return self.archive.save_file(persist, "emoji",
                                              persist.suffix or ".bin", md5=media.md5)
            thumb = base / "Thumb" / media.md5[:2] / (media.md5 + ".thumb")
            if thumb.exists():
                return self.archive.save_file(thumb, "emoji", ".thumb", md5=media.md5)
            return placeholder_media(media)
        if not media.md5:
            return placeholder_media(media)
        index = self._session_index(username)
        src = (index.get(media.md5) or index.get(media.md5 + "_h")
               or index.get(media.md5 + "_t"))
        if src is None:
            return placeholder_media(media)
        return self.archive.save_file(src, media.kind, src.suffix or media.ext,
                                      md5=media.md5)


def _archive_media(msgs: list[Message], resolver: MediaResolver,
                   username: str) -> tuple[int, int]:
    ok = miss = 0
    for m in msgs:
        if not m.media:
            continue
        try:
            m.media = resolver.resolve(m.media, username, _numeric_id(m.msg_id))
        except (OSError, ValueError):
            m.media = placeholder_media(m.media)
        if m.media.status == "ok":
            ok += 1
        else:
            miss += 1
    return ok, miss


def _make_session(username: str, contacts: dict[str, str]) -> Session:
    is_group = username.endswith("@chatroom")
    return Session(id=username, name=contacts.get(username, username),
                   chat_type="group" if is_group else "single")


def _resume_session(out_dir: Path) -> Session | None:
    try:
        data = json.loads((out_dir / "session.json").read_text(encoding="utf-8"))
        return Session(id=data["id"], name=data["name"],
                       chat_type=data.get("chat_type", "single"),
                       member_count=data.get("member_count", 0))
    except Exception:  # noqa: BLE001
        return None


def _open_shards(db_files, keys, schema, contacts, report):
    """打开所有会话分片，返回 [(db_path, edb, sender_map)]。"""
    shards = []
    for db_path in db_files:
        key = _db_key(keys, db_path)
        if not key:
            report.errors.append(f"{db_path.name}: 无可用密钥（未在内存中缓存？）")
            continue
        try:
            edb = db_access.open_encrypted(db_path, key)
        except ExportError as exc:
            report.errors.append(f"{db_path.name}: {exc}（{exc.hint}）")
            continue
        try:
            name2id = {r["rowid"]: r["user_name"] for r in edb.query(
                f'SELECT rowid, user_name FROM "{schema.name2id_table}"')}
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{db_path.name}: 读取 Name2Id 失败 {exc}")
            edb.close()
            continue
        sender_map = {rid: {"wxid": u, "name": contacts.get(u, u)}
                      for rid, u in name2id.items()}
        shards.append((db_path, edb, sender_map))
    return shards


def run_export(args: argparse.Namespace) -> ExportReport:
    report = ExportReport()
    if args.key_hex:
        args.key_hex = parse_key_hex(args.key_hex)  # 参数错误优先
    accounts = locator.locate_data(args.data_dir)
    account = accounts[0]
    if args.wxid:
        account = next((a for a in accounts if a.wxid == args.wxid), accounts[0])
    if not account.db_storage:
        raise WeChatNotFoundError(
            f"账号 {account.wxid} 未找到 db_storage 目录",
            hint="请确认该账号已完整登录并同步过数据")
    db_files = session_db_files(account.db_storage)
    if not db_files:
        raise WeChatNotFoundError(
            f"账号 {account.wxid} 未找到 message_N.db 会话库",
            hint="请确认已用该账号登录微信 4.x 并同步过消息")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    all_dbs = sorted(account.db_storage.rglob("*.db"))
    keys = _load_keys(args, all_dbs)
    contacts = _load_contacts(account.db_storage, keys)
    session_usernames = _load_session_usernames(account.db_storage, keys)
    voice_index, media_name2id = _load_voice_index(account.db_storage, keys)
    schema = load_schema(_load_snapshot())
    renderer = HtmlRenderer()
    account_root = account.db_storage.parent
    done_sessions: list[Session] = []
    counts: dict[str, int] = {}

    shards = _open_shards(db_files, keys, schema, contacts, report)
    try:
        md5_to_username: dict[str, str] = {}
        all_usernames = set(contacts) | set(session_usernames)
        for _, _, sender_map in shards:
            all_usernames.update(v["wxid"] for v in sender_map.values())
        for u in all_usernames:
            md5_to_username.setdefault(hashlib.md5(u.encode("utf-8")).hexdigest(), u)
        self_wxid = _self_wxid(account.wxid, all_usernames)

        # 会话 → 分片表（同一会话跨 message_0..N.db）
        session_tables: dict[str, list[tuple]] = {}
        for _, edb, sender_map in shards:
            for table in edb.tables():
                if not is_session_table(table):
                    continue
                username = md5_to_username.get(
                    table[len(schema.message_table_prefix):], table)
                session_tables.setdefault(username, []).append((edb, table, sender_map))

        wanted = set(args.session)
        for username in sorted(session_tables):
            if wanted and username not in wanted:
                continue
            out_dir = out_root / _safe_session_dir(username)
            if args.resume and (out_dir / ".done").exists():
                report.skipped += 1
                session = _resume_session(out_dir) or _make_session(username, contacts)
                done_sessions.append(session)
                try:
                    sdata = json.loads((out_dir / "session.json").read_text(encoding="utf-8"))
                    counts[session.id] = int(sdata.get("stats", {}).get("messages", 0))
                except Exception:  # noqa: BLE001
                    counts[session.id] = 0
                continue
            msgs: list[Message] = []
            for edb, table, sender_map in session_tables[username]:
                try:
                    rows = edb.query(
                        f'SELECT * FROM "{table}" ORDER BY "{schema.msg_id}" ASC')
                except Exception as exc:  # noqa: BLE001
                    report.errors.append(f"{username}/{table}: 查询失败 {exc}")
                    continue
                msgs.extend(parse_message_row(r, schema, self_wxid, username, sender_map)
                            for r in rows)
            msgs.sort(key=lambda m: (m.ts, _numeric_id(m.msg_id)))
            ok = miss = 0
            if not args.no_media:
                archive = MediaArchive(out_dir / "media")
                resolver = MediaResolver(account_root, archive, voice_index, media_name2id)
                ok, miss = _archive_media(msgs, resolver, username)
            session = _make_session(username, contacts)
            write_session_messages(out_dir, session, msgs)
            write_session_json(out_dir, session,
                               {"messages": len(msgs), "media_ok": ok,
                                "media_missing": miss})
            renderer.render_session(out_dir, session, msgs, {"messages": len(msgs)})
            report.sessions_done += 1
            report.messages += len(msgs)
            report.media_ok += ok
            report.media_missing += miss
            done_sessions.append(session)
            counts[username] = len(msgs)
    finally:
        for _, edb, _ in shards:
            edb.close()

    write_export_meta(out_root, {
        "account": account.wxid,
        "stats": {"sessions": report.sessions_done,
                  "messages": report.messages,
                  "media": {"ok": report.media_ok, "missing": report.media_missing},
                  "skipped": report.skipped},
    })
    write_sessions_index(out_root, done_sessions, counts)
    renderer.render_index(out_root, done_sessions, counts, {
        "account": account.wxid,
        "created_at": "见 export_meta.json",
    })
    if report.errors:
        raise PartialExportError(
            f"导出完成但存在 {len(report.errors)} 个失败项：\n" + "\n".join(report.errors),
            hint="失败会话未写入；修正后可用 --resume 续导")
    return report


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


if __name__ == "__main__":
    sys.exit(main())
