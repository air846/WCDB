# 表情（自定义表情包）本地文件格式与解密调查（2026-09-10）

## 结论（当前）

导出 HTML 中表情显示为 `[表情]` 下载链接而不是图片，是因为微信 4.x 的本地
表情文件是**加密的自有容器**，工具当前只做了原样归档（`.bin`）。解密需要
`emoji` 专用 AES 密钥，尚未定位到（见下）。**待微信运行时用内存扫描验证
`wxam` 明文假设。**

## 文件布局（实测 4.1.13.63）

```
business/emoticon/
├── Persist/<md5[:2]>/<md5>              非商店表情全图（245 个，size%16==0）
├── Thumb/<md5[:2]>/<md5>.thumb          非商店表情缩略图（245 个，size%16==0）
├── PersistStore/<md5(package_id)>       商店表情包容器（7 个，按 offset 拼接）
├── ThumbStore/<md5(package_id)>         商店表情包缩略图容器（含 .icon）
cache/YYYY-MM/Emoticon/<md5[:2]>/<md5>   近期表情缓存（部分与 Persist 相同）
```

- `PersistStore` 容器大小 == Σ `kStoreEmoticonFilesTable.emoticon_size_`（+16 对齐填充），
  说明容器是逐个加密表情的直接拼接，无额外头。
- `Persist`/`Thumb` 的 md5 与 `kNonStoreEmoticonTable.md5` 完全对应（242 + 3 额外）。

## 数据库（db_storage/emoticon/emoticon.db）

- `kNonStoreEmoticonTable(type, md5, caption, product_id, aes_key, thumb_url, tp_url,
  auth_key, cdn_url, extern_url, extern_md5, encrypt_url, designer_id, activity_id)`
- `kStoreEmoticonFilesTable(package_id_, md5_, type_, sort_order_,
  emoticon_size_, emoticon_offset_, thumb_size_, thumb_offset_)`
- `kStoreEmoticonPackageTable(package_id_, ...)`；`PersistStore/<md5(package_id)>` 映射已验证。

## 密文特征

| 目录 | 首 16 字节不同值 | 首 64 字节不同值 | 说明 |
|---|---|---|---|
| Thumb | **2**（`dbff9c15…` 154 个 / `e1bd6ecf…` 91 个） | 53（其中 153 个共享前 64B） | 固定头/同编码 JPEG 特征 |
| Persist | 204 / 245 | 243 | 首块随文件变化 |
| ThumbStore | 1 | 2 | 同 `e1bd…` |
| 所有文件 size % 16 == 0 |  |  | 分组密码 + 填充 |

## 已排除的算法（负结果）

在真实文件（含已知 `md5 ↔ aes_key` 对）上测试均未解出图片/wxam magic：

1. `kNonStoreEmoticonTable.aes_key`（hex/ASCII/base64/md5 派生/反转）：
   AES-ECB / CBC（IV=0、首块、尾块、md5、key）/ CFB8 / CFB128 / OFB / CTR /
   GCM（多种 nonce/tag 布局）→ 无 magic。
2. 图片全局密钥（含 `43e7d25eb1b9bb64`、`cfcd208495d565ef` 固定值）→ 无。
3. 单字节 XOR / 多字节 XOR / zlib / bz2 / lzma / zstd 解压 → 无。
4. 进程内存扫描（微信运行时）：
   - 字母数字 token 扫描（图片密钥提取同款）→ 无；
   - 8/16 字节对齐 raw 扫描，oracle 为 Thumb 首块/C1/C4，validator 为
     JFIF/Exif/GIF/PNG/zeros/wxgf → **0 候选**。

## 关键线索（二进制字符串，Weixin.dll）

```
emoticon file invalid / file read error / data decrypt key error / data decrypt failed
emoticon md5 verify failed          ← 解密后校验 md5(明文)==文件名
emoticon wxam data empty and no retry
emoticon data encrypt key failed / data save error
key to bytes error                  ← AesKey 字符串 → 字节
encrypt emoticon data failed / write encrypt emoticon data failed
AesKey (proto 字段) Caption ProductId CdnUrl ThumbUrl …
```

- `wxam` 出现在 Skia 编解码器 magic 表中（与 `heic` 并列），且 VoipEngine.dll
  导出 `WxAMFrameEnc_Construct`；第三方 fuzzer 显示 WXAM 可解码为 JPEG/GIF。
- **推测：Persist/Thumb 明文是 `wxam` 容器**（首 16/64 字节固定头，因此同 key
  下密文首块相同）。此前内存扫描的 validator 没有包含 `wxam`，这是最可能的遗漏。
- 本地文件解密密钥可能来自 `kNonStoreEmoticonTable.aes_key`（经某种包装），
  或为全局 emoji 密钥（加载于显示表情时的进程内存）。

## 下一步（需微信运行）

1. 启动微信并**打开一次表情面板 / 点开任意自定义表情**（让密钥进内存）。
2. 运行 `python tools/probe_emoji_key.py c0 8`（validator 已加入 `wxam`）。
   若命中，记录 key 与模式，回填 `wechat_export/emoji_key.py` 与解码器。
3. 解密成功后按 `wxam` → JPEG/GIF 转换（参照 `image_decoder.decode_wxgf`，
   可能同样可用 `av`/HEVC），归档为图片并在 HTML 内嵌。
