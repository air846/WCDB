"""SILK 语音 → WAV 解码（依赖可选 rsilk）。"""

import io
import wave

import pytest

from tests.fixtures.silk_factory import silk_bytes
from wechat_export import voice_decoder


def test_decode_silk_returns_wav():
    pytest.importorskip("rsilk")
    wav = voice_decoder.decode_silk(silk_bytes())
    assert wav is not None
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == 24000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert 0.3 < w.getnframes() / 24000 < 0.9


def test_decode_silk_invalid_returns_none():
    assert voice_decoder.decode_silk(b"garbage") is None
    assert voice_decoder.decode_silk(b"") is None
    assert voice_decoder.decode_silk(None) is None


def test_decode_silk_without_rsilk(monkeypatch):
    monkeypatch.setattr(voice_decoder, "_import_rsilk", lambda: None)
    assert voice_decoder.decode_silk(b"\x02#!SILK_V3\x00") is None


def test_voice_available_matches_import():
    assert voice_decoder.VOICE_AVAILABLE is (
        voice_decoder._import_rsilk() is not None)
