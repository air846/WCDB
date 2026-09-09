from wechat_export.exporter.media_archive import KIND_DIRS, MediaArchive


def test_save_bytes_dedup(tmp_path):
    arc = MediaArchive(tmp_path / "media")
    m1 = arc.save_bytes(b"aaa", "image", ".jpg", md5="m1")
    m2 = arc.save_bytes(b"aaa", "image", ".jpg", md5="m1")
    assert m1.status == "ok"
    assert m1.rel_path.startswith("media/image/")
    assert (tmp_path / m1.rel_path).exists()
    assert m2.rel_path == m1.rel_path
    s = arc.stats
    assert s["saved"] == 1 and s["duplicated"] == 1 and s["missing"] == 0


def test_save_bytes_different_md5(tmp_path):
    arc = MediaArchive(tmp_path / "media")
    m1 = arc.save_bytes(b"aaa", "image", ".jpg", md5="m1")
    m2 = arc.save_bytes(b"bbb", "image", ".jpg", md5="m2")
    assert m1.rel_path != m2.rel_path
    assert arc.stats["saved"] == 2


def test_kind_dirs():
    assert KIND_DIRS == {"image": "image", "video": "video", "voice": "voice",
                        "file": "file", "emoji": "emoji"}


def test_missing_source(tmp_path):
    arc = MediaArchive(tmp_path / "media")
    m = arc.save_file(tmp_path / "nope.jpg", "image", ".jpg", md5="x")
    assert m.status == "missing"
    assert arc.stats["missing"] == 1


def test_prune_removes_stale(tmp_path):
    arc = MediaArchive(tmp_path / "media")
    stale = tmp_path / "media" / "image" / "old.dat"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    (tmp_path / "media" / "image" / ".tmp_leftover").write_bytes(b"x")
    m = arc.save_bytes(b"aaa", "image", ".jpg", md5="m1")
    removed = arc.prune()
    assert removed == 2
    assert not stale.exists()
    assert not (tmp_path / "media" / "image" / ".tmp_leftover").exists()
    assert (tmp_path / m.rel_path).exists()
    assert arc.stats["pruned"] == 2
