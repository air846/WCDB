# 微信 4.x 逆向实测记录（M0）

- 日期：2026-09-09
- 环境：Windows / Python 3.12 / 微信 **4.1.13.63**（`<微信安装目录>\Weixin.exe`）
- 账号：`wxid_example00001_1a2b`
- 数据根：`<自定义父目录>\xwechat_files`（自定义路径）
- 结论：**M0 通过** —— 定位数据 → 提取全部库密钥（HMAC 验证）→ 解密 `message_0.db` → 读出真实消息。

## 1. 数据目录定位

- 4.x 把用户自定义数据根**父目录**明文写入 `%APPDATA%\Tencent\xwechat\config\*.ini`
  （文件内容即路径，如 `<自定义父目录>`），实际数据根为 `<该目录>\xwechat_files`。
  `locator` 已按此实现自动发现（commit 809440a）。
- 账号根：`<数据根>\<wxid>\`，其中 `db_storage\`（595MB，24 个 `.db`）、`msg\`（14GB 媒体）、
  `resource\`、`cache\`、`config\` 等。
- 库为活跃 WAL 模式（`*-wal`/`*-shm` 存在）；文件头部为随机字节（salt）。

## 2. SQLCipher 参数（实测确认）

- SQLCipher 4：**AES-256-CBC + HMAC-SHA512**
- `page_size = 4096`，`reserve = 80`（IV 16 + HMAC 64）
- 页 1：`file[0:16]` = salt 明文；加密区 `[16, 4016)`；**IV = page[4016:4032]**；
  **HMAC = page[4032:4096]**
- 其他页：加密区 `[0, 4016)`，IV/HMAC 同上位置
- KDF：`cipher_default_kdf_iter = 256000`，`fast_kdf_iter = 2`
- HMAC 密钥：`PBKDF2-HMAC-SHA512(enc_key, salt ^ 0x3A, 2, dklen=32)`；
  HMAC 数据：`page1[16:4032] + LE32(1)`；与 `page1[4032:4096]` 比对
- **每个 `.db` 文件有独立的 salt 与独立的 enc_key**（非全局单密钥）

## 3. 密钥提取（内存扫描，已实测可行）

目标进程：内存占用最大的 `Weixin.exe`（`tasklist` 解析）。

### 3.1 定位 codec 上下文

扫描特征前缀（56 字节，SQLCipher codec 配置常量）：

```
0000000000e80300020000001000000020000000100000001000000000100000
630000005000000040000000000000000200000002000000
```

命中后解析（x64）：

| 结构 | 偏移 | 含义 |
|---|---|---|
| codec ctx | `+0x48` | `salt_ptr` |
| codec ctx | `+0x50` | `hmac_salt_ptr` |
| codec ctx | `+0x68` | `read_cipher_ctx` |
| codec ctx | `+0x70` | `write_cipher_ctx` |
| cipher ctx | `+0x08` | `key_ptr`（32B，混淆） |
| cipher ctx | `+0x10` | `hmac_key_ptr`（32B，混淆） |
| cipher ctx | `+0x20` | `keyspec_ptr`（99B，混淆） |

校验：`hmac_salt == salt ^ 0x3A`。

### 3.2 keyspec 混淆与解密

4.1.13 的 keyspec 明文为 `x'<64hex enc_key><32hex salt>'`（99 字节），
内存中以**32 字节重复 XOR pad** 混淆。本机实测 pad：

```
00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
```

**运行时自动推导 pad（推荐，不依赖硬编码）**：已知 keyspec 密文
`ks_ct[66..97]` 对应明文 salt 的 32 个 hex 字符，且 `ks_ct[0..1]` 对应 `x'`，
故对任一候选 DB salt：

```
pad[2:32] = ks_ct[66:96] XOR salt_hex[0:30]
pad[0:2]  = ks_ct[96:98] XOR salt_hex[30:32]
校验：pad[0:2] == ks_ct[0:2] XOR b"x'"
```

解出 `enc_key` 后，用第 2 节的 HMAC 公式对 `page1` 验证。

### 3.3 实测结果

- 24 个库中 **21 个**在内存中缓存了密钥并通过 HMAC 验证（其余 3 个未打开/未缓存）。
- 解出的 `enc_key` 可直接作 AES-256-CBC 密钥解密对应库。
- `message_0.db` 完整解密后可被 stdlib `sqlite3` 打开，含 48 张表。

## 4. 真实 schema（`message_0.db`）

### 4.1 会话消息表

- **每个会话一张表**：`Msg_<md5(session_username)>`
  （如 `Msg_8b6bfb4f35c5feb9462dd00838005b29` ↔ `wxid_example00002`）
- 列：

```
local_id, server_id, local_type, sort_seq, real_sender_id, create_time,
status, upload_status, download_status, server_seq, origin_source,
source, message_content, compress_content, packed_info_data,
WCDB_CT_message_content, WCDB_CT_source
```

- `real_sender_id` → `Name2Id.rowid` → `user_name`
- `create_time` 为**秒级**时间戳（非毫秒）
- `source` / `message_content` 可能为 **zstd** 压缩（magic `28 b5 2f fd`），
  由 `WCDB_CT_*` 标志指示（实测 `4` = zstd，`0` = 明文）
- `local_type = (subtype << 32) | base_type`，实测：

| base_type | 含义 | 备注 |
|---|---|---|
| 1 | 文本 | |
| 3 | 图片 | 内容 XML 含 `md5` |
| 34 | 语音 | `media_0.db.VoiceInfo` |
| 43 | 视频 | |
| 47 | 表情 | `<msg><emoji ... md5=...>` |
| 49 | 应用/文件/卡片 | subtype：5/19/57/62/2000… |
| 50 | 音视频通话 | |
| 10000 | 系统消息 | 撤回/加群等 XML |

### 4.2 辅助表

- `Name2Id(user_name TEXT, is_session INTEGER)`：rowid ↔ username（sender/session 共用）
- `TimeStamp(timestamp INTEGER)`
- `wcdb_builtin_compression_record`
- `message_1..3.db`、`media_0.db`、`message_resource.db` 结构同类

### 4.3 其他库

- `contact.db`：`contact`（username/remark/nick_name/big_head_url…）、`chat_room`、
  `chatroom_member`、`name2id`、`stranger` 等
- `session.db`：`SessionTable(username, type, summary, last_timestamp, …)`、
  `Name2Id`、`SessionNoContactInfoTable` 等
- `media_0.db`：`VoiceInfo(chat_name_id, create_time, local_id, svr_id, voice_data BLOB, data_index)`
- `message_resource.db`：`MessageResourceInfo`、`MessageResourceDetail(resource_id, message_id, type, size, data_index, packed_info)`、
  `ChatName2Id`、`SenderName2Id` 等

## 5. 媒体文件

- 目录：`msg\attach\<md5(session_username)>\<YYYY-MM>\Img\<md5>.dat`
  （`_t.dat` 为缩略图；目录名与 `Msg_` 表同源）
- 图片 `.dat` 为微信单字节 XOR 加密格式
- 语音：`media_0.db.VoiceInfo.voice_data`（silk 格式）
- 视频/文件：`msg\attach\...\Video|File\...`（待细化）
- `msg\` 目录实测 14GB

## 6. 对 spec/计划的影响

| 原假设 | 实测 | 影响 |
|---|---|---|
| 单张 `message` 表 + `talker` 列 | 每会话 `Msg_<md5(username)>` 表 | `schema`/`parser`/`cli` 重构 |
| 全局单密钥 | **每库独立 enc_key** | `key_provider` 需返回 salt→key 映射；`db_access` 按库取 key |
| `createTime` 毫秒 | **秒** | 时间戳换算 |
| 文本明文 | 部分 **zstd 压缩**（`WCDB_CT_*`） | 需 zstd 解压（新增依赖 `zstandard`） |
| `isSender` 列 | `real_sender_id` → `Name2Id` | 方向判定改用 sender 与本人 wxid 比对 |
| reserved ∈ {48,16} | **reserved=80**（IV 位置不变） | `sqlcipher` 候选表加 80 |
| 媒体 md5 → FileStorage 文件 | `msg\attach\<md5(session)>\...\<md5>.dat` | 媒体 resolver 按实测实现 |

> 注：密钥与 pad 均为**本机运行时数据**；本文件不含任何真实密钥。
