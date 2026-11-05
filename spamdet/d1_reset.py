"""开发期安全删除 D1 ``samples`` 表的显式入口。"""

from __future__ import annotations

import argparse
import asyncio
from typing import Protocol, Sequence

from .config import SpamLearningConfig
from .d1 import D1Client


DROP_SAMPLES_SQL = "DROP TABLE IF EXISTS samples"


class TrustedSQLExecutor(Protocol):
    async def apply_migration(self, sql: str) -> None: ...


async def drop_samples_table(client: TrustedSQLExecutor) -> None:
    """只执行代码内固定的删表 SQL，不接受外部 SQL。"""

    await client.apply_migration(DROP_SAMPLES_SQL)


async def reset_samples_table() -> None:
    config = SpamLearningConfig.from_env()
    async with D1Client.from_config(config) as client:
        await drop_samples_table(client)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="删除目标 D1 的 samples 表（不会重建）")
    parser.add_argument(
        "--confirm-reset",
        action="store_true",
        help="确认永久删除 samples 表及其中全部数据",
    )
    args = parser.parse_args(argv)
    if not args.confirm_reset:
        parser.error("必须显式提供 --confirm-reset")
    asyncio.run(reset_samples_table())
    print("D1 samples 表已删除；请立即执行 Migration 重建")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
