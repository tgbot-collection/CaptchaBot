"""训练与在线推理共用的多字段特征契约。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Callable, Mapping

from .schemas import SpamSample


@dataclass(frozen=True, slots=True)
class FeatureChannels:
    message: str = ""
    bio: str = ""
    display_name: str = ""

    def any(self) -> bool:
        return bool(self.message or self.bio or self.display_name)


@dataclass(frozen=True, slots=True)
class ChannelWeights:
    message: float = 1.0
    bio: float = 0.75
    display_name: float = 0.25

    def __post_init__(self) -> None:
        values = (self.message, self.bio, self.display_name)
        if any(not math.isfinite(value) or value < 0 for value in values) or not any(value > 0 for value in values):
            raise ValueError("特征通道权重必须非负且至少一个大于 0")

    def to_dict(self) -> dict[str, float]:
        return {
            "message": self.message,
            "bio": self.bio,
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ChannelWeights":
        if set(value) != {"message", "bio", "display_name"}:
            raise ValueError("特征通道权重字段无效")
        try:
            return cls(**{key: float(value[key]) for key in value})
        except (TypeError, ValueError) as exc:
            raise ValueError("特征通道权重无效") from exc


DEFAULT_CHANNEL_WEIGHTS = ChannelWeights()


def extract_feature_channels(sample: SpamSample) -> FeatureChannels:
    """只提取获准参与模型的字段；username、ID、规则和预测均被排除。"""

    display_name = " ".join(
        part.strip()
        for part in (sample.user.first_name, sample.user.last_name)
        if isinstance(part, str) and part.strip()
    )
    return FeatureChannels(
        message=sample.message.text or sample.message.caption or "",
        bio=sample.user.bio or "",
        display_name=display_name,
    )


def normalized_channels(
    channels: FeatureChannels,
    normalize: Callable[[str], str],
) -> FeatureChannels:
    return FeatureChannels(
        message=normalize(channels.message),
        bio=normalize(channels.bio),
        display_name=normalize(channels.display_name),
    )


def feature_term_counts(
    channels: FeatureChannels,
    normalize: Callable[[str], str],
    minimum: int,
    maximum: int,
) -> Counter[str]:
    """分别生成带通道前缀的字符 n-gram，避免字段语义互相污染。"""

    result: Counter[str] = Counter()
    normalized = normalized_channels(channels, normalize)
    for prefix, text in (
        ("m:", normalized.message),
        ("b:", normalized.bio),
        ("n:", normalized.display_name),
    ):
        if not text:
            continue
        bounded = f"^{text}$"
        for size in range(minimum, maximum + 1):
            for start in range(max(0, len(bounded) - size + 1)):
                result[prefix + bounded[start : start + size]] += 1
    return result


def term_channel_weight(term: str, weights: ChannelWeights) -> float:
    if term.startswith("m:"):
        return weights.message
    if term.startswith("b:"):
        return weights.bio
    if term.startswith("n:"):
        return weights.display_name
    raise ValueError("模型包含未知特征通道")
