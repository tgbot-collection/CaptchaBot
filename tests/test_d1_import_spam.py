from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from spamdet.d1 import ManualSpamImportResult
from spamdet.d1_import_spam import main, read_spam_file


class D1ImportSpamCLITests(unittest.TestCase):
    def test_reads_one_multiline_utf8_sample_and_removes_only_terminal_newlines(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "message.txt"
            path.write_text("\ufeff第一行  \n第二行🚀\n\n", encoding="utf-8")

            self.assertEqual(read_spam_file(str(path)), "第一行  \n第二行🚀")

    def test_rejects_missing_non_txt_and_empty_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.txt"
            empty.write_text("\n\r\n", encoding="utf-8")
            wrong_suffix = root / "message.md"
            wrong_suffix.write_text("spam", encoding="utf-8")
            invalid_utf8 = root / "invalid.txt"
            invalid_utf8.write_bytes(b"\xff\xfe\xfa")
            for value in (str(root / "missing.txt"), str(empty), str(wrong_suffix), str(invalid_utf8)):
                with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                    read_spam_file(value)

    def test_cli_requires_human_confirmation(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "message.txt"
            path.write_text("人工确认 Spam", encoding="utf-8")
            with self.assertRaises(SystemExit) as context:
                main(["--file", str(path)])
        self.assertEqual(context.exception.code, 2)

    def test_cli_imports_and_reports_result(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "message.txt"
            path.write_text("第一行\n第二行", encoding="utf-8")
            operation = AsyncMock(return_value=ManualSpamImportResult(id=9, created=True))
            with patch("spamdet.d1_import_spam.import_spam_from_env", operation):
                self.assertEqual(main(["--file", str(path), "--confirm-spam"]), 0)
        operation.assert_awaited_once_with("第一行\n第二行")


if __name__ == "__main__":
    unittest.main()
