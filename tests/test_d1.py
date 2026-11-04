from __future__ import annotations

from collections.abc import Callable
import json
import sqlite3
import unittest

import httpx

from spam_learning.config import ConfigError, SpamLearningConfig
from spam_learning.d1 import (
    D1APIError,
    D1Client,
    D1HTTPError,
    D1NotFoundError,
    ReviewOutcome,
)
from spam_learning.d1_migrate import MIGRATION_PATH
from spam_learning.schemas import MessageSnapshot, SpamSample


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
        source="keyword",
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

    async def test_repeated_review_does_not_overwrite_label(self) -> None:
        sample_id = await self.client.create_pending(make_sample())

        first = await self.client.review(sample_id, "spam", 12345)
        second = await self.client.review(sample_id, "ham", 67890)

        self.assertEqual(first, ReviewOutcome.CHANGED)
        self.assertEqual(second, ReviewOutcome.ALREADY_REVIEWED)
        record = await self.client.get(sample_id)
        self.assertEqual(record.label, "spam")
        self.assertEqual(record.reviewed_by, 12345)

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
