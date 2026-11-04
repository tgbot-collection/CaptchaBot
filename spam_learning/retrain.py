"""Story 007：审核数据导出、候选重训、评估和显式批准。"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .binary_model import LabeledText, load_binary_model, train_binary_model
from .config import ConfigError, SpamLearningConfig
from .d1 import D1Client, StoredSample
from .model_loader import LocalPredictor
from .schemas import SCHEMA_VERSION
from .seed_model import PreprocessConfig, normalize_text, read_seed_examples, train_seed_model


DATASET_FORMAT_VERSION = 1
REGRESSION_FORMAT_VERSION = 1
_TEMPLATE_DIGITS_RE = re.compile(r"\d+")
_TEMPLATE_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]{6,}", re.IGNORECASE)


class RetrainError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    text: str
    label: str
    normalized: str
    template_group: str
    sources: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "label": self.label,
            "normalized": self.normalized,
            "template_group": self.template_group,
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    records: tuple[DatasetRecord, ...]
    dataset_sha256: str
    class_counts: Mapping[str, int]
    source_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    precision: float
    recall: float
    false_positives: int
    confusion_matrix: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "false_positives": self.false_positives,
            "confusion_matrix": dict(self.confusion_matrix),
        }


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    candidate_dir: Path
    model_version: str
    kind: str
    dataset_sha256: str
    evaluation: Mapping[str, Any] | None


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _template_group(normalized: str) -> str:
    template = _TEMPLATE_DIGITS_RE.sub("<num>", normalized)
    template = _TEMPLATE_ASCII_TOKEN_RE.sub("<token>", template)
    return _sha256(template.encode("utf-8"))[:24]


def _stored_text(item: StoredSample) -> str:
    message = item.sample.get("message")
    if not isinstance(message, dict):
        raise RetrainError(f"D1 样本 {item.id} 缺少 message object")
    text = message.get("text")
    caption = message.get("caption")
    value = text if isinstance(text, str) and text else caption
    if not isinstance(value, str):
        raise RetrainError(f"D1 样本 {item.id} 没有可训练文本")
    return value


def build_dataset(
    seed_path: str | os.PathLike[str],
    reviewed: Iterable[StoredSample],
    *,
    preprocessing: PreprocessConfig = PreprocessConfig(),
) -> DatasetSummary:
    seed_examples, _, _ = read_seed_examples(seed_path, preprocessing)
    raw: list[tuple[str, str, dict[str, Any]]] = [
        (item.raw, "spam", {"kind": "seed", "line": index})
        for index, item in enumerate(seed_examples, 1)
    ]
    for item in reviewed:
        if item.status != "reviewed" or item.label not in {"spam", "ham"}:
            raise RetrainError(f"D1 导出包含非人工审核样本 id={item.id}")
        if item.schema_version != SCHEMA_VERSION:
            raise RetrainError(f"D1 样本 {item.id} 的 schema_version 不兼容")
        raw.append(
            (
                _stored_text(item),
                item.label,
                {
                    "kind": "d1",
                    "id": item.id,
                    "schema_version": item.schema_version,
                    "reviewed_at": item.reviewed_at,
                },
            )
        )

    grouped: dict[str, dict[str, Any]] = {}
    for text, label, source in raw:
        normalized = normalize_text(text, preprocessing)
        if not normalized:
            continue
        existing = grouped.get(normalized)
        if existing is not None and existing["label"] != label:
            raise RetrainError("同一归一化文本同时存在 spam/ham 标签，必须人工解决冲突")
        if existing is None:
            grouped[normalized] = {"text": text, "label": label, "sources": [source]}
        else:
            existing["sources"].append(source)

    records = tuple(
        DatasetRecord(
            text=value["text"],
            label=value["label"],
            normalized=normalized,
            template_group=_template_group(normalized),
            sources=tuple(value["sources"]),
        )
        for normalized, value in sorted(grouped.items())
    )
    if not any(record.label == "spam" for record in records):
        raise RetrainError("合并数据集没有已确认 Spam")
    payload = [record.to_dict() for record in records]
    source_counts = Counter(source["kind"] for record in records for source in record.sources)
    return DatasetSummary(
        records=records,
        dataset_sha256=_sha256(_canonical_json(payload)),
        class_counts=dict(Counter(record.label for record in records)),
        source_counts=dict(source_counts),
    )


def write_dataset(path: str | os.PathLike[str], dataset: DatasetSummary) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "type": "metadata",
        "format_version": DATASET_FORMAT_VERSION,
        "schema_compatibility": [SCHEMA_VERSION],
        "dataset_sha256": dataset.dataset_sha256,
        "class_counts": dict(dataset.class_counts),
        "source_counts": dict(dataset.source_counts),
    }
    content = b"\n".join([_canonical_json(header), *(_canonical_json(record.to_dict()) for record in dataset.records)]) + b"\n"
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, destination)


def read_dataset(path: str | os.PathLike[str]) -> DatasetSummary:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        values = [json.loads(line) for line in lines[1:]]
        records = tuple(
            DatasetRecord(
                text=value["text"],
                label=value["label"],
                normalized=value["normalized"],
                template_group=value["template_group"],
                sources=tuple(value["sources"]),
            )
            for value in values
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RetrainError("无法读取重训数据集 JSONL") from exc
    if header.get("format_version") != DATASET_FORMAT_VERSION or not records:
        raise RetrainError("重训数据集格式不受支持或为空")
    payload = [record.to_dict() for record in records]
    actual_hash = _sha256(_canonical_json(payload))
    if actual_hash != header.get("dataset_sha256"):
        raise RetrainError("重训数据集 hash 校验失败")
    if any(record.label not in {"spam", "ham"} for record in records):
        raise RetrainError("重训数据集包含非法标签")
    return DatasetSummary(
        records=records,
        dataset_sha256=actual_hash,
        class_counts=dict(Counter(record.label for record in records)),
        source_counts=dict(Counter(source["kind"] for record in records for source in record.sources)),
    )


def _grouped_split(records: Sequence[DatasetRecord]) -> tuple[list[DatasetRecord], list[DatasetRecord]] | None:
    by_label: dict[str, dict[str, list[DatasetRecord]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        by_label[record.label][record.template_group].append(record)
    if any(len(by_label[label]) < 2 for label in ("spam", "ham")):
        return None
    test_groups: set[tuple[str, str]] = set()
    for label in ("spam", "ham"):
        groups = sorted(by_label[label])
        test_count = max(1, round(len(groups) * 0.2))
        for group in groups[: min(test_count, len(groups) - 1)]:
            test_groups.add((label, group))
    train = [record for record in records if (record.label, record.template_group) not in test_groups]
    test = [record for record in records if (record.label, record.template_group) in test_groups]
    if {record.label for record in train} != {"spam", "ham"} or {record.label for record in test} != {"spam", "ham"}:
        return None
    return train, test


def _metrics(expected: Sequence[str], predicted: Sequence[str]) -> EvaluationMetrics:
    tp = sum(a == "spam" and b == "spam" for a, b in zip(expected, predicted, strict=True))
    fp = sum(a == "ham" and b == "spam" for a, b in zip(expected, predicted, strict=True))
    tn = sum(a == "ham" and b == "ham" for a, b in zip(expected, predicted, strict=True))
    fn = sum(a == "spam" and b == "ham" for a, b in zip(expected, predicted, strict=True))
    return EvaluationMetrics(
        precision=tp / (tp + fp) if tp + fp else 0.0,
        recall=tp / (tp + fn) if tp + fn else 0.0,
        false_positives=fp,
        confusion_matrix={"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    )


def _evaluate_classifier(train: Sequence[DatasetRecord], test: Sequence[DatasetRecord], root: Path, dataset_hash: str) -> EvaluationMetrics:
    summary = train_binary_model(
        [LabeledText(item.text, item.label) for item in train],
        root,
        dataset_sha256=dataset_hash,
    )
    model = load_binary_model(summary.manifest_path.parent)
    predicted = [model.predict_text(item.text).decision for item in test]
    return _metrics([item.label for item in test], predicted)


def _read_regression(path: str | os.PathLike[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if value.get("format_version", REGRESSION_FORMAT_VERSION) != REGRESSION_FORMAT_VERSION:
                raise RetrainError("回归集格式版本不受支持")
            text, label = value["text"], value["label"]
            if not isinstance(text, str) or label not in {"spam", "ham"}:
                raise RetrainError("回归集记录无效")
            result.append((text, label))
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RetrainError("无法读取回归集 JSONL") from exc
    if not result:
        raise RetrainError("回归集不能为空")
    return result


def _regression_report(model_path: Path, regression: Sequence[tuple[str, str]]) -> dict[str, Any]:
    predictor = LocalPredictor.from_path(model_path)
    if not predictor.available:
        return {"available": False}
    decisions = [predictor.predict_text(text).decision for text, _ in regression]
    mapped = ["spam" if value in {"spam", "spam_like"} else "ham" for value in decisions]
    metrics = _metrics([label for _, label in regression], mapped)
    return {
        "available": True,
        "kind": predictor.kind,
        "decision_mapping": "spam|spam_like=>spam; ham|unknown=>ham",
        **metrics.to_dict(),
    }


def build_candidate(
    dataset: DatasetSummary,
    candidates_dir: str | os.PathLike[str],
    *,
    classifier: bool = False,
    regression_path: str | os.PathLike[str] | None = None,
    old_model_path: str | os.PathLike[str] | None = None,
) -> CandidateSummary:
    split = _grouped_split(dataset.records) if classifier else None
    evaluation: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name)
        model_dir = temporary / "model"
        if classifier and split is not None:
            train, test = split
            holdout = _evaluate_classifier(train, test, temporary / "evaluation", dataset.dataset_sha256)
            evaluation = {
                "strategy": "deterministic_template_group_holdout",
                "train_count": len(train),
                "test_count": len(test),
                "metrics": holdout.to_dict(),
                "warning": "样本量门槛不代表模型质量，分数未校准",
            }
            summary = train_binary_model(
                [LabeledText(item.text, item.label) for item in dataset.records],
                model_dir,
                dataset_sha256=dataset.dataset_sha256,
                evaluation=evaluation,
            )
            kind = "logistic_regression"
            model_version = summary.model_version
            manifest_path = model_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_counts"] = dict(dataset.source_counts)
            manifest_path.write_bytes(_canonical_json(manifest))
        else:
            spam_file = temporary / "confirmed-spam.txt"
            spam_file.write_text("\n".join(item.text for item in dataset.records if item.label == "spam") + "\n", encoding="utf-8")
            summary = train_seed_model(spam_file, model_dir)
            kind = "similarity"
            model_version = summary.model_version
            manifest_path = model_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.update(
                {
                    "dataset_sha256": dataset.dataset_sha256,
                    "class_counts": dict(dataset.class_counts),
                    "source_counts": dict(dataset.source_counts),
                    "schema_compatibility": [SCHEMA_VERSION],
                    "evaluation": None,
                }
            )
            manifest_path.write_bytes(_canonical_json(manifest))

        regression: dict[str, Any] | None = None
        if regression_path is not None:
            cases = _read_regression(regression_path)
            regression = {"candidate": _regression_report(model_dir, cases)}
            if old_model_path is not None:
                regression["old_model"] = _regression_report(Path(old_model_path), cases)
            manifest_path = model_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["regression"] = regression
            manifest_path.write_bytes(_canonical_json(manifest))

        bundle_name = f"{model_version}-{dataset.dataset_sha256[:12]}"
        destination = Path(candidates_dir) / bundle_name
        if destination.exists():
            raise RetrainError(f"候选目录已存在，拒绝覆盖：{destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{bundle_name}.", dir=destination.parent))
        try:
            shutil.copytree(model_dir, staging, dirs_exist_ok=True)
            os.rename(staging, destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return CandidateSummary(destination, model_version, kind, dataset.dataset_sha256, evaluation)


def approve_candidate(
    candidate_dir: str | os.PathLike[str],
    releases_dir: str | os.PathLike[str],
    *,
    approved_by: str,
) -> Path:
    source = Path(candidate_dir)
    try:
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        version = manifest["model_version"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise RetrainError("候选模型 manifest 无效") from exc
    if not approved_by.strip():
        raise RetrainError("approved_by 不能为空")
    predictor = LocalPredictor.from_path(source)
    if not predictor.available or predictor.model_version != version:
        raise RetrainError("候选模型无法通过安全加载校验，拒绝批准")
    destination = Path(releases_dir) / str(version)
    if destination.exists():
        raise RetrainError(f"release 已存在，拒绝覆盖：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=destination.parent))
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True)
        approval = {"approved_by": approved_by.strip(), "candidate": source.name, "model_version": version}
        (staging / "approval.json").write_bytes(_canonical_json(approval))
        os.rename(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


async def export_from_d1(seed_path: Path, output_path: Path, page_size: int) -> DatasetSummary:
    config = SpamLearningConfig.from_env()
    client = D1Client.from_config(config)
    try:
        reviewed = await client.export_reviewed(page_size=page_size)
    finally:
        await client.aclose()
    dataset = build_dataset(seed_path, reviewed)
    write_dataset(output_path, dataset)
    return dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出人工审核数据并构建、批准 Spam 候选模型")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="从 D1 导出 reviewed 并合并 spam.txt")
    export.add_argument("--seed", type=Path, default=Path("spam.txt"))
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--page-size", type=int, default=200)
    build = commands.add_parser("build", help="从已导出的 JSONL 构建不可变候选")
    build.add_argument("--dataset", type=Path, required=True)
    build.add_argument("--candidates", type=Path, default=Path("models/candidates"))
    build.add_argument("--classifier", action="store_true")
    build.add_argument("--regression", type=Path)
    build.add_argument("--old-model", type=Path)
    approve = commands.add_parser("approve", help="显式批准候选并复制到 release 目录")
    approve.add_argument("--candidate", type=Path, required=True)
    approve.add_argument("--releases", type=Path, default=Path("models/releases"))
    approve.add_argument("--approved-by", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export":
            dataset = asyncio.run(export_from_d1(args.seed, args.output, args.page_size))
            print(f"数据集已导出：sha256={dataset.dataset_sha256} counts={dict(dataset.class_counts)}")
        elif args.command == "build":
            if args.old_model is not None and args.regression is None:
                raise RetrainError("--old-model 必须与 --regression 一起使用")
            summary = build_candidate(
                read_dataset(args.dataset),
                args.candidates,
                classifier=args.classifier,
                regression_path=args.regression,
                old_model_path=args.old_model,
            )
            if args.classifier and summary.kind != "logistic_regression":
                print("真实 Ham/Spam 模板组不足，已安全回退为相似度候选；不能据此宣称分类质量。")
            print(f"候选已生成：path={summary.candidate_dir} kind={summary.kind} version={summary.model_version}")
            print("候选尚未批准或部署。")
        else:
            destination = approve_candidate(args.candidate, args.releases, approved_by=args.approved_by)
            print(f"候选已人工批准：{destination}")
            print(f"发布时显式设置 SPAM_MODEL_PATH={destination}；回滚时改回上一 release 并重启。")
    except (RetrainError, ConfigError, ValueError) as exc:
        raise SystemExit(f"操作失败：{exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
