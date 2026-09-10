"""微信 `.dat` 图片解码（V1 单字节 XOR / V2 AES-128-ECB + XOR）。

微信 4.x（实测 4.1.13.63）图片为 **V2 容器**：

    [6B magic: 07 08 'V' '2' 08 07][4B aes_size LE][4B xor_size LE][1B flag]
    [aligned_aes_size 字节 AES-128-ECB（PKCS7）][raw 明文][xor_size 字节单字节 XOR]

- `aligned_aes_size` = aes_size 向上取整到 16 的倍数；若 aes_size 已是 16 的倍数，
  PKCS7 会再补一整块（+16）。
- AES 密钥为全局 16 字节（可由 `image_key` 从微信进程内存提取或 `--image-key` 提供）。
- XOR 密钥通常为 0xED（可由文件尾部 `FF D9`/`IEND` 等已知结尾反推）。

旧版（V1 / 3.x）为整文件单字节 XOR，key 由文件头与图片 magic 反推。

表情（emoji）本地文件另有一套加密，见 `decrypt_emoji`：AES-128-CBC / IV=key / PKCS7，
明文为 wxgf / GIF / PNG / JPEG。

本模块只做纯解码，不接触进程内存；密钥提取见 `wechat_export.image_key`
与 `wechat_export.emoji_key`。
"""

import struct

from Crypto.Cipher import AES
from Crypto.Util import Padding

V2_MAGIC = b"\x07\x08V2\x08\x07"
V1_MAGIC = b"\x07\x08V1\x08\x07"
V1_FIXED_KEY = b"cfcd208495d565ef"  # md5("0")[:16]
HEADER_LEN = 15
BLOCK = 16

# (magic, ext, renderable)  —— 按 magic 长度降序匹配
IMAGE_MAGICS: list[tuple[bytes, str, bool]] = [
    (b"\x89PNG\r\n\x1a\n", ".png", True),
    (b"GIF87a", ".gif", True),
    (b"GIF89a", ".gif", True),
    (b"wxgf", ".wxgf", False),          # 微信自有容器（HEVC），浏览器不可直接渲染
    (b"\xff\xd8\xff", ".jpg", True),
    (b"RIFF", ".webp", True),
    (b"II*\x00", ".tif", False),
    (b"MM\x00*", ".tif", False),
    (b"BM", ".bmp", True),
]

# 尾部已知结尾 → (末尾字节数, 明文结尾)
TAIL_SIGNATURES = {
    ".jpg": (b"\xff\xd9", 2),
    ".png": (b"\xae\x42\x60\x82", 4),
    ".gif": (b";", 1),
    ".bmp": (None, 0),
    ".webp": (None, 0),
    ".tif": (None, 0),
    ".wxgf": (None, 0),
}

DEFAULT_XOR_KEY = 0xED


def aligned_aes_size(aes_size: int) -> int:
    """PKCS7 对齐：向上取整到 16 的倍数；已对齐则再补一整块。"""
    return aes_size + (BLOCK - aes_size % BLOCK)


def detect_format(data: bytes) -> tuple[str, str, bool] | None:
    """返回 (magic_kind, ext, renderable) 或 None。"""
    for magic, ext, renderable in IMAGE_MAGICS:
        if data.startswith(magic):
            if magic == b"RIFF" and data[8:12] != b"WEBP":
                continue
            return magic.decode("latin1"), ext, renderable
    return None


def parse_v2_header(data: bytes) -> dict | None:
    """解析 V2/V1 容器头；非法返回 None。"""
    if len(data) < HEADER_LEN or data[:6] not in (V2_MAGIC, V1_MAGIC):
        return None
    aes_size, xor_size = struct.unpack_from("<LL", data, 6)
    aligned = aligned_aes_size(aes_size)
    if HEADER_LEN + aligned + xor_size > len(data):
        return None
    return {"aes_size": aes_size, "xor_size": xor_size, "aligned_aes_size": aligned,
            "flag": data[14]}


def derive_xor_key(data: bytes, ext: str) -> int | None:
    """由文件尾部已知结尾反推单字节 XOR key。"""
    tail, n = TAIL_SIGNATURES.get(ext, (None, 0))
    if not tail or len(data) < n:
        return None
    key = data[-n] ^ tail[0]
    if all((data[-n + i] ^ key) == b for i, b in enumerate(tail)):
        return key
    return None


def global_xor_key(samples: list[bytes]) -> int:
    """从多个文件尾部统计最常见的 JPEG 结尾 XOR key（兜底 0xED）。"""
    counts: dict[int, int] = {}
    for data in samples:
        if len(data) < 2:
            continue
        key = data[-2] ^ 0xFF
        if (data[-1] ^ key) == 0xD9:
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return DEFAULT_XOR_KEY
    return max(counts, key=counts.get)


def decode_v2(data: bytes, aes_key: bytes,
              xor_key: int | None = None) -> tuple[bytes, str, bool] | None:
    """解码 V2 容器，返回 (明文, ext, renderable) 或 None。"""
    if aes_key is None or len(aes_key) not in (16, 24, 32):
        return None
    header = parse_v2_header(data)
    if header is None:
        return None
    aligned = header["aligned_aes_size"]
    start = HEADER_LEN
    end = start + aligned
    try:
        plain_aes = Padding.unpad(
            AES.new(aes_key, AES.MODE_ECB).decrypt(data[start:end]), BLOCK)
    except (ValueError, KeyError):
        return None
    raw_end = len(data) - header["xor_size"]
    raw = data[end:raw_end] if end < raw_end else b""
    tail = data[raw_end:]
    fmt = detect_format(plain_aes)
    if fmt is None:
        return None
    _, ext, renderable = fmt
    key = derive_xor_key(data, ext)
    if key is None:
        key = xor_key if xor_key is not None else DEFAULT_XOR_KEY
    plain_xor = bytes(b ^ key for b in tail)
    return plain_aes + raw + plain_xor, ext, renderable


def decode_v1(data: bytes) -> tuple[bytes, str, bool] | None:
    """旧格式（3.x）：单字节 XOR。兼容整文件 XOR 与首字节为 key 两种变体。"""
    for key in range(256):
        plain = bytes(b ^ key for b in data[:16])
        fmt = detect_format(plain)
        if fmt is not None:
            _, ext, renderable = fmt
            return bytes(b ^ key for b in data), ext, renderable
    if len(data) > 1:
        key = data[0]
        plain = bytes(b ^ key for b in data[1:17])
        fmt = detect_format(plain)
        if fmt is not None:
            _, ext, renderable = fmt
            return bytes(b ^ key for b in data[1:]), ext, renderable
    return None


def _import_av():
    try:
        import av  # noqa: PLC0415
        return av
    except ImportError:
        return None


WXGF_AVAILABLE = _import_av() is not None


def decode_wxgf(data: bytes, quality: int = 2) -> tuple[bytes, str] | None:
    """`wxgf`（微信 HEVC 容器）→ JPEG；需可选依赖 av，失败返回 None。"""
    av = _import_av()
    if av is None or not data.startswith(b"wxgf"):
        return None
    import fractions
    import io

    try:
        with av.open(io.BytesIO(data), format="hevc") as container:
            frame = next(container.decode(video=0), None)
    except Exception:  # noqa: BLE001
        return None
    if frame is None:
        return None
    try:
        enc = av.CodecContext.create("mjpeg", "w")
        enc.width, enc.height = frame.width, frame.height
        enc.pix_fmt = "yuvj420p"
        enc.time_base = fractions.Fraction(1, 25)
        enc.options = {"qmin": str(quality), "qmax": str(quality)}
        packets = enc.encode(frame.reformat(format="yuvj420p"))
        out = b"".join(bytes(p) for p in packets)
    except Exception:  # noqa: BLE001
        return None
    if not out.startswith(b"\xff\xd8"):
        return None
    return out, ".jpg"


def detect_emoji_plain(data: bytes) -> tuple[str, str, bool] | None:
    """表情明文识别：`detect_format` 之外再认 `wxam`（暂不可渲染）。"""
    if data[:4] == b"wxam":
        return "wxam", ".wxam", False
    return detect_format(data)


def decrypt_emoji(data: bytes, key: bytes | None) -> bytes | None:
    """解密微信表情本地文件（AES-128-CBC / IV=key / PKCS7，整文件一条流）。

    实测 4.1.13.63：`business/emoticon` 的 `Persist`/`Thumb`/`ThumbStore` 与
    账号根 `cache/<YYYY-MM>/Emoticon` 下每个文件都是一条独立 CBC 流，IV 即密钥；
    商店容器 `PersistStore` 是多个表情首尾相接的同一条流，须**整包解密后**按
    `emoticon.db` 偏移切片（切片起点未必 16 对齐，不能单独解密）。

    明文为 wxgf/GIF/PNG/JPEG 之一。密钥错误或非本格式返回 None，不抛异常。
    """
    if not key or len(key) not in (16, 24, 32) or len(data) < BLOCK or len(data) % BLOCK:
        return None
    try:
        plain = AES.new(key, AES.MODE_CBC, key).decrypt(data)
    except (ValueError, KeyError):
        return None
    try:
        plain = Padding.unpad(plain, BLOCK)
    except ValueError:
        pass  # 个别文件可能未带 PKCS7，交由 magic 判定
    return plain if detect_emoji_plain(plain) is not None else None


def decode_raw_aes(data: bytes, aes_key: bytes | None) -> tuple[bytes, str, bool] | None:
    """尝试整文件 AES-ECB（旧版表情容错路径；4.1.13 起实际为 CBC，见 decrypt_emoji）。"""
    if not aes_key or len(aes_key) not in (16, 24, 32) or len(data) < 16:
        return None
    blocks = len(data) // BLOCK
    try:
        plain = AES.new(aes_key, AES.MODE_ECB).decrypt(data[:blocks * BLOCK]) + data[blocks * BLOCK:]
    except (ValueError, KeyError):
        return None
    candidates = [plain]
    try:
        candidates.insert(0, Padding.unpad(plain[:blocks * BLOCK], BLOCK))
    except ValueError:
        pass
    for candidate in candidates:
        fmt = detect_format(candidate)
        if fmt is not None:
            _, ext, renderable = fmt
            return candidate, ext, renderable
    return None


def decode_dat(data: bytes, aes_key: bytes | None = None,
               xor_key: int | None = None) -> tuple[bytes, str, bool] | None:
    """自动识别 V2/V1/旧格式并解码，返回 (明文, ext, renderable) 或 None。"""
    if data[:6] == V2_MAGIC:
        return decode_v2(data, aes_key or b"", xor_key)
    if data[:6] == V1_MAGIC:
        return decode_v2(data, V1_FIXED_KEY, xor_key)
    return decode_v1(data)
