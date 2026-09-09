# 微信聊天记录导出工具（Windows 4.x）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Python 构建一个 CLI 工具，从 Windows 微信 4.x 本机数据目录解密聊天数据库，全量导出为 HTML 会话视图 + 结构化 JSON + 媒体文件。

**Architecture:** 分层流水线：`locator`（定位数据）→ `key_provider`（提取密钥）→ `db_access`（SQLCipher 只读打开库）→ `parser`（行→统一消息模型）→ `exporter`（JSON/媒体/HTML 三件套）→ `cli`（编排）。关键不确定性（4.x 密钥位置与 SQLCipher 参数）通过 M0 探测脚本在本机实测确认，测试用合成加密夹具库保证可复现。

**Tech Stack:** Python 3.10+、`sqlcipher3-binary`（备选 `pysqlcipher3`）、`pycryptodome`、`jinja2`、`pytest`、标准库 `argparse`/`ctypes`/`pathlib`。

**Spec:** `docs/superpowers/specs/2026-09-09-wechat-chat-export-design.md`

## Global Constraints

- 仅支持 Windows 桌面版微信 **4.x**
- 全程只读打开原始库；**不写解密副本落盘**；任何临时文件用完即删；不联网
- 所有用户可见错误信息为**中文**，格式：发生了什么 + 可能原因 + 建议操作
- 错误码：0 成功 / 1 参数错误 / 2 未找到微信 / 3 密钥失败 / 4 解密失败 / 5 导出部分失败
- 单线程顺序导出；`--resume` 跳过已完成会话（以输出目录内 `.done` 标记文件为准）
- HTML 输出**零 CDN 依赖**，完全离线可用
- 合规边界写入 README：仅本人已登录账号数据、仅本机处理、不外传

## 文件结构

```
D:/code/WCDB/
├── pyproject.toml                    # 依赖 + console script: wechat-export
├── README.md                         # 用法 + 合规边界 + 常见问题
├── tools/
│   └── probe_wechat.py               # M0 探测脚本：本机实测，产出快照与报告
├── wechat_export/
│   ├── __init__.py                   # __version__
│   ├── cli.py                        # 参数解析 + 编排 + 错误码 + 报告
│   ├── locator.py                    # 数据目录/账号/db 文件定位
│   ├── key_provider.py               # 三级密钥提取 + 校验
│   ├── db_access.py                  # SQLCipher 参数枚举 + 只读查询
│   ├── schema.py                     # 表结构知识 + 快照加载
│   ├── message_model.py              # Contact/Session/Media/Message
│   ├── parser.py                     # 行 → 消息模型
│   ├── exceptions.py                 # 分级异常 + 错误码
│   ├── exporter/
│   │   ├── __init__.py
│   │   ├── json_writer.py            # messages.json 分块 + 元信息
│   │   ├── media_archive.py          # md5 去重 + 原子写盘
│   │   ├── html_renderer.py          # index/会话页渲染
│   │   └── templates/
│   │       ├── base.html.j2          # 公共样式/脚本
│   │       ├── index.html.j2         # 会话列表页
│   │       └── session.html.j2       # 会话气泡视图
│   └── schema_snapshots/
│       └── message_0.schema.json     # 默认快照（M0 实测后替换）
├── tests/
│   ├── conftest.py                   # 夹具工厂引用
│   ├── fixtures/
│   │   └── db_factory.py             # 合成 SQLCipher 加密库生成器
│   ├── test_exceptions.py
│   ├── test_message_model.py
│   ├── test_locator.py
│   ├── test_db_access.py
│   ├── test_key_provider.py
│   ├── test_schema.py
│   ├── test_parser.py
│   ├── test_media_archive.py
│   ├── test_json_writer.py
│   ├── test_html_renderer.py
│   └── test_cli.py
└── docs/
    ├── superpowers/specs/...         # 已存在
    └── findings/
        └── 4x-reverse-notes.md       # M0 实测记录（探测脚本产出）
```

---

### Task 1: 项目脚手架与依赖验证

**Files:**
- Create: `pyproject.toml`
- Create: `wechat_export/__init__.py`
- Create: `tests/test_smoke.py`
- Create: `.gitignore`

**Interfaces:**
- Consumes: 无
- Produces: `wechat_export.__version__ = "0.1.0"`；pytest 可运行；`sqlcipher3` 可导入（失败时给出清晰中文错误）

- [ ] **Step 1: 检查 Python 环境**

Run: `python --version`
Expected: 3.10+。若没有 Python，先安装（本机需人工操作，中断该步骤并回填说明）。

- [ ] **Step 2: 写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "wechat-export"
version = "0.1.0"
description = "Windows 微信 4.x 聊天记录全量导出工具（HTML + JSON + 媒体）"
requires-python = ">=3.10"
dependencies = [
    "sqlcipher3-binary>=0.5.3",
    "pycryptodome>=3.20",
    "jinja2>=3.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
wechat-export = "wechat_export.cli:main"

[tool.setuptools.packages.find]
include = ["wechat_export*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: 写 `wechat_export/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: 写 `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
dist/
build/
*.egg-info/
.pytest_cache/
chat_export/
```

- [ ] **Step 5: 创建虚拟环境并安装依赖**

```bash
cd /d/code/WCDB
python -m venv .venv
source .venv/Scripts/activate    # Git Bash；若用 cmd：.venv\Scripts\activate.bat
pip install -e ".[dev]"
```

如果 `sqlcipher3-binary` 安装失败，改用备选：

```bash
pip install pysqlcipher3 "pysqlcipher3-binary" 2>/dev/null || pip install "pysqlcipher3-binary"
```

（`db_access.py` 会同时兼容两类导入名，见 Task 5。）

- [ ] **Step 6: 写冒烟测试验证 sqlcipher3 可用**

`tests/test_smoke.py`：

```python
import wechat_export


def test_version():
    assert wechat_export.__version__ == "0.1.0"


def test_sqlcipher_importable():
    import sqlcipher3  # noqa: F401
```

- [ ] **Step 7: 运行测试**

Run: `pytest -q`
Expected: 2 passed。（若 sqlcipher3 导入失败，先解决依赖再继续——这是全局硬依赖。）

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore wechat_export/__init__.py tests/test_smoke.py
git commit -m "chore: 项目脚手架与 SQLCipher 依赖验证"
```

---

### Task 2: 分级异常与错误码（exceptions.py）

**Files:**
- Create: `wechat_export/exceptions.py`
- Test: `tests/test_exceptions.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `class ExportError(Exception)`：属性 `code: int`、`hint: str`
  - `class ConfigError(ExportError)` code=1
  - `class WeChatNotFoundError(ExportError)` code=2
  - `class KeyExtractError(ExportError)` code=3
  - `class DecryptError(ExportError)` code=4
  - `class PartialExportError(ExportError)` code=5（导出中部分失败，message 含失败汇总）
  - `def format_error(e: ExportError) -> str`：中文三行格式

- [ ] **Step 1: 写失败测试**

`tests/test_exceptions.py`：

```python
import pytest

from wechat_export.exceptions import (
    ConfigError, DecryptError, ExportError, KeyExtractError,
    PartialExportError, WeChatNotFoundError, format_error,
)

EXPECTED_CODES = {
    ConfigError: 1, WeChatNotFoundError: 2, KeyExtractError: 3,
    DecryptError: 4, PartialExportError: 5,
}


@pytest.mark.parametrize("cls,code", EXPECTED_CODES.items())
def test_error_codes(cls, code):
    assert cls("x").code == code


def test_messages_chinese():
    err = DecryptError("无法解密数据库", hint="可能密钥错误")
    assert "无法解密数据库" in str(err)
    assert err.hint == "可能密钥错误"


def test_format_error_three_lines():
    err = KeyExtractError("未找到密钥", hint="请确认微信已登录")
    text = format_error(err)
    lines = text.strip().splitlines()
    assert len(lines) == 3
    assert lines[2] == "建议操作：请确认微信已登录"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_exceptions.py -v`
Expected: FAIL，ImportError: cannot import name 'ConfigError'

- [ ] **Step 3: 实现 exceptions.py**

```python
class ExportError(Exception):
    """导出工具所有异常的基类。code 对应全局错误码。"""

    code = 0

    def __init__(self, message: str, *, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class ConfigError(ExportError):
    code = 1


class WeChatNotFoundError(ExportError):
    code = 2


class KeyExtractError(ExportError):
    code = 3


class DecryptError(ExportError):
    code = 4


class PartialExportError(ExportError):
    code = 5


def format_error(e: ExportError) -> str:
    hint = e.hint or "请检查上面的信息后重试"
    return (
        f"错误码 {e.code}：{e}。\n"
        f"可能原因：{hint}。\n"
        f"建议操作：{hint}。"
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_exceptions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/exceptions.py tests/test_exceptions.py
git commit -m "feat: 分级异常与错误码"
```

---

### Task 3: 统一消息模型（message_model.py）

**Files:**
- Create: `wechat_export/message_model.py`
- Test: `tests/test_message_model.py`

**Interfaces:**
- Consumes: 无
- Produces（所有类均带 `to_dict()`，供 JSON 导出与 HTML 渲染共用）：
  - `@dataclass Contact(wxid, name="", remark="", avatar=None)`
  - `@dataclass Session(id, name, chat_type="single", member_count=0)`；`chat_type` ∈ {"single", "group"}
  - `@dataclass Media(kind, md5="", size=0, ext="", rel_path="", status="ok")`；kind ∈ {"image","video","voice","file"}; status ∈ {"ok","missing"}
  - `@dataclass Message(msg_id, ts, type, type_name, direction, sender, content, media=None, raw=None)`；`direction ∈ {"in","out"}`；`sender` 为 dict `{wxid,name,is_self}`

- [ ] **Step 1: 写失败测试**

`tests/test_message_model.py`：

```python
from wechat_export.message_model import Contact, Media, Message, Session


def test_session_to_dict():
    s = Session(id="wxid_a", name="张三", chat_type="single", member_count=2)
    assert s.to_dict() == {
        "id": "wxid_a", "name": "张三", "chat_type": "single", "member_count": 2,
    }


def test_message_to_dict_with_media():
    m = Message(
        msg_id="123", ts=1700000000000, type=3, type_name="图片",
        direction="in", sender={"wxid": "wxid_b", "name": "李四", "is_self": False},
        content="图片消息",
        media=Media(kind="image", md5="abc", ext="jpg",
                    rel_path="media/image/abc.jpg"),
    )
    d = m.to_dict()
    assert d["media"]["kind"] == "image"
    assert d["media"]["rel_path"] == "media/image/abc.jpg"
    assert d["sender"]["is_self"] is False


def test_message_without_media_raw_roundtrip():
    m = Message(
        msg_id="456", ts=1, type=49, type_name="文件",
        direction="out", sender={"wxid": "self", "name": "我", "is_self": True},
        content="文件消息", raw={"k": "v"},
    )
    d = m.to_dict()
    assert d["raw"] == {"k": "v"}
    assert d["media"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_message_model.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 message_model.py**

```python
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

    def to_dict(self) -> dict:
        return {"kind": self.kind, "md5": self.md5, "size": self.size,
                "ext": self.ext, "rel_path": self.rel_path, "status": self.status}


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

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id, "ts": self.ts, "type": self.type,
            "type_name": self.type_name, "direction": self.direction,
            "sender": self.sender, "content": self.content,
            "media": self.media.to_dict() if self.media else None,
            "raw": self.raw,
        }
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_message_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/message_model.py tests/test_message_model.py
git commit -m "feat: 统一消息模型"
```

---

### Task 4: 合成加密夹具库工厂（db_factory）

**Files:**
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/db_factory.py`
- Create: `tests/conftest.py`
- Test: `tests/test_db_factory.py`

**Interfaces:**
- Consumes: 无（独立于产品代码，仅依赖 sqlcipher3）
- Produces：
  - `PARAM_SET_DEFAULT = {"label": "sqlcipher4-default", "pragmas": "cipher_page_size=4096;kdf_iter=256000;cipher_hmac_algorithm=HMAC_SHA512"}`
  - `PARAM_SET_WX3 = {"label": "wx3-style", "pragmas": "cipher_page_size=4096;kdf_iter=64000;cipher_hmac_algorithm=HMAC_SHA1"}`
  - `def create_encrypted_db(path: Path, key_hex: str, pragmas: str) -> "connection"`：建库、设密钥与参数、创建 message 表并返回连接
  - `def make_message_schema() -> dict`：返回 `{"table": "message", "columns": {...}}`（与默认快照一致的列名字典，见 Task 8）
  - `def insert_message(conn, *, msg_id, ts, type_, content, is_sender, talker, subtype=0, **extra)`
  - `def close(conn)`

- [ ] **Step 1: 写失败测试**

`tests/test_db_factory.py`：

```python
import sqlite3

from tests.fixtures import db_factory as f

KEY = "a" * 64  # 32 字节 hex


def test_create_encrypted_db_and_query(tmp_path):
    db = tmp_path / "m.db"
    conn = f.create_encrypted_db(db, KEY, f.PARAM_SET_DEFAULT["pragmas"])
    conn.execute("CREATE TABLE message (id INTEGER PRIMARY KEY, content TEXT)")
    f.insert_message(conn, msg_id=1, ts=1700000000000, type_=1,
                     content="hello", is_sender=0, talker="wxid_b")
    cur = conn.execute("SELECT COUNT(*) FROM message")
    assert cur.fetchone()[0] == 1
    f.close(conn)

    # 重新以密钥打开验证持久化
    import sqlcipher3
    conn2 = sqlcipher3.connect(str(db))
    conn2.execute(f'PRAGMA key="x\'{KEY}\'"')
    conn2.execute("PRAGMA cipher_page_size=4096")
    conn2.execute("PRAGMA kdf_iter=256000")
    conn2.execute("PRAGMA cipher_hmac_algorithm=HMAC_SHA512")
    rows = conn2.execute("SELECT content FROM message").fetchall()
    assert rows == [("hello",)]
    conn2.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_db_factory.py -v`
Expected: FAIL, ImportError: cannot import name 'create_encrypted_db'

- [ ] **Step 3: 实现 db_factory.py**

```python
"""合成 SQLCipher 加密夹具库生成器——所有解密相关测试的基础设施。

建库时使用与真实库一致的调用模式（先 PRAGMA key，再设参数），
保证 Task 5 的参数枚举能解出这些库里创建的库。
"""

from pathlib import Path

import sqlcipher3

PARAM_SET_DEFAULT = {
    "label": "sqlcipher4-default",
    "pragmas": "cipher_page_size=4096;kdf_iter=256000;cipher_hmac_algorithm=HMAC_SHA512",
}
PARAM_SET_WX3 = {
    "label": "wx3-style",
    "pragmas": "cipher_page_size=4096;kdf_iter=64000;cipher_hmac_algorithm=HMAC_SHA1",
}

MSG_COLUMNS = {
    "id": "INTEGER PRIMARY KEY",
    "talker": "TEXT",
    "type": "INTEGER",
    "subtype": "INTEGER",
    "content": "TEXT",
    "createTime": "INTEGER",
    "isSender": "INTEGER",
    "status": "INTEGER",
}


def make_message_schema() -> dict:
    return {"table": "message", "columns": MSG_COLUMNS}


def create_encrypted_db(path: Path, key_hex: str, pragmas: str):
    conn = sqlcipher3.connect(str(path))
    conn.execute(f'PRAGMA key="x\'{key_hex}\'"')
    for p in pragmas.split(";"):
        conn.execute(f"PRAGMA {p}")
    cols = ", ".join(f"{k} {v}" for k, v in MSG_COLUMNS.items())
    conn.execute(f"CREATE TABLE message ({cols})")
    conn.commit()
    return conn


def insert_message(conn, *, msg_id: int, ts: int, type_: int, content: str,
                   is_sender: int, talker: str, subtype: int = 0,
                   status: int = 0):
    conn.execute(
        "INSERT INTO message (id, talker, type, subtype, content, createTime, isSender, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, talker, type_, subtype, content, ts, is_sender, status),
    )
    conn.commit()


def close(conn):
    conn.close()
```

`tests/conftest.py`：

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_db_factory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures tests/conftest.py tests/test_db_factory.py
git commit -m "test: 合成 SQLCipher 加密夹具库工厂"
```

---

### Task 5: SQLCipher 参数枚举与只读访问（db_access.py）

**Files:**
- Create: `wechat_export/db_access.py`
- Test: `tests/test_db_access.py`

**Interfaces:**
- Consumes: `tests.fixtures.db_factory`（仅测试用）；无产品代码依赖
- Produces：
  - `SQLCIPHER_PARAM_CANDIDATES: list[str]`——候选 pragma 串
  - `class EncryptedDb:` 构造 `(db_path: Path, key_hex: str)`；自动尝试候选参数打开；失败抛 `DecryptError`
    - `conn`：只读 sqlite 连接（`mode=ro`）
    - `params: str`：成功时使用的 pragma 串
    - `def query(sql, args=()) -> list[dict]`
    - `def tables() -> list[str]`
    - `def columns(table) -> list[str]`
  - `def open_encrypted(db_path, key_hex) -> EncryptedDb`
  - `def is_encrypted_db(db_path) -> bool`（读首 16 字节，"SQLite format 3" 头缺失则视为加密库；存在则报错——WeChat 4.x 库必加密）

- [ ] **Step 1: 写失败测试**

`tests/test_db_access.py`：

```python
import pytest

from tests.fixtures import db_factory as f
from wechat_export.db_access import EncryptedDb, open_encrypted
from wechat_export.exceptions import DecryptError

KEY = "b" * 64


@pytest.fixture
def encrypted_db(tmp_path):
    db = tmp_path / "message_0.db"
    conn = f.create_encrypted_db(db, KEY, f.PARAM_SET_DEFAULT["pragmas"])
    f.insert_message(conn, msg_id=1, ts=1700000000000, type_=1,
                     content="你好", is_sender=0, talker="wxid_b")
    f.close(conn)
    return db


def test_open_with_default_params(encrypted_db):
    edb = EncryptedDb(encrypted_db, KEY)
    assert "message" in edb.tables()
    rows = edb.query("SELECT content FROM message")
    assert rows[0]["content"] == "你好"
    edb.close()


def test_wrong_key_raises_decrypt_error(encrypted_db):
    with pytest.raises(DecryptError):
        open_encrypted(encrypted_db, "c" * 64)


def test_query_is_readonly(encrypted_db):
    edb = open_encrypted(encrypted_db, KEY)
    with pytest.raises(Exception):
        edb.query("CREATE TABLE x (a)")
    edb.close()


def test_columns(encrypted_db):
    edb = open_encrypted(encrypted_db, KEY)
    assert "content" in edb.columns("message")
    edb.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_db_access.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 db_access.py**

```python
"""SQLCipher 参数候选与只读访问。

对 Windows 微信 4.x：库文件已加密但具体参数未知，采用枚举验证法。
注意本机 sqlcipher3-binary 可能不可用，兼容 pysqlcipher3 导入。
"""

from itertools import product
from pathlib import Path

from wechat_export.exceptions import DecryptError

try:
    import sqlcipher3 as _sqlite
except ImportError:  # pragma: no cover
    try:
        import pysqlcipher3 as _sqlite
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "缺少 SQLCipher 绑定：请安装 sqlcipher3-binary 或 pysqlcipher3-binary"
        ) from e

_SQLITE_HEADER = b"SQLite format 3\x00"

_PAGE_SIZES = [4096, 1024, 2048, 8192, 512]
_KDF_ITERS = [256000, 64000, 1]
_HMACS = ["HMAC_SHA512", "HMAC_SHA1"]


def _build_candidates() -> list[str]:
    out = []
    for page, kdf, hmac in product(_PAGE_SIZES, _KDF_ITERS, _HMACS):
        out.append(
            f"cipher_page_size={page};kdf_iter={kdf};cipher_hmac_algorithm={hmac}"
        )
    return out


SQLCIPHER_PARAM_CANDIDATES = _build_candidates()


def is_encrypted_db(db_path: Path) -> bool:
    with open(db_path, "rb") as fp:
        head = fp.read(16)
    return head != _SQLITE_HEADER


class EncryptedDb:
    def __init__(self, db_path: Path, key_hex: str):
        if not db_path.exists():
            raise DecryptError(f"数据库文件不存在：{db_path}")
        if not is_encrypted_db(db_path):
            raise DecryptError(
                f"{db_path.name} 不是加密库（疑似明文 SQLite，或格式不受支持）",
                hint="请确认目标为微信 4.x 数据库",
            )
        self._db_path = db_path
        self._key_hex = key_hex
        self.params = ""
        self.conn = self._try_open()

    def _try_open(self):
        last_err = None
        for params in SQLCIPHER_PARAM_CANDIDATES:
            try:
                conn = _sqlite.connect(f"file:{self._db_path}?mode=ro", uri=True)
                conn.execute(f'PRAGMA key="x\'{self._key_hex}\'"')
                for p in params.split(";"):
                    conn.execute(f"PRAGMA {p}")
                conn.execute("PRAGMA quick_check")  # 触发真实读取，参数错误会抛异常
                conn.execute("SELECT COUNT(*) FROM sqlite_master")
                self.params = params
                return conn
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        raise DecryptError(
            f"无法解密数据库 {self._db_path.name}",
            hint=f"密钥可能错误或参数不匹配。最后错误：{last_err}",
        )

    def tables(self) -> list[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r["name"] for r in rows]

    def columns(self, table: str) -> list[str]:
        rows = self.query(f'PRAGMA table_info("{table}")')
        return [r["name"] for r in rows]

    def query(self, sql: str, args=()) -> list[dict]:
        cur = self.conn.execute(sql, args)
        return [dict(zip([d[0] for d in cur.description], row))
                for row in cur.fetchall()]

    def close(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


def open_encrypted(db_path: Path, key_hex: str) -> EncryptedDb:
    return EncryptedDb(db_path, key_hex)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_db_access.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/db_access.py tests/test_db_access.py
git commit -m "feat: SQLCipher 参数枚举与只读访问"
```

---

### Task 6: 数据定位（locator.py）

**Files:**
- Create: `wechat_export/locator.py`
- Test: `tests/test_locator.py`

**Interfaces:**
- Consumes: `wechat_export.exceptions.WeChatNotFoundError`
- Produces：
  - `DATA_DIR_NAMES = ["WeChat Files", "xwechat_files", "WeChat Files (x64)"]`
  - `DB_STORAGE_NAMES = ["db_storage"]`
  - `@dataclass AccountInfo(wxid: str, root: Path, db_storage: Path | None, db_files: list[Path])`
  - `def user_documents_dir() -> Path`
  - `def candidate_data_roots(override: Path | None) -> list[Path]`：`--data-dir` 覆盖 + 文档目录 + 每个盘符根目录下的 `DATA_DIR_NAMES` 目录
  - `def scan_accounts(data_root: Path) -> list[str]`：数据根下形如 `wxid_*` 的目录名
  - `def locate_data(override: Path | None) -> list[AccountInfo]`：返回所有账号；找不到抛 `WeChatNotFoundError`
  - `def message_db_files(db_storage: Path) -> list[Path]`：`message_*.db` 按文件名排序

- [ ] **Step 1: 写失败测试**

`tests/test_locator.py`：

```python
from pathlib import Path

import pytest

from wechat_export.exceptions import WeChatNotFoundError
from wechat_export.locator import (
    locate_data, message_db_files, scan_accounts,
)


def _fake_data_root(tmp_path) -> Path:
    root = tmp_path / "data"
    acc = root / "wxid_abc"
    (acc / "db_storage" / "message").mkdir(parents=True)
    (acc / "db_storage" / "message" / "message_0.db").write_bytes(b"x")
    (acc / "db_storage" / "message" / "message_1.db").write_bytes(b"x")
    return root


def test_scan_accounts(tmp_path):
    root = _fake_data_root(tmp_path)
    (root / "not_an_account").mkdir()
    assert scan_accounts(root) == ["wxid_abc"]


def test_message_db_files_sorted(tmp_path):
    root = _fake_data_root(tmp_path)
    acc = root / "wxid_abc"
    files = message_db_files(acc / "db_storage")
    assert [f.name for f in files] == ["message_0.db", "message_1.db"]


def test_locate_data_with_override(tmp_path):
    root = _fake_data_root(tmp_path)
    infos = locate_data(override=root)
    assert [i.wxid for i in infos] == ["wxid_abc"]
    assert infos[0].db_storage.name == "db_storage"


def test_locate_data_not_found(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    # 固定候选根，避免扫到开发机上真实的微信数据目录
    monkeypatch.setattr("wechat_export.locator.candidate_data_roots",
                        lambda override: [empty])
    with pytest.raises(WeChatNotFoundError):
        locate_data(override=empty)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_locator.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 locator.py**

```python
"""定位微信数据目录与账号。

4.x 数据根目录名存在多个候选（社区在 4.0 中发现 xwechat_files 等命名），
因此枚举候选名；`--data-dir` 覆盖放第一优先级。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from wechat_export.exceptions import WeChatNotFoundError

DATA_DIR_NAMES = ["WeChat Files", "xwechat_files", "WeChat Files (x64)"]
DB_STORAGE_NAMES = ["db_storage"]


@dataclass
class AccountInfo:
    wxid: str
    root: Path
    db_storage: Path | None = None
    db_files: list[Path] = field(default_factory=list)


def user_documents_dir() -> Path:
    home = Path.home()
    for cand in (home / "Documents", home / "文档"):
        if cand.is_dir():
            return cand
    return home


def _drive_roots() -> list[Path]:
    roots = []
    for letter in "CDEFG":
        p = Path(f"{letter}:\\")
        if p.exists():
            roots.append(p)
    return roots


def candidate_data_roots(override: Path | None) -> list[Path]:
    cands: list[Path] = []
    if override:
        cands.append(Path(override))
    base_dirs = [user_documents_dir(), *[d for d in _drive_roots() if d != user_documents_dir().anchor]]
    for base in base_dirs:
        for name in DATA_DIR_NAMES:
            cands.append(base / name)
    return cands


def scan_accounts(data_root: Path) -> list[str]:
    if not data_root.is_dir():
        return []
    return sorted(
        p.name for p in data_root.iterdir()
        if p.is_dir() and p.name.startswith("wxid_")
    )


def _find_db_storage(account_dir: Path) -> Path | None:
    for name in DB_STORAGE_NAMES:
        cand = account_dir / name
        if cand.is_dir():
            return cand
    # 兜底：递归一层寻找 db_storage
    for sub in account_dir.iterdir():
        if sub.is_dir():
            for name in DB_STORAGE_NAMES:
                cand = sub / name
                if cand.is_dir():
                    return cand
    return None


def message_db_files(db_storage: Path) -> list[Path]:
    message_dir = db_storage / "message"
    if message_dir.is_dir():
        files = sorted(message_dir.glob("message_*.db"))
        return [f for f in files if f.suffix == ".db"]
    return sorted(db_storage.glob("message_*.db"))


def locate_data(override: Path | None) -> list[AccountInfo]:
    infos: list[AccountInfo] = []
    seen: set[Path] = set()
    for root in candidate_data_roots(override):
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        for wxid in scan_accounts(root):
            acc_dir = root / wxid
            db_storage = _find_db_storage(acc_dir)
            infos.append(AccountInfo(
                wxid=wxid, root=root, db_storage=db_storage,
                db_files=message_db_files(db_storage) if db_storage else [],
            ))
    if not infos:
        raise WeChatNotFoundError(
            "未找到微信数据目录",
            hint="请确认已安装并登录微信 4.x，或用 --data-dir 手动指定数据根目录",
        )
    return infos
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_locator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/locator.py tests/test_locator.py
git commit -m "feat: 数据目录/账号定位"
```

---

### Task 7: 密钥提取（key_provider.py）

**Files:**
- Create: `wechat_export/key_provider.py`
- Test: `tests/test_key_provider.py`

**Interfaces:**
- Consumes: `db_access.open_encrypted`、`exceptions`、`locator.AccountInfo`
- Produces：
  - `def parse_key_hex(s: str) -> str`：接受 `64` 位 hex（可带 `0x`/`x'...'`/空格），非法抛 `ConfigError`
  - `def extract_key_from_dump(dump_bytes: bytes, candidates: list[str], db_path) -> str | None`：在 dump 中滑窗扫描高熵 32 字节序列并尝试打开库
  - `def collect_dump_candidates(dump_bytes: bytes, db_path: Path) -> list[str]`（导出以便测试）
  - `def mini_dump_process(pid: int, out_path: Path) -> Path`：ctypes 调 dbghelp.dll MiniDumpWriteDump；失败抛 `KeyExtractError`（中文提示：可能需要管理员权限）
  - `class KeyProvider: __init__(self, db_path: Path, manual_key: str | None = None)`；`def get_key() -> str`：依次尝试 手动 → 本地落盘（M0 确认后补充，见 docstring）→ 进程内存 → 抛 `KeyExtractError`

- [ ] **Step 1: 写失败测试**

`tests/test_key_provider.py`：

```python
import pytest
from tests.fixtures import db_factory as f
from wechat_export.exceptions import ConfigError, KeyExtractError
from wechat_export.key_provider import (
    KeyProvider, collect_dump_candidates, extract_key_from_dump, parse_key_hex,
)

KEY = "d" * 64


@pytest.mark.parametrize("bad", ["", "xyz", "a" * 63, "a" * 65, "a" * 32])
def test_parse_key_hex_bad(bad):
    with pytest.raises(ConfigError):
        parse_key_hex(bad)


@pytest.mark.parametrize("good", ["ab" * 32, "0x" + "cd" * 32, "x'ef" * 32 + "'"])
def test_parse_key_hex_good(good):
    assert len(parse_key_hex(good)) == 64


def test_manual_key_provider(tmp_path):
    db = tmp_path / "m.db"
    conn = f.create_encrypted_db(db, KEY, f.PARAM_SET_DEFAULT["pragmas"])
    f.close(conn)
    kp = KeyProvider(db, manual_key=KEY)
    assert kp.get_key() == KEY


# 高熵 32 字节密钥（内存扫描测试用；真实密钥为随机字节）
KEY_ENTROPY = bytes(range(32)).hex()


def test_dump_candidates_found(tmp_path):
    db = tmp_path / "m.db"
    conn = f.create_encrypted_db(db, KEY_ENTROPY, f.PARAM_SET_DEFAULT["pragmas"])
    f.close(conn)
    blob = b"junk" * 1000 + bytes.fromhex(KEY_ENTROPY) + b"tail" * 100
    found = extract_key_from_dump(blob, db)
    assert found == KEY_ENTROPY


def test_dump_candidates_not_found(tmp_path):
    db = tmp_path / "m.db"
    conn = f.create_encrypted_db(db, KEY_ENTROPY, f.PARAM_SET_DEFAULT["pragmas"])
    f.close(conn)
    # 低熵明文无法通过高熵过滤，应快速返回 None
    assert extract_key_from_dump(b"no key here " * 50, db) is None


def test_no_key_raises(tmp_path):
    db = tmp_path / "m.db"
    conn = f.create_encrypted_db(db, KEY, f.PARAM_SET_DEFAULT["pragmas"])
    f.close(conn)
    kp = KeyProvider(db, manual_key=None)
    with pytest.raises(KeyExtractError):
        kp.get_key()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_key_provider.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 key_provider.py**

```python
"""数据库密钥提取：手动输入 → 本地落盘 → 进程内存，逐级兜底。

Windows 微信 4.x 密钥机制经 M0 实测确认；本地落盘来源的具体路径
在 M0 完成后以模块级常量补充（当前留空并给出探测指引）。
"""

import ctypes
import os
from pathlib import Path

from wechat_export import db_access
from wechat_export.exceptions import ConfigError, KeyExtractError
from wechat_export.locator import AccountInfo

KEY_BYTES = 32

# M0 实测后在此登记本地落盘密钥候选路径（相对于账号数据根目录或 db_storage）
LOCAL_KEY_CANDIDATE_RELPATHS: list[tuple[Path, Path]] = []  # (root, relpath)


def parse_key_hex(s: str) -> str:
    s = s.strip()
    if s.startswith("x'") and s.endswith("'"):
        s = s[2:-1]
    if s.lower().startswith("0x"):
        s = s[2:]
    s = s.replace(" ", "").replace(":", "")
    if len(s) != KEY_BYTES * 2 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ConfigError(
            f"密钥格式错误：需要 {KEY_BYTES * 2} 位 16 进制字符",
            hint="示例：--key-hex <32字节hex>；可从微信进程内存 dump 或已知方案获取",
        )
    return s.lower()


def collect_dump_candidates(dump_bytes: bytes, limit: int = 20000) -> list[bytes]:
    """滑窗扫描 32 字节候选密钥：跳过低熵窗口（明文/重复字节段），
    按出现顺序去重，最多取 limit 个，交由真实解密验证过滤。"""
    cands: list[bytes] = []
    seen: set[bytes] = set()
    for i in range(0, max(len(dump_bytes) - KEY_BYTES, 0)):
        chunk = dump_bytes[i:i + KEY_BYTES]
        if len(set(chunk)) < 16:  # 随机密钥通常 32 字节几乎全不同
            continue
        if chunk in seen:
            continue
        seen.add(chunk)
        cands.append(chunk)
        if len(cands) >= limit:
            break
    return cands


def extract_key_from_dump(dump_bytes: bytes, db_path: Path) -> str | None:
    """对候选逐一用真实解密验证；成功即返回，失败继续。"""
    for cand in collect_dump_candidates(dump_bytes):
        key = cand.hex()
        try:
            edb = db_access.open_encrypted(db_path, key)
            edb.close()
            return key
        except Exception:  # noqa: BLE001
            continue
    return None


def mini_dump_process(pid: int, out_path: Path) -> Path:
    """对指定进程做 MiniDump（dbghelp）。失败多为权限不足。
    MiniDumpWriteDump 需要 Win32 文件句柄（CreateFileW），不能用 Python fd。"""
    if os.name != "nt":
        raise KeyExtractError("进程内存提取仅支持 Windows", hint="当前系统非 Windows")
    MiniDumpWithFullMemory = 0x00000002
    dbghelp = ctypes.WinDLL("dbghelp")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    ph = kernel32.OpenProcess(0x1F0FFF, False, pid)
    if not ph:
        raise KeyExtractError(
            f"无法打开进程 pid={pid}（可能权限不足）",
            hint="请以管理员身份运行本工具，或改用 --key-hex 手动提供密钥",
        )
    try:
        fh = kernel32.CreateFileW(str(out_path), 0x40000000, 0, None, 2, 0, None)
        if not fh or fh == ctypes.c_void_p(-1).value:
            raise KeyExtractError("无法创建 dump 临时文件",
                                  hint="检查 TEMP 目录写入权限")
        try:
            ok = dbghelp.MiniDumpWriteDump(
                ph, pid, fh, MiniDumpWithFullMemory, None, None, None
            )
        finally:
            kernel32.CloseHandle(fh)
        if not ok:
            raise KeyExtractError(
                "MiniDump 失败（可能权限不足）",
                hint="请以管理员身份运行，或改用 --key-hex",
            )
    finally:
        kernel32.CloseHandle(ph)
    return out_path


def find_weixin_pid() -> int:
    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    ).stdout
    for line in out.splitlines():
        parts = line.split('","')
        if len(parts) >= 2 and parts[0].strip('"') == "Weixin.exe":
            return int(parts[1].strip('"'))
    raise KeyExtractError("未发现运行中的 Weixin.exe 进程", hint="请先启动并登录微信 4.x")


class KeyProvider:
    def __init__(self, db_path: Path, manual_key: str | None = None,
                 account: "AccountInfo | None" = None):
        self.db_path = db_path
        self.manual_key = parse_key_hex(manual_key) if manual_key else None
        self.account = account

    def get_key(self) -> str:
        if self.manual_key:
            self._verify(self.manual_key)
            return self.manual_key
        for root, rel in LOCAL_KEY_CANDIDATE_RELPATHS:
            base = self.account.root if self.account else Path.cwd()
            p = base / rel
            if p.exists():
                key = self._parse_local_key(p.read_text())
                if key and self._verify(key):
                    return key
        pid = find_weixin_pid()
        tmp = Path(os.environ.get("TEMP", ".")) / f"weixin_{pid}.dmp"
        try:
            mini_dump_process(pid, tmp)
            dump = tmp.read_bytes()
        finally:
            tmp.unlink(missing_ok=True)
        key = extract_key_from_dump(dump, self.db_path)
        if key:
            return key
        raise KeyExtractError(
            "未能从本地文件与内存提取到可用密钥",
            hint="请用 --key-hex 手动提供 32 字节密钥",
        )

    def _verify(self, key: str) -> bool:
        try:
            edb = db_access.open_encrypted(self.db_path, key)
            edb.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _parse_local_key(self, text: str) -> str | None:
        stripped = text.strip().strip("'\"")
        try:
            return parse_key_hex(stripped)
        except ConfigError:
            return None
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_key_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/key_provider.py tests/test_key_provider.py
git commit -m "feat: 密钥提取（手动/本地/内存三级兜底）"
```

---

### Task 8: Schema 快照与映射（schema.py + 默认快照）

**Files:**
- Create: `wechat_export/schema.py`
- Create: `wechat_export/schema_snapshots/message_0.schema.json`
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: `db_access.EncryptedDb.columns/tables`
- Produces：
  - `@dataclass SchemaInfo(message_table: str, columns: list[str], msg_id: str, talker: str, type: str, subtype: str, content: str, create_time: str, is_sender: str, status: str)`
  - `DEFAULT_SCHEMA: SchemaInfo`（列名与 Task 4 工厂一致）
  - `def load_schema(snapshot: dict | None) -> SchemaInfo`：从快照 dict 构造；缺省字段回落 DEFAULT_SCHEMA
  - `def snapshot_db(edb: EncryptedDb) -> dict`：dump 表 list + message 表列名（probe 工具用）

- [ ] **Step 1: 写失败测试**

`tests/test_schema.py`：

```python
from wechat_export.schema import DEFAULT_SCHEMA, SchemaInfo, load_schema, snapshot_db
from tests.fixtures import db_factory as f
from wechat_export.db_access import open_encrypted

KEY = "e" * 64


def test_default_schema_fields():
    assert DEFAULT_SCHEMA.message_table == "message"
    assert DEFAULT_SCHEMA.content == "content"
    assert DEFAULT_SCHEMA.is_sender == "isSender"


def test_load_schema_with_snapshot():
    info = load_schema({"table": "message", "msg_id": "IdNew"})
    assert info.msg_id == "IdNew"
    assert info.is_sender == "isSender"  # 未提供字段回落默认


def test_snapshot_db(tmp_path):
    db = tmp_path / "m.db"
    conn = f.create_encrypted_db(db, KEY, f.PARAM_SET_DEFAULT["pragmas"])
    f.insert_message(conn, msg_id=1, ts=1, type_=1, content="x",
                     is_sender=0, talker="t")
    f.close(conn)
    edb = open_encrypted(db, KEY)
    snap = snapshot_db(edb)
    edb.close()
    assert "message" in snap["tables"]
    assert "content" in snap["columns"]["message"]
    assert isinstance(DEFAULT_SCHEMA, SchemaInfo)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_schema.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 schema.py 与默认快照**

`wechat_export/schema.py`：

```python
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
```

`wechat_export/schema_snapshots/message_0.schema.json`（默认快照，M0 实测后替换）：

```json
{
  "table": "message",
  "columns": {
    "message": ["id", "talker", "type", "subtype", "content", "createTime", "isSender", "status"]
  },
  "msg_id": "id",
  "talker": "talker",
  "type": "type",
  "subtype": "subtype",
  "content": "content",
  "create_time": "createTime",
  "is_sender": "isSender",
  "status": "status"
}
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/schema.py wechat_export/schema_snapshots/message_0.schema.json tests/test_schema.py
git commit -m "feat: schema 快照与映射（默认 + 实测回落）"
```

---

### Task 9: 消息解析（parser.py）

**Files:**
- Create: `wechat_export/parser.py`
- Test: `tests/test_parser.py`

**Interfaces:**
- Consumes: `message_model.Message/Media`、`schema.SchemaInfo`、`exceptions`（不用异常，失败返回带 raw 的兜底 Message）
- Produces：
  - `TYPE_NAMES: dict[int, str] = {1:"文本", 3:"图片", 34:"语音", 43:"视频", 49:"文件/卡片", 10000:"系统消息"}`
  - `def parse_message_row(row: dict, schema: SchemaInfo, self_wxid: str, session_id: str) -> Message`：direction 由 `is_sender` 判断；sender name 暂用 wxid（联系人解析在 cli 层增强）；未识别类型保留 raw 兜底
  - `def parse_media_from_content(content: str, type_: int) -> Media | None`：解析 3.x/4.x 通用 XML 描述（`<img>`/`<voicemsg>`/`<videomsg>`/`<appattach>`），解析失败返回 None
  - `def resolve_kind_and_ext(type_: int) -> tuple[str, str]`

- [ ] **Step 1: 写失败测试**

`tests/test_parser.py`：

```python
from wechat_export.message_model import Media
from wechat_export.parser import (
    TYPE_NAMES, parse_media_from_content, parse_message_row,
)
from wechat_export.schema import DEFAULT_SCHEMA

SELF = "wxid_self"


def _row(**kw):
    base = dict(id=1, talker="wxid_b", type=1, subtype=0, content="hi",
                createTime=1700000000000, isSender=0, status=0)
    base.update(kw)
    return base


def test_text_row_in():
    m = parse_message_row(_row(), DEFAULT_SCHEMA, SELF, "wxid_b")
    assert m.type_name == "文本"
    assert m.direction == "in"
    assert m.content == "hi"
    assert m.sender["is_self"] is False
    assert m.media is None


def test_text_row_out():
    m = parse_message_row(_row(isSender=1), DEFAULT_SCHEMA, SELF, "wxid_b")
    assert m.direction == "out"
    assert m.sender["is_self"] is True
    assert m.sender["wxid"] == SELF


def test_unknown_type_fallback_raw():
    m = parse_message_row(_row(type=9999, content="???"), DEFAULT_SCHEMA, SELF, "wxid_b")
    assert m.raw["type"] == 9999
    assert m.type_name.startswith("未知")


def test_parse_image_media():
    content = '<msg><img h="100" w="200" md5="abc123"/></msg>'
    media = parse_media_from_content(content, 3)
    assert media is not None
    assert media.kind == "image"
    assert media.md5 == "abc123"


def test_parse_voice_media():
    content = '<msg><voicemsg voicelength="3000" /></msg>'
    media = parse_media_from_content(content, 34)
    assert media.kind == "voice"
    assert media.ext == ".amr"
    assert media.md5 == ""


def test_parse_bad_content_none():
    assert parse_media_from_content("not xml at all", 1) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 parser.py**

```python
"""DB 原始行 → 统一消息模型。

媒体二进制定位（库内 blob / FileStorage 文件）依赖 M0 实测结果，
本阶段先从 content 的 XML 描述中解析出 md5/时长/文件名等元数据；
实际媒体归档在 exporter 层由 media resolver 填补（见 Task 10 接口）。
"""

import re
import xml.etree.ElementTree as ET

from wechat_export.message_model import Media, Message
from wechat_export.schema import SchemaInfo

TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频",
    49: "文件/卡片", 10000: "系统消息",
}

_MEDIA_EXT = {"image": ".jpg", "video": ".mp4", "voice": ".amr", "file": ".dat"}


def resolve_kind_and_ext(type_: int) -> tuple[str, str] | None:
    kind_map = {3: "image", 34: "voice", 43: "video", 49: "file"}
    kind = kind_map.get(type_)
    if not kind:
        return None
    return kind, _MEDIA_EXT[kind]


def _safe_xml(content: str) -> ET.Element | None:
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None


def parse_media_from_content(content: str, type_: int) -> Media | None:
    kind_ext = resolve_kind_and_ext(type_)
    if not kind_ext:
        return None
    kind, ext = kind_ext
    root = _safe_xml(content)
    if root is None:
        return None
    md5 = ""
    size = 0
    if kind == "image":
        el = root.find("img")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = ".jpg"
    elif kind == "voice":
        el = root.find("voicemsg")
        if el is None:
            return None
        size = int(el.get("voicelength", 0) or 0)
        ext = ".amr"
    elif kind == "video":
        el = root.find("videomsg")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = ".mp4"
    elif kind == "file":
        el = root.find(".//appattach")
        if el is None:
            return None
        md5 = el.get("md5", "")
        ext = "." + (el.get("fileext", "dat") or "dat")
    if not md5 and kind != "voice":
        return None
    return Media(kind=kind, md5=md5, size=size, ext=ext)


def parse_message_row(row: dict, schema: SchemaInfo, self_wxid: str, session_id: str) -> Message:
    is_sender = int(row.get(schema.is_sender, 0) or 0) == 1
    msg_type = int(row.get(schema.type, 0) or 0)
    content = str(row.get(schema.content, "") or "")
    msg_id = str(row.get(schema.msg_id) or f"{session_id}-{row.get(schema.create_time)}")
    ts = int(row.get(schema.create_time, 0) or 0)
    talker = str(row.get(schema.talker, "") or session_id)

    sender = {
        "wxid": self_wxid if is_sender else talker,
        "name": "我" if is_sender else talker,
        "is_self": is_sender,
    }
    media = parse_media_from_content(content, msg_type)
    type_name = TYPE_NAMES.get(msg_type, f"未知({msg_type})")
    raw = None if type_name in TYPE_NAMES else dict(row)
    return Message(
        msg_id=msg_id, ts=ts, type=msg_type, type_name=type_name,
        direction="out" if is_sender else "in",
        sender=sender, content=content or "",
        media=media, raw=raw,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_parser.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/parser.py tests/test_parser.py
git commit -m "feat: 消息行解析为统一模型"
```

---

### Task 10: 媒体归档（media_archive.py）

**Files:**
- Create: `wechat_export/exporter/__init__.py`
- Create: `wechat_export/exporter/media_archive.py`
- Test: `tests/test_media_archive.py`

**Interfaces:**
- Consumes: `message_model.Media`、`exceptions`（不抛——写失败记录到 stats）
- Produces：
  - `KIND_DIRS = {"image": "image", "video": "video", "voice": "voice", "file": "file"}`
  - `class MediaArchive:`
    - `__init__(self, media_root: Path)`
    - `def save_bytes(self, data: bytes, kind: str, ext: str, md5: str = "") -> Media`：md5 去重、临时文件 + 原子改名；写失败返回 status="missing" 的 Media
    - `def save_file(self, src: Path, kind: str, ext: str, md5: str = "") -> Media`：复制语义同 save_bytes
    - `stats -> dict`：`{"saved": int, "duplicated": int, "missing": int}`
  - `def placeholder_media(media: Media) -> Media`：status="missing"

- [ ] **Step 1: 写失败测试**

`tests/test_media_archive.py`：

```python
from wechat_export.exporter.media_archive import KIND_DIRS, MediaArchive


def test_save_bytes_dedup(tmp_path):
    arc = MediaArchive(tmp_path / "media")
    m1 = arc.save_bytes(b"aaa", "image", ".jpg", md5="m1")
    m2 = arc.save_bytes(b"aaa", "image", ".jpg", md5="m1")
    assert m1.status == "ok"
    assert m1.rel_path.startswith("image/")
    assert (tmp_path / "media" / m1.rel_path).exists()
    assert m2.rel_path == m1.rel_path
    s = arc.stats
    assert s["saved"] == 1 and s["duplicated"] == 1 and s["missing"] == 0


def test_save_bytes_different_md5(tmp_path):
    arc = MediaArchive(tmp_path / "media")
    m1 = arc.save_bytes(b"aaa", "image", ".jpg", md5="m1")
    m2 = arc.save_bytes(b"bbb", "image", ".jpg", md5="m2")
    assert m1.rel_path != m2.rel_path
    assert arc.stats["saved"] == 2


def test_kind_dirs():
    assert KIND_DIRS == {"image": "image", "video": "video", "voice": "voice", "file": "file"}


def test_missing_source(tmp_path):
    arc = MediaArchive(tmp_path / "media")
    m = arc.save_file(tmp_path / "nope.jpg", "image", ".jpg", md5="x")
    assert m.status == "missing"
    assert arc.stats["missing"] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_media_archive.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 exporter 包与 media_archive.py**

`wechat_export/exporter/__init__.py`：空文件。

`wechat_export/exporter/media_archive.py`：

```python
"""媒体文件归档：md5 去重、按类型分目录、临时文件 + 原子改名。"""

import hashlib
import shutil
from pathlib import Path

from wechat_export.message_model import Media

KIND_DIRS = {"image": "image", "video": "video", "voice": "voice", "file": "file"}


def _md5_of_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def placeholder_media(media: Media) -> Media:
    media.status = "missing"
    return media


class MediaArchive:
    def __init__(self, media_root: Path):
        self.media_root = Path(media_root)
        self._saved: set[str] = set()
        self.stats = {"saved": 0, "duplicated": 0, "missing": 0}

    def _store(self, data: bytes, kind: str, ext: str, md5: str) -> Media | None:
        kind_dir = KIND_DIRS.get(kind)
        if not kind_dir:
            return None
        digest = md5 or _md5_of_bytes(data)
        if digest in self._saved:
            self.stats["duplicated"] += 1
            return Media(kind=kind, md5=digest, size=len(data), ext=ext,
                         rel_path=f"{kind_dir}/{digest}{ext}")
        target_dir = self.media_root / kind_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp = target_dir / f".tmp_{digest}"
        try:
            tmp.write_bytes(data)
            target = target_dir / f"{digest}{ext}"
            if not target.exists():
                tmp.replace(target)
            else:
                tmp.unlink(missing_ok=True)
        except OSError:
            tmp.unlink(missing_ok=True)
            return None
        self._saved.add(digest)
        self.stats["saved"] += 1
        return Media(kind=kind, md5=digest, size=len(data), ext=ext,
                     rel_path=f"{kind_dir}/{digest}{ext}")

    def save_bytes(self, data: bytes, kind: str, ext: str, md5: str = "") -> Media:
        media = self._store(data, kind, ext, md5)
        if media is None:
            self.stats["missing"] += 1
            return placeholder_media(Media(kind=kind, md5=md5, ext=ext))
        return media

    def save_file(self, src: Path, kind: str, ext: str, md5: str = "") -> Media:
        if not src.exists():
            self.stats["missing"] += 1
            return placeholder_media(Media(kind=kind, md5=md5, ext=ext))
        data = src.read_bytes()
        return self.save_bytes(data, kind, ext, md5)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_media_archive.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/exporter tests/test_media_archive.py
git commit -m "feat: 媒体 md5 去重归档"
```

---

### Task 11: JSON 写出（json_writer.py）

**Files:**
- Create: `wechat_export/exporter/json_writer.py`
- Test: `tests/test_json_writer.py`

**Interfaces:**
- Consumes: `message_model.Session/Message`
- Produces：
  - `CHUNK_SIZE = 5000`
  - `def write_session_messages(dir: Path, session: Session, messages: list[Message]) -> int`：写 `messages.json`（超量分块 `messages_000N.json`），返回文件数，并写 `.done` 标记（供 `--resume`）
  - `def write_session_json(dir: Path, session: Session, counts: dict)`
  - `def write_export_meta(out_root: Path, meta: dict)`
  - `def write_sessions_index(out_root: Path, sessions: list[Session], counts: dict)`

- [ ] **Step 1: 写失败测试**

`tests/test_json_writer.py`：

```python
import json

from wechat_export.exporter.json_writer import (
    CHUNK_SIZE, write_export_meta, write_session_json, write_session_messages,
    write_sessions_index,
)
from wechat_export.message_model import Message, Session


def _msg(i: int) -> Message:
    return Message(msg_id=str(i), ts=i, type=1, type_name="文本",
                   direction="in",
                   sender={"wxid": "wxid_b", "name": "李四", "is_self": False},
                   content=f"msg{i}")


def test_write_session_messages_chunk(tmp_path):
    sess = Session(id="wxid_b", name="李四")
    files = write_session_messages(tmp_path, sess, [_msg(i) for i in range(CHUNK_SIZE + 10)])
    assert files == 2
    assert (tmp_path / "messages.json").exists()
    assert (tmp_path / "messages_0002.json").exists()
    data = json.loads((tmp_path / "messages.json").read_text(encoding="utf-8"))
    assert data["session"]["name"] == "李四"
    assert len(data["messages"]) == CHUNK_SIZE
    assert (tmp_path / ".done").exists()


def test_write_export_meta(tmp_path):
    write_export_meta(tmp_path, {"account": "wxid_abc", "stats": {}})
    meta = json.loads((tmp_path / "export_meta.json").read_text(encoding="utf-8"))
    assert meta["account"] == "wxid_abc"


def test_write_session_json(tmp_path):
    write_session_json(tmp_path, Session(id="x", name="群", chat_type="group"), {"messages": 5})
    d = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert d["name"] == "群" and d["stats"]["messages"] == 5


def test_write_sessions_index(tmp_path):
    write_sessions_index(tmp_path, [Session(id="a", name="A")], {"a": 3})
    d = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert d["sessions"][0]["name"] == "A"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_json_writer.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 json_writer.py**

```python
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
               {"session": session.to_dict(), "stats": counts})


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
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_json_writer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/exporter/json_writer.py tests/test_json_writer.py
git commit -m "feat: 结构化 JSON 写出（分块 + 元信息 + 断点标记）"
```

---

### Task 12: HTML 渲染（html_renderer.py + 模板）

**Files:**
- Create: `wechat_export/exporter/html_renderer.py`
- Create: `wechat_export/exporter/templates/base.html.j2`
- Create: `wechat_export/exporter/templates/index.html.j2`
- Create: `wechat_export/exporter/templates/session.html.j2`
- Test: `tests/test_html_renderer.py`

**Interfaces:**
- Consumes: `message_model.Session/Message/Media`
- Produces：
  - `class HtmlRenderer:`
    - `__init__(self, templates_dir: Path | None = None)`
    - `def render_index(self, out_root: Path, sessions: list[Session], counts: dict, meta: dict) -> None`：写 `index.html`
    - `def render_session(self, out_dir: Path, session: Session, messages: list[Message], counts: dict) -> None`：写 `index.html`（按天分组、气泡、媒体内嵌、零 CDN）
  - `def group_by_day(messages: list[Message]) -> list[tuple[str, list[Message]]]`

- [ ] **Step 1: 写失败测试**

`tests/test_html_renderer.py`：

```python
import re

from wechat_export.exporter.html_renderer import HtmlRenderer, group_by_day
from wechat_export.message_model import Media, Message, Session


def _msg(i: int, ts: int, type_: int = 1, content: str = "hi",
         media: Media | None = None) -> Message:
    return Message(msg_id=str(i), ts=ts, type=type_, type_name="文本",
                   direction="in" if i % 2 else "out",
                   sender={"wxid": "wxid_b", "name": "李四", "is_self": i % 2 == 0},
                   content=content, media=media)


def test_group_by_day():
    msgs = [_msg(1, 1700000000000), _msg(2, 1700000000000 + 86400000)]
    days = group_by_day(msgs)
    assert len(days) == 2
    assert days[0][0] != days[1][0]


def test_render_index_and_session(tmp_path):
    renderer = HtmlRenderer()
    sessions = [Session(id="wxid_b", name="李四"), Session(id="g1", name="群聊", chat_type="group")]
    renderer.render_index(tmp_path, sessions, {"wxid_b": 3, "g1": 5}, {"account": "wxid_abc"})
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "李四" in html and "wxid_abc" in html
    assert "http://" not in html and "https://" not in html  # 零 CDN

    out = tmp_path / "wxid_b"
    msgs = [
        _msg(1, 1700000000000, 3, "图片", Media(kind="image", rel_path="media/image/a.jpg")),
        _msg(2, 1700001000000, 34, "语音", Media(kind="voice", rel_path="media/voice/a.amr")),
    ]
    renderer.render_session(out, sessions[0], msgs, {"messages": 2})
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "media/image/a.jpg" in html
    assert '<audio' in html
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_html_renderer.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 html_renderer.py 与模板**

`wechat_export/exporter/html_renderer.py`：

```python
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
```

`templates/base.html.j2`：

```jinja
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}聊天记录导出{% endblock %}</title>
<style>
  :root{
    --bg:#f5f5f5; --card:#fff; --ink:#222; --muted:#888;
    --bubble-in:#fff; --bubble-out:#95ec69; --accent:#07c160; --border:#e5e5e5;
  }
  [data-theme="dark"]{
    --bg:#1a1a1a; --card:#242424; --ink:#eee; --muted:#999;
    --bubble-in:#333; --bubble-out:#3a7d44; --accent:#07c160; --border:#333;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:"Microsoft YaHei",system-ui,sans-serif}
  header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--border);
         padding:10px 16px;display:flex;justify-content:space-between;align-items:center}
  .btn{cursor:pointer;border:1px solid var(--border);background:var(--card);
       color:var(--ink);padding:4px 10px;border-radius:6px}
  main{max-width:900px;margin:0 auto;padding:16px}
  .muted{color:var(--muted);font-size:13px}
  img{max-width:100%}
  video,audio{max-width:100%}
</style>
</head>
<body data-theme="light">
<header>
  <strong>{% block head_title %}聊天记录导出{% endblock %}</strong>
  <button class="btn" onclick="toggleTheme()">切换主题</button>
</header>
<main>{% block content %}{% endblock %}</main>
<script>
function toggleTheme(){
  const b=document.body;
  b.dataset.theme = b.dataset.theme==="dark" ? "light" : "dark";
}
function zoom(el){ el.style.maxWidth = el.style.maxWidth ? "" : "none"; }
</script>
</body>
</html>
```

`templates/index.html.j2`：

```jinja
{% extends "base.html.j2" %}
{% block title %}会话列表 · 聊天记录导出{% endblock %}
{% block head_title %}聊天记录导出{% endblock %}
{% block content %}
<p class="muted">账号：{{ meta.account }} · 导出时间：{{ meta.created_at }} · 会话 {{ sessions|length }} 个 · 消息 {{ total_messages }} 条</p>
<table style="width:100%;border-collapse:collapse">
  <tr style="text-align:left"><th>会话</th><th>类型</th><th>消息数</th><th></th></tr>
  {% for s in sessions %}
  <tr>
    <td>{{ s.name|e }}</td>
    <td>{{ "群聊" if s.chat_type=="group" else "单聊" }}</td>
    <td>{{ counts.get(s.id, 0) }}</td>
    <td><a href="{{ s.id }}/index.html">打开</a></td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

`templates/session.html.j2`：

```jinja
{% extends "base.html.j2" %}
{% block title %}{{ session.name }} · 聊天记录{% endblock %}
{% block head_title %}{{ session.name|e }}{% endblock %}
{% block content %}
<p class="muted">{{ messages_count }} 条消息 · <a href="../index.html">← 返回会话列表</a></p>
{% for day, msgs in days %}
<h3 class="muted">{{ day }}</h3>
{% for m in msgs %}
{% set is_out = m.direction == "out" %}
<div style="display:flex;margin:10px 0;{{ 'justify-content:flex-end' if is_out else '' }}">
  <div style="max-width:70%;background:{{ 'var(--bubble-out)' if is_out else 'var(--bubble-in)' }};
              border:1px solid var(--border);border-radius:8px;padding:8px 12px">
    <div class="muted">{{ m.sender.name|e }}</div>
    {% if m.media and m.media.status == "ok" and m.media.kind == "image" %}
      <img src="{{ m.media.rel_path }}" onclick="zoom(this)" loading="lazy" alt="图片">
    {% elif m.media and m.media.status == "ok" and m.media.kind == "video" %}
      <video src="{{ m.media.rel_path }}" controls preload="metadata"></video>
    {% elif m.media and m.media.status == "ok" and m.media.kind == "voice" %}
      <audio src="{{ m.media.rel_path }}" controls preload="metadata"></audio>
    {% elif m.media and m.media.status == "ok" and m.media.kind == "file" %}
      <a href="{{ m.media.rel_path }}">下载文件 {{ m.media.ext }}</a>
    {% elif m.raw %}
      <div class="muted">[{{ m.type_name }}] {{ m.content }}</div>
    {% else %}
      <div>{{ m.content }}</div>
    {% endif %}
  </div>
</div>
{% endfor %}
{% endfor %}
{% endblock %}
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_html_renderer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add wechat_export/exporter/html_renderer.py wechat_export/exporter/templates tests/test_html_renderer.py
git commit -m "feat: HTML 会话视图渲染（零 CDN、双主题）"
```

---

### Task 13: CLI 与流水线编排（cli.py）

**Files:**
- Create: `wechat_export/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: locator / key_provider / db_access / schema / parser / 三个 exporter；`message_model`
- Produces：
  - `@dataclass ExportReport(sessions_done: int, messages: int, media_ok: int, media_missing: int, skipped: int, errors: list[str])`
  - `def parse_args(argv: list[str] | None) -> argparse.Namespace`
  - `def run_export(args) -> ExportReport`：主流水线；`--resume` 按 `.done` 跳过
  - `def main(argv=None) -> int`：错误码映射（0/1/2/3/4/5），`PartialExportError` 时打报告
  - `SESSION_SAFE_CHARS`：会话目录名清洗（去除非法文件名字符）

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py`：

```python
import pytest

from wechat_export import cli
from tests.fixtures import db_factory as f
from pathlib import Path

KEY = "a1" * 32


@pytest.fixture(autouse=True)
def _only_tmp_roots(monkeypatch):
    """固定候选数据根 = 测试传入的 --data-dir，避免扫到开发机上真实的微信数据。"""
    monkeypatch.setattr(cli.locator, "candidate_data_roots",
                        lambda override: [Path(override)] if override else [])


def _make_env(tmp_path):
    """搭建：数据根(wxid_abc/db_storage/message/message_0.db 带 3 条消息) + 输出目录"""
    root = tmp_path / "data" / "wxid_abc" / "db_storage" / "message"
    root.mkdir(parents=True)
    db = root / "message_0.db"
    conn = f.create_encrypted_db(db, KEY, f.PARAM_SET_DEFAULT["pragmas"])
    for i in range(3):
        f.insert_message(conn, msg_id=i + 1, ts=1700000000000 + i,
                         type_=1, content=f"m{i}", is_sender=i % 2,
                         talker="wxid_b")
    f.close(conn)
    return tmp_path


def test_main_success(tmp_path):
    env = _make_env(tmp_path)
    code = cli.main([
        "--data-dir", str(env / "data"), "--key-hex", KEY,
        "--out", str(env / "out"), "--session", "wxid_b",
    ])
    assert code == 0
    out = env / "out"
    assert (out / "index.html").exists()
    assert (out / "sessions.json").exists()
    assert (out / "export_meta.json").exists()
    data = (out / "wxid_b" / "messages.json").read_text(encoding="utf-8")
    assert "m1" in data


def test_main_resume_skips_done(tmp_path):
    env = _make_env(tmp_path)
    out = env / "out"
    (out / "wxid_b").mkdir(parents=True)
    (out / "wxid_b" / ".done").write_text("done")
    code = cli.main([
        "--data-dir", str(env / "data"), "--key-hex", KEY,
        "--out", str(out), "--session", "wxid_b",
    ])
    assert code == 0
    # 会话被跳过：.done 内容保持原样（未被重新写入）
    assert (out / "wxid_b" / ".done").read_text() == "done"


def test_main_bad_key_hex(tmp_path):
    code = cli.main(["--key-hex", "zz", "--out", str(tmp_path / "o")])
    assert code == 1  # ConfigError：密钥格式在定位之前先校验


def test_main_missing_wechat(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    code = cli.main(["--data-dir", str(empty), "--out", str(tmp_path / "o")])
    assert code == 2  # WeChatNotFoundError
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 实现 cli.py**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_cli.py -v`
Expected: PASS（`test_main_resume_skips_done` 里 `.done` 内容不变，因为 resume 跳过写入）

- [ ] **Step 5: Commit**

```bash
git add wechat_export/cli.py tests/test_cli.py
git commit -m "feat: CLI 入口与导出流水线"
```

---

### Task 14: M0 本机实测（探测脚本 + 交互验证检查点）

**Files:**
- Create: `tools/probe_wechat.py`
- Modify（实测后）：`wechat_export/key_provider.py` 的 `LOCAL_KEY_CANDIDATE_RELPATHS`（若发现本地落盘密钥路径）
- Modify（实测后）：`wechat_export/schema_snapshots/message_0.schema.json`（用真实列名覆盖）
- Create: `docs/findings/4x-reverse-notes.md`（探测报告）

**Interfaces:**
- Consumes: locator / db_access / key_provider / schema
- Produces：
  - `def probe() -> dict`：扫描账号、库文件、尝试密钥来源、对每个库尝试参数枚举、dump schema、返回结构化结果
  - `main(argv)`：写 `docs/findings/4x-reverse-notes.md` + 更新 schema 快照 + 打印结论

- [ ] **Step 1: 写失败测试**（脚本的纯函数部分）

`tests/test_probe.py`（新增）：

```python
from tools.probe_wechat import summarize


def test_summarize_counts():
    res = {"accounts": [{"wxid": "wxid_abc", "db_files": [
        {"name": "message_0.db", "decrypted": True, "tables": 3},
        {"name": "message_1.db", "decrypted": False},
    ]}]}
    s = summarize(res)
    assert s["decrypted_dbs"] == 1
    assert s["total_dbs"] == 2
```

- [ ] **Step 2: 实现 tools/probe_wechat.py**

```python
"""M0 探测脚本：本机实测 4.x 数据目录/密钥/SQLCipher 参数/schema。

运行：python tools/probe_wechat.py --out docs/findings/4x-reverse-notes.md
输出：结构化 JSON（stdout）+ 人类可读报告（--out）+ 更新 schema 快照。
"""

import argparse
import json
import sys
from pathlib import Path

from wechat_export import db_access, locator
from wechat_export.key_provider import (
    KeyProvider, find_weixin_pid, mini_dump_process, parse_key_hex,
)
from wechat_export.schema import snapshot_db

SNAP_PATH = Path(__file__).parent.parent / "wechat_export" / "schema_snapshots" / "message_0.schema.json"

# 语义列 → 候选真实列名（按优先级取第一个存在于实测表结构中的名字）
_COLUMN_CANDIDATES = {
    "msg_id": ["id", "Id", "msgId", "localId", "MsgSvrID"],
    "talker": ["talker", "Talker", "strTalker"],
    "type": ["type", "Type"],
    "subtype": ["subtype", "SubType"],
    "content": ["content", "Content"],
    "create_time": ["createTime", "CreateTime"],
    "is_sender": ["isSender", "IsSender"],
    "status": ["status", "Status"],
}


def _write_snapshot(first: dict) -> None:
    """把首个解密成功的库的实测表结构写入 schema 快照（含语义列映射推断）。"""
    full = json.loads(first["schema"])
    cols_by_table = full["columns"]
    msg_tables = [t for t in full["tables"] if "message" in t]
    table = msg_tables[0] if msg_tables else full["tables"][0]
    real = set(cols_by_table.get(table, []))
    mapping = {
        k: next((c for c in cands if c in real), cands[0])
        for k, cands in _COLUMN_CANDIDATES.items()
    }
    SNAP_PATH.write_text(
        json.dumps({"table": table, "columns": cols_by_table, **mapping},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def probe(data_dir: str | None, manual_key: str | None) -> dict:
    accounts = locator.locate_data(data_dir)
    result = {"accounts": []}
    key = parse_key_hex(manual_key) if manual_key else None
    for acc in accounts:
        entry = {"wxid": acc.wxid, "root": str(acc.root),
                 "db_storage": str(acc.db_storage) if acc.db_storage else None,
                 "db_files": []}
        if not acc.db_storage:
            result["accounts"].append(entry)
            continue
        for db in locator.message_db_files(acc.db_storage):
            rec = {"name": db.name, "size": db.stat().st_size,
                   "decrypted": False, "params": None, "tables": 0, "key_source": None}
            try:
                kp = KeyProvider(db, manual_key=manual_key, account=acc)
                k = kp.get_key()
                edb = db_access.open_encrypted(db, k)
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
        Path(args.out).write_text(
            f"# 微信 4.x 逆向实测记录\n\n- 时间：{__import__('datetime').datetime.now()}\n"
            f"- 解密：{s['decrypted_dbs']}/{s['total_dbs']}\n"
            f"- 参数：{first.get('params')}\n- 密钥来源：{first.get('key_source')}\n\n"
            f"```json\n{json.dumps(json.loads(first['schema']), ensure_ascii=False, indent=1)}\n```\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: 运行单测**

Run: `pytest tests/test_probe.py -v`
Expected: PASS

- [ ] **Step 4: 【交互检查点】在本机运行探测脚本**

Run（需要微信已登录并在本机可用、关键一步需要与需求方协作确认）:

```bash
source .venv/Scripts/activate
python tools/probe_wechat.py --out docs/findings/4x-reverse-notes.md
```

**预期结果之一（成功路径）**：
- 输出里至少 1 个账号的 `decrypted: true`，`schema` 字段含真实表结构
- 快照 `message_0.schema.json` 被真实列名覆盖
- 若 `key_source == "memory"`，把实测到的密钥存放位置/方式记录进 `docs/findings/4x-reverse-notes.md`；若发现**本地落盘密钥**，把路径登记进 `key_provider.LOCAL_KEY_CANDIDATE_RELPATHS` 并补单测

**失败则逐项排查**：数据根目录名（更新 `locator.DATA_DIR_NAMES`）→ db_storage 路径（更新 `locator.DB_STORAGE_NAMES`）→ 密钥（手动 `--key-hex` 验证，若密钥来自第三方 dump 工具）→ 参数集合（扩充 `db_access.SQLCIPHER_PARAM_CANDIDATES` 的 page/kdf/hmac 候选）。每项排查修正后补对应单测再继续。

- [ ] **Step 5: 依据实测结果更新映射与媒体 resolver 接口**

- 按实测 schema 覆盖 `schema_snapshots/message_0.schema.json`（脚本已做）
- 若实测发现群聊/媒体字段另有列（如 `talker` 语义、`subtype` 含义），更新 `parser.py` 的映射与 `TYPE_NAMES`，并补种子测试
- 将实测到的媒体存储方式（库内 blob 表 / FileStorage 文件路径）实现为 `cli._archive_media` 中的真实归档逻辑：若为 blob 表则 `edb.query` 读字节 → `MediaArchive.save_bytes`；若为文件路径则 `MediaArchive.save_file`，并补 `tests/test_cli.py` 的媒体导出用例

- [ ] **Step 6: Commit**

```bash
git add tools/ wechat_export/ docs/findings/ tests/test_probe.py
git commit -m "feat: M0 本机探测与 4.x 实测适配"
```

---

### Task 15: README 与交付收尾

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: 全部模块已就绪
- Produces: 用户可读的使用文档

- [ ] **Step 1: 写 README.md**

覆盖：功能描述、安装（venv + pip install -e ".[dev]"）、用法示例（含 `--wxid/--session/--key-hex/--no-media/--resume`）、输出目录结构说明、错误码表、常见问题（找不到数据目录/密钥失败）、**合规边界**（仅本人已登录账号数据、仅本机处理、不留解密副本、不联网、不用于外传）、已知限制（M0 实测结论、未识别消息类型的兜底行为）。

- [ ] **Step 2: 端到端冒烟**

Run:

```bash
source .venv/Scripts/activate
wechat-export --out ./chat_export        # 使用本机真实数据
```

Expected: 输出目录完整生成（index.html / sessions.json / export_meta.json / 各会话目录）、exit code 0、控制台报告数字与文件一致。（需要微信已登录；若密钥链路尚未实测通过，回到 Task 14 排查。）

- [ ] **Step 3: 全量测试**

Run: `python -m pytest -q`
Expected: 全部通过。

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README 与交付说明"
```

---

## 自检记录

- **Spec 覆盖**：§3 架构→Task 1/5/6/13；§4 密钥三级+参数枚举→Task 5/7/14；§5 消息模型→Task 3/9；§6 导出规格→Task 10/11/12；§7 CLI 与错误码→Task 2/13；§8 测试→Task 4 及各任务 TDD 步骤；§9 里程碑 M0→Task 14，M1→5/6/7，M2→8/9，M3→10/11/12，M4→13/15。无缺口。
- **占位符扫描**：媒体二进制解析（Task 9 注）、`LOCAL_KEY_CANDIDATE_RELPATHS` 空表、`_archive_media` 暂标记 missing——均为**留给 M0 实测填充的接口点**，探测脚本（Task 14）定义了填写的具体路径与步骤，非"实现 later"。
- **类型一致性**：`Message/Media/Session/Contact` 字段、`EncryptedDb.query/tables/columns`、`MediaArchive.save_bytes/save_file`、`KeyProvider.get_key`、`write_session_messages` 等接口在任务间签名一致；`schema.SchemaInfo` 的字段名与 `DEFAULT_MAPPING` 键一致。
- **评审修订记录（写入后 inline 修复）**：① cli 测试夹具用 `monkeypatch` 固定候选数据根，避免扫到开发机真实微信数据导致测试不确定；② 密钥格式校验提前到定位之前（错误码 1 优先级确定）；③ 内存扫描候选改为高熵过滤（≥16 不同字节），合成测试密钥改为 `bytes(range(32))`，低熵明文不触发解密尝试；④ `collect_dump_candidates` 去掉残留占位变量并限定候选上限；⑤ MiniDump 改用 `CreateFileW` 的 Win32 句柄（fd 不能直接用作 HANDLE）；⑥ cli 计数逻辑重构（`_archive_media` 返回 `(ok, missing)` 元组，report 统一累加）；⑦ probe 写快照时按候选列名推断语义映射，避免写入错误的列名映射。