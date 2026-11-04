"""Telegram Spam 学习模块的公共基础接口。"""

from .config import ConfigError, SpamLearningConfig
from .schemas import (
    SCHEMA_VERSION,
    EntitySnapshot,
    MessageSnapshot,
    ModelSuggestion,
    SpamSample,
    UserSnapshot,
    ViaBotSnapshot,
    sample_from_message,
    sample_from_seed_text,
)

__all__ = [
    "SCHEMA_VERSION",
    "ConfigError",
    "EntitySnapshot",
    "MessageSnapshot",
    "ModelSuggestion",
    "SpamLearningConfig",
    "SpamSample",
    "UserSnapshot",
    "ViaBotSnapshot",
    "sample_from_message",
    "sample_from_seed_text",
]
