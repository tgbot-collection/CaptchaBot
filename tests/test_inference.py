from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import threading
import time
import unittest

from spam_learning.binary_model import LabeledText, train_binary_model
from spam_learning.inference import InferenceService, ModelStatus, render_model_status
from spam_learning.schemas import MessageSnapshot, SpamSample
from spam_learning.seed_model import PredictionResult, train_seed_model


def make_sample(text: str = "帮我收米") -> SpamSample:
    return SpamSample(
        source="rule",
        message=MessageSnapshot(
            chat_id=-100123,
            message_id=42,
            date="2026-11-04T08:00:00Z",
            text=text,
            caption=None,
        ),
        matched_rule="blacklist_message:收米",
    )


class BlockingPredictor:
    kind = "similarity"
    available = True
    model_version = "test-v1"

    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()

    def predict(self, sample: SpamSample) -> PredictionResult:
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.03)
        with self.lock:
            self.active -= 1
        return PredictionResult("similarity", "unknown", 0.1, self.model_version)


class FailingPredictor:
    kind = "similarity"
    available = False
    model_version = None

    def predict(self, sample: SpamSample) -> PredictionResult:
        raise RuntimeError("prediction failed")


class InferenceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_loads_model_once_and_maps_prediction_to_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "spam.txt"
            source.write_text("帮我收米\n加我兼职\n", encoding="utf-8")
            summary = train_seed_model(source, root / "model")
            service = InferenceService.from_path(str(root / "model"), max_concurrency=1)

            enriched = await service.enrich(make_sample())

        self.assertTrue(service.status.available)
        self.assertEqual(service.status.version, summary.model_version)
        self.assertIsNotNone(enriched.model)
        assert enriched.model is not None
        self.assertEqual(enriched.model.version, summary.model_version)
        self.assertEqual(enriched.model.decision, "spam_like")
        self.assertIsNotNone(enriched.model.score)

    async def test_missing_model_becomes_unavailable_without_raising(self) -> None:
        service = InferenceService.from_path("/path/that/does/not/exist")

        enriched = await service.enrich(make_sample())

        self.assertFalse(service.status.available)
        assert enriched.model is not None
        self.assertEqual(enriched.model.decision, "unavailable")
        self.assertIsNone(enriched.model.version)
        self.assertIsNone(enriched.model.score)

    async def test_loads_approved_format_classifier_as_advice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            train_binary_model(
                [
                    LabeledText("兼职推广", "spam"),
                    LabeledText("投资返利", "spam"),
                    LabeledText("今晚吃饭", "ham"),
                    LabeledText("讨论天气", "ham"),
                ],
                root / "model",
                dataset_sha256="0" * 64,
            )
            service = InferenceService.from_path(str(root / "model"))
            enriched = await service.enrich(make_sample("兼职推广"))

        self.assertTrue(service.status.available)
        self.assertEqual(service.status.kind, "logistic_regression")
        assert enriched.model is not None
        self.assertEqual(enriched.model.kind, "logistic_regression")
        self.assertIn(enriched.model.decision, {"spam", "ham"})
        self.assertIsNotNone(enriched.model.score)

    async def test_runtime_failure_becomes_unavailable(self) -> None:
        service = InferenceService(FailingPredictor())

        enriched = await service.enrich(make_sample())

        assert enriched.model is not None
        self.assertEqual(enriched.model.decision, "unavailable")

    async def test_inference_concurrency_is_bounded(self) -> None:
        predictor = BlockingPredictor()
        service = InferenceService(predictor, max_concurrency=2)

        await asyncio.gather(*(service.enrich(make_sample(str(index))) for index in range(6)))

        self.assertEqual(predictor.maximum_active, 2)

    async def test_model_status_explains_all_decisions_and_score_semantics(self) -> None:
        text = render_model_status(
            ModelStatus(available=False, kind="similarity", version=None, built_at=None)
        )
        self.assertIn("可用性: 不可用", text)
        self.assertIn("构建日期: 未记录", text)
        self.assertIn("spam_like", text)
        self.assertIn("unknown", text)
        self.assertIn("unavailable", text)
        self.assertIn("相似度，不是 Spam 概率", text)
        classifier_text = render_model_status(
            ModelStatus(available=True, kind="logistic_regression", version="test", built_at=None)
        )
        self.assertIn("不能触发任何管理动作", classifier_text)
        self.assertIn("未校准", classifier_text)


if __name__ == "__main__":
    unittest.main()
