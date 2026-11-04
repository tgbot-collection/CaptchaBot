"""规则命中样本的有界异步采集。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import replace
import logging
import time
from typing import Any, Protocol

from .schemas import SpamSample


class PendingSampleStore(Protocol):
    async def create_pending(self, sample: SpamSample) -> int: ...


class CaptureService:
    """在删除关键路径之外补充 Bio 并写入 pending 样本。"""

    def __init__(
        self,
        telegram_client: Any,
        store: PendingSampleStore,
        *,
        queue_size: int = 100,
        worker_count: int = 2,
        bio_timeout: float = 2.0,
        bio_cache_ttl: float = 300.0,
        bio_cache_size: int = 1000,
        shutdown_timeout: float = 5.0,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size 必须大于 0")
        if worker_count <= 0:
            raise ValueError("worker_count 必须大于 0")
        if bio_timeout <= 0:
            raise ValueError("bio_timeout 必须大于 0")
        if bio_cache_ttl <= 0:
            raise ValueError("bio_cache_ttl 必须大于 0")
        if bio_cache_size <= 0:
            raise ValueError("bio_cache_size 必须大于 0")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout 必须大于 0")

        self._telegram = telegram_client
        self._store = store
        self._queue: asyncio.Queue[SpamSample] = asyncio.Queue(maxsize=queue_size)
        self._worker_count = worker_count
        self._bio_timeout = bio_timeout
        self._bio_cache_ttl = bio_cache_ttl
        self._bio_cache_size = bio_cache_size
        self._shutdown_timeout = shutdown_timeout
        self._bio_cache: OrderedDict[int, tuple[float, str | None]] = OrderedDict()
        self._tasks: list[asyncio.Task[None]] = []
        self._accepting = False

    def start(self) -> None:
        if self._tasks:
            return
        self._accepting = True
        self._tasks = [
            asyncio.create_task(self._worker(), name=f"spam-capture-{index}")
            for index in range(self._worker_count)
        ]

    def submit(self, sample: SpamSample) -> bool:
        """非阻塞提交快照；队列已满或服务停止时返回 False。"""

        if sample.source != "rule" or sample.status != "pending" or sample.label is not None:
            raise ValueError("只能采集未标注的 rule/pending 样本")
        if not self._accepting:
            return False
        try:
            self._queue.put_nowait(sample)
        except asyncio.QueueFull:
            logging.warning("Spam 采集队列已满，本次样本未入队")
            return False
        return True

    async def wait_until_idle(self) -> None:
        """等待当前队列处理完成，主要用于验证和受控关停。"""

        await self._queue.join()

    async def aclose(self) -> None:
        self._accepting = False
        if not self._tasks:
            return
        try:
            async with asyncio.timeout(self._shutdown_timeout):
                await self._queue.join()
        except TimeoutError:
            logging.warning("Spam 采集关停超时，取消剩余后台任务")
        finally:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

    async def _worker(self) -> None:
        while True:
            sample = await self._queue.get()
            try:
                enriched = await self._with_bio(sample)
                await self._store.create_pending(enriched)
            except Exception as exc:
                # 不记录异常原文，避免上游库将消息或私密标识符带入日志。
                logging.warning("Spam 样本后台采集失败（%s）", type(exc).__name__)
            finally:
                self._queue.task_done()

    async def _with_bio(self, sample: SpamSample) -> SpamSample:
        user_id = sample.user.id
        if sample.user.bio is not None or user_id is None:
            return sample

        now = time.monotonic()
        cached = self._bio_cache.get(user_id)
        if cached is not None and cached[0] > now:
            self._bio_cache.move_to_end(user_id)
            return replace(sample, user=replace(sample.user, bio=cached[1]))
        if cached is not None:
            del self._bio_cache[user_id]

        bio: str | None = None
        try:
            async with asyncio.timeout(self._bio_timeout):
                chat = await self._telegram.get_chat(user_id)
            value = getattr(chat, "bio", None)
            bio = value if isinstance(value, str) else None
        except Exception:
            # 权限不足、限流、超时或字段缺失都以 null 继续采集。
            bio = None

        self._bio_cache[user_id] = (now + self._bio_cache_ttl, bio)
        self._bio_cache.move_to_end(user_id)
        while len(self._bio_cache) > self._bio_cache_size:
            self._bio_cache.popitem(last=False)
        return replace(sample, user=replace(sample.user, bio=bio))
