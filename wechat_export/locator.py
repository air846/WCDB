"""定位微信数据目录与账号。

4.x 数据根目录名存在多个候选（社区在 4.0 中发现 xwechat_files 等命名），
因此枚举候选名；`--data-dir` 覆盖放第一优先级。

M0 实测（微信 4.1.13.63）：用户可自定义数据根父目录，4.x 将其明文写入
`%APPDATA%\\Tencent\\xwechat\\config\\*.ini`（文件内容即目录路径，如 `D:\\Document`），
实际数据根为 `<该目录>\\xwechat_files`。因此优先读取该配置，再回退到文档目录/盘符扫描。
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from wechat_export.exceptions import WeChatNotFoundError

DATA_DIR_NAMES = ["WeChat Files", "xwechat_files", "WeChat Files (x64)"]
DB_STORAGE_NAMES = ["db_storage"]

WECHAT_CONFIG_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Tencent" / "xwechat" / "config",
    Path(os.environ.get("APPDATA", "")) / "Tencent" / "WeChat" / "config",
]
_ABS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass
class AccountInfo:
    wxid: str
    root: Path
    db_storage: Path | None = None
    db_files: list[Path] = field(default_factory=list)


def user_documents_dir() -> Path:
    home = Path.home()
    for cand in (home / "Documents", home / "文档"):
        if cand.is_dir():
            return cand
    return home


def _drive_roots() -> list[Path]:
    roots = []
    for letter in "CDEFG":
        p = Path(f"{letter}:\\")
        if p.exists():
            roots.append(p)
    return roots


def _config_data_bases() -> list[Path]:
    """从 4.x 配置目录的 *.ini 读取自定义数据根父目录（文件内容即路径）。"""
    bases: list[Path] = []
    for cfg_dir in WECHAT_CONFIG_DIRS:
        if not cfg_dir.is_dir():
            continue
        for ini in sorted(cfg_dir.glob("*.ini")):
            try:
                text = ini.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                continue
            text = text.strip('"').strip("'")
            if _ABS_PATH_RE.match(text):
                p = Path(text)
                if p not in bases:
                    bases.append(p)
    return bases


def candidate_data_roots(override: Path | None) -> list[Path]:
    cands: list[Path] = []
    if override:
        cands.append(Path(override))
    base_dirs = [
        *_config_data_bases(),
        user_documents_dir(),
        *[d for d in _drive_roots() if d != user_documents_dir().anchor],
    ]
    seen: set[Path] = set()
    for base in base_dirs:
        if base in seen:
            continue
        seen.add(base)
        for name in DATA_DIR_NAMES:
            cands.append(base / name)
    return cands


def scan_accounts(data_root: Path) -> list[str]:
    if not data_root.is_dir():
        return []
    return sorted(
        p.name for p in data_root.iterdir()
        if p.is_dir() and p.name.startswith("wxid_")
    )


def _find_db_storage(account_dir: Path) -> Path | None:
    for name in DB_STORAGE_NAMES:
        cand = account_dir / name
        if cand.is_dir():
            return cand
    # 兜底：递归一层寻找 db_storage
    for sub in account_dir.iterdir():
        if sub.is_dir():
            for name in DB_STORAGE_NAMES:
                cand = sub / name
                if cand.is_dir():
                    return cand
    return None


def message_db_files(db_storage: Path) -> list[Path]:
    message_dir = db_storage / "message"
    if message_dir.is_dir():
        files = sorted(message_dir.glob("message_*.db"))
        return [f for f in files if f.suffix == ".db"]
    return sorted(db_storage.glob("message_*.db"))


def locate_data(override: Path | None) -> list[AccountInfo]:
    infos: list[AccountInfo] = []
    seen: set[Path] = set()
    for root in candidate_data_roots(override):
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        for wxid in scan_accounts(root):
            acc_dir = root / wxid
            db_storage = _find_db_storage(acc_dir)
            infos.append(AccountInfo(
                wxid=wxid, root=root, db_storage=db_storage,
                db_files=message_db_files(db_storage) if db_storage else [],
            ))
    if not infos:
        raise WeChatNotFoundError(
            "未找到微信数据目录",
            hint="请确认已安装并登录微信 4.x，或用 --data-dir 手动指定数据根目录",
        )
    return infos
