# 拆开微信 4.x：SQLCipher、内存密钥与一个 IV=Key 的顿悟

*air846 · 2026-09-10 · 源码：[github.com/air846/WCDB](https://github.com/air846/WCDB)*

> 给自己的聊天记录做一个本地归档，听起来是个周末项目。直到我发现新版本微信把能拆的东西全换了一遍。
>
> 这篇文章记录我逐层拆开微信 4.x（实测 4.1.13.63 / Windows x64）加密的过程：数据库、图片、表情三道防线，以及最后那道防线让我卡了最久的一次顿悟。
>
> 全文只谈**格式与算法**。密钥材料、账号标识等本机运行时数据一律不入文 —— 文中出现的所有 wxid、seed 均为占位示例。

---

## 起点：3.x 的经验全部作废

微信 3.x 时代，聊天记录解密是一件有标准答案的事：密钥存在内存里的固定位置，数据库是明文 SQLCipher 加一个众所周知的密码。现成工具一抓一大把。

4.x 把这些全推翻了。公开资料少得可怜，社区里流传的说法互相矛盾。我手上只有一个已登录的微信和一份 14GB 的 `msg` 目录。

先做的不是逆向，是摸清地形：

```
xwechat_files/<wxid>/
├── db_storage/     595MB，24 个 .db
├── msg/            14GB 媒体
├── business/emoticon/   表情
├── cache/
└── config/
```

数据根的位置倒是有个意外的小发现：如果你在微信设置里改过存储路径，那个自定义父目录会被**明文**写进 `%APPDATA%\Tencent\xwechat\config\*.ini`，文件内容就是一行路径。不用逆向，直接读。

真正的加密从 `db_storage` 开始。

---

## 第一关：SQLCipher 4，以及为什么我决定自己实现

`.db` 文件头部是 16 字节随机数据 —— 这是 SQLCipher 的 salt。翻了一下格式，参数是 SQLCipher 4：

- AES-256-CBC
- page_size = 4096
- reserve = 80（IV 16 字节 + HMAC-SHA512 64 字节）
- KDF 迭代 256000 轮

第一反应是找个 SQLCipher 绑定库，把密钥喂进去就完事。查了一圈发现这条路在 Windows 上是死的：`sqlcipher3-binary` 从来没发布过 Windows wheel，`pysqlcipher3-binary` 只到 Python 3.8。编译 SQLCipher 的 C 源码倒也不是不行，但那意味着整个工具多了个原生依赖，用户装起来要配编译环境。

于是我做了个后来被证明很舒服的决定：**纯 Python 逐页解密**。SQLCipher 的磁盘格式比想象的简单，翻一遍 `sqlcipher.c` 的 `sqlite3Codec` 就清楚了：

```
页 1：  [0:16]        salt（明文）
        [16 : P-80]   加密区（AES-256-CBC）
        [P-80 : P-64] 本页 IV（明文）
        [P-64 : P]    HMAC-SHA512

页 N：  [0 : P-80]    加密区
        [P-80 : P-64] 本页 IV
        [P-64 : P]    HMAC
```

两个容易踩的点：

**第一，密钥不经 PBKDF2。** 微信用的是 raw key（`PRAGMA key = "x'...'"`），那 32 字节直接就是 AES-256 的密钥。PBKDF2 只用来派生 HMAC 密钥。

**第二，页 1 的第一个块不是解出来的，是抄出来的。** 加密时 `sqlcipher.c` 压根没加密页 1 的前 16 字节（留作 salt），解密时它直接执行 `memcpy(ctx->buffer, SQLITE_FILE_HEADER, 16)` —— 把 `SQLite format 3\0` 硬写进去。所以你的解密循环必须手动补上这个魔数，而不是指望解出点什么：

```python
if off == 0:
    ct = page[HEADER_SIZE:page_size - reserved]
    out += SQLITE_MAGIC                    # 手动注入
    out += _decrypt_region(ct, key, iv)
else:
    out += _decrypt_region(page[:page_size - reserved], key, iv)
```

还有个连带的坑：页 1 的第 20 字节记录着本库的保留区大小，解密后必须回填成真实的 `reserved`（这里是 80），否则 SQLite 会把保留字节当成数据读，直接报库损坏。

### 布局参数得靠猜，但不能瞎猜

有个先有鸡还是先有蛋的问题：page_size 和 reserved 就写在文件头里 —— 而文件头是加密的。SQLCipher 源码里甚至有句注释在吐槽这件事：

> the first page of the database is encrypted and thus sqlite can't effectively determine the pagesize

所以布局只能枚举。但"枚举"不等于"试到能跑为止"，解密后的页 1 头部有一串确定性的特征可以拿来判真伪：

```python
def page1_header_ok(plain_page1: bytes, page_size: int, reserved: int = 0) -> bool:
    ps_field = 1 if page_size == 65536 else page_size
    if plain_page1[16:18] != ps_field.to_bytes(2, "big"):   # 页大小字段
        return False
    if plain_page1[18] not in (1, 2) or plain_page1[19] not in (1, 2):
        return False
    if plain_page1[20] != reserved:                          # 保留区必须自洽
        return False
    return plain_page1[21] == 0x40 and plain_page1[22] == 0x20 and plain_page1[23] == 0x20
```

注意最后一行：载荷比例、最大/最小载荷比例这三个字节是 SQLite 的常量。**用错密钥解出来的页 1 不可能同时撞上这七八个字节。**

### 明文一次都不落盘

解出来的明文怎么打开？写个临时文件再 `sqlite3.connect` 是最直觉的做法，但那就违反了我给自己定的规矩：解密副本不落盘。

标准库其实有条更干净的路 —— `sqlite3.Connection.deserialize()`：

```python
plain = bytearray(sc.decrypt_db(data, key_hex, page_size, reserved))
plain[18] = 1   # 改成 rollback 模式
plain[19] = 1
self.conn = sqlite3.connect(":memory:")
self.conn.deserialize(bytes(plain))
```

（那两行 `= 1` 是实测踩出来的。真实库是 WAL 模式，头部版本字节是 2，直接 deserialize 会因为找不到 `-wal` 文件而失败。强制标记为 rollback 模式就好了 —— 反正我们是只读的，不需要 WAL。）

到这里，**只要我有密钥**，就能读出任意一个库。问题是密钥从哪来。

---

## 第二关：密钥一定在内存里

先排除掉不可能的选项。每个 `.db` 有独立的 salt、独立的密钥，PBKDF2 迭代 256000 轮，暴力破解在数学上不成立。

但有个绕不开的事实：**微信自己得能解密这些库。** 那么密钥必然在某个时刻存在于 `Weixin.exe` 的地址空间里。问题只是怎么找到它，以及怎么确认找到的是对的。

### 特征前缀

翻 SQLCipher 源码时注意到，每个打开的库都会在内存里维护一个 codec 上下文结构，而这个结构的开头是一串配置常量 —— page_size、KDF 迭代次数、HMAC 算法这些。它们是连续排布的，在内存里形成一段**几乎不可能偶然出现**的固定字节序列：

```python
CODEC_PREFIX = bytes.fromhex(
    "0000000000e80300020000001000000020000000100000001000000000100000"
    "630000005000000040000000000000000200000002000000"
)
```

（`0003e8` = 1000，`02000000`，`10` = 16，`20` = 32……全是参数，纯结构。）

在进程内存里扫这 56 字节，命中处就是一个 codec 上下文。按 x64 布局解析：

| 结构 | 偏移 | 含义 |
|---|---|---|
| codec ctx | `+0x48` | `salt_ptr` |
| codec ctx | `+0x50` | `hmac_salt_ptr` |
| codec ctx | `+0x68` | `read_cipher_ctx` |
| codec ctx | `+0x70` | `write_cipher_ctx` |
| cipher ctx | `+0x20` | `keyspec_ptr` |

`keyspec_ptr` 指向的就是密钥本体：明文形态是 SQLCipher 的标准 keyspec 字符串 `x'<64位hex密钥><32位hex盐>'`。

### 4.1.13 加了一层混淆 —— 以及一次漂亮的自举

如果这么简单，这篇文章就该结束了。实测发现 4.1.13 起，这段 keyspec 在内存里是**被 XOR 混淆过的** —— 用一个 32 字节的 pad 循环异或。

按常规思路，接下来该去找这个 pad：它可能在内存里，也可能能通过逆向某个混淆函数还原。但那天我盯着 keyspec 的明文结构看了很久，突然意识到一件很妙的事：

```
keyspec 明文：x' <64位 hex 密钥> <32位 hex 盐> '
                ↑ 0..66            ↑ 66..98
```

**它的尾部就是 salt 的 hex 字符串 —— 而 salt 在 codec 上下文里是明文存着的**（`salt_ptr`，而且它必须等于 db 文件头那 16 字节）。

已知明文，未知密钥，XOR 加密 —— 这就是送分题：

```python
def derive_pad(keyspec_ct: bytes, salt_hex: str) -> bytes | None:
    """由 keyspec 密文 + 已知 salt 推导 32 字节 XOR pad。"""
    salt_ascii = salt_hex.encode("ascii")
    pad = bytearray(32)
    pad[2:32] = bytes(a ^ b for a, b in zip(keyspec_ct[66:96], salt_ascii[0:30]))
    pad[0:2]  = bytes(a ^ b for a, b in zip(keyspec_ct[96:98], salt_ascii[30:32]))
    # 自校验：密文开头是 "x'"，用它验证推导出的 pad 是否自洽
    if pad[0:2] != bytes(a ^ b for a, b in zip(keyspec_ct[0:2], b"x'")):
        return None
    return bytes(pad)
```

pad 不需要逆出来，不需要硬编码，不需要知道它是怎么生成的 —— **用同一段内存里的 salt 现场反推就行**。最后那行自校验是关键：如果推导出的 pad 连 `x'` 都对不上，说明这个 codec 不是我们要的，直接丢弃。

一处已知明文，撬开了整个混淆层。

### 验证：不信任何没有 HMAC 背书的密钥

扫出来的候选密钥不能直接信。SQLCipher 的每页都带 HMAC，这是现成的验证器 —— 用候选密钥对页 1 算一遍 HMAC，比对密文里的那 64 字节：

```python
def verify_key(key_hex: str, page1: bytes) -> bool:
    key = bytes.fromhex(key_hex)
    salt = page1[:16]
    mac_key = hashlib.pbkdf2_hmac(
        "sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)
    h = hmac_mod.new(mac_key, page1[16:PAGE_SIZE - RESERVE + 16], hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return h.digest() == page1[PAGE_SIZE - 64:PAGE_SIZE]
```

（HMAC 密钥用 `salt ^ 0x3A` 派生、迭代次数只要 2 轮 —— 因为这里不需要抗暴力，它只是完整性校验。）

实测结果：24 个库里 21 个在内存中缓存了密钥并通过校验，剩下 3 个当时没打开。**每一个通过的都真的能解密。** 没有一个误报。

---

## 第三关：图片 —— 藏在 `.dat` 里的 V2 容器

数据库通了，`msg/` 目录里的 14GB 媒体又是另一回事。图片不是 jpg，是 `.dat`。

3.x 时代的 `.dat` 是整文件单字节 XOR，密钥能从文件头反推。4.x 换了：

```
[6B magic: 07 08 'V' '2' 08 07] [4B aes_size] [4B xor_size] [1B flag]
[AES-128-ECB 密文] [raw 明文] [单字节 XOR 尾部]
```

一段文件拆成三种处理方式，第一次见确实会愣一下。有个容易翻车的细节在长度对齐上：

```python
def aligned_aes_size(aes_size: int) -> int:
    """PKCS7 对齐：向上取整到 16 的倍数；已对齐则再补一整块。"""
    return aes_size + (BLOCK - aes_size % BLOCK)
```

PKCS7 的规则是"不足则补齐，**恰好整数倍也补一整块**"。如果 `aes_size` 本身就是 16 的倍数，这里会多出 16 字节。当成普通向上取整写过一次，解出来的图全是花的。

### 密钥来得比数据库轻松

图片的 AES 密钥是**全局**的 16 字节 —— 不是每张图一个。它在微信解码图片时才加载进内存，所以提取方式很直接：拿一个 `.dat` 文件的密文块当 oracle，扫描进程内存，谁能让这个块解出图片 magic 谁就是密钥。

```python
def _try_key(key: bytes, oracles: list[bytes]) -> bool:
    pt = _decrypt_block(key, oracles[0])
    if pt is None or not _plaintext_ok(pt):
        return False
    # 多 oracle 交叉验证，杀误报
    for ct in oracles[1:]:
        if detect_format(_decrypt_block(key, ct)) is None:
            return False
    return True
```

这里有个反直觉的运行时约束，我写工具的时候专门在 README 里给它留了一行：**密钥可能只在你看过图片之后才驻留内存。** 冷启动的微信进程里扫不到 —— 你得先在微信里点开任意一张聊天图，密钥才会被加载。为此我还写了个轮询脚本 `watch_image_key.py`，蹲在后台等你点图。

### 一次误报，以及 magic 长度的教训

第一版 oracle 的校验函数长这样：

```python
if pt[:2] == b"BM":     # BMP
    return True
```

跑起来满屏候选。原因很朴素：**BMP 的 magic 只有 2 字节。** 在数万到数十万次尝试里，随便一个随机块撞上 `BM` 开头都是大概率事件。

这个教训在后面还会再出现一次，所以值得单独说：**oracle 的判别强度必须和搜索空间的大小匹配。** 2 字节 magic 配几万次尝试 = 必然误报。修法是给 BMP 补上头部字段校验（文件大小非零、数据偏移落在合理区间）：

```python
if pt[:2] == b"BM":
    size = int.from_bytes(pt[2:6], "little")
    offset = int.from_bytes(pt[10:14], "little")
    return 0 < size and 14 <= offset <= 1078
```

---

## 第四关：表情 —— 卡得最久的一道题

这是整件事里最曲折的部分。数据库和图片加起来用了一个下午加一个晚上，表情单独卡了我小半天 —— 而且中间有一整轮**完全错误的方向**。

### 错误的起点

第一次 dump 出 `business/emoticon/` 下的文件时，我在二进制字符串里翻到了 `wxam`。它是微信 Skia 封装的图片容器 magic，看起来很像个标准答案。加上 `emoticon.db` 里有一张 `kNonStoreEmoticonTable`，明明白白带着一列 `aes_key` —— 侦探小说里这简直是把凶手名字写在门牌上。

于是我开始了漫长的排除法。AES-ECB、CBC（IV=0 / 首块 / 尾块 / key）、CFB8、CFB128、OFB、CTR、GCM（各种 nonce/tag 布局），密钥形态从 hex 到 ASCII 到 base64 到 md5 派生到字节反转 —— **零命中。**

然后是单字节 XOR、多字节 XOR、zlib、bz2、lzma、zstd 解压 —— **零命中。**

我不信邪，写了个全内存暴力扫描器：扫 `Weixin.exe` 的每一个可读内存区，按 stride 8 取 16 字节窗口当候选密钥，用 Thumb 文件的密文块做 oracle。跑一轮 100 秒起步。

然后我跑了六轮：

```
=== oracle=c0  stride=8 keysize=16 ===  done  99s; candidates=0
=== oracle=c1  stride=8 keysize=16 ===  done  96s; candidates=0
=== oracle=c4  stride=8 keysize=16 ===  done 103s; candidates=0
=== oracle=c0  stride=8 keysize=32 ===  done  96s; candidates=0
=== oracle=xiv stride=8 keysize=16 ===  done 134s; candidates=0
=== oracle=pair stride=8 keysize=16 ===  done 208s; candidates=0
ALLDONE
```

十二分钟的内存扫描，零候选。

### 转折：三个枯燥的统计

放弃暴力之后，我回过头去做了一件本该更早做的事 —— 统计文件本身的性质，而不是猜算法。

**第一个信号：** 目录下每个文件的大小都是 16 的整数倍，而且用任意一个"看起来像密钥"的东西去做 PKCS7 校验，**填充居然全都合法**。

等等。PKCS7 填充合法意味着什么？意味着文件长度 modulo 16 的分布不是随机的 —— 最后一块的尾部字节在说谎，而它说得很有规律。这是**分组密码 + PKCS7 填充**的确凿证据。我此前的 XOR 和压缩类尝试可以全部出局了。

**第二个信号：** 我把 Thumb 目录下所有文件的前 16 字节拿出来去重 —— **只有两种取值**（154 个一种，91 个另一种）。

这个数字太整齐了。154 : 91 正好是该目录下 jpeg 与 png 的数量比。

**如果 IV 是随机的，密文首块不可能只有两种取值。** 同一个明文块加密成同一个密文块 —— 这是 ECB 的特征，或者……是**固定 IV 的 CBC**。

**第三个信号：** 把两组首块解密后对比，前缀相同、后缀不同。

答案的形状已经很清楚了。

### 顿悟：IV 就是密钥

那天下午我盯着探测脚本里那个没跑通的 `"xiv"` 变体看：

```python
if name == "xiv":
    c0, c1 = oracle
    d1 = aes(key, AES.MODE_ECB).decrypt(c1)
    pt = bytes(a ^ b for a, b in zip(d1, c0))   # P = D_k(C) XOR C_prev
```

这是标准的 CBC 解密公式。我当时假设 c0 是第一块的密文、于是把它当作第二块的 IV。但既然首块只有两种取值 —— **IV 根本不来自密文，它是固定的。** 而固定的 IV 是哪儿来的？

一个偷懒到极致、但工程上完全说得通的实现：

```python
AES.new(key, AES.MODE_CBC, key)   # IV = key
```

IV 就是密钥自己。

于是 `P_t = D_k(C_t) XOR k`。把探测脚本里的 `c0` 换成 `key` 本身，重跑 —— 命中。

回头看，那六轮失败扫描里跑的 `"xiv"` oracle，**离正确答案只差一个操作数**。当时的 `_emoji_rescan.log` 我到现在都留着。

### 密钥怎么来的

解密通了，但密钥本身还是未知的。回到内存里找。

这次不像数据库那样有个结构体可以顺藤摸瓜，我只能扫。但有了可用的 oracle，扫描就从"找密钥"变成了"验证候选"：

```python
def _plaintext_ok(pt: bytes) -> bool:
    """首块明文校验：只认 ≥4 字节强 magic。"""
    if pt[:8] == b"\x89PNG\r\n\x1a\n" or pt[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if pt[:3] == b"\xff\xd8\xff" or pt[:4] in (b"wxgf", b"wxam"):
        return True
    return pt[:4] == b"RIFF" and pt[8:12] == b"WEBP"
```

（注意这里刻意**不认 `BM`**，理由就是前面那张 BMP 教过的学费。注释我写得很直白：*"2 字节的 BM 在数万候选下必然误报"*。）

扫描目标选的是十进制数字串 `\d{6,14}` —— 这个选择基于一个观察：微信的账号级密钥派生常用这种形态的 seed。收集完候选后逐个派生、逐个验证：

```
seed = 10 位十进制串          # 账号级，微信启动后常驻内存
key  = md5(f"{seed}{wxid}EMOTICON").digest()[:16]
```

实测：53k 个候选，**15 秒命中**。

对比一下 —— 同样是全内存扫描，上一轮 12 分钟零候选，这一轮 15 秒。**差别不在算力，在于 oracle 对不对，以及知不知道该搜什么形状的东西。**

### 最后一个坑：商店表情包是一个整包

非商店表情（用户自己收藏的）每个文件一条独立的 CBC 流。但商店表情包不一样：

```
business/emoticon/PersistStore/<md5(package_id)>
```

这个容器**不是**每个表情各自加密后拼起来的，而是**整包一条连续的 CBC 流**，明文就是包内所有表情文件首尾相接。

这意味着：**你不能单独解密包里的某一个表情。** 因为 IV 不是密钥了 —— 第二块的 IV 是前一块的密文，而前一块在包的另一个位置。加上切片起点未必 16 字节对齐，单独解密必然得到乱码。

正确做法是整包解密，然后按 `emoticon.db` 里的偏移切片：

```sql
SELECT package_id_, md5_, emoticon_offset_, emoticon_size_,
       thumb_offset_, thumb_size_
FROM kStoreEmoticonFilesTable
```

实测验证：某个 326560 字节的容器切出 16 个表情，**每一个都以 `00 3B`（GIF trailer）结尾，且 `md5(切片) == md5_`** —— 16/16 全部对上。这种"全中"的时刻，是逆向里最爽的部分。

---

## 顺手的几个副产品

主体通了之后，剩下的都是添头，但有几个值得一提：

**`wxgf` —— 图片里还能再套一层容器。** 4.x 的多数整图不是 jpg，是个叫 `wxgf` 的自定义容器，里面装的是 **HEVC 视频流**（对，静态图用视频编码存）。好消息是它能当单帧 HEVC 解，`av` 库解开取首帧转 JPEG 就行。

**SILK 语音。** 语音消息是 SILK 格式，浏览器播不了。转成 24kHz WAV 之后 HTML 里可以直接 `<audio>` 播放。这个靠 `rsilk`。

**`type=49` 的应用消息。** 引用、链接、文件、转账、红包、聊天记录、小程序、视频号、拍一拍 —— 十几种，全都是 XML。解析成结构化卡片之后，HTML 里终于不用显示一坨尖括号了。

**每会话一张表。** 4.x 的 schema 和 3.x 完全不同：不是单张 `message` 表加 `talker` 列，而是**每个会话一张 `Msg_<md5(username)>` 表**。另外 `create_time` 是秒不是毫秒，`local_type` 是 `(subtype << 32) | base_type` 的组合值，部分 `message_content` 还是 **zstd 压缩**的（由 `WCDB_CT_*` 标志位指示）。

这些细节没有一个是靠猜能猜对的，全是实测撞出来的。

---

## 复盘：真正起作用的五个思路

写完之后回头看，这件事里最有价值的不是任何一段代码，而是几个反复被验证的方法：

**1. oracle 的质量决定一切。** 同样一台机器，同样全内存扫描：oracle 错了，12 分钟零候选；oracle 对了，15 秒命中。搜索空间再大也不是问题，**判断"这是不是对的"的函数才是瓶颈**。下次再遇到这类问题，我会先把一半时间花在设计验证器上。

**2. 统计会说话。** `size % 16 == 0`、PKCS7 填充全过、首块只有两种取值 —— 这三个信号里没有任何一行是"逆向"，全是把文件当数据处理。但它们直接指向了"分组密码 + 固定 IV"这个结论。**当你猜不出算法时，先去数一数文件的性质。**

**3. 从源码出发，不要从猜测出发。** SQLCipher 的页布局不是试出来的，是读 `sqlcipher.c` 读出来的 —— 包括"页 1 的魔数是 memcpy 进去的"这种靠猜绝不可能对上的细节。有源码可读的时候，读源码比做实验快得多。

**4. 已知明文是最锋利的工具。** XOR pad 那一关，一旦意识到"keyspec 的尾部就是 salt 的 hex，而 salt 是明文"，整个混淆层当场失效。**找已知明文，比逆向混淆算法本身省力得多。**

**5. 负结果值得记录。** 那个六轮全零的探测脚本我没有删。它的文件头现在写着一段注释，说明为什么它永远不可能命中、当时错在哪。`docs/findings/emoji-format-notes.md` 里也专门留了一节"已排除的算法"，把这十几种失败的尝试全列了出来。

因为我知道下一个做这件事的人（很可能是三个月后的我自己）会想知道：**哪些路已经走过了。**

---

## 关于边界

这个工具能做很多事，所以边界必须写清楚 —— 这部分在我的 README 里是单独一节，不是免责声明的小字：

- 只导出**本机当前已登录账号**的数据；
- 全程本机处理，**只读**打开原始库，**不写解密副本落盘**（解密明文仅存在于内存），**不联网**；
- 唯一的例外是显式开启的 `--fetch-emoji`（用消息自带的 cdnurl 补下本机缺失的表情），默认关闭，且只允许微信 CDN 域名；
- 提取到的密钥只在内存中使用、不落盘；
- 导出结果包含完整个人隐私，**不得用于外传或侵犯他人隐私**。

技术上能做和应该做是两回事。这份工具的存在理由是"我自己的聊天记录，我想自己保管" —— 仅此而已。

---

## 尾声

从设计文档到能完整导出一个会话，前后两天。最后跑通的那一刻，我盯着浏览器里那些气泡、图片和表情包看了很久 —— 那是从 14GB 的加密字节里一块一块拼回来的。

也是在那时候我才意识到，整件事里最难的部分根本不是密码学。AES 没有弱点，SQLCipher 也没有弱点。**真正的破绽全在"一个必须持续运行的软件，得把钥匙放在手边"这件事上** —— 放在内存里、用一个可以现场推导的 pad 混淆、IV 图省事直接用了密钥本身。

密码学是完美的。工程不是。

---

---

**源码仓库**：[github.com/air846/WCDB](https://github.com/air846/WCDB)

工具实现、探测脚本与全部实测记录都在这里：

- `wechat_export/` — 工具本体（密钥提取 / SQLCipher / 图片解码 / 表情解码 / 导出器）
- `docs/findings/4x-reverse-notes.md` — 数据库与密钥的实测报告
- `docs/findings/emoji-format-notes.md` — 表情格式调查（含已排除算法的负结果清单）
- `tools/probe_emoji_key.py` — 那个六轮全零的探测脚本，留着当反面教材

**作者**：[air846](https://github.com/air846)

---

*实测版本：微信 4.1.13.63（Windows x64）。其他 4.x 小版本的 codec 结构与混淆方式可能不同，请以实测为准。*
