"""建议性模型推理及管理员可见的模型状态。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import logging
import time
from typing import Protocol

from .schemas import ModelSuggestion, SpamSample
from .model_loader import LocalPredictor
from .seed_model import MODEL_KIND, PredictionResult


class Predictor(Protocol):
    kind: str

    @property
    def available(self) -> bool: ...

    @property
    def model_version(self) -> str | None: ...

    def predict(self, sample: SpamSample) -> PredictionResult: ...


@dataclass(frozen=True, slots=True)
class ModelStatus:
    available: bool
    kind: str
    version: str | None
    built_at: str | None = None


class InferenceService:
    """在线程池中执行本地预测，并显式限制并发。"""

    def __init__(self, predictor: Predictor, *, max_concurrency: int = 2) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency 必须大于 0")
        self._predictor = predictor
        self._semaphore = asyncio.Semaphore(max_concurrency)

    @classmethod
    def from_path(cls, path: str, *, max_concurrency: int = 2) -> "InferenceService":
        return cls(LocalPredictor.from_path(path), max_concurrency=max_concurrency)

    @property
    def status(self) -> ModelStatus:
        # V1 的确定性 manifest 不记录构建时间，不能用文件 mtime 冒充。
        return ModelStatus(
            available=self._predictor.available,
            kind=getattr(self._predictor, "kind", MODEL_KIND),
            version=self._predictor.model_version,
            built_at=None,
        )

    async def enrich(self, sample: SpamSample) -> SpamSample:
        started = time.perf_counter()
        try:
            async with self._semaphore:
                result = await asyncio.to_thread(self._predictor.predict, sample)
        except Exception as exc:
            # 模型故障只能降级建议，不能阻断 D1 pending 或人工审核。
            logging.warning("Spam 模型推理失败（%s）", type(exc).__name__)
            result = PredictionResult(
                getattr(self._predictor, "kind", MODEL_KIND),
                "unavailable",
                None,
                None,
            )
        duration_ms = (time.perf_counter() - started) * 1000
        logging.info(
            "Spam 模型推理完成 decision=%s duration_ms=%.3f",
            result.decision,
            duration_ms,
        )
        suggestion = ModelSuggestion(
            version=result.model_version,
            kind=result.kind,
            decision=result.decision,
            score=result.score,
            nearest_example=result.nearest_example,
        )
        return replace(sample, model=suggestion)


def render_model_status(status: ModelStatus) -> str:
    availability = "可用" if status.available else "不可用"
    version = status.version or "无"
    built_at = status.built_at or "未记录"
    if status.kind == "logistic_regression":
        semantics = (
            "spam / ham: 二分类模型建议，不能触发任何管理动作\n"
            "分数是未校准的 Spam 分数，不应解释为可靠概率。"
        )
    else:
        semantics = (
            "spam_like: 与已确认 Spam 种子较相似，仅供参考\n"
            "unknown: 未达到相似度阈值，不等于 Ham\n"
            "分数表示余弦相似度，不是 Spam 概率。"
        )
    return (
        "Spam 模型状态\n"
        f"可用性: {availability}\n"
        f"类型: {status.kind}\n"
        f"版本: {version}\n"
        f"构建日期: {built_at}\n\n"
        f"{semantics}\n"
        "unavailable: 模型不可用，不影响保存和人工审核"
    )
