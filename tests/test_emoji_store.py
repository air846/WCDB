"""商店表情包容器解析单测（合成 emoticon.db + 合成连续 CBC 流容器）。"""

import hashlib

from tests.fixtures import db_factory as f
from tests.fixtures.emoji_factory import encrypt_emoji, gif_bytes, png_bytes
from wechat_export.cli import MediaResolver
from wechat_export.emoji_store import EmojiStore
from wechat_export.exporter.media_archive import MediaArchive
from wechat_export.message_model import Media

DB_KEY = "cd" * 32
EMOJI_KEY = bytes(range(16, 32))
PACKAGE = "com.tencent.xin.emoticon.person.stiker_1762863369b8283ab711acd1ec"
PKG_MD5 = hashlib.md5(PACKAGE.encode("utf-8")).hexdigest()
MD5_A = "a1" * 16
MD5_B = "b2" * 16

DDL = ('CREATE TABLE kStoreEmoticonFilesTable (package_id_ TEXT, md5_ TEXT,'
       " type_ INTEGER, sort_order_ INTEGER, emoticon_size_ INTEGER,"
       " emoticon_offset_ INTEGER, thumb_size_ INTEGER, thumb_offset_ INTEGER)")


def _make_store(tmp_path, items, key=EMOJI_KEY):
    """items: [(md5, 明文)]；写出一整条连续 CBC 流容器与 emoticon.db。"""
    db = tmp_path / "db_storage" / "emoticon" / "emoticon.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    f.create_encrypted_db(db, DB_KEY)

    statements = [(DDL, ())]
    plain = b""
    for md5, data in items:
        statements.append(
            ("INSERT INTO kStoreEmoticonFilesTable VALUES (?,?,2,1,?,?,0,0)",
             (PACKAGE, md5, len(data), len(plain))))
        plain += data
    f.exec_sql(db, key_hex=DB_KEY, statements=statements)

    container = (tmp_path / "business" / "emoticon" / "PersistStore"
                 / PKG_MD5[:2] / PKG_MD5)
    container.parent.mkdir(parents=True, exist_ok=True)
    container.write_bytes(encrypt_emoji(plain, key))
    return db, {db.read_bytes()[:16].hex(): DB_KEY}


def test_lookup_returns_slices(tmp_path):
    items = [(MD5_A, gif_bytes(b"\x01" * 64)), (MD5_B, png_bytes())]
    _db, keys = _make_store(tmp_path, items)
    store = EmojiStore(tmp_path, tmp_path / "db_storage", keys, EMOJI_KEY)

    assert store.available() is True
    assert store.lookup(MD5_A) == items[0][1]
    assert store.lookup(MD5_B) == items[1][1]
    assert store.lookup(MD5_B.upper()) == items[1][1]   # 大小写归一
    assert store.lookup("ff" * 16) is None


def test_unavailable_without_emoji_key(tmp_path):
    items = [(MD5_A, gif_bytes())]
    _db, keys = _make_store(tmp_path, items)
    assert EmojiStore(tmp_path, tmp_path / "db_storage", keys, None).available() is False


def test_unavailable_without_db_key(tmp_path):
    items = [(MD5_A, gif_bytes())]
    _db, _keys = _make_store(tmp_path, items)
    store = EmojiStore(tmp_path, tmp_path / "db_storage", {}, EMOJI_KEY)
    assert store.available() is False
    assert store.lookup(MD5_A) is None


def test_lookup_none_when_container_missing(tmp_path):
    items = [(MD5_A, gif_bytes())]
    db, keys = _make_store(tmp_path, items)
    (tmp_path / "business" / "emoticon" / "PersistStore" / PKG_MD5[:2]
     / PKG_MD5).unlink()
    store = EmojiStore(tmp_path, tmp_path / "db_storage", keys, EMOJI_KEY)
    assert store.available() is True
    assert store.lookup(MD5_A) is None


def test_resolver_decodes_store_only_emoji(tmp_path):
    items = [(MD5_A, gif_bytes(b"\x07" * 64))]
    _db, keys = _make_store(tmp_path, items)
    store = EmojiStore(tmp_path, tmp_path / "db_storage", keys, EMOJI_KEY)
    archive = MediaArchive(tmp_path / "out" / "media")
    resolver = MediaResolver(tmp_path, archive, {}, {},
                             emoji_key=EMOJI_KEY, emoji_store=store)

    media = resolver.resolve(Media(kind="emoji", md5=MD5_A), "wxid_b", 1)
    assert media.status == "ok"
    assert media.ext == ".gif"
    assert (tmp_path / "out" / media.rel_path).read_bytes() == items[0][1]
    assert resolver.emoji_decoded == 1


def test_resolver_without_store_keeps_missing(tmp_path):
    resolver = MediaResolver(tmp_path, MediaArchive(tmp_path / "out" / "media"),
                             {}, {}, emoji_key=EMOJI_KEY)
    media = resolver.resolve(Media(kind="emoji", md5=MD5_A), "wxid_b", 1)
    assert media.status == "missing"
