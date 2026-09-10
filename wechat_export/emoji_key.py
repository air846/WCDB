"""微信表情（emoji）本地文件 AES 密钥提取（扫描运行中的 Weixin.exe 内存）。

实测微信 4.1.13.63：

    key    = md5(f"{seed}{wxid}EMOTICON").digest()[:16]     # AES-128
    cipher = AES-128-CBC，IV = key，PKCS7（整文件一条流）

- `seed`：账号级十进制数字串，微信启动后常驻进程内存（本机实测 10 位）；
- `wxid`：账号数据目录名去掉尾部 `_<4hex>`，即 `wxid_xxxxxxxx_1a2b` →
  `wxid_xxxxxxxx`（与 `cli._self_wxid` 同一规则）；
- 校验：把表情文件首块按 CBC 解出图片/容器 magic 即命中。

fast（默认）单遍扫描可读内存收集 `\\d{6,14}` 数字串，逐个派生密钥并用样本首块
验证；deep 额外尝试 wxid 字符串附近的 16 字节窗口与字母数字 token。

密钥只在内存中使用，不落盘（与 `wechat_export.image_key` 一致）。
"""

import hashlib
import re
from pathlib import Path

from Crypto.Cipher import AES

from wechat_export.exceptions import ConfigError
from wechat_export.image_key import find_weixin_pids
from wechat_export.key_provider import RemoteProcess

BLOCK = 16
CHUNK = 8 << 20
OVERLAP = 64
WXID_WINDOW = 4096  # deep：在 wxid 字符串前后各扫这么多字节
WXID_STRIDE = 8
SEED_RE = re.compile(rb"\d{6,14}")
ALNUM_RE = re.compile(rb"[A-Za-z0-9]{16,}")
WXID_SUFFIX_RE = re.compile(r"_[0-9a-f]{4}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def account_wxid(account_root) -> str:
    """账号目录名 → 真实 wxid（去掉尾部 `_<4hex>`）。"""
    return WXID_SUFFIX_RE.sub("", Path(account_root).name)


def derive_key(seed: str, wxid: str) -> bytes:
    """由账号级 seed 派生表情 AES-128 密钥。"""
    return hashlib.md5(f"{seed}{wxid}EMOTICON".encode("utf-8")).digest()[:16]


def parse_emoji_key(s: str) -> bytes:
    """解析手动表情密钥：32 位 hex 或 16/24/32 位 ASCII。"""
    s = s.strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    if len(s) == 32 and _HEX_RE.match(s):
        return bytes.fromhex(s)
    raw = s.encode("utf-8")
    if len(raw) in (16, 24, 32):
        return raw
    raise ConfigError(
        "表情密钥格式错误：需要 32 位 16 进制或 16/24/32 位 ASCII",
        hint="示例：--emoji-key 0123456789abcdef0123456789abcdef",
    )


def _plaintext_ok(pt: bytes) -> bool:
    """首块明文校验：只认 ≥4 字节强 magic（2 字节的 BM 在数万候选下必然误报）。"""
    if len(pt) < 12:
        return False
    if pt[:8] == b"\x89PNG\r\n\x1a\n" or pt[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if pt[:3] == b"\xff\xd8\xff" or pt[:4] in (b"wxgf", b"wxam"):
        return True
    return pt[:4] == b"RIFF" and pt[8:12] == b"WEBP"


def _decrypt_head(key: bytes, ct: bytes) -> bytes:
    try:
        return AES.new(key, AES.MODE_CBC, key).decrypt(ct)
    except (ValueError, KeyError):
        return b""


def _sample_heads(sample_paths, limit: int = 8) -> list[bytes]:
    """预读样本首块并按密文去重（Thumb 全库只有两种首块，去重省两个量级校验）。"""
    heads: dict[bytes, None] = {}
    for path in sample_paths:
        try:
            with open(path, "rb") as fp:
                head = fp.read(BLOCK)
        except OSError:
            continue
        if len(head) == BLOCK:
            heads.setdefault(head)
        if len(heads) >= limit:
            break
    return list(heads)


def verify_heads(key: bytes, heads: list[bytes], min_hits: int = 2) -> bool:
    """用样本首块校验候选密钥（纯内存，不读盘）。"""
    if not heads or len(key) not in (16, 24, 32):
        return False
    hits = sum(1 for head in heads if _plaintext_ok(_decrypt_head(key, head)))
    return hits >= min(min_hits, len(heads))


def verify_key(key: bytes, sample_paths, min_hits: int = 2) -> bool:
    """用样本文件首块校验候选密钥。"""
    return verify_heads(key, _sample_heads(sample_paths), min_hits)


def _confirm(key: bytes, sample_paths, limit: int = 3) -> bool:
    """整文件解密复核（PKCS7 + magic），杀掉首块层面残余的误报。"""
    from wechat_export.image_decoder import decrypt_emoji

    checked = 0
    for path in sample_paths:
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        if len(data) < BLOCK or len(data) % BLOCK:
            continue
        if decrypt_emoji(data, key) is None:
            return False
        checked += 1
        if checked >= limit:
            break
    return checked > 0


def _collect(proc, wxids: list[str]) -> tuple[list[bytes], list[int]]:
    """单遍扫描：收集 seed 数字串与 wxid 字符串出现位置。"""
    seeds: dict[bytes, None] = {}
    addrs: list[int] = []
    patterns = []
    for wxid in wxids:
        patterns.append(wxid.encode("utf-8"))
        patterns.append(wxid.encode("utf-16-le"))
    for base, size in proc.regions():
        offset, tail = 0, b""
        while offset < size:
            data = proc.read(base + offset, min(CHUNK, size - offset))
            if not data:
                offset += 0x1000
                tail = b""
                continue
            combined = tail + data
            for match in SEED_RE.finditer(combined):
                seeds.setdefault(match.group())
            for pattern in patterns:
                idx = combined.find(pattern)
                while idx >= 0:
                    addrs.append(base + offset - len(tail) + idx)
                    idx = combined.find(pattern, idx + 1)
            tail = combined[-OVERLAP:]
            offset += len(data)
    return list(seeds), addrs


def _try_seeds(seeds: list[bytes], wxids: list[str], heads: list[bytes]) -> bytes | None:
    for seed in seeds:
        seed_str = seed.decode("ascii", "ignore")
        for wxid in wxids:
            key = derive_key(seed_str, wxid)
            if verify_heads(key, heads):
                return key
    return None


def _try_windows(proc, addrs: list[int], heads: list[bytes]) -> bytes | None:
    """deep：密钥二进制可能就驻留在 wxid 字符串旁（账号会话 key 表）。"""
    for addr in addrs:
        start = max(0, addr - WXID_WINDOW)
        data = proc.read(start, WXID_WINDOW * 2)
        if not data:
            continue
        for i in range(0, len(data) - BLOCK + 1, WXID_STRIDE):
            key = data[i:i + BLOCK]
            if verify_heads(key, heads, min_hits=1):
                return key
    return None


def _try_tokens(proc, heads: list[bytes]) -> bytes | None:
    """deep：密钥可能以 32/64 位 hex 或 ASCII 串形式驻留内存。"""
    from wechat_export.image_key import _candidate_keys

    for base, size in proc.regions():
        offset, tail = 0, b""
        while offset < size:
            data = proc.read(base + offset, min(CHUNK, size - offset))
            if not data:
                offset += 0x1000
                tail = b""
                continue
            combined = tail + data
            for match in ALNUM_RE.finditer(combined):
                for key in _candidate_keys(match.group()):
                    if verify_heads(key, heads, min_hits=1):
                        return key
            tail = combined[-OVERLAP:]
            offset += len(data)
    return None


def extract_emoji_key(account_root, sample_paths, *, wxid: str | None = None,
                      deep: bool = False) -> bytes | None:
    """从运行中的 Weixin.exe 内存提取表情 AES 密钥；失败返回 None，不抛异常。"""
    heads = _sample_heads(sample_paths)
    if not heads:
        return None
    wxids = [wxid] if wxid else [account_wxid(account_root), Path(account_root).name]
    for pid in find_weixin_pids():
        try:
            with RemoteProcess(pid) as proc:
                seeds, addrs = _collect(proc, wxids)
                key = _try_seeds(seeds, wxids, heads)
                if key is None and deep:
                    key = _try_tokens(proc, heads)
                if key is None and deep:
                    key = _try_windows(proc, addrs, heads)
        except Exception:  # noqa: BLE001  # 权限不足/进程退出等
            continue
        if key and _confirm(key, sample_paths):
            return key
    return None
