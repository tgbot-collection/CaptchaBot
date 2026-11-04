from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from spam_learning.d1 import StoredSample
from spam_learning.model_loader import LocalPredictor
from spam_learning.retrain import (
    RetrainError,
    approve_candidate,
    build_candidate,
    build_dataset,
    read_dataset,
    write_dataset,
)


def stored(sample_id: int, text: str, label: str, *, status: str = "reviewed") -> StoredSample:
    return StoredSample(
        id=sample_id,
        schema_version=1,
        source="rule",
        sample={"message": {"text": text, "caption": None}, "user": {"id": sample_id}},
        text=text,
        status=status,
        label=label,
        model_version=None,
        prediction=None,
        prediction_score=None,
        created_at="2026-11-04 00:00:00",
        reviewed_at="2026-11-04 00:01:00",
        reviewed_by=1,
        review_chat_id=None,
        review_message_id=None,
        dedupe_key=f"key-{sample_id}",
    )


class RetrainTests(unittest.TestCase):
    def test_export_uses_only_reviewed_and_rejects_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            seed = Path(temporary) / "spam.txt"
            seed.write_text("确认 Spam\n", encoding="utf-8")
            with self.assertRaises(RetrainError):
                build_dataset(seed, [stored(1, "待审核", "spam", status="pending")])

    def test_merge_preserves_sources_and_rejects_conflicting_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            seed = Path(temporary) / "spam.txt"
            seed.write_text("重复 文本\n", encoding="utf-8")
            dataset = build_dataset(seed, [stored(1, "重复 文本", "spam")])
            self.assertEqual(len(dataset.records), 1)
            self.assertEqual({source["kind"] for source in dataset.records[0].sources}, {"seed", "d1"})
            with self.assertRaises(RetrainError):
                build_dataset(seed, [stored(2, "重复　文本", "ham")])

    def test_dataset_round_trip_and_hash_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "spam.txt"
            seed.write_text("正例\n", encoding="utf-8")
            dataset = build_dataset(seed, [stored(1, "正常讨论", "ham")])
            path = root / "dataset.jsonl"
            write_dataset(path, dataset)
            loaded = read_dataset(path)
            self.assertEqual(loaded.dataset_sha256, dataset.dataset_sha256)
            lines = path.read_text(encoding="utf-8").splitlines()
            value = json.loads(lines[-1])
            value["text"] = "篡改"
            lines[-1] = json.dumps(value, ensure_ascii=False)
            path.write_text("\n".join(lines), encoding="utf-8")
            with self.assertRaises(RetrainError):
                read_dataset(path)

    def test_zero_ham_still_builds_similarity_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "spam.txt"
            seed.write_text("收米兼职\n推广引流\n", encoding="utf-8")
            dataset = build_dataset(seed, [])
            candidate = build_candidate(dataset, root / "candidates", classifier=True)
            self.assertEqual(candidate.kind, "similarity")
            self.assertTrue((candidate.candidate_dir / "manifest.json").exists())
            with self.assertRaises(RetrainError):
                build_candidate(dataset, root / "candidates", classifier=True)

    def test_classifier_is_evaluated_loaded_and_only_suggests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "spam.txt"
            seed.write_text("兼职推广甲\n兼职推广乙\n", encoding="utf-8")
            reviewed = [
                stored(1, "投资返利甲", "spam"),
                stored(2, "投资返利乙", "spam"),
                stored(3, "今天讨论天气", "ham"),
                stored(4, "明天一起吃饭", "ham"),
            ]
            dataset = build_dataset(seed, reviewed)
            candidate = build_candidate(dataset, root / "candidates", classifier=True)
            predictor = LocalPredictor.from_path(candidate.candidate_dir)
            result = predictor.predict_text("兼职推广")
            self.assertEqual(candidate.kind, "logistic_regression")
            self.assertTrue(predictor.available)
            self.assertIn(result.decision, {"spam", "ham"})
            self.assertIsNotNone(candidate.evaluation)

    def test_approval_copies_candidate_and_never_overwrites_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "spam.txt"
            seed.write_text("确认 Spam\n", encoding="utf-8")
            candidate = build_candidate(build_dataset(seed, []), root / "candidates")
            release = approve_candidate(candidate.candidate_dir, root / "releases", approved_by="admin")
            self.assertTrue((release / "approval.json").exists())
            with self.assertRaises(RetrainError):
                approve_candidate(candidate.candidate_dir, root / "releases", approved_by="admin")


if __name__ == "__main__":
    unittest.main()
