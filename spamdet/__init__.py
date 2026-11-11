"""Telegram Spam 学习模块的公共基础接口。"""

from .config import ConfigError, SpamLearningConfig
from .capture import CaptureService
from .content import ExtractedEntity, ExtractedMessageContent, extract_message_content
from .dedupe import OccurrenceReservation, RedisOccurrenceStore
from .features import ChannelWeights, FeatureChannels, extract_feature_channels
from .schemas import (
    SCHEMA_VERSION,
    EntitySnapshot,
    MessageSnapshot,
    ModelSuggestion,
    SpamSample,
    UserSnapshot,
    ViaBotSnapshot,
    sample_from_admin_forward,
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
from .model_loader import LocalPredictor
from .review import ReviewService, render_dataset_stats, render_pending_notification

__all__ = [
    "SCHEMA_VERSION",
    "ConfigError",
    "CaptureService",
    "ChannelWeights",
    "D1APIError",
    "D1Client",
    "D1Error",
    "D1HTTPError",
    "D1NotFoundError",
    "EntitySnapshot",
    "ExtractedEntity",
    "ExtractedMessageContent",
    "FeatureChannels",
    "MessageSnapshot",
    "InferenceService",
    "ModelStatus",
    "LocalPredictor",
    "ModelSuggestion",
    "OccurrenceReservation",
    "RedisOccurrenceStore",
    "SpamLearningConfig",
    "SpamSample",
    "ReviewOutcome",
    "ReviewService",
    "SampleStats",
    "StoredSample",
    "UserSnapshot",
    "ViaBotSnapshot",
    "sample_from_admin_forward",
    "sample_from_message",
    "sample_from_seed_text",
    "extract_feature_channels",
    "extract_message_content",
    "render_dataset_stats",
    "render_model_status",
    "render_pending_notification",
]
