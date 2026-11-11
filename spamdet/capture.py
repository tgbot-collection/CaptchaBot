"""规则命中样本的有界异步采集。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import replace
import logging
import time
from typing import Any, Protocol

from .dedupe import OccurrenceReservation
from .schemas import ModelSuggestion, SpamSample


class PendingSampleStore(Protocol):
    async def create_pending(self, sample: SpamSample) -> int: ...


class PendingSampleNotifier(Protocol):
    async def notify_pending(self, sample_id: int, sample: SpamSample) -> None: ...


class SampleInference(Protocol):
    async def enrich(self, sample: SpamSample) -> SpamSample: ...


class OccurrenceDeduplicator(Protocol):
    async def reserve(self, sample: SpamSample) -> OccurrenceReservation | None: ...

    async def mark_saved(self, reservation: OccurrenceReservation, sample_id: int) -> bool: ...

    async def mark_retryable(self, reservation: OccurrenceReservation) -> bool: ...


class CaptureService:
    """在删除关键路径之外补充 Bio 并写入 pending 样本。"""

    def __init__(
        self,
        telegram_client: Any,
        store: PendingSampleStore,
        notifier: PendingSampleNotifier | None = None,
        *,
        inference: SampleInference | None = None,
        deduplicator: OccurrenceDeduplicator | None = None,
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
        self._notifier = notifier
        self._inference = inference
        self._deduplicator = deduplicator
        self._queue: asyncio.Queue[SpamSample] = asyncio.Queue(maxsize=queue_size)
        self._worker_count = worker_count
        self._bio_timeout = bio_timeout
        self._bio_cache_ttl = bio_cache_ttl
        self._bio_cache_size = bio_cache_size
        self._shutdown_timeout = shutdown_timeout
        self._bio_cache: OrderedDict[int, tuple[float, str | None, str]] = OrderedDict()
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

        if sample.source not in {"rule", "group"} or sample.status != "pending" or sample.label is not None:
            raise ValueError("只能采集未标注的 rule/group pending 样本")
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
            reservation: OccurrenceReservation | None = None
            try:
                if self._deduplicator is not None and sample.source == "rule":
                    try:
                        reservation = await self._deduplicator.reserve(sample)
                    except Exception as exc:
                        # Redis 故障时沿用原流程，宁可重复审核也不能静默丢样本。
                        logging.warning("Spam 重复审核计数失败（%s）", type(exc).__name__)
                    if reservation is not None and not reservation.is_leader:
                        logging.info("Spam 重复审核已合并，出现次数=%s", reservation.count)
                        continue
                enriched = await self._with_bio(sample) if sample.source == "rule" else sample
                enriched = await self._with_prediction(enriched)
                sample_id = await self._store.create_pending(enriched)
                if reservation is not None:
                    try:
                        if not await self._deduplicator.mark_saved(reservation, sample_id):
                            logging.warning("Spam 重复审核保存状态未更新（租约已变化）")
                    except Exception as exc:
                        logging.warning("Spam 重复审核保存状态更新失败（%s）", type(exc).__name__)
                if self._notifier is not None and sample.source == "rule":
                    try:
                        await self._notifier.notify_pending(sample_id, enriched)
                    except Exception as exc:
                        # pending 已成功写入；通知失败不能伪装为未保存或删除记录。
                        logging.warning("Spam 审核通知处理失败（%s）", type(exc).__name__)
            except Exception as exc:
                # 不记录异常原文，避免上游库将消息或私密标识符带入日志。
                logging.warning("Spam 样本后台采集失败（%s）", type(exc).__name__)
                if reservation is not None and reservation.is_leader:
                    try:
                        if not await self._deduplicator.mark_retryable(reservation):
                            logging.warning("Spam 重复审核重试状态未更新（租约已变化）")
                    except Exception as retry_exc:
                        logging.warning("Spam 重复审核重试状态更新失败（%s）", type(retry_exc).__name__)
            finally:
                self._queue.task_done()

    async def _with_prediction(self, sample: SpamSample) -> SpamSample:
        if self._inference is None:
            return sample
        try:
            return await self._inference.enrich(sample)
        except Exception as exc:
            # 即使推理服务自身出现意外故障，也必须继续写入 pending。
            logging.warning("Spam 模型建议生成失败（%s）", type(exc).__name__)
            return replace(
                sample,
                model=ModelSuggestion(
                    version=None,
                    kind="similarity",
                    decision="unavailable",
                    score=None,
                ),
            )

    async def _with_bio(self, sample: SpamSample) -> SpamSample:
        user_id = sample.user.id
        if sample.user.bio_state != "not_requested" or user_id is None:
            return sample

        now = time.monotonic()
        cached = self._bio_cache.get(user_id)
        if cached is not None and cached[0] > now:
            self._bio_cache.move_to_end(user_id)
            return replace(sample, user=replace(sample.user, bio=cached[1], bio_state=cached[2]))
        if cached is not None:
            del self._bio_cache[user_id]

        bio: str | None = None
        bio_state = "unavailable"
        try:
            async with asyncio.timeout(self._bio_timeout):
                chat = await self._telegram.get_chat(user_id)
            value = getattr(chat, "bio", None)
            bio = value if isinstance(value, str) and value else None
            bio_state = "present" if bio else "empty"
        except Exception:
            # 权限不足、限流、超时或字段缺失都以 null 继续采集。
            bio = None
            bio_state = "unavailable"

        self._bio_cache[user_id] = (now + self._bio_cache_ttl, bio, bio_state)
        self._bio_cache.move_to_end(user_id)
        while len(self._bio_cache) > self._bio_cache_size:
            self._bio_cache.popitem(last=False)
        return replace(sample, user=replace(sample.user, bio=bio, bio_state=bio_state))
