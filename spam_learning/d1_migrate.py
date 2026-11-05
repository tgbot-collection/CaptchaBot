"""D1 Migration 的本地校验和远程执行入口。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sqlite3

from .config import SpamLearningConfig
from .d1 import D1Client


MIGRATION_PATH = Path(__file__).resolve().parent.parent / "migrations" / "001_create_samples.sql"
GROUP_MIGRATION_PATH = MIGRATION_PATH.parent / "002_group_samples.sql"


def validate_migration() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(sql)
        connection.executescript(GROUP_MIGRATION_PATH.read_text(encoding="utf-8"))


async def apply_migration() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    config = SpamLearningConfig.from_env()
    async with D1Client.from_config(config) as client:
        await client.apply_migration(sql)
        await client.apply_migration(GROUP_MIGRATION_PATH.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="校验或执行 Story 002 D1 Migration")
    parser.add_argument("--apply", action="store_true", help="使用环境变量中的 D1 配置执行远程迁移")
    args = parser.parse_args()
    if args.apply:
        asyncio.run(apply_migration())
        print("D1 Migration 执行完成")
    else:
        validate_migration()
        print("D1 Migration 本地校验通过")


if __name__ == "__main__":
    main()
