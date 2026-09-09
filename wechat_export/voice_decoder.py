"""微信 SILK 语音 → WAV（浏览器可直接播放）。

微信语音（`media_0.db.VoiceInfo.voice_data`）是 SILK v3 编码，浏览器无法
播放。本模块用可选依赖 `rsilk` 解码为 24kHz 单声道 PCM 并封装 WAV。

未安装 rsilk 时 `VOICE_AVAILABLE=False`，`decode_silk` 返回 None，调用方
应回退为原样归档 `.silk` 并提供下载链接。
"""

import io
import wave

SAMPLE_RATE = 24000


def _import_rsilk():
    try:
        import rsilk  # noqa: PLC0415
        return rsilk
    except ImportError:
        return None


VOICE_AVAILABLE = _import_rsilk() is not None


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def decode_silk(data: bytes | None, sample_rate: int = SAMPLE_RATE) -> bytes | None:
    """SILK → WAV 字节；失败或缺少 rsilk 返回 None。"""
    if not data:
        return None
    rsilk = _import_rsilk()
    if rsilk is None:
        return None
    try:
        pcm = rsilk.decode(bytes(data), sample_rate)
    except Exception:  # noqa: BLE001  # 损坏/非标准 SILK
        return None
    if not pcm:
        return None
    return _wav_bytes(pcm, sample_rate)
