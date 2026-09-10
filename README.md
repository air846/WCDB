# wechat-export — 微信 4.x 聊天记录全量导出

> 仓库：<https://github.com/air846/WCDB> · 作者：[@air846](https://github.com/air846)

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
- **图片解码**：微信 4.x 的 `V2 .dat`（AES-128-ECB + 单字节 XOR）自动解码为
  `.jpg/.png/.gif/.webp`，旧版单字节 XOR `.dat` 同样兼容；`wxgf`（HEVC 容器）在
  安装可选依赖 `av` 后自动转 JPEG；图片密钥优先从 `Weixin.exe` 进程内存自动提取
  （也可 `--image-key` 手动提供）
- **语音转码**：SILK 语音在安装可选依赖 `rsilk` 后自动转 24kHz WAV，HTML 内直接
  播放并显示时长；未安装时保留 `.silk` 下载链接
- **表情/表情包解码**：微信 4.x 本地表情（`business/emoticon`）是 AES-128-CBC 加密
  （IV = 密钥），密钥由账号级 seed 派生并自动从 `Weixin.exe` 进程内存提取；解密后
  `wxgf` 转 JPEG、GIF/PNG 原样出图，商店表情包容器（`PersistStore`）按
  `emoticon.db` 偏移切片还原，HTML 直接显示表情图片（也可 `--emoji-key` 手动提供）
- **应用消息摘要**：`type=49` 的 appmsg XML 解析为可读卡片（引用/链接/文件/
  转账/红包/聊天记录/小程序/视频号/拍一拍等），JSON 保留原始 XML 并额外给出
  `appmsg` 结构化字段
- `--resume` 断点续导（导出格式升级时自动重导旧会话）

## 安装

```bash
python -m venv .venv
source .venv/Scripts/activate      # Git Bash；cmd 用 .venv\Scripts\activate.bat
pip install -e ".[dev]"
```

依赖：`pycryptodome`（AES）、`jinja2`（HTML 模板）、`zstandard`（解压压缩消息）、
`pytest`（开发）。

可选：`av`（wxgf/HEVC 图片转 JPEG；多数微信 4.x 整图是 wxgf，建议安装）、
`rsilk`（SILK 语音转 WAV，浏览器可直接播放）：

```bash
pip install -e ".[dev,wxgf,voice]"
```

## 用法

```bash
# 全量导出（自动定位数据目录、自动提取密钥）
wechat-export --out ./chat_export

# 指定账号 / 会话白名单（会话 username，可重复）
wechat-export --out ./chat_export --wxid wxid_xxx --session wxid_yyy --session 123@chatroom

# 跳过媒体（只出文本/JSON）
wechat-export --out ./chat_export --no-media

# 手动提供图片密钥（32位hex 或 16位ASCII；不传则自动从进程内存提取）
wechat-export --out ./chat_export --image-key <32位hex>

# 图片密钥自动提取失败时，可深度扫描进程内存（较慢）
wechat-export --out ./chat_export --image-key-scan deep

# 手动提供表情密钥（32位hex 或 16位ASCII；不传则自动从进程内存派生）
wechat-export --out ./chat_export --emoji-key <32位hex>

# 联网补下本机从未下载的表情（默认关闭；仅访问微信 CDN，原始响应缓存在 .emoji_cache）
wechat-export --out ./chat_export --fetch-emoji --fetch-emoji-limit 200

# 断点续导（跳过已完成的会话；若已拿到图片密钥，会自动重导含未解码图片的会话）
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

> `messages.json` / HTML 中的 `media.rel_path` 均以会话目录为根（如
> `media/image/<md5>.jpg`），可直接相对引用。

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
- 全程本机处理、只读打开原始库、**不写解密副本落盘**（解密明文仅驻内存）、**不联网**；
- 唯一的例外是显式开启的 `--fetch-emoji`：它会用消息里自带的 `cdnurl` 访问微信
  CDN 补下本机缺失的表情（仅允许 `qq.com` / `tencent.com` / `qpic.cn` / `wechat.com`
  域名，串行 + 限流 + 落盘缓存），**默认关闭**；
- 密钥仅作为本机数据库/媒体访问钥匙使用，只在内存中派生与使用、不落盘；
- 导出结果包含个人隐私，请自行妥善保管，**不得用于外传或侵犯他人隐私**。

## 已知限制

- 仅实测微信 4.1.13.63；其他 4.x 小版本的 codec 结构/pad 可能不同（脚本会明确报错）
- **图片密钥**：微信仅在解码图片时把 16 字节 AES 密钥加载进内存，因此自动提取前
  需先在微信里**点开任意一张聊天图片**；否则请用 `--image-key` 手动提供
- **wxgf 图片**：多数微信 4.x 整图是 wxgf（HEVC 容器），安装可选依赖 `av` 后会自动
  转成 JPEG；未安装或少数 HEIC 变体转码失败时保留 `.wxgf` 下载链接
- **表情/表情包**：本地已有的表情在微信运行时自动解密出图；**已卸载**的商店表情包
  （`kStoreEmoticonFilesTable` 里没有的包）与**从未下载**的表情仍会显示为 `[表情]`
  占位，后者可用 `--fetch-emoji` 尝试联网补下（旧消息的 `filekey` 可能已失效）
- 视频/文件类媒体仅能归档已下载到本机的文件（视频按 `msg/video` 下文件 id、
  文件按 `msg/file` 下同名文件匹配）；未下载的标记 `missing`
- 语音转 WAV 需要可选依赖 `rsilk`（PyPI 预编译轮子覆盖 Python 3.7–3.12；更高版本
  可能需自行编译 Rust 扩展）；极少数损坏/非标准 SILK 会保留 `.silk` 供下载
- 群成员显示名/头像等更丰富的联系人信息可后续增强

## 常见问题

- **图片加载不出来**：① 确认 HTML 用浏览器直接打开且 `media/` 目录与 `index.html`
  同级；② 若 `media/image/` 下仍是 `.dat`，说明导出时没有图片密钥——先在微信里点开
  一张图片，再 `wechat-export --out ./chat_export --resume`（会自动重导未解码会话），
  或 `--image-key` 手动提供；③ 若仍是 `.wxgf`，安装 `av` 后重跑
  （`pip install "wechat-export[wxgf]"`）
- **表情显示为 `[表情]` 占位**：① 若 `media/emoji/` 下仍是 `.bin`，说明导出时没拿到
  表情密钥——确认微信正在运行后 `wechat-export --out ./chat_export --resume`
  （会自动重导未解码会话），或 `--emoji-key` 手动提供；② 若占位消息在 XML 里
  `productid` 指向某个表情包，而该包在本机已卸载，则无法恢复；③ 其余从未下载的
  表情可加 `--fetch-emoji` 联网补下
- **语音播放不了**：安装可选依赖 `rsilk` 后重跑
  （`pip install "wechat-export[voice]"`），`--resume` 会自动重导并转成 `.wav`；
  若个别语音仍是 `.silk`，说明该条 SILK 已损坏/非标准，点击链接可下载原文件
- **type=49 消息显示为可读卡片**：引用/链接/文件/转账/红包等直接展示摘要；
  原始 XML 仍保存在 JSON 的 `content`，结构化字段在 `appmsg`
- **找不到数据目录**：用 `--data-dir` 指定数据根（`...\xwechat_files`）
- **密钥提取失败**：确认微信已登录并保持运行；或 `--key-hex` / `--keys-file` 手动提供
- **图片密钥提取失败**：先在微信中打开一张聊天图片（点开大图）再重试，或加
  `--image-key-scan deep` 深度扫描，或 `--image-key` 手动提供
- **权限不足**：以管理员身份运行（读取进程内存）
- **磁盘空间**：媒体可能很大（实测某账号 `msg` 目录 14GB），可用 `--no-media` 或 `--session` 控制
