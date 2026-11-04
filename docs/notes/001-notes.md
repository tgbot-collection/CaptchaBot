# Story 001：初始化架构 — 实施记录（Notes）

> 状态：已完成。
> 任务说明：[`../001-init.md`](../001-init.md)

## 实际完成内容
- 检查了真实仓库的单文件 Bot、Kurigram/Pyrogram Handler、asyncio 事件循环、APScheduler、依赖、Dockerfile、Compose 和多架构 CI。
- 新增 `spam_learning` 基础包，只包含当前 Story 实际使用的 Schema 和配置模块，没有提前创建 D1、采集、模型或审核模块。
- 定义 `schema_version=1` 的 `SpamSample` 及消息、用户、Inline Bot、entity、模型建议快照类型；提供不访问网络的 Pyrogram `Message` 同步快照函数。
- 定义纯文本人工种子构造函数。人工种子明确为 `source=manual`、`status=reviewed`、`label=spam`，不伪造 Pyrogram `Message`。
- 将 `BLACKLIST_MESSAGE` 的本地关键词检查移到 Sticker 和 Bio 网络请求之前；命中后的删除调用和返回值不变。
- 增加默认关闭的环境配置解析和校验；本 Story 未连接 D1、未发送 Telegram 审核通知、未执行模型推理。
- 修正 README 中与代码不一致的 `APP_ID` 名称，并排除 Docker 构建上下文中的 session、env 和 `.DS_Store`。

## 新增或修改的文件
- `spam_learning/__init__.py`：导出 Story 001 公共接口。
- `spam_learning/schemas.py`：v1 样本类型、JSON 序列化、消息快照和人工种子构造。
- `spam_learning/config.py`：功能开关、管理员、D1 和模型路径配置解析。
- `main.py`：提前执行 `BLACKLIST_MESSAGE` 本地匹配和删除。
- `.dockerignore`：阻止会话凭证和本地环境文件进入镜像。
- `README.md`：修正原有配置名并记录新增配置。
- `docs/CONTRACTS.md`：将草案收敛为 Story 001 的实际 Schema 和配置约定。
- `docs/PROGRESS.md`、本文件：更新 Story 状态和真实交接记录。

## 接口、Schema、配置与兼容性
- `sample_from_message(message, *, matched_rule, bio=None) -> SpamSample`：同步读取现有对象，不调用 Telegram API；Bio 只能显式传入，默认 `null`。
- `sample_from_seed_text(text) -> SpamSample`：保留原始 Unicode，无法取得的 Telegram 字段为 `null`。
- `SpamSample.to_dict()` / `SpamSample.to_json()`：输出 JSON 兼容结构，`to_json()` 使用 `ensure_ascii=False`。
- message text 与 caption 分开保存；只有 caption 或纯媒体时 text 为 `""`。时间统一为 UTC ISO 8601，枚举输出稳定字符串。
- `SpamLearningConfig.from_env()`：读取 `SPAM_LEARNING_ENABLED`、`SPAM_ADMIN_IDS`、`D1_ACCOUNT_ID`、`D1_DATABASE_ID`、`D1_API_TOKEN`、`SPAM_MODEL_PATH`。
- 功能默认关闭；默认模型路径是 `models/spam_learning`。开启时管理员 ID 和三项 D1 配置必填。Token 的 dataclass `repr` 已隐藏。
- 配置模块尚未挂入 `main.py`，因此默认运行路径和现有部署行为保持兼容。

## 关键技术决策及原因
- 使用标准库 frozen/slots dataclass，避免 Story 001 引入新的运行时依赖，并让字段和校验规则显式可审查。
- 快照提取采用 duck typing，不导入或构造 Pyrogram 内部对象，也不需要 Telegram 凭证。
- 不隐式读取 `message.from_user.bio`。未来如有权限查询 Bio，必须由有短超时和缓存的独立步骤显式传入。
- 手工正例使用 reviewed 状态，因为它们来自用户确认；在线关键词命中仍为 pending，关键词本身不等于人工标签。
- 只调整 `BLACKLIST_MESSAGE` 检查顺序，不接入采集任务，既消除删除前 Bio 等待，又避免提前实现 Story 003。
- 未创建 `storage.py`、`capture.py`、`model.py`、`review.py` 空壳，遵守“不要创建没有用途的空模块”。

## 验证记录
- `venv/bin/python -m compileall -q main.py spam_learning`：通过。
- `git diff --check`：通过。
- 未连接真实 Telegram、Redis 或 D1，未执行 Docker build；这些均不属于 Story 001 的实现范围。

## 已知问题、限制与后续事项
- 当前只建立 Schema 和配置，尚未将样本采集接入 Handler，这是 Story 003 的范围。
- `SpamLearningConfig` 尚未由 `main.py` 加载；因此即使设置开关，当前 Story 也不会启用任何新功能。
- 没有真实 Docker 多架构构建结果；现有 GitHub Actions 已声明 `linux/amd64,linux/arm64`，最终验收属于 Story 008。
- 工作区原有未跟踪 `.DS_Store` 未修改或删除。本地 `captchabot.session` 未删除，但已通过 `.dockerignore` 排除。

## 下一 Story 交接要点
- Story 002 应使用 `SpamLearningConfig` 中的 D1 配置，实现独立 REST storage 客户端和 Migration；仍不得接入在线 Handler 或管理员 UI。
- D1 存储可直接使用 `SpamSample.to_dict()` 生成 `message_json`，但必须按 `CONTRACTS.md` 单独保存查询字段并实现稳定、无私密信息的 dedupe key。
- D1 Token 不得进入日志或异常文本；Story 002 必须正确处理 HTTP 200 的业务失败、超时、有限重试、参数化 SQL、原子审核和并发限流。
- 实际在线集成入口是 `main.py` 的 `group_message_preprocess()`。Story 003 必须在关键词删除前同步调用快照函数，然后立即删除，再将后续工作放入有界后台队列。
- 不得改变 V1 模型仅提供建议、只有人工标签用于训练、D1 故障不影响关键词删除等约束。

## Git Commit / PR
- Git commit：`f372bd9e92a3c9c870186f1f567db487663e9a7f`（`step 1`）。
