"""探测：从运行中的 Weixin.exe 内存中寻找微信表情（emoji）本地文件的 AES 密钥。

**[已过时 / 历史工具]** 真实方案已于 2026-09-10 解出并在真机验证：密钥不是内存里的
裸 16 字节，而是由账号级 seed 派生 —— `key = md5(f"{seed}{wxid}EMOTICON")[:16]`，
密文是 **AES-128-CBC 且 IV = 密钥本身**。本脚本的 oracle 只做 AES-ECB（或把 IV 当成
文件首块），因此永远 0 命中。现行实现见 `wechat_export/emoji_key.py`，算法说明见
`docs/findings/emoji-format-notes.md`。保留本文件仅供追溯历史负结果。

背景见 `docs/findings/emoji-format-notes.md`：
- `business/emoticon/Persist/<md5>`、`Thumb/<md5>.thumb` 等文件均为分组加密
  （size % 16 == 0），浏览器无法直接显示；
- 二进制字符串表明表情明文可能是 `wxam` 容器（Skia 编解码 magic），但
  `kNonStoreEmoticonTable.aes_key` 并非直接可用（已排除多种 AES 模式）。

用法：
    python tools/probe_emoji_key.py --dir <xwechat_files>/<wxid>/business/emoticon [--oracle c0] [--stride 8]

oracle 取 Thumb 组首块（c0..c5）；扫描 Weixin.exe 可读内存中 16 字节窗口，
用 AES-ECB 解 oracle，命中 `wxam`/图片 magic 即候选，再完整校验。
"""

import argparse
import collections
import os
import sys
import time
from multiprocessing import Pool

from Crypto.Cipher import AES
from Crypto.Util import Padding

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wechat_export.image_key import find_weixin_pids  # noqa: E402
from wechat_export.key_provider import RemoteProcess  # noqa: E402

PNG_BLOCK = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
OFFSETS = {"c0": 0, "c1": 16, "c2": 32, "c3": 48, "c4": 64, "c5": 80}


def strict_valid(pt: bytes) -> str | None:
    """块内 magic 校验；wxam 4 字节 + 图片精确头，误报率极低。"""
    if pt[:4] == b"wxam":
        return "wxam"
    if pt[:10] == b"\xff\xd8\xff\xe0\x00\x10JFIF":
        return "jfif"
    if pt[:6] == b"\xff\xd8\xff\xe1" and pt[10:16] == b"Exif\x00\x00":
        return "exif"
    if pt == PNG_BLOCK:
        return "png"
    if pt[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if pt[:4] == b"wxgf":
        return "wxgf"
    return None


def _worker(args):
    pid, base, size, oracles, stride, key_size = args
    found = []
    try:
        with RemoteProcess(pid) as proc:
            offset = 0
            while offset < size:
                data = proc.read(base + offset, min(4 << 20, size - offset))
                if not data:
                    offset += 0x1000
                    continue
                aes = AES.new
                for i in range(0, len(data) - key_size + 1, stride):
                    key = data[i:i + key_size]
                    for name, oracle in oracles:
                        try:
                            if name == "xiv":
                                c0, c1 = oracle
                                d1 = aes(key, AES.MODE_ECB).decrypt(c1)
                                pt = bytes(a ^ b for a, b in zip(d1, c0))
                            elif name == "pair":
                                c0a, c0b = oracle
                                da = aes(key, AES.MODE_ECB).decrypt(c0a)
                                db = aes(key, AES.MODE_ECB).decrypt(c0b)
                                xor = bytes(a ^ b for a, b in zip(da, db))
                                if xor[:10] == b"\x00" * 10:
                                    found.append((key.hex(), name, "same-prefix", xor.hex()))
                                continue
                            else:
                                pt = aes(key, AES.MODE_ECB).decrypt(oracle)
                        except Exception:  # noqa: BLE001
                            continue
                        tag = strict_valid(pt)
                        if tag:
                            found.append((key.hex(), name, tag, pt.hex()))
                offset += len(data)
    except Exception:  # noqa: BLE001
        return found
    return found


def full_check(key_hex: str, emoticon_dir: str, key_size: int = 16) -> tuple | None:
    """完整文件校验：PKCS7 unpad 后头/尾均为合法图片，或 `wxam` 容器。"""
    key = bytes.fromhex(key_hex)
    for root, _, fs in os.walk(f"{emoticon_dir}/Thumb"):
        for f in fs:
            data = open(os.path.join(root, f), "rb").read()
            dec = AES.new(key, AES.MODE_ECB).decrypt(data)
            bodies = [dec]
            try:
                bodies.insert(0, Padding.unpad(dec, 16))
            except ValueError:
                pass
            for body in bodies:
                if body[:4] == b"wxam":
                    return "wxam", f, len(body)
                if body[:3] == b"\xff\xd8\xff" and body[-2:] == b"\xff\xd9":
                    return "jpeg", f, len(body)
                if body[:6] in (b"GIF87a", b"GIF89a") and body[-1] == 0x3B:
                    return "gif", f, len(body)
                if body[:8] == b"\x89PNG\r\n\x1a\n" and b"IEND" in body[-16:]:
                    return "png", f, len(body)
    return None


def build_oracles(emoticon_dir: str, which: str, source: str = "thumb") -> list[tuple[str, bytes]]:
    if source == "persist":
        files = []
        for root, _, fs in os.walk(f"{emoticon_dir}/Persist"):
            for f in fs:
                p = os.path.join(root, f)
                if os.path.getsize(p) >= 128:
                    files.append(p)
        files.sort(key=os.path.getsize)
        if not files:
            raise SystemExit(f"Persist 目录为空：{emoticon_dir}/Persist")
        with open(files[0], "rb") as fp:
            head = fp.read(128)
        names = ["c0", "c1", "c2"] if which == "all" else [which]
        return [(f"persist-{n}", head[OFFSETS[n]:OFFSETS[n] + 16]) for n in names]

    pref = collections.Counter()
    for root, _, fs in os.walk(f"{emoticon_dir}/Thumb"):
        for f in fs:
            with open(os.path.join(root, f), "rb") as fp:
                pref[fp.read(128)] += 1
    if not pref:
        raise SystemExit(f"Thumb 目录为空：{emoticon_dir}/Thumb")
    head = pref.most_common(1)[0][0]
    if which == "xiv":
        return [("xiv", (head[0:16], head[16:32]))]
    if which == "pair":
        heads = [h for h, _ in pref.most_common(2)]
        if len(heads) < 2:
            raise SystemExit("Thumb 首块分组不足 2 个，无法做 CBC 固定 IV 探测")
        return [("pair", (heads[0][0:16], heads[1][0:16]))]
    names = ["c0", "c1", "c2", "c3", "c4", "c5"] if which == "all" else [which]
    oracles = []
    for name in names:
        off = OFFSETS[name]
        if len(head) >= off + 16:
            oracles.append((name, head[off:off + 16]))
    return oracles


def main() -> None:
    p = argparse.ArgumentParser(description="微信表情本地文件 AES 密钥内存探测")
    p.add_argument("--dir", required=True, help="business/emoticon 目录")
    p.add_argument("--oracle", default="c0", choices=[*OFFSETS, "all", "xiv", "pair"],
                   help="Thumb oracle（c0..c5；xiv / pair 探测 CBC 固定 IV）")
    p.add_argument("--source", default="thumb", choices=["thumb", "persist"],
                   help="oracle 来源（默认 thumb）")
    p.add_argument("--key-size", type=int, default=16, choices=[16, 24, 32],
                   help="AES 密钥长度（默认 16）")
    p.add_argument("--stride", type=int, default=8, help="内存扫描步长（默认 8）")
    args = p.parse_args()

    oracles = build_oracles(args.dir, args.oracle, args.source)
    print("oracles:", [n for n, _ in oracles], "stride:", args.stride,
          "key-size:", args.key_size, flush=True)
    pids = find_weixin_pids()
    print("weixin pids:", pids, flush=True)
    if not pids:
        raise SystemExit("未发现运行中的 Weixin.exe；请先启动微信并打开表情面板后重试")

    tasks = []
    for pid in pids:
        try:
            with RemoteProcess(pid) as proc:
                for base, size in proc.regions():
                    if size > 0:
                        tasks.append((pid, base, size, oracles, args.stride, args.key_size))
        except Exception as e:  # noqa: BLE001
            print("pid", pid, "regions ERR", e)
    print("regions:", len(tasks), flush=True)

    t0 = time.time()
    done = 0
    candidates = set()
    with Pool(8) as pool:
        for res in pool.imap_unordered(_worker, tasks):
            done += 1
            if done % 200 == 0:
                print(f"progress {done}/{len(tasks)} {time.time()-t0:.0f}s "
                      f"candidates={len(candidates)}", flush=True)
            for item in res or []:
                if item in candidates:
                    continue
                candidates.add(item)
                print("CANDIDATE", item, flush=True)
                fc = full_check(item[0], args.dir, args.key_size)
                if fc:
                    print("VALIDATED", item, fc, flush=True)
                    pool.terminate()
                    return
    print(f"done {time.time()-t0:.0f}s; candidates={len(candidates)}", flush=True)


if __name__ == "__main__":
    main()
