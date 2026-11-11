from __future__ import annotations

import unittest
from unittest.mock import patch

from spamdet.dedupe import RedisOccurrenceStore
from spamdet.schemas import MessageSnapshot, SpamSample, UserSnapshot


def make_sample(*, user_id: int | None = 123, text: str = "同一条 Spam") -> SpamSample:
    return SpamSample(
        source="rule",
        message=MessageSnapshot(
            chat_id=-1001,
            message_id=1,
            date=None,
            text=text,
            caption=None,
        ),
        user=UserSnapshot(id=user_id),
        matched_rule="blacklist_message:spam",
    )


class FakeRedis:
    """只实现本模块 Lua 脚本所需的 Redis 语义。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def eval(self, script, key_count, key, *args):
        data = self.hashes.setdefault(key, {})
        if "HINCRBY" in script:
            now, owner, lease, _ttl = args
            count = int(data.get("count", "0")) + 1
            data["count"] = str(count)
            state = data.get("state")
            leader = state in {None, "retryable"} or (
                state == "processing" and int(data.get("lease_until", "0")) <= int(now)
            )
            if leader:
                data.update(
                    state="processing",
                    owner=str(owner),
                    lease_until=str(int(now) + int(lease)),
                )
            data.setdefault("first_seen", str(now))
            data["last_seen"] = str(now)
            return [int(leader), count]
        if "sample_id" in script:
            owner, sample_id, _ttl = args
            if data.get("owner") != owner:
                return 0
            data.update(state="saved", sample_id=str(sample_id))
            data.pop("owner", None)
            data.pop("lease_until", None)
            return 1
        owner, _ttl = args
        if data.get("owner") != owner:
            return 0
        data["state"] = "retryable"
        data.pop("owner", None)
        data.pop("lease_until", None)
        return 1

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)


class RedisOccurrenceStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_and_exact_content_are_merged_and_counted(self) -> None:
        redis = FakeRedis()
        store = RedisOccurrenceStore(redis)

        first = await store.reserve(make_sample())
        second = await store.reserve(make_sample())

        self.assertTrue(first.is_leader)
        self.assertFalse(second.is_leader)
        self.assertEqual(second.count, 2)
        self.assertEqual(await store.count(make_sample()), 2)
        key = next(iter(redis.hashes))
        self.assertNotIn("同一条 Spam", key)
        self.assertNotIn("123", key)

    async def test_user_and_original_unicode_are_part_of_fingerprint(self) -> None:
        store = RedisOccurrenceStore(FakeRedis())

        first = await store.reserve(make_sample(user_id=1, text="Ａ"))
        other_user = await store.reserve(make_sample(user_id=2, text="Ａ"))
        other_unicode = await store.reserve(make_sample(user_id=1, text="A"))

        self.assertTrue(first.is_leader)
        self.assertTrue(other_user.is_leader)
        self.assertTrue(other_unicode.is_leader)

    async def test_failed_leader_keeps_count_and_allows_retry(self) -> None:
        store = RedisOccurrenceStore(FakeRedis())
        first = await store.reserve(make_sample())
        self.assertTrue(await store.mark_retryable(first))

        retry = await store.reserve(make_sample())

        self.assertTrue(retry.is_leader)
        self.assertEqual(retry.count, 2)

    async def test_saved_entry_suppresses_followers(self) -> None:
        store = RedisOccurrenceStore(FakeRedis())
        first = await store.reserve(make_sample())
        self.assertTrue(await store.mark_saved(first, 99))

        follower = await store.reserve(make_sample())

        self.assertFalse(follower.is_leader)
        self.assertEqual(follower.count, 2)

    async def test_expired_processing_lease_can_be_reclaimed(self) -> None:
        store = RedisOccurrenceStore(FakeRedis(), lease_seconds=10)
        with patch("spamdet.dedupe.time.time", return_value=100):
            first = await store.reserve(make_sample())
        with patch("spamdet.dedupe.time.time", return_value=111):
            retry = await store.reserve(make_sample())

        self.assertTrue(first.is_leader)
        self.assertTrue(retry.is_leader)
        self.assertEqual(retry.count, 2)

    async def test_missing_user_or_non_rule_sample_bypasses_dedupe(self) -> None:
        store = RedisOccurrenceStore(FakeRedis())
        self.assertIsNone(await store.reserve(make_sample(user_id=None)))
        group = make_sample()
        group = SpamSample(source="group", message=group.message, user=group.user)
        self.assertIsNone(await store.reserve(group))


if __name__ == "__main__":
    unittest.main()
