"""数据库密钥提取：手动输入 → 本地落盘 → 进程内存，逐级兜底。

Windows 微信 4.x 密钥机制经 M0 实测确认；本地落盘来源的具体路径
在 M0 完成后以模块级常量补充（当前留空并给出探测指引）。
"""

import ctypes
import os
from pathlib import Path

from wechat_export import db_access
from wechat_export.exceptions import ConfigError, KeyExtractError
from wechat_export.locator import AccountInfo

KEY_BYTES = 32

# M0 实测后在此登记本地落盘密钥候选路径（相对于账号数据根目录或 db_storage）
LOCAL_KEY_CANDIDATE_RELPATHS: list[tuple[Path, Path]] = []  # (root, relpath)


def parse_key_hex(s: str) -> str:
    s = s.strip()
    if s.startswith("x'") and s.endswith("'"):
        s = s[2:-1]
    if s.lower().startswith("0x"):
        s = s[2:]
    # x'..' 形式可能按字节分组（如 x'ef'x'ef'...），剥掉内部 x' 组标记
    s = s.replace(" ", "").replace(":", "").replace("x'", "").replace("'", "")
    if len(s) != KEY_BYTES * 2 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ConfigError(
            f"密钥格式错误：需要 {KEY_BYTES * 2} 位 16 进制字符",
            hint="示例：--key-hex <32字节hex>；可从微信进程内存 dump 或已知方案获取",
        )
    return s.lower()


def collect_dump_candidates(dump_bytes: bytes, limit: int = 20000) -> list[bytes]:
    """滑窗扫描 32 字节候选密钥：跳过低熵窗口（明文/重复字节段），
    按出现顺序去重，最多取 limit 个，交由真实解密验证过滤。"""
    cands: list[bytes] = []
    seen: set[bytes] = set()
    for i in range(0, max(len(dump_bytes) - KEY_BYTES, 0)):
        chunk = dump_bytes[i:i + KEY_BYTES]
        if len(set(chunk)) < 16:  # 随机密钥通常 32 字节几乎全不同
            continue
        if chunk in seen:
            continue
        seen.add(chunk)
        cands.append(chunk)
        if len(cands) >= limit:
            break
    return cands


def extract_key_from_dump(dump_bytes: bytes, db_path: Path) -> str | None:
    """对候选逐一用真实解密验证；成功即返回，失败继续。"""
    for cand in collect_dump_candidates(dump_bytes):
        key = cand.hex()
        try:
            edb = db_access.open_encrypted(db_path, key)
            edb.close()
            return key
        except Exception:  # noqa: BLE001
            continue
    return None


def mini_dump_process(pid: int, out_path: Path) -> Path:
    """对指定进程做 MiniDump（dbghelp）。失败多为权限不足。
    MiniDumpWriteDump 需要 Win32 文件句柄（CreateFileW），不能用 Python fd。"""
    if os.name != "nt":
        raise KeyExtractError("进程内存提取仅支持 Windows", hint="当前系统非 Windows")
    MiniDumpWithFullMemory = 0x00000002
    dbghelp = ctypes.WinDLL("dbghelp")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    ph = kernel32.OpenProcess(0x1F0FFF, False, pid)
    if not ph:
        raise KeyExtractError(
            f"无法打开进程 pid={pid}（可能权限不足）",
            hint="请以管理员身份运行本工具，或改用 --key-hex 手动提供密钥",
        )
    try:
        fh = kernel32.CreateFileW(str(out_path), 0x40000000, 0, None, 2, 0, None)
        if not fh or fh == ctypes.c_void_p(-1).value:
            raise KeyExtractError("无法创建 dump 临时文件",
                                  hint="检查 TEMP 目录写入权限")
        try:
            ok = dbghelp.MiniDumpWriteDump(
                ph, pid, fh, MiniDumpWithFullMemory, None, None, None
            )
        finally:
            kernel32.CloseHandle(fh)
        if not ok:
            raise KeyExtractError(
                "MiniDump 失败（可能权限不足）",
                hint="请以管理员身份运行，或改用 --key-hex",
            )
    finally:
        kernel32.CloseHandle(ph)
    return out_path


def find_weixin_pid() -> int:
    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    ).stdout
    for line in out.splitlines():
        parts = line.split('","')
        if len(parts) >= 2 and parts[0].strip('"') == "Weixin.exe":
            return int(parts[1].strip('"'))
    raise KeyExtractError("未发现运行中的 Weixin.exe 进程", hint="请先启动并登录微信 4.x")


class KeyProvider:
    def __init__(self, db_path: Path, manual_key: str | None = None,
                 account: "AccountInfo | None" = None):
        self.db_path = db_path
        self.manual_key = parse_key_hex(manual_key) if manual_key else None
        self.account = account

    def get_key(self) -> str:
        if self.manual_key:
            self._verify(self.manual_key)
            return self.manual_key
        for root, rel in LOCAL_KEY_CANDIDATE_RELPATHS:
            base = self.account.root if self.account else Path.cwd()
            p = base / rel
            if p.exists():
                key = self._parse_local_key(p.read_text())
                if key and self._verify(key):
                    return key
        pid = find_weixin_pid()
        tmp = Path(os.environ.get("TEMP", ".")) / f"weixin_{pid}.dmp"
        try:
            mini_dump_process(pid, tmp)
            dump = tmp.read_bytes()
        finally:
            tmp.unlink(missing_ok=True)
        key = extract_key_from_dump(dump, self.db_path)
        if key:
            return key
        raise KeyExtractError(
            "未能从本地文件与内存提取到可用密钥",
            hint="请用 --key-hex 手动提供 32 字节密钥",
        )

    def _verify(self, key: str) -> bool:
        try:
            edb = db_access.open_encrypted(self.db_path, key)
            edb.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _parse_local_key(self, text: str) -> str | None:
        stripped = text.strip().strip("'\"")
        try:
            return parse_key_hex(stripped)
        except ConfigError:
            return None
