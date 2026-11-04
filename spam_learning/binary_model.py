"""可复现、安全 JSON 格式的字符 TF-IDF Logistic Regression 模型。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from .schemas import SpamSample
from .seed_model import ModelLoadError, PredictionResult, PreprocessConfig, normalize_text


MODEL_KIND = "logistic_regression"
MODEL_FORMAT_VERSION = 1
DEFAULT_NGRAM_RANGE = (2, 5)
_MAX_MODEL_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class LabeledText:
    text: str
    label: str


@dataclass(frozen=True, slots=True)
class BinaryTrainingSummary:
    model_version: str
    artifact_path: Path
    manifest_path: Path


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _ngrams(text: str, minimum: int, maximum: int) -> Counter[str]:
    bounded = f"^{text}$"
    result: Counter[str] = Counter()
    for size in range(minimum, maximum + 1):
        for start in range(max(0, len(bounded) - size + 1)):
            result[bounded[start : start + size]] += 1
    return result


def _vector(
    counts: Mapping[str, int],
    vocabulary_index: Mapping[str, int],
    idf: Sequence[float],
) -> dict[int, float]:
    values: dict[int, float] = {}
    for term, count in counts.items():
        index = vocabulary_index.get(term)
        if index is not None and count > 0:
            values[index] = (1.0 + math.log(count)) * idf[index]
    norm = math.sqrt(sum(value * value for value in values.values()))
    return {} if norm == 0 else {index: value / norm for index, value in values.items()}


def _sigmoid(value: float) -> float:
    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


class BinaryTextModel:
    def __init__(
        self,
        *,
        model_version: str,
        preprocessing: PreprocessConfig,
        ngram_range: tuple[int, int],
        vocabulary: Sequence[str],
        idf: Sequence[float],
        weights: Sequence[float],
        bias: float,
        threshold: float,
    ) -> None:
        self.model_version = model_version
        self.preprocessing = preprocessing
        self.ngram_range = ngram_range
        self.vocabulary = tuple(vocabulary)
        self.idf = tuple(idf)
        self.weights = tuple(weights)
        self.bias = bias
        self.threshold = threshold
        self._vocabulary_index = {term: index for index, term in enumerate(self.vocabulary)}

    def predict(self, sample: SpamSample) -> PredictionResult:
        return self.predict_text(sample.message.text or sample.message.caption or "")

    def predict_text(self, text: str) -> PredictionResult:
        normalized = normalize_text(text, self.preprocessing)
        vector = _vector(
            _ngrams(normalized, *self.ngram_range),
            self._vocabulary_index,
            self.idf,
        )
        score = _sigmoid(self.bias + sum(self.weights[index] * value for index, value in vector.items()))
        decision = "spam" if score >= self.threshold else "ham"
        return PredictionResult(MODEL_KIND, decision, score, self.model_version)


class BinaryPredictor:
    kind = MODEL_KIND

    def __init__(self, model: BinaryTextModel | None) -> None:
        self._model = model

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> "BinaryPredictor":
        try:
            return cls(load_binary_model(path))
        except ModelLoadError:
            return cls(None)

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def model_version(self) -> str | None:
        return self._model.model_version if self._model is not None else None

    def predict(self, sample: SpamSample) -> PredictionResult:
        if self._model is None:
            return PredictionResult(MODEL_KIND, "unavailable", None, None)
        return self._model.predict(sample)

    def predict_text(self, text: str) -> PredictionResult:
        if self._model is None:
            return PredictionResult(MODEL_KIND, "unavailable", None, None)
        return self._model.predict_text(text)


def train_binary_model(
    examples: Sequence[LabeledText],
    output_dir: str | os.PathLike[str],
    *,
    dataset_sha256: str,
    evaluation: Mapping[str, Any] | None = None,
    preprocessing: PreprocessConfig = PreprocessConfig(),
    ngram_range: tuple[int, int] = DEFAULT_NGRAM_RANGE,
    iterations: int = 400,
    learning_rate: float = 0.5,
    l2: float = 0.01,
    threshold: float = 0.5,
) -> BinaryTrainingSummary:
    if not examples or {item.label for item in examples} != {"spam", "ham"}:
        raise ValueError("二分类训练必须同时包含真实 spam 和 ham")
    if iterations <= 0 or learning_rate <= 0 or l2 < 0 or not 0 < threshold < 1:
        raise ValueError("Logistic Regression 参数无效")
    minimum, maximum = ngram_range
    normalized = [normalize_text(item.text, preprocessing) for item in examples]
    if any(not text for text in normalized):
        raise ValueError("二分类训练文本归一化后不能为空")
    counts = [_ngrams(text, minimum, maximum) for text in normalized]
    vocabulary = sorted({term for document in counts for term in document})
    vocabulary_index = {term: index for index, term in enumerate(vocabulary)}
    frequency = Counter(term for document in counts for term in document)
    total = len(examples)
    idf = [math.log((1.0 + total) / (1.0 + frequency[term])) + 1.0 for term in vocabulary]
    vectors = [_vector(document, vocabulary_index, idf) for document in counts]
    labels = [1.0 if item.label == "spam" else 0.0 for item in examples]
    weights = [0.0] * len(vocabulary)
    positive_rate = min(1.0 - 1e-9, max(1e-9, sum(labels) / total))
    bias = math.log(positive_rate / (1.0 - positive_rate))
    for iteration in range(iterations):
        gradient: dict[int, float] = {}
        bias_gradient = 0.0
        for vector, label in zip(vectors, labels, strict=True):
            probability = _sigmoid(bias + sum(weights[index] * value for index, value in vector.items()))
            error = probability - label
            bias_gradient += error
            for index, value in vector.items():
                gradient[index] = gradient.get(index, 0.0) + error * value
        rate = learning_rate / math.sqrt(1.0 + iteration)
        bias -= rate * bias_gradient / total
        shrink = 1.0 - rate * l2
        weights = [value * shrink for value in weights]
        for index, value in gradient.items():
            weights[index] -= rate * value / total

    signature = {
        "format_version": MODEL_FORMAT_VERSION,
        "kind": MODEL_KIND,
        "dataset_sha256": dataset_sha256,
        "sample_count": len(examples),
        "class_counts": dict(Counter(item.label for item in examples)),
        "schema_compatibility": [1],
        "preprocessing": preprocessing.to_dict(),
        "vectorizer": {"analyzer": "char", "ngram_range": [minimum, maximum], "min_df": 1, "norm": "l2"},
        "classifier": {
            "algorithm": "batch_logistic_regression",
            "iterations": iterations,
            "learning_rate": learning_rate,
            "l2": l2,
            "threshold": threshold,
        },
    }
    version_hash = _sha256(_canonical_json({**signature, "vocabulary": vocabulary, "idf": idf, "weights": weights, "bias": bias}))
    model_version = f"char-logreg-v1-{version_hash[:16]}"
    artifact = {
        **signature,
        "model_version": model_version,
        "vocabulary": vocabulary,
        "idf": idf,
        "weights": weights,
        "bias": bias,
    }
    artifact_bytes = _canonical_json(artifact)
    artifact_hash = _sha256(artifact_bytes)
    artifact_name = f"{model_version}.json"
    manifest = {
        **signature,
        "model_version": model_version,
        "artifact": artifact_name,
        "artifact_sha256": artifact_hash,
        "evaluation": evaluation,
    }
    destination = Path(output_dir)
    artifact_path = destination / artifact_name
    manifest_path = destination / "manifest.json"
    try:
        _atomic_write(artifact_path, artifact_bytes)
        _atomic_write(manifest_path, _canonical_json(manifest))
    except OSError as exc:
        raise ValueError("无法保存二分类模型") from exc
    return BinaryTrainingSummary(model_version, artifact_path, manifest_path)


def load_binary_model(model_dir: str | os.PathLike[str]) -> BinaryTextModel:
    directory = Path(model_dir)
    try:
        manifest_bytes = (directory / "manifest.json").read_bytes()
        if len(manifest_bytes) > _MAX_MODEL_BYTES:
            raise ModelLoadError("模型 manifest 超过安全大小限制")
        manifest = json.loads(manifest_bytes)
        artifact_name = manifest["artifact"]
        if not isinstance(artifact_name, str) or Path(artifact_name).name != artifact_name:
            raise ModelLoadError("manifest artifact 路径无效")
        artifact_bytes = (directory / artifact_name).read_bytes()
        if len(artifact_bytes) > _MAX_MODEL_BYTES or _sha256(artifact_bytes) != manifest["artifact_sha256"]:
            raise ModelLoadError("二分类 artifact 校验失败")
        artifact = json.loads(artifact_bytes)
        if manifest.get("kind") != MODEL_KIND or artifact.get("kind") != MODEL_KIND:
            raise ModelLoadError("模型类型不受支持")
        model_version = artifact["model_version"]
        vocabulary = artifact["vocabulary"]
        idf = [float(value) for value in artifact["idf"]]
        weights = [float(value) for value in artifact["weights"]]
        minimum, maximum = map(int, artifact["vectorizer"]["ngram_range"])
        preprocessing = PreprocessConfig.from_dict(artifact["preprocessing"])
        bias = float(artifact["bias"])
        threshold = float(artifact["classifier"]["threshold"])
    except ModelLoadError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ModelLoadError("无法读取二分类 JSON 模型") from exc
    if (
        not isinstance(model_version, str)
        or re.fullmatch(r"char-logreg-v1-[0-9a-f]{16}", model_version) is None
        or manifest.get("model_version") != model_version
        or not vocabulary
        or any(not isinstance(term, str) for term in vocabulary)
        or len(vocabulary) != len(set(vocabulary))
        or len(vocabulary) != len(idf)
        or len(vocabulary) != len(weights)
        or any(not math.isfinite(value) for value in (*idf, *weights, bias))
        or minimum <= 0
        or maximum < minimum
        or not 0 < threshold < 1
    ):
        raise ModelLoadError("二分类模型内容无效")
    return BinaryTextModel(
        model_version=model_version,
        preprocessing=preprocessing,
        ngram_range=(minimum, maximum),
        vocabulary=vocabulary,
        idf=idf,
        weights=weights,
        bias=bias,
        threshold=threshold,
    )
