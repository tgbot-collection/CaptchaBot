# Story 006：推理接入 — 实施记录（Notes）

> 状态：已完成。
> 任务说明：[`../006-inference.md`](../006-inference.md)

## 实际完成内容
- Bot 在 Spam 学习功能启动时从 `SPAM_MODEL_PATH` 加载一次本地 JSON 模型；模型缺失、损坏或格式不支持时安全降级为 `unavailable`，不影响启动、D1 pending 或人工审核。
- 新增建议性 `InferenceService`，通过 `asyncio.to_thread()` 将 CPU 推理移出事件循环，并用独立 semaphore 限制并发。每次推理记录 decision 和毫秒耗时，不记录消息原文、Bio、Telegram ID 或最近样本。
- 将推理接入既有 `CaptureService` worker：删除后的后台任务先补充 Bio，再推理，然后写 D1，最后在 D1 成功后发送审核通知。推理服务意外抛错时仍构造 `unavailable` 建议并继续后续流程。
- 将 `PredictionResult.model_version` 显式映射到 `ModelSuggestion.version`；模型不可用时版本和相似度均为 `null`，没有伪造版本字符串。
- 审核通知将分数明确显示为“相似度（不是概率）”，并解释 `spam_like`、`unknown`、`unavailable`；三种结果均不触发任何管理动作。
- 新增仅限配置管理员私聊使用的 `/model` 命令，显示模型可用性、类型、版本、构建日期状态和三种 decision 的含义。
- 原关键词判断、删除函数、封禁分支和 callback 审核原子更新逻辑未改变。

## 新增或修改的文件
- `spam_learning/inference.py`：模型加载后的异步推理服务、并发限制、状态结构和 `/model` 文本渲染。
- `spam_learning/capture.py`：在 Bio 补充和 D1 写入之间接入建议性推理，并实现异常降级。
- `spam_learning/schemas.py`：允许不可用模型的 `ModelSuggestion.version=null`。
- `spam_learning/config.py`：增加推理并发配置。
- `spam_learning/review.py`：明确相似度语义和三种 decision 的管理员说明。
- `spam_learning/__init__.py`：导出推理公共接口。
- `main.py`：启动时加载模型、注入采集服务、增加 `/model` 并清理关停引用。
- `tests/test_inference.py`：覆盖有效/缺失/运行时故障模型、并发限制和状态说明。
- `tests/test_capture.py`、`tests/test_review.py`、`tests/test_main_capture.py`、`tests/test_d1.py`：覆盖接入顺序、故障继续、通知语义、管理员权限和配置校验。
- `README.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、本文件：同步配置、接口、状态和交接信息。

## 接口、Schema、配置与兼容性
- `InferenceService.from_path(path, max_concurrency=2)`：调用 `SimilarityPredictor.from_path()` 一次，缺失或损坏模型得到不可用服务。
- `InferenceService.enrich(sample) -> SpamSample`：在线程池预测，返回带 `ModelSuggestion` 的不可变样本副本；内部异常不向采集链路传播。
- `InferenceService.status -> ModelStatus`：提供 `available`、`kind`、`version`、`built_at`。V1 manifest 不含构建日期，因此 `built_at=null`。
- `CaptureService(..., inference=...)`：推理器是可选依赖；主程序启用 Spam 学习时总是注入，即使本地模型不可用。
- `SPAM_INFERENCE_CONCURRENCY=2`：正整数，本地推理独立并发上限。`SPAM_CAPTURE_WORKERS`、队列、D1 并发和其他默认值保持不变。
- `ModelSuggestion.version` 从 `str` 收敛为 `str | None`，与既有 `PredictionResult.model_version=null` 的 unavailable 契约一致。D1 现有 `model_version` 列已经允许 NULL，无需 Migration。
- `/model` 仅匹配私聊，Handler 内再次校验唯一 `SPAM_ADMIN_ID`。功能关闭时不创建推理服务。

## 关键技术决策及原因
- 不在每条消息上读取模型文件；启动时只加载一次，避免重复 I/O 和版本在单进程运行期间漂移。
- 不在删除 Handler 中推理；仍采用“同步快照、立即删除、非阻塞入队”，保证模型和 D1 故障不进入删除关键路径。
- 使用 `asyncio.to_thread()` 而不是直接在事件循环执行同步 TF-IDF 扫描，并增加独立 semaphore；当前数据虽小，但此边界可以防止未来语料增长后阻塞 Bot。
- semaphore 等待时间计入 `duration_ms`，因此指标反映管理员建议生成阶段观察到的推理总延迟，而非只统计 CPU 执行时间。
- `unavailable` 使用真实的 `version=null` 和 `score=null`；没有用协议状态冒充模型版本，也没有把缺少建议误报成 Ham。
- 未把文件 mtime 当成构建时间。Story 005 的确定性 manifest 有意不写时间戳，`/model` 因此明确显示“未记录”。
- 最近正例仍随统一样本 JSON 持久化，但通知只展示 Story 要求的 decision、相似度、类型和版本，避免增加不必要的管理员消息长度。

## 验证记录
- `venv/bin/python -m unittest discover -v`：59 项测试全部通过；覆盖模型加载后的在线复用、缺失模型、运行时推理异常、并发上限、Bio→推理→D1→通知顺序、推理故障仍写 pending、unavailable 的 D1 NULL 字段、管理员 `/model` 权限、相似度措辞及既有删除回归。
- `venv/bin/python -m spam_learning.d1_migrate`：通过，无需 Cloudflare 凭证。
- `venv/bin/python -m compileall -q main.py spam_learning tests`：通过。
- `venv/bin/pip check`：通过，无损坏依赖；因本机 pip cache 目录不可写出现缓存禁用警告，不影响依赖校验结果。
- `git diff --check`：通过。
- 未连接真实 Telegram 或 Cloudflare D1，未执行远程 Migration 或 Docker build；本 Story 通过 fake Telegram、内存 SQLite D1 transport 和本地真实模型包验证。

## 已知问题、限制与后续事项
- 当前没有 metrics 后端，推理耗时以结构稳定的应用日志 `decision`、`duration_ms` 记录；若部署环境需要直方图或告警，应在不记录内容字段的前提下接入现有监控系统。
- V1 manifest 没有构建日期，`/model` 显示“未记录”。不能为了显示日期破坏 Story 005 的可复现模型格式。
- 纯 Python 模型当前适合不足 100 条种子；语料显著增长后的吞吐和线程池占用尚未做压力测试。
- 真实 Telegram `/model`、审核通知和真实 D1 写入尚未联机验证。

## 下一 Story 交接要点
- Story 007 只能使用 D1 中 `status=reviewed` 的人工 `spam`/`ham`；不能把 `spam_like`、规则命中或 pending 样本当训练标签。
- 后续模型如果更换实现，应继续返回 `PredictionResult` 并保持 `unavailable` 的 version/score 为 NULL；`InferenceService` 与通知层不应依赖具体向量器。
- 二分类模型即使未来可用，是否执行管理动作仍需独立 Story 明确授权；本 Story 的任何预测都只能作为建议。
- 新模型格式如需构建日期，应将其作为明确、可校验的 manifest 字段设计；不得从文件系统时间推断。

## Git Commit / PR
- 尚未提交。
