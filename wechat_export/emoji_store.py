"""商店表情包容器解析（`PersistStore` / `ThumbStore` + `emoticon.db`）。

微信 4.x 的商店表情包把一包内所有表情**首尾相接**存进一个容器文件：

    business/emoticon/PersistStore/<md5(package_id)[:2]>/<md5(package_id)>
    business/emoticon/ThumbStore/<md5(package_id)[:2]>/<md5(package_id)>

容器整体是**一条连续 CBC 流**（不是每个表情各自加密），所以必须整包解密后再按
`emoticon.db` 的 `kStoreEmoticonFilesTable` 里 `emoticon_offset_`/`emoticon_size_`
（缩略图用 `thumb_offset_`/`thumb_size_`）切片；单独解密某一刀会因 IV 不是密钥而得错。

实测 4.1.13.63：切出的每个切片都是完整表情文件，且 `md5(切片) == md5_`。
"""

import hashlib
from pathlib import Path

from wechat_export import db_access
from wechat_export.image_decoder import decrypt_emoji

BLOCK = 16
# (容器目录名, 表中列名前缀)
_SOURCES = (("PersistStore", "emoticon"), ("ThumbStore", "thumb"))


class EmojiStore:
    """md5 → 表情明文。索引惰性构建、容器解密结果按包缓存，失败一律降级为 None。"""

    def __init__(self, account_root, db_storage, keys: dict,
                 emoji_key: bytes | None = None):
        self.account_root = Path(account_root)
        self.db_storage = Path(db_storage)
        self.keys = keys or {}
        self.emoji_key = emoji_key
        self._loaded = False
        self._indexes: dict[str, dict[str, tuple[str, int, int]]] = {
            dirname: {} for dirname, _ in _SOURCES}
        self._containers: dict[tuple[str, str], bytes | None] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        db_path = self.db_storage / "emoticon" / "emoticon.db"
        if not self.emoji_key or not db_path.exists():
            return
        try:
            key_hex = self.keys.get(db_path.read_bytes()[:16].hex())
        except OSError:
            return
        if not key_hex:
            return
        try:
            edb = db_access.open_encrypted(db_path, key_hex)
            rows = edb.query(
                "SELECT package_id_, md5_, emoticon_offset_, emoticon_size_,"
                " thumb_offset_, thumb_size_ FROM kStoreEmoticonFilesTable")
            edb.close()
        except Exception:  # noqa: BLE001  # 密钥不符/表结构变化：静默降级
            return
        for row in rows:
            md5 = str(row.get("md5_") or "").lower()
            package = str(row.get("package_id_") or "")
            if not md5 or not package:
                continue
            pkg_md5 = hashlib.md5(package.encode("utf-8")).hexdigest()
            for dirname, prefix in _SOURCES:
                self._indexes[dirname].setdefault(md5, (
                    pkg_md5,
                    int(row.get(f"{prefix}_offset_") or 0),
                    int(row.get(f"{prefix}_size_") or 0)))

    def available(self) -> bool:
        """`emoticon.db` 是否成功建立索引（决定是否值得为之重导）。"""
        self._load()
        return any(self._indexes[dirname] for dirname, _ in _SOURCES)

    def lookup(self, md5: str) -> bytes | None:
        """返回该表情的**明文**（已解密）；本地无此表情返回 None。"""
        self._load()
        key = (md5 or "").lower()
        for dirname, _prefix in _SOURCES:
            entry = self._indexes[dirname].get(key)
            if entry is None:
                continue
            pkg_md5, offset, size = entry
            plain = self._container(dirname, pkg_md5)
            if plain and size > 0 and offset >= 0 and offset + size <= len(plain):
                return plain[offset:offset + size]
        return None

    def _container(self, dirname: str, pkg_md5: str) -> bytes | None:
        cache_key = (dirname, pkg_md5)
        if cache_key in self._containers:
            return self._containers[cache_key]
        path = (self.account_root / "business" / "emoticon" / dirname
                / pkg_md5[:2] / pkg_md5)
        plain = None
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        if data and len(data) % BLOCK == 0:
            plain = decrypt_emoji(data, self.emoji_key)
        self._containers[cache_key] = plain
        return plain
