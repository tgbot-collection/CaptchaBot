"""基于 Redis 的规则审核重复消息聚合。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import secrets
import time
from typing import Any

from .schemas import SpamSample


_RESERVE_SCRIPT = """
local count = redis.call('HINCRBY', KEYS[1], 'count', 1)
local state = redis.call('HGET', KEYS[1], 'state')
local now = tonumber(ARGV[1])
local leader = 0

if not state or state == 'retryable' then
  leader = 1
elseif state == 'processing' then
  local lease_until = tonumber(redis.call('HGET', KEYS[1], 'lease_until') or '0')
  if lease_until <= now then
    leader = 1
  end
end

if leader == 1 then
  redis.call('HSET', KEYS[1],
    'state', 'processing',
    'owner', ARGV[2],
    'lease_until', now + tonumber(ARGV[3]))
end
if count == 1 then
  redis.call('HSET', KEYS[1], 'first_seen', now)
end
redis.call('HSET', KEYS[1], 'last_seen', now)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
return {leader, count}
"""

_MARK_SAVED_SCRIPT = """
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] then
  return 0
end
redis.call('HSET', KEYS[1], 'state', 'saved', 'sample_id', ARGV[2])
redis.call('HDEL', KEYS[1], 'owner', 'lease_until')
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""

_MARK_RETRYABLE_SCRIPT = """
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] then
  return 0
end
redis.call('HSET', KEYS[1], 'state', 'retryable')
redis.call('HDEL', KEYS[1], 'owner', 'lease_until')
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""


@dataclass(frozen=True, slots=True)
class OccurrenceReservation:
    """一次原子计数结果；只有 leader 可以继续写 D1。"""

    key: str
    owner: str | None
    is_leader: bool
    count: int


class RedisOccurrenceStore:
    """按“同一用户 + 完全相同可见内容”聚合规则审核。"""

    def __init__(
        self,
        redis_client: Any,
        *,
        ttl_seconds: int = 604800,
        lease_seconds: int = 60,
        key_prefix: str = "spamdet:review-dedupe:v1:",
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于 0")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        if ttl_seconds < lease_seconds:
            raise ValueError("ttl_seconds 不能小于 lease_seconds")
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._lease_seconds = lease_seconds
        self._key_prefix = key_prefix

    async def reserve(self, sample: SpamSample) -> OccurrenceReservation | None:
        """原子累加次数，并让首个或可重试调用者成为 leader。"""

        key = self._key_for(sample)
        if key is None:
            return None
        owner = secrets.token_hex(16)
        result = await self._redis.eval(
            _RESERVE_SCRIPT,
            1,
            key,
            int(time.time()),
            owner,
            self._lease_seconds,
            self._ttl_seconds,
        )
        is_leader, count = int(result[0]) == 1, int(result[1])
        return OccurrenceReservation(
            key=key,
            owner=owner if is_leader else None,
            is_leader=is_leader,
            count=count,
        )

    async def mark_saved(self, reservation: OccurrenceReservation, sample_id: int) -> bool:
        if not reservation.is_leader or reservation.owner is None:
            return False
        result = await self._redis.eval(
            _MARK_SAVED_SCRIPT,
            1,
            reservation.key,
            reservation.owner,
            sample_id,
            self._ttl_seconds,
        )
        return int(result) == 1

    async def mark_retryable(self, reservation: OccurrenceReservation) -> bool:
        if not reservation.is_leader or reservation.owner is None:
            return False
        result = await self._redis.eval(
            _MARK_RETRYABLE_SCRIPT,
            1,
            reservation.key,
            reservation.owner,
            self._ttl_seconds,
        )
        return int(result) == 1

    async def count(self, sample: SpamSample) -> int | None:
        key = self._key_for(sample)
        if key is None:
            return None
        value = await self._redis.hget(key, "count")
        return int(value) if value is not None else None

    def _key_for(self, sample: SpamSample) -> str | None:
        if sample.source != "rule" or sample.user.id is None:
            return None
        content = sample.message.text or sample.message.caption or ""
        if not content:
            return None
        canonical = json.dumps(
            ["spam-review-dedupe-v1", sample.user.id, content],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._key_prefix + hashlib.sha256(canonical).hexdigest()
