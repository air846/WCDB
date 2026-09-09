"""数据库密钥提取（M0 实测的微信 4.x 内存扫描）。

微信 4.x 每个 `.db` 有独立的 32 字节 enc_key。WCDB 在进程内存中为每个打开的
库维护一个 SQLCipher codec 上下文，其中 keyspec 明文为
`x'<64hex enc_key><32hex salt>'`。4.1.13 起该字符串以 32 字节重复 XOR pad
混淆；pad 可由 codec 上下文里的 salt（与 DB 文件头一致）在运行时推导，
无需硬编码、不写盘、不注入。

对外主要接口：
- `parse_key_hex`：手动密钥格式校验
- `extract_keys(db_paths)`：扫描运行中的 Weixin.exe，返回 {salt_hex: key_hex}
- `KeyProvider.get_keys()`：手动 → 预置 → 内存提取 三级兜底
"""

import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac as hmac_mod
import struct
import subprocess
from pathlib import Path

from wechat_export.exceptions import ConfigError, KeyExtractError

KEY_BYTES = 32
SALT_BYTES = 16
PAGE_SIZE = 4096
RESERVE = 80
HMAC_SIZE = 64
KEYS_SPEC_LEN = 99  # x' + 64hex + 32hex + '

# SQLCipher codec 配置常量前缀（M0 实测 4.1.13.63）
CODEC_PREFIX = bytes.fromhex(
    "0000000000e80300020000001000000020000000100000001000000000100000"
    "630000005000000040000000000000000200000002000000"
)
OFF_SALT_PTR = 0x48
OFF_HMAC_SALT_PTR = 0x50
OFF_READ_CTX = 0x68
OFF_WRITE_CTX = 0x70
OFF_KEYSPEC_PTR = 0x20

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE_PAGE_TYPES = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}


def parse_key_hex(s: str) -> str:
    s = s.strip()
    if s.startswith("x'") and s.endswith("'"):
        s = s[2:-1]
    if s.lower().startswith("0x"):
        s = s[2:]
    s = s.replace(" ", "").replace(":", "").replace("x'", "").replace("'", "")
    if len(s) != KEY_BYTES * 2 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ConfigError(
            f"密钥格式错误：需要 {KEY_BYTES * 2} 位 16 进制字符",
            hint="示例：--key-hex <32字节hex>；可用工具自动从微信进程内存提取",
        )
    return s.lower()


def derive_pad(keyspec_ct: bytes, salt_hex: str) -> bytes | None:
    """由 keyspec 密文 + 已知 salt 推导 32 字节 XOR pad；校验失败返回 None。"""
    if len(keyspec_ct) != KEYS_SPEC_LEN or len(salt_hex) != SALT_BYTES * 2:
        return None
    salt_ascii = salt_hex.encode("ascii")
    pad = bytearray(32)
    pad[2:32] = bytes(a ^ b for a, b in zip(keyspec_ct[66:96], salt_ascii[0:30]))
    pad[0:2] = bytes(a ^ b for a, b in zip(keyspec_ct[96:98], salt_ascii[30:32]))
    if pad[0:2] != bytes(a ^ b for a, b in zip(keyspec_ct[0:2], b"x'")):
        return None
    return bytes(pad)


def deobfuscate(data: bytes, pad: bytes) -> bytes:
    return bytes(b ^ pad[i % 32] for i, b in enumerate(data))


def verify_key(key_hex: str, page1: bytes) -> bool:
    """用 SQLCipher HMAC-SHA512 校验 key 是否匹配该库的页 1。"""
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        return False
    if len(key) != KEY_BYTES or len(page1) < PAGE_SIZE:
        return False
    salt = page1[:SALT_BYTES]
    mac_key = hashlib.pbkdf2_hmac(
        "sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=KEY_BYTES)
    h = hmac_mod.new(mac_key, page1[SALT_BYTES:PAGE_SIZE - RESERVE + 16], hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return h.digest() == page1[PAGE_SIZE - HMAC_SIZE:PAGE_SIZE]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def parse_codec_context(read, address: int, page1_map: dict | None = None) -> list[tuple[str, str]]:
    """解析一个 codec 上下文，返回 [(salt_hex, key_hex)]。read(addr,size)->bytes。"""
    codec = read(address, 0x88)
    if len(codec) < 0x88 or codec[:len(CODEC_PREFIX)] != CODEC_PREFIX:
        return []
    fields = struct.unpack_from("<15I", codec, 0)
    salt = read(_u64(codec, OFF_SALT_PTR), fields[3])
    if len(salt) != fields[3]:
        return []
    hmac_salt = read(_u64(codec, OFF_HMAC_SALT_PTR), fields[3])
    if hmac_salt and hmac_salt != bytes(b ^ 0x3A for b in salt):
        return []
    salt_hex = salt.hex()
    if page1_map is not None and salt_hex not in page1_map:
        return []
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for offset in (OFF_READ_CTX, OFF_WRITE_CTX):
        caddr = _u64(codec, offset)
        if not caddr:
            continue
        cipher = read(caddr, 0x28)
        if len(cipher) < 0x28:
            continue
        keyspec_ct = read(_u64(cipher, OFF_KEYSPEC_PTR), fields[8])
        if len(keyspec_ct) != fields[8]:
            continue
        pad = derive_pad(keyspec_ct, salt_hex)
        if pad is None:
            continue
        keyspec = deobfuscate(keyspec_ct, pad)
        if not (keyspec.startswith(b"x'") and keyspec.endswith(b"'")
                and len(keyspec) == KEYS_SPEC_LEN):
            continue
        key_hex = keyspec[2:66].decode("ascii", "ignore")
        if keyspec[66:98].decode("ascii", "ignore") != salt_hex:
            continue
        if key_hex in seen:
            continue
        seen.add(key_hex)
        if page1_map is not None and not verify_key(key_hex, page1_map[salt_hex]):
            continue
        results.append((salt_hex, key_hex))
    return results


def scan_pattern(read, regions, pattern: bytes = CODEC_PREFIX, chunk: int = 8 << 20) -> list[int]:
    """在可读内存区域中扫描 pattern，返回命中地址。"""
    hits: list[int] = []
    overlap = len(pattern) - 1
    for base, size in regions:
        offset = 0
        tail = b""
        while offset < size:
            data = read(base + offset, min(chunk, size - offset))
            if not data:
                offset += 0x1000
                tail = b""
                continue
            combined = tail + data
            combined_base = base + offset - len(tail)
            start = 0
            while True:
                idx = combined.find(pattern, start)
                if idx < 0:
                    break
                hits.append(combined_base + idx)
                start = idx + 1
            tail = combined[-overlap:] if overlap else b""
            offset += len(data)
    return hits


class RemoteProcess:
    """只读访问 Windows 进程内存。"""

    def __init__(self, pid: int):
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise KeyExtractError("需要 64 位 Python 才能读取 64 位微信进程")
        self.pid = pid
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.OpenProcess.restype = wt.HANDLE
        self.handle = self.kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.handle:
            raise KeyExtractError(
                f"无法打开进程 pid={pid}（权限不足？）",
                hint="请以管理员身份运行，或改用 --key-hex/--keys-file",
            )
        self.kernel32.ReadProcessMemory.argtypes = [
            wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.kernel32.ReadProcessMemory.restype = wt.BOOL

    class _MBI(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD),
                    ("_p", wt.WORD), ("RegionSize", ctypes.c_size_t),
                    ("State", wt.DWORD), ("Protect", wt.DWORD),
                    ("Type", wt.DWORD), ("_p2", wt.DWORD)]

    def read(self, address: int, size: int) -> bytes:
        if address <= 0 or size <= 0 or address + size > 0x800000000000:
            return b""
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t(0)
        ok = self.kernel32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(n))
        if not ok and n.value == 0:
            return b""
        return buf.raw[:n.value]

    def regions(self):
        self.kernel32.VirtualQueryEx.argtypes = [
            wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(self._MBI), ctypes.c_size_t]
        self.kernel32.VirtualQueryEx.restype = ctypes.c_size_t
        address = 0
        while address < 0x7FFFFFFFFFFF:
            mbi = self._MBI()
            if not self.kernel32.VirtualQueryEx(
                    self.handle, ctypes.c_void_p(address), ctypes.byref(mbi),
                    ctypes.sizeof(mbi)):
                break
            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize)
            nxt = base + size
            if size <= 0 or nxt <= address:
                break
            protect = int(mbi.Protect) & 0xFF
            if (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD)
                    and protect != PAGE_NOACCESS and protect in READABLE_PAGE_TYPES):
                yield base, size
            address = nxt

    def close(self):
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def find_weixin_pid() -> int:
    """返回内存占用最大的 Weixin.exe PID。"""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True).stdout
    best = (0, 0)
    for line in out.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 5 and parts[0].strip('"').lower() == "weixin.exe":
            pid = int(parts[1])
            mem = int(parts[4].replace(",", "").replace(" K", "").strip() or "0")
            if mem > best[1]:
                best = (pid, mem)
    if not best[0]:
        raise KeyExtractError(
            "未发现运行中的 Weixin.exe 进程",
            hint="请先启动并登录微信 4.x，或改用 --key-hex/--keys-file")
    return best[0]


def extract_keys(db_paths) -> dict[str, str]:
    """扫描 Weixin.exe 内存，返回 {salt_hex: key_hex}（仅返回能通过 HMAC 验证的）。"""
    page1_map: dict[str, bytes] = {}
    for path in db_paths:
        path = Path(path)
        try:
            with open(path, "rb") as fp:
                page1 = fp.read(PAGE_SIZE)
        except OSError:
            continue
        if len(page1) == PAGE_SIZE:
            page1_map[page1[:SALT_BYTES].hex()] = page1
    if not page1_map:
        return {}
    pid = find_weixin_pid()
    keys: dict[str, str] = {}
    with RemoteProcess(pid) as proc:
        for address in scan_pattern(proc.read, proc.regions()):
            for salt_hex, key_hex in parse_codec_context(proc.read, address, page1_map):
                keys[salt_hex] = key_hex
    return keys


class KeyProvider:
    """手动 → 预置 → 进程内存 三级兜底，返回 {salt_hex: key_hex}。"""

    def __init__(self, db_paths=None, manual_key: str | None = None,
                 keys: dict | None = None):
        self.db_paths = [Path(p) for p in (db_paths or [])]
        self.manual_key = parse_key_hex(manual_key) if manual_key else None
        self.keys = {k.lower(): v.lower() for k, v in (keys or {}).items()}

    def get_keys(self) -> dict[str, str]:
        if self.manual_key:
            result = {}
            for path in self.db_paths:
                try:
                    with open(path, "rb") as fp:
                        salt = fp.read(SALT_BYTES)
                except OSError:
                    continue
                result[salt.hex()] = self.manual_key
            return result
        if self.keys:
            return dict(self.keys)
        keys = extract_keys(self.db_paths)
        if not keys:
            raise KeyExtractError(
                "未能从微信进程内存提取到可用密钥",
                hint="请确认微信已登录；或改用 --key-hex/--keys-file 手动提供",
            )
        return keys
