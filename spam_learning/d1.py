"""Cloudflare D1 REST 存储客户端。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import httpx

from .config import SpamLearningConfig
from .schemas import SpamSample


_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_LABELS = frozenset({"spam", "ham"})


class D1Error(RuntimeError):
    """D1 操作失败的基类；异常文本不会包含 Token 或请求参数。"""


class D1HTTPError(D1Error):
    """HTTP、网络或超时错误。"""


class D1APIError(D1Error):
    """Cloudflare 返回 HTTP 200，但 API 或 SQL 业务执行失败。"""


class D1NotFoundError(D1Error):
    """目标样本不存在。"""


class ReviewOutcome(StrEnum):
    CHANGED = "changed"
    ALREADY_REVIEWED = "already_reviewed"


@dataclass(frozen=True, slots=True)
class StoredSample:
    id: int
    schema_version: int
    source: str
    sample: dict[str, Any]
    text: str
    status: str
    label: str | None
    model_version: str | None
    prediction: str | None
    prediction_score: float | None
    created_at: str
    reviewed_at: str | None
    reviewed_by: int | None
    review_chat_id: int | None
    review_message_id: int | None
    dedupe_key: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StoredSample":
        try:
            sample = json.loads(str(row["message_json"]))
            if not isinstance(sample, dict):
                raise TypeError
            return cls(
                id=int(row["id"]),
                schema_version=int(row["schema_version"]),
                source=str(row["source"]),
                sample=sample,
                text=str(row["text"]),
                status=str(row["status"]),
                label=str(row["label"]) if row.get("label") is not None else None,
                model_version=(str(row["model_version"]) if row.get("model_version") is not None else None),
                prediction=(str(row["prediction"]) if row.get("prediction") is not None else None),
                prediction_score=(
                    float(row["prediction_score"]) if row.get("prediction_score") is not None else None
                ),
                created_at=str(row["created_at"]),
                reviewed_at=(str(row["reviewed_at"]) if row.get("reviewed_at") is not None else None),
                reviewed_by=int(row["reviewed_by"]) if row.get("reviewed_by") is not None else None,
                review_chat_id=(int(row["review_chat_id"]) if row.get("review_chat_id") is not None else None),
                review_message_id=(
                    int(row["review_message_id"]) if row.get("review_message_id") is not None else None
                ),
                dedupe_key=str(row["dedupe_key"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise D1APIError("D1 返回了格式不正确的样本记录") from exc


@dataclass(frozen=True, slots=True)
class SampleStats:
    total: int
    pending: int
    reviewed: int
    spam: int
    ham: int


class D1Client:
    """使用参数化 SQL 访问 Cloudflare D1 Query REST API。"""

    def __init__(
        self,
        *,
        account_id: str,
        database_id: str,
        api_token: str,
        timeout: float = 5.0,
        max_retries: int = 2,
        retry_backoff: float = 0.2,
        max_concurrency: int = 4,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not account_id or not database_id or not api_token:
            raise ValueError("account_id、database_id 和 api_token 均不能为空")
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0")
        if retry_backoff < 0:
            raise ValueError("retry_backoff 不能小于 0")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency 必须大于 0")

        self._url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{quote(account_id, safe='')}/d1/database/{quote(database_id, safe='')}/query"
        )
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._timeout = httpx.Timeout(timeout)
        self._headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient()

    @classmethod
    def from_config(cls, config: SpamLearningConfig, **kwargs: Any) -> "D1Client":
        account_id, database_id, api_token = config.require_d1()
        return cls(
            account_id=account_id,
            database_id=database_id,
            api_token=api_token,
            **kwargs,
        )

    async def __aenter__(self) -> "D1Client":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def apply_migration(self, sql: str) -> None:
        """通过 D1 REST API 执行受信任的 Migration SQL。"""

        if not sql.strip():
            raise ValueError("Migration SQL 不能为空")
        await self._execute(sql, ())

    async def create_pending(self, sample: SpamSample) -> int:
        if sample.source not in {"rule", "group"} or sample.status != "pending" or sample.label is not None:
            raise ValueError("create_pending 只接受未标注的 rule/group pending 样本")
        if sample.message.chat_id is None or sample.message.message_id is None:
            raise ValueError("在线样本必须包含 chat_id 和 message_id")

        dedupe_key = _online_dedupe_key(sample)
        model = sample.model
        columns = ["schema_version", "source", "message_json", "text", "status"]
        params = [str(sample.schema_version), sample.source, sample.to_json(), sample.message.text, "pending"]
        if model is not None:
            columns.extend(("model_version", "prediction"))
            params.extend((model.version, model.decision))
            if model.score is not None:
                columns.append("prediction_score")
                params.append(str(model.score))
        columns.append("dedupe_key")
        params.append(dedupe_key)
        placeholders = ", ".join("?" for _ in columns)
        result = await self._query(
            f"""
            INSERT INTO samples ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(dedupe_key) DO NOTHING
            RETURNING id
            """,
            params,
        )
        rows = _result_rows(result)
        if rows:
            return _positive_id(rows[0].get("id"), "新建样本")

        existing = await self._query("SELECT id FROM samples WHERE dedupe_key = ?", (dedupe_key,))
        existing_rows = _result_rows(existing)
        if not existing_rows:
            raise D1APIError("D1 去重写入后未能读取样本")
        return _positive_id(existing_rows[0].get("id"), "已有样本")

    async def next_group_pending(self) -> StoredSample | None:
        result = await self._query(
            "SELECT * FROM samples WHERE source = 'group' AND status = 'pending' "
            "AND review_message_id IS NULL ORDER BY id ASC LIMIT 1", ()
        )
        rows = _result_rows(result)
        return StoredSample.from_row(rows[0]) if rows else None

    async def review(self, sample_id: int, label: str, admin_id: int) -> ReviewOutcome:
        sample_id = _positive_id(sample_id, "sample_id")
        admin_id = _positive_id(admin_id, "admin_id")
        if label not in _LABELS:
            raise ValueError("label 必须是 spam 或 ham")

        result = await self._query(
            """
            UPDATE samples
            SET status = 'reviewed', label = ?, reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?
            WHERE id = ? AND status = 'pending'
            RETURNING id
            """,
            (label, str(admin_id), str(sample_id)),
        )
        if _result_rows(result):
            return ReviewOutcome.CHANGED

        current = await self.get(sample_id)
        if current.status == "reviewed":
            return ReviewOutcome.ALREADY_REVIEWED
        raise D1APIError("样本审核状态异常，未执行覆盖")

    async def set_review_message(self, sample_id: int, chat_id: int, message_id: int) -> None:
        """记录已成功发送的单管理员审核通知位置。"""

        sample_id = _positive_id(sample_id, "sample_id")
        chat_id = _positive_id(chat_id, "chat_id")
        message_id = _positive_id(message_id, "message_id")
        result = await self._query(
            """
            UPDATE samples
            SET review_chat_id = ?, review_message_id = ?
            WHERE id = ?
            RETURNING id
            """,
            (str(chat_id), str(message_id), str(sample_id)),
        )
        if not _result_rows(result):
            raise D1NotFoundError(f"样本 {sample_id} 不存在")

    async def get(self, sample_id: int) -> StoredSample:
        sample_id = _positive_id(sample_id, "sample_id")
        result = await self._query("SELECT * FROM samples WHERE id = ?", (str(sample_id),))
        rows = _result_rows(result)
        if not rows:
            raise D1NotFoundError(f"样本 {sample_id} 不存在")
        return StoredSample.from_row(rows[0])

    async def stats(self) -> SampleStats:
        result = await self._query(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed,
                SUM(CASE WHEN label = 'spam' THEN 1 ELSE 0 END) AS spam,
                SUM(CASE WHEN label = 'ham' THEN 1 ELSE 0 END) AS ham
            FROM samples
            """,
            (),
        )
        rows = _result_rows(result)
        if len(rows) != 1:
            raise D1APIError("D1 返回了格式不正确的统计结果")
        row = rows[0]
        try:
            return SampleStats(**{name: int(row.get(name) or 0) for name in SampleStats.__dataclass_fields__})
        except (TypeError, ValueError) as exc:
            raise D1APIError("D1 返回了格式不正确的统计结果") from exc

    async def export_reviewed(self, *, page_size: int = 200) -> list[StoredSample]:
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size 必须在 1 到 1000 之间")

        exported: list[StoredSample] = []
        last_id = 0
        while True:
            result = await self._query(
                """
                SELECT * FROM samples
                WHERE status = 'reviewed' AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (str(last_id), str(page_size)),
            )
            rows = _result_rows(result)
            page = [StoredSample.from_row(row) for row in rows]
            exported.extend(page)
            if len(page) < page_size:
                return exported
            last_id = page[-1].id

    async def _query(self, sql: str, params: Sequence[str]) -> dict[str, Any]:
        results = await self._execute(sql, params)
        if len(results) != 1:
            raise D1APIError("D1 单条查询返回了意外的结果数量")
        return results[0]

    async def _execute(self, sql: str, params: Sequence[str]) -> list[dict[str, Any]]:
        async with self._semaphore:
            return await self._execute_limited(sql, params)

    async def _execute_limited(self, sql: str, params: Sequence[str]) -> list[dict[str, Any]]:
        payload = {"sql": sql, "params": list(params)}
        response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.post(
                    self._url,
                    json=payload,
                    headers=self._headers,
                    timeout=self._timeout,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self._max_retries:
                    raise D1HTTPError("D1 请求超时或网络不可用") from exc
                await self._retry_sleep(attempt)
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                await self._retry_sleep(attempt)
                continue
            if not 200 <= response.status_code < 300:
                raise D1HTTPError(f"D1 HTTP 请求失败，状态码 {response.status_code}")
            break

        if response is None:  # pragma: no cover - 循环逻辑的防御性保护
            raise D1HTTPError("D1 请求未获得响应")
        try:
            body = response.json()
        except ValueError as exc:
            raise D1APIError("D1 返回了非 JSON 响应") from exc
        if not isinstance(body, dict):
            raise D1APIError("D1 返回了格式不正确的响应")
        if body.get("success") is not True:
            raise D1APIError(_api_error_message(body.get("errors")))
        results = body.get("result")
        if not isinstance(results, list) or not results:
            raise D1APIError("D1 响应缺少 SQL 执行结果")
        for result in results:
            if not isinstance(result, dict) or result.get("success") is not True:
                raise D1APIError("D1 SQL 执行失败")
        return results

    async def _retry_sleep(self, attempt: int) -> None:
        delay = self._retry_backoff * (2**attempt)
        if delay:
            await asyncio.sleep(delay)


def _online_dedupe_key(sample: SpamSample) -> str:
    identity = f"v1\0{sample.source}\0{sample.message.chat_id}\0{sample.message.message_id}"
    return "v1:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _positive_id(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是正整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if parsed <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return parsed


def _result_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("results", [])
    if rows is None:
        return []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise D1APIError("D1 返回了格式不正确的查询结果")
    return rows


def _api_error_message(errors: Any) -> str:
    if not isinstance(errors, list):
        return "D1 API 返回业务失败"
    safe_parts: list[str] = []
    for error in errors[:3]:
        if not isinstance(error, dict):
            continue
        code = error.get("code")
        message = error.get("message")
        if code is not None and isinstance(message, str):
            safe_parts.append(f"{code}: {message[:200]}")
        elif code is not None:
            safe_parts.append(str(code))
    return "D1 API 返回业务失败" + ("（" + "; ".join(safe_parts) + "）" if safe_parts else "")
