"""可选联网补下表情的单测（全部离线，不打真实网络）。"""

import io
import urllib.error
import urllib.request

import pytest

from tests.fixtures.emoji_factory import encrypt_emoji, gif_bytes, png_bytes
from wechat_export.cli import MediaResolver
from wechat_export.emoji_fetch import EmojiFetcher, decode_emoji_payload
from wechat_export.exporter.media_archive import MediaArchive
from wechat_export.message_model import Media

EMOJI_KEY = bytes(range(16, 32))
PER_STICKER_KEY = bytes.fromhex("130e2f62736ae7916d9d9a6dd51fd49d")   # 合成
MD5 = "a1" * 16
URL = "http://vweixinf.tc.qq.com/110/20401/stodownload?m=" + MD5 + "&filekey=abc"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, payload: bytes, calls: list | None = None):
    def fake_urlopen(request, timeout=None):
        if calls is not None:
            calls.append(request.full_url)
        return _Resp(payload)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_decode_plain_payload():
    data = gif_bytes()
    assert decode_emoji_payload(data, None) == (data, ".gif")


def test_decode_global_key_payload():
    plain = png_bytes()
    assert decode_emoji_payload(encrypt_emoji(plain, EMOJI_KEY), EMOJI_KEY) == (plain, ".png")


def test_decode_per_sticker_aeskey_payload():
    plain = gif_bytes(b"\x05" * 64)
    data = encrypt_emoji(plain, PER_STICKER_KEY)
    assert decode_emoji_payload(data, None, PER_STICKER_KEY.hex()) == (plain, ".gif")


def test_decode_unknown_payload():
    assert decode_emoji_payload(b"\x00" * 32, None) is None
    assert decode_emoji_payload(b"xyz", None) is None


@pytest.mark.parametrize("url,ok", [
    (URL, True),
    ("https://vweixinf.tc.qq.com/x", True),
    ("http://evil.com/x", False),
    ("http://qq.com.evil.com/x", False),
    ("file:///etc/passwd", False),
    ("", False),
])
def test_host_allowlist(tmp_path, url, ok):
    assert EmojiFetcher(tmp_path / "media", EMOJI_KEY)._allowed(url) is ok


def test_fetch_downloads_once_then_hits_cache(tmp_path, monkeypatch):
    calls: list = []
    _serve(monkeypatch, encrypt_emoji(gif_bytes(), EMOJI_KEY), calls)
    fetcher = EmojiFetcher(tmp_path / "out" / "media", EMOJI_KEY, delay=0)

    assert fetcher.fetch(MD5, URL) == (gif_bytes(), ".gif")
    assert fetcher.fetch(MD5, URL) == (gif_bytes(), ".gif")
    assert len(calls) == 1                      # 第二次命中磁盘缓存，不再发请求
    assert fetcher.attempts == 1 and fetcher.downloaded == 1 and fetcher.failed == 0
    assert (tmp_path / "out" / ".emoji_cache" / MD5).exists()


def test_fetch_respects_limit(tmp_path, monkeypatch):
    _serve(monkeypatch, gif_bytes())
    fetcher = EmojiFetcher(tmp_path / "out" / "media", EMOJI_KEY, limit=1, delay=0)

    assert fetcher.fetch(MD5, URL) is not None
    assert fetcher.exhausted is True
    assert fetcher.fetch("b2" * 16, URL) is None
    assert fetcher.attempts == 1


def test_fetch_handles_network_error(tmp_path, monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("blocked")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    fetcher = EmojiFetcher(tmp_path / "out" / "media", EMOJI_KEY, delay=0)

    assert fetcher.fetch(MD5, URL) is None
    assert fetcher.failed == 1 and fetcher.downloaded == 0


def test_fetch_rejects_bad_input(tmp_path):
    fetcher = EmojiFetcher(tmp_path / "out" / "media", EMOJI_KEY)
    assert fetcher.fetch("not-a-md5", URL) is None
    assert fetcher.fetch(MD5, "http://evil.com/x") is None
    assert fetcher.attempts == 0


def test_resolver_fetches_when_local_missing(tmp_path, monkeypatch):
    _serve(monkeypatch, encrypt_emoji(gif_bytes(b"\x09" * 64), EMOJI_KEY))
    fetcher = EmojiFetcher(tmp_path / "out" / "media", EMOJI_KEY, delay=0)
    resolver = MediaResolver(tmp_path, MediaArchive(tmp_path / "out" / "media"),
                             {}, {}, emoji_key=EMOJI_KEY, emoji_fetcher=fetcher)

    media = resolver.resolve(
        Media(kind="emoji", md5=MD5, cdn_url=URL), "wxid_b", 1)
    assert media.status == "ok"
    assert media.ext == ".gif"
    assert resolver.emoji_fetched == 1


def test_resolver_without_fetcher_stays_missing(tmp_path):
    resolver = MediaResolver(tmp_path, MediaArchive(tmp_path / "out" / "media"),
                             {}, {}, emoji_key=EMOJI_KEY)
    media = resolver.resolve(
        Media(kind="emoji", md5=MD5, cdn_url=URL), "wxid_b", 1)
    assert media.status == "missing"
    assert resolver.emoji_fetched == 0
