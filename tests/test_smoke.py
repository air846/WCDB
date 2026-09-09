import wechat_export


def test_version():
    assert wechat_export.__version__ == "0.1.0"


def test_pycryptodome_importable():
    from Crypto.Cipher import AES  # noqa: F401