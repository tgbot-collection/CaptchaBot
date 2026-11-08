"""将旧样本中误标为 UTC 的 Asia/Shanghai 消息时间一次性修正为 UTC。"""

from __future__ import annotations

import argparse
import asyncio
from typing import Protocol, Sequence

from .config import SpamLearningConfig
from .d1 import D1Client


# 旧代码把 Kurigram 返回的 Asia/Shanghai 无时区时间直接追加 Z，导致比真实 UTC 快 8 小时。
# 时间差条件使语句可重复执行：已修正记录与 created_at 仅相差数秒，不会再次减 8 小时。
BACKFILL_SQL = """
UPDATE samples
SET message_json = json_set(
    message_json,
    '$.message.date',
    strftime('%Y-%m-%dT%H:%M:%SZ', json_extract(message_json, '$.message.date'), '-8 hours')
)
WHERE schema_version = 2
  AND json_type(message_json, '$.message.date') = 'text'
  AND julianday(json_extract(message_json, '$.message.date')) IS NOT NULL
  AND (julianday(json_extract(message_json, '$.message.date')) - julianday(created_at)) * 24
      BETWEEN 7.5 AND 8.5
""".strip()


class TrustedSQLExecutor(Protocol):
    async def apply_migration(self, sql: str) -> None: ...


async def backfill_message_dates(client: TrustedSQLExecutor) -> None:
    """执行代码内固定 SQL；不接受外部 SQL、时区或偏移参数。"""

    await client.apply_migration(BACKFILL_SQL)


async def backfill_from_env() -> None:
    config = SpamLearningConfig.from_env()
    async with D1Client.from_config(config) as client:
        await backfill_message_dates(client)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修正旧 D1 样本中误标为 UTC 的上海消息时间")
    parser.add_argument(
        "--confirm-asia-shanghai-history",
        action="store_true",
        help="确认目标库历史样本均由 TZ=Asia/Shanghai 的旧代码写入",
    )
    args = parser.parse_args(argv)
    if not args.confirm_asia_shanghai_history:
        parser.error("必须显式提供 --confirm-asia-shanghai-history")
    asyncio.run(backfill_from_env())
    print("D1 历史 message.date UTC 修正执行完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
