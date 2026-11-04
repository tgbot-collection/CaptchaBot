"""Telegram Spam 学习模块的公共基础接口。"""

from .config import ConfigError, SpamLearningConfig
from .capture import CaptureService
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
from .d1 import (
    D1APIError,
    D1Client,
    D1Error,
    D1HTTPError,
    D1NotFoundError,
    ReviewOutcome,
    SampleStats,
    StoredSample,
)
from .inference import InferenceService, ModelStatus, render_model_status
from .review import ReviewService, render_dataset_stats, render_pending_notification

__all__ = [
    "SCHEMA_VERSION",
    "ConfigError",
    "CaptureService",
    "D1APIError",
    "D1Client",
    "D1Error",
    "D1HTTPError",
    "D1NotFoundError",
    "EntitySnapshot",
    "MessageSnapshot",
    "InferenceService",
    "ModelStatus",
    "ModelSuggestion",
    "SpamLearningConfig",
    "SpamSample",
    "ReviewOutcome",
    "ReviewService",
    "SampleStats",
    "StoredSample",
    "UserSnapshot",
    "ViaBotSnapshot",
    "sample_from_message",
    "sample_from_seed_text",
    "render_dataset_stats",
    "render_model_status",
    "render_pending_notification",
]
