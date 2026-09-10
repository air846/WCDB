"""可选：从微信 CDN 补下本机缺失的表情（默认关闭，需显式 `--fetch-emoji`）。

消息 XML 的 `<emoji>` 带 `cdnurl`（`http://vweixinf.tc.qq.com/.../stodownload?m=<md5>&filekey=...`）
与每表情的 `aeskey`。本模块只在该开关打开时联网，且：

- 仅允许微信/腾讯自有域名的 http(s) 地址（`filekey` 是消息自带的签名，可能已失效）；
- 串行 + 固定间隔 + 超时，失败一律降级为占位，不影响导出成功与退出码；
- 原始响应缓存到 `<out>/.emoji_cache/<md5>`（在 `media/` 之外，不受 `prune()` 影响），
  重复导出不会重复下载。

下载内容是否加密尚无定论，因此按「原样 → 表情全局密钥（CBC/IV=key）→
消息自带的 per-sticker `aeskey`（CBC/ECB）」逐级尝试，识别不出即视为失败。
"""

import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util import Padding

from wechat_export.image_decoder import decrypt_emoji, detect_emoji_plain

BLOCK = 16
MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
URL_RE = re.compile(r"^https?://", re.I)
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# 只信任微信/腾讯自有域名，避免消息内容里的地址把请求带去任意主机
ALLOWED_HOSTS = (".qq.com", ".tencent.com", ".qpic.cn", ".wechat.com")


def _aes_candidates(aes_key_hex: str) -> list[bytes]:
    """由消息里的 `aeskey` 派生候选密钥：32 位 hex → 16 字节；16/24/32 位 ASCII 原样。"""
    s = (aes_key_hex or "").strip()
    if len(s) == 32 and HEX_RE.match(s):
        return [bytes.fromhex(s)]
    raw = s.encode("utf-8", "ignore")
    return [raw] if len(raw) in (16, 24, 32) else []


def _try_aes(data: bytes, key: bytes) -> bytes | None:
    for mode, iv in ((AES.MODE_CBC, key), (AES.MODE_CBC, b"\x00" * BLOCK),
                     (AES.MODE_ECB, None)):
        try:
            cipher = AES.new(key, mode, iv) if iv else AES.new(key, mode)
            plain = cipher.decrypt(data)
        except (ValueError, KeyError):
            continue
        try:
            plain = Padding.unpad(plain, BLOCK)
        except ValueError:
            pass
        if detect_emoji_plain(plain) is not None:
            return plain
    return None


def decode_emoji_payload(data: bytes, emoji_key: bytes | None,
                         aes_key_hex: str = "") -> tuple[bytes, str] | None:
    """下载内容 → (明文, ext)；逐级尝试解密，识别不出返回 None。"""
    fmt = detect_emoji_plain(data)
    if fmt is not None:
        return data, fmt[1]
    if len(data) < BLOCK or len(data) % BLOCK:
        return None
    if emoji_key:
        plain = decrypt_emoji(data, emoji_key)
        if plain is not None:
            return plain, detect_emoji_plain(plain)[1]
    for key in _aes_candidates(aes_key_hex):
        plain = _try_aes(data, key)
        if plain is not None:
            return plain, detect_emoji_plain(plain)[1]
    return None


class EmojiFetcher:
    """按 `cdnurl` 串行补下缺失表情；达到上限或出错后只走缓存。"""

    def __init__(self, media_root, emoji_key: bytes | None = None,
                 limit: int = 200, timeout: float = 10.0, delay: float = 0.3):
        self.cache_dir = Path(media_root).parent / ".emoji_cache"
        self.emoji_key = emoji_key
        self.limit = max(0, limit)
        self.timeout = timeout
        self.delay = delay
        self.attempts = 0
        self.downloaded = 0
        self.failed = 0
        self._last = 0.0

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.limit

    def fetch(self, md5: str, url: str,
              aes_key_hex: str = "") -> tuple[bytes, str] | None:
        """取回并解码一个表情；非法输入/超额/失败均返回 None。"""
        if not MD5_RE.match(md5 or "") or not self._allowed(url):
            return None
        data = self._read_cache(md5)
        if data is None:
            if self.exhausted:
                return None
            data = self._download(md5, url)
            if data is None:
                return None
        return decode_emoji_payload(data, self.emoji_key, aes_key_hex)

    @staticmethod
    def _allowed(url: str) -> bool:
        if not URL_RE.match(url or ""):
            return False
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return bool(host) and host.endswith(ALLOWED_HOSTS)

    def _read_cache(self, md5: str) -> bytes | None:
        try:
            return (self.cache_dir / md5).read_bytes()
        except OSError:
            return None

    def _download(self, md5: str, url: str) -> bytes | None:
        self.attempts += 1
        gap = self.delay - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = resp.read()
        except Exception:  # noqa: BLE001  # 403/超时/DNS 等一律降级
            self.failed += 1
            return None
        if not data:
            self.failed += 1
            return None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / md5).write_bytes(data)
        except OSError:
            pass
        self.downloaded += 1
        return data
