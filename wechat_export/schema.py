"""4.x 真实表结构知识（M0 实测，微信 4.1.13.63）。

message_N.db 不再使用单张 message 表，而是：
- 每个会话一张表：`Msg_<md5(session_username)>`
- `Name2Id(rowid, user_name, is_session)`：real_sender_id / 会话名映射
- `create_time` 为秒级；`source`/`message_content` 可能 zstd 压缩（`WCDB_CT_*`）
"""

import hashlib
from dataclasses import dataclass, field

from wechat_export.db_access import EncryptedDb

MSG_TABLE_PREFIX = "Msg_"
NAME2ID_TABLE = "Name2Id"

MSG_COLUMNS = [
    "local_id", "server_id", "local_type", "sort_seq", "real_sender_id",
    "create_time", "status", "upload_status", "download_status", "server_seq",
    "origin_source", "source", "message_content", "compress_content",
    "packed_info_data", "WCDB_CT_message_content", "WCDB_CT_source",
]

CT_NONE = 0
CT_ZSTD = 4


@dataclass
class SchemaInfo:
    message_table_prefix: str = MSG_TABLE_PREFIX
    name2id_table: str = NAME2ID_TABLE
    columns: list[str] = field(default_factory=lambda: list(MSG_COLUMNS))
    msg_id: str = "local_id"
    type: str = "local_type"
    sender_id: str = "real_sender_id"
    create_time: str = "create_time"
    content: str = "message_content"
    source: str = "source"
    content_ct: str = "WCDB_CT_message_content"
    source_ct: str = "WCDB_CT_source"
    status: str = "status"


DEFAULT_SCHEMA = SchemaInfo()


def session_table_name(username: str) -> str:
    return MSG_TABLE_PREFIX + hashlib.md5(username.encode("utf-8")).hexdigest()


def is_session_table(name: str) -> bool:
    return (name.startswith(MSG_TABLE_PREFIX)
            and len(name) == len(MSG_TABLE_PREFIX) + 32
            and all(c in "0123456789abcdef" for c in name[len(MSG_TABLE_PREFIX):]))


def load_schema(snapshot: dict | None) -> SchemaInfo:
    if not snapshot:
        return DEFAULT_SCHEMA
    cols = snapshot.get("columns", {})
    msg_table = next((t for t in cols if is_session_table(t)), None)
    return SchemaInfo(
        message_table_prefix=snapshot.get("table_prefix", MSG_TABLE_PREFIX),
        name2id_table=snapshot.get("name2id_table", NAME2ID_TABLE),
        columns=list(cols.get(msg_table, MSG_COLUMNS)) if msg_table else list(MSG_COLUMNS),
        msg_id=snapshot.get("msg_id", "local_id"),
        type=snapshot.get("type", "local_type"),
        sender_id=snapshot.get("sender_id", "real_sender_id"),
        create_time=snapshot.get("create_time", "create_time"),
        content=snapshot.get("content", "message_content"),
        source=snapshot.get("source", "source"),
        content_ct=snapshot.get("content_ct", "WCDB_CT_message_content"),
        source_ct=snapshot.get("source_ct", "WCDB_CT_source"),
        status=snapshot.get("status", "status"),
    )


def snapshot_db(edb: EncryptedDb) -> dict:
    tables = edb.tables()
    return {"tables": tables, "columns": {t: edb.columns(t) for t in tables}}
