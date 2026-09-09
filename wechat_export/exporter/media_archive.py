"""媒体文件归档：md5 去重、按类型分目录、临时文件 + 原子改名。"""

import hashlib
import shutil
from pathlib import Path

from wechat_export.message_model import Media

KIND_DIRS = {"image": "image", "video": "video", "voice": "voice", "file": "file", "emoji": "emoji"}


def _md5_of_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def placeholder_media(media: Media) -> Media:
    media.status = "missing"
    return media


class MediaArchive:
    def __init__(self, media_root: Path):
        self.media_root = Path(media_root)
        self._saved: set[tuple[str, str]] = set()
        self._files: set[str] = set()
        self.stats = {"saved": 0, "duplicated": 0, "missing": 0, "pruned": 0}

    def _store(self, data: bytes, kind: str, ext: str, md5: str) -> Media | None:
        kind_dir = KIND_DIRS.get(kind)
        if not kind_dir:
            return None
        digest = md5 or _md5_of_bytes(data)
        rel_path = f"media/{kind_dir}/{digest}{ext}"
        self._files.add(f"{kind_dir}/{digest}{ext}")
        if (kind_dir, digest) in self._saved:
            self.stats["duplicated"] += 1
            return Media(kind=kind, md5=digest, size=len(data), ext=ext,
                         rel_path=rel_path)
        target_dir = self.media_root / kind_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp = target_dir / f".tmp_{digest}"
        try:
            tmp.write_bytes(data)
            target = target_dir / f"{digest}{ext}"
            if not target.exists():
                tmp.replace(target)
            else:
                tmp.unlink(missing_ok=True)
        except OSError:
            tmp.unlink(missing_ok=True)
            return None
        self._saved.add((kind_dir, digest))
        self.stats["saved"] += 1
        return Media(kind=kind, md5=digest, size=len(data), ext=ext,
                     rel_path=rel_path)

    def prune(self) -> int:
        """删除本轮未写入的旧媒体文件（例如上次未解码的 .dat/.wxgf）。"""
        removed = 0
        if not self.media_root.is_dir():
            return 0
        for kind_dir in KIND_DIRS.values():
            directory = self.media_root / kind_dir
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if not path.is_file():
                    continue
                if path.name.startswith(".tmp_") or f"{kind_dir}/{path.name}" not in self._files:
                    path.unlink(missing_ok=True)
                    removed += 1
        self.stats["pruned"] += removed
        return removed

    def save_bytes(self, data: bytes, kind: str, ext: str, md5: str = "") -> Media:
        media = self._store(data, kind, ext, md5)
        if media is None:
            self.stats["missing"] += 1
            return placeholder_media(Media(kind=kind, md5=md5, ext=ext))
        return media

    def save_file(self, src: Path, kind: str, ext: str, md5: str = "") -> Media:
        if not src.exists():
            self.stats["missing"] += 1
            return placeholder_media(Media(kind=kind, md5=md5, ext=ext))
        data = src.read_bytes()
        return self.save_bytes(data, kind, ext, md5)
