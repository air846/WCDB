"""4.x 表结构知识：默认映射 + snapshot 驱动回落。

M0 探测脚本（tools/probe_wechat.py）会把真实 message_0.db 的表结构
dump 到 schema_snapshots/message_0.schema.json；load_schema 优先使用
实测快照，字段缺失时回落默认映射。4.x 社区已知的 message 表列名如下。
"""

from dataclasses import dataclass

from wechat_export.db_access import EncryptedDb

DEFAULT_COLUMNS = [
    "id", "talker", "type", "subtype", "content",
    "createTime", "isSender", "status",
]
DEFAULT_MAPPING = {
    "msg_id": "id", "talker": "talker", "type": "type", "subtype": "subtype",
    "content": "content", "create_time": "createTime", "is_sender": "isSender",
    "status": "status",
}


@dataclass
class SchemaInfo:
    message_table: str
    columns: list[str]
    msg_id: str
    talker: str
    type: str
    subtype: str
    content: str
    create_time: str
    is_sender: str
    status: str


DEFAULT_SCHEMA = SchemaInfo(
    message_table="message",
    columns=list(DEFAULT_COLUMNS),
    **DEFAULT_MAPPING,
)


def load_schema(snapshot: dict | None) -> SchemaInfo:
    if not snapshot:
        return DEFAULT_SCHEMA
    cols = snapshot.get("columns", {}).get(snapshot.get("table", "message"), [])
    mapping = {k: snapshot.get(k, v) for k, v in DEFAULT_MAPPING.items()}
    return SchemaInfo(
        message_table=snapshot.get("table", "message"),
        columns=cols or list(DEFAULT_COLUMNS),
        **mapping,
    )


def snapshot_db(edb: EncryptedDb) -> dict:
    tables = edb.tables()
    return {
        "tables": tables,
        "columns": {t: edb.columns(t) for t in tables},
    }
