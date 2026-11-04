# Story 003：消息采集 — 实施记录（Notes）

> 状态：已完成。
> 任务说明：[`../003-capture.md`](../003-capture.md)

## 实际完成内容
- 在 `group_message_preprocess()` 的现有 Spam 规则删除路径中接入采集，包含 `via_bot`/guest bot/Inline Keyboard、`BLACKLIST_MESSAGE`、Sticker 启发式与黑名单、contact/invite link/Bio、emoji/姓名/ID 黑名单。guest bot 回复及其触发消息分别采集；验证码业务消息不采集。
- 新增 `delete_rule_message()`：功能开启时在删除前同步提取内存快照，随后执行原 `message.delete()`，并在删除尝试后用 `put_nowait()` 非阻塞入队。
- 新增有界 `CaptureService`，使用固定 worker 处理 Bio 查询和 D1 `create_pending()`；队列满时丢弃采集任务，不阻塞删除。
- Bio 查询设置独立短超时，并使用同时受 TTL 和条目数限制的 LRU 式缓存；权限不足、限流、超时或字段缺失时以 `bio=null` 继续写入。既有路径已取得 Bio 时直接复用，不重复请求。
- Bot 启动时按配置创建单个长生命周期 `D1Client` 和采集 worker，停止时在有界时间内排空队列、取消剩余任务并关闭 HTTP 客户端。
- 采集路径的日志不包含消息全文、Bio、Telegram ID 或 D1 Token，异常只记录类型。

## 新增或修改的文件
- `main.py`：接入删除前快照、删除后入队及启停生命周期。
- `spam_learning/capture.py`：有界后台队列、worker、Bio 超时/缓存和 D1 写入。
- `spam_learning/config.py`：增加队列、worker、Bio 和关停参数的配置与校验。
- `spam_learning/schemas.py`、`spam_learning/d1.py`、`spam_learning/__init__.py`：将在线来源收敛为 `rule` 并导出采集服务。
- `migrations/001_create_samples.sql`：将未部署初始 Migration 的在线来源约束由 `keyword` 改为 `rule`。
- `tests/test_capture.py`、`tests/test_main_capture.py`、`tests/test_d1.py`、`tests/test_d1_migration.py`：增加采集和删除关键路径回归并同步新来源值。
- `README.md`、`docs/000-overview.md`、`docs/003-capture.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、本文件：同步实际范围、配置、契约和验证结果。

## 接口、Schema、配置与兼容性
- `delete_rule_message(message, *, matched_rule, bio=None)` 只在采集开启时创建快照；无论开关都直接 `await message.delete()`。删除失败时仍在 `finally` 中尝试入队，然后继续传播原异常。
- `CaptureService.start()` 创建固定 worker；`submit(sample) -> bool` 非阻塞返回是否入队；`aclose()` 执行有界关停。
- 在线样本使用 `source=rule`、`status=pending`、`label=null`；`matched_rule` 保存稳定规则类型，需要时附带命中的配置值。同一 `chat_id/message_id` 由 D1 唯一去重键保证最多一条记录。
- `source=rule` 取代了 Story 001–002 草案中的 `source=keyword`。用户确认 D1 尚未部署，因此直接修改初始 Migration，不保留未上线的旧值兼容包袱。
- 新增环境变量：`SPAM_CAPTURE_QUEUE_SIZE=100`、`SPAM_CAPTURE_WORKERS=2`、`SPAM_BIO_TIMEOUT_SECONDS=2.0`、`SPAM_BIO_CACHE_TTL_SECONDS=300.0`、`SPAM_BIO_CACHE_SIZE=1000`、`SPAM_CAPTURE_SHUTDOWN_TIMEOUT_SECONDS=5.0`。值必须为正数。
- `SPAM_LEARNING_ENABLED=false` 时不创建 D1 客户端、队列或 worker；新功能默认关闭。开启时仍按既有契约校验 `SPAM_ADMIN_IDS` 和 D1 配置，虽然本 Story 不发送管理员通知。

## 关键技术决策及原因
- 使用“删除前内存快照 + 删除尝试后 `put_nowait()`”：既避免 Telegram 删除后对象字段不可靠，也保证删除前不等待 D1、Bio 或其他网络请求。
- 删除 API 失败仍采集规则命中事实，但不吞掉原异常；这保留了既有 Handler 的失败语义。
- 后台队列容量和 worker 数分别限制内存积压与 Telegram/D1 并发。D1 客户端自身仍保留 Story 002 的并发上限和有限重试。
- 缓存 `null` Bio 以避免对权限不足或无 Bio 的账号反复请求；缓存只存在进程内，不记录到日志。
- 不使用 `asyncio.create_task()` 为每条消息创建无上限任务；所有后续处理都通过有界队列。

## 验证记录
- `venv/bin/python -m unittest discover -v`：26 项测试全部通过，覆盖 Bio 成功/超时/缓存、队列满非阻塞丢弃、删除前快照、删除失败仍入队且异常继续传播、功能关闭删除、关键词命中无网络查询、配置校验、D1 重试去重和 Migration 约束。
- `venv/bin/python -m spam_learning.d1_migrate`：通过，无需 Cloudflare 凭证。
- `venv/bin/python -m compileall -q main.py spam_learning tests`：通过。
- `venv/bin/pip check`：通过，无损坏依赖。
- `git diff --check`：通过。
- 未使用真实 Telegram 账号或 Cloudflare D1 凭证，未执行远程 Migration 或 Docker build。

## 已知问题、限制与后续事项
- 真实 Telegram/Kurigram 的 Bio 权限、FloodWait 行为和 Bot 关停顺序尚未联机验证；已按失败留空、短超时和有界关停设计。
- 队列满时会主动放弃样本，这是为保护删除延迟和内存上限的明确取舍；目前只有脱敏日志，没有 metrics 系统。
- pending/reviewed 个人数据的具体保留期和删除机制仍未确定，上线前必须明确。
- D1 仍未真实部署；部署前需执行 `python -m spam_learning.d1_migrate --apply` 并验证目标账号响应。

## 下一 Story 交接要点
- Story 004 只能在 `D1Client.create_pending()` 成功返回样本 ID 后发送管理员审核通知；队列丢弃或 D1 失败时不得发送虚假按钮。
- 审核回调必须验证 `SPAM_ADMIN_IDS`，及时 answer callback，并且只在 D1 原子更新成功后修改通知。
- `CaptureService` 当前只负责补充并写入。Story 004 需以明确的成功后续接口扩展，不得在删除 Handler 中等待审核通知。
- 后续训练与导出必须按 `source=rule` 读取在线样本，且只使用 `status=reviewed` 的人工标签。

## Git Commit / PR
- 尚未提交。
