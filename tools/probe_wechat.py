"""M0 探测脚本：本机实测 4.x 数据目录/密钥/SQLCipher 布局/schema。

运行：python tools/probe_wechat.py --out docs/findings/4x-reverse-notes.md
输出：结构化 JSON（stdout）+ 人类可读报告（--out）+ 更新 schema 快照。
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

from wechat_export import db_access, locator
from wechat_export.key_provider import KeyProvider, parse_key_hex
from wechat_export.schema import snapshot_db

SNAP_PATH = (Path(__file__).parent.parent / "wechat_export" / "schema_snapshots"
             / "message_0.schema.json")


def _write_snapshot(first: dict) -> None:
    """把首个解密成功的库的实测表结构写入 schema 快照。"""
    full = json.loads(first["schema"])
    SNAP_PATH.write_text(
        json.dumps({
            "table_prefix": "Msg_",
            "name2id_table": "Name2Id",
            "columns": full["columns"],
            "msg_id": "local_id",
            "type": "local_type",
            "sender_id": "real_sender_id",
            "create_time": "create_time",
            "content": "message_content",
            "source": "source",
            "content_ct": "WCDB_CT_message_content",
            "source_ct": "WCDB_CT_source",
            "status": "status",
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def _keys_for(db_files, manual_key: str | None) -> dict[str, str]:
    if manual_key:
        key = parse_key_hex(manual_key)
        return {f.read_bytes()[:16].hex(): key for f in db_files}
    return KeyProvider(db_files).get_keys()


def probe(data_dir: str | None, manual_key: str | None) -> dict:
    accounts = locator.locate_data(data_dir)
    result = {"accounts": []}
    for acc in accounts:
        entry = {"wxid": acc.wxid, "root": str(acc.root),
                 "db_storage": str(acc.db_storage) if acc.db_storage else None,
                 "db_files": []}
        if not acc.db_storage:
            result["accounts"].append(entry)
            continue
        db_files = locator.message_db_files(acc.db_storage)
        try:
            keys = _keys_for(db_files, manual_key)
        except Exception as exc:  # noqa: BLE001
            entry["key_error"] = str(exc)
            keys = {}
        for db in db_files:
            rec = {"name": db.name, "size": db.stat().st_size,
                   "decrypted": False, "params": None, "tables": 0,
                   "key_source": None}
            try:
                salt = db.read_bytes()[:16].hex()
                key = keys.get(salt)
                if not key:
                    raise RuntimeError("该库密钥未提取到")
                edb = db_access.open_encrypted(db, key)
                snap = snapshot_db(edb)
                params = edb.params
                edb.close()
                rec.update(decrypted=True, params=params,
                           key_source="manual" if manual_key else "memory",
                           tables=len(snap["tables"]),
                           schema=json.dumps(snap, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                rec["error"] = str(exc)
            entry["db_files"].append(rec)
        result["accounts"].append(entry)
    return result


def summarize(res: dict) -> dict:
    total = dec = 0
    for acc in res["accounts"]:
        for db in acc["db_files"]:
            total += 1
            dec += 1 if db.get("decrypted") else 0
    return {"total_dbs": total, "decrypted_dbs": dec}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="微信 4.x 数据探测")
    p.add_argument("--data-dir")
    p.add_argument("--key-hex")
    p.add_argument("--out", default="docs/findings/4x-reverse-notes.md")
    args = p.parse_args(argv)
    res = probe(args.data_dir, args.key_hex)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    s = summarize(res)
    print(f"\n解密成功 {s['decrypted_dbs']}/{s['total_dbs']} 个库", file=sys.stderr)
    if s["decrypted_dbs"]:
        first = next(db for a in res["accounts"]
                     for db in a["db_files"] if db.get("decrypted"))
        _write_snapshot(first)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            f"# 微信 4.x 逆向实测记录\n\n- 时间：{datetime.datetime.now()}\n"
            f"- 解密：{s['decrypted_dbs']}/{s['total_dbs']}\n"
            f"- 参数：{first.get('params')}\n- 密钥来源：{first.get('key_source')}\n\n"
            f"```json\n{json.dumps(json.loads(first['schema']), ensure_ascii=False, indent=1)}\n```\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
