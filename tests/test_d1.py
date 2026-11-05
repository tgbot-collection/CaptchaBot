from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
import sqlite3
import unittest

import httpx

from spamdet.config import ConfigError, SpamLearningConfig
from spamdet.d1 import (
    D1APIError,
    D1Client,
    D1HTTPError,
    D1NotFoundError,
    ReviewOutcome,
)
from spamdet.d1_migrate import MIGRATION_PATH
from spamdet.schemas import MessageSnapshot, ModelSuggestion, SpamSample


class SQLiteD1Transport:
    """用 SQLite 执行 REST 请求中的 SQL，模拟 D1 Query API。"""

    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = json.loads(request.content)
        before = self.connection.total_changes
        cursor = self.connection.execute(payload["sql"], payload.get("params", []))
        rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
        self.connection.commit()
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "errors": [],
                "result": [
                    {
                        "success": True,
                        "results": rows,
                        "meta": {
                            "changes": self.connection.total_changes - before,
                            "last_row_id": cursor.lastrowid,
                        },
                    }
                ],
            },
        )


def make_sample(*, chat_id: int = -100123, message_id: int = 42) -> SpamSample:
    return SpamSample(
        source="rule",
        message=MessageSnapshot(
            chat_id=chat_id,
            message_id=message_id,
            date="2026-11-04T08:00:00Z",
            text="保留原始 Unicode：收米🚀",
            caption=None,
        ),
        matched_rule="收米",
    )


def make_client(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> D1Client:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return D1Client(
        account_id="account",
        database_id="database",
        api_token="secret-token",
        http_client=http_client,
        retry_backoff=0,
        **kwargs,
    )


class D1ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.transport = SQLiteD1Transport()
        self.client = make_client(self.transport)

    async def asyncTearDown(self) -> None:
        await self.client._http.aclose()
        self.transport.connection.close()

    async def test_group_sample_stays_unlabeled_until_admin_review(self) -> None:
        sample = replace(make_sample(), source="group", matched_rule=None)
        sample_id = await self.client.create_pending(sample)
        self.assertEqual(sample_id, await self.client.create_pending(sample))
        pending = await self.client.next_group_pending()
        self.assertIsNotNone(pending)
        self.assertEqual(pending.label, None)
        await self.client.review(sample_id, "ham", 123)
        self.assertEqual((await self.client.get(sample_id)).label, "ham")
        self.assertIsNone(await self.client.next_group_pending())

    async def test_create_pending_is_idempotent_and_hides_identifiers_in_key(self) -> None:
        sample = make_sample()
        first_id = await self.client.create_pending(sample)
        second_id = await self.client.create_pending(sample)

        self.assertEqual(first_id, second_id)
        record = await self.client.get(first_id)
        self.assertEqual(record.sample, sample.to_dict())
        self.assertEqual(record.text, sample.message.text)
        self.assertNotIn(str(sample.message.chat_id), record.dedupe_key)
        self.assertNotIn(str(sample.message.message_id), record.dedupe_key)
        count = self.transport.connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        self.assertEqual(count, 1)

    async def test_unavailable_prediction_persists_null_version_and_score(self) -> None:
        sample = replace(
            make_sample(),
            model=ModelSuggestion(
                version=None,
                kind="similarity",
                decision="unavailable",
                score=None,
            ),
        )

        sample_id = await self.client.create_pending(sample)
        record = await self.client.get(sample_id)

        self.assertEqual(record.prediction, "unavailable")
        self.assertIsNone(record.model_version)
        self.assertIsNone(record.prediction_score)
        self.assertEqual(record.sample["model"]["version"], None)

    async def test_repeated_review_does_not_overwrite_label(self) -> None:
        sample_id = await self.client.create_pending(make_sample())

        first = await self.client.review(sample_id, "spam", 12345)
        second = await self.client.review(sample_id, "ham", 67890)

        self.assertEqual(first, ReviewOutcome.CHANGED)
        self.assertEqual(second, ReviewOutcome.ALREADY_REVIEWED)
        record = await self.client.get(sample_id)
        self.assertEqual(record.label, "spam")
        self.assertEqual(record.reviewed_by, 12345)

    async def test_review_message_reference_is_stored(self) -> None:
        sample_id = await self.client.create_pending(make_sample())

        await self.client.set_review_message(sample_id, 12345, 678)

        record = await self.client.get(sample_id)
        self.assertEqual((record.review_chat_id, record.review_message_id), (12345, 678))

    async def test_review_missing_sample_is_explicit(self) -> None:
        with self.assertRaises(D1NotFoundError):
            await self.client.review(999, "spam", 12345)

    async def test_stats_and_stable_paginated_export(self) -> None:
        ids = []
        for message_id, label in ((3, "spam"), (1, "ham"), (2, "spam")):
            sample_id = await self.client.create_pending(make_sample(message_id=message_id))
            ids.append(sample_id)
            await self.client.review(sample_id, label, 12345)
        await self.client.create_pending(make_sample(message_id=4))

        stats = await self.client.stats()
        exported = await self.client.export_reviewed(page_size=2)

        self.assertEqual((stats.total, stats.pending, stats.reviewed, stats.spam, stats.ham), (4, 1, 3, 2, 1))
        self.assertEqual([record.id for record in exported], sorted(ids))

    async def test_all_sql_values_are_sent_as_params(self) -> None:
        sample = make_sample()
        await self.client.create_pending(sample)
        payload = json.loads(self.transport.requests[0].content)

        self.assertIn("VALUES (?, ?, ?, ?, ?", payload["sql"])
        self.assertNotIn(sample.message.text, payload["sql"])
        self.assertIn(sample.message.text, payload["params"])
        self.assertTrue(all(isinstance(param, str) for param in payload["params"]))
        self.assertEqual(self.transport.requests[0].headers["Authorization"], "Bearer secret-token")


class D1ResponseHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_http_error(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "result": [{"success": True, "results": []}]},
            )

        client = make_client(handler, max_retries=1)
        try:
            self.assertEqual(await client.export_reviewed(), [])
            self.assertEqual(calls, 2)
        finally:
            await client._http.aclose()

    async def test_http_200_top_level_business_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                json={"success": False, "errors": [{"code": 7500, "message": "query failed"}]},
            )

        client = make_client(handler)
        try:
            with self.assertRaisesRegex(D1APIError, "7500"):
                await client.stats()
        finally:
            await client._http.aclose()

    async def test_http_200_statement_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "result": [{"success": False, "error": "bad SQL"}]},
            )

        client = make_client(handler)
        try:
            with self.assertRaises(D1APIError):
                await client.stats()
        finally:
            await client._http.aclose()

    async def test_network_error_is_bounded_and_does_not_leak_token(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectTimeout("secret-token", request=request)

        client = make_client(handler, max_retries=1)
        try:
            with self.assertRaises(D1HTTPError) as caught:
                await client.stats()
            self.assertEqual(calls, 2)
            self.assertNotIn("secret-token", str(caught.exception))
        finally:
            await client._http.aclose()


class D1ConfigTests(unittest.TestCase):
    def test_single_admin_config_is_required_when_enabled(self) -> None:
        with self.assertRaisesRegex(ConfigError, "SPAM_ADMIN_ID"):
            SpamLearningConfig.from_env({"SPAM_LEARNING_ENABLED": "true"})

        config = SpamLearningConfig.from_env(
            {
                "SPAM_LEARNING_ENABLED": "true",
                "SPAM_ADMIN_ID": "12345",
                "D1_ACCOUNT_ID": "account",
                "D1_DATABASE_ID": "database",
                "D1_API_TOKEN": "token",
            }
        )
        self.assertEqual(config.admin_id, 12345)

    def test_single_admin_config_rejects_lists(self) -> None:
        with self.assertRaisesRegex(ConfigError, "SPAM_ADMIN_ID"):
            SpamLearningConfig.from_env({"SPAM_ADMIN_ID": "123,456"})

    def test_capture_config_defaults_and_overrides(self) -> None:
        defaults = SpamLearningConfig.from_env({})
        self.assertEqual(
            (
                defaults.capture_queue_size,
                defaults.capture_workers,
                defaults.bio_timeout_seconds,
                defaults.bio_cache_ttl_seconds,
                defaults.bio_cache_size,
                defaults.capture_shutdown_timeout_seconds,
            ),
            (100, 2, 2.0, 300.0, 1000, 5.0),
        )
        configured = SpamLearningConfig.from_env(
            {
                "SPAM_CAPTURE_QUEUE_SIZE": "7",
                "SPAM_CAPTURE_WORKERS": "3",
                "SPAM_INFERENCE_CONCURRENCY": "4",
                "SPAM_BIO_TIMEOUT_SECONDS": "1.5",
                "SPAM_BIO_CACHE_TTL_SECONDS": "60",
                "SPAM_BIO_CACHE_SIZE": "25",
                "SPAM_CAPTURE_SHUTDOWN_TIMEOUT_SECONDS": "4",
            }
        )
        self.assertEqual(configured.capture_queue_size, 7)
        self.assertEqual(configured.capture_workers, 3)
        self.assertEqual(configured.inference_concurrency, 4)
        self.assertEqual(configured.bio_timeout_seconds, 1.5)
        self.assertEqual(configured.bio_cache_ttl_seconds, 60.0)
        self.assertEqual(configured.bio_cache_size, 25)
        self.assertEqual(configured.capture_shutdown_timeout_seconds, 4.0)

    def test_capture_config_rejects_non_positive_values(self) -> None:
        with self.assertRaisesRegex(ConfigError, "SPAM_CAPTURE_QUEUE_SIZE"):
            SpamLearningConfig.from_env({"SPAM_CAPTURE_QUEUE_SIZE": "0"})
        with self.assertRaisesRegex(ConfigError, "SPAM_INFERENCE_CONCURRENCY"):
            SpamLearningConfig.from_env({"SPAM_INFERENCE_CONCURRENCY": "0"})

    def test_d1_config_can_be_required_while_feature_is_disabled(self) -> None:
        config = SpamLearningConfig.from_env(
            {
                "D1_ACCOUNT_ID": "account",
                "D1_DATABASE_ID": "database",
                "D1_API_TOKEN": "token",
            }
        )
        self.assertFalse(config.enabled)
        self.assertEqual(config.require_d1(), ("account", "database", "token"))

    def test_missing_d1_config_is_explicit(self) -> None:
        config = SpamLearningConfig.from_env({})
        with self.assertRaisesRegex(ConfigError, "D1_ACCOUNT_ID, D1_DATABASE_ID, D1_API_TOKEN"):
            config.require_d1()


if __name__ == "__main__":
    unittest.main()
