"""少量人工 Spam 正例的确定性字符 TF-IDF 相似度模型。"""

from __future__ import annotations

import argparse
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
import unicodedata

from .schemas import SpamSample


MODEL_FORMAT_VERSION = 1
MODEL_KIND = "similarity"
DEFAULT_THRESHOLD = 0.65
DEFAULT_NGRAM_RANGE = (2, 5)
_MANIFEST_NAME = "manifest.json"
_MAX_MODEL_BYTES = 64 * 1024 * 1024
_ZERO_WIDTH_CHARACTERS = frozenset(
    {
        "\u00ad",  # soft hyphen
        "\u034f",  # combining grapheme joiner
        "\u061c",  # Arabic letter mark
        "\u180e",  # Mongolian vowel separator
        "\u200b",  # zero width space
        "\u200c",  # zero width non-joiner
        "\u200d",  # zero width joiner
        "\u200e",  # left-to-right mark
        "\u200f",  # right-to-left mark
        "\u2060",  # word joiner
        "\u2061",  # function application
        "\u2062",  # invisible times
        "\u2063",  # invisible separator
        "\u2064",  # invisible plus
        "\u2066",  # left-to-right isolate
        "\u2067",  # right-to-left isolate
        "\u2068",  # first strong isolate
        "\u2069",  # pop directional isolate
        "\ufeff",  # zero width no-break space / BOM
    }
)
_URL_RE = re.compile(
    r"(?i)(?<![\w@])(?:https?://|www\.|(?:t|telegram)\.me/)[^\s<>]+"
)
_MENTION_RE = re.compile(r"(?<![\w@])@[a-z0-9_]{1,32}\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


class ModelError(ValueError):
    """模型训练、保存或加载失败。"""


class TrainingDataError(ModelError):
    """训练语料为空或不可用。"""


class ModelLoadError(ModelError):
    """本地模型包缺失、损坏或格式不受支持。"""


@dataclass(frozen=True, slots=True)
class PreprocessConfig:
    """必须写入 manifest 的确定性预处理参数。"""

    remove_zero_width: bool = True
    nfkc: bool = True
    casefold: bool = True
    collapse_whitespace: bool = True
    tokenize_urls: bool = True
    tokenize_mentions: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {
            "remove_zero_width": self.remove_zero_width,
            "nfkc": self.nfkc,
            "casefold": self.casefold,
            "collapse_whitespace": self.collapse_whitespace,
            "tokenize_urls": self.tokenize_urls,
            "tokenize_mentions": self.tokenize_mentions,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreprocessConfig":
        expected = set(cls().to_dict())
        if set(value) != expected or any(not isinstance(value[key], bool) for key in expected):
            raise ModelLoadError("manifest 中的 preprocessing 配置无效")
        return cls(**{key: value[key] for key in expected})


@dataclass(frozen=True, slots=True)
class PredictionResult:
    """遵循 CONTRACTS.md 的建议性预测结果。"""

    kind: str
    decision: str
    score: float | None
    model_version: str | None
    nearest_example: str | None = None

    def to_dict(self) -> dict[str, str | float | None]:
        return {
            "kind": self.kind,
            "decision": self.decision,
            "score": self.score,
            "model_version": self.model_version,
            "nearest_example": self.nearest_example,
        }


@dataclass(frozen=True, slots=True)
class SeedExample:
    raw: str
    normalized: str


@dataclass(frozen=True, slots=True)
class TrainingSummary:
    model_version: str
    sample_count: int
    skipped_empty: int
    skipped_duplicate: int
    artifact_path: Path
    manifest_path: Path


def normalize_text(text: str, config: PreprocessConfig = PreprocessConfig()) -> str:
    """归一化用于特征提取的副本，绝不修改保存的原文。"""

    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    result = unicodedata.normalize("NFKC", text) if config.nfkc else text
    if config.remove_zero_width:
        result = "".join(character for character in result if character not in _ZERO_WIDTH_CHARACTERS)
    if config.casefold:
        result = result.casefold()
    if config.tokenize_urls:
        result = _URL_RE.sub(" urltoken ", result)
    if config.tokenize_mentions:
        result = _MENTION_RE.sub(" mentiontoken ", result)
    if config.collapse_whitespace:
        result = _WHITESPACE_RE.sub(" ", result).strip()
    return result


def read_seed_examples(
    path: str | os.PathLike[str],
    config: PreprocessConfig = PreprocessConfig(),
) -> tuple[list[SeedExample], int, int]:
    """读取 UTF-8 每行一条的种子；精确原文去重并保留首个顺序。"""

    try:
        content = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise TrainingDataError("无法读取 UTF-8 种子语料") from exc

    examples: list[SeedExample] = []
    seen: set[str] = set()
    skipped_empty = 0
    skipped_duplicate = 0
    for raw in content.splitlines():
        if not raw.strip():
            skipped_empty += 1
            continue
        if raw in seen:
            skipped_duplicate += 1
            continue
        seen.add(raw)
        normalized = normalize_text(raw, config)
        if not normalized:
            skipped_empty += 1
            continue
        examples.append(SeedExample(raw=raw, normalized=normalized))
    if not examples:
        raise TrainingDataError("种子语料没有可用的 Spam 正例")
    return examples, skipped_empty, skipped_duplicate


def _character_ngrams(text: str, min_n: int, max_n: int) -> Counter[str]:
    # 边界标记让单字符文本也能产生 2-gram，同时区分前后缀位置。
    bounded = f"^{text}$"
    counts: Counter[str] = Counter()
    for size in range(min_n, max_n + 1):
        for start in range(max(0, len(bounded) - size + 1)):
            counts[bounded[start : start + size]] += 1
    return counts


def _vectorize_counts(
    counts: Mapping[str, int],
    vocabulary_index: Mapping[str, int],
    idf: Sequence[float],
) -> dict[int, float]:
    weighted: dict[int, float] = {}
    for term, count in counts.items():
        index = vocabulary_index.get(term)
        if index is not None and count > 0:
            weighted[index] = (1.0 + math.log(count)) * idf[index]
    norm = math.sqrt(sum(value * value for value in weighted.values()))
    if norm == 0.0:
        return {}
    return {index: value / norm for index, value in weighted.items()}


def _cosine(left: Mapping[int, float], right: Mapping[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class SeedSimilarityModel:
    """已加载的不可变本地模型；只产生建议，不执行管理动作。"""

    def __init__(
        self,
        *,
        model_version: str,
        threshold: float,
        preprocess_config: PreprocessConfig,
        ngram_range: tuple[int, int],
        vocabulary: Sequence[str],
        idf: Sequence[float],
        examples: Sequence[SeedExample],
        vectors: Sequence[Mapping[int, float]],
    ) -> None:
        self.model_version = model_version
        self.threshold = threshold
        self.preprocess_config = preprocess_config
        self.ngram_range = ngram_range
        self.vocabulary = tuple(vocabulary)
        self.idf = tuple(idf)
        self.examples = tuple(examples)
        self.vectors = tuple(dict(vector) for vector in vectors)
        self._vocabulary_index = {term: index for index, term in enumerate(self.vocabulary)}

    def predict(self, sample: SpamSample) -> PredictionResult:
        text = sample.message.text or sample.message.caption or ""
        return self.predict_text(text)

    def predict_text(self, text: str) -> PredictionResult:
        normalized = normalize_text(text, self.preprocess_config)
        if not normalized:
            return PredictionResult(MODEL_KIND, "unknown", 0.0, self.model_version)
        query = _vectorize_counts(
            _character_ngrams(normalized, *self.ngram_range),
            self._vocabulary_index,
            self.idf,
        )
        if not query:
            return PredictionResult(MODEL_KIND, "unknown", 0.0, self.model_version)

        nearest_index = 0
        nearest_score = -1.0
        for index, vector in enumerate(self.vectors):
            score = _cosine(query, vector)
            if score > nearest_score:
                nearest_index = index
                nearest_score = score
        score = max(0.0, min(1.0, nearest_score))
        decision = "spam_like" if score >= self.threshold else "unknown"
        nearest = self.examples[nearest_index].raw if score > 0.0 else None
        return PredictionResult(MODEL_KIND, decision, score, self.model_version, nearest)


class SimilarityPredictor:
    """缺失或损坏模型时优雅返回 ``unavailable`` 的公共预测器。"""

    def __init__(self, model: SeedSimilarityModel | None) -> None:
        self._model = model

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> "SimilarityPredictor":
        try:
            return cls(load_seed_model(path))
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
            return _unavailable()
        return self._model.predict(sample)

    def predict_text(self, text: str) -> PredictionResult:
        if self._model is None:
            return _unavailable()
        return self._model.predict_text(text)


def _unavailable() -> PredictionResult:
    return PredictionResult(MODEL_KIND, "unavailable", None, None)


def train_seed_model(
    input_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    preprocess_config: PreprocessConfig = PreprocessConfig(),
    ngram_range: tuple[int, int] = DEFAULT_NGRAM_RANGE,
) -> TrainingSummary:
    """训练并原子发布安全 JSON 模型包。"""

    if not 0.0 < threshold <= 1.0:
        raise ValueError("threshold 必须位于 (0, 1] 区间")
    min_n, max_n = ngram_range
    if min_n <= 0 or max_n < min_n:
        raise ValueError("ngram_range 必须是递增的正整数区间")

    examples, skipped_empty, skipped_duplicate = read_seed_examples(input_path, preprocess_config)
    document_counts = [_character_ngrams(example.normalized, min_n, max_n) for example in examples]
    vocabulary = sorted({term for counts in document_counts for term in counts})
    if not vocabulary:
        raise TrainingDataError("种子语料无法生成字符 n-gram 特征")
    vocabulary_index = {term: index for index, term in enumerate(vocabulary)}
    document_frequency = Counter(term for counts in document_counts for term in counts)
    sample_count = len(examples)
    idf = [math.log((1.0 + sample_count) / (1.0 + document_frequency[term])) + 1.0 for term in vocabulary]
    vectors = [_vectorize_counts(counts, vocabulary_index, idf) for counts in document_counts]

    training_signature = {
        "format_version": MODEL_FORMAT_VERSION,
        "kind": MODEL_KIND,
        "threshold": threshold,
        "preprocessing": preprocess_config.to_dict(),
        "vectorizer": {
            "analyzer": "char",
            "ngram_range": [min_n, max_n],
            "boundary_markers": True,
            "term_frequency": "1+log(tf)",
            "inverse_document_frequency": "log((1+n)/(1+df))+1",
            "norm": "l2",
            "min_df": 1,
        },
        "examples": [{"raw": item.raw, "normalized": item.normalized} for item in examples],
    }
    signature_hash = _sha256(_canonical_json(training_signature))
    model_version = f"seed-sim-v1-{signature_hash[:16]}"
    artifact = {
        **training_signature,
        "model_version": model_version,
        "vocabulary": vocabulary,
        "idf": idf,
        "vectors": [
            [[index, value] for index, value in sorted(vector.items())]
            for vector in vectors
        ],
    }
    artifact_bytes = _canonical_json(artifact)
    artifact_sha256 = _sha256(artifact_bytes)
    artifact_name = f"{model_version}.json"
    manifest = {
        "format_version": MODEL_FORMAT_VERSION,
        "kind": MODEL_KIND,
        "model_version": model_version,
        "artifact": artifact_name,
        "artifact_sha256": artifact_sha256,
        "sample_count": sample_count,
        "threshold": threshold,
        "preprocessing": preprocess_config.to_dict(),
        "vectorizer": training_signature["vectorizer"],
        "training_corpus_sha256": _sha256(_canonical_json([item.raw for item in examples])),
    }

    destination = Path(output_dir)
    artifact_path = destination / artifact_name
    manifest_path = destination / _MANIFEST_NAME
    try:
        if not artifact_path.exists() or artifact_path.read_bytes() != artifact_bytes:
            _atomic_write(artifact_path, artifact_bytes)
        # manifest 最后替换；加载器不会观察到指向半成品的新版本。
        _atomic_write(manifest_path, _canonical_json(manifest))
    except OSError as exc:
        raise ModelError("无法保存本地模型包") from exc

    return TrainingSummary(
        model_version=model_version,
        sample_count=sample_count,
        skipped_empty=skipped_empty,
        skipped_duplicate=skipped_duplicate,
        artifact_path=artifact_path,
        manifest_path=manifest_path,
    )


def _read_json(path: Path) -> Any:
    try:
        size = path.stat().st_size
        if size > _MAX_MODEL_BYTES:
            raise ModelLoadError("模型文件超过安全大小限制")
        return json.loads(path.read_text(encoding="utf-8"))
    except ModelLoadError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelLoadError("无法读取本地 JSON 模型") from exc


def load_seed_model(model_dir: str | os.PathLike[str]) -> SeedSimilarityModel:
    """严格加载并校验本地模型；只接受 JSON，不执行序列化代码。"""

    directory = Path(model_dir)
    manifest = _read_json(directory / _MANIFEST_NAME)
    if not isinstance(manifest, dict):
        raise ModelLoadError("manifest 必须是 JSON object")
    if manifest.get("format_version") != MODEL_FORMAT_VERSION or manifest.get("kind") != MODEL_KIND:
        raise ModelLoadError("模型格式版本或类型不受支持")
    artifact_name = manifest.get("artifact")
    if not isinstance(artifact_name, str) or Path(artifact_name).name != artifact_name:
        raise ModelLoadError("manifest artifact 路径无效")
    artifact_path = directory / artifact_name
    try:
        artifact_bytes = artifact_path.read_bytes()
    except OSError as exc:
        raise ModelLoadError("模型 artifact 不存在") from exc
    if len(artifact_bytes) > _MAX_MODEL_BYTES:
        raise ModelLoadError("模型文件超过安全大小限制")
    if _sha256(artifact_bytes) != manifest.get("artifact_sha256"):
        raise ModelLoadError("模型 artifact 校验失败")
    try:
        artifact = json.loads(artifact_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ModelLoadError("模型 artifact 不是有效 UTF-8 JSON") from exc
    if not isinstance(artifact, dict):
        raise ModelLoadError("模型 artifact 必须是 JSON object")
    if (
        artifact.get("format_version") != MODEL_FORMAT_VERSION
        or artifact.get("kind") != MODEL_KIND
        or artifact.get("model_version") != manifest.get("model_version")
    ):
        raise ModelLoadError("manifest 与模型 artifact 不一致")

    try:
        threshold = float(artifact["threshold"])
        preprocess_config = PreprocessConfig.from_dict(artifact["preprocessing"])
        ngram_values = artifact["vectorizer"]["ngram_range"]
        min_n, max_n = int(ngram_values[0]), int(ngram_values[1])
        vocabulary = artifact["vocabulary"]
        idf = [float(value) for value in artifact["idf"]]
        examples = [SeedExample(raw=item["raw"], normalized=item["normalized"]) for item in artifact["examples"]]
        vectors = [
            {int(index): float(value) for index, value in vector}
            for vector in artifact["vectors"]
        ]
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ModelLoadError("模型 artifact 结构无效") from exc
    model_version = artifact.get("model_version")
    if (
        not isinstance(model_version, str)
        or re.fullmatch(r"seed-sim-v1-[0-9a-f]{16}", model_version) is None
        or not 0.0 < threshold <= 1.0
        or min_n <= 0
        or max_n < min_n
        or not vocabulary
        or any(not isinstance(term, str) for term in vocabulary)
        or len(set(vocabulary)) != len(vocabulary)
        or len(vocabulary) != len(idf)
        or not examples
        or len(examples) != len(vectors)
        or any(index < 0 or index >= len(vocabulary) for vector in vectors for index in vector)
        or any(not math.isfinite(value) or value <= 0.0 for value in idf)
        or any(not vector for vector in vectors)
        or any(not math.isfinite(value) or value <= 0.0 for vector in vectors for value in vector.values())
    ):
        raise ModelLoadError("模型 artifact 内容无效")
    if (
        manifest.get("sample_count") != len(examples)
        or manifest.get("threshold") != artifact.get("threshold")
        or manifest.get("preprocessing") != artifact.get("preprocessing")
        or manifest.get("vectorizer") != artifact.get("vectorizer")
    ):
        raise ModelLoadError("manifest 与模型参数不一致")
    return SeedSimilarityModel(
        model_version=model_version,
        threshold=threshold,
        preprocess_config=preprocess_config,
        ngram_range=(min_n, max_n),
        vocabulary=vocabulary,
        idf=idf,
        examples=examples,
        vectors=vectors,
    )


def _format_prediction(result: PredictionResult) -> str:
    score = "无" if result.score is None else f"{result.score:.6f}"
    nearest = result.nearest_example if result.nearest_example is not None else "无"
    return f"decision={result.decision} 相似度={score} nearest_example={nearest}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="训练或演示本地 Spam 正例相似度模型")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input", type=Path, help="UTF-8 Spam 种子文件，每行一条")
    mode.add_argument("--model", type=Path, help="已训练模型目录")
    parser.add_argument("--output", type=Path, help="训练输出目录")
    parser.add_argument("--text", action="append", default=[], help="训练后或加载后演示的文本，可重复")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="spam_like 相似度阈值")
    parser.add_argument("--keep-zero-width", action="store_true", help="保留零宽控制符（默认移除）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input is not None:
        if args.output is None:
            raise SystemExit("使用 --input 训练时必须提供 --output")
        try:
            summary = train_seed_model(
                args.input,
                args.output,
                threshold=args.threshold,
                preprocess_config=PreprocessConfig(remove_zero_width=not args.keep_zero_width),
            )
        except (ModelError, ValueError) as exc:
            raise SystemExit(f"训练失败：{exc}") from exc
        print(
            f"模型已生成：version={summary.model_version} samples={summary.sample_count} "
            f"empty_skipped={summary.skipped_empty} duplicate_skipped={summary.skipped_duplicate}"
        )
        predictor = SimilarityPredictor.from_path(args.output)
    else:
        if args.output is not None:
            raise SystemExit("加载模型时不需要 --output")
        predictor = SimilarityPredictor.from_path(args.model)
        if not predictor.available:
            print("模型不可用：decision=unavailable")

    for text in args.text:
        print(_format_prediction(predictor.predict_text(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
