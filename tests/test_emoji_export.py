"""表情在导出流水线中的行为：解密、多源检索、降级、重导判定、渲染。"""

import json

import pytest

from tests.fixtures.emoji_factory import (
    encrypt_emoji, gif_bytes, jpeg_bytes, png_bytes, wxgf_bytes,
)
from wechat_export import cli
from wechat_export.exporter.media_archive import MediaArchive
from wechat_export.message_model import Media

EMOJI_KEY = bytes(range(16, 32))
MD5 = "a1" * 16


def _archive(tmp_path):
    return MediaArchive(tmp_path / "out" / "media")


def _resolver(tmp_path, key=EMOJI_KEY):
    return cli.MediaResolver(tmp_path, _archive(tmp_path), {}, {}, emoji_key=key)


def _put(tmp_path, rel, data):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_resolver_decrypts_persist(tmp_path):
    plain = gif_bytes()
    _put(tmp_path, f"business/emoticon/Persist/{MD5[:2]}/{MD5}",
         encrypt_emoji(plain, EMOJI_KEY))
    media = _resolver(tmp_path).resolve(Media(kind="emoji", md5=MD5), "wxid_b", 1)
    assert media.status == "ok"
    assert media.ext == ".gif"
    assert (tmp_path / "out" / media.rel_path).read_bytes() == plain


def test_resolver_reads_cache_dir(tmp_path):
    plain = png_bytes()
    _put(tmp_path, f"cache/2026-09/Emoticon/{MD5[:2]}/{MD5}",
         encrypt_emoji(plain, EMOJI_KEY))
    media = _resolver(tmp_path).resolve(Media(kind="emoji", md5=MD5), "wxid_b", 1)
    assert media.ext == ".png"
    assert (tmp_path / "out" / media.rel_path).read_bytes() == plain


def test_resolver_falls_back_to_thumb(tmp_path):
    _put(tmp_path, f"business/emoticon/Thumb/{MD5[:2]}/{MD5}.thumb",
         encrypt_emoji(jpeg_bytes(), EMOJI_KEY))
    media = _resolver(tmp_path).resolve(Media(kind="emoji", md5=MD5), "wxid_b", 1)
    assert media.ext == ".jpg"


def test_resolver_prefers_persist_over_thumb(tmp_path):
    _put(tmp_path, f"business/emoticon/Persist/{MD5[:2]}/{MD5}",
         encrypt_emoji(png_bytes(), EMOJI_KEY))
    _put(tmp_path, f"business/emoticon/Thumb/{MD5[:2]}/{MD5}.thumb",
         encrypt_emoji(jpeg_bytes(), EMOJI_KEY))
    media = _resolver(tmp_path).resolve(Media(kind="emoji", md5=MD5), "wxid_b", 1)
    assert media.ext == ".png"


def test_resolver_prefers_newest_cache_month(tmp_path):
    _put(tmp_path, f"cache/2026-08/Emoticon/{MD5[:2]}/{MD5}",
         encrypt_emoji(jpeg_bytes(), EMOJI_KEY))
    _put(tmp_path, f"cache/2026-09/Emoticon/{MD5[:2]}/{MD5}",
         encrypt_emoji(png_bytes(), EMOJI_KEY))
    media = _resolver(tmp_path).resolve(Media(kind="emoji", md5=MD5), "wxid_b", 1)
    assert media.ext == ".png"


def test_resolver_transcodes_wxgf_emoji(tmp_path):
    from wechat_export.image_decoder import WXGF_AVAILABLE
    if not WXGF_AVAILABLE:
        pytest.skip("av 未安装")
    _put(tmp_path, f"business/emoticon/Persist/{MD5[:2]}/{MD5}",
         encrypt_emoji(wxgf_bytes(), EMOJI_KEY))
    media = _resolver(tmp_path).resolve(Media(kind="emoji", md5=MD5), "wxid_b", 1)
    assert media.ext == ".jpg"
    assert (tmp_path / "out" / media.rel_path).read_bytes().startswith(b"\xff\xd8")


def test_resolver_without_key_keeps_bin(tmp_path):
    data = encrypt_emoji(gif_bytes(), EMOJI_KEY)
    _put(tmp_path, f"business/emoticon/Persist/{MD5[:2]}/{MD5}", data)
    resolver = _resolver(tmp_path, key=None)
    media = resolver.resolve(Media(kind="emoji", md5=MD5), "wxid_b", 1)
    assert media.ext == ".bin"
    assert (tmp_path / "out" / media.rel_path).read_bytes() == data
    assert resolver.emoji_pending == 1
    assert resolver.emoji_decoded == 0


def test_resolver_wrong_key_keeps_bin(tmp_path):
    data = encrypt_emoji(gif_bytes(), EMOJI_KEY)
    _put(tmp_path, f"business/emoticon/Persist/{MD5[:2]}/{MD5}", data)
    resolver = _resolver(tmp_path, key=b"\x00" * 16)
    media = resolver.resolve(Media(kind="emoji", md5=MD5), "wxid_b", 1)
    assert media.ext == ".bin"
    assert (tmp_path / "out" / media.rel_path).read_bytes() == data


@pytest.mark.parametrize("bad", ["../../x", "a*", "", "not-a-md5"])
def test_resolver_rejects_bad_md5(tmp_path, bad):
    media = _resolver(tmp_path).resolve(Media(kind="emoji", md5=bad), "wxid_b", 1)
    assert media.status == "missing"
    emoji_dir = tmp_path / "out" / "media" / "emoji"
    assert not emoji_dir.exists() or not any(emoji_dir.iterdir())


def test_prune_removes_stale_bin(tmp_path):
    arc = _archive(tmp_path)
    stale = tmp_path / "out" / "media" / "emoji" / f"{MD5}.bin"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old-ciphertext")
    arc.save_bytes(gif_bytes(), "emoji", ".gif", md5=MD5)
    arc.prune()
    assert not stale.exists()
    assert (tmp_path / "out" / "media" / "emoji" / f"{MD5}.gif").exists()


def test_render_emoji_gif_as_image(tmp_path):
    from wechat_export.exporter.html_renderer import HtmlRenderer
    from wechat_export.message_model import Message, Session

    msg = Message(msg_id="1", ts=1700000000000, type=47, type_name="表情",
                  direction="in",
                  sender={"wxid": "b", "name": "B", "is_self": False},
                  content="",
                  media=Media(kind="emoji", ext=".gif",
                              rel_path="media/emoji/x.gif"))
    HtmlRenderer().render_session(tmp_path / "s", Session(id="s", name="S"),
                                  [msg], {"messages": 1})
    html = (tmp_path / "s" / "index.html").read_text(encoding="utf-8")
    assert '<img src="media/emoji/x.gif"' in html
    assert "[表情]" not in html


def _write_stats(out, **stats):
    out.mkdir(parents=True, exist_ok=True)
    (out / "session.json").write_text(json.dumps({
        "id": "s", "name": "S", "stats": stats}), encoding="utf-8")


def test_needs_reexport_with_emoji_key_only(tmp_path):
    out = tmp_path / "s"
    _write_stats(out, messages=1, format_version=cli.EXPORT_FORMAT_VERSION,
                 image_key=True, emoji_key=False,
                 wxgf_available=cli.WXGF_AVAILABLE,
                 voice_available=cli.VOICE_AVAILABLE)
    assert cli._needs_reexport(out, None, EMOJI_KEY) is True   # 只有表情密钥也要重导
    assert cli._needs_reexport(out, None, None) is False       # 本次没密钥 → 不重导


def test_needs_reexport_when_fetch_limit_increases(tmp_path):
    out = tmp_path / "s"
    _write_stats(out, messages=1, format_version=cli.EXPORT_FORMAT_VERSION,
                 image_key=True, emoji_key=True, emoji_store=True,
                 emoji_fetch_limit=3,
                 wxgf_available=cli.WXGF_AVAILABLE,
                 voice_available=cli.VOICE_AVAILABLE)
    assert cli._needs_reexport(out, b"k" * 16, EMOJI_KEY, True, 3) is False
    assert cli._needs_reexport(out, b"k" * 16, EMOJI_KEY, True, 200) is True
    # 只开了 --fetch-emoji 而没有密钥时也应触发
    assert cli._needs_reexport(out, None, None, False, 5) is True


def test_needs_reexport_not_downgraded_without_emoji_key(tmp_path):
    out = tmp_path / "s"
    _write_stats(out, messages=1, format_version=cli.EXPORT_FORMAT_VERSION,
                 image_key=True, emoji_key=True,
                 wxgf_available=cli.WXGF_AVAILABLE,
                 voice_available=cli.VOICE_AVAILABLE)
    # 本次没拿到表情密钥：不能重导，否则会把上次的 .gif 覆盖成 .bin 并被 prune 清掉
    assert cli._needs_reexport(out, b"k" * 16, None) is False
    assert cli._needs_reexport(out, b"k" * 16, EMOJI_KEY) is False
