# 表情（自定义表情 / 表情包）本地文件格式与解密（2026-09-10）

## 结论：已解出并在真机验证

微信 4.x 的本地表情文件是 **AES-128-CBC 加密**，密钥由**账号级 seed** 派生：

```
key    = md5(f"{seed}{wxid}EMOTICON").digest()[:16]      # AES-128
cipher = AES-128-CBC，IV = key，PKCS7，整文件一条流
```

- `seed`：账号级十进制数字串，微信启动后常驻进程内存。扫描 `\d{6,14}` 候选逐个
  派生 + 验证，本机实测 **15 秒命中**（53k 候选）。
- `wxid`：账号数据目录名去掉尾部 `_<4hex>`（`wxid_xxxxxxxx_1a2b` →
  `wxid_xxxxxxxx`，与 `cli._self_wxid` 同一规则）。
- 验证 oracle：把样本首块按 CBC 解出 `wxgf` / `GIF8` / `\x89PNG` / `\xff\xd8\xff`
  即命中；再用整文件解密 + PKCS7 + magic 复核。

**回归向量**（合成值，只用来冻结拼接顺序）：

```
seed = 1234567        wxid = wxid_demo
key  = md5("1234567wxid_demoEMOTICON")[:16] = 943713aae3f94c28005cdb11c17d3b80
```

真机实测（4.1.13.63）：seed 是 10 位十进制串，`\d{6,14}` 扫全部候选 53k 个、
15 秒命中，解出 `JFIF`/`\x89PNG`/`GIF89a` 明文。**真实 seed 与派生密钥同属本机
运行时数据，不写入仓库**（与 `4x-reverse-notes.md` 的密钥处理约定一致）。

密钥只在内存中使用、不落盘；实现见 `wechat_export/emoji_key.py`（提取）与
`wechat_export/image_decoder.py:decrypt_emoji`（解密）。

## 明文格式（实测分布，本机 245 个 Persist）

| 目录 | 明文格式分布 |
|---|---|
| `Persist/<md5[:2]>/<md5>` | 220 `wxgf`（单视频流、单帧，静态 HEVC）+ 15 `GIF89a`（动画）+ 4 png + 6 jpeg |
| `Thumb/<md5[:2]>/<md5>.thumb` | 154 jpeg + 91 png（缩略图，例 95×77） |
| `ThumbStore/<md5(package_id)>` | png（多个 PNG 首尾相接） |
| `PersistStore/<md5(package_id)>` | GIF 首尾相接（见下） |
| `cache/<YYYY-MM>/Emoticon/<md5[:2]>/<md5>` | 35 个 `wxgf` |

- 所有文件 `size % 16 == 0` 且 **PKCS7 填充校验全部通过** → 确认分组密码 + 填充。
- 此前"首块只有 2 种取值"的现象有了正确解释：`Thumb` 里 jpeg 组的明文首块相同、
  png 组另相同（154 / 91 恰好对应上表的 jpeg / png 数量），并非未知容器头。
- 明文不是 `wxam`：`Persist` 里 220/245 是 **`wxgf`**，交给既有的
  `image_decoder.decode_wxgf`（av → HEVC 首帧 → JPEG）即可直接出图。
- `md5(明文) != 文件名`（文件名是服务端表情 md5），**不能靠哈希反查**，只能按
  文件名/数据库映射定位。

## 文件布局（实测 4.1.13.63）

```
business/emoticon/
├── Persist/<md5[:2]>/<md5>              非商店表情全图（245 个）
├── Thumb/<md5[:2]>/<md5>.thumb          非商店表情缩略图（245 个）
├── PersistStore/<md5(package_id)[:2]>/<md5(package_id)>   商店表情包容器（7 个）
├── ThumbStore/<md5(package_id)[:2]>/<md5(package_id)>     商店包缩略图容器（+ .icon）
└── Temp/                                空
cache/YYYY-MM/Emoticon/<md5[:2]>/<md5>   近期表情缓存（在账号根，不在 business/ 下）
```

## 数据库（db_storage/emoticon/emoticon.db）

- `kNonStoreEmoticonTable(type, md5, caption, product_id, aes_key, thumb_url, tp_url,
  auth_key, cdn_url, extern_url, extern_md5, encrypt_url, designer_id, activity_id)`（242 行）
- `kStoreEmoticonFilesTable(package_id_, md5_, type_, sort_order_,
  emoticon_size_, emoticon_offset_, thumb_size_, thumb_offset_)`（128 行）
- `kStoreEmoticonPackageTable(package_id_, ...)`（27 行）；
  `PersistStore/<md5(package_id)>` 与已安装包的映射实测 **7/7 命中**。

## 商店表情包容器（PersistStore）的关键性质

容器**不是**每个表情各自加密后拼接，而是**整包一条连续 CBC 流**，明文就是包内
各表情文件首尾相接。因此：

1. 整包解密后按 `emoticon_offset_` / `emoticon_size_` 切片；
2. **不能单独解密某一刀**（IV 不是密钥、切片起点也未必 16 字节对齐）。

实测包 `com.tencent.xin.emoticon.person.stiker_1762863369b8283ab711acd1ec`
（容器 `md5(package_id)` = `12706a725520f8228c70013a75c3360e`，326560 B）：
16 个切片**全部**以 `00 3b`（GIF trailer）结尾，且 `md5(切片) == md5_`（16/16 一致）。
实现见 `wechat_export/emoji_store.py`。

## 历史：已排除的算法（负结果，保留备查）

早期把明文误猜为 `wxam` 且只试了 AES-ECB，因此 0 命中：

1. `kNonStoreEmoticonTable.aes_key`（hex/ASCII/base64/md5 派生/反转）：
   AES-ECB / CBC（IV=0、首块、尾块、md5、key）/ CFB8 / CFB128 / OFB / CTR /
   GCM（多种 nonce/tag 布局）→ 无 magic。
2. 图片全局密钥（含 `43e7d25eb1b9bb64`、`cfcd208495d565ef` 固定值）→ 无。
3. 单字节 XOR / 多字节 XOR / zlib / bz2 / lzma / zstd 解压 → 无。
4. 进程内存扫描（`tools/probe_emoji_key.py`）：
   - 字母数字 token 扫描（图片密钥提取同款）→ 无；
   - 8/16 字节对齐 raw 扫描，oracle 为 Thumb 首块/C1/C4 → **0 候选**。

   失败原因：真实方案是 **CBC 且 IV = 密钥本身**，而当时的 oracle 只做 ECB
   （或把 IV 当成文件首块）。把 oracle 换成 `P_t = D_k(C_t) XOR k` 后立即可命中。
   `tools/probe_emoji_key.py` 保留为历史工具，其 ECB 假设已过时。

## 仍然存在的限制

- 商店包里**已卸载**的包无法恢复：本机 1189 条商店表情消息中，只有 19 个 md5 属于
  当前已安装的包（`kStoreEmoticonFilesTable` 只覆盖已安装包）。
- 本地从未下载的表情只有消息 XML 里的 `cdnurl`（`http://vweixinf.tc.qq.com/.../
  stodownload?m=<md5>&filekey=...`，`filekey` 为接收时签发的签名）与 per-sticker
  `aeskey`；可选联网补下见 `wechat_export/emoji_fetch.py`（默认关闭），旧消息的
  `filekey` 可能已失效。
