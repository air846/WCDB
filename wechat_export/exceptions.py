class ExportError(Exception):
    """导出工具所有异常的基类。code 对应全局错误码。"""

    code = 0

    def __init__(self, message: str, *, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class ConfigError(ExportError):
    code = 1


class WeChatNotFoundError(ExportError):
    code = 2


class KeyExtractError(ExportError):
    code = 3


class DecryptError(ExportError):
    code = 4


class PartialExportError(ExportError):
    code = 5


def format_error(e: ExportError) -> str:
    hint = e.hint or "请检查上面的信息后重试"
    return (
        f"错误码 {e.code}：{e}。\n"
        f"可能原因：{hint}。\n"
        f"建议操作：{hint}。"
    )
