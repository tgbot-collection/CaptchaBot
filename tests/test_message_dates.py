from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import time
import unittest
from unittest.mock import patch

from spamdet.d1_message_date_backfill import BACKFILL_SQL, main
from spamdet.schemas import _utc_iso


class MessageDateSerializationTests(unittest.TestCase):
    def test_naive_kurigram_datetime_uses_process_timezone_before_utc_conversion(self) -> None:
        if not hasattr(time, "tzset"):
            self.skipTest("当前平台不支持 time.tzset")
        old_timezone = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Asia/Shanghai"
            time.tzset()
            self.assertEqual(
                _utc_iso(datetime(2026, 11, 8, 21, 37, 53)),
                "2026-11-08T13:37:53Z",
            )
        finally:
            if old_timezone is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_timezone
            time.tzset()

    def test_aware_datetime_is_converted_to_utc(self) -> None:
        china = timezone(timedelta(hours=8))
        self.assertEqual(
            _utc_iso(datetime(2026, 11, 8, 21, 37, 53, tzinfo=china)),
            "2026-11-08T13:37:53Z",
        )


class MessageDateBackfillTests(unittest.TestCase):
    def test_backfill_only_changes_rows_with_eight_hour_offset_and_is_idempotent(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE samples (id INTEGER PRIMARY KEY, schema_version INTEGER, "
            "message_json TEXT, created_at TEXT)"
        )
        wrong = {"message": {"date": "2026-11-08T21:37:53Z"}}
        correct = {"message": {"date": "2026-11-08T13:44:07Z"}}
        connection.executemany(
            "INSERT INTO samples VALUES (?, 2, ?, ?)",
            (
                (1, json.dumps(wrong), "2026-11-08 13:37:55"),
                (2, json.dumps(correct), "2026-11-08 13:44:08"),
                (3, json.dumps({"message": {"date": None}}), "2026-11-08 13:50:00"),
            ),
        )

        connection.execute(BACKFILL_SQL)
        first = [json.loads(row[0])["message"]["date"] for row in connection.execute(
            "SELECT message_json FROM samples ORDER BY id"
        )]
        connection.execute(BACKFILL_SQL)
        second = [json.loads(row[0])["message"]["date"] for row in connection.execute(
            "SELECT message_json FROM samples ORDER BY id"
        )]

        self.assertEqual(first, ["2026-11-08T13:37:53Z", "2026-11-08T13:44:07Z", None])
        self.assertEqual(second, first)

    def test_cli_requires_explicit_confirmation(self) -> None:
        with self.assertRaises(SystemExit) as context:
            main([])
        self.assertEqual(context.exception.code, 2)

    def test_cli_runs_fixed_backfill_after_confirmation(self) -> None:
        with patch("spamdet.d1_message_date_backfill.backfill_from_env") as operation:
            self.assertEqual(main(["--confirm-asia-shanghai-history"]), 0)
        operation.assert_awaited_once_with()
