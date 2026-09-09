import pytest

from wechat_export.exceptions import (
    ConfigError, DecryptError, ExportError, KeyExtractError,
    PartialExportError, WeChatNotFoundError, format_error,
)

EXPECTED_CODES = {
    ConfigError: 1, WeChatNotFoundError: 2, KeyExtractError: 3,
    DecryptError: 4, PartialExportError: 5,
}


@pytest.mark.parametrize("cls,code", EXPECTED_CODES.items())
def test_error_codes(cls, code):
    assert cls("x").code == code


def test_messages_chinese():
    err = DecryptError("无法解密数据库", hint="可能密钥错误")
    assert "无法解密数据库" in str(err)
    assert err.hint == "可能密钥错误"


def test_format_error_three_lines():
    err = KeyExtractError("未找到密钥", hint="请确认微信已登录")
    text = format_error(err)
    lines = text.strip().splitlines()
    assert len(lines) == 3
    assert lines[2] == "建议操作：请确认微信已登录。"
