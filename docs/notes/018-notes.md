# Story 018：相同用户相同内容合并审核 — 实施记录（Notes）

> 状态：已完成。任务说明：[`../018-review-dedupe.md`](../018-review-dedupe.md)

## 实际完成内容

- 新增默认关闭的规则审核聚合。只有 `source=rule`、存在用户 ID 且具有可见文字的样本参与；
  `group` 普通采集和 `manual` 管理员转发保持原行为。
- 指纹按“固定版本 + 用户 ID + 原始 text（为空时 caption）”的 canonical JSON 计算 SHA-256。
  Redis key 不包含明文用户 ID 或消息内容，不使用会改变原始 Unicode 的模型归一化。
- 新增 Redis Hash 状态机和 Lua 原子脚本。首次消息获得 leader 租约并继续 Bio、推理、D1 和审核通知；
  重复消息只执行 `HINCRBY count` 后退出，不访问 Bio、模型、D1 或 Telegram 通知。
- D1 首次写入成功后将 Redis 状态标为 `saved` 并记录样本 ID；D1 失败时标为 `retryable`，后续
  相同消息可继承已有计数重试。进程异常退出时，处理租约到期后也可由后续消息接管。
- Redis 计数使用滑动 TTL，默认在最后一次出现 7 天后过期。审核通知发送前读取当前次数；管理员
  点击 Spam/Ham 后再次读取并把最新次数写入审核完成文本。
- Redis 连接或脚本失败时记录脱敏异常并降级为原有逐条 D1/通知流程，避免去重依赖导致样本丢失。
- 将验证码超时扫描从阻塞的 `keys("*")` 改为只匹配验证码 key 形态的 `scan_iter("-*,*")`，避免
  新增的 `spamdet:review-dedupe:v1:*` Hash 被误解析成验证码记录。
- 规则删除 Handler 未增加任何等待。删除前快照、立即删除、删除后非阻塞入队的顺序保持不变。

## 修改文件、接口和配置

- `spamdet/dedupe.py`：新增 `OccurrenceReservation` 和 `RedisOccurrenceStore`，包含原子 reserve、
  saved/retryable 状态转换及次数读取。
- `spamdet/capture.py`：在 worker 的 Bio/模型/D1 之前接入可选去重器；重复项提前退出，故障降级。
- `spamdet/review.py`：通知发送和 callback 完成时显示可取得的 Redis 出现次数。
- `spamdet/config.py`：新增并校验三项配置：
  `SPAM_REVIEW_DEDUPE_ENABLED=false`、`SPAM_REVIEW_DEDUPE_TTL_SECONDS=604800`、
  `SPAM_REVIEW_DEDUPE_LEASE_SECONDS=60`。TTL 必须大于或等于租约。
- `main.py`：复用现有 Redis 客户端创建聚合存储并注入采集/审核服务；收窄验证码 Redis 扫描。
- `spamdet/__init__.py`：导出去重公共类型。
- `tests/test_dedupe.py`、`tests/test_capture.py`、`tests/test_review.py`、`tests/test_d1.py`：覆盖指纹、
  原子状态语义、短路范围、故障重试/降级、次数显示和配置。
- `README.md`、`docs/000-overview.md`、`docs/CONTRACTS.md`、`docs/PROGRESS.md`、当前 Story 和本 Notes：
  同步配置、边界、故障语义和交接记录。
- 未新增第三方依赖、D1 字段、Migration、callback_data 或模型格式。

## 关键设计决策

- 仅聚合规则审核，不聚合 `group/pending`。如果普通群样本先占用同一指纹，后来规则命中可能被抑制，
  导致没有主动审核通知；按来源隔离可以避免这一语义错误。
- 使用 SHA-256 而不是建议示例中的 MD5。两者都可用于摘要 key，但 SHA-256 碰撞余量更大；Hash 中
  只保存控制字段和计数，不保存原文。
- 使用 Lua 而不是客户端依次 `HGET/HSET/HINCRBY`，保证多个 Capture worker 并发时只有一个 leader，
  并且每条重复消息都只增加一次计数。
- 状态转换携带随机 owner。旧 leader 在租约过期、已有新 leader 接管后，不能把新 leader 的状态错误
  标记为 saved 或 retryable。
- Redis 不可用时选择 fail-open，继续原有 D1/通知流程。该取舍可能产生重复审核，但不会静默丢失
  需要人工判断的数据，也不影响已完成的规则删除。
- 次数不写入 D1，遵循“重复消息不要存 D1”的要求；因此它是有期限的运行态聚合信息，不进入训练集。

## 实际验证

- `venv/bin/python -m unittest discover -q`：121 项完整测试全部通过。
- 新增测试确认同用户同原始内容只执行一次 Bio、推理、D1 和通知；第二次只合并。不同用户、全角
  `Ａ` 与半角 `A` 分别处理；无用户 ID、`group` 样本不参与。
- 新增测试确认 D1 失败后状态变为 retryable 且累计次数保留；租约过期可接管；Redis reserve 抛错时
  仍写 D1 并发送通知。
- 新增测试确认审核通知和审核完成文本均显示次数，且 Redis key 不含测试原文或明文用户 ID。
- 使用本机临时 Redis 7.2.4（`127.0.0.1:6399`，关闭持久化）真实执行 Lua：首次
  `first_leader=True`，第二次和保存后的第三次均为 follower，`saved=True`，最终 `count=3`；验证后
  已正常关闭临时 Redis。
- `venv/bin/python -m compileall -q main.py spamdet tests`：通过。
- `venv/bin/python -m spamdet.d1_migrate`：本地 Schema v2 Migration 校验通过。
- `venv/bin/pip check`：通过；本机 pip cache 权限警告不影响结果。
- `git diff --check`：通过。
- 未连接生产 Redis、真实 Telegram 或远程 Cloudflare D1，未执行真实审核通知、远程数据写入或
  Docker 多架构构建；本 Story 没有依赖或 Dockerfile 变更。

## 已知问题与下一 Story 交接

- Redis 计数是有期限的运行态数据。TTL 到期、Redis 数据被清空/驱逐或容器被无持久化重建后，
  后续相同内容会重新成为首次样本并可能新增一条 D1 pending；不能把 Redis 次数当永久审计数据。
- 初始通知只显示发送时的计数，不为每次重复消息编辑 Telegram 通知，以免 Spam 洪峰造成大量编辑
  请求；管理员点击审核按钮时会再次读取并显示当时最新计数。审核完成后继续出现的次数仍只保存在
  Redis，既有已完成通知不会主动刷新。
- Redis 与 D1 之间没有分布式事务。正常路径先原子 reserve、再写 D1、最后标记 saved；极端情况下
  D1 已成功但紧接着 Redis 丢失/不可写，租约到期后可能再次产生 D1 pending。当前选择可用性优先，
  未引入跨系统事务或让重复消息查询 D1。
- 处理租约默认 60 秒，应高于正常 Bio、推理和 D1 写入总耗时；若生产 D1 长期超过该时间，可通过
  `SPAM_REVIEW_DEDUPE_LEASE_SECONDS` 调大，但不得超过 TTL。
- Git commit：本提交（`deduplicate repeated spam reviews`）。
- PR：尚未创建。
