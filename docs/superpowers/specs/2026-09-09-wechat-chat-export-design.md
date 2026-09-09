# 微信聊天记录导出工具（Windows 4.x）— 设计文档

- 日期：2026-09-09
- 状态：已与需求方确认（分三节评审通过）
- 项目根目录：`D:/code/WCDB`

## 1. 目标与范围

### 背景

Windows 桌面版微信将聊天记录存储在 SQLCipher 加密的 SQLite 数据库中。本工具的目标是：从**用户本人已登录账号**的本机数据目录，将聊天记录**全量导出**为"浏览器可读的 HTML 会话视图 + 结构化 JSON 数据 + 媒体文件本体"三件产物，供用户本地留存、回顾、分析与二次处理。

### 已确认的需求

| 维度 | 结论 |
|---|---|
| 平台 | Windows 桌面版微信，**仅支持 4.x（新版）** |
| 内容范围 | 全量：文本、图片、视频、语音、文件等消息及媒体本体 |
| 输出形态 | HTML 会话视图 + 结构化 JSON + 媒体文件归档（两者都要） |
| 实现语言 | Python（本领域生态最成熟，SQLCipher 解密与 HTML 生成均有成熟库） |
| 交互形态 | 纯 CLI 命令行工具 |

### 非目标（明确不做）

- 不支持微信 3.x 及以下版本
- 不做 GUI（后续可迭代）
- 不做数据在线同步/上传，任何形式的外发均不属于本工具职责
- 不做消息修改/删除（导出为只读）

### 合规边界

- 仅支持导出**本机当前已登录账号**的数据
- 全程本机处理、只读打开原始库、不写解密副本落盘、不联网
- 解密密钥仅作为本机数据库访问钥匙使用
- 上述边界写入 README 声明

## 2. 风险与对策

**最高风险：Windows 微信 4.x 的数据库加密机制公开资料少于 3.x，密钥提取方式与 SQLCipher 参数需在本机实测验证。**

对策：
- 里程碑 M0 为**解密可行性验证（spike）**：在本机定位数据目录 → 提取密钥 → 打开 `message_0.db` → 读出真实消息。验证通过再铺开正式实现。
- 密钥提取采用三级兜底策略（见 §4）。
- SQLCipher 读取路径已按官方源码核实，M0 实测确认（见 §4）。
- 验证产物为《4.x 逆向验证报告》与 schema 快照，存入仓库供后续实现与测试使用。

## 3. 整体架构

分层流水线架构，模块职责单一、可独立测试：

```
locator（定位账号/数据目录/会话列表）
  → key_provider（提取数据库密钥，含手动输入兜底）
  → db_access（SQLCipher 只读打开 message 分片库，暴露只读查询）
  → parser + message_model（DB 原始行 → 统一消息模型）
  → exporter（JSON 写入 / 媒体 md5 去重归档 / HTML 渲染）
  → cli（参数解析 / 流水线编排 / 错误码 / 汇总报告）
```

### 模块职责

| 模块 | 职责 | 依赖 |
|---|---|---|
| `locator` | 定位安装/数据目录、账号(wxid)列表、会话列表 | 无 |
| `key_provider` | 三级策略提取 32 字节密钥 | 无 |
| `db_access` | 纯 Python 解密到内存（sqlite3 deserialize）+ 只读查询 | pycryptodome + stdlib sqlite3 |
| `schema` | 4.x 库表/字段映射（M0 后固化，含快照） | 无 |
| `message_model` | Contact/Session/Media/Message 数据类 | 无 |
| `parser` | 原始行 → 消息模型，按消息类型分支 | message_model |
| `exporter.json_writer` | 每会话 messages.json（分块） | 无 |
| `exporter.media_archive` | 媒体归档：md5 去重、分类目录、原子写盘 | 无 |
| `exporter.html_renderer` | HTML 会话视图（Jinja2 模板，零 CDN 依赖） | jinja2 |
| `cli` | argparse 参数、流水线编排、错误码、报告 | 全部 |
| `exceptions` | 分级异常定义 | 无 |

### 项目结构

```
D:/code/WCDB/
├── wechat_export/
│   ├── __init__.py
│   ├── cli.py
│   ├── locator.py
│   ├── key_provider.py
│   ├── db_access.py
│   ├── schema.py
│   ├── message_model.py
│   ├── parser.py
│   ├── exceptions.py
│   ├── exporter/
│   │   ├── __init__.py
│   │   ├── json_writer.py
│   │   ├── media_archive.py
│   │   ├── html_renderer.py
│   │   └── templates/          # Jinja2 HTML 模板
│   └── schema_snapshots/       # M0 实测 schema 快照（JSON）
├── tests/                      # pytest 单测
├── docs/
│   └── superpowers/specs/      # 设计文档
├── pyproject.toml
└── README.md
```

### 依赖选型

- `pycryptodome`：纯 Python 实现 SQLCipher 逐页 AES-256-CBC 解密。依据官方源码（sqlcipher/src/sqlcipher.c）核实：raw key 直作 AES 密钥（不经 PBKDF2）、每页 IV 存于该页保留区前 16 字节、页大小与保留区大小明文记录于文件头（偏移 16/20）。**无 SQLCipher 原生绑定依赖**——`sqlcipher3-binary` 从未发布 Windows 轮子、`pysqlcipher3-binary` 仅有 py3.8 轮的 Windows 轮子，均已排除
- `jinja2`：HTML 模板渲染
- 参数解析用标准库 `argparse`（不额外引入 click）

## 4. 4.x 逆向与解密设计

### 已知事实

- 数据目录：`文档\WeChat Files\<wxid>\db_storage\`
- 消息库按分片存储：`db_storage\message\message_0.db`、`message_1.db`…
- 联系人/会话信息位于 `contact.db`、`session.db` 等附属库（文件名待 M0 实测确认）
- 加密为 SQLCipher（参数与 3.x 不同，待实测）

### 密钥提取三级策略

```
1. 本地落盘密钥   — 若 4.x 将密钥存于本机文件/注册表（M0 实测确认位置）
2. 进程内存提取   — 对运行中的 Weixin.exe 做 MiniDump，扫描 32 字节密钥
3. 手动输入兜底   — 用户提供 32 字节 hex key 时直接录入（--key-hex 参数）
```

### SQLCipher 读取路径（已按官方源码核实）

- 页大小 = 文件头偏移 16 的大端 uint16（1=65536）；保留区大小 = 偏移 20 的字节；两者均明文
- 原始 32 字节密钥（`x'...'`）**直接作为 AES-256-CBC 密钥**，不经过 PBKDF2（raw key 分支）
- 每页 IV = 该页保留区前 16 字节（明文存储）；解密仅需逐页 AES 解密，**不验证 HMAC**（仅完整性用途）
- 解密后的明文 SQLite 只存在于内存（stdlib `sqlite3.Connection.deserialize`），不写盘，头部保留区字节清零
- M0 实测确认 4.x 库符合此路径；若实测偏离（如非 raw key），按实测在 `sqlcipher.py` 增加分支并更新本 spec

### 读写安全

- 全程只读原始库文件：纯 Python 逐页 AES-256-CBC 解密（pycryptodome），解密明文仅驻内存
- 不写解密副本落盘；任何临时文件用完即删
- 数据仅本机处理，不联网

## 5. 统一消息模型

```python
Contact { wxid, name, remark, avatar(可选) }
Session { id, name(备注优先), type(单聊/群聊), member_count, ... }
Media   { kind(image/video/voice/file), md5, size, ext, rel_path,
          status(ok/missing) }
Message {
  msg_id, ts(毫秒), type(int), type_name,
  direction(in/out), sender{ wxid, name, is_self },
  content,               # 文本/描述（图片=路径描述，语音=时长，文件=文件名）
  media: Media | None,
  raw,                   # 未识别类型的原始内容兜底（不丢失数据）
}
```

### 消息类型覆盖

- 完整建模：文本、图片、语音、视频、文件、链接/卡片、表情、系统消息（撤回、加群、拍一拍等）
- 兜底建模：红包/转账/小程序等无法完整还原内容的类型 → 保留 `type_name + content 摘要 + raw`，HTML 渲染为灰色说明卡片（可展开查看 raw JSON）
- 群聊支持：sender 解析、群会话元信息

### 媒体解析抽象

`MediaResolver` 接口——4.x 媒体是库内 blob 还是 FileStorage 文件路径，M0/M2 实测后实现对应 resolver；解析失败的消息标记 `missing` 并计入报告，不中断导出。

## 6. 导出规格

### 目录结构

```
<out>/
├── index.html                  # 会话列表页（统计、跳转）
├── export_meta.json            # 全局元信息（账号、导出时间、统计、工具版本）
├── sessions.json               # 会话清单
└── <会话目录>/
    ├── session.json            # 会话元信息
    ├── messages.json           # 结构化完整记录（超大会话分块 messages_0001.json…）
    ├── index.html              # 会话页：气泡式聊天视图
    └── media/
        ├── image/  video/  voice/  file/
        └── avatars/ (可选)
```

### HTML 视图规格

- 全部静态本地资源，零 CDN 依赖，完全离线可开
- 时间线按天分组；本人/对方气泡左右分列
- 图片懒加载 + 点击放大；`<video>`/`<audio>` 直接播放
- 浅色/深色两套主题（CSS 变量）
- 会话列表页显示消息数/成员数
- 未识别类型渲染为灰色说明卡片

### JSON 规格

- 每消息字段固定，含 `raw` 兜底，保证数据不丢失
- 超大会话自动分块，避免单文件过大

## 7. CLI 与错误处理

### 命令形态

```
wechat-export --out ./chat_export [--wxid XXX] [--session 白名单]
              [--key-hex 32字节hex] [--no-media] [--resume]
```

- `--wxid`：指定账号（默认当前登录账号）
- `--session`：会话白名单（可重复）
- `--key-hex`：手动密钥兜底
- `--no-media`：跳过媒体归档（只出文本/JSON）
- `--resume`：跳过已完成的会话（断点续导）

### 错误处理原则

- 每个失败点给出"发生了什么 + 可能原因 + 建议操作"的中文提示
- 错误码分层：0 成功 / 1 参数错误 / 2 未找到微信 / 3 密钥失败 / 4 解密失败 / 5 导出部分失败
- 会话级独立容错：单个会话失败不丢已完成的会话
- 单线程顺序导出（避免多开 SQLite 的复杂度）

### 健壮性

- 媒体写盘：临时文件 + 原子重命名
- 磁盘空间不足/文件损坏/权限问题 → 对应提示进入导出报告
- 单条消息解析失败 → 保留 raw + 警告计数，继续导出

## 8. 测试策略

- 基于 M0 schema 快照生成**合成 SQLCipher 加密夹具库**（同一套参数），种子覆盖全部消息类型
- parser 单测、JSON 一致性测试、HTML 渲染快照测试、媒体去重测试、CLI 集成测试
- 真实数据仅在需求方本机冒烟测试，不进自动化测试
- 解密参数正确性由"合成夹具库可解 + 真实库冒烟"双保险验证

## 9. 里程碑

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| M0 spike | 本机定位数据目录 → 提取密钥 → 打开 message_0.db → 读出真实消息 | 《4.x 逆向验证报告》+ schema 快照 + 最小可用解密片段 |
| M1 | locator + key_provider 按实测固化 | 单测 + 本机真实库验证 |
| M2 | db_access + schema + parser + message_model | 全消息类型正确解析（含媒体） |
| M3 | exporter 三件套（JSON / 媒体归档 / HTML） | 完整导出一次，HTML 可离线打开 |
| M4 | CLI 打磨、README、异常完善、打包脚本 | 端到端可用 |

## 10. 设计评审记录

- 2026-09-09 第 1 节（架构骨架）——通过
- 2026-09-09 第 2 节（4.x 逆向与解密）——通过
- 2026-09-09 第 3 节（消息模型与导出器）——通过