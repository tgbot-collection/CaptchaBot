from __future__ import annotations

import sqlite3
import unittest

from spamdet.d1_migrate import MIGRATION_PATH, validate_migration


class D1MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.connection.close()

    def test_migration_can_be_validated_without_credentials(self) -> None:
        validate_migration()

    def test_migration_is_idempotent(self) -> None:
        self.connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))

    def test_status_and_label_constraints(self) -> None:
        common = (2, "rule", "{}", "text", "dedupe")
        self.connection.execute(
            """
            INSERT INTO samples (schema_version, source, message_json, text, status, label, dedupe_key)
            VALUES (?, ?, ?, ?, 'pending', NULL, ?)
            """,
            common,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO samples (schema_version, source, message_json, text, status, label, dedupe_key)
                VALUES (2, 'rule', '{}', 'text', 'pending', 'spam', 'bad-pending')
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO samples (schema_version, source, message_json, text, status, label, dedupe_key)
                VALUES (2, 'rule', '{}', 'text', 'reviewed', NULL, 'bad-reviewed')
                """
            )

    def test_dedupe_key_is_unique(self) -> None:
        sql = """
            INSERT INTO samples (schema_version, source, message_json, text, status, label, dedupe_key)
            VALUES (2, 'rule', '{}', 'text', 'pending', NULL, 'same-key')
        """
        self.connection.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(sql)

    def test_legacy_keyword_source_is_rejected(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO samples (schema_version, source, message_json, text, status, label, dedupe_key)
                VALUES (2, 'keyword', '{}', 'text', 'pending', NULL, 'legacy-source')
                """
            )

    def test_final_schema_supports_group_and_is_repeatable(self) -> None:
        self.connection.execute(
            "INSERT INTO samples (id,schema_version,source,message_json,text,status,label,reviewed_by,dedupe_key) "
            "VALUES (9,2,'rule','{}','旧消息','reviewed','ham',123,'old-key')"
        )
        self.connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        self.connection.execute(
            "INSERT INTO samples (schema_version,source,message_json,text,status,dedupe_key) "
            "VALUES (2,'group','{}','普通消息','pending','new-key')"
        )
        self.assertEqual(
            self.connection.execute("SELECT label,reviewed_by FROM samples WHERE id=9").fetchone(),
            ("ham", 123),
        )


if __name__ == "__main__":
    unittest.main()
