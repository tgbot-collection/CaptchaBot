from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from spamdet.features import FeatureChannels, extract_feature_channels
from spamdet.schemas import MessageSnapshot, SpamSample, UserSnapshot, sample_from_seed_text
from spamdet.seed_model import (
    ModelLoadError,
    PreprocessConfig,
    SimilarityPredictor,
    TrainingDataError,
    load_seed_model,
    main,
    normalize_text,
    read_seed_examples,
    train_seed_model,
    train_feature_similarity_model,
)


class SeedModelTests(unittest.TestCase):
    def test_feature_contract_uses_bio_and_name_but_excludes_username_and_id(self):
        base = SpamSample(
            source="rule",
            message=MessageSnapshot(None, None, None, "普通短句", None),
            user=UserSnapshot(
                id=123,
                username="random_123456",
                first_name="接单",
                last_name="客服",
                bio="做单联系 @worker",
                bio_state="present",
            ),
        )
        other = SpamSample(
            source="rule",
            message=base.message,
            user=UserSnapshot(
                id=999,
                username="totally_different",
                first_name="接单",
                last_name="客服",
                bio="做单联系 @worker",
                bio_state="present",
            ),
        )
        self.assertEqual(extract_feature_channels(base), extract_feature_channels(other))

    def test_similarity_prediction_uses_bio_channel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = train_feature_similarity_model(
                [
                    (
                        FeatureChannels(
                            message="有时间就行",
                            bio="做单联系 @worker 公群 https://t.me/+example",
                            display_name="看喔竹耶",
                        ),
                        "有时间就行",
                    )
                ],
                root / "model",
            )
            model = load_seed_model(root / "model")
            sample = SpamSample(
                source="rule",
                message=MessageSnapshot(None, None, None, "有时间就行", None),
                user=UserSnapshot(
                    first_name="看喔竹耶",
                    bio="做单联系 @another 公群 https://t.me/+changed",
                    bio_state="present",
                ),
            )
            result = model.predict(sample)
        self.assertEqual(result.model_version, summary.model_version)
        self.assertEqual(result.decision, "spam_like")
        self.assertGreater(result.score or 0.0, 0.9)

    def test_normalize_is_deterministic_without_replacing_chinese(self):
        text = "  Ａ\u200bＢ　中文 HTTPS://EXAMPLE.COM/a  @Some_User  "
        self.assertEqual(normalize_text(text), "ab 中文 urltoken mentiontoken")
        self.assertIn("\u200b", normalize_text(text, PreprocessConfig(remove_zero_width=False)))

    def test_read_preserves_raw_and_skips_empty_and_exact_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "spam.txt"
            source.write_text("原 文\n\n原 文\n原　文\n\u200b\n", encoding="utf-8")
            examples, empty, duplicate = read_seed_examples(source)
        self.assertEqual([item.raw for item in examples], ["原 文", "原　文"])
        self.assertEqual([item.normalized for item in examples], ["原 文", "原 文"])
        self.assertEqual(empty, 2)
        self.assertEqual(duplicate, 1)

    def test_empty_corpus_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "spam.txt"
            source.write_text("\n\u200b\n", encoding="utf-8")
            with self.assertRaises(TrainingDataError):
                train_seed_model(source, Path(temporary) / "model")

    def test_train_load_predict_and_short_unicode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "spam.txt"
            source.write_text("帮我收米 日入上万\n加我兼职\n骗\n", encoding="utf-8")
            summary = train_seed_model(source, root / "model", threshold=0.65)
            model = load_seed_model(root / "model")

            exact = model.predict_text("帮我收米 日入上万")
            self.assertEqual(exact.decision, "spam_like")
            self.assertAlmostEqual(exact.score or 0.0, 1.0)
            self.assertEqual(exact.nearest_example, "帮我收米 日入上万")
            self.assertEqual(exact.model_version, summary.model_version)

            short = model.predict_text("骗")
            self.assertEqual(short.decision, "spam_like")
            self.assertAlmostEqual(short.score or 0.0, 1.0)
            unknown = model.predict_text("完全不同的日常讨论")
            self.assertEqual(unknown.decision, "unknown")
            self.assertIsNotNone(unknown.score)
            empty = model.predict_text("\u200b")
            self.assertEqual(empty.decision, "unknown")
            self.assertEqual(empty.score, 0.0)
            self.assertIsNone(empty.nearest_example)

            sample_result = model.predict(sample_from_seed_text("加我兼职"))
            self.assertEqual(sample_result.decision, "spam_like")

    def test_training_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "spam.txt"
            source.write_text("样本甲\n样本乙\n", encoding="utf-8")
            first = train_seed_model(source, root / "first")
            second = train_seed_model(source, root / "second")
            self.assertEqual(first.model_version, second.model_version)
            self.assertEqual(first.manifest_path.read_bytes(), second.manifest_path.read_bytes())
            self.assertEqual(first.artifact_path.read_bytes(), second.artifact_path.read_bytes())

    def test_manifest_rejects_path_traversal_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "spam.txt"
            source.write_text("样本\n", encoding="utf-8")
            summary = train_seed_model(source, root / "model")

            summary.artifact_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ModelLoadError):
                load_seed_model(root / "model")

            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
            manifest["artifact"] = "../outside.json"
            summary.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ModelLoadError):
                load_seed_model(root / "model")

    def test_predictor_gracefully_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            predictor = SimilarityPredictor.from_path(Path(temporary) / "missing")
            result = predictor.predict_text("anything")
        self.assertFalse(predictor.available)
        self.assertEqual(result.decision, "unavailable")
        self.assertIsNone(result.score)
        self.assertIsNone(result.model_version)

    def test_cli_trains_and_demonstrates_similarity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "spam.txt"
            source.write_text("加我兼职\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--input",
                        str(source),
                        "--output",
                        str(root / "model"),
                        "--text",
                        "加我兼职",
                    ]
                )
        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("模型已生成", rendered)
        self.assertIn("decision=spam_like", rendered)
        self.assertIn("相似度=1.000000", rendered)
        self.assertIn("nearest_example=加我兼职", rendered)
        self.assertNotIn("概率", rendered)


if __name__ == "__main__":
    unittest.main()
