import wechat_export


def test_version():
    assert wechat_export.__version__ == "0.1.0"


def test_sqlcipher_importable():
    import sqlcipher3  # noqa: F401