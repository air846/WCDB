import pytest
from tests.fixtures import db_factory as f
from wechat_export.exceptions import ConfigError, KeyExtractError
from wechat_export.key_provider import (
    KeyProvider, collect_dump_candidates, extract_key_from_dump, parse_key_hex,
)

KEY = "d" * 64


@pytest.mark.parametrize("bad", ["", "xyz", "a" * 63, "a" * 65, "a" * 32])
def test_parse_key_hex_bad(bad):
    with pytest.raises(ConfigError):
        parse_key_hex(bad)


@pytest.mark.parametrize("good", ["ab" * 32, "0x" + "cd" * 32, "x'ef" * 32 + "'"])
def test_parse_key_hex_good(good):
    assert len(parse_key_hex(good)) == 64


def test_manual_key_provider(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY)
    kp = KeyProvider(db, manual_key=KEY)
    assert kp.get_key() == KEY


# 高熵 32 字节密钥（内存扫描测试用；真实密钥为随机字节）
KEY_ENTROPY = bytes(range(32)).hex()


def test_dump_candidates_found(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY_ENTROPY)
    blob = b"junk" * 1000 + bytes.fromhex(KEY_ENTROPY) + b"tail" * 100
    found = extract_key_from_dump(blob, db)
    assert found == KEY_ENTROPY


def test_dump_candidates_not_found(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY_ENTROPY)
    # 低熵明文无法通过高熵过滤，应快速返回 None
    assert extract_key_from_dump(b"no key here " * 50, db) is None


def test_no_key_raises(tmp_path):
    db = tmp_path / "m.db"
    f.create_encrypted_db(db, KEY)
    kp = KeyProvider(db, manual_key=None)
    with pytest.raises(KeyExtractError):
        kp.get_key()
