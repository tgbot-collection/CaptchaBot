# Story 009：群消息采集 — 实施记录（Notes）

> 状态：已完成。任务说明：[`../009-group-recording.md`](../009-group-recording.md)

## 实际完成内容
- 增加 `SPAM_CAPTURE_GROUPS` 群组白名单。未设置或空值关闭；`all` 允许全部群；逗号分隔的负数群 ID 仅允许列出的群。非法值在配置解析时失败。
- 现有群消息 Handler 在全部规则未命中后，对允许群的非空文本或 caption 同步取快照，非阻塞提交既有有界队列。规则命中删除链路未改。
- 普通消息标为 `source=group/status=pending/label=null`。后台可提供模型建议，但跳过 Bio 查询，不触发管理动作。
- 普通消息写入 D1 后不逐条推送；管理员私聊 `/review` 每次领取最早的未领取样本，以现有两个按钮人工标注。规则命中通知方式不变。
- 新增 D1 来源约束迁移，保留旧记录与审核引用；导出仍只使用 reviewed 人工标签。

## 修改文件与接口配置
- `spam_learning/config.py`：解析 `SPAM_CAPTURE_GROUPS`；`records_group(chat_id)` 执行选择。配置名在发布前统一为 `SPAM_CAPTURE_*`，不保留草案别名。
- `spam_learning/schemas.py`：支持 `group` 来源、普通消息快照及存储快照重建。
- `main.py`：普通消息入队和管理员 `/review`。
- `spam_learning/capture.py`：接受 `group/pending`，普通样本不查 Bio、不自动通知。
- `spam_learning/d1.py`：接受 `group/pending`、`next_group_pending()`。
- `spam_learning/review.py`：主动领取并显示来源；沿用管理员校验、原子回调和写入成功后更新通知。
- `migrations/002_group_samples.sql`、`spam_learning/d1_migrate.py`：按顺序执行迁移，重建 CHECK 表并保留原字段与索引。
- `tests/test_main_capture.py`、`tests/test_d1.py`、`tests/test_d1_migration.py`：覆盖选择、未标注写入、重复去重和迁移保留。
- `README.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、当前 Story 和 Notes：记录配置与边界。
- 未新增依赖或改变既有环境变量默认值。`SPAM_LEARNING_ENABLED=false` 时普通采集仍关闭。

## 决策与边界
- `source=group` 与 `source=rule` 分开，避免将普通消息伪装为规则命中。D1 去重键含来源和消息位置；编辑后如果规则命中，可能同时存在普通样本和规则样本，两者仍须人工确认。
- 只存有文本或 caption 的普通消息，因为当前模型只使用文本；纯媒体消息不进入本 Story 普通采集。
- 复用固定 worker 和有界队列；高流量时队列满会丢弃采集，不影响群消息处理。普通消息不主动推送，避免管理员私聊被大量正常聊天淹没。
- 普通消息不查询 Bio；已存快照仍包含必要的 Telegram 来源字段，按项目维护者决定长期保留。
- `002_group_samples.sql` 使用多语句批量提交，不包含显式 BEGIN/COMMIT；本地 SQLite 校验通过。真实 D1 响应仍待部署验证。

## 实际验证
- `venv/bin/python -m unittest discover -q`：73 项通过，覆盖普通群消息入队、不查 Bio、不自动推送、主动领取、人工审核、白名单和迁移。
- `venv/bin/python -m unittest tests.test_main_capture tests.test_d1 tests.test_d1_migration -q`：31 项通过。
- `venv/bin/python -m spam_learning.d1_migrate`、`venv/bin/python -m compileall -q main.py spam_learning tests`、`git diff --check`：通过。
- 新迁移在内存 SQLite 上重复运行，旧 reviewed/Ham 和审核者字段保留；普通 `group` 样本可写入。
- 未连接真实 Telegram 或 Cloudflare D1，未执行远程迁移；真实环境中的多语句 D1 Migration、管理员 `/review` 时序需上线观察。

## 已知问题与交接
- 部署顺序：先运行 `python -m spam_learning.d1_migrate --apply`，确认迁移成功，再设置 `SPAM_CAPTURE_GROUPS`。远程迁移失败时保持变量关闭。
- `/review` 每次领取一条；已领取而未审核的样本保持 pending，但不会再次被该命令自动领取。可从原通知审核；通知成功而引用保存失败可能再次领取。
- 不自动将未命中规则的消息标 Ham；只有人工审核的 reviewed 样本进入训练。模型只建议，不执行删除。
- Git commit / PR：尚未提交。
