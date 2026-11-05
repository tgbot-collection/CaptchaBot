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
from .features import ChannelWeights, FeatureChannels
from .schemas import SCHEMA_VERSION
from .seed_model import (
    PreprocessConfig,
    normalize_text,
    read_seed_examples,
    train_feature_similarity_model,
)


DATASET_FORMAT_VERSION = 2
REGRESSION_FORMAT_VERSION = 1
_TEMPLATE_DIGITS_RE = re.compile(r"\d+")
_TEMPLATE_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]{6,}", re.IGNORECASE)


class RetrainError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    text: str
    bio: str
    display_name: str
    label: str
    normalized_message: str
    normalized_bio: str
    normalized_display_name: str
    message_template_group: str
    bio_template_group: str | None
    actor_groups: tuple[str, ...]
    sources: tuple[dict[str, Any], ...]

    @property
    def channels(self) -> FeatureChannels:
        return FeatureChannels(self.text, self.bio, self.display_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bio": self.bio,
            "display_name": self.display_name,
            "label": self.label,
            "normalized_message": self.normalized_message,
            "normalized_bio": self.normalized_bio,
            "normalized_display_name": self.normalized_display_name,
            "message_template_group": self.message_template_group,
            "bio_template_group": self.bio_template_group,
            "actor_groups": list(self.actor_groups),
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    records: tuple[DatasetRecord, ...]
    dataset_sha256: str
    class_counts: Mapping[str, int]
    source_counts: Mapping[str, int]
    feature_coverage: Mapping[str, Mapping[str, int]]


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


def _actor_group(user_id: object, fallback: str) -> str:
    if isinstance(user_id, int):
        return _sha256(f"telegram-user-v1:{user_id}".encode("utf-8"))[:24]
    return _sha256(f"sample-v1:{fallback}".encode("utf-8"))[:24]


def _feature_coverage(records: Sequence[DatasetRecord]) -> dict[str, dict[str, int]]:
    return {
        label: {
            "samples": sum(record.label == label for record in records),
            "bio": sum(record.label == label and bool(record.normalized_bio) for record in records),
            "display_name": sum(
                record.label == label and bool(record.normalized_display_name) for record in records
            ),
            "actors": len(
                {
                    actor
                    for record in records
                    if record.label == label
                    for actor in record.actor_groups
                }
            ),
        }
        for label in ("spam", "ham")
    }


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


def _stored_channels(item: StoredSample) -> FeatureChannels:
    message = _stored_text(item)
    user = item.sample.get("user")
    if not isinstance(user, dict):
        user = {}
    bio = user.get("bio") if isinstance(user.get("bio"), str) else ""
    display_name = " ".join(
        part.strip()
        for part in (user.get("first_name"), user.get("last_name"))
        if isinstance(part, str) and part.strip()
    )
    return FeatureChannels(message, bio, display_name)


def build_dataset(
    seed_path: str | os.PathLike[str],
    reviewed: Iterable[StoredSample],
    *,
    preprocessing: PreprocessConfig = PreprocessConfig(),
) -> DatasetSummary:
    seed_examples, _, _ = read_seed_examples(seed_path, preprocessing)
    raw: list[tuple[FeatureChannels, str, str, dict[str, Any]]] = [
        (FeatureChannels(message=item.raw), "spam", f"seed:{index}", {"kind": "seed", "line": index})
        for index, item in enumerate(seed_examples, 1)
    ]
    for item in reviewed:
        if item.status != "reviewed" or item.label not in {"spam", "ham"}:
            raise RetrainError(f"D1 导出包含非人工审核样本 id={item.id}")
        if item.schema_version != SCHEMA_VERSION:
            raise RetrainError(f"D1 样本 {item.id} 的 schema_version 不兼容")
        channels = _stored_channels(item)
        user = item.sample.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        raw.append(
            (
                channels,
                item.label,
                _actor_group(user_id, str(item.id)),
                {
                    "kind": "d1",
                    "id": item.id,
                    "schema_version": item.schema_version,
                    "reviewed_at": item.reviewed_at,
                },
            )
        )

    grouped: dict[str, dict[str, Any]] = {}
    for channels, label, actor_group, source in raw:
        normalized_message = normalize_text(channels.message, preprocessing)
        normalized_bio = normalize_text(channels.bio, preprocessing)
        normalized_display_name = normalize_text(channels.display_name, preprocessing)
        if not (normalized_message or normalized_bio or normalized_display_name):
            continue
        normalized_key = _canonical_json(
            {
                "message": normalized_message,
                "bio": normalized_bio,
                "display_name": normalized_display_name,
            }
        ).decode("utf-8")
        existing = grouped.get(normalized_key)
        if existing is not None and existing["label"] != label:
            raise RetrainError("同一归一化多字段样本同时存在 spam/ham 标签，必须人工解决冲突")
        if existing is None:
            grouped[normalized_key] = {
                "channels": channels,
                "label": label,
                "sources": [source],
                "actor_groups": {actor_group},
                "normalized_message": normalized_message,
                "normalized_bio": normalized_bio,
                "normalized_display_name": normalized_display_name,
            }
        else:
            existing["sources"].append(source)
            existing["actor_groups"].add(actor_group)

    records = tuple(
        DatasetRecord(
            text=value["channels"].message,
            bio=value["channels"].bio,
            display_name=value["channels"].display_name,
            label=value["label"],
            normalized_message=value["normalized_message"],
            normalized_bio=value["normalized_bio"],
            normalized_display_name=value["normalized_display_name"],
            message_template_group=_template_group(value["normalized_message"]),
            bio_template_group=(
                _template_group(value["normalized_bio"])
                if value["normalized_bio"]
                else None
            ),
            actor_groups=tuple(sorted(value["actor_groups"])),
            sources=tuple(value["sources"]),
        )
        for _, value in sorted(grouped.items())
    )
    if not any(record.label == "spam" for record in records):
        raise RetrainError("合并数据集没有已确认 Spam")
    payload = [record.to_dict() for record in records]
    source_counts = Counter(source["kind"] for record in records for source in record.sources)
    feature_coverage = _feature_coverage(records)
    return DatasetSummary(
        records=records,
        dataset_sha256=_sha256(_canonical_json(payload)),
        class_counts=dict(Counter(record.label for record in records)),
        source_counts=dict(source_counts),
        feature_coverage=feature_coverage,
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
        "feature_coverage": {key: dict(value) for key, value in dataset.feature_coverage.items()},
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
                bio=value["bio"],
                display_name=value["display_name"],
                label=value["label"],
                normalized_message=value["normalized_message"],
                normalized_bio=value["normalized_bio"],
                normalized_display_name=value["normalized_display_name"],
                message_template_group=value["message_template_group"],
                bio_template_group=value["bio_template_group"],
                actor_groups=tuple(value["actor_groups"]),
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
    feature_coverage = _feature_coverage(records)
    if header.get("feature_coverage") != feature_coverage:
        raise RetrainError("重训数据集特征覆盖统计不一致")
    return DatasetSummary(
        records=records,
        dataset_sha256=actual_hash,
        class_counts=dict(Counter(record.label for record in records)),
        source_counts=dict(Counter(source["kind"] for record in records for source in record.sources)),
        feature_coverage=feature_coverage,
    )


def _grouped_split(records: Sequence[DatasetRecord]) -> tuple[list[DatasetRecord], list[DatasetRecord]] | None:
    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    owners: dict[str, int] = {}
    for index, record in enumerate(records):
        tokens = [
            *(f"actor:{value}" for value in record.actor_groups),
            f"message:{record.message_template_group}",
        ]
        if record.bio_template_group is not None:
            tokens.append(f"bio:{record.bio_template_group}")
        for token in tokens:
            if token in owners:
                union(index, owners[token])
            else:
                owners[token] = index

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(records)):
        components[find(index)].append(index)
    component_labels = {
        root: {records[index].label for index in indices}
        for root, indices in components.items()
    }
    if any(sum(label in labels for labels in component_labels.values()) < 2 for label in ("spam", "ham")):
        return None

    selected: set[int] = set()
    for label in ("spam", "ham"):
        candidates = sorted(
            root for root, labels in component_labels.items()
            if label in labels and root not in selected
        )
        chosen = next(
            (
                root
                for root in candidates
                if {
                    records[index].label
                    for other_root, indices in components.items()
                    if other_root not in selected | {root}
                    for index in indices
                } == {"spam", "ham"}
            ),
            None,
        )
        if chosen is None:
            return None
        selected.add(chosen)

    train = [record for root, indices in components.items() if root not in selected for index in indices for record in [records[index]]]
    test = [record for root, indices in components.items() if root in selected for index in indices for record in [records[index]]]
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


def _labeled(record: DatasetRecord) -> LabeledText:
    return LabeledText(record.text, record.label, record.bio, record.display_name)


def _evaluate_classifier(
    train: Sequence[DatasetRecord],
    test: Sequence[DatasetRecord],
    root: Path,
    dataset_hash: str,
    channel_weights: ChannelWeights,
) -> EvaluationMetrics:
    summary = train_binary_model(
        [_labeled(item) for item in train],
        root,
        dataset_sha256=dataset_hash,
        channel_weights=channel_weights,
    )
    model = load_binary_model(summary.manifest_path.parent)
    predicted = [model.predict_channels(item.channels).decision for item in test]
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
    if classifier:
        bio_counts = [dataset.feature_coverage.get(label, {}).get("bio", 0) for label in ("spam", "ham")]
        if (bio_counts[0] == 0) != (bio_counts[1] == 0):
            raise RetrainError("Spam/Ham 只有一类包含 Bio，拒绝构建会学习缺失捷径的二分类候选")
    evaluation: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name)
        model_dir = temporary / "model"
        if classifier and split is not None:
            train, test = split
            feature_sets = {
                "message_only": ChannelWeights(1.0, 0.0, 0.0),
                "message_bio": ChannelWeights(1.0, 0.75, 0.0),
                "message_bio_display_name": ChannelWeights(),
            }
            metrics = {
                name: _evaluate_classifier(
                    train,
                    test,
                    temporary / f"evaluation-{name}",
                    dataset.dataset_sha256,
                    weights,
                ).to_dict()
                for name, weights in feature_sets.items()
            }
            evaluation = {
                "strategy": "deterministic_actor_message_bio_component_holdout",
                "train_count": len(train),
                "test_count": len(test),
                "feature_sets": metrics,
                "feature_coverage": {key: dict(value) for key, value in dataset.feature_coverage.items()},
                "warning": "样本量门槛不代表模型质量，分数未校准",
            }
            summary = train_binary_model(
                [_labeled(item) for item in dataset.records],
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
            spam_records = [item for item in dataset.records if item.label == "spam"]
            summary = train_feature_similarity_model(
                [(item.channels, item.text) for item in spam_records],
                model_dir,
            )
            kind = "similarity"
            model_version = summary.model_version
            manifest_path = model_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.update(
                {
                    "dataset_sha256": dataset.dataset_sha256,
                    "class_counts": dict(dataset.class_counts),
                    "source_counts": dict(dataset.source_counts),
                    "feature_coverage": {key: dict(value) for key, value in dataset.feature_coverage.items()},
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
