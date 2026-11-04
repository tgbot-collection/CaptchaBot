"""Spam 学习功能的环境变量配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
import os


class ConfigError(ValueError):
    """配置值缺失或格式不正确。"""


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


def _parse_bool(name: str, value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name} 必须是 true/false、1/0、yes/no 或 on/off")


def _parse_admin_ids(value: str | None) -> tuple[int, ...]:
    if value is None or not value.strip():
        return ()

    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            raise ConfigError("SPAM_ADMIN_IDS 不能包含空值")
        try:
            admin_id = int(item)
        except ValueError as exc:
            raise ConfigError("SPAM_ADMIN_IDS 必须是逗号分隔的整数") from exc
        if admin_id <= 0:
            raise ConfigError("SPAM_ADMIN_IDS 必须是正整数")
        if admin_id not in result:
            result.append(admin_id)
    return tuple(result)


def _optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class SpamLearningConfig:
    """默认关闭的 Spam 学习配置。

    功能关闭时允许 D1 和管理员配置为空；开启时必须提供后续采集与审核
    所需的最小配置。Story 001 只解析配置，不建立任何网络连接。
    """

    enabled: bool = False
    admin_ids: tuple[int, ...] = ()
    d1_account_id: str | None = None
    d1_database_id: str | None = None
    d1_api_token: str | None = field(default=None, repr=False)
    model_path: Path = Path("models/spam_learning")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SpamLearningConfig":
        values = os.environ if environ is None else environ
        enabled = _parse_bool(
            "SPAM_LEARNING_ENABLED",
            values.get("SPAM_LEARNING_ENABLED"),
            default=False,
        )
        model_path_value = values.get("SPAM_MODEL_PATH", "models/spam_learning").strip()
        if not model_path_value:
            raise ConfigError("SPAM_MODEL_PATH 不能为空")

        config = cls(
            enabled=enabled,
            admin_ids=_parse_admin_ids(values.get("SPAM_ADMIN_IDS")),
            d1_account_id=_optional_value(values.get("D1_ACCOUNT_ID")),
            d1_database_id=_optional_value(values.get("D1_DATABASE_ID")),
            d1_api_token=_optional_value(values.get("D1_API_TOKEN")),
            model_path=Path(model_path_value),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.enabled:
            return

        missing: list[str] = []
        if not self.admin_ids:
            missing.append("SPAM_ADMIN_IDS")
        missing.extend(self._missing_d1_config())
        if missing:
            raise ConfigError("SPAM_LEARNING_ENABLED 开启时缺少配置：" + ", ".join(missing))

    def _missing_d1_config(self) -> list[str]:
        missing: list[str] = []
        if not self.d1_account_id:
            missing.append("D1_ACCOUNT_ID")
        if not self.d1_database_id:
            missing.append("D1_DATABASE_ID")
        if not self.d1_api_token:
            missing.append("D1_API_TOKEN")
        return missing

    def require_d1(self) -> tuple[str, str, str]:
        """返回 D1 凭证；迁移工具可在主功能关闭时独立使用。"""

        missing = self._missing_d1_config()
        if missing:
            raise ConfigError("D1 操作缺少配置：" + ", ".join(missing))
        # 上面的检查已排除 None；显式断言可帮助静态类型检查器收窄类型。
        assert self.d1_account_id is not None
        assert self.d1_database_id is not None
        assert self.d1_api_token is not None
        return self.d1_account_id, self.d1_database_id, self.d1_api_token
