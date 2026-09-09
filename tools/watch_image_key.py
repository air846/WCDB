"""后台轮询图片 AES 密钥（在微信中查看图片后自动捕获）。

微信只在解码图片时把图片 AES 密钥加载到 `Weixin.exe` 内存。本脚本循环扫描，
一旦你在微信里点开任意一张聊天图片（保持查看器打开几秒），即可捕获密钥并
写入文件，供 `wechat-export --image-key` 使用。

用法：
    python tools/watch_image_key.py [--minutes 30] [--interval 0] [--out image_key.txt]

拿到密钥后：
    wechat-export --out ./chat_export --resume --image-key <hex>
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wechat_export.cli import _image_samples  # noqa: E402
from wechat_export.image_key import extract_image_key  # noqa: E402
from wechat_export.locator import locate_data  # noqa: E402


def _account_root() -> Path | None:
    try:
        account = locate_data(None)[0]
    except Exception:  # noqa: BLE001
        return None
    return account.db_storage.parent if account.db_storage else None


def main() -> int:
    p = argparse.ArgumentParser(description="轮询捕获微信图片 AES 密钥")
    p.add_argument("--minutes", type=float, default=30.0, help="最长轮询时间（分钟）")
    p.add_argument("--interval", type=float, default=0.0,
                   help="每轮之间额外等待秒数（默认 0，扫描本身耗时约 20s）")
    p.add_argument("--out", default="image_key.txt", help="密钥输出文件")
    p.add_argument("--deep", action="store_true", help="每轮额外做深度扫描（很慢）")
    args = p.parse_args()

    root = _account_root()
    if root is None:
        print("未找到微信数据目录；请用 wechat-export --data-dir 指定后重试",
              file=sys.stderr)
        return 2
    samples = _image_samples(root)
    if not samples:
        print(f"{root} 下未找到 V2 .dat 样本", file=sys.stderr)
        return 2

    out = Path(args.out)
    deadline = time.time() + args.minutes * 60
    print(f"监听中（最多 {args.minutes:g} 分钟）… 请现在在微信里点开一张图片并保持查看器打开。")
    round_no = 0
    while time.time() < deadline:
        round_no += 1
        key = extract_image_key(samples, deep=args.deep)
        if key:
            out.write_text(key.hex(), encoding="ascii")
            print(f"\n已捕获图片密钥：{key.hex()}")
            print(f"已写入：{out.resolve()}")
            print("接着执行：wechat-export --out ./chat_export --resume "
                  f"--image-key {key.hex()}")
            return 0
        print(f"  第 {round_no} 轮未命中（密钥可能尚未加载），继续监听…", flush=True)
        if args.interval > 0:
            time.sleep(args.interval)
    print("监听超时，未捕获到密钥。请确认微信中确实打开了图片后重试。", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
