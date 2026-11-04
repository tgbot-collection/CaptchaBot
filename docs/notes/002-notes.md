# Story 002：D1 集成 — 实施记录（Notes）

> 状态：已完成。
> 任务说明：[`../002-d1.md`](../002-d1.md)

## 实际完成内容
- 新增幂等的 `samples` SQL Migration，包含状态/标签 CHECK、JSON 校验、唯一去重键及状态和稳定导出索引。
- 新增基于 `httpx.AsyncClient` 的 Cloudflare D1 Query REST 客户端，实现参数化 SQL、超时、有限指数退避重试、默认并发上限，以及 HTTP 200 内业务失败解析。
- 实现 `create_pending()`、`review()`、`get()`、`stats()` 和 `export_reviewed()`；重复消息返回已有 ID，重复审核不覆盖首次人工标签。
- 新增本地 Migration 校验和远程 Migration CLI。远程执行直接使用 D1 REST API，不需要 Cloudflare Worker。
- 新增内存 SQLite D1 模拟测试，覆盖 Migration 约束、客户端接口、分页、故障响应和敏感 Token 不进入异常文本。
- 未接入 `main.py`、在线消息 Handler、Bio 查询或管理员 UI，现有删除路径没有改动。

## 新增或修改的文件
- `migrations/001_create_samples.sql`：`samples` 表、约束和索引。
- `spam_learning/d1.py`：D1 REST 客户端、返回类型和错误类型。
- `spam_learning/d1_migrate.py`：本地校验及远程迁移入口。
- `spam_learning/config.py`：增加可在主功能关闭时独立校验和读取 D1 配置的 `require_d1()`。
- `spam_learning/__init__.py`：导出 Story 002 的公共接口。
- `tests/test_d1.py`：客户端行为和故障测试。
- `tests/test_d1_migration.py`：Migration 语法、幂等、约束和唯一键测试。
- `requirements.txt`：增加 `httpx==0.28.1`。
- `README.md`：记录本地和远程 Migration 操作及 Token 最小权限要求。
- `docs/CONTRACTS.md`、`docs/PROGRESS.md`、本文件：同步实际接口、状态和交接信息。

## 接口、Schema、配置与兼容性
- `D1Client.from_config(config)` 使用既有 `D1_ACCOUNT_ID`、`D1_DATABASE_ID`、`D1_API_TOKEN`；`SpamLearningConfig.require_d1()` 允许在 `SPAM_LEARNING_ENABLED=false` 时单独迁移。
- 客户端默认 HTTP 超时 5 秒、最多重试 2 次、退避基数 0.2 秒、并发上限 4；这些参数可在构造时调整。
- `create_pending(sample) -> int` 只接受含 Telegram `chat_id/message_id` 的 `keyword/pending` 样本。重复写入通过唯一 SHA-256 `dedupe_key` 返回原 ID，不覆盖数据。
- `review(sample_id, label, admin_id) -> ReviewOutcome` 原子执行 `WHERE status='pending'` 的 `UPDATE ... RETURNING`；返回 `changed` 或 `already_reviewed`。不存在的 ID 抛出 `D1NotFoundError`。
- `get(sample_id) -> StoredSample` 将 `message_json` 解析为 `sample` 字典；`stats() -> SampleStats` 返回五类计数。
- `export_reviewed(page_size=200) -> list[StoredSample]` 使用 `id > last_id ORDER BY id ASC LIMIT ?` 的 keyset 分页，页大小限制为 1 到 1000。
- `review_chat_id` 和 `review_message_id` 已预留，但本 Story 没有管理员 UI，因此暂时保持 NULL。
- `python -m spam_learning.d1_migrate` 仅用内存 SQLite 校验；增加 `--apply` 后才读取 D1 环境变量并发起网络请求。
- `main.py` 没有导入或实例化 D1 客户端，功能默认关闭行为和关键词删除路径保持兼容。

## 关键技术决策及原因
- 使用 Cloudflare 官方 Query REST 端点，而不是 Worker；同时检查响应顶层 `success` 和每条 SQL 的 `result[].success`，避免把 HTTP 200 误判成写入成功。
- 使用 `httpx.AsyncClient` 提供真正的异步 I/O、连接复用和可测试 Transport；没有把同步 HTTP 工作放进事件循环。
- 只对网络/超时、429、500、502、503、504 重试。`create_pending` 有唯一键保护，审核更新带 pending 条件，因此请求在响应丢失后重试也不会重复创建或覆盖标签。
- 在线去重原文先加入版本、source、chat ID 和消息 ID，再计算 SHA-256；数据库只保存摘要，避免唯一键直接泄露 Telegram 标识符。
- 导出采用 keyset 分页而不是 offset，确保稳定排序，并避免数据量增长后 offset 扫描和页间漂移。
- Migration 显式使用 `label IS NOT NULL`。SQLite 的 CHECK 结果为 NULL 时会通过，不能只依赖 `label IN ('spam', 'ham')` 排除 reviewed/NULL。
- 没有实现手工种子写入 D1；Story 002 的接口明确是在线 pending 样本，种子合并和去重属于 Story 007。

## 验证记录
- `venv/bin/python -m unittest discover -v`：15 项测试全部通过。
- `venv/bin/python -m spam_learning.d1_migrate`：通过，无需 Cloudflare 凭证。
- `venv/bin/python -m compileall -q main.py spam_learning tests`：通过。
- `venv/bin/pip check`：通过，无损坏依赖。
- `git diff --check`：通过。
- 测试覆盖重复 `create_pending`、重复 callback 不覆盖标签、缺失 ID、统计、两页以上稳定导出、参数绑定、HTTP 503 重试、网络失败重试上限、HTTP 200 顶层业务失败、statement 失败和 Token 不泄漏。
- 未使用真实 Cloudflare 凭证，未执行远程 Migration；未连接真实 Telegram 或 Redis。

## 已知问题、限制与后续事项
- Cloudflare Query REST API 受全局 API 限流约束；客户端已处理 429 和限制并发，但大规模导出仍应控制频率。
- 未验证真实 D1 的远程 Migration 和响应，仅按 Cloudflare 官方 API 契约并用兼容 SQLite 语义模拟；部署前需使用目标 D1 执行一次 `--apply` 验证。
- pending/reviewed 个人数据的实际保留期限和清理任务尚未确定，上线前必须明确；本 Story 未擅自增加删除策略。
- 异常对象不会包含 Token 或 SQL 参数，但后续调用方仍不得记录完整 `StoredSample`、Bio 或消息全文。

## 下一 Story 交接要点
- Story 003 在 `group_message_preprocess()` 的关键词命中分支中，必须先同步调用 `sample_from_message()` 获取快照，再立即删除消息；D1 写入只能进入有界后台队列，不能被删除路径 await。
- 后台任务使用同一个长生命周期 `D1Client` 以复用连接，并在 Bot 停止时调用 `aclose()`；不要为每条消息创建客户端。
- 只有 `create_pending()` 成功返回 ID 后，后续 Story 004 才能发送带审核按钮的通知。D1 写入失败不得假装已保存，也不能影响已经完成的删除。
- Bio 查询应有独立短超时和缓存，失败时以 NULL 写入；不能借用 D1 客户端的重试延迟阻塞删除。
- Story 004 必须先验证 `SPAM_ADMIN_IDS`，及时 answer callback，再调用原子 `review()`；收到 `already_reviewed` 时展示原状态，不能再次改标签。

## Git Commit / PR
- 尚未提交。
