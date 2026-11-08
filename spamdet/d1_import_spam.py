"""从一个 UTF-8 文本文件补录一条人工确认的 D1 Spam 样本。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Sequence

from .config import SpamLearningConfig
from .d1 import D1Client, ManualSpamImportResult


def read_spam_file(value: str) -> str:
    """读取一条多行 Spam；保留内部内容，只移除文件末尾换行。"""

    path = Path(value)
    if path.suffix.lower() != ".txt":
        raise argparse.ArgumentTypeError("补录文件必须使用 .txt 扩展名")
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"补录文件不存在或不是普通文件：{path}")
    try:
        text = path.read_text(encoding="utf-8-sig").rstrip("\r\n")
    except (OSError, UnicodeError) as exc:
        raise argparse.ArgumentTypeError("补录文件必须是可读取的 UTF-8 文本") from exc
    if not text.strip():
        raise argparse.ArgumentTypeError("补录文件不能为空")
    return text


async def import_spam_from_env(text: str) -> ManualSpamImportResult:
    config = SpamLearningConfig.from_env()
    admin_id = config.require_admin_id()
    async with D1Client.from_config(config) as client:
        return await client.import_manual_spam(text, admin_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从一个 UTF-8 .txt 文件补录人工确认的 D1 Spam")
    parser.add_argument("--file", required=True, type=read_spam_file, dest="text", help="一条完整 Spam 文本")
    parser.add_argument(
        "--confirm-spam",
        action="store_true",
        help="确认文件内容已经过人工审核，标签为 Spam",
    )
    args = parser.parse_args(argv)
    if not args.confirm_spam:
        parser.error("必须显式提供 --confirm-spam")

    result = asyncio.run(import_spam_from_env(args.text))
    if result.created:
        print(f"已补录 D1 Spam 样本 id={result.id}")
    else:
        print(f"相同 reviewed/spam 样本已存在 id={result.id}，未重复写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
