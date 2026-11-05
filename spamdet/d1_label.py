"""通过明确的人工标签批量更新 D1 样本。"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Sequence

from .config import SpamLearningConfig
from .d1 import D1Client


def parse_sample_ids(value: str) -> list[int]:
    """解析 ``1,2,3``，拒绝空项、零、负数及其他字符。"""

    parts = value.split(",")
    if not parts or any(not re.fullmatch(r"[1-9]\d*", part.strip()) for part in parts):
        raise argparse.ArgumentTypeError("id 必须是逗号分隔的正整数，例如 1,2,3")
    return list(dict.fromkeys(int(part.strip()) for part in parts))


async def label_samples_from_env(label: str, sample_ids: Sequence[int]) -> list[int]:
    config = SpamLearningConfig.from_env()
    admin_id = config.require_admin_id()
    async with D1Client.from_config(config) as client:
        return await client.label_samples(sample_ids, label, admin_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量人工标注 D1 samples 记录")
    parser.add_argument("--label", required=True, choices=("spam", "ham"), help="人工确认标签")
    parser.add_argument("--ids", required=True, type=parse_sample_ids, help="逗号分隔的样本 ID")
    args = parser.parse_args(argv)

    changed_ids = asyncio.run(label_samples_from_env(args.label, args.ids))
    print(f"已将 {len(changed_ids)} 条样本标注为 {args.label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
