# Story 018：相同用户相同内容合并审核

## 背景

同一用户短时间内反复发送完全相同的 Spam 时，现有规则会逐条立即删除，但后台会为每个不同
Telegram message ID 创建一条 D1 pending，并逐条通知管理员审核。管理员需要重复处理相同内容。

## 目标

- 不改变任何现有规则的立即删除行为。
- 对 `source=rule` 且具有用户 ID、可见文字的样本，按“用户 ID + 原始可见内容”聚合。
- 首次出现照常补充 Bio、推理、写入一条 D1 pending 并发送一条审核通知。
- 后续重复只在 Redis 原子增加出现次数；不查询 Bio、不推理、不写 D1、不发送新审核通知。
- 审核通知发送时显示当前次数，管理员点击审核按钮后再次显示 Redis 中的最新次数。
- Redis/D1 故障不得影响规则删除；Redis 不可用时降级为原有逐条审核流程。

## 范围和边界

- 只聚合 `rule/pending`，不聚合普通群采集 `group/pending`，也不改变管理员转发的
  `manual/reviewed/spam` 入口。
- 内容按原始 Unicode 完全相等比较，不执行模型归一化。text 为空时使用 caption；没有用户 ID 或
  没有可见文字时跳过去重。
- 指纹使用带版本前缀的 canonical JSON SHA-256；Redis key 不包含消息原文或明文用户 ID。
- Redis 使用每个指纹一份 Hash，保存 `count/state/first_seen/last_seen` 等控制字段。计数键在最后一次
  出现后按 TTL 过期，默认 7 天，不写入 D1 Schema。
- 首次处理使用带 owner 的租约。D1 写入失败时状态改为 `retryable`，后续相同消息可以继承累计次数
  重试；进程异常退出后租约默认 60 秒可被新消息接管。
- 新功能通过 `SPAM_REVIEW_DEDUPE_ENABLED` 默认关闭；不新增依赖或 D1 Migration。

## 配置

- `SPAM_REVIEW_DEDUPE_ENABLED=false`
- `SPAM_REVIEW_DEDUPE_TTL_SECONDS=604800`
- `SPAM_REVIEW_DEDUPE_LEASE_SECONDS=60`

TTL 必须大于或等于租约，三项配置均在启动时校验。

## 验收条件

- 同一用户、完全相同内容连续提交两次，只产生一次 Bio 查询、一次推理、一次 D1 写入和一次通知，
  Redis 次数为 2。
- 不同用户或不同原始 Unicode 内容分别处理。
- 重复消息仍由原规则逐条立即删除。
- D1 首次写入失败后，后续相同消息能够重试，累计次数不归零。
- Redis 失败时样本仍走原有 D1/通知路径。
- 普通群采集不进入该聚合逻辑。
- 审核通知和审核完成文本显示可取得的出现次数。
- 完整自动化测试、编译、Migration 本地校验、依赖检查和 `git diff --check` 通过。
