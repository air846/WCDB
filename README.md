# wechat-export — 微信 4.x 聊天记录全量导出

从**本机已登录的 Windows 微信 4.x** 数据目录，解密聊天数据库并导出为
**HTML 会话视图 + 结构化 JSON + 媒体文件**三件产物。纯本地处理，只读原库，
不写解密副本落盘，不联网。

> 实测版本：微信 **4.1.13.63**（Windows x64）。逆向细节见
> [`docs/findings/4x-reverse-notes.md`](docs/findings/4x-reverse-notes.md)。

## 功能

- **自动定位**数据目录（读取 4.x 自定义路径配置）与账号
- **自动提取密钥**：扫描运行中的 `Weixin.exe` 进程内存中的 SQLCipher codec 上下文
  （每库独立密钥，HMAC 校验）；也支持 `--key-hex` / `--keys-file` 手动兜底
- **纯 Python 解密**：SQLCipher 4（AES-256-CBC，page=4096，reserve=80，HMAC-SHA512），
  解密明文只存在于内存（`sqlite3.deserialize`）
- 解析 4.x 真实 schema：每会话 `Msg_<md5(username)>` 表、`Name2Id` 发送者映射、
  zstd 压缩内容、组合类型 `(subtype<<32)|base_type`
- 导出 HTML（气泡视图、按天分组、双主题、零 CDN）、JSON（分块 + `.done` 断点）、
  媒体（图片/视频/语音/表情按 md5 去重归档）
- `--resume` 断点续导

## 安装

```bash
python -m venv .venv
source .venv/Scripts/activate      # Git Bash；cmd 用 .venv\Scripts\activate.bat
pip install -e ".[dev]"
```

依赖：`pycryptodome`（AES）、`jinja2`（HTML 模板）、`zstandard`（解压压缩消息）、
`pytest`（开发）。

## 用法

```bash
# 全量导出（自动定位数据目录、自动提取密钥）
wechat-export --out ./chat_export

# 指定账号 / 会话白名单（会话 username，可重复）
wechat-export --out ./chat_export --wxid wxid_xxx --session wxid_yyy --session 123@chatroom

# 跳过媒体（只出文本/JSON）
wechat-export --out ./chat_export --no-media

# 断点续导（跳过已完成的会话）
wechat-export --out ./chat_export --resume

# 手动提供密钥（单密钥或 JSON 映射）
wechat-export --out ./chat_export --key-hex <64位hex>
wechat-export --out ./chat_export --keys-file keys.json
```

`keys.json` 支持 `{"<salt_hex>": "<key_hex>"}` 或
`{"<path>": {"salt": "...", "enc_key": "..."}}` 两种格式。

### 输出结构

```
<out>/
├── index.html                  # 会话列表页
├── export_meta.json            # 全局元信息与统计
├── sessions.json               # 会话清单
└── <会话username>/
    ├── session.json            # 会话元信息 + 统计
    ├── messages.json           # 消息（超大会话分块 messages_000N.json）
    ├── index.html              # 气泡式会话视图
    ├── .done                   # 完成标记（--resume 用）
    └── media/
        ├── image/  video/  voice/  file/  emoji/
```

## 错误码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 参数错误（如密钥格式） |
| 2 | 未找到微信数据/账号 |
| 3 | 密钥提取失败 |
| 4 | 解密失败 |
| 5 | 导出部分失败（报告含失败项，修正后可 `--resume`） |
| 130 | 用户中断 |

## 工作原理（M0 实测）

1. **定位**：4.x 把自定义数据根父目录明文写入
   `%APPDATA%\Tencent\xwechat\config\*.ini`，实际数据根为 `<该目录>\xwechat_files\<wxid>`。
2. **密钥**：`Weixin.exe` 内存中每个打开的库都有一个 SQLCipher codec 上下文
   （特征前缀可扫描定位）。其中 keyspec 明文为 `x'<64hex enc_key><32hex salt>'`，
   4.1.13 起以 32 字节重复 XOR pad 混淆；pad 可由 codec 内已知 salt 运行时推导。
   提取到的 `enc_key` 用 SQLCipher HMAC-SHA512 对页 1 校验。
3. **解密**：逐页 AES-256-CBC；页 1 的 `file[0:16]` 为 salt，IV 位于保留区开头
   （`page[P-80:P-64]`）；解密结果注入 SQLite 魔数后经 `sqlite3.deserialize` 只读打开。
4. **解析**：`Msg_<md5(username)>` 会话表 + `Name2Id`；`create_time` 为秒；
   `source`/`message_content` 按 `WCDB_CT_*` 标志做 zstd 解压；
   `local_type=(subtype<<32)|base_type`。

## 合规边界

- 仅支持导出**本机当前已登录账号**的数据；
- 全程本机处理、只读打开原始库、**不写解密副本落盘**（解密明文仅驻内存）、不联网；
- 密钥仅作为本机数据库访问钥匙使用；
- 导出结果包含个人隐私，请自行妥善保管，**不得用于外传或侵犯他人隐私**。

## 已知限制

- 仅实测微信 4.1.13.63；其他 4.x 小版本的 codec 结构/pad 可能不同（脚本会明确报错）
- 图片/表情的 `.dat` 为微信自有编码，当前**原样归档**（未转码为浏览器可渲染格式）
- 视频/文件类媒体仅能归档已下载到本机的文件；未下载的标记 `missing`
- 语音以 `.silk` 原样归档
- 群成员显示名/头像等更丰富的联系人信息可后续增强

## 常见问题

- **找不到数据目录**：用 `--data-dir` 指定数据根（`...\xwechat_files`）
- **密钥提取失败**：确认微信已登录并保持运行；或 `--key-hex` / `--keys-file` 手动提供
- **权限不足**：以管理员身份运行（读取进程内存）
- **磁盘空间**：媒体可能很大（实测某账号 `msg` 目录 14GB），可用 `--no-media` 或 `--session` 控制
