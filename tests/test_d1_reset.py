from __future__ import annotations

from contextlib import redirect_stderr
import io
import unittest

from spamdet.d1_reset import DROP_SAMPLES_SQL, drop_samples_table, main


class RecordingExecutor:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def apply_migration(self, sql: str) -> None:
        self.sql.append(sql)


class D1ResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_drop_uses_only_fixed_samples_table_sql(self) -> None:
        executor = RecordingExecutor()

        await drop_samples_table(executor)

        self.assertEqual(executor.sql, ["DROP TABLE IF EXISTS samples"])
        self.assertEqual(DROP_SAMPLES_SQL, executor.sql[0])


class D1ResetCLITests(unittest.TestCase):
    def test_confirmation_flag_is_required(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main([])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
