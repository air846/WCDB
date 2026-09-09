"""合成 SILK 音频（需可选依赖 rsilk；未安装时由调用方 importorskip）。"""

import math
import struct


def silk_bytes(seconds: float = 0.5, rate: int = 24000) -> bytes:
    import rsilk

    n = int(rate * seconds)
    pcm = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(n))
    return rsilk.encode(pcm, rate, rate)
