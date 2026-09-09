"""微信 4.x 图片 AES 密钥提取（从运行中的 Weixin.exe 进程内存）。

V2 `.dat` 的 AES 密钥是**全局 16 字节**，微信在解码图片时加载/缓存。提取方式：

1. 取任意 V2 文件偏移 15 处的 16 字节密文块作为 oracle；
2. 扫描微信进程内存，找能把该块解出图片 magic 的候选密钥：
   - 快速模式：字母数字 token（微信 4.x 常见为 32 字符小写字母数字串，取前 16 字节）；
   - 深度模式：额外按 16 字节对齐逐窗口暴力扫描 RW 区域。
3. 多个 oracle 交叉校验，避免误报。

密钥可能只在**查看过图片后**才驻留内存；若提取失败，请先在微信中打开任意一张
聊天图片（点开大图）再重试。也支持 `--image-key` 手动提供。
"""

import ctypes
import multiprocessing as mp
import re
import subprocess
import struct
from pathlib import Path

from Crypto.Cipher import AES

from wechat_export.image_decoder import V2_MAGIC, detect_format, HEADER_LEN
from wechat_export.key_provider import RemoteProcess

ALNUM_RE = re.compile(rb"[A-Za-z0-9]{16,}")
READABLE_PAGE_TYPES = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
CHUNK = 8 << 20
RAW_WORKERS = 8


def find_weixin_pids() -> list[int]:
    """按内存占用从大到小返回所有 Weixin.exe PID。"""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True).stdout
    pids: list[tuple[int, int]] = []
    for line in out.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 5 and parts[0].strip('"').lower() == "weixin.exe":
            try:
                mem = int(parts[4].replace(",", "").replace(" K", "").strip() or "0")
            except ValueError:
                mem = 0
            pids.append((mem, int(parts[1])))
    return [pid for _, pid in sorted(pids, reverse=True)]


def _candidate_keys(token: bytes) -> list[bytes]:
    """由一段字母数字 token 派生候选 AES 密钥。"""
    keys: list[bytes] = []
    if len(token) >= 16:
        keys.append(token[:16])
    if len(token) >= 32:
        keys.append(token[:32])
    if len(token) in (32, 64) and all(c in b"0123456789abcdefABCDEF" for c in token):
        try:
            raw = bytes.fromhex(token.decode("ascii"))
        except ValueError:
            raw = b""
        for start in (0, len(raw) - 16):
            if start >= 0 and len(raw[start:start + 16]) == 16:
                keys.append(raw[start:start + 16])
        if len(raw) >= 32:
            keys.append(raw[:32])
    return keys


def _decrypt_block(key: bytes, ct: bytes) -> bytes | None:
    if len(key) not in (16, 24, 32):
        return None
    try:
        return AES.new(key, AES.MODE_ECB).decrypt(ct)
    except (ValueError, KeyError):
        return None


def _plaintext_ok(pt: bytes) -> bool:
    """严格图片头校验（尽量降低暴力扫描误报）。"""
    if len(pt) < 12:
        return False
    if pt[:3] == b"\xff\xd8\xff":
        if pt[3] in (0xE0, 0xE1):
            return pt[6:10] in (b"JFIF", b"Exif")
        return pt[3] in (0xDB, 0xEE, 0xC0, 0xC2, 0xC4)
    return detect_format(pt) is not None


def _try_key(key: bytes, oracles: list[bytes]) -> bool:
    if len(key) < 16:
        return False
    pt = _decrypt_block(key, oracles[0])
    if pt is None or not _plaintext_ok(pt):
        return False
    for ct in oracles[1:]:
        pt2 = _decrypt_block(key, ct)
        if pt2 is None or detect_format(pt2) is None:
            return False
    return True


def collect_oracles(dat_paths, limit_groups: int = 3) -> list[bytes]:
    """从 V2 `.dat` 文件收集不同密文组的首块（最多 limit_groups 个）。"""
    groups: dict[bytes, int] = {}
    for path in dat_paths:
        try:
            with open(path, "rb") as fp:
                head = fp.read(31)
        except OSError:
            continue
        if len(head) < 31 or head[:6] != V2_MAGIC:
            continue
        ct = head[HEADER_LEN:HEADER_LEN + 16]
        groups[ct] = groups.get(ct, 0) + 1
    ordered = sorted(groups.items(), key=lambda kv: kv[1], reverse=True)
    # 优先 JPEG/PNG（magic 更长、校验更严）作为主 oracle
    return [ct for ct, _ in ordered[:limit_groups]]


def _iter_regions(proc, raw_only: bool):
    for base, size in proc.regions():
        yield base, size


def _scan_alnum(proc, oracles: list[bytes]) -> bytes | None:
    for base, size in _iter_regions(proc, raw_only=False):
        offset = 0
        tail = b""
        while offset < size:
            data = proc.read(base + offset, min(CHUNK, size - offset))
            if not data:
                offset += 0x1000
                tail = b""
                continue
            combined = tail + data
            for m in ALNUM_RE.finditer(combined):
                token = m.group()
                for key in _candidate_keys(token):
                    if _try_key(key, oracles):
                        return key
                # token 内可能嵌有密钥（例如 JSON 包裹），逐窗口尝试
                for start in range(0, max(0, len(token) - 15), 8):
                    key = token[start:start + 16]
                    if _try_key(key, oracles):
                        return key
            tail = combined[-64:]
            offset += len(data)
    return None


def _scan_raw_worker(args):
    pid, base, size, oracles = args
    from wechat_export.key_provider import RemoteProcess as _RP
    try:
        with _RP(pid) as proc:
            offset = 0
            while offset < size:
                data = proc.read(base + offset, min(4 << 20, size - offset))
                if not data:
                    offset += 0x1000
                    continue
                for i in range(0, len(data) - 15, 16):
                    key = data[i:i + 16]
                    pt = _decrypt_block(key, oracles[0])
                    if pt is not None and _plaintext_ok(pt):
                        if _try_key(key, oracles):
                            return key
                offset += len(data)
    except Exception:  # noqa: BLE001
        return None
    return None


def _scan_raw(pids: list[int], oracles: list[bytes]) -> bytes | None:
    tasks = []
    for pid in pids:
        try:
            with RemoteProcess(pid) as proc:
                for base, size in proc.regions():
                    if size <= 0:
                        continue
                    tasks.append((pid, base, size, oracles))
        except Exception:  # noqa: BLE001
            continue
    if not tasks:
        return None
    workers = min(RAW_WORKERS, len(tasks))
    if workers <= 1:
        for task in tasks:
            key = _scan_raw_worker(task)
            if key:
                return key
        return None
    with mp.Pool(workers) as pool:
        for key in pool.imap_unordered(_scan_raw_worker, tasks):
            if key:
                pool.terminate()
                return key
    return None


def extract_image_key(dat_paths, deep: bool = False) -> bytes | None:
    """从微信进程内存提取图片 AES 密钥；失败返回 None。"""
    oracles = collect_oracles(dat_paths)
    if not oracles:
        return None
    pids = find_weixin_pids()
    if not pids:
        return None
    for pid in pids:
        try:
            with RemoteProcess(pid) as proc:
                key = _scan_alnum(proc, oracles)
                if key:
                    return key
        except Exception:  # noqa: BLE001
            continue
    if deep:
        return _scan_raw(pids, oracles)
    return None
